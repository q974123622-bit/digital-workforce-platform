import hashlib
import json
import logging
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app import models
from app.services.agentteams_collaboration import (
    AgentTeamsCollaborationService,
    CollaborationConflict,
)
from app.services.agentteams_gateway import MatrixEvent, MatrixSyncBatch
from app.services.agentteams_protocol import AgentProtocolMessage, render_protocol_line


ROOM_ID = "!agentteams:test"


class FakeGateway:
    """In-memory Matrix boundary double; collaboration state stays real."""

    def __init__(self, batches=None, *, send_error=None):
        self.batches = list(batches or [])
        self.send_error = send_error
        self.sent = []
        self.sync_calls = []
        self.closed = 0

    def send_protocol_message(self, room_id, text, protocol_line):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((room_id, text, protocol_line))
        return f"$sent-{len(self.sent)}"

    def sync_messages(self, since, room_ids, timeout_ms=0):
        self.sync_calls.append((since, set(room_ids), timeout_ms))
        return self.batches.pop(0) if self.batches else MatrixSyncBatch(events=[], next_cursor=since or "s1")

    def close(self):
        self.closed += 1


class BatchWithoutCursor:
    def __init__(self, events):
        self.events = events


def _enable_test_leader(db_session):
    leader = db_session.get(models.DigitalEmployee, "DT-E10281")
    leader.runtime_ref = "@dwp-twin-e10281:local"
    db_session.add(models.TeamMember(
        team_id="TEAM-ONBOARD", employee_id=leader.employee_no, role="member",
    ))
    db_session.flush()
    return leader


def _create_run(db_session):
    leader = _enable_test_leader(db_session)
    service = AgentTeamsCollaborationService()
    run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id=leader.employee_no,
        title="新员工入职",
        instruction="协调完成入职准备",
        material={"employee": "李四"},
    )
    return service, run, leader


def _message(run, leader, *, event_type="agent.message", key="message-1", payload=None):
    return AgentProtocolMessage(
        version="1.0",
        message_id=f"MSG-{key}",
        type=event_type,
        root_task_id=run.id,
        task_id=run.id,
        parent_task_id=None,
        trace_id=run.trace_id,
        sender=leader.employee_no,
        receiver="platform",
        timestamp="2026-08-31T15:00:00+08:00",
        idempotency_key=f"{run.id}:{key}",
        payload=payload or {"text": "收到，开始处理"},
    )


def _event(event_id, body, *, sender="@dwp-twin-e10281:local"):
    return MatrixEvent(
        event_id=event_id,
        room_id=ROOM_ID,
        sender=sender,
        body=body,
        ts=1_788_000_000_000,
    )


def _delegate_one_child(db_session, service, run, leader):
    service.apply_message(
        db_session,
        matrix_sender=leader.runtime_ref,
        message=AgentProtocolMessage(
            version="1.0",
            message_id="MSG-delegate-1",
            type="task.delegate",
            root_task_id=run.id,
            task_id=run.id,
            parent_task_id=None,
            trace_id=run.trace_id,
            sender=leader.employee_no,
            receiver="platform",
            timestamp="2026-08-31T15:00:00+08:00",
            idempotency_key=f"{run.id}:delegate:1",
            payload={"subtasks": [{
                "client_task_key": "policy",
                "title": "制度核查",
                "instruction": "核查制度",
                "assignee_agent_id": "VE-0002",
                "depends_on": [],
            }]},
        ),
    )
    return db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()


def _node_message(run, node, *, event_type, key, payload):
    worker = node.assignee_agent_id
    return AgentProtocolMessage(
        version="1.0",
        message_id=f"MSG-{key}",
        type=event_type,
        root_task_id=run.id,
        task_id=node.id,
        parent_task_id=node.parent_task_id,
        trace_id=run.trace_id,
        sender=worker,
        receiver="platform",
        timestamp="2026-08-31T15:00:00+08:00",
        idempotency_key=f"{run.id}:{node.id}:{key}",
        payload=payload,
    )


