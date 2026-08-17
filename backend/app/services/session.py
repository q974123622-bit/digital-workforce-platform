"""Session Manager（Sprint 4）：保存 employee_id / session_id / message history。"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models


def new_session_id() -> str:
    return f"S-{uuid4().hex[:12]}"


def get_or_create(db: Session, session_id: str | None, employee_id: str) -> tuple[models.ChatSession, bool]:
    """按 session_id 获取或创建会话；返回 (session, created)。"""
    if session_id:
        session = db.scalar(select(models.ChatSession).where(models.ChatSession.session_id == session_id))
        if session is not None:
            return session, False
    session = models.ChatSession(session_id=session_id or new_session_id(), employee_id=employee_id, trace_id=f"T-{uuid4().hex[:12]}")
    db.add(session)
    db.commit()
    db.refresh(session)
    return session, True


def add_message(
    db: Session,
    *,
    session_id: str,
    role: str,
    content: str,
    tool_cards: list | None = None,
) -> models.ChatMessage:
    msg = models.ChatMessage(session_id=session_id, role=role, content=content, tool_cards=tool_cards or [])
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def history(db: Session, session_id: str) -> list[models.ChatMessage]:
    return list(db.scalars(select(models.ChatMessage).where(models.ChatMessage.session_id == session_id).order_by(models.ChatMessage.id)))
