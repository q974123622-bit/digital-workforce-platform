"""Harness 容器漂移自愈：容器被误删时经 Docker socket 按编排口径自建。

容器化 backend 没有 docker CLI，`ensure_container` 原来在容器不存在时直接报
「Harness 容器不存在」。现在要自己把容器建起来，且**镜像 / 4 条挂载 / 标签 / 网络
必须与 compose.yaml 逐字一致**（否则 runtime_registry 认不出、会重复建容器）。

这里全程用假 Engine，不碰真实 Docker。
"""

from __future__ import annotations

import pytest

from app.services import runtime_manager
from app.services.docker_engine import EngineResult

MANAGED_LABEL = "dwp.managed=true"
SELF_ID = "aaaaaaaaaaaabbbbbbbbccccccccddddddddeeeeeeeeffffffff0000000011111111"
SIBLING = "dwp-harness-ai-general"
NETWORK = "dwp-ai-employee-platform_dwp"


class _FakeEngine:
    """只实现 runtime_manager 用到的接口；数据挂在类属性上，方便断言。"""

    inspect_results: dict[str, dict] = {}
    siblings: list[dict] = []
    created: list[tuple[str, dict]] = []
    started: list[str] = []
    execs: list[list[str]] = []
    inspect_calls: list[str] = []

    def __init__(self, socket_path: str = "/var/run/docker.sock", timeout: float = 15):
        self.socket_path, self.timeout = socket_path, timeout

    def close(self) -> None:
        pass

    def version(self) -> str:
        return "26.1.4"

    def image_exists(self, image: str) -> bool:
        return True

    def container_status(self, name: str) -> str | None:
        return None

    def inspect_container(self, name: str) -> dict | None:
        _FakeEngine.inspect_calls.append(name)
        return _FakeEngine.inspect_results.get(name)

    def list_containers(self, label: str | None = None) -> list[dict]:
        return list(_FakeEngine.siblings)

    def create_container(self, name: str, spec: dict) -> EngineResult:
        _FakeEngine.created.append((name, spec))
        return EngineResult(0, stdout="fake-container-id")

    def start_container(self, name: str) -> EngineResult:
        _FakeEngine.started.append(name)
        return EngineResult(0)

    def exec(self, name, command, *, environment=None, workdir=None) -> EngineResult:
        _FakeEngine.execs.append(list(command))
        return EngineResult(0, stdout="ok")


def _sibling_payload() -> dict:
    """一个正常 Harness 容器的 inspect 结果（提供宿主源路径、网络与安全选项）。"""
    return {
        "Config": {"Labels": {
            "dwp.managed": "true",
            "dwp.employee_id": "AI-GENERAL",
            "dwp.dsh.runtime": "./runtime/dsh-0.1.5-rc.1",
            "dwp.dsh.profile": "rev-20260915",
        }},
        "HostConfig": {
            "NetworkMode": NETWORK,
            "SecurityOpt": ["no-new-privileges:true"],
        },
        "Mounts": [
            {"Destination": "/opt/dsh-runtime", "Source": "/srv/dwp/runtime/dsh-0.1.5-rc.1"},
            {"Destination": "/opt/dwp/profile", "Source": "/srv/dwp/profile/current"},
            {"Destination": "/dsh-home", "Source": "/srv/dwp/harness/AI-GENERAL/dsh-home"},
            {"Destination": "/workspace", "Source": "/srv/dwp/harness/AI-GENERAL/workspace"},
        ],
    }


def _self_payload() -> dict:
    """backend 自身容器：harness 根与 docker 网络只能从这里推出来。"""
    return {
        "HostConfig": {"NetworkMode": NETWORK},
        "Mounts": [
            {"Destination": "/app/backend/harness-workspaces", "Source": "/srv/dwp/harness"},
            {"Destination": "/data", "Source": "/srv/dwp/data"},
        ],
    }


