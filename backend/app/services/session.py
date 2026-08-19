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


def latest_session(db: Session, employee_id: str) -> models.ChatSession | None:
    """获取某数字员工最近的一次未删除会话（用于退出后恢复对话）。"""
    return db.scalar(
        select(models.ChatSession)
        .where(models.ChatSession.employee_id == employee_id, models.ChatSession.deleted == False)
        .order_by(models.ChatSession.created_at.desc())
        .limit(1)
    )


def list_sessions(db: Session, employee_id: str) -> list[models.ChatSession]:
    """列出某数字员工的所有未删除会话，按创建时间倒序（最新的在前）。"""
    return list(
        db.scalars(
            select(models.ChatSession)
            .where(models.ChatSession.employee_id == employee_id, models.ChatSession.deleted == False)
            .order_by(models.ChatSession.created_at.desc())
        )
    )


def soft_delete(db: Session, session_id: str) -> bool:
    """软删除会话：仅标记 deleted=True，不物理删除（数据保留供管理回查）。"""
    session = db.scalar(select(models.ChatSession).where(models.ChatSession.session_id == session_id))
    if session is None:
        return False
    session.deleted = True
    db.commit()
    return True


def set_title_if_empty(db: Session, session_id: str, content: str) -> None:
    """会话标题为空时生成标题。

    PoC：清理空白并截断第一条消息（超长加省略号）。
    TODO: 接入 DeepSeek 密钥后升级为 LLM 自动总结（见 docs/MEMORY_PLUGIN_DESIGN.md 待确认清单）。
    """
    session = db.scalar(select(models.ChatSession).where(models.ChatSession.session_id == session_id))
    if session and not session.title:
        cleaned = content.strip().replace("\n", " ")
        session.title = cleaned[:20] + ("…" if len(cleaned) > 20 else "")
        db.commit()
