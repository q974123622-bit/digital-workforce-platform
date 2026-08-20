"""Workflow 编排引擎（本轮新增）。

Workflow Plugin 负责编排多个原子 Plugin / 知识查询；所有子调用必须重新经过
Plugin Gateway（Policy + Audit），Workflow 不能直接调用 Adapter。

递归保护（Runtime 级，不新增数据库表）：
- MAX_WORKFLOW_DEPTH：超过深度返回 blocked（workflow_depth_exceeded）
- visited_workflows：同一 Workflow 链中重复出现返回 blocked（workflow_cycle_detected）

Workflow 只返回统一结构化结果，最终自然语言总结由 Skill + LLM 完成。
"""

from fastapi import HTTPException

MAX_WORKFLOW_DEPTH = 4


class WorkflowExecutionContext:
    """一次 Workflow 调用的执行上下文；子调用能力由 Gateway 注入，避免循环依赖。"""

    def __init__(
        self,
        *,
        employee_id: str,
        trace_id: str,
        depth: int = 0,
        visited_workflows: tuple = (),
        invoke_child=None,
        search_knowledge_child=None,
    ):
        self.employee_id = employee_id
        self.trace_id = trace_id
        self.depth = depth
        self.visited_workflows = visited_workflows
        self.invoke_child = invoke_child
        self.search_knowledge_child = search_knowledge_child

    def child_context(self, endpoint: str) -> "WorkflowExecutionContext":
        return WorkflowExecutionContext(
            employee_id=self.employee_id,
            trace_id=self.trace_id,
            depth=self.depth + 1,
            visited_workflows=self.visited_workflows + (endpoint,),
            invoke_child=self.invoke_child,
            search_knowledge_child=self.search_knowledge_child,
        )


def _blocked_result(plugin, ctx: WorkflowExecutionContext, reason: str) -> dict:
    return {
        "source": "demo-workflow",
        "workflow_id": plugin.id,
        "status": "blocked",
        "steps": [],
        "data": {},
        "trace_id": ctx.trace_id,
        "reason": reason,
    }


def workflow_result(ctx: WorkflowExecutionContext, workflow_id: str, status: str, steps: list, data: dict, reason: str | None = None) -> dict:
    result = {
        "source": "demo-workflow",
        "workflow_id": workflow_id,
        "status": status,
        "steps": steps,
        "data": data,
        "trace_id": ctx.trace_id,
    }
    if reason is not None:
        result["reason"] = reason
    return result


def _run_step(ctx: WorkflowExecutionContext, step_id: str, plugin_id: str, call) -> dict:
    """执行一个子调用（真实回调最终走 gateway.invoke_plugin / search_knowledge）。"""
    try:
        result = call()
        decision = result.get("decision", "allow")
        return {
            "step_id": step_id,
            "plugin_id": plugin_id,
            "status": decision,
            "decision": decision,
            "audit_ids": result.get("audit_ids", []),
            "policy_id": result.get("policy_id"),
            "data": result.get("data"),
        }
    except HTTPException as exc:
        if exc.status_code == 403:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return {
                "step_id": step_id,
                "plugin_id": plugin_id,
                "status": "denied",
                "decision": "deny",
                "audit_ids": [detail["audit_id"]] if detail.get("audit_id") else [],
                "policy_id": detail.get("policy_id"),
                "reason": detail.get("reason"),
                "data": None,
            }
        raise


def invoke_plugin_step(ctx: WorkflowExecutionContext, step_id: str, plugin_id: str, action: str, params: dict, workflow_ctx: WorkflowExecutionContext | None = None) -> dict:
    """Workflow 内调用一个子 Plugin（必须再次经过 Gateway）。"""
    child_ctx = workflow_ctx if workflow_ctx is not None else ctx
    return _run_step(
        ctx,
        step_id,
        plugin_id,
        lambda: ctx.invoke_child(
            employee_id=ctx.employee_id,
            plugin_id=plugin_id,
            action=action,
            params=params,
            trace_id=ctx.trace_id,
            workflow_ctx=child_ctx,
        ),
    )


def search_knowledge_step(ctx: WorkflowExecutionContext, step_id: str, kb_id: str, query: str) -> dict:
    """Workflow 内查询知识库（必须再次经过 Gateway，自然继承 mock / rag 模式）。"""
    return _run_step(
        ctx,
        step_id,
        f"knowledge:{kb_id}",
        lambda: ctx.search_knowledge_child(
            employee_id=ctx.employee_id,
            knowledge_base_id=kb_id,
            query=query,
            trace_id=ctx.trace_id,
        ),
    )


def _aggregate_status(steps: list, critical: tuple = ()) -> str:
    """统一状态：error > approval_required > denied(关键) > partial > success。"""
    statuses = [s["status"] for s in steps]
    if any(st == "error" for st in statuses):
        return "error"
    if any(st == "approval" for st in statuses):
        return "approval_required"
    if any(s["status"] == "denied" and s["step_id"] in critical for s in steps):
        return "denied"
    if any(st == "denied" for st in statuses):
        return "partial"
    return "success"


# ---- Workflow Handlers ----


def _regulation_compare(ctx: WorkflowExecutionContext, params: dict) -> dict:
    query = str(params.get("query", ""))
    external = search_knowledge_step(ctx, "external", "KB-REG-EXTERNAL", query)
    internal = search_knowledge_step(ctx, "internal", "KB-REG-INTERNAL", query)
    steps = [external, internal]
    data = {
        "external_result": external.get("data") if external["status"] == "allow" else None,
        "internal_result": internal.get("data") if internal["status"] == "allow" else None,
    }
    return workflow_result(ctx, "regulation-compare-workflow", _aggregate_status(steps), steps, data)


def _document_compliance(ctx: WorkflowExecutionContext, params: dict) -> dict:
    document_name = str(params.get("document_name", ""))
    query = str(params.get("query", ""))
    steps: list[dict] = []
    doc = invoke_plugin_step(ctx, "read_document", "document-read", "read", {"document_name": document_name})
    steps.append(doc)
    if doc["status"] in ("denied", "approval", "error"):
        return workflow_result(ctx, "document-compliance-workflow", doc["status"], steps, {}, reason=f"document_step_{doc['status']}")
    external = search_knowledge_step(ctx, "external_regulations", "KB-REG-EXTERNAL", query)
    steps.append(external)
    internal = search_knowledge_step(ctx, "internal_regulations", "KB-REG-INTERNAL", query)
    steps.append(internal)
    data = {
        "document": doc.get("data"),
        "external_regulations": external.get("data") if external["status"] == "allow" else None,
        "internal_regulations": internal.get("data") if internal["status"] == "allow" else None,
    }
    return workflow_result(ctx, "document-compliance-workflow", _aggregate_status(steps, critical=("read_document",)), steps, data)


def _it_support(ctx: WorkflowExecutionContext, params: dict) -> dict:
    question = str(params.get("question", ""))
    escalate = bool(params.get("escalate"))
    steps: list[dict] = []
    kb = search_knowledge_step(ctx, "kb", "KB-IT-SERVICE", question)
    steps.append(kb)
    data: dict = {"kb_result": kb.get("data") if kb["status"] == "allow" else None}
    if kb["status"] != "allow":
        return workflow_result(ctx, "it-support-workflow", _aggregate_status(steps), steps, data)
    if not escalate:
        return workflow_result(ctx, "it-support-workflow", "success", steps, data)
    target = str(params.get("target_employee_id") or "").strip()
    if not target:
        search = invoke_plugin_step(ctx, "employee_search", "employee-search", "read", {"department": "IT 服务部", "digital_only": True})
        steps.append(search)
        if search["status"] != "allow":
            return workflow_result(ctx, "it-support-workflow", _aggregate_status(steps), steps, data, reason="employee_search_failed")
        employees = (search.get("data") or {}).get("employees") or []
        available = [e for e in employees if e.get("status") == "active"]
        if not available:
            return workflow_result(ctx, "it-support-workflow", "partial", steps, data, reason="no_available_employee")
        target = available[0]["employee_no"]
    collab = invoke_plugin_step(ctx, "collaborate", "employee-collaboration", "execute", {"target_employee_id": target, "action": "ask", "request": question})
    steps.append(collab)
    data["collaboration_result"] = collab.get("data")
    return workflow_result(ctx, "it-support-workflow", _aggregate_status(steps), steps, data)


