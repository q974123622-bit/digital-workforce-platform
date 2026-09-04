from __future__ import annotations

from datetime import datetime, timezone
import json

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


def test_health_closes_every_constructed_gateway_when_membership_check_raises(
    client, db_session, monkeypatch, tmp_path
):
    """A five-account strict check closes all clients and fails closed on membership error."""
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps({
        "matrix_url": "https://matrix.test", "room_id": "!room:matrix.test", "team_id": "TEAM-ONBOARD",
        "platform": {"mxid": "@platform:matrix.test", "token": "platform-secret"},
        "agents": {
            "manager": {"agent_id": "VE-0001", "mxid": "@manager:matrix.test", "token": "manager-secret"},
            "hr": {"agent_id": "VE-0002", "mxid": "@hr:matrix.test", "token": "hr-secret"},
            "it": {"agent_id": "VE-0003", "mxid": "@it:matrix.test", "token": "it-secret"},
            "audit": {"agent_id": "VE-0005", "mxid": "@audit:matrix.test", "token": "audit-secret"},
        }, "team_catalog": {},
    }), encoding="utf-8")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(config_path))
    for agent_id, mxid in (("VE-0001", "@manager:matrix.test"), ("VE-0002", "@hr:matrix.test"), ("VE-0003", "@it:matrix.test"), ("VE-0005", "@audit:matrix.test")):
        employee = db_session.get(models.DigitalEmployee, agent_id)
        employee.runtime_type, employee.runtime_ref, employee.status = "agentteams", mxid, "active"
    db_session.flush()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    for agent_id in ("VE-0001", "VE-0002", "VE-0003", "VE-0005"):
        (state_dir / f"{agent_id}.json").write_text(json.dumps({"heartbeat_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")

    class FailingGateway:
        constructed = []

        def __init__(self, *, matrix_token, **_kwargs):
            self.token, self.closed = matrix_token, False
            self.constructed.append(self)

        def whoami(self):
            return {
                "platform-secret": "@platform:matrix.test", "manager-secret": "@manager:matrix.test",
                "hr-secret": "@hr:matrix.test", "it-secret": "@it:matrix.test", "audit-secret": "@audit:matrix.test",
            }[self.token]

        def joined_members(self, _room_id):
            raise RuntimeError("membership unavailable")

        def close(self):
            self.closed = True

    monkeypatch.setattr(agentteams_collaboration, "AgentTeamsGateway", FailingGateway)

    response = client.get("/api/v1/agentteams-collaboration/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ready_for_demo"] is False
    assert len(FailingGateway.constructed) == 5
    assert all(item.closed for item in FailingGateway.constructed)
    assert "secret" not in json.dumps(body).lower()
