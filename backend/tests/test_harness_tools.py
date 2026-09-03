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
    assert names == ["search_knowledge", "save_memory"]


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
    # 张三分身没有直接知识库；可进行一次受控委派，并可保存显式长期记忆。
    assert names == ["ask_digital_employee", "save_memory"]


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
