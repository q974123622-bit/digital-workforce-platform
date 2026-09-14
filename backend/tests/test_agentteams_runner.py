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
from app.services.agentteams_protocol import AgentProtocolMessage, parse_protocol_lines, render_protocol_line


ROOM_ID = "!agentteams:test"


class FakeGateway:
    """In-memory Matrix boundary double; collaboration state stays real."""

    mode = "agentteams_strict"

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


def test_runner_accepts_transport_keyword_and_keeps_gateway_alias():
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    transport = FakeGateway()

    runner = AgentTeamsCollaborationRunner(transport=transport, room_id=ROOM_ID)

    assert runner.transport is transport
    assert runner.gateway is transport


def test_runner_uses_transport_factory_when_not_injected(monkeypatch):
    from app.services import agentteams_runner as runner_module

    transport = FakeGateway()
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    monkeypatch.setattr(
        runner_module,
        "create_agentteams_transport",
        lambda: transport,
        raising=False,
    )

    runner = runner_module.AgentTeamsCollaborationRunner(room_id=ROOM_ID)

    assert runner.transport is transport
    assert runner.gateway is transport


def test_strict_runner_factory_uses_validated_runtime_identity_and_room(monkeypatch, tmp_path):
    """Strict delivery must use the same identity contract as strict health."""
    from app.services import agentteams_runner as runner_module

    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(json.dumps({
        "matrix_url": "http://runtime.matrix.test",
        "room_id": "!runtime-room:test",
        "team_id": "TEAM-ONBOARD",
        "platform": {"mxid": "@platform:test", "token": "runtime-platform-token"},
        "agents": {
                "manager": {"agent_id": "VE-0001", "mxid": "@manager:test", "token": "manager-token", "allowed_skill_ids": ["manager-skill"], "safe_tool_catalog": []},
                "hr": {"agent_id": "VE-0002", "mxid": "@hr:test", "token": "hr-token", "allowed_skill_ids": ["hr-skill"], "safe_tool_catalog": []},
                "it": {"agent_id": "VE-0003", "mxid": "@it:test", "token": "it-token", "allowed_skill_ids": ["it-skill"], "safe_tool_catalog": []},
                "audit": {"agent_id": "VE-0005", "mxid": "@audit:test", "token": "audit-token", "allowed_skill_ids": ["audit-skill"], "safe_tool_catalog": []},
                "hr_material": {"agent_id": "VE-HR-MATERIAL", "mxid": "@hr-material:test", "token": "hr-material-token", "allowed_skill_ids": ["hr-material-skill"], "safe_tool_catalog": []},
                "hr_training": {"agent_id": "VE-HR-TRAINING", "mxid": "@hr-training:test", "token": "hr-training-token", "allowed_skill_ids": ["hr-training-skill"], "safe_tool_catalog": []},
                "it_account": {"agent_id": "VE-IT-ACCOUNT", "mxid": "@it-account:test", "token": "it-account-token", "allowed_skill_ids": ["it-account-skill"], "safe_tool_catalog": []},
                "it_device": {"agent_id": "VE-IT-DEVICE", "mxid": "@it-device:test", "token": "it-device-token", "allowed_skill_ids": ["it-device-skill"], "safe_tool_catalog": []},
            },
            "team_catalog": {},
            "team_relationships": {
                "VE-0001": {"role": "Manager", "reports_to": [], "direct_reports": ["VE-0002", "VE-0003", "VE-0005"]},
                "VE-0002": {"role": "HR负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-HR-MATERIAL", "VE-HR-TRAINING"]},
                "VE-0003": {"role": "IT负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-IT-ACCOUNT", "VE-IT-DEVICE"]},
                "VE-0005": {"role": "审计负责人", "reports_to": ["VE-0001"], "direct_reports": []},
                "VE-HR-MATERIAL": {"role": "HR材料专员", "reports_to": ["VE-0002"], "direct_reports": []},
                "VE-HR-TRAINING": {"role": "HR培训专员", "reports_to": ["VE-0002"], "direct_reports": []},
                "VE-IT-ACCOUNT": {"role": "IT账号专员", "reports_to": ["VE-0003"], "direct_reports": []},
                "VE-IT-DEVICE": {"role": "IT设备专员", "reports_to": ["VE-0003"], "direct_reports": []},
            },
        }), encoding="utf-8")
    calls = []

    class StrictTransport(FakeGateway):
        def __init__(self, *, base_url, matrix_token):
            super().__init__()
            calls.append((base_url, matrix_token))

    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(runtime_path))
    monkeypatch.setenv("AGENTTEAMS_MATRIX_URL", "http://stale.example")
    monkeypatch.setenv("AGENTTEAMS_MATRIX_TOKEN", "stale-token")
    monkeypatch.setenv("AGENTTEAMS_ROOM_ID", "!stale-room:test")
    monkeypatch.setattr(runner_module, "AgentTeamsGateway", StrictTransport, raising=False)

    runner = runner_module.AgentTeamsCollaborationRunner()

    assert calls == [("http://runtime.matrix.test", "runtime-platform-token")]
    assert runner.room_id == "!runtime-room:test"


