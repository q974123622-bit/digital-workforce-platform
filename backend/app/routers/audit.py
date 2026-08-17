from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[schemas.AuditOut])
def list_audit(
    trace_id: str | None = Query(default=None),
    employee_id: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = select(models.AuditEvent).order_by(models.AuditEvent.ts.desc())
    if trace_id:
        q = q.where(models.AuditEvent.trace_id == trace_id)
    if employee_id:
        q = q.where(models.AuditEvent.employee_id == employee_id)
    if decision:
        q = q.where(models.AuditEvent.decision == decision)
    return db.scalars(q).all()


@router.post("", response_model=schemas.AuditOut, status_code=201)
def create_audit(payload: schemas.AuditCreate, db: Session = Depends(get_db)):
    event = models.AuditEvent(**payload.model_dump())
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.get("/{event_id}", response_model=schemas.AuditOut)
def get_audit(event_id: int, db: Session = Depends(get_db)):
    event = db.get(models.AuditEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="审计事件不存在")
    return event


@router.delete("/{event_id}", status_code=204)
def delete_audit(event_id: int, db: Session = Depends(get_db)):
    event = db.get(models.AuditEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="审计事件不存在")
    db.delete(event)
    db.commit()


# 说明：审计事件为追加式记录，刻意不提供 PUT/PATCH 更新接口。
