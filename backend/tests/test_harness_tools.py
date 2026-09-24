import json
import time

import pytest
from fastapi import HTTPException

from app import models
from app.services.harness_token import issue_token, verify_token
from app.services.chat import ChatResult


def _bind_skill(db, skill_id: str, target_agent_id: str, **overrides) -> None:
    name = overrides.pop("name", "报销制度速答")
    description = overrides.pop("description", "掌握差旅报销标准")
    content = overrides.pop("content", "1. 差旅住宿标准：一线城市每晚不超过 600 元。")
    db.add(models.Skill(
        id=skill_id, owner_human_no="E10281", name=name,
        description=description, content=content,
        status=overrides.pop("status", "active"),
    ))
    # 技能以 Plugin 为唯一载体：正文取已发布版本的 manifest['instruction']。
    plugin_id = f"SKILL-{skill_id}"
    db.add(models.Plugin(
        id=plugin_id, name=name, type="skill", plugin_type="skill", scope="personal",
        owner_human_no="E10281", status="active", description=description,
        current_version="1.0.0",
    ))
    db.add(models.PluginVersion(
        plugin_id=plugin_id, version="1.0.0", deployment_mode="instruction",
        artifact_path="", sha256="test", manifest={"instruction": content},
        data_level="L1", review_status="approved", publish_status="published",
        submitted_by="E10281", reviewed_by="automatic-policy",
    ))
    db.add(models.AgentPluginBinding(
        plugin_id=plugin_id, target_agent_id=target_agent_id,
        authorized_by=overrides.pop("authorized_by", "automatic-policy"),
        admin_enabled=overrides.pop("admin_enabled", True),
        employee_enabled=overrides.pop("employee_enabled", True),
        decision_mode=overrides.pop("decision_mode", "allow"),
        **overrides,
    ))
    db.commit()


def test_load_skill_tool_lists_and_returns_sop(client, db_session, monkeypatch):
    """技能按需加载：目录里给枚举，模型自行判断后调 load_skill 取回完整 SOP。"""
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    _bind_skill(db_session, "SK-LOAD-1", "DT-E10281")
    token = issue_token(employee_id="DT-E10281", requester_human_no="E10281", trace_id="T-SKILL", depth=0)
    headers = {"Authorization": f"Bearer {token}"}

    catalog = client.post(
        "/internal/agent-tools/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ).json()
    tool = next(item for item in catalog["result"]["tools"] if item["name"] == "load_skill")
    assert tool["inputSchema"]["properties"]["skill_name"]["enum"] == ["报销制度速答"]

    called = client.post(
        "/internal/agent-tools/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "load_skill", "arguments": {"skill_name": "报销制度速答"}}},
    )
    body = json.loads(called.json()["result"]["content"][0]["text"])
    assert body["skill_name"] == "报销制度速答"
    assert body["sop_truncated"] is False
    assert "600" in body["sop"]


def test_load_skill_hides_and_rejects_unbound_skill(client, db_session, monkeypatch):
    """未绑定（或员工已停用）的技能既不出现在工具里，也不能靠编造技能名取回。"""
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    _bind_skill(db_session, "SK-LOAD-2", "DT-E10281", employee_enabled=False)
    db_session.add(models.Skill(
        id="SK-LOAD-3", owner_human_no="E10281", name="从未绑定技能",
        description="d", content="c", status="active",
    ))
    db_session.commit()
    token = issue_token(employee_id="DT-E10281", requester_human_no="E10281", trace_id="T-SKILL2", depth=0)
    headers = {"Authorization": f"Bearer {token}"}

    names = [
        item["name"] for item in client.post(
            "/internal/agent-tools/mcp", headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        ).json()["result"]["tools"]
    ]
    assert "load_skill" not in names

    denied = client.post(
        "/internal/agent-tools/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "load_skill", "arguments": {"skill_name": "从未绑定技能"}}},
    )
    # MCP 约定：工具级失败以 isError 结果返回（HTTP 仍是 200）
    body = denied.json()["result"]
    assert body["isError"] is True
    assert "技能不存在或未授权" in body["content"][0]["text"]


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


def test_harness_token_carries_optional_goal(monkeypatch):
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    token = issue_token(
        employee_id="DT-E10281", requester_human_no="E10281",
        trace_id="T-G", depth=0, goal="报销流程怎么走",
    )
    assert verify_token(token).goal == "报销流程怎么走"
    # 未传 goal 时向后兼容（旧令牌无该字段）
    assert verify_token(issue_token(
        employee_id="DT-E10281", requester_human_no="E10281", trace_id="T-G2", depth=0,
    )).goal == ""


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


def test_failed_delegation_records_error_and_keeps_budget(client, db_session, monkeypatch):
    """委派失败必须落原因，且不消耗「单次任务只能委派一次」的额度。

    回归背景（2026-09-17 实测）：两个数字分身同时委派同一目标时，后到的那个因目标员工
    正忙被 409 拒绝且不重试 → `delegation_run` 只留 `status=failed` + `evidence=[]`，
    发起方仍返回 completed，用户拿到的是**没有知识库依据**的答案；而且失败已经占用了
    委派额度，模型连重试的机会都没有。
    """
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    calls = {"n": 0}

    def flaky_run_agent(*_args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise HTTPException(
                status_code=409,
                detail={"message": "投资分析AI员工 正在执行其他任务，已等待 60 秒仍未空闲",
                        "retryable": True},
            )
        return ChatResult(session_id="H", trace_id=kwargs["trace_id"], message="answer")

    monkeypatch.setattr("app.services.harness_agent.run_agent", flaky_run_agent)
    token = issue_token(
        employee_id="DT-E10281", requester_human_no="E10281",
        trace_id="T-DELEGATE-RETRY", depth=0,
    )
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"target_agent_id": "AI-INVESTMENT", "question": "q"}

    assert client.post("/internal/agent-tools/delegate", headers=headers, json=payload).status_code == 409
    runs = db_session.query(models.DelegationRun).filter_by(trace_id="T-DELEGATE-RETRY").all()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert "正在执行其他任务" in runs[0].error

    # 失败不消耗额度 → 允许重试，并且这次成功
    retry = client.post("/internal/agent-tools/delegate", headers=headers, json=payload)
    assert retry.status_code == 200, retry.text
    statuses = [
        row.status for row in db_session.query(models.DelegationRun)
        .filter_by(trace_id="T-DELEGATE-RETRY").order_by(models.DelegationRun.created_at).all()
    ]
    assert statuses == ["failed", "completed"]

    # 成功之后仍然只允许委派一次
    assert client.post("/internal/agent-tools/delegate", headers=headers, json=payload).status_code == 409


def test_delegation_attempts_are_capped(client, db_session, monkeypatch):
    """连续失败也要有上限，避免退化成委派循环。"""
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")

    def always_busy(*_args, **_kwargs):
        raise HTTPException(status_code=409, detail={"message": "AI员工正忙，请稍后重试", "retryable": True})

    monkeypatch.setattr("app.services.harness_agent.run_agent", always_busy)
    token = issue_token(
        employee_id="DT-E10281", requester_human_no="E10281",
        trace_id="T-DELEGATE-CAP", depth=0,
    )
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"target_agent_id": "AI-INVESTMENT", "question": "q"}
    for _ in range(4):
        assert client.post("/internal/agent-tools/delegate", headers=headers, json=payload).status_code == 409

    rows = db_session.query(models.DelegationRun).filter_by(trace_id="T-DELEGATE-CAP").all()
    assert len(rows) == 3, "超过上限的尝试不应再产生委派记录"


