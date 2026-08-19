"""记忆插件接口。

提供：
- POST /memory        写入一条记忆（带 7 维度标签）
- GET  /memory        查询记忆（支持按主体/类型/对方/可见性/等级 多条件过滤，时间倒序）
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("", response_model=schemas.MemoryOut, status_code=201)
def add_memory(payload: schemas.MemoryCreate, db: Session = Depends(get_db)):
    """写入一条记忆：主体是谁、类型是什么、内容、可见性、等级、生命周期等。"""
    mem = models.MemoryEntry(**payload.model_dump())
    db.add(mem)
    db.commit()
    db.refresh(mem)
    return mem


@router.get("", response_model=list[schemas.MemoryOut])
def list_memory(
    subject_type: str | None = Query(default=None),
    subject_no: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    related_subject_no: str | None = Query(default=None),
    visibility: str | None = Query(default=None),
    data_level: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """按条件查询记忆，时间从新到旧排列。所有过滤条件可选，缺省表示不过滤。"""
    q = select(models.MemoryEntry).order_by(models.MemoryEntry.created_at.desc())
    if subject_type:
        q = q.where(models.MemoryEntry.subject_type == subject_type)
    if subject_no:
        q = q.where(models.MemoryEntry.subject_no == subject_no)
    if kind:
        q = q.where(models.MemoryEntry.kind == kind)
    if related_subject_no:
        q = q.where(models.MemoryEntry.related_subject_no == related_subject_no)
    if visibility:
        q = q.where(models.MemoryEntry.visibility == visibility)
    if data_level:
        q = q.where(models.MemoryEntry.data_level == data_level)
    return db.scalars(q).all()
