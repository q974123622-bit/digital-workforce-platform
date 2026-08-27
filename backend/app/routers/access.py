"""L3 敏感资源读取白名单申请与审批。"""

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

TERMINAL_STATUSES = {"granted", "rejected"}


def _resolve_resource(db: Session, resource_type: str, resource_id: str) -> tuple[str, str | None, str]:
    """解析白名单对应的 grant；只允许登记过的 L3 资源。"""
    if resource_type == "knowledge":
        kb = resolve_kb(db, resource_id)
        if kb is None:
            raise HTTPException(status_code=404, detail={"message": "知识库资源不存在", "resource_id": resource_id})
        if kb.data_level != "L3":
            raise HTTPException(status_code=400, detail="只有 L3 知识库需要白名单申请")
        return plugin_id_for_level(kb.data_level), kb.id, "read"
    plugin = db.get(models.Plugin, resource_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail={"message": "插件不存在", "resource_id": resource_id})
    if plugin.data_level != "L3":
        raise HTTPException(status_code=400, detail="只有 L3 插件需要白名单申请")
    grant_action = "search" if plugin.type == "memory" else "read"
    return plugin.id, None, grant_action


def _formal_subject(db: Session, employee_no: str, *, approver: bool = False):
    subject = resolve_identity(db, employee_no)
    if subject is None:
        raise HTTPException(status_code=404, detail={"message": "数字员工不存在", "employee_no": employee_no})
    allowed = subject.employment_type == "formal" and (not approver or subject.employee_type == "twin")
    if not allowed:
        role = "正式员工数字分身" if approver else "正式员工"
        raise HTTPException(
            status_code=403,
            detail={"message": "策略拒绝", "policy_id": "ACCESS-FORMAL-ONLY", "reason": f"仅{role}可执行该操作"},
        )
    return subject


@router.post("", response_model=schemas.AccessRequestOut, status_code=201)
def create_request(
    payload: schemas.AccessRequestCreate,
    applicant_no: str = Query(..., description="申请人数字员工工号"),
    db: Session = Depends(get_db),
):
    plugin_id, kb_id, _ = _resolve_resource(db, payload.resource_type, payload.resource_id)
    try:
        _formal_subject(db, applicant_no)
    except HTTPException as exc:
        if exc.status_code == 403:
            write_audit(
                db, trace_id=f"ARQ-{applicant_no}-deny", employee_id=applicant_no,
                plugin_id=plugin_id, knowledge_base_id=kb_id, action="access_apply",
                decision=DECISION_DENY, reason="非正式员工不可发起敏感资源申请",
                result_summary="denied",
            )
        raise

    duplicate = db.scalar(
        select(models.AccessRequest).where(
            models.AccessRequest.applicant_no == applicant_no,
            models.AccessRequest.resource_type == payload.resource_type,
            models.AccessRequest.resource_id == payload.resource_id,
            models.AccessRequest.status == "pending",
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail={"message": "相同资源已有待审批申请", "request_id": duplicate.id})

    request = models.AccessRequest(
        applicant_no=applicant_no, resource_type=payload.resource_type,
        resource_id=payload.resource_id, reason=payload.reason,
        status="pending", approval_chain=[],
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    write_audit(
        db, trace_id=f"ARQ-{request.id}", employee_id=applicant_no,
        plugin_id=plugin_id, knowledge_base_id=kb_id, action="access_apply",
        decision="pending", reason=f"{payload.resource_type}:{payload.resource_id}",
        result_summary="status=pending",
    )
    return request


@router.post("/{request_id}/approve", response_model=schemas.AccessRequestOut)
def approve_request(request_id: int, payload: schemas.AccessRequestApproveIn, db: Session = Depends(get_db)):
    request = db.get(models.AccessRequest, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail={"message": "申请单不存在", "request_id": request_id})
    if request.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail={"message": "申请单已终态，不可重复审批", "status": request.status})
    _formal_subject(db, payload.actor_no, approver=True)
    plugin_id, kb_id, grant_action = _resolve_resource(db, request.resource_type, request.resource_id)
    trace_id = f"ARQ-{request.id}"

    request.decided_by = payload.actor_no
    request.decided_at = datetime.now()
    request.approval_chain = [{"actor_no": payload.actor_no, "decision": "approve" if payload.approve else "reject"}]
    if payload.approve:
        grant = db.scalar(
            select(models.EmployeePluginGrant).where(
                models.EmployeePluginGrant.employee_id == request.applicant_no,
                models.EmployeePluginGrant.plugin_id == plugin_id,
                models.EmployeePluginGrant.action == grant_action,
            )
        )
        if grant is None:
            grant = models.EmployeePluginGrant(
                employee_id=request.applicant_no, plugin_id=plugin_id, action=grant_action,
                decision_mode=DECISION_ALLOW, grant_source="whitelist",
            )
            db.add(grant)
        else:
            grant.decision_mode = DECISION_ALLOW
            grant.grant_source = "whitelist"
        request.status = "granted"
        decision = DECISION_ALLOW
    else:
        request.status = "rejected"
        decision = DECISION_DENY
    db.commit()
    db.refresh(request)

    write_audit(
        db, trace_id=trace_id, employee_id=request.applicant_no,
        plugin_id=plugin_id, knowledge_base_id=kb_id, action="access_approve",
        decision=decision, reason=f"{'审批通过' if payload.approve else '审批拒绝'} by {payload.actor_no}",
        result_summary=f"status={request.status}",
    )
    if payload.approve:
        write_audit(
            db, trace_id=trace_id, employee_id=request.applicant_no,
            plugin_id=plugin_id, knowledge_base_id=kb_id, action="access_grant",
            decision=DECISION_ALLOW, reason="L3 读取白名单授权写入",
            result_summary=f"action={grant_action}, grant_source=whitelist",
        )
    return request


@router.get("", response_model=list[schemas.AccessRequestOut])
def list_requests(
    applicant_no: str | None = Query(None), status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    query = select(models.AccessRequest).order_by(models.AccessRequest.id.desc())
    if applicant_no:
        query = query.where(models.AccessRequest.applicant_no == applicant_no)
    if status:
        query = query.where(models.AccessRequest.status == status)
    return db.scalars(query).all()
