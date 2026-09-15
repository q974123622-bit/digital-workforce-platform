from __future__ import annotations

from app import models
from app.services.agentteams_collaboration import AgentTeamsCollaborationService
from app.services.agentteams_runner import AgentTeamsCollaborationRunner
from app.services.agentteams_role_change_scenario import (
    ROLE_CHANGE_INSTRUCTION,
    ROLE_CHANGE_TITLE,
)


ROLE_CHANGE_PLUGIN_IDS = {
    "agentteams-role-change-hr",
    "agentteams-role-change-it-plan",
    "agentteams-role-change-audit",
    "agentteams-role-change-it-activate",
}


def test_onboarding_local_demo_uses_three_manager_rounds_and_real_leader_workers(
    db_session, monkeypatch,
):
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    monkeypatch.delenv("AGENTTEAMS_ROOM_ID", raising=False)
    service = AgentTeamsCollaborationService()
    run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title="新员工A按期入职评估",
        instruction="评估新员工A是否可以按期入职。",
        material={},
    )
    run.owner_human_no = "E10281"
    db_session.commit()

    runner = AgentTeamsCollaborationRunner(service=service)
    try:
        for _ in range(60):
            runner.tick_once(db_session)
            db_session.commit()
            db_session.refresh(run)
            if run.status in {"completed", "failed", "timed_out", "cancelled"}:
                break
    finally:
        runner.close()

    nodes = db_session.query(models.AgentTaskNode).filter_by(root_task_id=run.id).all()
    by_key = {node.client_task_key: node for node in nodes}
    assert run.status == "completed", {
        "nodes": [(node.client_task_key, node.status, node.error_code) for node in nodes],
        "events": [
            (event.event_type, event.summary, event.detail)
            for event in db_session.query(models.AgentTaskEvent)
            .filter_by(root_task_id=run.id).order_by(models.AgentTaskEvent.event_seq)
        ],
        "messages": [
            (message.direction, message.message_type, message.delivery_status, message.payload)
            for message in db_session.query(models.AgentMessage)
            .filter_by(root_task_id=run.id).order_by(models.AgentMessage.id)
        ],
    }
    assert {
        "onboarding-it-reconciliation",
        "onboarding-it-accounts-follow-up",
        "onboarding-it-devices-follow-up",
    } <= set(by_key)
    follow_up = by_key["onboarding-it-reconciliation"]
    assert follow_up.parent_task_id == run.id
    assert follow_up.assignee_agent_id == "VE-0003"
    assert {
        node.assignee_agent_id for node in nodes
        if node.parent_task_id == follow_up.id
    } == {"VE-IT-ACCOUNT", "VE-IT-DEVICE"}
    graph = service.build_graph(db_session, run.id)
    assert graph["current_round"] == 3
    delegation_rounds = [
        event.detail.get("round")
        for event in db_session.query(models.AgentTaskEvent)
        .filter_by(root_task_id=run.id, task_id=run.id, event_type="task.delegate")
        .order_by(models.AgentTaskEvent.event_seq)
    ]
    assert delegation_rounds == [1, 2, 3]


def test_local_demo_completes_the_persisted_collaboration_loop(db_session, monkeypatch):
    """A broken factory, protocol, persistence, tool, or summary link fails this test."""
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    monkeypatch.delenv("AGENTTEAMS_ROOM_ID", raising=False)
    service = AgentTeamsCollaborationService()
    run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title=ROLE_CHANGE_TITLE,
        instruction=ROLE_CHANGE_INSTRUCTION,
        material={},
    )
    run.owner_human_no = "E10281"
    db_session.commit()

    runner = AgentTeamsCollaborationRunner(service=service)
    try:
        assert runner.transport_mode == "local_demo"
        assert runner.room_id == "local-demo"
        for _ in range(40):
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
    diagnostic_messages = [
        (message.direction, message.message_type, message.delivery_status, message.payload)
        for message in db_session.query(models.AgentMessage)
        .filter_by(root_task_id=run.id)
        .order_by(models.AgentMessage.id)
    ]
    diagnostic_events = [
        (event.event_type, event.summary, event.detail)
        for event in db_session.query(models.AgentTaskEvent)
        .filter_by(root_task_id=run.id)
        .order_by(models.AgentTaskEvent.event_seq)
    ]
    assert run.status == "completed", {
        "nodes": [(node.client_task_key, node.status, node.error_code) for node in nodes],
        "rejections": [
            event for event in diagnostic_events
            if "reject" in event[0] or "failed" in event[0]
        ],
        "pending": [
            message for message in diagnostic_messages
            if message[2] not in {"received", "sent"}
        ],
    }
    assert root.status == "completed"
    assert set(by_key) == {
        "root",
        "role-change-hr",
        "role-change-it-plan",
        "role-change-audit",
        "role-change-it-activate",
        "role-change-hr-material",
        "role-change-hr-training",
        "role-change-it-plan-account",
        "role-change-it-plan-device",
        "role-change-audit-review",
        "role-change-it-activate-worker",
    }
    assert {
        node.assignee_agent_id for node in nodes if node.parent_task_id == root.id
    } == {"VE-0002", "VE-0003", "VE-0005"}
    assert {
        node.assignee_agent_id for node in nodes if node.depth == 2
    } == {"VE-HR-MATERIAL", "VE-HR-TRAINING", "VE-IT-ACCOUNT", "VE-IT-DEVICE", "VE-AUDIT-REVIEW"}
    audit = by_key["role-change-audit"]
    activation = by_key["role-change-it-activate"]
    dependency = db_session.query(models.AgentTaskDependency).filter_by(
        task_id=activation.id,
        depends_on_task_id=audit.id,
    ).one_or_none()
    assert dependency is not None
    graph = service.build_graph(db_session, run.id)
    assert graph["current_round"] == 2

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
    assert len(tool_events) == 6
    assert all(event.detail["decision"] == "allow" for event in tool_events)
    audit_ids = [audit_id for event in tool_events for audit_id in event.detail["audit_ids"]]
    assert audit_ids
    audits = db_session.query(models.AuditEvent).filter(
        models.AuditEvent.id.in_(audit_ids)
    ).all()
    assert len(audits) == len(audit_ids)
    assert all(audit.trace_id == run.trace_id for audit in audits)
    assert {audit.employee_id for audit in audits} == {
        "VE-HR-MATERIAL", "VE-HR-TRAINING", "VE-IT-ACCOUNT", "VE-IT-DEVICE", "VE-AUDIT-REVIEW",
    }
    assert {audit.plugin_id for audit in audits} == ROLE_CHANGE_PLUGIN_IDS
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
    assert "岗位调整" in run.summary
    assert "审计" in run.summary
    assert "权限激活" in run.summary
