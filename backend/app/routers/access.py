"""L3 敏感资源读取白名单申请与审批（老接口，路径与响应结构保持兼容）。

写链路已收敛到 ``services/access_request.py``：本模块只做请求解析与目标校验。
插件类申请不再限于 L3，审批通过后由服务层写 ``AgentPluginBinding``
（管理员侧推荐改用 ``POST /admin/access-requests/{id}/approve``，因为老接口要求
审批人必须是「正式员工的分身」，管理员账号没有分身会 403）。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.access_request import (
    decide_access_request,
    formal_subject,
    resolve_plugin_for_request,
)
from ..services.gateway import write_audit
from ..services.knowledge_registry import plugin_id_for_level, resolve as resolve_kb
from ..services.policy import DECISION_DENY

router = APIRouter(prefix="/access-requests", tags=["access"])


def _resolve_resource(
    db: Session, resource_type: str, resource_id: str, requester_no: str | None = None,
) -> tuple[str, str | None]:
    """解析申请目标。

    知识库仍只允许 L3（敏感数据白名单语义不变）；插件放开为「存在 ∧ active ∧
    有已发布版本」，个人插件只能由属主本人申请。
    """
    if resource_type == "knowledge":
        kb = resolve_kb(db, resource_id)
        if kb is None:
            raise HTTPException(status_code=404, detail={"message": "知识库资源不存在", "resource_id": resource_id})
        if kb.data_level != "L3":
            raise HTTPException(status_code=400, detail="只有 L3 知识库需要白名单申请")
        return plugin_id_for_level(kb.data_level), kb.id
    plugin = resolve_plugin_for_request(db, resource_id, requester_human_no=requester_no)
    return plugin.id, None


@router.post("", response_model=schemas.AccessRequestOut, status_code=201)
def create_request(
    payload: schemas.AccessRequestCreate,
    applicant_no: str = Query(..., description="申请人AI员工工号"),
    db: Session = Depends(get_db),
):
    plugin_id, kb_id = _resolve_resource(db, payload.resource_type, payload.resource_id, applicant_no)
    try:
        formal_subject(db, applicant_no)
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
    formal_subject(db, payload.actor_no, approver=True)
    return decide_access_request(db, request, approve=payload.approve, decided_by=payload.actor_no)


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