def test_strict_runner_factory_fails_closed_for_invalid_runtime(monkeypatch, tmp_path):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    missing_runtime = tmp_path / "missing-runtime.json"
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(missing_runtime))

    with pytest.raises(ValueError, match="invalid_runtime_config"):
        AgentTeamsCollaborationRunner()


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
    run.owner_human_no = "E10281"
    db_session.flush()
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
                "required_capability": "hr-employee-mcp",
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
            "team_id": "TEAM-ONBOARD",
            "owner_human_no": "E10281",
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
    assert {
        key: value for key, value in result.payload.items()
        if key not in {"team_capability_catalog", "team_catalog_version"}
    } == {
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
    assert result.payload["team_catalog_version"] == 3
    assert {item["agent_id"] for item in result.payload["team_capability_catalog"]} >= {
        "VE-0002", "VE-0003", "VE-0005",
    }
    assert "token" not in json.dumps(result.payload).lower()


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
    assert {
        key: value for key, value in result.payload.items()
        if key not in {"team_capability_catalog", "team_catalog_version"}
    } == {"ok": False, "decision": "deny", "audit_ids": []}
    assert result.payload["team_capability_catalog"]
    assert result.payload["team_catalog_version"] >= 1


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
    assert set(result.payload) == {
        "ok", "decision", "audit_ids", "truncated",
        "team_capability_catalog", "team_catalog_version",
    }
    assert result.payload["truncated"] is True
    assert result.payload["team_capability_catalog"]
    assert result.payload["team_catalog_version"] >= 1
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
    assert {
        key: value for key, value in result.payload.items()
        if key not in {"team_capability_catalog", "team_catalog_version"}
    } == {"ok": False, "decision": "failed", "audit_ids": []}
    assert result.payload["team_capability_catalog"]
    assert result.payload["team_catalog_version"] >= 1


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
    assert {
        key: value for key, value in result.payload.items()
        if key not in {"team_capability_catalog", "team_catalog_version"}
    } == {"ok": False, "decision": "deny", "audit_ids": [73]}
    assert result.payload["team_capability_catalog"]
    assert result.payload["team_catalog_version"] >= 1
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


def test_runner_suppresses_stale_submit_after_task_is_cancelled(db_session):
    """A user cancellation must not let an earlier queued submission escape."""
    from app.routers.agentteams_collaboration import cancel_task
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, _leader = _create_run(db_session)
    cancel_task(run.id, actor_no="E10281", db=db_session)
    gateway = FakeGateway()

    AgentTeamsCollaborationRunner(
        gateway=gateway, service=service, room_id=ROOM_ID,
    ).tick_once(db_session)

    sent_types = [parse_protocol_lines(protocol_line)[0].type for _, _, protocol_line in gateway.sent]
    submit = db_session.query(models.AgentMessage).filter_by(
        root_task_id=run.id, message_type="task.submit",
    ).one()
    cancellation = db_session.query(models.AgentMessage).filter_by(
        root_task_id=run.id, message_type="task.cancel",
    ).one()
    assert sent_types == ["task.cancel"]
    assert submit.delivery_status == "cancelled"
    assert cancellation.delivery_status == "sent"


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


def test_runner_keeps_outbox_pending_when_transport_returns_non_matrix_event_id(db_session):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, _leader = _create_run(db_session)
    gateway = FakeGateway()
    gateway.send_protocol_message = lambda *_args: "not-a-matrix-event"

    AgentTeamsCollaborationRunner(gateway=gateway, service=service, room_id=ROOM_ID).tick_once(db_session)

    outbox = db_session.query(models.AgentMessage).filter_by(
        task_id=run.id, direction="outbound", message_type="task.submit",
    ).one()
    assert outbox.delivery_status == "pending"
    assert outbox.matrix_event_id is None


def test_runner_does_not_advance_cursor_when_technical_batch_processing_fails(db_session, monkeypatch):
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, leader = _create_run(db_session)
    protocol = _message(run, leader, key="technical-failure")
    gateway = FakeGateway([
        MatrixSyncBatch(
            events=[_event("$technical-failure", render_protocol_line(protocol))],
            next_cursor="s2",
        )
    ])

    def fail_apply(*_args, **_kwargs):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(service, "apply_message", fail_apply)

    with pytest.raises(RuntimeError, match="database write failed"):
        AgentTeamsCollaborationRunner(gateway=gateway, service=service, room_id=ROOM_ID).tick_once(db_session)

    assert db_session.query(models.AgentMatrixCursor).count() == 0
    assert db_session.query(models.AgentMessage).filter_by(direction="inbound").count() == 0
    assert db_session.get(models.AgentTaskNode, run.id).status == "planning"


@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("unknown_root", "unknown_root_task"),
        ("unauthorized_sender", "matrix_sender_mismatch"),
        ("invalid_transition", "invalid_transition"),
    ],
)
def test_runner_audits_business_rejection_advances_cursor_and_keeps_sent_outbox(
    db_session, case, expected_error,
):
    """A bad DWP_EVENT must not pin Matrix sync or undo a successful send."""
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, run, leader = _create_run(db_session)
    if case == "unknown_root":
        bad = _message(run, leader, key=case).model_copy(update={
            "root_task_id": "AT-unknown",
            "task_id": "AT-unknown",
            "trace_id": "AT-unknown",
            "idempotency_key": "AT-unknown:unknown-root",
        })
        bad_sender = leader.runtime_ref
    elif case == "unauthorized_sender":
        bad = _message(run, leader, key=case)
        bad_sender = "@untrusted:local"
    else:
        bad = _message(
            run, leader, event_type="task.progress", key=case, payload={"progress": 50},
        )
        bad_sender = leader.runtime_ref

    legal = _message(run, leader, key=f"legal-after-{case}")
    gateway = FakeGateway([
        MatrixSyncBatch(
            events=[
                _event(f"$bad-{case}", render_protocol_line(bad), sender=bad_sender),
                _event(f"$legal-{case}", render_protocol_line(legal), sender=leader.runtime_ref),
            ],
            next_cursor="s2",
        )
    ])

    AgentTeamsCollaborationRunner(gateway=gateway, service=service, room_id=ROOM_ID).tick_once(db_session)

    rejected = db_session.query(models.AgentMessage).filter_by(
        direction="inbound", matrix_event_id=f"$bad-{case}",
    ).one()
    assert rejected.message_type == "protocol.rejected"
    assert rejected.payload["error"] == expected_error
    assert db_session.query(models.AgentTaskEvent).filter_by(
        event_type="protocol.rejected",
    ).count() == 1
    assert db_session.query(models.AgentMessage).filter_by(
        direction="inbound", matrix_event_id=f"$legal-{case}", message_type="agent.message",
    ).count() == 1
    assert db_session.query(models.AgentMatrixCursor).one().cursor == "s2"
    assert db_session.get(models.AgentTaskNode, run.id).status == "planning"
    submit = db_session.query(models.AgentMessage).filter_by(
        root_task_id=run.id, message_type="task.submit", direction="outbound",
    ).one()
    assert submit.delivery_status == "sent"
    assert submit.matrix_event_id == "$sent-1"


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


