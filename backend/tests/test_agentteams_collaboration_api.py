from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from app import models
from app.services.agentteams_collaboration import AgentTeamsCollaborationService
from app.services.agentteams_local_scenario import (
    LOCAL_DEMO_INSTRUCTION,
    LOCAL_DEMO_TITLE,
)


ONBOARDING_ROLES = {
    "manager": "VE-0001",
    "hr": "VE-0002",
    "it": "VE-0003",
    "audit": "VE-0005",
    "hr_material": "VE-HR-MATERIAL",
    "hr_training": "VE-HR-TRAINING",
    "it_account": "VE-IT-ACCOUNT",
    "it_device": "VE-IT-DEVICE",
    "audit_review": "VE-AUDIT-REVIEW",
}
DATABASE_ROLES = {
    "manager": "VE-0001",
    "db_ops_lead": "DB-OPS-LEAD",
    "db_perf_lead": "DB-PERF-LEAD",
    "db_write": "DB-WRITE-01",
    "db_lock": "DB-LOCK-01",
    "db_vacuum": "DB-VACUUM-01",
    "db_index": "DB-INDEX-01",
    "db_query": "DB-QUERY-01",
}


def _strict_runtime_document(team_id="TEAM-ONBOARD") -> dict:
    roles = ONBOARDING_ROLES if team_id == "TEAM-ONBOARD" else DATABASE_ROLES
    document = {
        "matrix_url": "http://matrix.test",
        "room_id": "!team:test",
        "team_id": team_id,
        "platform": {"mxid": "@platform:test", "token": "platform-token"},
        "agents": {
            role: {
                "agent_id": agent_id,
                "mxid": f"@{role}:test",
                "token": f"{role}-token",
                "allowed_skill_ids": [f"{role}-skill"],
                "safe_tool_catalog": [],
            }
            for role, agent_id in roles.items()
        },
        "team_catalog": {},
    }
    if team_id == "TEAM-ONBOARD":
        document["team_relationships"] = {
            "VE-0001": {"role": "Manager", "reports_to": [], "direct_reports": ["VE-0002", "VE-0003", "VE-0005"]},
            "VE-0002": {"role": "HR负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-HR-MATERIAL", "VE-HR-TRAINING"]},
            "VE-0003": {"role": "IT负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-IT-ACCOUNT", "VE-IT-DEVICE"]},
            "VE-0005": {"role": "审计负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-AUDIT-REVIEW"]},
            "VE-HR-MATERIAL": {"role": "HR材料专员", "reports_to": ["VE-0002"], "direct_reports": []},
            "VE-HR-TRAINING": {"role": "HR培训专员", "reports_to": ["VE-0002"], "direct_reports": []},
            "VE-IT-ACCOUNT": {"role": "IT账号专员", "reports_to": ["VE-0003"], "direct_reports": []},
            "VE-IT-DEVICE": {"role": "IT设备专员", "reports_to": ["VE-0003"], "direct_reports": []},
            "VE-AUDIT-REVIEW": {"role": "审计复核专员", "reports_to": ["VE-0005"], "direct_reports": []},
        }
    else:
        document["team_relationships"] = {
            "VE-0001": {"role": "Manager", "reports_to": [], "direct_reports": ["DB-OPS-LEAD", "DB-PERF-LEAD"]},
            "DB-OPS-LEAD": {"role": "数据库运维负责人", "reports_to": ["VE-0001"], "direct_reports": ["DB-WRITE-01", "DB-LOCK-01", "DB-VACUUM-01"]},
            "DB-PERF-LEAD": {"role": "数据库性能负责人", "reports_to": ["VE-0001"], "direct_reports": ["DB-INDEX-01", "DB-QUERY-01"]},
            "DB-WRITE-01": {"role": "写入诊断Worker", "reports_to": ["DB-OPS-LEAD"], "direct_reports": []},
            "DB-LOCK-01": {"role": "锁诊断Worker", "reports_to": ["DB-OPS-LEAD"], "direct_reports": []},
            "DB-VACUUM-01": {"role": "Vacuum诊断Worker", "reports_to": ["DB-OPS-LEAD"], "direct_reports": []},
            "DB-INDEX-01": {"role": "索引诊断Worker", "reports_to": ["DB-PERF-LEAD"], "direct_reports": []},
            "DB-QUERY-01": {"role": "查询诊断Worker", "reports_to": ["DB-PERF-LEAD"], "direct_reports": []},
        }
    return document


def _install_strict_runtime(monkeypatch, tmp_path, *, team_id="TEAM-ONBOARD") -> dict:
    document = _strict_runtime_document(team_id)
    runtime_path = tmp_path / f"{team_id.lower()}-runtime.json"
    runtime_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(runtime_path))
    return document


