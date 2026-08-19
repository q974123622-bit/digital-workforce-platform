"""Access Request API（P20）：L3 敏感资源白名单申请/审批。

链路：正式员工发起申请 → 管理员审批 → 白名单 grant 写入 → 再次访问 allow → 审计可追溯。
非正式员工（intern）发起申请 → 403 POLICY_DENIED（策略拒绝，不落申请单）。
resource_type 枚举：knowledge | plugin | data（申请对象不限于知识库）。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.gateway import write_audit
from ..services.identity import resolve_identity
from ..services.knowledge_registry import plugin_id_for_level, resolve as resolve_kb
from ..services.policy import DECISION_ALLOW, DECISION_DENY

router = APIRouter(prefix="/access-requests", tags=["access"])

TERMINAL_STATUSES = {"approved", "granted", "rejected"}


def _resolve_grant_plugin(db: Session, resource_type: str, resource_id: str) -> tuple[str, str | None]:
    """返回 (plugin_id, knowledge_base_id or None)；资源不存在 → 404。"""
    if resource_type == "knowledge":
        kb = resolve_kb(db, resource_id)
        if kb is None:
            raise HTTPException(status_code=404, detail={"message": "知识库资源不存在", "resource_id": resource_id})
        return plugin_id_for_level(kb.data_level), kb.id
    if resource_type == "plugin":
        plugin = db.get(models.Plugin, resource_id)
        if plugin is None:
            raise HTTPException(status_code=404, detail={"message": "插件不存在", "resource_id": resource_id})
        return plugin.id, None
    # data：本期无独立数据资源登记，按 resource_id 作为插件映射（预留）
    return resource_id, None


@router.post("", response_model=schemas.AccessRequestOut, status_code=201)
def create_request(
    payload: schemas.AccessRequestCreate,
    applicant_no: str = Query(..., description="申请人数字员工工号"),
    db: Session = Depends(get_db),
):
    """发起敏感资源访问申请；仅 employment_type=formal 可申请，实习生 403 且不落申请单。"""
    subject = resolve_identity(db, applicant_no)
    if subject is None:
        raise HTTPException(status_code=404, detail={"message": "数字员工不存在", "employee_no": applicant_no})
    plugin_id, kb_id = _resolve_grant_plugin(db, payload.resource_type, payload.resource_id)
    if subject.employment_type != "formal":
        write_audit(
            db,
            trace_id=f"ARQ-{applicant_no}-deny",
            employee_id=applicant_no,
            plugin_id=plugin_id,
            action="access_apply",
            decision=DECISION_DENY,
            knowledge_base_id=kb_id,
            reason="非正式员工不可发起敏感资源申请（仅 formal 可申请）",
            result_summary="denied: intern 不可申请敏感资源",
        )
        raise HTTPException(
            status_code=403,
            detail={
                "message": "策略拒绝",
                "policy_id": "ACCESS-FORMAL-ONLY",
                "reason": "仅正式员工可发起敏感资源申请",
            },
        )
    request = models.AccessRequest(
        applicant_no=applicant_no,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        reason=payload.reason,
        status="pending",
        approval_chain=[],
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    write_audit(
        db,
        trace_id=f"ARQ-{request.id}",
        employee_id=applicant_no,
        plugin_id=plugin_id,
        action="access_apply",
        decision="pending",
        knowledge_base_id=kb_id,
        reason=f"{payload.resource_type}:{payload.resource_id}",
        result_summary="申请已发起 status=pending",
    )
    return request


@router.post("/{request_id}/approve", response_model=schemas.AccessRequestOut)
def approve_request(
    request_id: int,
    payload: schemas.AccessRequestApproveIn,
    db: Session = Depends(get_db),
):
    """管理员一键通过/拒绝；通过时写白名单 grant（employee_plugin_grant, grant_source=whitelist）并置 granted。"""
    request = db.get(models.AccessRequest, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail={"message": "申请单不存在", "request_id": request_id})
    if request.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"message": "申请单已终态，不可重复审批", "status": request.status},
        )
    actor = resolve_identity(db, payload.actor_no)
    if actor is None:
        raise HTTPException(status_code=404, detail={"message": "审批人不存在", "actor_no": payload.actor_no})
    if actor.employment_type != "formal":
        raise HTTPException(
            status_code=403,
            detail={"message": "策略拒绝", "policy_id": "ACCESS-FORMAL-ONLY", "reason": "仅正式员工可审批"},
        )
    plugin_id, kb_id = _resolve_grant_plugin(db, request.resource_type, request.resource_id)
    trace_id = f"ARQ-{request.id}"
    if payload.approve:
        grant = db.scalar(
            select(models.EmployeePluginGrant).where(
                models.EmployeePluginGrant.employee_id == request.applicant_no,
                models.EmployeePluginGrant.plugin_id == plugin_id,
            )
        )
        if grant is None:
            grant = models.EmployeePluginGrant(
                employee_id=request.applicant_no,
                plugin_id=plugin_id,
                action="read",
                decision_mode=DECISION_ALLOW,
                grant_source="whitelist",
            )
            db.add(grant)
        else:
            grant.action = "read"
            grant.decision_mode = DECISION_ALLOW
            grant.grant_source = "whitelist"
        request.status = "granted"
        request.decided_by = payload.actor_no
        request.decided_at = datetime.now()
        db.commit()
        write_audit(
            db,
            trace_id=trace_id,
            employee_id=request.applicant_no,
            plugin_id=plugin_id,
            action="access_approve",
            decision=DECISION_ALLOW,
            knowledge_base_id=kb_id,
            reason=f"审批通过 by {payload.actor_no}",
            result_summary="status=granted",
        )
        write_audit(
            db,
            trace_id=trace_id,
            employee_id=request.applicant_no,
            plugin_id=plugin_id,
            action="access_grant",
            decision=DECISION_ALLOW,
            knowledge_base_id=kb_id,
            reason="白名单授权写入",
            result_summary="grant_source=whitelist",
        )
    else:
        request.status = "rejected"
        request.decided_by = payload.actor_no
        request.decided_at = datetime.now()
        db.commit()
        write_audit(
            db,
            trace_id=trace_id,
            employee_id=request.applicant_no,
            plugin_id=plugin_id,
            action="access_approve",
            decision=DECISION_DENY,
            knowledge_base_id=kb_id,
            reason=f"审批拒绝 by {payload.actor_no}",
            result_summary="status=rejected",
        )
    db.refresh(request)
    return request


@router.get("", response_model=list[schemas.AccessRequestOut])
def list_requests(
    applicant_no: str | None = Query(None, description="按申请人过滤"),
    status: str | None = Query(None, description="按状态过滤：pending|approved|rejected|granted"),
    db: Session = Depends(get_db),
):
    """查询申请单（含待审批列表）；按 applicant_no / status 过滤。"""
    query = select(models.AccessRequest).order_by(models.AccessRequest.id.desc())
    if applicant_no:
        query = query.where(models.AccessRequest.applicant_no == applicant_no)
    if status:
        query = query.where(models.AccessRequest.status == status)
    return db.scalars(query).all()
