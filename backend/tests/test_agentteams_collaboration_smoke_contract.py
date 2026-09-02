from __future__ import annotations

from app import models


ONE_WORKER_HEALTH = {
    "enabled": True,
    "matrix": "ready",
    "room": "ready",
    "manager": "ready",
    "platform_team": "ready",
    "ready_for_demo": True,
    "reason": "",
    "workers": [
        {
            "agent_id": "VE-0001",
            "matrix_user_id": "@ve-0001:test",
            "status": "ready",
        }
    ],
}


def test_health_requires_room_manager_and_two_workers(client, monkeypatch):
    """A raw health stub cannot treat one configured Worker as demo-ready."""
    monkeypatch.setattr(
        "app.routers.agentteams_collaboration.inspect_environment",
        lambda _db: ONE_WORKER_HEALTH,
    )

    response = client.get("/api/v1/agentteams-collaboration/health")

    assert response.status_code == 200
    assert response.json()["ready_for_demo"] is False
    assert "至少需要两个Worker" in response.json()["reason"]


def test_task_submission_cannot_bypass_the_normalized_health_gate(client, db_session, monkeypatch):
    """Task creation must use the same derived readiness as the health route."""
    leader = db_session.get(models.DigitalEmployee, "DT-E10281")
    leader.runtime_ref = "@dwp-twin-e10281:test"
    if not db_session.query(models.TeamMember).filter_by(
        team_id="TEAM-ONBOARD", employee_id=leader.employee_no
    ).first():
        db_session.add(models.TeamMember(
            team_id="TEAM-ONBOARD", employee_id=leader.employee_no, role="member"
        ))
    db_session.flush()
    monkeypatch.setattr(
        "app.routers.agentteams_collaboration.inspect_environment",
        lambda _db: ONE_WORKER_HEALTH,
    )

    response = client.post("/api/v1/agentteams-collaboration/tasks", json={
        "team_id": "TEAM-ONBOARD",
        "leader_agent_id": "DT-E10281",
        "title": "入职协作",
        "instruction": "完成制度和账号核查",
        "material": {},
    })

    assert response.status_code == 503