@pytest.fixture(autouse=True)
def _isolated_strict_runtime(monkeypatch, tmp_path):
    """Never let API tests inherit a developer's credential-bearing runtime file."""
    return _install_strict_runtime(monkeypatch, tmp_path)


READY_HEALTH = {
    "enabled": True,
    "matrix": "ready",
    "room": "ready",
    "manager": "ready",
    "platform_team": "ready",
    "strict_ready": True,
    "configured_team_id": "TEAM-ONBOARD",
    "ready_for_demo": True,
    "reason": "",
    "workers": [
        {"agent_id": agent_id, "matrix_user_id": f"@{role}:test", "status": "ready"}
        for role, agent_id in ONBOARDING_ROLES.items()
    ],
}

ACTOR_NO = "E10281"
UNAUTHORIZED_ACTOR_NO = "E20999"
TEAM_OPERATOR_NOT_CREATOR_NO = "E10021"


def _stub_health(monkeypatch, value: dict) -> None:
    """Stub the API boundary, never the Matrix runner or service itself."""
    try:
        from app.routers import agentteams_collaboration
    except ImportError:
        return
    monkeypatch.setattr(agentteams_collaboration, "inspect_environment", lambda _db: value)


def _prepare_leader(db_session):
    leader = db_session.get(models.DigitalEmployee, "VE-0001")
    leader.runtime_ref = "@manager:test"
    db_session.flush()
    return leader


def _create_task(db_session):
    _prepare_leader(db_session)
    run = AgentTeamsCollaborationService().create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title="入职协作",
        instruction="完成制度和账号核查",
        material={"employee": "李四"},
    )
    run.owner_human_no = ACTOR_NO
    db_session.flush()
    return run


def _prepare_alternative_team(db_session):
    db_session.add(models.Team(
        id="TEAM-ALT",
        name="其他协作团队",
        leader_employee_id="VE-0002",
        description="不兼容固定本地场景的合法团队",
    ))
    db_session.add_all([
        models.TeamMember(team_id="TEAM-ALT", employee_id="VE-0002", role="manager"),
        models.TeamMember(team_id="TEAM-ALT", employee_id="VE-0003", role="leader", reports_to_employee_id="VE-0002"),
        models.TeamMember(team_id="TEAM-ALT", employee_id="VE-0005", role="worker", reports_to_employee_id="VE-0003"),
    ])
    db_session.flush()


def test_health_reports_matrix_room_manager_and_workers(client):
    response = client.get("/api/v1/agentteams-collaboration/health")

    assert response.status_code == 200
    body = response.json()
    assert {"matrix", "room", "manager", "platform_team", "workers", "ready_for_demo"} <= body.keys()
    assert body["ready_for_demo"] is False


