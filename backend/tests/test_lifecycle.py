"""Sprint 9 生命周期绑定测试：命名规则 + worker 创建/删除（mock subprocess）。"""

import pytest

from app.services import agentteams_lifecycle as at
from app.services.agentteams_gateway import AgentTeamsUnavailableError


def test_worker_name_rules():
    assert at.worker_name("VE-0001", "virtual") == "dwp-ve-0001"
    assert at.worker_name("DT-E10281", "twin") == "dwp-twin-e10281"
    assert at.worker_name("RPA-001", "rpa") == "dwp-rpa-001"
    assert at.matrix_user_id("dwp-ve-0001").startswith("@dwp-ve-0001:")


def test_create_worker_calls_agt(monkeypatch):
    calls: list[list[str]] = []

    def fake_agt(args, timeout=180, input_text=None):
        calls.append(args)
        return ""

    monkeypatch.setattr(at, "_agt", fake_agt)
    states = {"exists": False, "phase": "Pending"}

    def fake_get_worker(name):
        if states["exists"]:
            return {"name": name, "phase": states["phase"]}
        return None

    monkeypatch.setattr(at, "get_worker", fake_get_worker)
    monkeypatch.setattr(at, "_cp_into_container", lambda local: "/tmp/fake-soul.md")

    name = at.create_worker(
        employee_no="VE-0009",
        employee_type="virtual",
        display_name="测试助手",
        soul="你是测试助手。",
        wait_ready=False,
    )
    assert name == "dwp-ve-0009"
    assert any(c[:2] == ["create", "worker"] and "dwp-ve-0009" in c for c in calls)


def test_delete_worker_removes_from_team_first(monkeypatch):
    calls: list[list[str]] = []

    def fake_agt(args, timeout=180, input_text=None):
        calls.append(args)
        return ""

    monkeypatch.setattr(at, "_agt", fake_agt)
    monkeypatch.setattr(
        at,
        "get_worker",
        lambda name: {"name": name, "phase": "Running", "team": "team-onboard"},
    )
    monkeypatch.setattr(
        at,
        "get_team",
        lambda: {
            "name": "team-onboard",
            "leaderName": "dwp-ve-0001",
            "workerMembers": [{"name": "dwp-ve-0001"}, {"name": "dwp-ve-0009"}],
        },
    )
    monkeypatch.setattr(at, "_cp_into_container", lambda local: "/tmp/fake-team.yaml")

    at.delete_worker("dwp-ve-0009")
    assert any(c[:2] == ["apply", "-f"] for c in calls)
    assert any(c[:2] == ["delete", "worker"] and "dwp-ve-0009" in c for c in calls)


def test_get_worker_failure_is_not_misreported_as_missing(monkeypatch):
    def fake_agt(args, timeout=180, input_text=None):
        raise AgentTeamsUnavailableError("agt 执行失败")

    monkeypatch.setattr(at, "_agt", fake_agt)
    with pytest.raises(AgentTeamsUnavailableError):
        at.get_worker("dwp-ve-0001")


def test_reset_agentteams_context_runs_cleanup(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output=None, text=None, timeout=None):
        calls.append(cmd)
        return None

    monkeypatch.setattr(at.shutil, "which", lambda name: "docker")
    monkeypatch.setenv("AGENTTEAMS_MINIO_ACCESS_KEY", "demo-admin")
    monkeypatch.setenv("AGENTTEAMS_MINIO_SECRET_KEY", "demo-secret")
    monkeypatch.setattr(at.subprocess, "run", fake_run)
    at.reset_agentteams_context()
    assert len(calls) == 3
    assert "agentteams-manager" in calls[0] and "state.json" in calls[0][-1]
    assert "shared/tasks/task-" in calls[2][-1]
