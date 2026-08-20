import pytest

from app import models
from app.services.gateway import invoke_plugin
from app.services.runtime_adapter import RuntimeResult


class SuccessHarness:
    def __init__(self, text="Harness 已形成报销工具调用计划"):
        self.calls = []
        self.text = text

    def run(self, *, employee_id, task_prompt, trace_id, context=None):
        self.calls.append((employee_id, task_prompt, trace_id, context))
        return RuntimeResult(mode="harness", ok=True, result=self.text)


class FailedHarness:
    def run(self, *, employee_id, task_prompt, trace_id, context=None):
        return RuntimeResult(mode="demo", ok=False, result="不可用")


def test_capability_catalog_unifies_plugins_and_skills(client):
    resp = client.get("/api/v1/capabilities?actor_no=E10281")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 15  # 11 plugins + 张三的 4 skills
    skill = next(row for row in rows if row["id"] == "SK-0001")
    workflow = next(row for row in rows if row["id"] == "expense-claim")
    assert skill["kind"] == "instruction" and skill["executable"] is False
    assert workflow["executable"] is True
    assert workflow["executor"]["primary"] == "harness"
    assert workflow["ready"] is True


def test_harness_drives_then_calls_adapter_once(db_session, monkeypatch):
    harness = SuccessHarness()
    calls = []

    def adapter_once(plugin, params):
        calls.append(plugin.id)
        return {"source": "demo", "workflow": plugin.id, "status": "submitted"}

    monkeypatch.setattr("app.services.capability_executor.run_adapter", adapter_once)
    result = invoke_plugin(
        db_session,
        employee_id="VE-0002",
        plugin_id="expense-claim",
        action="execute",
        params={"employee_name": "张三", "amount": 100},
        trace_id="CAP-H-1",
        runtime=harness,
        execution_context={
            "task_id": "TASK-001",
            "request": "帮张三报销",
            "subtask": "提交报销申请",
            "collaboration_summary": "AgentTeams 已确认金额与发票齐全",
        },
    )
    assert result["runtime_mode"] == "harness"
    assert result["tool_name"] == "差旅报销流程"
    assert result["data"]["workflow"] == "expense-claim"
    assert calls == ["expense-claim"]
    assert len(harness.calls) == 1
    prompt = harness.calls[0][1]
    assert "TASK-001" in prompt and "VE-0002" in prompt
    assert "AgentTeams 已确认金额与发票齐全" in prompt
    assert harness.calls[0][3].context_id == "VE-0002:TASK-001"


def test_harness_failure_falls_back_to_adapter_once(db_session, monkeypatch):
    calls = []

    def adapter_once(plugin, params):
        calls.append(plugin.id)
        return {"source": "demo", "status": "submitted"}

    monkeypatch.setattr("app.services.capability_executor.run_adapter", adapter_once)
    result = invoke_plugin(
        db_session,
        employee_id="VE-0002",
        plugin_id="expense-claim",
        action="execute",
        params={},
        trace_id="CAP-H-2",
        runtime=FailedHarness(),
    )
    assert result["runtime_mode"] == "demo_adapter"
    assert calls == ["expense-claim"]


def test_harness_summary_is_not_truncated(db_session):
    long_summary = "计划开始：" + ("完整执行细节。" * 200) + "：计划结束"
    result = invoke_plugin(
        db_session,
        employee_id="VE-0002",
        plugin_id="expense-claim",
        action="execute",
        params={},
        trace_id="CAP-LONG-1",
        runtime=SuccessHarness(long_summary),
    )
    assert len(long_summary) > 1000
    assert result["runtime_summary"] == long_summary


@pytest.mark.parametrize(
    ("employee_id", "plugin_id", "tool_name", "approval_granted"),
    [
        ("VE-0002", "hr-employee-mcp", "员工查询 MCP", False),
        ("VE-0003", "adp-onboarding", "入职流程 Workflow", False),
        ("RPA-0001", "rpa-report", "报表机器人", True),
        ("VE-0004", "purchase-request", "采购申请流程", True),
    ],
)
def test_each_worker_gets_independent_harness_context(
    db_session, employee_id, plugin_id, tool_name, approval_granted
):
    harness = SuccessHarness()
    task_id = f"TASK-{employee_id}"
    result = invoke_plugin(
        db_session,
        employee_id=employee_id,
        plugin_id=plugin_id,
        action="execute",
        params={"employee_name": "岳灵珊"},
        trace_id=task_id,
        approval_granted=approval_granted,
        runtime=harness,
        execution_context={
            "task_id": task_id,
            "request": "完成跨员工协作任务",
            "subtask": f"由 {employee_id} 执行岗位子任务",
            "collaboration_summary": "AgentTeams 已完成分工并确认执行边界",
        },
    )
    context = harness.calls[0][3]
    prompt = harness.calls[0][1]
    assert result["runtime_mode"] == "harness"
    assert result["runtime_context_id"] == f"{employee_id}:{task_id}"
    assert result["tool_name"] == tool_name
    assert context.employee_id == employee_id
    assert context.role_prompt and context.responsibility
    assert context.collaboration_summary == "AgentTeams 已完成分工并确认执行边界"
    assert all(value in prompt for value in (employee_id, task_id, context.role_prompt, context.responsibility))


def test_disabled_plugin_and_wrong_action_are_rejected(client):
    assert client.put("/api/v1/plugins/expense-claim", json={"status": "disabled"}).status_code == 200
    disabled = client.post(
        "/internal/gateway/invoke",
        json={
            "employee_id": "VE-0002",
            "plugin_id": "expense-claim",
            "action": "execute",
            "params": {},
            "trace_id": "CAP-DISABLED",
        },
    )
    assert disabled.status_code == 409

    wrong_action = client.post(
        "/internal/gateway/invoke",
        json={
            "employee_id": "VE-0002",
            "plugin_id": "leave-request",
            "action": "read",
            "params": {},
            "trace_id": "CAP-ACTION",
        },
    )
    assert wrong_action.status_code == 400


def test_skill_owner_and_plugin_reference_integrity(client):
    assert client.put(
        "/api/v1/skills/SK-0001?actor_no=E20999",
        json={"status": "disabled"},
    ).status_code == 403
    assert client.delete("/api/v1/skills/SK-0001?actor_no=E20999").status_code == 403
    assert client.delete("/api/v1/plugins/expense-claim").status_code == 409


def test_unregistered_dynamic_plugin_is_visible_but_not_executable(client):
    created = client.post(
        "/api/v1/plugins",
        json={"id": "not-wired", "name": "未接线能力", "type": "http", "data_level": "L1"},
    )
    assert created.status_code == 201
    rows = client.get("/api/v1/capabilities?actor_no=E10281").json()
    contract = next(row for row in rows if row["id"] == "not-wired")
    assert contract["ready"] is False
    assert contract["issues"]