def test_local_demo_health_is_ready_without_matrix(client, monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    for name in (
        "AGENTTEAMS_MATRIX_URL",
        "AGENTTEAMS_MATRIX_TOKEN",
        "AGENTTEAMS_ADMIN_USER",
        "AGENTTEAMS_ADMIN_PASSWORD",
        "AGENTTEAMS_ROOM_ID",
        "AGENTTEAMS_MANAGER_MXID",
    ):
        monkeypatch.delenv(name, raising=False)

    response = client.get("/api/v1/agentteams-collaboration/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ready_for_demo"] is True
    assert body["mode"] == "local_demo"
    assert body["transport"] == "local"
    assert body["matrix"] == "not_connected"


def test_local_demo_health_requires_both_seed_team_workers(client, db_session, monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    worker = db_session.query(models.TeamMember).filter_by(
        team_id="TEAM-ONBOARD", employee_id="VE-0003"
    ).one()
    db_session.delete(worker)
    db_session.flush()

    body = client.get("/api/v1/agentteams-collaboration/health").json()

    assert body["ready_for_demo"] is False
    assert "两名" in body["reason"]


def test_local_demo_health_does_not_require_matrix_identity(
    client, db_session, monkeypatch
):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    db_session.get(models.DigitalEmployee, "VE-0003").runtime_ref = "@it:matrix.local"
    db_session.flush()

    body = client.get("/api/v1/agentteams-collaboration/health").json()

    assert body["ready_for_demo"] is True
    assert body["workers"][-1]["status"] == "configured"


def test_local_demo_health_uses_local_wording_for_an_unavailable_leader(
    client, db_session, monkeypatch
):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    db_session.get(models.DigitalEmployee, "VE-0001").status = "disabled"
    db_session.flush()

    body = client.get("/api/v1/agentteams-collaboration/health").json()

    assert body["ready_for_demo"] is False
    assert "本地Demo指定Leader未就绪" in body["reason"]
    assert "MXID" not in body["reason"]


def test_strict_health_still_blocks_without_matrix(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(tmp_path / "missing-runtime.json"))
    for name in (
        "AGENTTEAMS_MATRIX_URL",
        "AGENTTEAMS_MATRIX_TOKEN",
        "AGENTTEAMS_ADMIN_USER",
        "AGENTTEAMS_ADMIN_PASSWORD",
        "AGENTTEAMS_ROOM_ID",
        "AGENTTEAMS_MANAGER_MXID",
    ):
        monkeypatch.delenv(name, raising=False)

    body = client.get("/api/v1/agentteams-collaboration/health").json()

    assert body["ready_for_demo"] is False
    assert body["mode"] == "agentteams_strict"
    assert body["transport"] == "matrix"
    assert body["matrix"] == "not_configured"


def test_strict_health_requires_nine_verified_identities_and_eight_fresh_agent_heartbeats(
    client, db_session, monkeypatch, tmp_path, _isolated_strict_runtime
):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    agents = _isolated_strict_runtime["agents"]
    for identity in agents.values():
        agent_id, mxid = identity["agent_id"], identity["mxid"]
        employee = db_session.get(models.DigitalEmployee, agent_id)
        assert employee is not None
        employee.runtime_ref = mxid
        employee.runtime_type = "agentteams"
        employee.status = "active"
    db_session.flush()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    from app.services.agentteams_agent_runtime import heartbeat_signature
    tokens = {identity["agent_id"]: identity["token"] for identity in agents.values()}
    mxids = {identity["agent_id"]: identity["mxid"] for identity in agents.values()}
    for agent_id in tokens:
        heartbeat_at = datetime.now(timezone.utc).isoformat()
        (state_dir / f"{agent_id}.json").write_text(json.dumps({"agent_id": agent_id, "mxid": mxids[agent_id], "heartbeat_at": heartbeat_at, "heartbeat_signature": heartbeat_signature(agent_id, mxids[agent_id], heartbeat_at, tokens[agent_id])}), encoding="utf-8")

    class FakeGateway:
        def __init__(self, *, matrix_token, **_kwargs):
            self.matrix_token = matrix_token
        def whoami(self):
            identities = [_isolated_strict_runtime["platform"], *agents.values()]
            return {identity["token"]: identity["mxid"] for identity in identities}[self.matrix_token]
        def joined_members(self, _room_id):
            return {
                _isolated_strict_runtime["platform"]["mxid"],
                *(identity["mxid"] for identity in agents.values()),
            }
        def close(self):
            pass

    from app.routers import agentteams_collaboration
    monkeypatch.setattr(agentteams_collaboration, "AgentTeamsGateway", FakeGateway)

    body = client.get("/api/v1/agentteams-collaboration/health").json()

    assert body["ready_for_demo"] is True
    assert {item["agent_id"] for item in body["workers"]} == set(ONBOARDING_ROLES.values())
    assert all(item["status"] == "ready" for item in body["workers"])
    assert "token" not in json.dumps(body).lower()

    (state_dir / "VE-0005.json").unlink()
    stale_body = client.get("/api/v1/agentteams-collaboration/health").json()
    audit_worker = next(item for item in stale_body["workers"] if item["agent_id"] == "VE-0005")

    assert stale_body["ready_for_demo"] is False
    assert audit_worker["status"] == "stale_heartbeat"
    assert "VE-0005未运行或心跳已过期" in stale_body["reason"]
    assert "Matrix ID" not in stale_body["reason"]

    heartbeat_at = datetime.now(timezone.utc).isoformat()
    (state_dir / "VE-0005.json").write_text(json.dumps({
        "agent_id": "VE-0005", "mxid": mxids["VE-0005"], "heartbeat_at": heartbeat_at,
        "heartbeat_signature": heartbeat_signature("VE-0005", mxids["VE-0005"], heartbeat_at, tokens["VE-0005"]),
    }), encoding="utf-8")
    (state_dir / "VE-0001.json").unlink()
    manager_stale = client.get("/api/v1/agentteams-collaboration/health").json()

    assert manager_stale["manager"] == "stale_heartbeat"
    assert manager_stale["ready_for_demo"] is False
    assert "VE-0001未运行或心跳已过期" in manager_stale["reason"]


def test_submit_returns_created_agentteams_task(client, db_session, monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    _prepare_leader(db_session)
    _stub_health(monkeypatch, READY_HEALTH)

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "team_id": "TEAM-ONBOARD",
            "leader_agent_id": "VE-0001",
            "title": "入职协作",
            "instruction": "完成制度和账号核查",
            "material": {},
            "thinking_enabled": False,
        },
    )

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["source"] == "agentteams"
    assert body["status"] == "planning"
    assert body["nodes"][0]["id"] == body["id"]
    submit = db_session.query(models.AgentMessage).filter_by(
        task_id=body["id"], message_type="task.submit"
    ).one()
    assert submit.payload["thinking_enabled"] is False


def test_submit_without_team_lets_manager_select_team_leader(
    client, db_session, monkeypatch
):
    from app.routers import agentteams_collaboration

    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    _stub_health(monkeypatch, READY_HEALTH)
    observed = {}

    class FakeSelectionEngine:
        def select_team(self, context):
            observed["context"] = context
            return SimpleNamespace(
                action="delegate",
                team_id="TEAM-ONBOARD",
                leader_agent_id="VE-0001",
                reason="任务与新员工入职团队的职责匹配。",
                reasoning_content="",
            )

    monkeypatch.setattr(
        agentteams_collaboration,
        "_team_selection_engine",
        lambda: FakeSelectionEngine(),
    )

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "title": "入职协作",
            "instruction": "完成制度和账号核查",
            "material": {},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["team_id"] == "TEAM-ONBOARD"
    assert {item.team_id for item in observed["context"].team_catalog} >= {
        "TEAM-ONBOARD", "TEAM-DB-BENCH"
    }
    root = db_session.get(models.AgentTaskNode, body["id"])
    assert root.assignee_agent_id == "VE-0001"
    selected = db_session.query(models.AgentTaskEvent).filter_by(
        root_task_id=body["id"], event_type="team.selected"
    ).one()
    assert selected.target_agent_id == "VE-0001"
    assert selected.detail == {
        "team_id": "TEAM-ONBOARD",
        "leader_agent_id": "VE-0001",
        "reason": "任务与新员工入职团队的职责匹配。",
        "selection_mode": "manager",
    }


def test_local_demo_manager_selects_its_fixed_team_without_a_model_key(
    client, monkeypatch
):
    from app.routers import agentteams_collaboration

    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    monkeypatch.setattr(
        agentteams_collaboration,
        "_team_selection_engine",
        lambda: (_ for _ in ()).throw(AssertionError("fixed demo must not call a model")),
    )

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "title": LOCAL_DEMO_TITLE,
            "instruction": LOCAL_DEMO_INSTRUCTION,
            "material": {},
        },
    )

    assert response.status_code == 201
    assert response.json()["team_id"] == "TEAM-ONBOARD"