def _tool_request_payload(**overrides):
    payload = {
        "plugin_id": "knowledge-l1",
        "action": "read",
        "params": {"query": "员工入职制度"},
        "knowledge_base_id": "KB-REG-EXTERNAL",
        "context": "已完成制度核查",
    }
    payload.update(overrides)
    return payload


def _worker_matrix_sender(db_session, service, node):
    worker = db_session.get(models.DigitalEmployee, node.assignee_agent_id)
    assert worker is not None
    matrix_sender = service._matrix_user_id_for_runtime(worker.runtime_ref)
    assert matrix_sender is not None
    return matrix_sender


def test_tool_request_uses_platform_gateway(db_session, monkeypatch):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)
    calls = []

    def fake_invoke_plugin(db, **kwargs):
        calls.append((db, kwargs))
        return {
            "ok": True,
            "decision": "allow",
            "audit_ids": [41, 42],
            "tool_name": "员工制度查询",
            "tool_type": "knowledge",
            "execution_mode": "knowledge_adapter",
            "data": {
                "answer": "制度已核查",
                "sources": ["员工手册"],
                "bounded": "x" * 600,
            },
        }

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", fake_invoke_plugin)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="gateway",
        payload=_tool_request_payload(employee_id=leader.employee_no),
    )
    runner = AgentTeamsCollaborationRunner(
        gateway=FakeGateway([
            MatrixSyncBatch(
                events=[
                    _event(
                        "$tool-gateway",
                        render_protocol_line(request),
                        sender=_worker_matrix_sender(db_session, service, node),
                    )
                ],
                next_cursor="s2",
            )
        ]),
        service=service,
        room_id=ROOM_ID,
    )

    runner.tick_once(db_session)

    assert len(calls) == 1
    call_db, kwargs = calls[0]
    assert call_db is db_session
    assert kwargs == {
        "employee_id": node.assignee_agent_id,
        "plugin_id": "knowledge-l1",
        "action": "read",
        "params": {"query": "员工入职制度"},
        "trace_id": node.trace_id,
        "knowledge_base_id": "KB-REG-EXTERNAL",
        "execution_context": {
            "task_id": node.id,
            "request": "协调完成入职准备",
            "subtask": "核查制度",
            "collaboration_summary": "已完成制度核查",
        },
    }
    result = db_session.query(models.AgentMessage).filter_by(
        direction="outbound", task_id=node.id, message_type="tool.result",
    ).one()
    assert result.idempotency_key == (
        f"{node.id}:tool.result:{hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:32]}"
    )
    assert result.receiver_agent_id == node.assignee_agent_id
    assert result.payload == {
        "ok": True,
        "decision": "allow",
        "audit_ids": [41, 42],
        "tool_name": "员工制度查询",
        "tool_type": "knowledge",
        "execution_mode": "knowledge_adapter",
        "data": {
            "answer": "制度已核查",
            "sources": ["员工手册"],
            "bounded": "x" * 512,
        },
    }


def test_tool_request_derives_knowledge_plugin_from_kb_level(db_session, monkeypatch):
    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)
    calls = []

    def fake_invoke_plugin(_db, **kwargs):
        calls.append(kwargs)
        return {"ok": False, "decision": "deny", "audit_ids": [61], "data": None}

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", fake_invoke_plugin)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="kb-level",
        payload=_tool_request_payload(
            plugin_id="knowledge-l1",
            knowledge_base_id="KB-CUSTOMER-SENSITIVE",
        ),
    )

    service.apply_message(
        db_session,
        matrix_sender=_worker_matrix_sender(db_session, service, node),
        message=request,
    )

    assert calls[0]["plugin_id"] == "knowledge-l3"
    assert calls[0]["knowledge_base_id"] == "KB-CUSTOMER-SENSITIVE"


