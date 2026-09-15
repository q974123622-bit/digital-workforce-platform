from __future__ import annotations

from datetime import datetime, timezone
import json

from app import models
from app.routers import agentteams_collaboration
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


STRICT_HEALTH_ROLES = {
    "manager": ("VE-0001", "agentteams-onboarding-readiness"),
    "hr": ("VE-0002", "hr-onboarding-assistant"),
    "it": ("VE-0003", "it-service-assistant"),
    "audit": ("VE-0005", "audit-evidence-review"),
    "hr_material": ("VE-HR-MATERIAL", "hr-material-specialist"),
    "hr_training": ("VE-HR-TRAINING", "hr-training-specialist"),
    "it_account": ("VE-IT-ACCOUNT", "it-account-specialist"),
    "it_device": ("VE-IT-DEVICE", "it-device-specialist"),
    "audit_review": ("VE-AUDIT-REVIEW", "audit-evidence-review"),
}


def _install_full_strict_health_runtime(db_session, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    identities = {
        agent_id: (f"@{role}:matrix.test", f"{role}-secret")
        for role, (agent_id, _skill_id) in STRICT_HEALTH_ROLES.items()
    }
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps({
        "matrix_url": "https://matrix.test",
        "room_id": "!room:matrix.test",
        "team_id": "TEAM-ONBOARD",
        "platform": {"mxid": "@platform:matrix.test", "token": "platform-secret"},
        "agents": {
            role: {
                "agent_id": agent_id,
                "mxid": identities[agent_id][0],
                "token": identities[agent_id][1],
                "allowed_skill_ids": [skill_id],
                "safe_tool_catalog": [],
            }
            for role, (agent_id, skill_id) in STRICT_HEALTH_ROLES.items()
        },
        "team_catalog": {},
        "team_relationships": {
            "VE-0001": {"role": "Manager", "reports_to": [], "direct_reports": ["VE-0002", "VE-0003", "VE-0005"]},
            "VE-0002": {"role": "HR负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-HR-MATERIAL", "VE-HR-TRAINING"]},
            "VE-0003": {"role": "IT负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-IT-ACCOUNT", "VE-IT-DEVICE"]},
            "VE-0005": {"role": "审计负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-AUDIT-REVIEW"]},
            "VE-HR-MATERIAL": {"role": "HR材料专员", "reports_to": ["VE-0002"], "direct_reports": []},
            "VE-HR-TRAINING": {"role": "HR培训专员", "reports_to": ["VE-0002"], "direct_reports": []},
            "VE-IT-ACCOUNT": {"role": "IT账号专员", "reports_to": ["VE-0003"], "direct_reports": []},
            "VE-IT-DEVICE": {"role": "IT设备专员", "reports_to": ["VE-0003"], "direct_reports": []},
            "VE-AUDIT-REVIEW": {"role": "审计复核专员", "reports_to": ["VE-0005"], "direct_reports": []},
        },
    }), encoding="utf-8")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(config_path))
    for agent_id, (mxid, _token) in identities.items():
        employee = db_session.get(models.DigitalEmployee, agent_id)
        employee.runtime_type, employee.runtime_ref, employee.status = "agentteams", mxid, "active"
    db_session.flush()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    for agent_id, (mxid, token) in identities.items():
        heartbeat = datetime.now(timezone.utc).isoformat()
        (state_dir / f"{agent_id}.json").write_text(json.dumps({
            "agent_id": agent_id,
            "mxid": mxid,
            "heartbeat_at": heartbeat,
            "heartbeat_signature": heartbeat_signature(agent_id, mxid, heartbeat, token),
        }), encoding="utf-8")
    return identities


def test_health_rejects_whitespace_worker_runtime_refs(
    client, db_session, monkeypatch, tmp_path
):
    """Health must resolve runtime refs with the same strictness as authorization."""
    identities = _install_full_strict_health_runtime(
        db_session, monkeypatch, tmp_path
    )

    class ReadyGateway:
        def __init__(self, *, matrix_token, **_kwargs):
            self.token = matrix_token

        def whoami(self):
            expected = {"platform-secret": "@platform:matrix.test"}
            expected.update({token: mxid for mxid, token in identities.values()})
            return expected[self.token]

        def joined_members(self, _room_id):
            return {"@platform:matrix.test", *(mxid for mxid, _token in identities.values())}

        def close(self):
            return None

    monkeypatch.setattr(agentteams_collaboration, "AgentTeamsGateway", ReadyGateway)
    for worker_id, runtime_ref in (("VE-0002", "worker one"), ("VE-0003", "worker two")):
        worker = db_session.get(models.DigitalEmployee, worker_id)
        worker.runtime_ref = runtime_ref
    db_session.flush()

    response = client.get("/api/v1/agentteams-collaboration/health")

    assert response.status_code == 200
    assert response.json()["ready_for_demo"] is False
    assert "平台绑定与运行配置不一致" in response.json()["reason"]


def test_health_closes_every_constructed_gateway_when_membership_check_raises(
    client, db_session, monkeypatch, tmp_path
):
    """The full strict check closes every identity client and fails closed."""
    identities = _install_full_strict_health_runtime(
        db_session, monkeypatch, tmp_path
    )

    class FailingGateway:
        constructed = []

        def __init__(self, *, matrix_token, **_kwargs):
            self.token, self.closed = matrix_token, False
            self.constructed.append(self)

        def whoami(self):
            expected = {"platform-secret": "@platform:matrix.test"}
            expected.update({token: mxid for mxid, token in identities.values()})
            return expected[self.token]

        def joined_members(self, _room_id):
            raise RuntimeError("membership unavailable")

        def close(self):
            self.closed = True

    monkeypatch.setattr(agentteams_collaboration, "AgentTeamsGateway", FailingGateway)

    response = client.get("/api/v1/agentteams-collaboration/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ready_for_demo"] is False
    assert len(FailingGateway.constructed) == 10
    assert all(item.closed for item in FailingGateway.constructed)
    assert "secret" not in json.dumps(body).lower()
