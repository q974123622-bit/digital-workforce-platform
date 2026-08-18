"""个人记忆接口（记忆插件）。

提供：
- POST /memory        写入一条个人记忆
- GET  /memory/{human_no}  查询某真人的全部记忆（按时间倒序）
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("", response_model=schemas.MemoryOut, status_code=201)
def add_memory(payload: schemas.MemoryCreate, db: Session = Depends(get_db)):
    """写入一条记忆：human_no 是谁的、employee_no 和谁聊的、content 聊了什么。"""
    mem = models.PersonalMemory(**payload.model_dump())
    db.add(mem)
    db.commit()
    db.refresh(mem)
    return mem


@router.get("/{human_no}", response_model=list[schemas.MemoryOut])
def list_memory(human_no: str, db: Session = Depends(get_db)):
    """查询某真人的全部记忆，按时间从新到旧排列。"""
    rows = db.scalars(
        select(models.PersonalMemory)
        .where(models.PersonalMemory.human_no == human_no)
        .order_by(models.PersonalMemory.created_at.desc())
    ).all()
    return rows