@pytest.fixture(autouse=True)
def _fake_engine(tmp_path, monkeypatch):
    _FakeEngine.inspect_results = {SELF_ID: _self_payload(), SIBLING: _sibling_payload()}
    _FakeEngine.siblings = [{"Names": [f"/{SIBLING}"], "Labels": {"dwp.managed": "true"}}]
    _FakeEngine.created = []
    _FakeEngine.started = []
    _FakeEngine.execs = []
    _FakeEngine.inspect_calls = []

    monkeypatch.setattr(runtime_manager, "REPO_ROOT", tmp_path / "repo")
    monkeypatch.setattr(runtime_manager, "DockerEngineClient", _FakeEngine)
    monkeypatch.setattr(runtime_manager, "self_container_id", lambda: SELF_ID)
    monkeypatch.setattr(runtime_manager, "_has_docker_cli", lambda: False)
    monkeypatch.setattr(runtime_manager, "_host_layout_cache", None)
    monkeypatch.setattr(runtime_manager, "DSH_RUNTIME_HOST", "")
    for name in (
        "DWP_HARNESS_HOST_ROOT", "DWP_DSH_RUNTIME_HOST",
        "DWP_PROFILE_HOST", "DWP_HARNESS_NETWORK",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


def test_ensure_container_self_heals_with_compose_parity():
    name = runtime_manager.ensure_container("AI-XYZQ")

    assert name == "dwp-harness-ai-xyzq"
    assert _FakeEngine.started == [name]
    assert len(_FakeEngine.created) == 1
    created_name, spec = _FakeEngine.created[0]
    assert created_name == name

    # 镜像 / 入口 / 工作目录
    assert spec["Image"] == runtime_manager.HARNESS_IMAGE
    assert spec["Entrypoint"] == ["sh", "-c"]
    assert spec["WorkingDir"] == "/workspace"
    assert "exit 1" in spec["Cmd"][0]  # 挂载守卫：缺 dsh runtime 要立刻失败

    env = dict(item.split("=", 1) for item in spec["Env"])
    assert env["DSH_HOME"] == "/dsh-home"
    assert env["DSH_PERMISSION_MODE"] == "workspace-write"
    assert env["DSH_TELEMETRY_MODE"] == "DISABLED"
    assert env["PATH"].startswith("/opt/dsh-runtime/bin:")

    # 标签必须齐：runtime_registry 靠它们认容器与版本
    assert spec["Labels"]["dwp.managed"] == "true"
    assert spec["Labels"]["dwp.employee_id"] == "AI-XYZQ"
    assert spec["Labels"]["dwp.dsh.runtime"] == "./runtime/dsh-0.1.5-rc.1"
    assert spec["Labels"]["dwp.dsh.profile"] == "rev-20260915"

    # 四条挂载与 compose 一致（宿主路径来自 inspect 反推）
    assert spec["HostConfig"]["Binds"] == [
        "/srv/dwp/runtime/dsh-0.1.5-rc.1:/opt/dsh-runtime:ro",
        "/srv/dwp/profile/current:/opt/dwp/profile:ro",
        "/srv/dwp/harness/AI-XYZQ/dsh-home:/dsh-home",
        "/srv/dwp/harness/AI-XYZQ/workspace:/workspace",
    ]
    assert spec["HostConfig"]["NetworkMode"] == NETWORK
    assert spec["HostConfig"]["RestartPolicy"] == {"Name": "unless-stopped"}
    assert spec["HostConfig"]["SecurityOpt"] == ["no-new-privileges:true"]

    # 自建后仍走既有的 profile 同步与健康检查
    assert any("dwp-knowledge-agent-v2" in command[-1] for command in _FakeEngine.execs)


def test_ensure_container_falls_back_to_env_vars(monkeypatch):
    """冷启动（自身与兄弟容器都探测不到）时用环境变量兜底。"""
    _FakeEngine.inspect_results = {}
    _FakeEngine.siblings = []
    monkeypatch.setattr(runtime_manager, "self_container_id", lambda: "")
    monkeypatch.setenv("DWP_HARNESS_HOST_ROOT", "/host/harness")
    monkeypatch.setenv("DWP_PROFILE_HOST", "/host/profile")
    monkeypatch.setenv("DWP_HARNESS_NETWORK", "custom-net")
    monkeypatch.setattr(runtime_manager, "DSH_RUNTIME_HOST", "/host/runtime")

    runtime_manager.ensure_container("AI-FUND")

    _, spec = _FakeEngine.created[0]
    assert spec["HostConfig"]["Binds"] == [
        "/host/runtime:/opt/dsh-runtime:ro",
        "/host/profile:/opt/dwp/profile:ro",
        "/host/harness/AI-FUND/dsh-home:/dsh-home",
        "/host/harness/AI-FUND/workspace:/workspace",
    ]
    assert spec["HostConfig"]["NetworkMode"] == "custom-net"


def test_ensure_container_reports_which_variable_is_missing():
    """探测不到又没配变量：报可读错误并指明该配哪个变量，绝不猜路径。"""
    _FakeEngine.inspect_results = {}
    _FakeEngine.siblings = []
    runtime_manager._host_layout_cache = None
    original = runtime_manager.self_container_id
    runtime_manager.self_container_id = lambda: ""
    try:
        with pytest.raises(RuntimeError) as excinfo:
            runtime_manager.ensure_container("AI-RESEARCH")
    finally:
        runtime_manager.self_container_id = original

    message = str(excinfo.value)
    assert "DWP_HARNESS_HOST_ROOT" in message
    assert "DWP_DSH_RUNTIME_HOST" in message
    assert "DWP_PROFILE_HOST" in message
    assert _FakeEngine.created == []


def test_host_layout_is_cached_between_calls():
    runtime_manager.host_layout()
    first = len(_FakeEngine.inspect_calls)
    runtime_manager.host_layout()
    assert len(_FakeEngine.inspect_calls) == first


def test_existing_running_container_is_reused_without_create():
    _FakeEngine.container_status = lambda self, name: "running"  # type: ignore[assignment]
    try:
        runtime_manager.ensure_container("AI-XYZQ")
    finally:
        _FakeEngine.container_status = lambda self, name: None  # type: ignore[assignment]
    assert _FakeEngine.created == []
    assert _FakeEngine.started == []
    assert any("command -v dsh" in command[-1] for command in _FakeEngine.execs)