def _employee_assist(ctx: WorkflowExecutionContext, params: dict) -> dict:
    request = str(params.get("request", ""))
    target = str(params.get("target_employee_id") or "").strip()
    steps: list[dict] = []
    search_params: dict = {"digital_only": True}
    if target:
        search_params["keyword"] = target
    else:
        for key in ("keyword", "department", "type"):
            if params.get(key):
                search_params[key] = params[key]
    search = invoke_plugin_step(ctx, "search_employee", "employee-search", "read", search_params)
    steps.append(search)
    if search["status"] != "allow":
        return workflow_result(ctx, "employee-assist-workflow", _aggregate_status(steps), steps, {})
    employees = (search.get("data") or {}).get("employees") or []
    digital = [e for e in employees if e.get("type") in ("twin", "virtual", "rpa")]
    if not digital:
        return workflow_result(ctx, "employee-assist-workflow", "partial", steps, {}, reason="not_found")
    chosen = target if target else digital[0]["employee_no"]
    collab = invoke_plugin_step(ctx, "collaborate", "employee-collaboration", "execute", {"target_employee_id": chosen, "action": "ask", "request": request})
    steps.append(collab)
    data = {"target_employee": chosen, "collaboration_result": collab.get("data")}
    return workflow_result(ctx, "employee-assist-workflow", _aggregate_status(steps), steps, data)


def _report_export(ctx: WorkflowExecutionContext, params: dict) -> dict:
    steps: list[dict] = []
    work = invoke_plugin_step(ctx, "work_records", "work-record-query", "read", {"employee_id": ctx.employee_id})
    steps.append(work)
    if work["status"] != "allow":
        return workflow_result(ctx, "report-export-workflow", _aggregate_status(steps), steps, {})
    report = invoke_plugin_step(ctx, "rpa_report", "rpa-report", "execute", {})
    steps.append(report)
    data = {
        "work_records": work.get("data"),
        "report": report.get("data") if report["status"] == "allow" else None,
    }
    return workflow_result(ctx, "report-export-workflow", _aggregate_status(steps), steps, data)


