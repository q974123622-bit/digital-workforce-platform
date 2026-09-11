from __future__ import annotations

from datetime import datetime, timezone
import json

from app import models
from app.routers import agentteams_collaboration
from app.services.agentteams_gateway import AgentTeamsResult
from app.services.agentteams_agent_runtime import heartbeat_signature
from app.services.agentteams_local_scenario import LOCAL_DEMO_INSTRUCTION, LOCAL_DEMO_TITLE


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
    assert "至少需要两名指定Worker" in response.json()["reason"]


def test_health_normalises_unknown_mode_to_unready_strict(client, monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "typo")
    monkeypatch.delenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", raising=False)

    response = client.get("/api/v1/agentteams-collaboration/health")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "agentteams_strict"
    assert body["strict_ready"] is False
    assert body["ready_for_demo"] is False


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
            "title": LOCAL_DEMO_TITLE,
            "instruction": LOCAL_DEMO_INSTRUCTION,
            "material": {},
        },
    )

    assert response.status_code == 503


def test_strict_readiness_for_one_team_cannot_authorize_another_team(client, db_session, monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    monkeypatch.setattr(
        "app.routers.agentteams_collaboration.inspect_environment",
        lambda _db: {
            "enabled": True, "matrix": "ready", "room": "ready", "manager": "ready",
            "platform_team": "ready", "strict_ready": True, "configured_team_id": "TEAM-ONBOARD",
            "workers": [
                {"agent_id": agent_id, "matrix_user_id": mxid, "status": "ready"}
                for agent_id, mxid in (
                    ("VE-0001", "@manager:test"), ("VE-0002", "@hr:test"),
                    ("VE-0003", "@it:test"), ("VE-0005", "@audit:test"),
                )
            ],
        },
    )
    db_session.add(models.Team(
        id="TEAM-ALT", name="Other team", leader_employee_id="VE-0002", description="strict gate test",
    ))
    db_session.add_all([
        models.TeamMember(team_id="TEAM-ALT", employee_id="VE-0002", role="leader"),
        models.TeamMember(team_id="TEAM-ALT", employee_id="VE-0003", role="worker"),
    ])
    db_session.flush()

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": "E10281"},
        json={
            "team_id": "TEAM-ALT", "leader_agent_id": "VE-0002", "title": "其他团队",
            "instruction": "strict readiness must remain bound", "material": {},
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
    assert "至少需要两名指定Worker" in response.json()["reason"]


def test_health_closes_every_constructed_gateway_when_membership_check_raises(
    client, db_session, monkeypatch, tmp_path
):
    """A five-account strict check closes all clients and fails closed on membership error."""
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    config_path = tmp_path / "runtime.json"
    skills = {
        "manager": "agentteams-onboarding-readiness", "hr": "hr-onboarding-assistant",
        "it": "it-service-assistant", "audit": "audit-evidence-review",
    }
    config_path.write_text(json.dumps({
        "matrix_url": "https://matrix.test", "room_id": "!room:matrix.test", "team_id": "TEAM-ONBOARD",
        "platform": {"mxid": "@platform:matrix.test", "token": "platform-secret"},
        "agents": {
            "manager": {"agent_id": "VE-0001", "mxid": "@manager:matrix.test", "token": "manager-secret", "allowed_skill_ids": [skills["manager"]], "safe_tool_catalog": []},
            "hr": {"agent_id": "VE-0002", "mxid": "@hr:matrix.test", "token": "hr-secret", "allowed_skill_ids": [skills["hr"]], "safe_tool_catalog": []},
            "it": {"agent_id": "VE-0003", "mxid": "@it:matrix.test", "token": "it-secret", "allowed_skill_ids": [skills["it"]], "safe_tool_catalog": []},
            "audit": {"agent_id": "VE-0005", "mxid": "@audit:matrix.test", "token": "audit-secret", "allowed_skill_ids": [skills["audit"]], "safe_tool_catalog": []},
        }, "team_catalog": {},
    }), encoding="utf-8")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(config_path))
    for agent_id, mxid in (("VE-0001", "@manager:matrix.test"), ("VE-0002", "@hr:matrix.test"), ("VE-0003", "@it:matrix.test"), ("VE-0005", "@audit:matrix.test")):
        employee = db_session.get(models.DigitalEmployee, agent_id)
        employee.runtime_type, employee.runtime_ref, employee.status = "agentteams", mxid, "active"
    db_session.flush()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    identities = {
        "VE-0001": ("@manager:matrix.test", "manager-secret"),
        "VE-0002": ("@hr:matrix.test", "hr-secret"),
        "VE-0003": ("@it:matrix.test", "it-secret"),
        "VE-0005": ("@audit:matrix.test", "audit-secret"),
    }
    for agent_id, (mxid, token) in identities.items():
        heartbeat = datetime.now(timezone.utc).isoformat()
        (state_dir / f"{agent_id}.json").write_text(json.dumps({
            "agent_id": agent_id, "mxid": mxid, "heartbeat_at": heartbeat,
            "heartbeat_signature": heartbeat_signature(agent_id, mxid, heartbeat, token),
        }), encoding="utf-8")

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