def test_tool_request_rejects_unknown_knowledge_base_without_gateway(db_session, monkeypatch):
    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)
    calls = []

    def fake_invoke_plugin(_db, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "decision": "allow", "audit_ids": [62], "data": {}}

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", fake_invoke_plugin)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="unknown-kb",
        payload=_tool_request_payload(knowledge_base_id="KB-NOT-FOUND"),
    )

    events = service.apply_message(
        db_session,
        matrix_sender=_worker_matrix_sender(db_session, service, node),
        message=request,
    )

    assert calls == []
    assert events[0].event_type == "tool.denied"
    assert events[0].detail["reason"] == "knowledge_base_not_found"
    result = db_session.query(models.AgentMessage).filter_by(
        direction="outbound", task_id=node.id, message_type="tool.result",
    ).one()
    assert result.payload == {"ok": False, "decision": "deny", "audit_ids": []}


def test_tool_request_rejects_non_knowledge_plugin_with_knowledge_base(db_session, monkeypatch):
    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)
    calls = []

    def fake_invoke_plugin(_db, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "decision": "allow", "audit_ids": [63], "data": {}}

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", fake_invoke_plugin)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="non-knowledge-kb",
        payload=_tool_request_payload(plugin_id="hr-employee-mcp", action="execute"),
    )

    events = service.apply_message(
        db_session,
        matrix_sender=_worker_matrix_sender(db_session, service, node),
        message=request,
    )

    assert calls == []
    assert events[0].event_type == "tool.denied"
    assert events[0].detail["reason"] == "knowledge_base_requires_knowledge_read"


def test_tool_request_rejects_non_platform_receiver(db_session, monkeypatch):
    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)
    calls = []

    def fake_invoke_plugin(_db, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "decision": "allow", "audit_ids": [64], "data": {}}

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", fake_invoke_plugin)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="wrong-receiver",
        payload=_tool_request_payload(),
    )
    request.receiver = node.coordinator_agent_id

    with pytest.raises(CollaborationConflict, match="invalid_tool_receiver"):
        service.apply_message(
            db_session,
            matrix_sender=_worker_matrix_sender(db_session, service, node),
            message=request,
        )

    assert calls == []


def test_tool_result_filters_non_http_uris_and_token_scheme(db_session, monkeypatch):
    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)

    def fake_invoke_plugin(_db, **_kwargs):
        return {
            "ok": True,
            "decision": "allow",
            "audit_ids": [65],
            "data": {
                "database": "postgresql://user:password@db.example/app",
                "remote": "ssh://root@internal.example:22/home",
                "local": "file:///private/secrets.txt",
                "note": "Token abc.def.ghi",
                "safe": "保留",
            },
        }

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", fake_invoke_plugin)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="uri-sanitized",
        payload=_tool_request_payload(),
    )

    service.apply_message(
        db_session,
        matrix_sender=_worker_matrix_sender(db_session, service, node),
        message=request,
    )

    result = db_session.query(models.AgentMessage).filter_by(
        direction="outbound", task_id=node.id, message_type="tool.result",
    ).one()
    emitted = json.dumps(result.payload, ensure_ascii=False)
    assert "postgresql://" not in emitted
    assert "ssh://" not in emitted
    assert "file://" not in emitted
    assert "Token abc" not in emitted
    assert result.payload["data"]["safe"] == "保留"


def test_tool_result_redacts_api_key_prefixes_in_all_sendable_fields(db_session, monkeypatch):
    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)

    def fake_invoke_plugin(_db, **_kwargs):
        return {
            "ok": True,
            "decision": "allow",
            "audit_ids": [66],
            "tool_name": "ApiKey abc.def.ghi",
            "tool_type": "X-Api-Key abc.def.ghi",
            "execution_mode": "apikey abc.def.ghi",
            "runtime_mode": "x-api-key abc.def.ghi",
            "data": {
                "message": "ApiKey abc.def.ghi",
                "nested": {"message": "X-Api-Key abc.def.ghi"},
                "safe": "保留",
            },
        }

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", fake_invoke_plugin)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="api-key-prefix-sanitized",
        payload=_tool_request_payload(),
    )

    service.apply_message(
        db_session,
        matrix_sender=_worker_matrix_sender(db_session, service, node),
        message=request,
    )

    result = db_session.query(models.AgentMessage).filter_by(
        direction="outbound", task_id=node.id, message_type="tool.result",
    ).one()
    emitted = json.dumps(result.payload, ensure_ascii=False)
    assert "abc.def.ghi" not in emitted
    assert "ApiKey abc" not in emitted
    assert "X-Api-Key abc" not in emitted
    assert result.payload["data"]["safe"] == "保留"