def _policy_change_impact(ctx: WorkflowExecutionContext, params: dict) -> dict:
    """制度/监管变更影响分析：只收集结构化证据，不做正式合规结论。"""
    document_name = str(params.get("document_name", ""))
    query = str(params.get("query", ""))
    collaborate = bool(params.get("collaborate", True))
    target_employee_id = str(params.get("target_employee_id") or "").strip()
    employee_keyword = str(params.get("employee_keyword") or "").strip()
    department = str(params.get("department") or "").strip()

    steps: list[dict] = []

    # Step 1：读取变更材料（关键步骤，Deny/异常直接终止，不继续假装分析）
    doc = invoke_plugin_step(ctx, "read_document", "document-read", "read", {"document_name": document_name})
    steps.append(doc)
    if doc["status"] == "denied":
        return workflow_result(ctx, "policy-change-impact-workflow", "denied", steps, {"document": None}, reason="document_step_denied")
    if doc["status"] == "approval":
        return workflow_result(ctx, "policy-change-impact-workflow", "approval_required", steps, {"document": None}, reason="document_step_approval")
    if doc["status"] == "error":
        return workflow_result(ctx, "policy-change-impact-workflow", "error", steps, {"document": None}, reason="document_step_error")

    doc_data = doc.get("data") or {}
    doc_inner = doc_data.get("status") if isinstance(doc_data, dict) else None
    if doc_inner == "error":
        return workflow_result(ctx, "policy-change-impact-workflow", "error", steps, {"document": doc_data}, reason="document_error")
    if doc_inner in ("not_found", "empty"):
        return workflow_result(ctx, "policy-change-impact-workflow", "partial", steps, {"document": doc_data}, reason=f"document_{doc_inner}")

    data: dict = {"document": doc_data}

    # Step 2：外部监管（L1，Formal/Intern 均可读）
    external = search_knowledge_step(ctx, "external_regulation", "KB-REG-EXTERNAL", f"{query} 外部监管要求")
    steps.append(external)
    data["external_regulation"] = external.get("data") if external["status"] == "allow" else None

    # Step 3：内部制度（L2，Intern 预期 Deny）
    internal = search_knowledge_step(ctx, "internal_policy", "KB-REG-INTERNAL", f"{query} 内部制度要求")
    steps.append(internal)
    data["internal_policy"] = internal.get("data") if internal["status"] == "allow" else None

    # Step 4：证券业务影响（L2，Intern 预期 Deny）
    securities = search_knowledge_step(ctx, "securities_impact", "KB-SECURITIES", f"{query} 证券业务影响")
    steps.append(securities)
    data["securities_impact"] = securities.get("data") if securities["status"] == "allow" else None

    # Step 5 + 6：可选员工搜索与协作
    if collaborate:
        search_params: dict = {"digital_only": True}
        if target_employee_id:
            search_params["keyword"] = target_employee_id
        else:
            if employee_keyword:
                search_params["keyword"] = employee_keyword
            if department:
                search_params["department"] = department
        search = invoke_plugin_step(ctx, "employee_search", "employee-search", "read", search_params)
        steps.append(search)
        employees = (search.get("data") or {}).get("employees") or []
        digital = [e for e in employees if e.get("type") in ("twin", "virtual", "rpa")]
        if target_employee_id:
            affected = next((e for e in digital if e.get("employee_no") == target_employee_id), None)
        else:
            affected = digital[0] if digital else None
        data["affected_employee"] = affected
        if affected is None:
            data["collaboration_result"] = None
            return workflow_result(ctx, "policy-change-impact-workflow", "partial", steps, data, reason="employee_not_found")
        collab = invoke_plugin_step(ctx, "collaborate", "employee-collaboration", "execute", {
            "target_employee_id": affected.get("employee_no"),
            "action": "ask",
            "request": f"请根据监管变更主题「{query}」，补充你认为需要关注的业务影响点。",
        })
        steps.append(collab)
        data["collaboration_result"] = collab.get("data") if collab["status"] == "allow" else None
    else:
        data["affected_employee"] = None
        data["collaboration_result"] = None

    return workflow_result(ctx, "policy-change-impact-workflow", _aggregate_status(steps), steps, data)


WORKFLOW_REGISTRY: dict[str, callable] = {
    "workflow://regulation/compare": _regulation_compare,
    "workflow://document/compliance": _document_compliance,
    "workflow://it/support": _it_support,
    "workflow://employee/assist": _employee_assist,
    "workflow://report/export": _report_export,
    "workflow://policy/change-impact": _policy_change_impact,
}


def run_workflow(plugin, params: dict, ctx: WorkflowExecutionContext) -> dict:
    """Gateway 调用入口：递归保护 + 分发到注册 Handler。"""
    endpoint = plugin.endpoint_ref
    if ctx.depth > MAX_WORKFLOW_DEPTH:
        return _blocked_result(plugin, ctx, "workflow_depth_exceeded")
    if endpoint in ctx.visited_workflows:
        return _blocked_result(plugin, ctx, "workflow_cycle_detected")
    handler = WORKFLOW_REGISTRY.get(endpoint)
    if handler is None:
        return {
            "source": "demo-workflow",
            "workflow_id": plugin.id,
            "status": "error",
            "steps": [],
            "data": {},
            "trace_id": ctx.trace_id,
            "reason": f"未注册的 Workflow：{endpoint}",
        }
    return handler(ctx.child_context(endpoint), params)
