from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])


@router.get("", response_model=list[schemas.KnowledgeBaseOut])
def list_knowledge_bases(db: Session = Depends(get_db)):
    return db.scalars(select(models.KnowledgeBase).order_by(models.KnowledgeBase.id)).all()


@router.get("/{kb_id}", response_model=schemas.KnowledgeBaseOut)
def get_knowledge_base(kb_id: str, db: Session = Depends(get_db)):
    kb = db.get(models.KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb
