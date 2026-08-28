"""Plugin Gateway — 唯一插件执行入口（Sprint 2）。

调用链：Employee Identity → Policy Engine → Plugin Gateway → Employee Harness
→ Adapter Tool → Result。
Gateway 不做授权决策（只转发评估结果），每次调用落一条审计。
"""

import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models
from . import config
from .capability_contract import plugin_contract
from .capability_executor import execute_capability
from .identity import resolve_identity
from .knowledge_adapter import select_adapter
from .knowledge_registry import plugin_id_for_level, resolve as resolve_knowledge_base
from .memory_service import render_prompt_context, retrieve_for_prompt
from .policy import DECISION_ALLOW, DECISION_APPROVAL, DECISION_DENY, ResourceRef, evaluate
from .runtime_adapter import HarnessExecutionContext, RuntimeAdapter

# 数据级别排序：用于发送前过滤（hit.data_level 超过员工 max_data_level 不发送）。
_DATA_LEVEL_RANK = {"L1": 1, "L2": 2, "L3": 3}


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


def _invoke_memory_search(
    db: Session,
    *,
    subject,
    plugin,
    params: dict,
    trace_id: str,
    execution_context: dict | None,
) -> dict:
    """memory.search 专用执行分支（Round 2 B1）。

    与通用执行器互斥：owner / current_session_id 由 Gateway 注入，不接受模型参数；
    检索结果按 ``subject.max_data_level`` 过滤后渲染，审计只记 hits/ids/chars，不记正文。
    """
    query = params.get("query")
    if not isinstance(query, str) or not query.strip():
        return {
            "ok": False,
            "data": None,
            "decision": "parameter_error",
            "error": "query_required",
        }

    limit_raw = params.get("limit", 3)
    if isinstance(limit_raw, bool) or not isinstance(limit_raw, int):
        return {
            "ok": False,
            "data": None,
            "decision": "parameter_error",
            "error": "limit_invalid",
        }
    if limit_raw <= 0:
        return {
            "ok": False,
            "data": None,
            "decision": "parameter_error",
            "error": "limit_invalid",
        }
    limit = min(limit_raw, 10)

    owner_employee_no = subject.employee_id
    current_session_id = str((execution_context or {}).get("current_session_id") or "")

    try:
        hits = retrieve_for_prompt(
            db,
            owner_employee_no=owner_employee_no,
            query=query,
            current_session_id=current_session_id,
            limit=limit,
            max_chars=config.memory_max_chars(),
        )
        max_rank = _DATA_LEVEL_RANK.get(subject.max_data_level, 0)
        hits = [
            hit
            for hit in hits
            if _DATA_LEVEL_RANK.get(hit.data_level, 0) <= max_rank
        ]
        text = render_prompt_context(hits, max_chars=config.memory_max_chars())
    except Exception as exc:  # noqa: BLE001 —— 降级为 error 态，不阻断聊天
        audit_id = write_audit(
            db,
            trace_id=trace_id,
            employee_id=owner_employee_no,
            plugin_id=plugin.id,
            action="search",
            decision="error",
            reason=f"memory.search 异常：{type(exc).__name__}",
            result_summary=f"error={type(exc).__name__}",
        )
        return {
            "ok": False,
            "data": None,
            "decision": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "audit_ids": [audit_id],
        }

    result_summary = f"hits={len(hits)} ids={[h.memory_id for h in hits]} chars={len(text)}"
    audit_id = write_audit(
        db,
        trace_id=trace_id,
        employee_id=owner_employee_no,
        plugin_id=plugin.id,
        action="search",
        decision="allow",
        result_summary=result_summary,
    )
    return {
        "ok": True,
        "data": {
            "text": text,
            "hits": [
                {
                    "memory_id": h.memory_id,
                    "kind": h.kind,
                    "data_level": h.data_level,
                    "score": h.score,
                }
                for h in hits
            ],
            "memory_ids": [h.memory_id for h in hits],
        },
        "decision": "allow" if hits else "empty",
        "audit_ids": [audit_id],
    }


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

    # memory 是平台保留能力：契约已锁定 search + adapter 执行器，
    # 由 Gateway 专用分支执行（owner/会话服务端注入、数据级别过滤、元数据审计），不进通用执行器。
    if plugin.type == "memory":
        return _invoke_memory_search(
            db,
            subject=subject,
            plugin=plugin,
            params=params,
            trace_id=trace_id,
            execution_context=execution_context,
        )

    # ALLOW，或已由任务状态机完成一次性人工审批：Harness 规划后执行 Adapter 工具。
    # approval_granted 只能由服务端任务状态机传入，HTTP Gateway DTO 不暴露该字段。
    if knowledge_base_id is not None:
        kb = resolve_knowledge_base(db, knowledge_base_id)
        data = select_adapter(plugin, kb).search(
            employee_id=employee_id,
            knowledge_base_id=knowledge_base_id,
            query=str(params.get("query", "")),
            trace_id=trace_id,
        )
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
    summary = json.dumps(data, ensure_ascii=False)[:200]
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
) -> dict:
    """知识库专用入口：仍统一经过 Policy → Gateway → Adapter 管线，不绕过。"""
    kb = resolve_knowledge_base(db, knowledge_base_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库资源不存在")
    plugin_id = plugin_id_for_level(kb.data_level)
    return invoke_plugin(
        db,
        employee_id=employee_id,
        plugin_id=plugin_id,
        action="read",
        params={"query": query, "knowledge_base_id": knowledge_base_id},
        trace_id=trace_id,
        knowledge_base_id=knowledge_base_id,
    )


def search_memory(
    db: Session,
    *,
    employee_id: str,
    query: str,
    current_session_id: str | None,
    trace_id: str,
    limit: int = 3,
) -> dict:
    """记忆检索专用入口：统一经 Plugin Gateway 的 memory 分支执行，不绕过 Policy / 审计。

    owner_employee_no 恒为当前数字员工，不接受模型/前端参数；current_session_id 由编排层注入。
    """
    return invoke_plugin(
        db,
        employee_id=employee_id,
        plugin_id="agent-memory",
        action="search",
        params={"query": query, "limit": limit},
        trace_id=trace_id,
        execution_context={"current_session_id": current_session_id},
    )
