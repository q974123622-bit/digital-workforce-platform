from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import knowledge_source_health

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])


def kb_to_out(db: Session, kb: models.KnowledgeBase) -> schemas.KnowledgeBaseOut:
    """组装知识库输出：附带双后端映射与当前可达性（管理端据此置灰）。"""
    view = knowledge_source_health.kb_availability(db, kb)
    return schemas.KnowledgeBaseOut(
        id=kb.id,
        name=kb.name,
        level=kb.level,
        data_level=kb.data_level,
        resource_type=kb.resource_type,
        allowed_employment_type=list(kb.allowed_employment_type or []),
        department_scope=list(kb.department_scope or []),
        domain=kb.domain,
        description=kb.description,
        status=kb.status,
        doc_path=kb.doc_path,
        sources=view["sources"],
        active_source=view["active_source"],
        available=view["available"],
        unavailable_reason=view["unavailable_reason"],
    )


@router.get("", response_model=list[schemas.KnowledgeBaseOut])
def list_knowledge_bases(db: Session = Depends(get_db)):
    rows = db.scalars(select(models.KnowledgeBase).order_by(models.KnowledgeBase.id)).all()
    return [kb_to_out(db, kb) for kb in rows]


@router.get("/{kb_id}", response_model=schemas.KnowledgeBaseOut)
def get_knowledge_base(kb_id: str, db: Session = Depends(get_db)):
    kb = db.get(models.KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb_to_out(db, kb)
