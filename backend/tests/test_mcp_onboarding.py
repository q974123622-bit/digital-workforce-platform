"""MCP 自助接入离线单测：探测解析 / AI 简介降级 / 落库发布 / 动态运行时。

全部走假客户端（monkeypatch ``mcp_registry.build_client``），不连任何真实服务。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import models
from app.services import mcp_credentials, mcp_intro, mcp_registry
from app.services.external_mcp_client import ExternalMcpError
from app.services.harness_token import issue_token
from app.services.llm import LLMUnavailableError

TOOLS = [
    {
        "name": "Echo",
        "description": "回显输入文本",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "待回显文本"}},
            "required": ["text"],
        },
    },
    {
        "name": "ServerTime",
        "description": "返回服务端时间",
        "inputSchema": {"type": "object", "properties": {}},
    },
]
HANDSHAKE = {
    "protocolVersion": "2024-11-05",
    "capabilities": {"tools": {"listChanged": True}},
    "serverInfo": {"name": "demo-mcp", "version": "1.2.0"},
}


class _FakeMcpClient:
    """替代 ``ExternalMcpClient``：协议层行为可预期，便于断言分派与转发。"""

    def __init__(self, tools=None, *, handshake=None, error=None):
        self.tools = TOOLS if tools is None else tools
        self.handshake = HANDSHAKE if handshake is None else handshake
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def initialize(self):
        if self.error:
            raise ExternalMcpError(self.error)
        return self.handshake

    def list_tools(self):
        if self.error:
            raise ExternalMcpError(self.error)
        return self.tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments or {}))
        return {"content": [{"type": "text", "text": f"called:{name}"}], "isError": False}


@pytest.fixture(autouse=True)
def _registry_isolation(monkeypatch):
    """关闭适配器缓存并清空注册表，避免测试间互相污染；同时给出固定的凭据加密密钥。"""
    monkeypatch.setenv("DWP_MCP_REGISTRY_TTL_SECONDS", "0")
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_KEY", "unit-test-key-material")
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_KEY_FILE", "/nonexistent/mcp_credential_key")
    mcp_registry.invalidate()
    yield
    mcp_registry.invalidate()


def _patch_client(monkeypatch, fake: _FakeMcpClient) -> dict:
    captured: dict = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(mcp_registry, "build_client", fake_build)
    return captured


def _login_admin(client) -> None:
    response = client.post("/api/v1/auth/key-login", json={"access_key": "test-key-admin00"})
    assert response.status_code == 200


def _login_employee(client, access_key: str = "test-key-e10281") -> None:
    response = client.post("/api/v1/auth/key-login", json={"access_key": access_key})
    assert response.status_code == 200


def _register_shared(client, *, api_key: str | None = "super-secret-value") -> dict:
    payload = {
        "url": "http://10.0.0.9:9100/mcp",
        "name": "行情数据 MCP",
        "description": "提供行情查询能力",
        "mcp_category": "fund",
        "data_level": "L1",
        "scope": "shared",
        "version": "1.0.0",
        "tools": TOOLS,
        "auth_header_name": "x-api-key" if api_key else None,
        "api_key": api_key,
    }
    response = client.post("/api/v1/admin/mcp/plugins", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# -- 探测 --------------------------------------------------------------------


def test_probe_parses_tools_without_persisting(client, db_session, monkeypatch):
    _login_admin(client)
    fake = _FakeMcpClient()
    captured = _patch_client(monkeypatch, fake)
    before = len(db_session.scalars(select(models.Plugin)).all())

    response = client.post("/api/v1/admin/mcp/probe", json={
        "url": "http://10.0.0.9:9100/mcp", "api_key": "probe-secret",
        "auth_header_name": "Authorization", "timeout_seconds": 12,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tools_count"] == 2
    assert [tool["name"] for tool in body["tools"]] == ["Echo", "ServerTime"]
    assert body["server_info"]["name"] == "demo-mcp"
    assert body["protocol_version"] == "2024-11-05"
    assert body["authenticated"] is True
    assert body["tools"][0]["parameters"][0]["name"] == "text"
    # 自定义鉴权头名与密钥透传到客户端，但绝不回显密钥
    assert captured["auth_header_name"] == "Authorization"
    assert captured["api_key"] == "probe-secret"
    assert "probe-secret" not in response.text
    # 探测不落库
    assert len(db_session.scalars(select(models.Plugin)).all()) == before


def test_probe_failure_returns_readable_reason(client, monkeypatch):
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient(error="无法连接 External MCP 服务"))
    response = client.post("/api/v1/admin/mcp/probe", json={"url": "http://10.0.0.9:9100/mcp"})
    assert response.status_code == 502
    assert "无法连接" in response.json()["error"]["message"]


def test_probe_rejects_non_http_scheme(client):
    _login_admin(client)
    response = client.post("/api/v1/admin/mcp/probe", json={"url": "ftp://10.0.0.9/mcp"})
    assert response.status_code == 422


# -- AI 简介 -----------------------------------------------------------------


class _StubProvider:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error

    def chat(self, messages, tools=None):  # pragma: no cover - 接口完整性
        raise NotImplementedError

    def tool_call(self, messages, tools):  # pragma: no cover
        raise NotImplementedError

    def structured_output(self, messages, schema):
        assert all(message.get("source") == "demo" for message in messages)
        if self.error:
            raise self.error
        return self.data


def test_draft_generates_chinese_intro(client, monkeypatch):
    _login_admin(client)
    monkeypatch.setattr(mcp_intro, "DeepSeekProvider", lambda: _StubProvider(data={
        "name": "行情查询服务",
        "description": "面向投资场景的行情查询能力。",
        "tools": [
            {"name": "Echo", "description": "把输入原样返回，用于连通性验证"},
            {"name": "ServerTime", "description": "获取服务端当前时间"},
        ],
        "mcp_category": "fund",
        "data_level": "L2",
    }))
    response = client.post("/api/v1/admin/mcp/draft", json={
        "url": "http://10.0.0.9:9100/mcp",
        "server_info": HANDSHAKE["serverInfo"],
        "tools": [
            {"name": "Echo", "description": "echo", "parameters": [{"name": "text", "type": "string"}]},
            {"name": "ServerTime", "description": "time", "parameters": []},
        ],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["generated"] is True
    assert body["name"] == "行情查询服务"
    assert body["mcp_category"] == "fund"
    assert body["data_level"] == "L2"
    assert body["tools"][1]["description"] == "获取服务端当前时间"


def test_draft_degrades_when_model_unavailable(client, monkeypatch):
    _login_admin(client)
    monkeypatch.setattr(
        mcp_intro, "DeepSeekProvider",
        lambda: _StubProvider(error=LLMUnavailableError("DEEPSEEK_API_KEY 未配置")),
    )
    response = client.post("/api/v1/admin/mcp/draft", json={
        "url": "http://10.0.0.9:9100/mcp",
        "tools": [{"name": "Echo", "description": "回显输入文本", "parameters": []}],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["generated"] is False
    assert "大模型不可用" in body["degraded_reason"]
    assert body["tools"][0]["description"] == "回显输入文本"


def test_draft_respects_custom_prompt(client, monkeypatch):
    _login_admin(client)
    seen: dict = {}

    class _Recording(_StubProvider):
        def structured_output(self, messages, schema):
            seen["content"] = messages[-1]["content"]
            return {"name": "自定义名", "description": "自定义简介", "tools": [], "mcp_category": "other", "data_level": "L1"}

    monkeypatch.setattr(mcp_intro, "DeepSeekProvider", lambda: _Recording())
    response = client.post("/api/v1/admin/mcp/draft", json={
        "url": "http://10.0.0.9:9100/mcp",
        "prompt": "请用更简短的口吻重写，突出风险提示",
        "tools": [{"name": "Echo", "description": "回显输入文本", "parameters": []}],
    })
    assert response.status_code == 200
    assert "请用更简短的口吻重写" in seen["content"]


# -- 登记 / 发布 / 动态运行时 --------------------------------------------------


def test_admin_registration_publishes_and_serves_tools(client, db_session, monkeypatch):
    _login_admin(client)
    fake = _FakeMcpClient()
    _patch_client(monkeypatch, fake)
    created = _register_shared(client)

    plugin_id = created["plugin_id"]
    assert created["review_status"] == "approved"
    assert created["publish_status"] == "published"
    assert created["tool_prefix"].startswith("mcp_")
    assert created["secret_hint"].endswith("alue")
    assert "super-secret-value" not in str(created)

    plugin = db_session.get(models.Plugin, plugin_id)
    db_session.refresh(plugin)
    assert plugin.status == "active"
    assert plugin.current_version == "1.0.0"
    assert plugin.runtime_meta["url"] == "http://10.0.0.9:9100/mcp"
    assert plugin.runtime_meta["tool_prefix"] == created["tool_prefix"]
    # 明文密钥不落库
    credential = db_session.get(models.McpCredential, plugin_id)
    assert credential is not None
    assert credential.secret_ciphertext != "super-secret-value"
    assert mcp_credentials.load_secret(db_session, plugin_id) == "super-secret-value"
    # External MCP 按探测结果写真实运行态（hosted 才保持未接入）
    runtime = db_session.scalar(select(models.McpRuntimeInstance).join(
        models.PluginVersion, models.PluginVersion.id == models.McpRuntimeInstance.plugin_version_id,
    ).where(models.PluginVersion.plugin_id == plugin_id))
    assert runtime is not None and runtime.state == "running"

    # 授权给 AI-GENERAL 后才出现在该员工的工具清单里
    bound = client.post("/api/v1/admin/agent-plugin-bindings", json={
        "plugin_id": plugin_id, "target_agent_id": "AI-GENERAL",
    })
    assert bound.status_code == 201, bound.text

    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    prefix = created["tool_prefix"]
    token = issue_token(employee_id="AI-GENERAL", requester_human_no="E10281", trace_id="T-MCP", depth=0)
    headers = {"Authorization": f"Bearer {token}"}
    catalog = client.post(
        "/internal/agent-tools/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ).json()
    names = [tool["name"] for tool in catalog["result"]["tools"]]
    assert f"{prefix}Echo" in names
    assert f"{prefix}ServerTime" in names
    # 内部工具不受影响
    assert "search_knowledge" in names or "list_attachments" in names or "load_skill" in names

    called = client.post(
        "/internal/agent-tools/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": f"{prefix}Echo", "arguments": {"text": "hi"}}},
    ).json()
    assert called["result"]["content"][0]["text"] == "called:Echo"
    # 前缀已在适配器内部剥离，转发给 MCP server 的是原始工具名
    assert fake.calls == [("Echo", {"text": "hi"})]


def test_unauthorized_employee_sees_no_registered_tools(client, monkeypatch):
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    _register_shared(client)

    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    token = issue_token(employee_id="AI-INVESTMENT", requester_human_no="E10281", trace_id="T-MCP2", depth=0)
    catalog = client.post(
        "/internal/agent-tools/mcp", headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ).json()
    names = [tool["name"] for tool in catalog["result"]["tools"]]
    assert not any(name.startswith("mcp_") for name in names)


def test_withdraw_removes_tools_immediately(client, monkeypatch):
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    created = _register_shared(client)
    plugin_id, prefix = created["plugin_id"], created["tool_prefix"]
    client.post("/api/v1/admin/agent-plugin-bindings", json={
        "plugin_id": plugin_id, "target_agent_id": "AI-GENERAL",
    })
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    headers = {"Authorization": f"Bearer {issue_token(employee_id='AI-GENERAL', requester_human_no='E10281', trace_id='T-W', depth=0)}"}

    def _names() -> list[str]:
        return [
            tool["name"] for tool in client.post(
                "/internal/agent-tools/mcp", headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            ).json()["result"]["tools"]
        ]

    assert f"{prefix}Echo" in _names()
    assert client.post(f"/api/v1/admin/plugins/{plugin_id}/withdraw").status_code == 200
    assert f"{prefix}Echo" not in _names()


def test_registered_service_list_never_leaks_secret(client, monkeypatch):
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    _register_shared(client)
    response = client.get("/api/v1/admin/mcp/plugins")
    assert response.status_code == 200
    body = response.json()
    assert len(body["services"]) == 1
    service = body["services"][0]
    assert service["url"] == "http://10.0.0.9:9100/mcp"
    assert service["tools_count"] == 2
    assert service["secret_hint"].endswith("alue")
    assert "super-secret-value" not in response.text


def test_reprobe_updates_connectivity(client, monkeypatch):
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    created = _register_shared(client)
    response = client.post(f"/api/v1/admin/mcp/plugins/{created['plugin_id']}/probe")
    assert response.status_code == 200, response.text
    assert response.json()["tools_count"] == 2
    # 未传 api_key 时复用已存密钥
    assert response.json()["authenticated"] is True


# -- 探测失败摘要 / 目录编辑 / 下架恢复 ---------------------------------------


def test_reprobe_failure_persists_summary_and_keeps_502(client, monkeypatch):
    """重新测试失败：仍返回 502 可读原因，同时把失败摘要落库供列表页展示。"""
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    created = _register_shared(client)
    plugin_id = created["plugin_id"]

    _patch_client(monkeypatch, _FakeMcpClient(error="连接被拒绝（10.0.0.9:9100）"))
    failed = client.post(f"/api/v1/admin/mcp/plugins/{plugin_id}/probe")
    assert failed.status_code == 502
    assert "连接被拒绝" in failed.json()["error"]["message"]

    service = client.get("/api/v1/admin/mcp/plugins").json()["services"][0]
    assert service["probe_state"] == "unavailable"
    assert service["runtime_state"] == "unavailable"
    assert "连接被拒绝" in service["last_error"]
    assert service["probe_checked_at"]
    # 上一次成功的工具数与延迟保留，不因一次失败清空
    assert service["tools_count"] == 2


def test_reprobe_success_clears_failure_summary(client, monkeypatch):
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient(error="连接超时"))
    created = _register_shared(client)
    plugin_id = created["plugin_id"]
    assert client.post(f"/api/v1/admin/mcp/plugins/{plugin_id}/probe").status_code == 502

    _patch_client(monkeypatch, _FakeMcpClient())
    assert client.post(f"/api/v1/admin/mcp/plugins/{plugin_id}/probe").status_code == 200
    service = client.get("/api/v1/admin/mcp/plugins").json()["services"][0]
    assert service["probe_state"] == "reachable"
    assert service["last_error"] == ""
    assert service["runtime_state"] == "running"


def test_update_mcp_service_edits_metadata_and_rotates_secret(client, db_session, monkeypatch):
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    created = _register_shared(client)
    plugin_id = created["plugin_id"]

    response = client.put(f"/api/v1/admin/mcp/plugins/{plugin_id}", json={
        "name": "行情数据 MCP（改名）",
        "description": "面向投资场景的行情查询能力。",
        "mcp_category": "employee_service",
        "data_level": "L2",
        "url": "http://10.0.0.9:9200/mcp",
        "timeout_seconds": 30,
        "auth_header_name": "Authorization",
        "api_key": "rotated-secret-value",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "行情数据 MCP（改名）"
    assert body["description"] == "面向投资场景的行情查询能力。"
    assert body["mcp_category"] == "employee_service"
    assert body["data_level"] == "L2"
    assert body["url"] == "http://10.0.0.9:9200/mcp"
    assert body["timeout_seconds"] == 30
    assert body["reprobed"] is True
    # 工具清单没变就不该堆版本
    assert body["new_version"] is None
    # 密钥轮换后只回掩码，明文绝不出现
    assert "rotated-secret-value" not in response.text
    assert body["secret_hint"] == "****alue"

    plugin = db_session.get(models.Plugin, plugin_id)
    db_session.refresh(plugin)
    assert plugin.runtime_meta["url"] == "http://10.0.0.9:9200/mcp"
    assert plugin.runtime_meta["auth_header_name"] == "Authorization"
    assert plugin.data_level == "L2"
    assert mcp_credentials.load_secret(db_session, plugin_id) == "rotated-secret-value"


def test_update_mcp_service_can_switch_to_anonymous(client, db_session, monkeypatch):
    """api_key 传空串 = 显式转匿名（删除凭据），与探测接口语义一致。"""
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    created = _register_shared(client)
    plugin_id = created["plugin_id"]

    response = client.put(f"/api/v1/admin/mcp/plugins/{plugin_id}", json={"api_key": ""})
    assert response.status_code == 200, response.text
    assert response.json()["authenticated"] is False
    assert response.json()["secret_hint"] in (None, "")
    assert db_session.get(models.McpCredential, plugin_id) is None


def test_update_mcp_service_keeps_secret_when_omitted(client, db_session, monkeypatch):
    """不传 api_key 时保留既有密钥（编辑信息不该顺手清掉鉴权）。"""
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    created = _register_shared(client)
    plugin_id = created["plugin_id"]

    response = client.put(f"/api/v1/admin/mcp/plugins/{plugin_id}", json={"name": "仅改名"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "仅改名"
    # 名称变化不涉及运行参数，不重测、不产生新版本
    assert body["reprobed"] is False
    assert body["new_version"] is None
    assert body["authenticated"] is True
    assert mcp_credentials.load_secret(db_session, plugin_id) == "super-secret-value"


def test_update_mcp_service_publishes_new_version_when_tools_change(client, db_session, monkeypatch):
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    created = _register_shared(client)
    plugin_id = created["plugin_id"]
    assert created["current_version"] == "1.0.0"

    # 服务端换了工具清单：地址变化触发重测，清单不一致应自动出新版本
    _patch_client(monkeypatch, _FakeMcpClient(tools=[TOOLS[0]]))
    response = client.put(f"/api/v1/admin/mcp/plugins/{plugin_id}", json={
        "url": "http://10.0.0.9:9300/mcp",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["new_version"] == "1.0.1"
    assert body["current_version"] == "1.0.1"
    assert body["tools_count"] == 1

    plugin = db_session.get(models.Plugin, plugin_id)
    db_session.refresh(plugin)
    assert plugin.current_version == "1.0.1"
    versions = db_session.scalars(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin_id,
    )).all()
    assert sorted(row.version for row in versions) == ["1.0.0", "1.0.1"]
    published = next(row for row in versions if row.version == "1.0.1")
    assert published.publish_status == "published"
    assert [tool["name"] for tool in published.manifest["tools"]] == ["Echo"]


def test_update_mcp_service_failure_still_persists_edits(client, monkeypatch):
    """改地址后重测失败：接口报 502 可读原因，但改动的信息已经保存下来。"""
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    created = _register_shared(client)
    plugin_id = created["plugin_id"]

    _patch_client(monkeypatch, _FakeMcpClient(error="域名解析失败"))
    failed = client.put(f"/api/v1/admin/mcp/plugins/{plugin_id}", json={
        "url": "http://bad-host:9100/mcp", "description": "改到新地址",
    })
    assert failed.status_code == 502
    assert "域名解析失败" in failed.json()["error"]["message"]

    service = client.get("/api/v1/admin/mcp/plugins").json()["services"][0]
    assert service["url"] == "http://bad-host:9100/mcp"
    assert service["description"] == "改到新地址"
    assert service["probe_state"] == "unavailable"


def test_update_rejects_unknown_category_and_bad_url(client, monkeypatch):
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    created = _register_shared(client)
    plugin_id = created["plugin_id"]
    assert client.put(
        f"/api/v1/admin/mcp/plugins/{plugin_id}", json={"mcp_category": "not-a-category"},
    ).status_code == 422
    assert client.put(
        f"/api/v1/admin/mcp/plugins/{plugin_id}", json={"url": "ftp://10.0.0.9/mcp"},
    ).status_code == 422


def test_withdraw_and_restore_registered_mcp(client, monkeypatch):
    """下架/恢复复用治理接口：下架后员工侧工具清单立刻失效，恢复后需另行授权。"""
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    created = _register_shared(client)
    plugin_id, prefix = created["plugin_id"], created["tool_prefix"]
    client.post("/api/v1/admin/agent-plugin-bindings", json={
        "plugin_id": plugin_id, "target_agent_id": "AI-GENERAL",
    })
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    headers = {"Authorization": f"Bearer {issue_token(employee_id='AI-GENERAL', requester_human_no='E10281', trace_id='T-GOV', depth=0)}"}

    def _names() -> list[str]:
        return [
            tool["name"] for tool in client.post(
                "/internal/agent-tools/mcp", headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            ).json()["result"]["tools"]
        ]

    assert f"{prefix}Echo" in _names()
    withdrawn = client.put(f"/api/v1/admin/plugins/{plugin_id}/governance", json={"status": "withdrawn"})
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "withdrawn"
    assert f"{prefix}Echo" not in _names()

    restored = client.put(f"/api/v1/admin/plugins/{plugin_id}/governance", json={"status": "active"})
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    # 恢复上架不自动恢复绑定，授权需另行配置
    assert f"{prefix}Echo" not in _names()


# -- 密钥 --------------------------------------------------------------------


def test_saving_secret_requires_encryption_key(client, monkeypatch):
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    monkeypatch.delenv("DWP_MCP_CREDENTIAL_KEY", raising=False)
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_KEY_FILE", "/nonexistent/mcp_credential_key")
    response = client.post("/api/v1/admin/mcp/plugins", json={
        "url": "http://10.0.0.9:9100/mcp", "name": "行情数据 MCP",
        "mcp_category": "fund", "tools": TOOLS, "api_key": "super-secret-value",
    })
    assert response.status_code == 409
    assert "凭据加密密钥" in response.json()["error"]["message"]


def test_anonymous_registration_allowed_without_key(client, monkeypatch):
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    monkeypatch.delenv("DWP_MCP_CREDENTIAL_KEY", raising=False)
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_KEY_FILE", "/nonexistent/mcp_credential_key")
    created = _register_shared(client, api_key=None)
    assert created["authenticated"] is False
    assert created["secret_hint"] in (None, "")


def test_credential_roundtrip_and_tamper_detection(monkeypatch):
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_KEY", "unit-test-key-material")
    token = mcp_credentials.encrypt("hello-secret")
    assert token != "hello-secret"
    assert mcp_credentials.decrypt(token) == "hello-secret"
    # 篡改密文必须解不出来，而不是解出垃圾
    assert mcp_credentials.decrypt(token[:-4] + "AAAA") == ""
    assert mcp_credentials.mask("hello-secret") == "****cret"


# -- 员工自助登记 ------------------------------------------------------------


def test_employee_onboarding_pending_then_admin_publish_then_self_enable(client, db_session, monkeypatch):
    _login_employee(client)
    fake = _FakeMcpClient()
    _patch_client(monkeypatch, fake)
    response = client.post("/api/v1/my/mcp/plugins", json={
        "url": "http://10.0.0.9:9100/mcp", "name": "个人行情 MCP",
        "mcp_category": "fund", "tools": TOOLS, "api_key": "employee-secret",
    })
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["scope"] == "personal"
    assert created["review_status"] == "pending"
    assert created["publish_status"] == "submitted"
    plugin_id, version = created["plugin_id"], created["version"]

    # 待审阶段员工看不到自己的工具（未发布 → 无可用版本）
    assert client.get("/api/v1/my/plugins").json()["effective"] == []

    # 管理端审批 + 发布
    _login_admin(client)
    pending = client.get("/api/v1/admin/plugin-submissions").json()
    row = next(item for item in pending if item["plugin_id"] == plugin_id)
    assert client.post(
        f"/api/v1/admin/plugin-submissions/{row['id']}/approve", json={"note": ""},
    ).status_code == 200
    published = client.post(f"/api/v1/admin/plugins/{plugin_id}/versions/{version}/publish")
    assert published.status_code == 200, published.text

    # 发布后自动为属主分身建「管理员已授权、员工未启用」绑定 → 员工端「可添加」
    _login_employee(client)
    catalog = client.get("/api/v1/my/plugins").json()
    assert plugin_id in [item["id"] for item in catalog["available"]]
    assert client.post(f"/api/v1/my/plugins/{plugin_id}/enable", json={}).status_code == 200
    assert plugin_id in [
        item["plugin_id"] for item in client.get("/api/v1/my/plugins").json()["effective"]
    ]


def test_employee_cannot_register_shared_scope(client, monkeypatch):
    _login_employee(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    response = client.post("/api/v1/my/mcp/plugins", json={
        "url": "http://10.0.0.9:9100/mcp", "name": "越权 MCP", "scope": "shared", "tools": TOOLS,
    })
    assert response.status_code == 201
    # 范围被强制为 personal，不会产生共享插件
    assert response.json()["scope"] == "personal"


def test_admin_cannot_register_personal_scope(client, monkeypatch):
    _login_admin(client)
    _patch_client(monkeypatch, _FakeMcpClient())
    response = client.post("/api/v1/admin/mcp/plugins", json={
        "url": "http://10.0.0.9:9100/mcp", "name": "个人 MCP", "scope": "personal", "tools": TOOLS,
    })
    assert response.status_code == 422


# -- 前缀分派 ----------------------------------------------------------------


def test_longest_prefix_wins():
    class _Adapter:
        def __init__(self, prefix):
            self.tool_prefix = prefix

    short = _Adapter("mcp_ab__")
    long = _Adapter("mcp_abc__")
    assert mcp_registry.match([short, long], "mcp_abc__Tool") is long
    assert mcp_registry.match([short, long], "mcp_ab__Tool") is short
    assert mcp_registry.match([short, long], "other__Tool") is None


def test_reserved_prefixes_are_rejected(db_session):
    assert mcp_registry.prefix_conflict(db_session, "qieman__") is True
    assert mcp_registry.prefix_conflict(db_session, "xyzq__") is True
    assert mcp_registry.prefix_conflict(db_session, "ehome__") is True
    allocated = mcp_registry.allocate_tool_prefix(db_session)
    assert allocated.startswith("mcp_") and allocated.endswith("__")
    assert mcp_registry.prefix_conflict(db_session, allocated) is False