def test_submit_rejects_half_explicit_team_override_without_calling_manager(
    client, monkeypatch
):
    from app.routers import agentteams_collaboration

    monkeypatch.setattr(
        agentteams_collaboration,
        "_team_selection_engine",
        lambda: (_ for _ in ()).throw(AssertionError("manager must not run")),
    )

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "team_id": "TEAM-ONBOARD",
            "title": "入职协作",
            "instruction": "完成制度和账号核查",
            "material": {},
        },
    )

    assert response.status_code == 422


def test_submit_returns_503_when_environment_is_not_ready_without_creating_task(
    client, db_session, monkeypatch
):
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    _prepare_leader(db_session)
    _stub_health(monkeypatch, {
        **READY_HEALTH,
        "matrix": "unavailable",
        "ready_for_demo": False,
        "reason": "matrix_unavailable",
    })
    before = db_session.query(models.TaskRun).filter_by(source="agentteams").count()

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "team_id": "TEAM-ONBOARD",
            "leader_agent_id": "VE-0001",
            "title": "入职协作",
            "instruction": "完成制度和账号核查",
            "material": {},
        },
    )

    assert response.status_code == 503
    assert db_session.query(models.TaskRun).filter_by(source="agentteams").count() == before


def test_submit_rechecks_local_worker_status_from_database(
    client, db_session, monkeypatch
):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    db_session.get(models.DigitalEmployee, "VE-0003").status = "disabled"
    db_session.flush()
    _stub_health(monkeypatch, {
        "mode": "local_demo",
        "enabled": True,
        "matrix": "not_connected",
        "room": "local",
        "manager": "configured",
        "platform_team": "ready",
        "workers": [
            {"agent_id": "VE-0002", "matrix_user_id": "", "status": "configured"},
            {"agent_id": "VE-0003", "matrix_user_id": "", "status": "configured"},
        ],
    })
    before = db_session.query(models.TaskRun).filter_by(source="agentteams").count()

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "team_id": "TEAM-ONBOARD",
            "leader_agent_id": "VE-0001",
            "title": LOCAL_DEMO_TITLE,
            "instruction": LOCAL_DEMO_INSTRUCTION,
            "material": {},
        },
    )

    assert response.status_code == 503
    assert db_session.query(models.TaskRun).filter_by(source="agentteams").count() == before


