"""兴证e家员工服务 MCP 离线单测。

覆盖：插件注册与授权幂等、双鉴权头（应用级 key + 按人 JWT）、JWT 过期预检、
缺参转「待补全」结构化结果、以及 ``tools/list`` 只依赖应用级 key。

全部走假客户端，不连任何真实服务；测试里的密钥都是假值。
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app import models, seed
from app.services import mcp_credentials, mcp_registry, tool_input
from app.services.external_mcp_adapter import EHOME_PLUGIN_ID, EHOME_TOOL_PREFIX, ehome_adapter
from app.services.external_mcp_client import ExternalMcpError
from app.services.harness_token import issue_token

APP_KEY = "app-key-for-test"
APP_HEADER = "X-MCP-API-KEY"
USER_HEADER = "X-MCP-JWT"

TOOLS = [
    {"name": "getEmpCardBalance", "description": "餐卡余额", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "getPhone", "description": "通讯录", "inputSchema": {"type": "object", "properties": {}}},
]


def _b64(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwt(exp_delta_seconds: int = 3600, login_id: str = "E10281") -> str:
    exp = int((datetime.utcnow() + timedelta(seconds=exp_delta_seconds)).timestamp())
    return f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64({'loginId': login_id, 'exp': exp})}.sig"


class _FakeClient:
    """替代 ``ExternalMcpClient``：记录收到的工具名/参数，可注入错误或返回体。"""

    def __init__(self, *, tools=None, result=None, error=None, headers=None):
        self.tools = TOOLS if tools is None else tools
        self.result = result or {"content": [{"type": "text", "text": "ok"}], "isError": False}
        self.error = error
        self.headers = headers or {}
        self.calls: list[tuple[str, dict]] = []

    def initialize(self):
        return {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "claw对接MCP", "version": "1.0.0"},
        }

    def list_tools(self):
        if self.error:
            raise ExternalMcpError(self.error)
        return self.tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments or {}))
        if self.error:
            raise ExternalMcpError(self.error)
        return self.result


@pytest.fixture(autouse=True)
def _ehome_env(monkeypatch):
    """固定兴证e家配置与凭据加密密钥，并把单例适配器的缓存/客户端隔离掉。"""
    monkeypatch.setenv("DWP_EHOME_MCP_URL", "http://10.0.0.9/ehome/mcp/claw")
    monkeypatch.setenv("DWP_EHOME_MCP_API_KEY", APP_KEY)
    monkeypatch.setenv("DWP_EHOME_MCP_APP_HEADER", APP_HEADER)
    monkeypatch.setenv("DWP_EHOME_MCP_USER_HEADER", USER_HEADER)
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_KEY", "unit-test-key-material")
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_KEY_FILE", "/nonexistent/mcp_credential_key")
    monkeypatch.setenv("DWP_MCP_REGISTRY_TTL_SECONDS", "0")
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    monkeypatch.setattr(ehome_adapter, "_client", None)
    monkeypatch.setattr(ehome_adapter, "_client_factory_headers", None)
    monkeypatch.setattr(ehome_adapter, "_cached_tools", [])
    monkeypatch.setattr(ehome_adapter, "_cached_at", 0.0)
    mcp_registry.invalidate()
    yield
    mcp_registry.invalidate()


def _headers_for(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token(employee_id: str = "AI-XYZQ", human_no: str = "E10281", trace_id: str = "T-EHOME") -> str:
    return issue_token(employee_id=employee_id, requester_human_no=human_no, trace_id=trace_id, depth=0)


# -- JWT 解析 -----------------------------------------------------------------


def test_jwt_expiry_reads_exp_claim():
    exp = mcp_credentials.jwt_expiry(_jwt(3600))
    assert exp is not None
    assert exp > datetime.utcnow()
    # 无 exp / 非法 token 一律返回 None（不抛异常，不阻断调用）
    assert mcp_credentials.jwt_expiry(f"{_b64({'alg': 'none'})}.{_b64({'sub': 'x'})}.sig") is None
    assert mcp_credentials.jwt_expiry("garbage") is None
    assert mcp_credentials.jwt_expiry("") is None
    assert mcp_credentials.jwt_expiry(f"{_b64({'a': 1})}.!!!.{_b64({'b': 2})}") is None


# -- 按人凭据存储 --------------------------------------------------------------


def test_user_credential_roundtrip_isolated_and_expiring(db_session):
    token = _jwt()
    row = mcp_credentials.save_user_credential(
        db_session, plugin_id=EHOME_PLUGIN_ID, human_no="E10281", header_name=USER_HEADER,
        secret=token, updated_by="E10281", expires_at=mcp_credentials.jwt_expiry(token),
    )
    db_session.commit()
    assert row is not None
    # 明文不落库，只留掩码
    assert row.secret_ciphertext != token
    assert token not in row.secret_ciphertext
    assert row.secret_hint == mcp_credentials.mask(token)
    assert mcp_credentials.load_user_credential(
        db_session, plugin_id=EHOME_PLUGIN_ID, human_no="E10281",
    ) == token
    # 按人隔离：另一个人没有凭据
    assert mcp_credentials.load_user_credential(
        db_session, plugin_id=EHOME_PLUGIN_ID, human_no="E20999",
    ) == ""
    assert mcp_credentials.user_credential_expired(row) is False

    expired = mcp_credentials.save_user_credential(
        db_session, plugin_id=EHOME_PLUGIN_ID, human_no="E10281", header_name=USER_HEADER,
        secret=_jwt(-60), updated_by="E10281", expires_at=mcp_credentials.jwt_expiry(_jwt(-60)),
    )
    db_session.commit()
    assert expired is not None and mcp_credentials.user_credential_expired(expired) is True

    # 空密钥表示清除该人凭据
    assert mcp_credentials.save_user_credential(
        db_session, plugin_id=EHOME_PLUGIN_ID, human_no="E10281", header_name=USER_HEADER,
        secret="", updated_by="E10281",
    ) is None
    db_session.commit()
    assert mcp_credentials.load_user_credential(
        db_session, plugin_id=EHOME_PLUGIN_ID, human_no="E10281",
    ) == ""


# -- 注册 / 授权 ---------------------------------------------------------------


def test_ensure_ehome_mcp_is_idempotent(db_session):
    seed.ensure_ehome_mcp(db_session)
    db_session.commit()

    plugin = db_session.get(models.Plugin, EHOME_PLUGIN_ID)
    assert plugin is not None
    assert plugin.plugin_type == "mcp" and plugin.scope == "shared"
    assert plugin.mcp_category == "employee_service"
    # 刻意只放 mcp_url_ref：不写 url/tool_prefix，避免被当成动态注册源重复构造适配器
    assert plugin.runtime_meta == {"mcp_url_ref": "DWP_EHOME_MCP_URL"}
    assert EHOME_PLUGIN_ID not in [item.id for item in mcp_registry.external_plugins(db_session)]

    version = db_session.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == EHOME_PLUGIN_ID,
    ))
    assert version is not None
    assert version.deployment_mode == "external"
    assert version.publish_status == "published"

    def _targets() -> set[str]:
        return {
            row.target_agent_id for row in db_session.scalars(select(models.AgentPluginBinding).where(
                models.AgentPluginBinding.plugin_id == EHOME_PLUGIN_ID,
            )).all()
        }

    assert _targets() == {"AI-XYZQ", "DT-E10281", "DT-E20999"}
    seed.ensure_ehome_mcp(db_session)
    db_session.commit()
    assert _targets() == {"AI-XYZQ", "DT-E10281", "DT-E20999"}


def test_ensure_ehome_mcp_skipped_when_unconfigured(db_session, monkeypatch):
    """URL 或应用级 key 缺失时不得注册插件（避免出现永远调不通的工具）。"""
    # 种子阶段可能已按环境变量注册过，这里先清干净再验证「未配置 → 不注册」。
    for row in db_session.scalars(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == EHOME_PLUGIN_ID,
    )).all():
        db_session.delete(row)
    for row in db_session.scalars(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == EHOME_PLUGIN_ID,
    )).all():
        db_session.delete(row)
    plugin = db_session.get(models.Plugin, EHOME_PLUGIN_ID)
    if plugin is not None:
        db_session.delete(plugin)
    db_session.commit()

    monkeypatch.delenv("DWP_EHOME_MCP_URL", raising=False)
    monkeypatch.delenv("DWP_EHOME_MCP_API_KEY", raising=False)
    monkeypatch.setenv("DWP_EHOME_MCP_API_KEY_FILE", "/nonexistent/ehome_mcp_api_key")
    seed.ensure_ehome_mcp(db_session)
    db_session.commit()
    assert db_session.get(models.Plugin, EHOME_PLUGIN_ID) is None


# -- 适配器：双头 / 前缀 / 声明 -------------------------------------------------


def test_required_inputs_declares_app_and_user_secret():
    entries = {item["key"]: item for item in ehome_adapter.required_inputs()}
    assert entries[APP_HEADER]["source"] == "app_secret"
    assert entries[USER_HEADER]["source"] == "user_secret"
    assert entries[USER_HEADER]["kind"] == "secret"
    # 通讯录类公开工具不需要用户级身份，避免无谓地打扰员工
    assert "getPhone" in entries[USER_HEADER]["exclude_tools"]


def test_prefixed_tools_carry_platform_hints(monkeypatch):
    """服务端 schema 与实现不一致的坑要写进工具描述，否则模型会反复试错。"""
    monkeypatch.setattr(ehome_adapter, "_client", _FakeClient(tools=[
        {"name": "getXtSubmitList", "description": "查询已发送审", "inputSchema": {"type": "object"}},
        {"name": "getEmpCardBill", "description": "餐卡账单", "inputSchema": {"type": "object"}},
        {"name": "getPhone", "description": "查手机号", "inputSchema": {"type": "object"}},
    ]))
    tools = {tool["name"]: tool for tool in ehome_adapter.prefixed_tools(force=True)}
    submit = tools[f"{EHOME_TOOL_PREFIX}getXtSubmitList"]["description"]
    assert "keyword" in submit and "必填" in submit
    assert "yyyyMMdd" in submit
    assert "resphead.code" in submit
    # 每个工具都带通用判读提示；没有专属提示的工具不会凭空多出内容
    assert "resphead.code" in tools[f"{EHOME_TOOL_PREFIX}getPhone"]["description"]
    assert "yyyyMMdd" not in tools[f"{EHOME_TOOL_PREFIX}getPhone"]["description"]


def test_meeting_room_hint_requires_string_argument():
    hint = ehome_adapter._tool_description_suffix_fn("getMeetingRoomUseInfo")
    assert "字符串" in hint and "getMeetingroomList" in hint


def test_call_tool_rejects_foreign_prefix():
    with pytest.raises(ExternalMcpError):
        ehome_adapter.call_tool("qieman__SearchFunds", {})


def test_call_tool_uses_per_call_headers(monkeypatch):
    seen: list[dict] = []
    fake = _FakeClient(result={"content": [{"type": "text", "text": "balance:100"}], "isError": False})

    def factory(headers):
        seen.append(headers)
        fake.headers = headers
        return fake

    monkeypatch.setattr(ehome_adapter, "_client_factory_headers", factory)
    result = ehome_adapter.call_tool(
        f"{EHOME_TOOL_PREFIX}getEmpCardBalance", {},
        {APP_HEADER: APP_KEY, USER_HEADER: "jwt-value"},
    )
    assert result["content"][0]["text"] == "balance:100"
    # 前缀在适配器内部剥离，转发给 server 的是原始工具名
    assert fake.calls == [("getEmpCardBalance", {})]
    assert seen == [{APP_HEADER: APP_KEY, USER_HEADER: "jwt-value"}]


def test_static_path_unchanged_without_headers(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(ehome_adapter, "_client", fake)
    monkeypatch.setattr(ehome_adapter, "_client_factory_headers", lambda headers: fake)
    ehome_adapter.call_tool(f"{EHOME_TOOL_PREFIX}getPhone", {"empName": "张三"})
    assert fake.calls == [("getPhone", {"empName": "张三"})]


# -- 工具清单 / 调用链路（走 /internal/agent-tools/mcp）-------------------------


def test_tools_list_needs_only_app_key(client, db_session, monkeypatch):
    seed.ensure_ehome_mcp(db_session)
    db_session.commit()
    monkeypatch.setattr(ehome_adapter, "_client", _FakeClient())

    catalog = client.post(
        "/internal/agent-tools/mcp", headers=_headers_for(_token()),
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ).json()
    names = [tool["name"] for tool in catalog["result"]["tools"]]
    assert f"{EHOME_TOOL_PREFIX}getEmpCardBalance" in names
    assert f"{EHOME_TOOL_PREFIX}getPhone" in names


def test_tools_list_hidden_without_binding(client, db_session, monkeypatch):
    seed.ensure_ehome_mcp(db_session)
    db_session.commit()
    monkeypatch.setattr(ehome_adapter, "_client", _FakeClient())

    catalog = client.post(
        "/internal/agent-tools/mcp",
        headers=_headers_for(_token(employee_id="AI-INVESTMENT", trace_id="T-EHOME-2")),
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ).json()
    names = [tool["name"] for tool in catalog["result"]["tools"]]
    assert not any(name.startswith(EHOME_TOOL_PREFIX) for name in names)


def test_call_without_user_jwt_returns_input_required(client, db_session, monkeypatch):
    seed.ensure_ehome_mcp(db_session)
    db_session.commit()
    fake = _FakeClient()
    monkeypatch.setattr(ehome_adapter, "_client", fake)
    monkeypatch.setattr(ehome_adapter, "_client_factory_headers", lambda headers: fake)

    body = client.post(
        "/internal/agent-tools/mcp", headers=_headers_for(_token(trace_id="T-EHOME-3")),
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": f"{EHOME_TOOL_PREFIX}getEmpCardBalance", "arguments": {}}},
    ).json()

    result = body["result"]
    assert result["isError"] is False
    required = result["dwpInputRequired"]
    assert required["plugin_id"] == EHOME_PLUGIN_ID
    assert [field["key"] for field in required["fields"]] == [USER_HEADER]
    assert required["fields"][0]["reason"] == "missing"
    # 缺凭据时绝不发起真实调用
    assert fake.calls == []

    row = db_session.get(models.ToolInputRequest, required["request_id"])
    assert row is not None
    assert row.status == tool_input.STATUS_PENDING
    assert row.execution_id == ""  # 该 trace 没有执行记录，仍要落库以便前端展示
    assert USER_HEADER in json.dumps(row.fields, ensure_ascii=False)


def test_call_with_stored_jwt_passes_both_headers(client, db_session, monkeypatch):
    seed.ensure_ehome_mcp(db_session)
    mcp_credentials.save_user_credential(
        db_session, plugin_id=EHOME_PLUGIN_ID, human_no="E10281", header_name=USER_HEADER,
        secret=_jwt(), updated_by="E10281", expires_at=mcp_credentials.jwt_expiry(_jwt()),
    )
    db_session.commit()

    seen: list[dict] = []
    fake = _FakeClient(result={"content": [{"type": "text", "text": "balance:100"}], "isError": False})

    def factory(headers):
        seen.append(headers)
        return fake

    monkeypatch.setattr(ehome_adapter, "_client", fake)
    monkeypatch.setattr(ehome_adapter, "_client_factory_headers", factory)

    body = client.post(
        "/internal/agent-tools/mcp", headers=_headers_for(_token(trace_id="T-EHOME-4")),
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": f"{EHOME_TOOL_PREFIX}getEmpCardBalance", "arguments": {}}},
    ).json()
    assert body["result"]["content"][0]["text"] == "balance:100"
    assert seen and seen[0][APP_HEADER] == APP_KEY
    assert seen[0][USER_HEADER] == mcp_credentials.load_user_credential(
        db_session, plugin_id=EHOME_PLUGIN_ID, human_no="E10281",
    )


def test_expired_jwt_triggers_input_required(client, db_session, monkeypatch):
    seed.ensure_ehome_mcp(db_session)
    stale = _jwt(-120)
    mcp_credentials.save_user_credential(
        db_session, plugin_id=EHOME_PLUGIN_ID, human_no="E10281", header_name=USER_HEADER,
        secret=stale, updated_by="E10281", expires_at=mcp_credentials.jwt_expiry(stale),
    )
    db_session.commit()
    fake = _FakeClient()
    monkeypatch.setattr(ehome_adapter, "_client", fake)
    monkeypatch.setattr(ehome_adapter, "_client_factory_headers", lambda headers: fake)

    body = client.post(
        "/internal/agent-tools/mcp", headers=_headers_for(_token(trace_id="T-EHOME-5")),
        json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": f"{EHOME_TOOL_PREFIX}getEmpCardBalance", "arguments": {}}},
    ).json()
    fields = body["result"]["dwpInputRequired"]["fields"]
    assert fields[0]["reason"] == "expired"
    # 已过期凭据只回掩码，不回明文
    assert fields[0]["masked"] == mcp_credentials.mask(stale)
    assert stale not in json.dumps(body, ensure_ascii=False)
    assert fake.calls == []


def test_missing_header_error_becomes_input_required(client, db_session, monkeypatch):
    """服务端把缺参放在 result 里（未抛异常）时，同样要转成待补全。"""
    seed.ensure_ehome_mcp(db_session)
    mcp_credentials.save_user_credential(
        db_session, plugin_id=EHOME_PLUGIN_ID, human_no="E10281", header_name=USER_HEADER,
        secret=_jwt(), updated_by="E10281", expires_at=mcp_credentials.jwt_expiry(_jwt()),
    )
    db_session.commit()
    fake = _FakeClient(result={
        "isError": True,
        "content": [{"type": "text", "text": "Missing required header 'X-MCP-JWT'"}],
    })
    monkeypatch.setattr(ehome_adapter, "_client", fake)
    monkeypatch.setattr(ehome_adapter, "_client_factory_headers", lambda headers: fake)

    body = client.post(
        "/internal/agent-tools/mcp", headers=_headers_for(_token(trace_id="T-EHOME-6")),
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/call",
              "params": {"name": f"{EHOME_TOOL_PREFIX}getVacationDay", "arguments": {}}},
    ).json()
    result = body["result"]
    assert result["isError"] is False
    assert [field["key"] for field in result["dwpInputRequired"]["fields"]] == [USER_HEADER]
    assert result["dwpInputRequired"]["reason"] == tool_input.REASON_TOOL_ERROR


def test_keyword_required_error_becomes_text_field(client, db_session, monkeypatch):
    """未声明的业务参数（keyword 实际必填但 schema 未标注）要映射成文本字段。"""
    seed.ensure_ehome_mcp(db_session)
    mcp_credentials.save_user_credential(
        db_session, plugin_id=EHOME_PLUGIN_ID, human_no="E10281", header_name=USER_HEADER,
        secret=_jwt(), updated_by="E10281", expires_at=mcp_credentials.jwt_expiry(_jwt()),
    )
    db_session.commit()
    fake = _FakeClient(result={
        "isError": True,
        "content": [{"type": "text", "text": "请求参数校验失败：keyword 为必填项"}],
    })
    monkeypatch.setattr(ehome_adapter, "_client", fake)
    monkeypatch.setattr(ehome_adapter, "_client_factory_headers", lambda headers: fake)

    body = client.post(
        "/internal/agent-tools/mcp", headers=_headers_for(_token(trace_id="T-EHOME-7")),
        json={"jsonrpc": "2.0", "id": 6, "method": "tools/call",
              "params": {"name": f"{EHOME_TOOL_PREFIX}getXtSubmitList",
                         "arguments": {"beginDate": "20260901", "endDate": "20260922"}}},
    ).json()
    fields = body["result"]["dwpInputRequired"]["fields"]
    assert [field["key"] for field in fields] == ["keyword"]
    assert fields[0]["kind"] == "text"
