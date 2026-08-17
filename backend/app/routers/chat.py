"""Chat 会话接口（Sprint 4）：会话历史只读。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import schemas
from ..database import get_db
from ..services.session import history

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/sessions/{session_id}/messages", response_model=list[schemas.ChatMessageOut])
def list_messages(session_id: str, db: Session = Depends(get_db)):
    rows = history(db, session_id)
    if not rows:
        raise HTTPException(status_code=404, detail="会话不存在或暂无消息")
    return rows