def test_submit_uses_configured_strict_mode_instead_of_raw_local_health(
    client, db_session, monkeypatch
):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    _stub_health(monkeypatch, {
        "mode": "local_demo",
        "enabled": True,
        "matrix": "not_configured",
        "room": "not_configured",
        "manager": "configured",
        "platform_team": "ready",
        "workers": [
            {"agent_id": "VE-0002", "matrix_user_id": "", "status": "configured"},
            {"agent_id": "VE-0003", "matrix_user_id": "not-an-identity", "status": "configured"},
        ],
    })
    before = db_session.query(models.TaskRun).filter_by(source="agentteams").count()

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "team_id": "TEAM-ONBOARD",
            "leader_agent_id": "VE-0001",
            "title": "不能把严格模式降级为本地模式",
            "instruction": "不应创建",
            "material": {},
        },
    )

    assert response.status_code == 503
    assert db_session.query(models.TaskRun).filter_by(source="agentteams").count() == before


def test_local_demo_and_strict_reject_incompatible_team(
    client, db_session, monkeypatch
):
    _prepare_alternative_team(db_session)
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    payload = {
        "team_id": "TEAM-ALT",
        "leader_agent_id": "VE-0002",
        "title": "其他团队任务",
        "instruction": "固定本地场景不能执行",
        "material": {},
    }
    before = db_session.query(models.TaskRun).filter_by(source="agentteams").count()

    local_response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json=payload,
    )

    assert local_response.status_code == 409
    assert "TEAM-ONBOARD" in local_response.json()["error"]["message"]
    assert db_session.query(models.TaskRun).filter_by(source="agentteams").count() == before

    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    _stub_health(monkeypatch, READY_HEALTH)
    strict_response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json=payload,
    )
    assert strict_response.status_code == 503
    assert db_session.query(models.TaskRun).filter_by(source="agentteams").count() == before


def test_strict_generic_entry_accepts_database_team_and_enqueues_real_scenario(
    client, db_session, monkeypatch, tmp_path
):
    from app.routers import agentteams_collaboration

    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    _install_strict_runtime(monkeypatch, tmp_path, team_id="TEAM-DB-BENCH")
    _stub_health(monkeypatch, {**READY_HEALTH, "configured_team_id": "TEAM-DB-BENCH"})
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        agentteams_collaboration,
        "enqueue_database_collaboration_task",
        lambda task_id, scenario_id: queued.append((task_id, scenario_id)),
    )

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "team_id": "TEAM-DB-BENCH",
            "leader_agent_id": "VE-0001",
            "title": "锁等待诊断",
            "instruction": "请由数据库团队协作定位锁等待根因。",
            "material": {"scenario_id": "lock_contention"},
        },
    )

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["team_id"] == "TEAM-DB-BENCH"
    assert body["status"] == "planning"
    assert queued == [(body["id"], "lock_contention")]


def test_local_demo_generic_entry_accepts_database_team_and_enqueues_real_scenario(
    client, db_session, monkeypatch
):
    from app.routers import agentteams_collaboration

    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        agentteams_collaboration,
        "enqueue_database_collaboration_task",
        lambda task_id, scenario_id: queued.append((task_id, scenario_id)),
    )

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "team_id": "TEAM-DB-BENCH",
            "leader_agent_id": "VE-0001",
            "title": "锁等待诊断",
            "instruction": "请由数据库团队协作定位锁等待根因。",
            "material": {"scenario_id": "lock_contention"},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["team_id"] == "TEAM-DB-BENCH"
    assert queued == [(body["id"], "lock_contention")]


def test_database_team_generic_entry_requires_a_known_scenario(
    client, db_session, monkeypatch
):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    _stub_health(monkeypatch, READY_HEALTH)
    before = db_session.query(models.TaskRun).filter_by(source="agentteams").count()

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "team_id": "TEAM-DB-BENCH",
            "leader_agent_id": "VE-0001",
            "title": "数据库诊断",
            "instruction": "诊断一个不存在的场景。",
            "material": {"scenario_id": "unknown"},
        },
    )

    assert response.status_code == 422
    assert "数据库诊断场景" in response.json()["error"]["message"]
    assert db_session.query(models.TaskRun).filter_by(source="agentteams").count() == before

def test_local_demo_rejects_non_fixed_business_input_without_creating_task(
    client, db_session, monkeypatch
):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    payload = {
        "team_id": "TEAM-ONBOARD",
        "leader_agent_id": "VE-0001",
        "title": "任意销售分析",
        "instruction": "分析本季度销售数据并给出预测",
        "material": {"quarter": "Q3"},
    }
    before = db_session.query(models.TaskRun).filter_by(source="agentteams").count()

    local_response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json=payload,
    )

    assert local_response.status_code == 409
    assert "固定入职演示剧本" in local_response.json()["error"]["message"]
    assert db_session.query(models.TaskRun).filter_by(source="agentteams").count() == before

    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    _stub_health(monkeypatch, READY_HEALTH)
    strict_response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json=payload,
    )
    assert strict_response.status_code == 201
    assert strict_response.json()["title"] == "任意销售分析"