def test_tool_result_payload_has_hard_utf8_byte_limit(db_session, monkeypatch):
    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)
    oversized_metadata = "汉" * 512

    def fake_invoke_plugin(_db, **_kwargs):
        return {
            "ok": True,
            "decision": "allow",
            "audit_ids": [66],
            "tool_name": oversized_metadata,
            "tool_type": oversized_metadata,
            "execution_mode": oversized_metadata,
            "runtime_mode": oversized_metadata,
            "data": {"result": "x" * 5000},
        }

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", fake_invoke_plugin)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="hard-limit",
        payload=_tool_request_payload(),
    )

    service.apply_message(
        db_session,
        matrix_sender=_worker_matrix_sender(db_session, service, node),
        message=request,
    )

    result = db_session.query(models.AgentMessage).filter_by(
        direction="outbound", task_id=node.id, message_type="tool.result",
    ).one()
    assert set(result.payload) == {"ok", "decision", "audit_ids", "truncated"}
    assert result.payload["truncated"] is True
    assert len(json.dumps(result.payload, ensure_ascii=False).encode("utf-8")) <= 4096


def test_runner_rolls_back_gateway_writes_before_safe_failure_result(db_session, monkeypatch):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)
    before_audits = db_session.query(models.AuditEvent).count()

    def failing_invoke_plugin(db, **_kwargs):
        db.add(models.AuditEvent(
            trace_id=node.trace_id,
            actor=node.assignee_agent_id,
            employee_id=node.assignee_agent_id,
            plugin_id="knowledge-l1",
            action="read",
            decision="allow",
            reason="must roll back",
        ))
        raise RuntimeError("Token abc postgresql://db.example/partial")

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", failing_invoke_plugin)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="gateway-rollback",
        payload=_tool_request_payload(),
    )
    runner = AgentTeamsCollaborationRunner(
        gateway=FakeGateway([
            MatrixSyncBatch(
                events=[
                    _event(
                        "$tool-gateway-rollback",
                        render_protocol_line(request),
                        sender=_worker_matrix_sender(db_session, service, node),
                    )
                ],
                next_cursor="s2",
            )
        ]),
        service=service,
        room_id=ROOM_ID,
    )

    runner.tick_once(db_session)

    assert db_session.query(models.AuditEvent).count() == before_audits
    assert db_session.query(models.AgentMatrixCursor).one().cursor == "s2"
    event = db_session.query(models.AgentTaskEvent).filter_by(task_id=node.id, event_type="tool.failed").one()
    assert event.detail == {"decision": "failed", "audit_ids": []}
    result = db_session.query(models.AgentMessage).filter_by(
        direction="outbound", task_id=node.id, message_type="tool.result",
    ).one()
    assert result.payload == {"ok": False, "decision": "failed", "audit_ids": []}


def test_runner_keeps_cursor_atomic_when_gateway_commits(db_session, monkeypatch):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)

    def committing_invoke_plugin(db, **_kwargs):
        db.commit()
        return {"ok": True, "decision": "allow", "audit_ids": [54], "data": {"ok": "done"}}

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", committing_invoke_plugin)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="committing-gateway",
        payload=_tool_request_payload(),
    )
    runner = AgentTeamsCollaborationRunner(
        gateway=FakeGateway([
            MatrixSyncBatch(
                events=[
                    _event(
                        "$tool-committing-gateway",
                        render_protocol_line(request),
                        sender=_worker_matrix_sender(db_session, service, node),
                    )
                ],
                next_cursor="s2",
            )
        ]),
        service=service,
        room_id=ROOM_ID,
    )

    runner.tick_once(db_session)

    assert db_session.query(models.AgentMatrixCursor).one().cursor == "s2"
    assert db_session.query(models.AgentMessage).filter_by(
        direction="outbound", task_id=node.id, message_type="tool.result",
    ).count() == 1


