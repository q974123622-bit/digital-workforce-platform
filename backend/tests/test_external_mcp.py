"""External MCP Client / Adapter 单元测试（全部走 httpx.MockTransport，不连真实服务）。"""

import json

import httpx
import pytest
from sqlalchemy import select

from app import models
from app.services.external_mcp_adapter import QIEMAN_PLUGIN_ID, QIEMAN_TOOL_PREFIX, QiemanMcpAdapter
from app.services.external_mcp_client import ExternalMcpClient, ExternalMcpError


def _make_qieman_adapter(client=None, url="", api_key=""):
    return QiemanMcpAdapter(
        plugin_id=QIEMAN_PLUGIN_ID,
        tool_prefix=QIEMAN_TOOL_PREFIX,
        source_label="盈米",
        url_fn=lambda: url,
        api_key_fn=lambda: api_key,
        timeout_fn=lambda: 20.0,
        client=client,
    )


def _mcp_response(payload, result):
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload.get("id"), "result": result})


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# -- ExternalMcpClient ---------------------------------------------------

def test_client_initialize_list_and_call():
    def handler(request):
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return _mcp_response(payload, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {"listChanged": True}}})
        if method == "notifications/initialized":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": {}})
        if method == "tools/list":
            return _mcp_response(payload, {"tools": [{"name": "SearchFunds", "description": "搜索基金", "inputSchema": {"type": "object"}}]})
        if method == "tools/call":
            return _mcp_response(payload, {"content": [{"type": "text", "text": "ok"}], "isError": False})
        return _mcp_response(payload, {})

    http_client = _client(handler)
    mcp = ExternalMcpClient(url="https://mcp.invalid", api_key="secret-key", client=http_client)
    tools = mcp.list_tools()
    assert tools == [{"name": "SearchFunds", "description": "搜索基金", "inputSchema": {"type": "object"}}]
    result = mcp.call_tool("SearchFunds", {"keyword": "沪深300"})
    assert result["isError"] is False
    assert result["content"][0]["text"] == "ok"
    http_client.close()


def test_client_sends_api_key_header():
    captured = {}

    def handler(request):
        captured["headers"] = request.headers
        payload = json.loads(request.content)
        if payload.get("method") == "initialize":
            return _mcp_response(payload, {"capabilities": {}})
        if payload.get("method") == "notifications/initialized":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": {}})
        if payload.get("method") == "tools/list":
            return _mcp_response(payload, {"tools": []})
        return _mcp_response(payload, {})

    http_client = _client(handler)
    ExternalMcpClient(url="https://mcp.invalid", api_key="secret-key", client=http_client).list_tools()
    http_client.close()
    assert captured["headers"]["x-api-key"] == "secret-key"
    assert "application/json, text/event-stream" in captured["headers"]["Accept"]


def test_client_http_401_raises_without_leaking():
    def handler(_request):
        return httpx.Response(401, text="credential and secret body")

    http_client = _client(handler)
    mcp = ExternalMcpClient(url="https://mcp.invalid", api_key="secret-key", client=http_client)
    with pytest.raises(ExternalMcpError) as caught:
        mcp.list_tools()
    http_client.close()
    assert "认证失败" in str(caught.value)
    assert "credential" not in str(caught.value)


def test_client_jsonrpc_error_raises():
    def handler(request):
        payload = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32601, "message": "Method not found"}})

    http_client = _client(handler)
    mcp = ExternalMcpClient(url="https://mcp.invalid", api_key="k", client=http_client)
    with pytest.raises(ExternalMcpError, match="Method not found"):
        mcp.list_tools()
    http_client.close()


# -- QiemanMcpAdapter ----------------------------------------------------

def test_adapter_caches_tool_list():
    count = {"n": 0}

    def handler(request):
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "tools/list":
            count["n"] += 1
            return _mcp_response(payload, {"tools": [{"name": "SearchFunds", "inputSchema": {}}]})
        if method == "initialize":
            return _mcp_response(payload, {"capabilities": {}})
        if method == "notifications/initialized":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": {}})
        return _mcp_response(payload, {})

    http_client = _client(handler)
    mcp_client = ExternalMcpClient(url="https://mcp.invalid", api_key="k", client=http_client)
    adapter = _make_qieman_adapter(client=mcp_client)
    adapter.list_tools()
    adapter.list_tools()
    http_client.close()
    assert count["n"] == 1  # 第二次命中进程级缓存


def test_adapter_prefixed_tools_adds_namespace():
    def handler(request):
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "tools/list":
            return _mcp_response(payload, {"tools": [{"name": "SearchFunds", "inputSchema": {}}, {"name": "GuessFundCode", "inputSchema": {}}]})
        if method == "initialize":
            return _mcp_response(payload, {"capabilities": {}})
        if method == "notifications/initialized":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": {}})
        return _mcp_response(payload, {})

    http_client = _client(handler)
    adapter = _make_qieman_adapter(client=ExternalMcpClient(url="https://mcp.invalid", api_key="k", client=http_client))
    names = [t["name"] for t in adapter.prefixed_tools()]
    http_client.close()
    assert names == ["qieman__SearchFunds", "qieman__GuessFundCode"]


def test_adapter_call_tool_strips_prefix():
    called = {}

    def handler(request):
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "tools/call":
            called["name"] = payload["params"]["name"]
            return _mcp_response(payload, {"content": [{"type": "text", "text": "done"}], "isError": False})
        if method == "initialize":
            return _mcp_response(payload, {"capabilities": {}})
        if method == "notifications/initialized":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": {}})
        return _mcp_response(payload, {})

    http_client = _client(handler)
    adapter = _make_qieman_adapter(client=ExternalMcpClient(url="https://mcp.invalid", api_key="k", client=http_client))
    result = adapter.call_tool("qieman__SearchFunds", {"keyword": "x"})
    http_client.close()
    assert called["name"] == "SearchFunds"  # 已去掉前缀
    assert result["content"][0]["text"] == "done"


def test_adapter_not_configured_returns_empty(monkeypatch):
    monkeypatch.delenv("DWP_QIEMAN_MCP_URL", raising=False)
    monkeypatch.delenv("DWP_QIEMAN_MCP_API_KEY", raising=False)
    adapter = _make_qieman_adapter()
    assert adapter.configured() is False
    assert adapter.list_tools() == []
    assert adapter.prefixed_tools() == []


def test_adapter_authorization_gate(db_session):
    if db_session.get(models.Plugin, QIEMAN_PLUGIN_ID) is None:
        db_session.add(models.Plugin(id=QIEMAN_PLUGIN_ID, name="盈米基金数据 MCP", type="mcp"))
    binding = db_session.scalar(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == QIEMAN_PLUGIN_ID,
        models.AgentPluginBinding.target_agent_id == "AI-FUND",
    ))
    if binding is None:
        db_session.add(models.AgentPluginBinding(
            plugin_id=QIEMAN_PLUGIN_ID, target_agent_id="AI-FUND",
            admin_enabled=True, employee_enabled=True, decision_mode="allow",
        ))
    db_session.commit()
    adapter = _make_qieman_adapter()
    assert adapter.is_authorized(db_session, "AI-FUND") is True
    assert adapter.is_authorized(db_session, "AI-GENERAL") is False