def test_local_demo_retry_rejects_task_from_an_incompatible_team(
    client, db_session, monkeypatch
):
    _prepare_alternative_team(db_session)
    service = AgentTeamsCollaborationService()
    run = service.create_task(
        db_session,
        team_id="TEAM-ALT",
        leader_agent_id="VE-0002",
        title="失败的其他团队任务",
        instruction="不能切换到固定本地场景重试",
        material={},
    )
    run.owner_human_no = ACTOR_NO
    run.status = "failed"
    db_session.get(models.AgentTaskNode, run.id).status = "failed"
    db_session.commit()
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    before = db_session.query(models.TaskRun).filter_by(source="agentteams").count()

    response = client.post(
        f"/api/v1/agentteams-collaboration/tasks/{run.id}/retry",
        params={"actor_no": ACTOR_NO},
    )

    assert response.status_code == 409
    assert "TEAM-ONBOARD" in response.json()["error"]["message"]
    assert db_session.query(models.TaskRun).filter_by(source="agentteams").count() == before


def test_task_detail_graph_events_messages_and_result_only_use_the_root_id(client, db_session):
    run = _create_task(db_session)

    listed = client.get("/api/v1/agentteams-collaboration/tasks", params={"actor_no": ACTOR_NO})
    detail = client.get(f"/api/v1/agentteams-collaboration/tasks/{run.id}", params={"actor_no": ACTOR_NO})
    graph = client.get(f"/api/v1/agentteams-collaboration/tasks/{run.id}/graph", params={"actor_no": ACTOR_NO})
    events = client.get(f"/api/v1/agentteams-collaboration/tasks/{run.id}/events", params={"actor_no": ACTOR_NO})
    messages = client.get(f"/api/v1/agentteams-collaboration/tasks/{run.id}/messages", params={"actor_no": ACTOR_NO})
    result = client.get(f"/api/v1/agentteams-collaboration/tasks/{run.id}/result", params={"actor_no": ACTOR_NO})

    assert listed.status_code == 200
    assert [task["id"] for task in listed.json()] == [run.id]
    assert detail.status_code == 200
    assert detail.json()["id"] == run.id
    assert [node["root_task_id"] for node in detail.json()["nodes"]] == [run.id]
    assert [node["client_task_key"] for node in detail.json()["nodes"]] == ["root"]
    assert graph.status_code == 200
    assert graph.json()["nodes"][0]["root_task_id"] == run.id
    assert graph.json()["nodes"][0]["client_task_key"] == "root"
    assert events.status_code == 200
    assert events.json()[0]["root_task_id"] == run.id
    assert messages.status_code == 200
    assert messages.json()[0]["root_task_id"] == run.id
    assert result.status_code == 200
    assert result.json()["task_id"] == run.id
    assert result.json()["node_results"][0]["root_task_id"] == run.id


def test_terminal_task_evaluation_is_persisted_and_reused(client, db_session, monkeypatch):
    run = _create_task(db_session)
    run.status = "completed"
    run.summary = "岗位调整可以按期完成。"
    root = db_session.get(models.AgentTaskNode, run.id)
    root.status = "completed"
    root.title = "岗位调整评估"
    root.instruction = "评估张明的岗位调整是否可以按期完成。"
    root.input_payload = {
        "coordination": {
            "round": 2,
            "selected_skill_ids": ["agentteams-role-change-readiness"],
            "team_catalog_version": 1,
        }
    }
    db_session.flush()
    expected = {
        "version": "agentteams-evaluation-v1",
        "evaluated_at": "2026-09-07T09:00:00+00:00",
        "rule_score": 50,
        "rule_max_score": 60,
        "rule_dimensions": [],
        "rule_deductions": ["缺少串行依赖。"],
        "model_status": "completed",
        "model_score": 34,
        "model_max_score": 40,
        "model_dimensions": [],
        "model_strengths": ["拆解清楚"],
        "model_deductions": ["结论可以更精简"],
        "model_verdict": "协作过程完整。",
        "total_score": 84,
        "total_max_score": 100,
    }

    class FakeEvaluator:
        calls = 0

        def evaluate(self, **_kwargs):
            self.calls += 1
            return expected

    evaluator = FakeEvaluator()
    from app.routers import agentteams_collaboration
    monkeypatch.setattr(agentteams_collaboration, "_build_evaluator", lambda: evaluator)
    path = f"/api/v1/agentteams-collaboration/tasks/{run.id}/evaluation"

    first = client.post(path, params={"actor_no": ACTOR_NO})
    second = client.post(path, params={"actor_no": ACTOR_NO})

    assert first.status_code == 200
    assert first.json() == expected
    assert second.json() == expected
    assert evaluator.calls == 1
    events = db_session.query(models.AgentTaskEvent).filter_by(
        root_task_id=run.id, event_type="task.evaluation.completed"
    ).all()
    assert len(events) == 1
    assert events[0].detail == expected


