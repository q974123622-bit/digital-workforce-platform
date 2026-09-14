import time

import pytest

from app.services.harness_token import issue_token, verify_token
from app.services.chat import ChatResult


def test_harness_token_round_trip_and_tampering(monkeypatch):
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    token = issue_token(
        employee_id="AI-GENERAL", requester_human_no="E10281",
        trace_id="T-1", depth=0,
    )
    claims = verify_token(token)
    assert claims.employee_id == "AI-GENERAL"
    assert claims.requester_human_no == "E10281"
    with pytest.raises(ValueError):
        verify_token(token[:-1] + ("A" if token[-1] != "A" else "B"))


def test_harness_token_rejects_expired(monkeypatch):
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    token = issue_token(
        employee_id="AI-GENERAL", requester_human_no="E10281",
        trace_id="T-1", depth=0, ttl_seconds=30,
    )
    monkeypatch.setattr(time, "time", lambda: 9_999_999_999)
    with pytest.raises(ValueError, match="已过期"):
        verify_token(token)


def test_mcp_catalog_hides_delegate_from_role_employee(client, monkeypatch):
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    token = issue_token(
        employee_id="AI-GENERAL", requester_human_no="E10281",
        trace_id="T-MCP", depth=0,
    )
    response = client.post(
        "/internal/agent-tools/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert response.status_code == 200
    names = [tool["name"] for tool in response.json()["result"]["tools"]]
    assert names == ["search_knowledge"]


def test_mcp_catalog_allows_one_delegate_for_twin(client, monkeypatch):
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    token = issue_token(
        employee_id="DT-E10281", requester_human_no="E10281",
        trace_id="T-MCP", depth=0,
    )
    response = client.post(
        "/internal/agent-tools/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    names = [tool["name"] for tool in response.json()["result"]["tools"]]
    # 张三分身没有直接知识库；可进行一次受控委派。
    assert names == ["ask_digital_employee"]


def test_role_employee_cannot_delegate(client, monkeypatch):
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    token = issue_token(
        employee_id="AI-GENERAL", requester_human_no="E10281",
        trace_id="T-NO-DELEGATE", depth=0,
    )
    response = client.post(
        "/internal/agent-tools/delegate",
        headers={"Authorization": f"Bearer {token}"},
        json={"target_agent_id": "AI-INVESTMENT", "question": "q"},
    )
    assert response.status_code == 403


def test_twin_can_delegate_only_once(client, monkeypatch):
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    token = issue_token(
        employee_id="DT-E10281", requester_human_no="E10281",
        trace_id="T-ONE-DELEGATE", depth=0,
    )

    def fake_run_agent(*_args, **kwargs):
        return ChatResult(session_id="H", trace_id=kwargs["trace_id"], message="answer")

    monkeypatch.setattr("app.services.harness_agent.run_agent", fake_run_agent)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"target_agent_id": "AI-INVESTMENT", "question": "q"}
    assert client.post("/internal/agent-tools/delegate", headers=headers, json=payload).status_code == 200
    assert client.post("/internal/agent-tools/delegate", headers=headers, json=payload).status_code == 409


def test_tool_body_cannot_override_token_identity(client, monkeypatch):
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    token = issue_token(
        employee_id="AI-GENERAL", requester_human_no="E10281",
        trace_id="T-NO-SPOOF", depth=0,
    )
    response = client.post(
        "/internal/agent-tools/knowledge/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"employee_id": "AI-INVESTMENT", "knowledge_base_id": "KB-IT-SERVICE", "query": "VPN"},
    )
    assert response.status_code == 422


class _FakeQiemanAdapter:
    plugin_id = "qieman-fund-mcp"
    tool_prefix = "qieman__"
    source_label = "盈米"

    def configured(self):
        return True

    def is_authorized(self, db, employee_id):
        return True

    def prefixed_tools(self, force=False):
        return [{"name": "qieman__SearchFunds", "description": "搜索基金", "inputSchema": {"type": "object"}}]

    def call_tool(self, name, arguments):
        return {"content": [{"type": "text", "text": f"called:{name}"}], "isError": False}


def test_mcp_catalog_merges_and_forwards_qieman_tools(client, monkeypatch):
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    from app.routers import internal as internal_mod

    monkeypatch.setattr(internal_mod, "_EXTERNAL_ADAPTERS", (_FakeQiemanAdapter(),))
    token = issue_token(employee_id="AI-GENERAL", requester_human_no="E10281", trace_id="T-QIEMAN", depth=0)
    headers = {"Authorization": f"Bearer {token}"}

    # tools/list 动态合并盈米工具，且保留内部工具
    response = client.post(
        "/internal/agent-tools/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    names = [tool["name"] for tool in response.json()["result"]["tools"]]
    assert "qieman__SearchFunds" in names
    assert "search_knowledge" in names

    # tools/call 按前缀转发到盈米
    response2 = client.post(
        "/internal/agent-tools/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "qieman__SearchFunds", "arguments": {"keyword": "x"}}},
    )
    assert response2.json()["result"]["content"][0]["text"] == "called:qieman__SearchFunds"
