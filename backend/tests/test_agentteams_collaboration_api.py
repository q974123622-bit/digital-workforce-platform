from __future__ import annotations

from datetime import datetime, timezone
import json

from app import models
from app.services.agentteams_collaboration import AgentTeamsCollaborationService


READY_HEALTH = {
    "enabled": True,
    "matrix": "ready",
    "room": "ready",
    "manager": "configured",
    "platform_team": "ready",
    "strict_ready": True,
    "configured_team_id": "TEAM-ONBOARD",
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
    run = AgentTeamsCollaborationService().create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="DT-E10281",
        title="入职协作",
        instruction="完成制度和账号核查",
        material={"employee": "李四"},
    )
    run.owner_human_no = ACTOR_NO
    db_session.flush()
    return run


def test_health_reports_matrix_room_manager_and_workers(client):
    response = client.get("/api/v1/agentteams-collaboration/health")

    assert response.status_code == 200
    body = response.json()
    assert {"matrix", "room", "manager", "platform_team", "workers", "ready_for_demo"} <= body.keys()
    assert body["ready_for_demo"] is False


def test_strict_health_requires_five_verified_identities_and_fresh_agent_heartbeats(
    client, db_session, monkeypatch, tmp_path
):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    skills = {
        "manager": "agentteams-onboarding-readiness", "hr": "hr-onboarding-assistant",
        "it": "it-service-assistant", "audit": "audit-evidence-review",
    }
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps({
        "matrix_url": "http://matrix.test", "room_id": "!team:test", "team_id": "TEAM-ONBOARD",
        "platform": {"mxid": "@platform:test", "token": "platform-token"},
        "agents": {
            "manager": {"agent_id": "VE-0001", "mxid": "@manager:test", "token": "manager-token", "allowed_skill_ids": [skills["manager"]], "safe_tool_catalog": []},
            "hr": {"agent_id": "VE-0002", "mxid": "@hr:test", "token": "hr-token", "allowed_skill_ids": [skills["hr"]], "safe_tool_catalog": []},
            "it": {"agent_id": "VE-0003", "mxid": "@it:test", "token": "it-token", "allowed_skill_ids": [skills["it"]], "safe_tool_catalog": []},
            "audit": {"agent_id": "VE-0005", "mxid": "@audit:test", "token": "audit-token", "allowed_skill_ids": [skills["audit"]], "safe_tool_catalog": []},
        }, "team_catalog": {},
    }), encoding="utf-8")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(config_path))
    for agent_id, mxid in (("VE-0001", "@manager:test"), ("VE-0002", "@hr:test"), ("VE-0003", "@it:test"), ("VE-0005", "@audit:test")):
        employee = db_session.get(models.DigitalEmployee, agent_id)
        employee.runtime_ref, employee.runtime_type, employee.status = mxid, "agentteams", "active"
    db_session.flush()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    from app.services.agentteams_agent_runtime import heartbeat_signature
    tokens = {"VE-0001": "manager-token", "VE-0002": "hr-token", "VE-0003": "it-token", "VE-0005": "audit-token"}
    mxids = {"VE-0001": "@manager:test", "VE-0002": "@hr:test", "VE-0003": "@it:test", "VE-0005": "@audit:test"}
    for agent_id in tokens:
        heartbeat_at = datetime.now(timezone.utc).isoformat()
        (state_dir / f"{agent_id}.json").write_text(json.dumps({"agent_id": agent_id, "mxid": mxids[agent_id], "heartbeat_at": heartbeat_at, "heartbeat_signature": heartbeat_signature(agent_id, mxids[agent_id], heartbeat_at, tokens[agent_id])}), encoding="utf-8")

    class FakeGateway:
        def __init__(self, *, matrix_token, **_kwargs):
            self.matrix_token = matrix_token
        def whoami(self):
            return {
                "platform-token": "@platform:test", "manager-token": "@manager:test",
                "hr-token": "@hr:test", "it-token": "@it:test", "audit-token": "@audit:test",
            }[self.matrix_token]
        def joined_members(self, _room_id):
            return {"@platform:test", "@manager:test", "@hr:test", "@it:test", "@audit:test"}
        def close(self):
            pass

    from app.routers import agentteams_collaboration
    monkeypatch.setattr(agentteams_collaboration, "AgentTeamsGateway", FakeGateway)
    body = client.get("/api/v1/agentteams-collaboration/health").json()
    assert body["ready_for_demo"] is True
    assert {item["agent_id"] for item in body["workers"]} == {"VE-0001", "VE-0002", "VE-0003", "VE-0005"}
    assert all(item["status"] == "ready" for item in body["workers"])
    assert "token" not in json.dumps(body).lower()

def test_submit_returns_created_agentteams_task(client, db_session, monkeypatch):
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
        },
    )

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
