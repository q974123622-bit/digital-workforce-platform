from __future__ import annotations

from app import models
from app.services.agentteams_collaboration import AgentTeamsCollaborationService
from app.services.agentteams_runner import AgentTeamsCollaborationRunner


def test_local_demo_completes_the_persisted_collaboration_loop(db_session, monkeypatch):
    """A broken factory, protocol, persistence, tool, or summary link fails this test."""
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    monkeypatch.delenv("AGENTTEAMS_ROOM_ID", raising=False)
    service = AgentTeamsCollaborationService()
    run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title="新员工入职协作",
        instruction="完成入职材料、制度与 IT 账号准备核查",
        material={"employee": "李四"},
    )
    run.owner_human_no = "E10281"
    db_session.commit()

    runner = AgentTeamsCollaborationRunner(service=service)
    try:
        assert runner.transport_mode == "local_demo"
        assert runner.room_id == "local-demo"
        for _ in range(10):
            runner.tick_once(db_session)
            db_session.commit()
            db_session.refresh(run)
            if run.status in {"completed", "failed", "timed_out", "cancelled"}:
                break
    finally:
        runner.close()

    root = db_session.get(models.AgentTaskNode, run.id)
    nodes = db_session.query(models.AgentTaskNode).filter_by(root_task_id=run.id).all()
    by_key = {node.client_task_key: node for node in nodes}
    assert run.status == "completed"
    assert root.status == "completed"
    assert set(by_key) == {"root", "hr-policy", "it-account", "hr-it-check"}
    assert by_key["hr-policy"].parent_task_id == root.id
    assert by_key["it-account"].parent_task_id == root.id
    assert by_key["hr-it-check"].parent_task_id == by_key["hr-policy"].id

    events = db_session.query(models.AgentTaskEvent).filter_by(
        root_task_id=run.id
    ).order_by(models.AgentTaskEvent.event_seq).all()
    event_types = {event.event_type for event in events}
    assert {
        "task.delegate",
        "task.accepted",
        "task.progress",
        "agent.message",
        "task.summarizing",
        "task.summary",
    } <= event_types
    tool_events = [event for event in events if event.event_type == "tool.result"]
    assert len(tool_events) == 1
    assert tool_events[0].detail["decision"] == "allow"
    audit_ids = tool_events[0].detail["audit_ids"]
    assert audit_ids
    audits = db_session.query(models.AuditEvent).filter(
        models.AuditEvent.id.in_(audit_ids)
    ).all()
    assert len(audits) == len(audit_ids)
    assert all(audit.trace_id == run.trace_id for audit in audits)
    assert all(audit.employee_id == "VE-0003" for audit in audits)
    assert all(audit.plugin_id == "adp-onboarding" for audit in audits)
    assert all(audit.action == "execute" and audit.decision == "allow" for audit in audits)

    messages = db_session.query(models.AgentMessage).filter_by(root_task_id=run.id).all()
    outbound_ids = {
        message.matrix_event_id
        for message in messages
        if message.direction == "outbound" and message.matrix_event_id
    }
    inbound_ids = {
        message.matrix_event_id
        for message in messages
        if message.direction == "inbound" and message.matrix_event_id
    }
    assert outbound_ids and all(event_id.startswith("local-out-") for event_id in outbound_ids)
    assert inbound_ids and all(event_id.startswith("local-in-") for event_id in inbound_ids)
    # Offline local-demo IDs are intentionally synthetic and are never evidence
    # for the separate strict Matrix / DeepSeek acceptance scripts.
    assert all(not event_id.startswith("$") for event_id in outbound_ids | inbound_ids)
    assert any(message.message_type == "tool.result" for message in messages)
    assert "已完成事项" in run.summary
    assert "风险" in run.summary
    assert "下一步" in run.summary
