from __future__ import annotations

from app import models
from app.routers import agentteams_collaboration
from app.services.agentteams_gateway import AgentTeamsResult


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
    monkeypatch.setattr(
        "app.routers.agentteams_collaboration.inspect_environment",
        lambda _db: ONE_WORKER_HEALTH,
    )

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": "E10281"},
        json={
            "team_id": "TEAM-ONBOARD",
            "leader_agent_id": "VE-0001",
            "title": "入职协作",
            "instruction": "完成制度和账号核查",
            "material": {},
        },
    )

    assert response.status_code == 503


def _configure_matrix_preflight(monkeypatch) -> None:
    values = {
        agentteams_collaboration.config.AGENTTEAMS_MATRIX_URL: "https://matrix.test",
        agentteams_collaboration.config.AGENTTEAMS_MATRIX_TOKEN: "test-token",
        agentteams_collaboration.config.AGENTTEAMS_ROOM_ID: "!room:matrix.test",
        agentteams_collaboration.config.AGENTTEAMS_MANAGER_MXID: "@manager:matrix.test",
    }
    monkeypatch.setattr(
        agentteams_collaboration.config, "agentteams_collaboration_enabled", lambda: True
    )
    monkeypatch.setattr(
        agentteams_collaboration.config, "get", lambda key, default=None: values.get(key, default)
    )


def test_health_rejects_whitespace_worker_runtime_refs(client, db_session, monkeypatch):
    """Health must resolve runtime refs with the same strictness as authorization."""
    _configure_matrix_preflight(monkeypatch)
    monkeypatch.setattr(
        agentteams_collaboration.AgentTeamsGateway,
        "health",
        lambda _self, _room_id: AgentTeamsResult(ok=True, mode="agentteams", detail="ready"),
    )
    for worker in db_session.query(models.DigitalEmployee).filter_by(runtime_type="agentteams"):
        worker.runtime_type = "demo"
    for worker_id, runtime_ref in (("VE-0002", "worker one"), ("VE-0003", "worker two")):
        worker = db_session.get(models.DigitalEmployee, worker_id)
        worker.runtime_type = "agentteams"
        worker.runtime_ref = runtime_ref
    db_session.flush()

    response = client.get("/api/v1/agentteams-collaboration/health")

    assert response.status_code == 200
    assert response.json()["ready_for_demo"] is False
    assert "至少需要两个Worker" in response.json()["reason"]


def test_health_closes_gateway_when_room_check_raises(client, monkeypatch):
    """Polling health cannot accumulate an httpx client when the room check fails."""
    _configure_matrix_preflight(monkeypatch)

    class FailingGateway:
        closed = False

        def health(self, _room_id):
            raise RuntimeError("room unavailable")

        def close(self):
            self.closed = True

    gateway = FailingGateway()
    monkeypatch.setattr(agentteams_collaboration, "AgentTeamsGateway", lambda: gateway)

    response = client.get("/api/v1/agentteams-collaboration/health")

    assert response.status_code == 200
    assert gateway.closed is True