def test_partial_evaluation_is_persisted_but_retried_until_model_completes(client, db_session, monkeypatch):
    run = _create_task(db_session)
    run.status = "completed"
    run.summary = "岗位调整可以按期完成。"
    root = db_session.get(models.AgentTaskNode, run.id)
    root.status = "completed"
    root.title = "岗位调整评估"
    root.instruction = "评估张明的岗位调整是否可以按期完成。"
    root.input_payload = {"coordination": {"selected_skill_ids": ["agentteams-role-change-readiness"]}}
    db_session.flush()
    base = {
        "version": "agentteams-evaluation-v1", "evaluated_at": "2026-09-07T09:00:00+00:00",
        "rule_score": 50, "rule_max_score": 60, "rule_dimensions": [], "rule_deductions": [],
        "model_score": None, "model_max_score": 40, "model_dimensions": [], "model_strengths": [],
        "model_deductions": ["大模型评测暂不可用。"], "model_verdict": "",
        "total_score": None, "total_max_score": 100,
    }
    completed = {**base, "model_status": "completed", "model_score": 35, "model_deductions": [], "total_score": 85}

    class FakeEvaluator:
        calls = 0
        def evaluate(self, **_kwargs):
            self.calls += 1
            return {**base, "model_status": "unavailable"} if self.calls == 1 else completed

    evaluator = FakeEvaluator()
    from app.routers import agentteams_collaboration
    monkeypatch.setattr(agentteams_collaboration, "_build_evaluator", lambda: evaluator)
    path = f"/api/v1/agentteams-collaboration/tasks/{run.id}/evaluation"

    first = client.post(path, params={"actor_no": ACTOR_NO})
    second = client.post(path, params={"actor_no": ACTOR_NO})
    third = client.post(path, params={"actor_no": ACTOR_NO})

    assert first.json()["model_status"] == "unavailable"
    assert second.json()["model_status"] == "completed"
    assert third.json()["model_status"] == "completed"
    assert evaluator.calls == 2
    assert db_session.query(models.AgentTaskEvent).filter_by(root_task_id=run.id, event_type="task.evaluation.partial").count() == 1
    assert db_session.query(models.AgentTaskEvent).filter_by(root_task_id=run.id, event_type="task.evaluation.completed").count() == 1


def test_evaluation_rejects_demo_task_with_user_material(client, db_session):
    run = _create_task(db_session)
    run.status = "completed"
    run.summary = "岗位调整可以按期完成。"
    root = db_session.get(models.AgentTaskNode, run.id)
    root.status = "completed"
    root.title = "岗位调整评估"
    root.instruction = "评估张明的岗位调整是否可以按期完成。"
    root.input_payload = {
        "employee_phone": "13800138000",
        "coordination": {"selected_skill_ids": ["agentteams-role-change-readiness"]},
    }
    db_session.flush()

    response = client.post(
        f"/api/v1/agentteams-collaboration/tasks/{run.id}/evaluation",
        params={"actor_no": ACTOR_NO},
    )

    assert response.status_code == 422
    assert "虚构演示任务" in response.json()["error"]["message"]


def test_evaluation_requires_a_terminal_owned_task(client, db_session, monkeypatch):
    run = _create_task(db_session)
    path = f"/api/v1/agentteams-collaboration/tasks/{run.id}/evaluation"

    assert client.post(path, params={"actor_no": ACTOR_NO}).status_code == 409
    assert client.post(path, params={"actor_no": UNAUTHORIZED_ACTOR_NO}).status_code == 403


def test_graph_returns_persisted_round_and_selected_skills(client, db_session):
    run = _create_task(db_session)
    root = db_session.get(models.AgentTaskNode, run.id)
    root.input_payload = {
        "coordination": {
            "round": 2,
            "selected_skill_ids": ["agentteams-onboarding-readiness"],
        }
    }
    db_session.commit()

    response = client.get(
        f"/api/v1/agentteams-collaboration/tasks/{run.id}/graph",
        params={"actor_no": ACTOR_NO},
    )

    assert response.status_code == 200
    assert response.json()["current_round"] == 2
    assert response.json()["selected_skill_ids"] == ["agentteams-onboarding-readiness"]


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

    all_events = client.get(
        f"/api/v1/agentteams-collaboration/tasks/{run.id}/events",
        params={"actor_no": ACTOR_NO},
    )
    later_events = client.get(
        f"/api/v1/agentteams-collaboration/tasks/{run.id}/events",
        params={"actor_no": ACTOR_NO, "after_event_id": all_events.json()[0]["id"]},
    )

    assert all_events.status_code == 200
    assert later_events.status_code == 200
    assert [event["event_seq"] for event in later_events.json()] == [2]