def test_runner_cursor_isolated_by_transport_mode_and_room(db_session):
    from app.services.agentteams_local_demo import LocalDemoTransport
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service, _run, _leader = _create_run(db_session)
    local = LocalDemoTransport()
    AgentTeamsCollaborationRunner(
        transport=local,
        service=service,
        room_id="local-room",
    )._receive_new(db_session)

    strict_same_room = FakeGateway(
        [MatrixSyncBatch(events=[], next_cursor="strict-same-room")]
    )
    AgentTeamsCollaborationRunner(
        transport=strict_same_room,
        service=service,
        room_id="local-room",
    )._receive_new(db_session)

    strict_other_room = FakeGateway(
        [MatrixSyncBatch(events=[], next_cursor="strict-other-room")]
    )
    AgentTeamsCollaborationRunner(
        transport=strict_other_room,
        service=service,
        room_id="other-room",
    )._receive_new(db_session)

    assert strict_same_room.sync_calls[0][0] is None
    assert strict_other_room.sync_calls[0][0] is None
    assert db_session.query(models.AgentMatrixCursor).count() == 3


def test_local_runner_restart_replays_sent_outbox_and_recovers_response(db_session, monkeypatch):
    from app.services.agentteams_local_demo import LocalDemoTransport
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")

    service = AgentTeamsCollaborationService()
    run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title="新员工入职",
        instruction="协调完成入职准备",
        material={"employee": "李四"},
    )
    interrupted = AgentTeamsCollaborationRunner(
        transport=LocalDemoTransport(),
        service=service,
        room_id="local-demo",
    )

    interrupted._send_pending(db_session)
    db_session.commit()
    submit = db_session.query(models.AgentMessage).filter_by(
        root_task_id=run.id,
        direction="outbound",
        message_type="task.submit",
    ).one()
    assert submit.delivery_status == "sent"
    assert db_session.query(models.AgentTaskNode).filter_by(
        parent_task_id=run.id,
    ).count() == 0

    restarted = AgentTeamsCollaborationRunner(
        transport=LocalDemoTransport(),
        service=service,
        room_id="local-demo",
    )
    restarted.tick_once(db_session)

    assert db_session.query(models.AgentTaskNode).filter_by(
        parent_task_id=run.id,
    ).count() == 2