def test_tool_request_policy_denied_returns_denied_tool_result(db_session, monkeypatch):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)

    def deny_plugin(_db, **_kwargs):
        raise HTTPException(
            status_code=403,
            detail={"audit_id": 73, "message": "策略拒绝", "token": "must-not-leak"},
        )

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", deny_plugin)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="denied",
        payload=_tool_request_payload(),
    )
    AgentTeamsCollaborationRunner(
        gateway=FakeGateway([
            MatrixSyncBatch(
                events=[
                    _event(
                        "$tool-denied",
                        render_protocol_line(request),
                        sender=_worker_matrix_sender(db_session, service, node),
                    )
                ],
                next_cursor="s2",
            )
        ]),
        service=service,
        room_id=ROOM_ID,
    ).tick_once(db_session)

    result = db_session.query(models.AgentMessage).filter_by(
        direction="outbound", task_id=node.id, message_type="tool.result",
    ).one()
    assert result.payload == {"ok": False, "decision": "deny", "audit_ids": [73]}
    assert db_session.get(models.AgentTaskNode, node.id).status == "assigned"
    assert db_session.get(models.TaskRun, run.id).status == "planning"


def test_tool_request_cannot_impersonate_another_worker(db_session, monkeypatch):
    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("unauthorized tool request must not reach the gateway")

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", fail_if_called)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="impersonation",
        payload=_tool_request_payload(),
    )
    request.sender = leader.employee_no

    with pytest.raises(CollaborationConflict, match="unauthorized_sender"):
        service.apply_message(db_session, matrix_sender=leader.runtime_ref, message=request)

    assert db_session.query(models.AgentMessage).filter_by(
        direction="outbound", task_id=node.id, message_type="tool.result",
    ).count() == 0
    assert db_session.get(models.AgentTaskNode, node.id).status == "assigned"


def test_tool_result_omits_secret_fields(db_session, monkeypatch):
    service, run, leader = _create_run(db_session)
    node = _delegate_one_child(db_session, service, run, leader)

    def fake_invoke_plugin(_db, **_kwargs):
        return {
            "ok": True,
            "decision": "allow",
            "audit_ids": [91],
            "data": {
                "visible": "正常结果",
                "token": "token-value",
                "ApiKey": "key-value",
                "nested": {
                    "Authorization": "Bearer hidden",
                    "endpoint": "https://private.example/api",
                    "traceback": "Traceback (most recent call last): hidden adapter detail",
                    "keep": "可保留",
                },
                "items": [{"password": "hidden", "label": "保留标签"}],
            },
        }

    monkeypatch.setattr("app.services.agentteams_collaboration.invoke_plugin", fake_invoke_plugin)
    request = _node_message(
        run,
        node,
        event_type="tool.request",
        key="sanitized",
        payload=_tool_request_payload(),
    )

    service.apply_message(
        db_session,
        matrix_sender=_worker_matrix_sender(db_session, service, node),
        message=request,
    )

    result = db_session.query(models.AgentMessage).filter_by(
        direction="outbound", task_id=node.id, message_type="tool.result",
    ).one()
    emitted = json.dumps(result.payload, ensure_ascii=False)
    assert "token-value" not in emitted
    assert "key-value" not in emitted
    assert "Bearer hidden" not in emitted
    assert "private.example" not in emitted
    assert "Traceback" not in emitted
    assert "password" not in emitted.casefold()
    assert result.payload["data"] == {
        "visible": "正常结果",
        "nested": {"keep": "可保留"},
        "items": [{"label": "保留标签"}],
    }


