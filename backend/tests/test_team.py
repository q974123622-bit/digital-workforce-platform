"""Sprint 5 TeamTaskOrchestrator 测试（FakeLLM，不依赖真实 DeepSeek）。"""

from app import models

from app.services.llm import LLMProvider, LLMResponse, LLMUnavailableError
from app.services.runtime_adapter import RuntimeResult
from app.services.team_orchestrator import TeamTaskOrchestrator


class FakeLLM(LLMProvider):
    def __init__(self, summary="Leader 汇总：入职准备已完成。"):
        self.summary = summary
        self.fail = False

    def chat(self, messages, tools=None):
        if self.fail:
            raise LLMUnavailableError("LLM 不可用")
        return LLMResponse(content=self.summary)

    def tool_call(self, messages, tools):
        return self.chat(messages, tools)

    def structured_output(self, messages, schema):
        return {}


class FakeRuntime:
    def __init__(self, ok=True, text="Harness 执行摘要：已完成该子任务。"):
        self.ok = ok
        self.text = text

    def run(self, **kwargs):
        return RuntimeResult(mode="harness" if self.ok else "demo", ok=self.ok, result=self.text)


def _orchestrator(summary="Leader 汇总：入职准备已完成。", runtime=None):
    return TeamTaskOrchestrator(FakeLLM(summary=summary), runtime=runtime or FakeRuntime())


def _create(client, orch, request="帮王小明完成入职准备并确认权限"):
    return orch.create_task(client, team_id="TEAM-ONBOARD", request=request)


def test_create_task_reaches_approval(db_session):
    orch = _orchestrator()
    run = _create(db_session, orch)
    assert run.status == "approval"
    assert len(run.subtasks) == 3
    assert [s.status for s in run.subtasks] == ["completed", "completed", "approval"]
    assert run.subtasks[0].worker_no == "VE-0002"
    assert run.subtasks[1].worker_no == "VE-0003"
    assert run.subtasks[2].worker_no == "VE-0003"
    assert run.subtasks[2].approval is not None
    # P20：L3 审批已改为白名单；团队审批演示走 L2 插件的 approval grant（无内置策略 id）
    assert run.subtasks[2].approval.get("policy_id") is None
    assert run.subtasks[2].approval.get("reason")
    assert run.subtasks[2].runtime_mode == "pending"
    assert run.subtasks[2].tool_name == "员工查询 MCP"
    assert run.subtasks[2].tool_type == "mcp"
    assert run.trace_id == run.id


def test_approve_continues_and_completes(client, db_session):
    orch = _orchestrator(summary="已为王小明完成入职准备：制度确认、账号开通、权限报表已生成。")
    run = _create(db_session, orch)
    assert run.status == "approval"
    approved = orch.approve(db_session, task_id=run.id, approve=True, actor_no="E10281")
    assert approved.status == "completed"
    assert all(s.status == "completed" for s in approved.subtasks)
    assert approved.summary == "已为王小明完成入职准备：制度确认、账号开通、权限报表已生成。"
    assert "已批准执行（Mock 结果）" not in (approved.subtasks[2].result or "")
    assert "员工查询 MCP" in (approved.subtasks[2].result or "")
    decisions = [
        event.decision
        for event in db_session.query(models.AuditEvent)
        .filter_by(trace_id=run.trace_id, plugin_id="hr-employee-mcp")
        .order_by(models.AuditEvent.id)
        .all()
    ]
    assert decisions == ["allow", "approval", "allow"]


def test_conversation_task_rejects_unrelated_approver(client):
    resp = client.post(
        "/api/v1/tasks/T-20260819-DEMO1/approve",
        json={"approve": True, "actor_no": "E10021"},
    )
    assert resp.status_code == 403


def test_reject_marks_denied(client, db_session):
    orch = _orchestrator()
    run = _create(db_session, orch)
    denied = orch.approve(db_session, task_id=run.id, approve=False, actor_no="E10281")
    assert denied.status == "denied"
    assert denied.subtasks[2].status == "denied"


def test_approve_wrong_state_409(client, db_session):
    orch = _orchestrator()
    run = _create(db_session, orch)
    orch.approve(db_session, task_id=run.id, approve=True, actor_no="E10281")
    resp = client.post(
        f"/api/v1/tasks/{run.id}/approve",
        json={"approve": True, "actor_no": "E10281"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "STATE_CONFLICT"


def test_task_and_team_not_found(client, db_session):
    orch = _orchestrator()
    resp = client.post("/api/v1/teams/NOT-EXIST/tasks", json={"request": "x"})
    assert resp.status_code == 404
    assert client.get("/api/v1/teams/TEAM-ONBOARD/tasks/T-NOPE").status_code == 404


def test_audit_trace_through_task(client, db_session):
    orch = _orchestrator()
    run = _create(db_session, orch)
    orch.approve(db_session, task_id=run.id, approve=True, actor_no="E10281")
    events = client.get(f"/api/v1/audit", params={"trace_id": run.trace_id}).json()
    actions = [e["action"] for e in events]
    assert "create" in actions
    assert "approve" in actions
    assert "summarize" in actions
    plugins = {e["plugin_id"] for e in events}
    assert {"hr-employee-mcp", "adp-onboarding", "team:task", "team:approval", "team:summary"} <= plugins


def test_summary_fallback_when_llm_unavailable(client, db_session):
    orch = _orchestrator()
    orch.provider.fail = True
    run = _create(db_session, orch)
    assert run.status == "approval"
    completed = orch.approve(db_session, task_id=run.id, approve=True, actor_no="E10281")
    assert completed.status == "completed"
    assert "协作完成" in completed.summary


def test_harness_result_included_in_subtask(client, db_session):
    orch = _orchestrator(runtime=FakeRuntime(ok=True, text="已核对入职材料清单。"))
    run = _create(db_session, orch)
    assert run.subtasks[0].status == "completed"
    assert run.subtasks[0].runtime_mode == "harness"
    assert run.subtasks[1].execution_mode == "harness"
    assert run.subtasks[0].tool_name == "员工查询 MCP"
    assert run.subtasks[1].tool_name == "入职流程 Workflow"
    assert "已核对入职材料清单" in run.subtasks[1].runtime_summary


def test_harness_fallback_to_gateway(client, db_session):
    orch = _orchestrator(runtime=FakeRuntime(ok=False))
    run = _create(db_session, orch)
    assert run.subtasks[0].status == "completed"
    assert run.subtasks[0].runtime_mode == "demo_adapter"
    assert run.subtasks[1].runtime_mode == "demo_adapter"
    assert "流程：员工查询 MCP" in run.subtasks[0].result
    assert "王老师" in run.subtasks[0].result


def test_docker_harness_requires_key():
    import os

    from app.services.runtime_adapter import DockerHarnessRuntimeAdapter

    old = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        r = DockerHarnessRuntimeAdapter().run(employee_id="X", task_prompt="t", trace_id="t")
        assert r.mode == "demo"
        assert not r.ok
    finally:
        if old is not None:
            os.environ["DEEPSEEK_API_KEY"] = old