def test_cancel_only_changes_a_non_terminal_root_task(client, db_session):
    run = _create_task(db_session)

    cancelled = client.post(
        f"/api/v1/agentteams-collaboration/tasks/{run.id}/cancel",
        params={"actor_no": ACTOR_NO},
    )
    repeated = client.post(
        f"/api/v1/agentteams-collaboration/tasks/{run.id}/cancel",
        params={"actor_no": ACTOR_NO},
    )

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

    response = client.post(
        f"/api/v1/agentteams-collaboration/tasks/{run.id}/retry",
        params={"actor_no": ACTOR_NO},
    )

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

    responses = [
        client.post(path, params={"actor_no": ACTOR_NO})
        if path.endswith(("/cancel", "/retry"))
        else client.get(path, params={"actor_no": ACTOR_NO})
        for path in paths
    ]

    assert [response.status_code for response in responses] == [404] * len(paths)


def test_task_endpoints_reject_missing_and_untrusted_callers(client, db_session):
    run = _create_task(db_session)
    base = "/api/v1/agentteams-collaboration/tasks"
    task_paths = [
        base,
        f"{base}/{run.id}",
        f"{base}/{run.id}/graph",
        f"{base}/{run.id}/events",
        f"{base}/{run.id}/messages",
        f"{base}/{run.id}/result",
        f"{base}/{run.id}/cancel",
        f"{base}/{run.id}/retry",
    ]

    for path in task_paths:
        response = client.post(path) if path.endswith(("/cancel", "/retry")) else client.get(path)
        assert response.status_code == 422

    listed = client.get(base, params={"actor_no": UNAUTHORIZED_ACTOR_NO})
    assert listed.status_code == 200
    assert listed.json() == []

    for path in task_paths[1:]:
        response = (
            client.post(path, params={"actor_no": UNAUTHORIZED_ACTOR_NO})
            if path.endswith(("/cancel", "/retry"))
            else client.get(path, params={"actor_no": UNAUTHORIZED_ACTOR_NO})
        )
        assert response.status_code == 403

    unknown = client.get(base, params={"actor_no": "E-UNKNOWN"})
    assert unknown.status_code == 404


def test_submit_rejects_a_team_member_substituted_for_the_designated_leader(
    client, db_session, monkeypatch
):
    _prepare_leader(db_session)
    _stub_health(monkeypatch, READY_HEALTH)
    before = db_session.query(models.TaskRun).filter_by(source="agentteams").count()

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "team_id": "TEAM-ONBOARD",
            "leader_agent_id": "DT-E10281",
            "title": "越权负责人替换",
            "instruction": "不应创建",
            "material": {},
        },
    )

    assert response.status_code == 403
    assert db_session.query(models.TaskRun).filter_by(source="agentteams").count() == before


def test_creator_exclusively_owns_agentteams_tasks_after_creation(client, db_session, monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    _stub_health(monkeypatch, READY_HEALTH)
    created = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "team_id": "TEAM-ONBOARD",
            "leader_agent_id": "VE-0001",
            "title": "创建者专属协作任务",
            "instruction": "仅创建者可操作",
            "material": {},
        },
    )

    assert created.status_code == 201
    task_id = created.json()["id"]
    assert client.get(
        f"/api/v1/agentteams-collaboration/tasks/{task_id}",
        params={"actor_no": ACTOR_NO},
    ).status_code == 200

    for actor_no in (TEAM_OPERATOR_NOT_CREATOR_NO, UNAUTHORIZED_ACTOR_NO):
        listed = client.get("/api/v1/agentteams-collaboration/tasks", params={"actor_no": actor_no})
        assert listed.status_code == 200
        assert listed.json() == []
        for suffix in ("", "/graph", "/events", "/messages", "/result", "/cancel", "/retry"):
            path = f"/api/v1/agentteams-collaboration/tasks/{task_id}{suffix}"
            response = (
                client.post(path, params={"actor_no": actor_no})
                if suffix in {"/cancel", "/retry"}
                else client.get(path, params={"actor_no": actor_no})
            )
            assert response.status_code == 403


def test_submit_rejects_an_inactive_human_actor(client, db_session, monkeypatch):
    db_session.get(models.HumanEmployee, ACTOR_NO).status = "inactive"
    db_session.flush()
    _stub_health(monkeypatch, READY_HEALTH)

    response = client.post(
        "/api/v1/agentteams-collaboration/tasks",
        params={"actor_no": ACTOR_NO},
        json={
            "team_id": "TEAM-ONBOARD",
            "leader_agent_id": "VE-0001",
            "title": "停用身份不能创建",
            "instruction": "不应创建",
            "material": {},
        },
    )

    assert response.status_code == 403
