"""内部接口（服务间，不暴露前端）：Policy Evaluate / Gateway Invoke。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import schemas
from ..database import get_db
from ..services.gateway import invoke_plugin
from ..services.identity import resolve_identity
from ..services.policy import ResourceRef, evaluate

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/policy/evaluate", response_model=schemas.PolicyEvaluateOut)
def policy_evaluate(payload: schemas.PolicyEvaluateIn, db: Session = Depends(get_db)):
    """四维策略评估；subject 以数据库身份为准（调用方传入的身份字段仅作参考，不可伪造）。"""
    subject = resolve_identity(db, payload.subject.employee_no)
    if subject is None:
        raise HTTPException(status_code=404, detail="数字员工不存在")
    resource = ResourceRef(
        type=payload.resource.type,
        id=payload.resource.id,
        data_level=payload.resource.data_level or "L1",
    )
    result = evaluate(db, subject, resource, payload.action, payload.context)
    return schemas.PolicyEvaluateOut(
        decision=result.decision,
        policy_id=result.policy_id,
        reason=result.reason,
    )


@router.post("/gateway/invoke", response_model=schemas.GatewayInvokeOut)
def gateway_invoke(payload: schemas.GatewayInvokeIn, db: Session = Depends(get_db)):
    """唯一插件执行入口：Identity → Policy → Gateway → Adapter → Result + Audit。"""
    return invoke_plugin(
        db,
        employee_id=payload.employee_id,
        plugin_id=payload.plugin_id,
        action=payload.action,
        params=payload.params or {},
        trace_id=payload.trace_id,
    )
