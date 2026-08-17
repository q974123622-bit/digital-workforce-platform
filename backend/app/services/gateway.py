"""Plugin Gateway — 唯一插件执行入口（Sprint 2）。

调用链：Employee Identity → Policy Engine → Plugin Gateway → Adapter → Result。
Gateway 不做授权决策（只转发评估结果），每次调用落一条审计。
"""

import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models
from .adapters import run_adapter
from .identity import resolve_identity
from .policy import DECISION_ALLOW, DECISION_APPROVAL, DECISION_DENY, ResourceRef, evaluate


def _write_audit(
    db: Session,
    *,
    trace_id: str,
    employee_id: str,
    plugin_id: str,
    action: str,
    decision: str,
    reason: str | None = None,
    result_summary: str | None = None,
) -> int:
    event = models.AuditEvent(
        trace_id=trace_id or "NO-TRACE",
        actor=employee_id,
        employee_id=employee_id,
        plugin_id=plugin_id,
        action=action,
        decision=decision,
        reason=reason,
        result_summary=result_summary,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event.id


def invoke_plugin(
    db: Session,
    *,
    employee_id: str,
    plugin_id: str,
    action: str,
    params: dict,
    trace_id: str,
) -> dict:
    subject = resolve_identity(db, employee_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="数字员工不存在")
    plugin = db.get(models.Plugin, plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail="插件不存在")

    resource = ResourceRef(type=plugin.type, id=plugin.id, data_level=plugin.data_level)
    result = evaluate(db, subject, resource, action)

    if result.decision == DECISION_DENY:
        reason = f"{result.policy_id}: {result.reason}" if result.policy_id else result.reason
        audit_id = _write_audit(
            db,
            trace_id=trace_id,
            employee_id=employee_id,
            plugin_id=plugin_id,
            action=action,
            decision=DECISION_DENY,
            reason=reason,
        )
        raise HTTPException(
            status_code=403,
            detail={"message": "策略拒绝", "policy_id": result.policy_id, "reason": reason, "audit_id": audit_id},
        )

    if result.decision == DECISION_APPROVAL:
        reason = f"{result.policy_id}: {result.reason}" if result.policy_id else result.reason
        audit_id = _write_audit(
            db,
            trace_id=trace_id,
            employee_id=employee_id,
            plugin_id=plugin_id,
            action=action,
            decision=DECISION_APPROVAL,
            reason=reason,
        )
        return {"ok": False, "data": None, "decision": DECISION_APPROVAL, "audit_ids": [audit_id], "policy_id": result.policy_id}

    # ALLOW：执行 Mock Adapter
    data = run_adapter(plugin, params)
    summary = json.dumps(data, ensure_ascii=False)[:200]
    audit_id = _write_audit(
        db,
        trace_id=trace_id,
        employee_id=employee_id,
        plugin_id=plugin_id,
        action=action,
        decision=DECISION_ALLOW,
        reason=result.reason if result.policy_id else None,
        result_summary=summary,
    )
    return {"ok": True, "data": data, "decision": DECISION_ALLOW, "audit_ids": [audit_id], "policy_id": result.policy_id}