def test_runner_sends_pending_outbox(db_session):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, _leader = _create_run(db_session)
    gateway = FakeGateway()

    AgentTeamsCollaborationRunner(gateway=gateway, service=service, room_id=ROOM_ID).tick_once(db_session)

    outbox = db_session.query(models.AgentMessage).filter_by(
        task_id=run.id, direction="outbound", message_type="task.submit",
    ).one()
    assert outbox.delivery_status == "sent"
    assert outbox.matrix_event_id == "$sent-1"
    assert gateway.sent and gateway.sent[0][0] == ROOM_ID


def test_runner_persists_valid_inbound_event_and_cursor(db_session):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, leader = _create_run(db_session)
    protocol = _message(run, leader)
    gateway = FakeGateway([
        MatrixSyncBatch(events=[_event("$reply-1", render_protocol_line(protocol))], next_cursor="s2")
    ])
    before_events = db_session.query(models.AgentTaskEvent).count()

    AgentTeamsCollaborationRunner(gateway=gateway, service=service, room_id=ROOM_ID).tick_once(db_session)

    assert db_session.query(models.AgentMessage).filter_by(direction="inbound").count() == 1
    assert db_session.query(models.AgentTaskEvent).count() == before_events + 1
    assert db_session.query(models.AgentMatrixCursor).one().cursor == "s2"


def test_runner_leaves_outbox_pending_when_send_fails(db_session):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, _leader = _create_run(db_session)
    gateway = FakeGateway(send_error=RuntimeError("Matrix unavailable"))

    AgentTeamsCollaborationRunner(gateway=gateway, service=service, room_id=ROOM_ID).tick_once(db_session)

    outbox = db_session.query(models.AgentMessage).filter_by(
        task_id=run.id, direction="outbound", message_type="task.submit",
    ).one()
    assert outbox.delivery_status == "pending"
    assert outbox.matrix_event_id is None


def test_runner_does_not_advance_cursor_when_batch_processing_fails(db_session):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, leader = _create_run(db_session)
    invalid_transition = _message(
        run, leader, event_type="task.progress", key="bad-progress", payload={"progress": 50},
    )
    gateway = FakeGateway([
        MatrixSyncBatch(
            events=[_event("$bad-progress", render_protocol_line(invalid_transition))],
            next_cursor="s2",
        )
    ])

    with pytest.raises(CollaborationConflict, match="invalid_transition"):
        AgentTeamsCollaborationRunner(gateway=gateway, service=service, room_id=ROOM_ID).tick_once(db_session)

    assert db_session.query(models.AgentMatrixCursor).count() == 0
    assert db_session.query(models.AgentMessage).filter_by(direction="inbound").count() == 0
    assert db_session.get(models.AgentTaskNode, run.id).status == "planning"


def test_runner_replay_deduplicates_matrix_event(db_session):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, leader = _create_run(db_session)
    protocol = _message(run, leader, key="replay")
    event = _event("$replayed", render_protocol_line(protocol))
    first_gateway = FakeGateway([MatrixSyncBatch(events=[event], next_cursor="s2")])
    AgentTeamsCollaborationRunner(gateway=first_gateway, service=service, room_id=ROOM_ID).tick_once(db_session)
    db_session.commit()
    before_messages = db_session.query(models.AgentMessage).filter_by(direction="inbound").count()
    before_events = db_session.query(models.AgentTaskEvent).count()

    second_gateway = FakeGateway([MatrixSyncBatch(events=[event], next_cursor="s3")])
    AgentTeamsCollaborationRunner(gateway=second_gateway, service=service, room_id=ROOM_ID).tick_once(db_session)

    assert second_gateway.sync_calls[0][0] == "s2"
    assert db_session.query(models.AgentMessage).filter_by(direction="inbound").count() == before_messages
    assert db_session.query(models.AgentTaskEvent).count() == before_events


