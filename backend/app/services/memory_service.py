from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from .memory_adapter import get_memory_adapter, namespace


def five_complete_rounds(messages: list[dict]) -> list[dict]:
    """Return the last five complete user/assistant pairs; dangling current goal is excluded."""
    pairs: list[list[dict]] = []
    pending: dict | None = None
    for item in messages:
        if item.get("role") == "user":
            pending = item
        elif item.get("role") == "assistant" and pending is not None:
            pairs.append([pending, item])
            pending = None
    return [item for pair in pairs[-5:] for item in pair]


def relevant_memories(db: Session, requester: str, agent_id: str, query: str) -> list[dict]:
    rows = get_memory_adapter().search(db, namespace(requester, agent_id), query, 5)
    return [{"type": row.memory_type, "summary": row.content[:500]} for row in rows]


def save_explicit(db: Session, requester: str, agent_id: str, content: str, memory_type: str = "preference"):
    if not content.strip():
        raise ValueError("记忆内容不能为空")
    lowered = content.lower()
    if any(marker in lowered for marker in ("authorization:", "bearer ", "api_key", "password=", "token=")):
        raise ValueError("记忆内容疑似包含认证信息，已拒绝保存")
    return get_memory_adapter().upsert(
        db, namespace(requester, agent_id), content.strip(), memory_type, "explicit", None,
    )


def compact_conversation(db: Session, conversation_id: str, requester: str, agent_id: str) -> None:
    rows = db.scalars(select(models.ConversationMessage).where(
        models.ConversationMessage.conversation_id == conversation_id,
    ).order_by(models.ConversationMessage.seq)).all()
    history = [{"role": row.role, "content": row.content, "seq": row.seq} for row in rows]
    complete = five_complete_rounds(history)
    completed_pair_count = len(complete) // 2
    # five_complete_rounds deliberately truncates; use all complete pair end sequences to find overflow.
    pair_ends: list[int] = []
    pending = None
    for row in rows:
        if row.role == "user": pending = row
        elif row.role == "assistant" and pending is not None:
            pair_ends.append(row.seq); pending = None
    if len(pair_ends) <= 5:
        return
    compact_through = pair_ends[-6]
    state = db.get(models.ConversationMemoryState, conversation_id)
    if state and state.compacted_through_seq >= compact_through:
        return
    old = [row for row in rows if (state.compacted_through_seq if state else 0) < row.seq <= compact_through]
    # Deterministic safe compaction for the MVP; internal model extraction can replace this behind the adapter.
    text = "；".join(row.content.strip()[:300] for row in old if row.content.strip())
    if text:
        get_memory_adapter().upsert(
            db, namespace(requester, agent_id), f"历史会话摘要：{text}"[:2000], "summary",
            "automatic", datetime.now() + timedelta(days=180),
        )
    state = state or models.ConversationMemoryState(conversation_id=conversation_id)
    state.compacted_through_seq = compact_through
    state.rolling_summary = text[:2000]
    state.status = "completed"
    state.updated_at = datetime.now()
    db.add(state); db.commit()
