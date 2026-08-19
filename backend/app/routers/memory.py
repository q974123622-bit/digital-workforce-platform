"""记忆插件接口。

提供：
- POST /memory        写入一条记忆（带 7 维度标签）
- GET  /memory        查询记忆（支持按主体/类型/对方/可见性/等级 多条件过滤，时间倒序）
"""

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.memory_attachment import save_attachment
from ..services.memory_compress import compress_expired_sessions
from ..services.memory_permission import can_read_memory

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("", response_model=schemas.MemoryOut, status_code=201)
def add_memory(payload: schemas.MemoryCreate, db: Session = Depends(get_db)):
    """写入一条记忆：主体是谁、类型是什么、内容、可见性、等级、生命周期等。"""
    mem = models.MemoryEntry(**payload.model_dump())
    db.add(mem)
    db.commit()
    db.refresh(mem)
    return mem


@router.post("/summarize")
def summarize(db: Session = Depends(get_db)):
    """压缩过期会话成摘要（Step 6），返回压缩的会话数。"""
    count = compress_expired_sessions(db)
    return {"summarized": count}


@router.post("/attachments", response_model=schemas.MemoryOut, status_code=201)
def upload_attachment(
    subject_no: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传附件（文本文件）：存文件 + 提取摘要，返回附件记忆。"""
    content = file.file.read().decode("utf-8", errors="ignore")
    filename = file.filename or "unnamed.txt"
    return save_attachment(db, subject_no=subject_no, filename=filename, content=content)


@router.get("", response_model=list[schemas.MemoryOut])
def list_memory(
    subject_type: str | None = Query(default=None),
    subject_no: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    related_subject_no: str | None = Query(default=None),
    visibility: str | None = Query(default=None),
    data_level: str | None = Query(default=None),
    x_demo_actor: str | None = Header(default=None, alias="X-Demo-Actor"),
    db: Session = Depends(get_db),
):
    """按条件查询记忆，时间从新到旧排列；并按读者身份（X-Demo-Actor）过滤权限。"""
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
    rows = db.scalars(q).all()
    # 按权限过滤：只返回读者能看的记忆
    return [r for r in rows if can_read_memory(x_demo_actor, r, db)]