def test_runner_records_invalid_protocol_without_state_change(db_session):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, _leader = _create_run(db_session)
    bad_json = (
        'DWP_EVENT {"version":"1.0","message_id":"bad","root_task_id":"'
        f'{run.id}","task_id":"{run.id}","trace_id":"{run.trace_id}",'
        '"sender":"DT-E10281","receiver":"platform","timestamp":"now",'
        '"idempotency_key":"bad-event","payload":{}}'
    )
    gateway = FakeGateway([MatrixSyncBatch(events=[_event("$bad-json", bad_json)], next_cursor="s2")])
    before_events = db_session.query(models.AgentTaskEvent).count()

    AgentTeamsCollaborationRunner(gateway=gateway, service=service, room_id=ROOM_ID).tick_once(db_session)

    assert db_session.get(models.AgentTaskNode, run.id).status == "planning"
    assert db_session.query(models.AgentTaskEvent).count() == before_events + 1
    assert db_session.query(models.AgentTaskEvent).filter_by(event_type="protocol.invalid").count() == 1
    assert db_session.query(models.AgentMessage).filter_by(direction="inbound").one().matrix_event_id == "$bad-json"


def test_runner_retains_plain_text_without_state_change(db_session):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, _leader = _create_run(db_session)
    gateway = FakeGateway([MatrixSyncBatch(events=[_event("$plain", "普通协作文本")], next_cursor="s2")])
    before_events = db_session.query(models.AgentTaskEvent).count()

    AgentTeamsCollaborationRunner(gateway=gateway, service=service, room_id=ROOM_ID).tick_once(db_session)

    inbound = db_session.query(models.AgentMessage).filter_by(direction="inbound").one()
    assert inbound.body == "普通协作文本"
    assert inbound.matrix_event_id == "$plain"
    assert db_session.get(models.AgentTaskNode, run.id).status == "planning"
    assert db_session.query(models.AgentTaskEvent).count() == before_events


def test_runner_marks_unaccepted_node_timed_out(db_session, monkeypatch):
    from app.services import config
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, leader = _create_run(db_session)
    child = _delegate_one_child(db_session, service, run, leader)
    child.created_at = datetime.now() - timedelta(seconds=61)
    monkeypatch.setattr(config, "agentteams_accept_timeout_seconds", lambda: 60)
    monkeypatch.setattr(config, "agentteams_task_timeout_seconds", lambda: 600)

    AgentTeamsCollaborationRunner(
        gateway=FakeGateway(), service=service, room_id=ROOM_ID,
    ).tick_once(db_session)

    assert child.status == "timed_out"
    assert db_session.query(models.AgentTaskEvent).filter_by(
        task_id=child.id, event_type="task.timed_out",
    ).count() == 1


def test_new_runner_continues_from_saved_cursor(db_session):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, _run, _leader = _create_run(db_session)
    AgentTeamsCollaborationRunner(
        gateway=FakeGateway([MatrixSyncBatch(events=[], next_cursor="s2")]),
        service=service,
        room_id=ROOM_ID,
    ).tick_once(db_session)
    db_session.commit()

    next_gateway = FakeGateway([MatrixSyncBatch(events=[], next_cursor="s3")])
    AgentTeamsCollaborationRunner(
        gateway=next_gateway, service=service, room_id=ROOM_ID,
    ).tick_once(db_session)

    assert next_gateway.sync_calls == [("s2", {ROOM_ID}, 0)]
    assert db_session.query(models.AgentMatrixCursor).one().cursor == "s3"


@pytest.mark.parametrize("bad_batch", ["empty", "missing"])
def test_runner_rejects_missing_next_cursor_without_losing_saved_cursor(db_session, bad_batch):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, _run, _leader = _create_run(db_session)
    gateway = FakeGateway([MatrixSyncBatch(events=[], next_cursor="s2")])
    runner = AgentTeamsCollaborationRunner(gateway=gateway, service=service, room_id=ROOM_ID)
    runner.tick_once(db_session)
    db_session.commit()
    gateway.batches.append(
        MatrixSyncBatch(events=[], next_cursor="")
        if bad_batch == "empty"
        else BatchWithoutCursor(events=[])
    )

    with pytest.raises(ValueError, match="matrix_sync_missing_cursor"):
        runner.tick_once(db_session)

    assert gateway.sync_calls[-1][0] == "s2"
    assert db_session.query(models.AgentMatrixCursor).one().cursor == "s2"


