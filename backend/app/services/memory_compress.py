"""对话压缩（Step 6）：把过期会话提炼成精要摘要，存进 MemoryEntry。"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from .llm import DeepSeekProvider
from .session import history

DEFAULT_DAYS = 30


def _generate_summary(messages: list[models.ChatMessage]) -> str:
    """生成摘要：优先 LLM 总结，无密钥/失败时降级为拼接对话。"""
    try:
        provider = DeepSeekProvider()
        conversation = "\n".join(f"{m.role}: {m.content}" for m in messages if m.role in ("user", "assistant"))
        resp = provider.chat(
            [{"role": "user", "content": f"请用一段话（不超过 100 字）概括以下对话的要点：\n{conversation}", "source": "demo"}]
        )
        summary = (resp.content or "").strip()
        if summary:
            return summary[:200]
    except Exception:
        pass
    # 降级：拼接前几条用户/助手消息
    parts = [m.content for m in messages if m.role in ("user", "assistant")][:3]
    return "；".join(p.strip() for p in parts if p.strip())[:200] or "（无对话内容）"


def compress_expired_sessions(db: Session, days: int = DEFAULT_DAYS) -> int:
    """压缩超过 days 天、未压缩、未删除的会话，返回压缩的会话数。"""
    cutoff = datetime.now() - timedelta(days=days)
    sessions = db.scalars(
        select(models.ChatSession).where(
            models.ChatSession.deleted == False,
            models.ChatSession.summarized == False,
            models.ChatSession.created_at < cutoff,
        )
    ).all()

    count = 0
    for session in sessions:
        msgs = history(db, session.session_id)
        if not msgs:
            continue
        summary = _generate_summary(msgs)
        emp = db.get(models.DigitalEmployee, session.employee_id)
        subject_type = emp.type if emp else "virtual"
        entry = models.MemoryEntry(
            subject_type=subject_type,
            subject_no=session.employee_id,
            kind="summary",
            content=summary,
            content_type="text",
            related_subject_no=session.session_id,
            visibility="personal",
            data_level="L2",
            lifecycle="summarized",
        )
        db.add(entry)
        session.summarized = True
        count += 1
    db.commit()
    return count
