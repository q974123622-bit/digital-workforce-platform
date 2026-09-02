from __future__ import annotations

from app import models
from app.services.agentteams_collaboration import AgentTeamsCollaborationService


READY_HEALTH = {
    "enabled": True,
    "matrix": "ready",
    "room": "ready",
    "manager": "configured",
    "platform_team": "ready",
    "ready_for_demo": True,
    "reason": "",
    "workers": [
        {
            "agent_id": "VE-0001",
            "matrix_user_id": "@ve-0001:test",
            "status": "configured",
        },
        {
            "agent_id": "VE-0002",
            "matrix_user_id": "@ve-0002:test",
            "status": "configured",
        },
    ],
}


def _stub_health(monkeypatch, value: dict) -> None:
    """Stub the API boundary, never the Matrix runner or service itself."""
    try:
        from app.routers import agentteams_collaboration
    except ImportError:
        return
    monkeypatch.setattr(agentteams_collaboration, "inspect_environment", lambda _db: value)


def _prepare_leader(db_session):
    leader = db_session.get(models.DigitalEmployee, "DT-E10281")
    leader.runtime_ref = "@dwp-twin-e10281:test"
    if not db_session.query(models.TeamMember).filter_by(
        team_id="TEAM-ONBOARD", employee_id=leader.employee_no
    ).first():
        db_session.add(models.TeamMember(
            team_id="TEAM-ONBOARD", employee_id=leader.employee_no, role="member"
        ))
    db_session.flush()
    return leader


def _create_task(db_session):
    _prepare_leader(db_session)
    return AgentTeamsCollaborationService().create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="DT-E10281",
        title="入职协作",
        instruction="完成制度和账号核查",
        material={"employee": "李四"},
    )


def test_health_reports_matrix_room_manager_and_workers(client):
    response = client.get("/api/v1/agentteams-collaboration/health")

    assert response.status_code == 200
    body = response.json()
    assert {"matrix", "room", "manager", "platform_team", "workers", "ready_for_demo"} <= body.keys()
    assert body["ready_for_demo"] is False


def test_submit_returns_created_agentteams_task(client, db_session, monkeypatch):
    _prepare_leader(db_session)
    _stub_health(monkeypatch, READY_HEALTH)

    response = client.post("/api/v1/agentteams-collaboration/tasks", json={
        "team_id": "TEAM-ONBOARD",
        "leader_agent_id": "DT-E10281",
        "title": "入职协作",
        "instruction": "完成制度和账号核查",
        "material": {},
    })

    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "agentteams"
    assert body["status"] == "planning"
    assert body["nodes"][0]["id"] == body["id"]


def test_submit_returns_503_when_environment_is_not_ready_without_creating_task(
    client, db_session, monkeypatch
):
    _prepare_leader(db_session)
    _stub_health(monkeypatch, {
        **READY_HEALTH,
        "matrix": "unavailable",
        "ready_for_demo": False,
        "reason": "matrix_unavailable",
    })
    before = db_session.query(models.TaskRun).filter_by(source="agentteams").count()

    response = client.post("/api/v1/agentteams-collaboration/tasks", json={
        "team_id": "TEAM-ONBOARD",
        "leader_agent_id": "DT-E10281",
        "title": "入职协作",
        "instruction": "完成制度和账号核查",
        "material": {},
    })

    assert response.status_code == 503
    assert db_session.query(models.TaskRun).filter_by(source="agentteams").count() == before


def test_task_detail_graph_events_messages_and_result_only_use_the_root_id(client, db_session):
    run = _create_task(db_session)

    listed = client.get("/api/v1/agentteams-collaboration/tasks")
    detail = client.get(f"/api/v1/agentteams-collaboration/tasks/{run.id}")
    graph = client.get(f"/api/v1/agentteams-collaboration/tasks/{run.id}/graph")
    events = client.get(f"/api/v1/agentteams-collaboration/tasks/{run.id}/events")
    messages = client.get(f"/api/v1/agentteams-collaboration/tasks/{run.id}/messages")
    result = client.get(f"/api/v1/agentteams-collaboration/tasks/{run.id}/result")

    assert listed.status_code == 200
    assert [task["id"] for task in listed.json()] == [run.id]
    assert detail.status_code == 200
    assert detail.json()["id"] == run.id
    assert [node["root_task_id"] for node in detail.json()["nodes"]] == [run.id]
    assert graph.status_code == 200
    assert graph.json()["nodes"][0]["root_task_id"] == run.id
    assert events.status_code == 200
    assert events.json()[0]["root_task_id"] == run.id
    assert messages.status_code == 200
    assert messages.json()[0]["root_task_id"] == run.id
    assert result.status_code == 200
    assert result.json()["task_id"] == run.id
    assert result.json()["node_results"][0]["root_task_id"] == run.id


def test_event_query_honors_after_event_id(client, db_session):
    run = _create_task(db_session)
    db_session.add(models.AgentTaskEvent(
        root_task_id=run.id,
        task_id=run.id,
        event_seq=2,
        event_type="task.progress",
        actor_agent_id="DT-E10281",
        target_agent_id=None,
        summary="progress updated",
        detail={"progress": 10},
    ))
    db_session.flush()

    all_events = client.get(f"/api/v1/agentteams-collaboration/tasks/{run.id}/events")
    later_events = client.get(
        f"/api/v1/agentteams-collaboration/tasks/{run.id}/events",
        params={"after_event_id": all_events.json()[0]["id"]},
    )

    assert all_events.status_code == 200
    assert later_events.status_code == 200
    assert [event["event_seq"] for event in later_events.json()] == [2]


def test_cancel_only_changes_a_non_terminal_root_task(client, db_session):
    run = _create_task(db_session)

    cancelled = client.post(f"/api/v1/agentteams-collaboration/tasks/{run.id}/cancel")
    repeated = client.post(f"/api/v1/agentteams-collaboration/tasks/{run.id}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert db_session.get(models.AgentTaskNode, run.id).status == "cancelled"
    assert db_session.query(models.AgentTaskEvent).filter_by(
        root_task_id=run.id, event_type="task.cancelled"
    ).count() == 1
    assert db_session.query(models.AgentMessage).filter_by(
        root_task_id=run.id, task_id=run.id, message_type="task.cancel"
    ).count() == 1
    assert repeated.status_code == 409


def test_retry_rejects_completed_task(client, db_session):
    run = _create_task(db_session)
    db_session.get(models.TaskRun, run.id).status = "completed"
    db_session.get(models.AgentTaskNode, run.id).status = "completed"
    db_session.flush()

    response = client.post(f"/api/v1/agentteams-collaboration/tasks/{run.id}/retry")

    assert response.status_code == 409
    assert db_session.get(models.TaskRun, run.id).status == "completed"


def test_unknown_root_task_returns_404_for_every_task_query(client):
    root = "AT-DOES-NOT-EXIST"
    paths = [
        f"/api/v1/agentteams-collaboration/tasks/{root}",
        f"/api/v1/agentteams-collaboration/tasks/{root}/graph",
        f"/api/v1/agentteams-collaboration/tasks/{root}/events",
        f"/api/v1/agentteams-collaboration/tasks/{root}/messages",
        f"/api/v1/agentteams-collaboration/tasks/{root}/result",
        f"/api/v1/agentteams-collaboration/tasks/{root}/cancel",
        f"/api/v1/agentteams-collaboration/tasks/{root}/retry",
    ]

    responses = [client.post(path) if path.endswith(("/cancel", "/retry")) else client.get(path) for path in paths]

    assert [response.status_code for response in responses] == [404] * len(paths)