@pytest.mark.parametrize(
    ("source", "root_status", "run_status"),
    [
        ("agentteams", "completed", "completed"),
        ("agentteams", "cancelled", "cancelled"),
        ("agentteams", "timed_out", "timed_out"),
        ("builtin", "waiting_children", "planning"),
    ],
)
def test_runner_does_not_expire_assigned_nodes_outside_active_agentteams_runs(
    db_session, monkeypatch, source, root_status, run_status,
):
    from app.services import config
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, leader = _create_run(db_session)
    child = _delegate_one_child(db_session, service, run, leader)
    root = db_session.get(models.AgentTaskNode, run.id)
    run.source = source
    run.status = run_status
    root.status = root_status
    child.created_at = datetime.now() - timedelta(seconds=61)
    monkeypatch.setattr(config, "agentteams_accept_timeout_seconds", lambda: 60)
    monkeypatch.setattr(config, "agentteams_task_timeout_seconds", lambda: 600)
    before_events = db_session.query(models.AgentTaskEvent).count()

    AgentTeamsCollaborationRunner(
        gateway=FakeGateway(), service=service, room_id=ROOM_ID,
    ).tick_once(db_session)

    assert child.status == "assigned"
    assert db_session.query(models.AgentTaskEvent).count() == before_events


@pytest.mark.parametrize(
    ("terminal_status", "event_type", "payload"),
    [
        ("completed", "task.accepted", {}),
        ("cancelled", "task.progress", {"progress": 10}),
        ("timed_out", "task.accepted", {}),
    ],
)
def test_runner_audits_late_protocol_for_terminal_root_without_state_change(
    db_session, terminal_status, event_type, payload,
):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, leader = _create_run(db_session)
    child = _delegate_one_child(db_session, service, run, leader)
    root = db_session.get(models.AgentTaskNode, run.id)
    run.status = terminal_status
    root.status = terminal_status
    protocol = _node_message(
        run,
        child,
        event_type=event_type,
        key=f"late-{terminal_status}",
        payload=payload,
    )
    before_events = db_session.query(models.AgentTaskEvent).count()
    gateway = FakeGateway([
        MatrixSyncBatch(events=[_event(f"$late-{terminal_status}", render_protocol_line(protocol))], next_cursor="s2")
    ])

    AgentTeamsCollaborationRunner(gateway=gateway, service=service, room_id=ROOM_ID).tick_once(db_session)

    inbound = db_session.query(models.AgentMessage).filter_by(
        direction="inbound", matrix_event_id=f"$late-{terminal_status}",
    ).one()
    assert inbound.message_type == "matrix.terminal_late"
    assert db_session.get(models.TaskRun, run.id).status == terminal_status
    assert db_session.get(models.AgentTaskNode, root.id).status == terminal_status
    assert db_session.get(models.AgentTaskNode, child.id).status == "assigned"
    assert db_session.query(models.AgentTaskEvent).count() == before_events
    assert db_session.query(models.AgentMatrixCursor).one().cursor == "s2"


def test_runner_logs_error_category_without_exception_text(monkeypatch, caplog):
    from app.services import agentteams_runner as runner_module
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    class SessionDouble:
        rollback_calls = 0
        close_calls = 0

        def commit(self):
            raise AssertionError("failed tick must not commit")

        def rollback(self):
            self.rollback_calls += 1

        def close(self):
            self.close_calls += 1

    class StopAfterWait:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, _seconds):
            self.stopped = True
            return True

    session = SessionDouble()
    runner = AgentTeamsCollaborationRunner(gateway=FakeGateway(), room_id=ROOM_ID)
    monkeypatch.setattr(runner_module, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        runner,
        "tick_once",
        lambda _db: (_ for _ in ()).throw(RuntimeError("secret-token=do-not-log")),
    )

    with caplog.at_level(logging.WARNING):
        runner.run_forever(StopAfterWait())

    assert session.rollback_calls == 1
    assert session.close_calls == 1
    assert "RuntimeError" in caplog.text
    assert "secret-token" not in caplog.text