def test_restarted_local_runner_delivers_next_task_behind_old_cursor(db_session, monkeypatch):
    from app.services.agentteams_local_demo import LocalDemoTransport
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")

    service = AgentTeamsCollaborationService()
    first_run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title="first task",
        instruction="establish a persisted local cursor",
        material={},
    )
    AgentTeamsCollaborationRunner(
        transport=LocalDemoTransport(),
        service=service,
        room_id="local-demo",
    ).tick_once(db_session)
    old_cursor = db_session.query(models.AgentMatrixCursor).one().cursor
    old_event_ids = {
        event_id
        for (event_id,) in db_session.query(models.AgentMessage.matrix_event_id)
        .filter_by(direction="inbound", root_task_id=first_run.id)
        if event_id is not None
    }
    for queued_assignment in db_session.query(models.AgentMessage).filter_by(
        direction="outbound",
        root_task_id=first_run.id,
        delivery_status="pending",
    ):
        queued_assignment.delivery_status = "cancelled"
    db_session.commit()

    second_run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title="second task",
        instruction="must not be skipped by the old cursor",
        material={},
    )
    AgentTeamsCollaborationRunner(
        transport=LocalDemoTransport(),
        service=service,
        room_id="local-demo",
    ).tick_once(db_session)

    assert db_session.query(models.AgentTaskNode).filter_by(
        parent_task_id=second_run.id,
    ).count() == 2
    new_delegate = db_session.query(models.AgentMessage).filter_by(
        direction="inbound",
        root_task_id=second_run.id,
        message_type="task.delegate",
    ).one()
    assert new_delegate.matrix_event_id not in old_event_ids
    assert db_session.query(models.AgentMatrixCursor).one().cursor != old_cursor


def test_local_runner_does_not_replay_strict_or_other_room_sent_outbox(db_session):
    from app.services.agentteams_local_demo import LocalDemoTransport
    from app.services.agentteams_runner import AgentTeamsCollaborationRunner

    service = AgentTeamsCollaborationService()
    strict_run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title="strict task",
        instruction="must stay on strict transport",
        material={},
    )
    other_room_run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title="other room task",
        instruction="must stay in its room",
        material={},
    )
    strict_outbox = db_session.query(models.AgentMessage).filter_by(
        root_task_id=strict_run.id,
        direction="outbound",
        message_type="task.submit",
    ).one()
    strict_outbox.delivery_status = "sent"
    strict_outbox.matrix_room_id = "local-demo"
    strict_outbox.matrix_event_id = "$strict-event"
    other_room_outbox = db_session.query(models.AgentMessage).filter_by(
        root_task_id=other_room_run.id,
        direction="outbound",
        message_type="task.submit",
    ).one()
    other_room_outbox.delivery_status = "sent"
    other_room_outbox.matrix_room_id = "other-room"
    other_room_outbox.matrix_event_id = "local-out-existing"
    db_session.flush()

    transport = LocalDemoTransport()
    AgentTeamsCollaborationRunner(
        transport=transport,
        service=service,
        room_id="local-demo",
    )._send_pending(db_session)

    assert transport.sync_messages(None, {"local-demo"}).events == []


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