def test_delegation_passes_slot_wait_seconds(client, monkeypatch):
    """委派链路要把「等待目标员工空闲」的预算传给 run_agent（而不是立即失败）。"""
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    monkeypatch.setenv("DWP_DELEGATION_SLOT_WAIT_SECONDS", "30")
    captured: dict = {}

    def fake_run_agent(*_args, **kwargs):
        captured.update(kwargs)
        return ChatResult(session_id="H", trace_id=kwargs["trace_id"], message="answer")

    monkeypatch.setattr("app.services.harness_agent.run_agent", fake_run_agent)
    token = issue_token(
        employee_id="DT-E10281", requester_human_no="E10281", trace_id="T-SLOT-WAIT", depth=0,
    )
    response = client.post(
        "/internal/agent-tools/delegate",
        headers={"Authorization": f"Bearer {token}"},
        json={"target_agent_id": "AI-GENERAL", "question": "q"},
    )
    assert response.status_code == 200
    assert captured["slot_wait_seconds"] == 30


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


def test_delegate_enum_is_dynamic_and_describes_expertise(client, monkeypatch):
    """委派枚举必须来自在岗岗位员工，且带专长描述，不能写死。"""
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    token = issue_token(employee_id="DT-E10281", requester_human_no="E10281", trace_id="T-ENUM", depth=0)
    response = client.post(
        "/internal/agent-tools/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    tool = next(t for t in response.json()["result"]["tools"] if t["name"] == "ask_digital_employee")
    enum = tool["inputSchema"]["properties"]["target_agent_id"]["enum"]
    assert "AI-GENERAL" in enum
    # 分身不是 role_employee，不能出现在委派目标里
    assert not any(item.startswith("DT-") for item in enum)
    # 描述里必须带上员工专长，模型才能按能力而非名字猜
    assert any(item in tool["description"] for item in enum)


def test_delegate_enum_drops_deactivated_employee(client, db_session, monkeypatch):
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    employee = db_session.get(models.DigitalEmployee, "AI-XYZQ")
    employee.status = "disabled"
    db_session.commit()

    token = issue_token(employee_id="DT-E10281", requester_human_no="E10281", trace_id="T-ENUM2", depth=0)
    response = client.post(
        "/internal/agent-tools/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    tool = next(t for t in response.json()["result"]["tools"] if t["name"] == "ask_digital_employee")
    assert "AI-XYZQ" not in tool["inputSchema"]["properties"]["target_agent_id"]["enum"]


def test_delegation_passes_parent_goal_to_callee(client, monkeypatch):
    """委派时把上游原始目标透传给被委派方，避免多跳语义衰减。"""
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    captured: dict = {}

    def fake_run_agent(*_args, **kwargs):
        captured.update(kwargs)
        return ChatResult(session_id="H", trace_id=kwargs["trace_id"], message="answer")

    monkeypatch.setattr("app.services.harness_agent.run_agent", fake_run_agent)
    token = issue_token(
        employee_id="DT-E10281", requester_human_no="E10281", trace_id="T-GOAL", depth=0,
        goal="报销流程怎么走",
    )
    response = client.post(
        "/internal/agent-tools/delegate",
        headers={"Authorization": f"Bearer {token}"},
        json={"target_agent_id": "AI-GENERAL", "question": "差旅住宿标准是多少"},
    )
    assert response.status_code == 200
    assert captured["parent_goal"] == "报销流程怎么走"
