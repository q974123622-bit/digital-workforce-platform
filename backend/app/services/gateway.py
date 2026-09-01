"""Plugin Gateway — 唯一插件执行入口（Sprint 2）。

调用链：Employee Identity → Policy Engine → Plugin Gateway → Employee Harness
→ Adapter Tool → Result。
Gateway 不做授权决策（只转发评估结果），每次调用落一条审计。
"""

import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import config
from .capability_contract import plugin_contract
from .capability_executor import execute_capability
from .identity import resolve_identity
from .knowledge_adapter import select_adapter
from .knowledge_registry import plugin_id_for_level, resolve as resolve_knowledge_base
from .policy import DECISION_ALLOW, DECISION_APPROVAL, DECISION_DENY, ResourceRef, evaluate
from .runtime_adapter import HarnessExecutionContext, RuntimeAdapter


def write_audit(
    db: Session,
    *,
    trace_id: str,
    employee_id: str,
    plugin_id: str,
    action: str,
    decision: str,
    knowledge_base_id: str | None = None,
    reason: str | None = None,
    result_summary: str | None = None,
) -> int:
    event = models.AuditEvent(
        trace_id=trace_id or "NO-TRACE",
        actor=employee_id,
        employee_id=employee_id,
        plugin_id=plugin_id,
        knowledge_base_id=knowledge_base_id,
        action=action,
        decision=decision,
        reason=reason,
        result_summary=result_summary,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event.id


def _result_summary(data: object) -> str:
    """Internal retrieval audits contain metadata only, never query or hit content."""
    if isinstance(data, dict) and data.get("source") in {"internal", "mock", "demo", "rag", "volcengine_mcp"} and "knowledge_base_id" in data:
        hits = data.get("hits")
        return json.dumps(
            {
                "source": "mock" if data.get("source") == "demo" else data.get("source"),
                "knowledge_base_id": data.get("knowledge_base_id"),
                "hit_count": len(hits) if isinstance(hits, list) else 0,
            },
            ensure_ascii=False,
        )
    return json.dumps(data, ensure_ascii=False)[:200]


def invoke_plugin(
    db: Session,
    *,
    employee_id: str,
    plugin_id: str,
    action: str,
    params: dict,
    trace_id: str,
    knowledge_base_id: str | None = None,
    approval_granted: bool = False,
    runtime: RuntimeAdapter | None = None,
    execution_context: dict | None = None,
) -> dict:
    subject = resolve_identity(db, employee_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="数字员工不存在")
    plugin = db.get(models.Plugin, plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail="插件不存在")
    if plugin.status != "active":
        audit_id = write_audit(
            db,
            trace_id=trace_id,
            employee_id=employee_id,
            plugin_id=plugin_id,
            action=action,
            decision=DECISION_DENY,
            reason="插件已禁用，不能执行",
        )
        raise HTTPException(
            status_code=409,
            detail={"message": "插件已禁用，不能执行", "audit_id": audit_id},
        )
    contract = plugin_contract(plugin)
    if not contract.ready:
        audit_id = write_audit(
            db,
            trace_id=trace_id,
            employee_id=employee_id,
            plugin_id=plugin_id,
            action=action,
            decision=DECISION_DENY,
            reason="能力契约未就绪",
        )
        raise HTTPException(
            status_code=409,
            detail={"message": "能力契约未就绪", "issues": contract.issues, "audit_id": audit_id},
        )
    if action not in contract.actions:
        audit_id = write_audit(
            db,
            trace_id=trace_id,
            employee_id=employee_id,
            plugin_id=plugin_id,
            action=action,
            decision=DECISION_DENY,
            reason=f"能力契约不支持动作：{action}",
        )
        raise HTTPException(
            status_code=400,
            detail={"message": f"能力 {plugin_id} 不支持动作 {action}", "audit_id": audit_id},
        )

    resource = ResourceRef(type=plugin.type, id=plugin.id, data_level=plugin.data_level)
    result = evaluate(db, subject, resource, action)

    if result.decision == DECISION_DENY:
        reason = f"{result.policy_id}: {result.reason}" if result.policy_id else result.reason
        audit_id = write_audit(
            db,
            trace_id=trace_id,
            employee_id=employee_id,
            plugin_id=plugin_id,
            action=action,
            decision=DECISION_DENY,
            knowledge_base_id=knowledge_base_id,
            reason=reason,
        )
        raise HTTPException(
            status_code=403,
            detail={"message": "策略拒绝", "policy_id": result.policy_id, "reason": reason, "audit_id": audit_id},
        )

    if result.decision == DECISION_APPROVAL and not approval_granted:
        reason = f"{result.policy_id}: {result.reason}" if result.policy_id else result.reason
        audit_id = write_audit(
            db,
            trace_id=trace_id,
            employee_id=employee_id,
            plugin_id=plugin_id,
            action=action,
            decision=DECISION_APPROVAL,
            knowledge_base_id=knowledge_base_id,
            reason=reason,
        )
        return {
            "ok": False,
            "data": None,
            "decision": DECISION_APPROVAL,
            "audit_ids": [audit_id],
            "policy_id": result.policy_id,
            "execution_mode": "pending",
            "runtime_mode": "pending",
            "tool_name": plugin.name,
            "tool_type": plugin.type,
        }

    # ALLOW，或已由任务状态机完成一次性人工审批：Harness 规划后执行 Adapter 工具。
    # approval_granted 只能由服务端任务状态机传入，HTTP Gateway DTO 不暴露该字段。
    if knowledge_base_id is not None:
        kb = resolve_knowledge_base(db, knowledge_base_id)
        try:
            data = select_adapter(plugin, kb).search(
                employee_id=employee_id,
                knowledge_base_id=knowledge_base_id,
                query=str(params.get("query", "")),
                trace_id=trace_id,
            )
        except RuntimeError as exc:
            if config.kb_mode() != "internal":
                raise
            audit_id = write_audit(
                db, trace_id=trace_id, employee_id=employee_id, plugin_id=plugin_id,
                action=action, decision=DECISION_DENY, knowledge_base_id=knowledge_base_id,
                reason=str(exc), result_summary=json.dumps({
                    "source": "internal", "knowledge_base_id": knowledge_base_id,
                    "hit_count": 0, "status": "failed",
                }, ensure_ascii=False),
            )
            raise HTTPException(
                status_code=502,
                detail={"message": str(exc), "retryable": True, "audit_id": audit_id},
            ) from exc
        runtime_mode = "knowledge_adapter"
        tool_name = plugin.name
        tool_type = plugin.type
        runtime_summary = ""
        runtime_context_id = f"{employee_id}:{trace_id or 'knowledge'}"
    else:
        employee = db.get(models.DigitalEmployee, employee_id)
        context_data = execution_context or {}
        collaboration_summary = str(context_data.get("collaboration_summary") or "").strip()
        context = HarnessExecutionContext(
            task_id=str(context_data.get("task_id") or trace_id or "NO-TASK"),
            employee_id=employee_id,
            employee_name=employee.name if employee is not None else employee_id,
            role_prompt=subject.role_prompt or "按数字员工岗位职责执行任务",
            responsibility=(
                str(context_data.get("responsibility") or "").strip()
                or subject.role_prompt
                or f"负责{subject.department}相关任务"
            ),
            request=str(context_data.get("request") or ""),
            subtask=str(context_data.get("subtask") or ""),
            collaboration_summary=collaboration_summary,
            capability_id=plugin.id,
            capability_name=plugin.name,
        )
        execution = execute_capability(
            plugin,
            params,
            trace_id=trace_id,
            context=context,
            runtime=runtime,
        )
        data = execution.data
        runtime_mode = execution.runtime_mode
        tool_name = execution.tool_name
        tool_type = execution.tool_type
        runtime_summary = execution.runtime_summary
        runtime_context_id = execution.context_id
    summary = _result_summary(data)
    audit_id = write_audit(
        db,
        trace_id=trace_id,
        employee_id=employee_id,
        plugin_id=plugin_id,
        action=action,
        decision=DECISION_ALLOW,
        knowledge_base_id=knowledge_base_id,
        reason=(
            f"人工审批已通过：{result.reason}"
            if result.decision == DECISION_APPROVAL and approval_granted
            else (result.reason if result.policy_id else None)
        ),
        result_summary=summary,
    )
    return {
        "ok": True,
        "data": data,
        "decision": DECISION_ALLOW,
        "audit_ids": [audit_id],
        "policy_id": result.policy_id,
        "execution_mode": runtime_mode,
        "runtime_mode": runtime_mode,
        "runtime_context_id": runtime_context_id,
        "runtime_summary": runtime_summary,
        "tool_name": tool_name,
        "tool_type": tool_type,
    }


def search_knowledge(
    db: Session,
    *,
    employee_id: str,
    knowledge_base_id: str,
    query: str,
    trace_id: str,
    requester_human_no: str | None = None,
) -> dict:
    """知识库专用入口：仍统一经过 Policy → Gateway → Adapter 管线，不绕过。"""
    kb = resolve_knowledge_base(db, knowledge_base_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库资源不存在")
    if kb.status != "active":
        raise HTTPException(status_code=403, detail={"reason": "知识库当前未启用"})
    subject = resolve_identity(db, employee_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="数字员工不存在")
    if requester_human_no:
        requester = db.get(models.HumanEmployee, requester_human_no)
        if requester is None:
            raise HTTPException(status_code=403, detail={"reason": "请求用户身份不存在"})
        if kb.allowed_employment_type and requester.employment_type not in kb.allowed_employment_type:
            raise HTTPException(status_code=403, detail={"reason": "请求用户不在知识库允许的员工范围内"})
        if kb.department_scope and "*" not in kb.department_scope and requester.department not in kb.department_scope:
            raise HTTPException(status_code=403, detail={"reason": "请求用户不在知识库允许的部门范围内"})
    plugin_id = plugin_id_for_level(kb.data_level)
    # Preserve the platform Policy decision (and its audit) before applying the
    # more specific employee-to-knowledge-base grant.
    base_policy = evaluate(
        db, subject, ResourceRef(type="knowledge", id=plugin_id, data_level=kb.data_level), "read"
    )
    if base_policy.decision != DECISION_ALLOW:
        return invoke_plugin(
            db, employee_id=employee_id, plugin_id=plugin_id, action="read",
            params={"query": query, "knowledge_base_id": knowledge_base_id},
            trace_id=trace_id, knowledge_base_id=knowledge_base_id,
        )
    explicit_grants = db.scalars(
        select(models.AgentKnowledgeGrant).where(
            models.AgentKnowledgeGrant.employee_id == employee_id,
            models.AgentKnowledgeGrant.action == "read",
            models.AgentKnowledgeGrant.status == "active",
        )
    ).all()
    if knowledge_base_id not in {grant.knowledge_base_id for grant in explicit_grants}:
        audit_id = write_audit(
            db, trace_id=trace_id, employee_id=employee_id, plugin_id=plugin_id,
            action="read", decision=DECISION_DENY, knowledge_base_id=knowledge_base_id,
            reason="该数字员工未获授权访问此知识库",
        )
        raise HTTPException(
            status_code=403,
            detail={
                "reason": "该数字员工未获授权访问此知识库",
                "policy_id": "AGENT-KB-GRANT", "audit_id": audit_id,
            },
        )
    return invoke_plugin(
        db,
        employee_id=employee_id,
        plugin_id=plugin_id,
        action="read",
        params={"query": query, "knowledge_base_id": knowledge_base_id},
        trace_id=trace_id,
        knowledge_base_id=knowledge_base_id,
    )
