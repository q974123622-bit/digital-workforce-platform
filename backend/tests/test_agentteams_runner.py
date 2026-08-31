from datetime import datetime, timedelta

import pytest

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
        return self.batches.pop(0) if self.batches else MatrixSyncBatch(events=[], next_cursor=since or "")

    def close(self):
        self.closed += 1


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
