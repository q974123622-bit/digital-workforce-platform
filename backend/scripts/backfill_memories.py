"""把已有本地聊天记录回填为可跨会话检索的记忆。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal
from app.services.memory_service import capture_turn


@dataclass
class BackfillStats:
    scanned: int = 0
    created: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass(frozen=True)
class _BackfillCandidate:
    owner_employee_no: str
    source_type: str
    source_session_id: str
    source_ref: str
    user_text: str
    assistant_text: str
    trace_id: str | None = None


def backfill_memories(
    db: Session, *, dry_run: bool = False, limit: int | None = None
) -> BackfillStats:
    """回填所有可配对的历史问答；重复运行不会重复写入。"""
    stats = BackfillStats()
    candidates = list(_chat_candidates(db)) + list(_conversation_candidates(db))
    if limit is not None:
        candidates = candidates[: max(0, limit)]

    for candidate in candidates:
        stats.scanned += 1
        existing = db.scalar(
            select(models.MemoryEntry).where(
                models.MemoryEntry.source_ref == candidate.source_ref
            )
        )
        if existing is not None:
            stats.skipped += 1
            continue
        if dry_run:
            stats.created += 1
            continue
        memory_id = capture_turn(
            db,
            owner_employee_no=candidate.owner_employee_no,
            source_type=candidate.source_type,
            source_session_id=candidate.source_session_id,
            source_ref=candidate.source_ref,
            user_text=candidate.user_text,
            assistant_text=candidate.assistant_text,
            trace_id=candidate.trace_id,
        )
        if memory_id is None:
            stats.errors += 1
        else:
            stats.created += 1
    return stats


def _chat_candidates(db: Session) -> Iterable[_BackfillCandidate]:
    sessions = {
        session.session_id: session
        for session in db.scalars(select(models.ChatSession))
    }
    last_user: dict[str, models.ChatMessage] = {}
    rows = db.scalars(
        select(models.ChatMessage).order_by(
            models.ChatMessage.session_id, models.ChatMessage.id
        )
    )
    for message in rows:
        if message.role == "user":
            last_user[message.session_id] = message
            continue
        if message.role != "assistant" or not _eligible_response(message.content):
            continue
        user_message = last_user.get(message.session_id)
        session = sessions.get(message.session_id)
        if user_message is None or session is None:
            continue
        yield _BackfillCandidate(
            owner_employee_no=session.employee_id,
            source_type="chat",
            source_session_id=session.session_id,
            source_ref=f"chat:{session.session_id}:assistant:{message.id}",
            user_text=user_message.content,
            assistant_text=message.content,
            trace_id=session.trace_id,
        )


def _conversation_candidates(db: Session) -> Iterable[_BackfillCandidate]:
    last_user: dict[str, models.ConversationMessage] = {}
    rows = db.scalars(
        select(models.ConversationMessage).order_by(
            models.ConversationMessage.conversation_id,
            models.ConversationMessage.seq,
        )
    )
    for message in rows:
        if message.role == "user":
            last_user[message.conversation_id] = message
            continue
        if message.role != "assistant" or not _eligible_response(message.content):
            continue
        user_message = last_user.get(message.conversation_id)
        if user_message is None or not message.participant_no:
            continue
        yield _BackfillCandidate(
            owner_employee_no=message.participant_no,
            source_type="conversation",
            source_session_id=message.conversation_id,
            source_ref=(
                f"conversation:{message.conversation_id}:assistant:{message.seq}"
            ),
            user_text=user_message.content,
            assistant_text=message.content,
        )


def _eligible_response(content: str) -> bool:
    text = content.strip()
    return bool(text) and not text.startswith(
        ("LLM 暂不可用", "工具调用次数过多", "（")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="回填本地聊天长期记忆")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写数据库")
    parser.add_argument("--limit", type=int, default=None, help="最多处理的问答轮数")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        stats = backfill_memories(db, dry_run=args.dry_run, limit=args.limit)
    finally:
        db.close()
    print(
        f"scanned={stats.scanned} created={stats.created} "
        f"skipped={stats.skipped} errors={stats.errors}"
    )
    return 1 if stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
