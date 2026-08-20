"""Workflow 插件测试：递归保护、Formal/Intern 差异、Approval、Chat dispatch。"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import models
from app.services import gateway
from app.services import workflow_engine as we
from app.services.chat import ChatOrchestrator
from app.services.llm import LLMProvider, LLMResponse, ToolCall


class FakeLLM(LLMProvider):
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        return self.script.pop(0)

    def tool_call(self, messages, tools):
        return self.chat(messages, tools)

    def structured_output(self, messages, schema):
        return {}


def _run(db, employee_id, plugin_id, params, trace_id):
    return gateway.invoke_plugin(
        db,
        employee_id=employee_id,
        plugin_id=plugin_id,
        action="execute",
        params=params,
        trace_id=trace_id,
    )


def _audits(db, trace_id):
    return db.query(models.AuditEvent).filter(models.AuditEvent.trace_id == trace_id).order_by(models.AuditEvent.id).all()


# ---- regulation-compare-workflow：Formal / Intern ----


def test_regulation_compare_formal_success(db_session):
    result = _run(db_session, "DT-E10281", "regulation-compare-workflow", {"query": "反洗钱"}, "T-WF-REG-FORMAL")
    assert result["ok"] is True
    data = result["data"]
    assert data["status"] == "success"
    assert {s["step_id"] for s in data["steps"]} == {"external", "internal"}
    assert all(s["decision"] == "allow" for s in data["steps"])
    assert data["data"]["external_result"] is not None
    assert data["data"]["internal_result"] is not None
    plugin_ids = {a.plugin_id for a in _audits(db_session, "T-WF-REG-FORMAL")}
    assert "regulation-compare-workflow" in plugin_ids
    assert "knowledge-l1" in plugin_ids
    assert "knowledge-l2" in plugin_ids


def test_regulation_compare_intern_partial(db_session):
    result = _run(db_session, "DT-E20999", "regulation-compare-workflow", {"query": "反洗钱"}, "T-WF-REG-INTERN")
    data = result["data"]
    assert data["status"] == "partial"
    by_step = {s["step_id"]: s for s in data["steps"]}
    assert by_step["external"]["decision"] == "allow"
    assert by_step["internal"]["decision"] == "deny"
    assert data["data"]["external_result"] is not None
    assert data["data"]["internal_result"] is None


# ---- document-compliance-workflow ----


def test_document_compliance_formal_success(db_session):
    result = _run(db_session, "DT-E10281", "document-compliance-workflow", {"document_name": "normal-document.md", "query": "合规"}, "T-WF-DOC-FORMAL")
    data = result["data"]
    assert data["status"] == "success"
    assert len(data["steps"]) == 3
    assert data["data"]["document"] is not None
    assert data["data"]["external_regulations"] is not None
    assert data["data"]["internal_regulations"] is not None


def test_document_compliance_stops_on_document_deny():
    def deny_child(**kwargs):
        raise HTTPException(status_code=403, detail={"message": "策略拒绝", "policy_id": "X", "reason": "deny", "audit_id": 1})

    ctx = we.WorkflowExecutionContext(employee_id="DT-E20999", trace_id="T-WF-DOC-DENY", invoke_child=deny_child, search_knowledge_child=None)
    plugin = SimpleNamespace(id="document-compliance-workflow", endpoint_ref="workflow://document/compliance")
    result = we.run_workflow(plugin, {"document_name": "normal-document.md", "query": "x"}, ctx)
    assert result["status"] == "denied"
    assert len(result["steps"]) == 1
    assert result["steps"][0]["step_id"] == "read_document"


# ---- it-support-workflow ----


def test_it_support_no_escalate(db_session):
    result = _run(db_session, "DT-E10281", "it-support-workflow", {"question": "VPN怎么连？"}, "T-WF-IT-SIMPLE")
    data = result["data"]
    assert data["status"] == "success"
    assert len(data["steps"]) == 1
    assert data["steps"][0]["step_id"] == "kb"


def test_it_support_escalate_intern_partial(db_session):
    result = _run(db_session, "DT-E20999", "it-support-workflow", {"question": "VPN怎么连？", "escalate": True}, "T-WF-IT-ESC")
    data = result["data"]
    assert data["status"] == "partial"
    by_step = {s["step_id"]: s for s in data["steps"]}
    assert by_step["kb"]["decision"] == "allow"
    assert by_step["collaborate"]["decision"] == "deny"


# ---- employee-assist-workflow ----


def test_employee_assist_success(db_session):
    result = _run(db_session, "DT-E10281", "employee-assist-workflow", {"keyword": "HR", "request": "入职制度咨询"}, "T-WF-ASSIST-OK")
    data = result["data"]
    assert data["status"] == "success"
    assert data["data"]["target_employee"] in ("VE-0002",)
    assert data["data"]["collaboration_result"] is not None


def test_employee_assist_not_found(db_session):
    result = _run(db_session, "DT-E10281", "employee-assist-workflow", {"keyword": "ZZZ", "request": "x"}, "T-WF-ASSIST-NF")
    assert result["data"]["status"] == "partial"
    assert result["data"].get("reason") == "not_found"


# ---- 递归保护 ----


def test_workflow_cycle_blocked(monkeypatch):
    plugin_a = SimpleNamespace(id="wf-a", endpoint_ref="workflow://test/a")
    plugin_b = SimpleNamespace(id="wf-b", endpoint_ref="workflow://test/b")

    def handler_a(ctx, params):
        return we.run_workflow(plugin_b, params, ctx.child_context(plugin_b.endpoint_ref))

    def handler_b(ctx, params):
        return we.run_workflow(plugin_a, params, ctx.child_context(plugin_a.endpoint_ref))

    monkeypatch.setitem(we.WORKFLOW_REGISTRY, "workflow://test/a", handler_a)
    monkeypatch.setitem(we.WORKFLOW_REGISTRY, "workflow://test/b", handler_b)
    top = we.WorkflowExecutionContext(employee_id="DT-E10281", trace_id="T-WF-CYCLE")
    result = we.run_workflow(plugin_a, {}, top)
    assert result["status"] == "blocked"
    assert result["reason"] == "workflow_cycle_detected"


def test_workflow_cycle_blocked_via_gateway(db_session, monkeypatch):
    """经真实 Gateway 的 Workflow→Workflow 嵌套，递归保护沿链生效而非重置。"""
    db_session.add(models.Plugin(
        id="wf-a", name="wf-a", type="workflow",
        endpoint_ref="workflow://test/a", data_level="L1",
        status="active", description="",
    ))
    db_session.add(models.Plugin(
        id="wf-b", name="wf-b", type="workflow",
        endpoint_ref="workflow://test/b", data_level="L1",
        status="active", description="",
    ))
    db_session.add(models.EmployeePluginGrant(employee_id="DT-E10281", plugin_id="wf-a", action="execute", decision_mode="allow"))
    db_session.add(models.EmployeePluginGrant(employee_id="DT-E10281", plugin_id="wf-b", action="execute", decision_mode="allow"))
    db_session.commit()

    def handler_a(ctx, params):
        return we.invoke_plugin_step(ctx, "b", "wf-b", "execute", {})

    def handler_b(ctx, params):
        return we.invoke_plugin_step(ctx, "a", "wf-a", "execute", {})

    monkeypatch.setitem(we.WORKFLOW_REGISTRY, "workflow://test/a", handler_a)
    monkeypatch.setitem(we.WORKFLOW_REGISTRY, "workflow://test/b", handler_b)

    result = gateway.invoke_plugin(
        db_session,
        employee_id="DT-E10281",
        plugin_id="wf-a",
        action="execute",
        params={},
        trace_id="T-WF-CYCLE-GW",
    )

    step_b = result["data"]
    assert step_b["plugin_id"] == "wf-b"
    step_a = step_b["data"]
    assert step_a["plugin_id"] == "wf-a"
    blocked = step_a["data"]
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "workflow_cycle_detected"


def test_workflow_depth_blocked():
    plugin = SimpleNamespace(id="wf-depth", endpoint_ref="workflow://test/depth")
    ctx = we.WorkflowExecutionContext(employee_id="DT-E10281", trace_id="T-WF-DEPTH", depth=we.MAX_WORKFLOW_DEPTH + 1)
    result = we.run_workflow(plugin, {}, ctx)
    assert result["status"] == "blocked"
    assert result["reason"] == "workflow_depth_exceeded"


# ---- report-export-workflow：Approval ----


def test_report_export_approval_required(db_session):
    result = _run(db_session, "VE-0003", "report-export-workflow", {}, "T-WF-REPORT-APR")
    data = result["data"]
    assert data["status"] == "approval_required"
    by_step = {s["step_id"]: s for s in data["steps"]}
    assert by_step["work_records"]["decision"] == "allow"
    assert by_step["rpa_report"]["decision"] == "approval"
    assert data["data"]["report"] is None
    audit_decisions = {a.plugin_id: a.decision for a in _audits(db_session, "T-WF-REPORT-APR")}
    assert audit_decisions["rpa-report"] == "approval"
    assert audit_decisions["report-export-workflow"] == "allow"


# ---- ChatOrchestrator dispatch 进入 Workflow ----


def test_chat_dispatch_compare_regulations(db_session):
    script = [
        LLMResponse(content="", tool_calls=[ToolCall(id="tc-1", name="compare_regulations", arguments={"query": "反洗钱"})]),
        LLMResponse(content="对比完成"),
    ]
    orchestrator = ChatOrchestrator(FakeLLM(script))
    result = orchestrator.handle_message(db_session, employee_no="DT-E10281", message="对比监管", session_id=None)
    assert result.tool_cards and result.tool_cards[0].decision == "allow"
    assert result.tool_cards[0].plugin_id == "regulation-compare-workflow"


def test_chat_dispatch_handle_it_support(db_session):
    script = [
        LLMResponse(content="", tool_calls=[ToolCall(id="tc-2", name="handle_it_support", arguments={"question": "VPN"})]),
        LLMResponse(content="IT 支持完成"),
    ]
    orchestrator = ChatOrchestrator(FakeLLM(script))
    result = orchestrator.handle_message(db_session, employee_no="DT-E10281", message="IT问题", session_id=None)
    assert result.tool_cards and result.tool_cards[0].plugin_id == "it-support-workflow"


def test_chat_dispatch_assist_with_employee(db_session):
    script = [
        LLMResponse(content="", tool_calls=[ToolCall(id="tc-3", name="assist_with_employee", arguments={"request": "咨询制度"})]),
        LLMResponse(content="协助完成"),
    ]
    orchestrator = ChatOrchestrator(FakeLLM(script))
    result = orchestrator.handle_message(db_session, employee_no="DT-E10281", message="协助", session_id=None)
    assert result.tool_cards and result.tool_cards[0].plugin_id == "employee-assist-workflow"
