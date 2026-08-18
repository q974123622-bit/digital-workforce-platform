"""SandboxManager 测试（mock 检测器与 subprocess，不依赖本机 daemon）。

覆盖：docker 可用 → mode=docker；docker 不可用 → mode=local 且审计含 mode=local；
拒绝路径 403 且不调用容器启动；执行超时 → 返回 timeout 且不挂起。
"""

import subprocess
import time
from types import SimpleNamespace

import pytest

from app import models
from app.services import sandbox_manager
from app.services.sandbox_manager import SandboxManager


class FakePopen:
    instances = []

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.returncode = kwargs.pop("fake_returncode", 0)
        FakePopen.instances.append(self)

    def communicate(self, timeout=None):
        if self.kwargs.get("fake_timeout"):
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        return (
            self.kwargs.get("fake_stdout", "hello from container"),
            self.kwargs.get("fake_stderr", ""),
        )

    def kill(self):
        pass

    def wait(self, timeout=None):
        pass


class FakeRun:
    calls = []

    @classmethod
    def reset(cls):
        cls.calls = []

    @classmethod
    def fake(cls, *args, **kwargs):
        cls.calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture(autouse=True)
def _reset_fakes():
    FakePopen.instances = []
    FakeRun.reset()
    yield


def _execute(**overrides):
    kwargs = dict(
        employee_id="DT-E10281",
        command="echo hi",
        mount_dir="",
        network="none",
        execution_location="remote",
        trace_id="T-SBX-TEST",
    )
    kwargs.update(overrides)
    return SandboxManager(exec_timeout=0.5).execute(**kwargs)


def _run(client, task_id, **overrides):
    payload = {
        "employee_id": "DT-E10281",
        "task_id": task_id,
        "command": "echo hi",
        "mount_dir": "",
        "network": "none",
        "execution_location": "remote",
    }
    payload.update(overrides)
    return client.post("/internal/sandbox/run", json=payload)


def test_docker_available_mode_docker(monkeypatch):
    monkeypatch.setattr(sandbox_manager, "docker_available", lambda timeout=3.0: True)
    monkeypatch.setattr(sandbox_manager.subprocess, "Popen", FakePopen)
    result = _execute()
    assert result["mode"] == "docker"
    assert result["status"] == "ok"
    assert "hello from container" in result["logs"]
    cmd = FakePopen.instances[0].cmd
    assert cmd[:2] == ["docker", "run"]
    assert "--network" in cmd
    assert "none" in cmd
    assert "-v" in cmd
    assert "python:3.11-slim" in cmd
    assert cmd[-1] == "echo hi"


def test_docker_mode_through_api_with_audit(client, db_session, monkeypatch):
    monkeypatch.setattr(sandbox_manager, "docker_available", lambda timeout=3.0: True)
    monkeypatch.setattr(sandbox_manager.subprocess, "Popen", FakePopen)
    resp = _run(client, "T-DOCKER")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "docker"
    events = (
        db_session.query(models.AuditEvent)
        .filter(models.AuditEvent.trace_id == "SBX-T-DOCKER")
        .all()
    )
    assert events
    assert any("mode=docker" in (e.result_summary or "") for e in events)


def test_docker_unavailable_mode_local_with_audit(client, db_session, monkeypatch):
    monkeypatch.setattr(sandbox_manager, "docker_available", lambda timeout=3.0: False)
    resp = _run(client, "T-LOCAL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "local"
    assert body["status"] == "ok"
    events = (
        db_session.query(models.AuditEvent)
        .filter(models.AuditEvent.trace_id == "SBX-T-LOCAL")
        .all()
    )
    assert events
    assert any("mode=local" in (e.result_summary or "") for e in events)


def test_deny_local_execution_does_not_start_container(client, monkeypatch):
    calls = []

    def _boom(*args, **kwargs):
        calls.append(1)
        raise AssertionError("容器启动函数不应被调用")

    monkeypatch.setattr(sandbox_manager, "docker_available", _boom)
    resp = _run(client, "T-DENY-L", execution_location="local")
    assert resp.status_code == 403
    assert resp.json()["error"]["detail"]["policy_id"] == "POLICY-004"
    assert calls == []


def test_deny_internet_network_does_not_start_container(client, monkeypatch):
    calls = []

    def _boom(*args, **kwargs):
        calls.append(1)
        raise AssertionError("容器启动函数不应被调用")

    monkeypatch.setattr(sandbox_manager, "docker_available", _boom)
    resp = _run(client, "T-DENY-N", network="bridge")
    assert resp.status_code == 403
    assert resp.json()["error"]["detail"]["policy_id"] == "POLICY-003"
    assert calls == []


def test_execution_timeout_returns_timeout_without_hanging(monkeypatch):
    monkeypatch.setattr(sandbox_manager, "docker_available", lambda timeout=3.0: True)

    def _popen(cmd, **kwargs):
        return FakePopen(cmd, fake_timeout=True, **kwargs)

    monkeypatch.setattr(sandbox_manager.subprocess, "Popen", _popen)
    monkeypatch.setattr(sandbox_manager.subprocess, "run", FakeRun.fake)

    start = time.monotonic()
    result = _execute()
    elapsed = time.monotonic() - start

    assert result["mode"] == "docker"
    assert result["status"] == "timeout"
    assert "超时" in result["logs"][0]
    assert elapsed < 5  # 不挂起
    commands = [call[0][0] for call in FakeRun.calls]
    assert any(len(c) >= 2 and c[0] == "docker" and c[1] == "kill" for c in commands)
    assert any(len(c) >= 3 and c[0] == "docker" and c[1] == "rm" and c[2] == "--force" for c in commands)
