"""用户画像（Step 8）：从用户的历史记忆自动提炼画像，存 kind=profile。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from .llm import DeepSeekProvider

_KIND_LABEL = {
    "conversation": "对话",
    "decision": "决策",
    "fact": "事实",
    "summary": "摘要",
    "attachment": "附件",
    "basic_info": "基本信息",
    "profile": "画像",
}


def _generate_profile(memories: list[models.MemoryEntry]) -> str:
    """从用户记忆提炼画像：优先 LLM；无密钥/失败时降级为拼接记忆内容。"""
    lines = [f"[{_KIND_LABEL.get(m.kind, m.kind)}] {m.content}" for m in memories if m.content]
    material = "\n".join(lines)

    try:
        provider = DeepSeekProvider()
        resp = provider.chat(
            [
                {
                    "role": "user",
                    "content": (
                        "请根据以下这位员工的记忆，总结一份用户画像（不超过 150 字），涵盖："
                        "沟通风格、偏好习惯、关注领域、决策倾向。只输出画像本身，不要用 Markdown。\n"
                        f"{material[:3000]}"
                    ),
                    "source": "demo",
                }
            ]
        )
        profile = (resp.content or "").strip()
        if profile:
            return profile[:300]
    except Exception:
        pass
    # 降级：拼接记忆内容
    parts = [m.content for m in memories if m.content][:5]
    return "；".join(p.strip() for p in parts if p.strip())[:300] or "（暂无足够记忆生成画像）"


def generate_profile(db: Session, *, subject_no: str) -> models.MemoryEntry:
    """生成/刷新某用户的画像：读其全部记忆，提炼画像，存（或更新）kind=profile。"""
    memories = list(
        db.scalars(
            select(models.MemoryEntry)
            .where(models.MemoryEntry.subject_no == subject_no)
            .order_by(models.MemoryEntry.created_at.desc())
        )
    )
    profile_text = _generate_profile(memories)

    existing = db.scalar(
        select(models.MemoryEntry).where(
            models.MemoryEntry.subject_no == subject_no,
            models.MemoryEntry.kind == "profile",
        )
    )
    if existing is not None:
        existing.content = profile_text
        db.commit()
        db.refresh(existing)
        return existing

    entry = models.MemoryEntry(
        subject_type="human",
        subject_no=subject_no,
        kind="profile",
        content=profile_text,
        content_type="text",
        visibility="personal",
        data_level="L2",
        lifecycle="active",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
