"""MemoryAdapter（Step 9）：统一记忆访问接口，供任意 Runtime（Harness/OpenClaw/平台自身）调用。

对照 runtime_adapter.py 的 RuntimeAdapter（执行引擎接口），本模块提供「记忆」这一
能力域的统一接口：recall（召回记忆）与 remember（写入记忆）。任何对话引擎要接入
记忆插件，都通过本接口，而不是直接操作 MemoryEntry 表 —— 体现「一切即插件」的解耦。
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from .memory_service import MemoryHit, render_prompt_context
from .memory_permission import can_read_memory

_KIND_LABEL = {
    "profile": "画像",
    "fact": "事实",
    "decision": "决策",
    "summary": "摘要",
    "conversation": "对话",
    "attachment": "附件",
    "basic_info": "基本信息",
}


@dataclass(frozen=True)
class MemoryRecall:
    """一次记忆召回的结果。"""

    entries: list[models.MemoryEntry] = field(default_factory=list)

    @property
    def context(self) -> str:
        """把召回的记忆格式化成可注入 agent 上下文的文本。"""
        hits = [
            MemoryHit(
                memory_id=entry.id or 0,
                content=f"[{_KIND_LABEL.get(entry.kind, entry.kind)}] {entry.content}",
                created_at=entry.created_at or datetime.now(),
                score=0,
                kind=entry.kind,
            )
            for entry in self.entries
        ]
        return render_prompt_context(hits)


class MemoryAdapter:
    """统一记忆访问接口：recall（读）与 remember（写）。"""

    def recall(
        self,
        db: Session,
        *,
        subject_no: str,
        reader_no: str | None = None,
        limit: int = 10,
    ) -> MemoryRecall:
        """召回某主体的记忆（按权限过滤，时间倒序，截断 limit 条）。"""
        rows = db.scalars(
            select(models.MemoryEntry)
            .where(models.MemoryEntry.subject_no == subject_no)
            .order_by(models.MemoryEntry.created_at.desc())
        ).all()
        visible = [r for r in rows if can_read_memory(reader_no, r, db)]
        return MemoryRecall(entries=visible[:limit])

    def remember(
        self,
        db: Session,
        *,
        subject_type: str,
        subject_no: str,
        kind: str,
        content: str,
        visibility: str = "personal",
        data_level: str = "L1",
        **kwargs,
    ) -> models.MemoryEntry:
        """写入一条记忆。"""
        entry = models.MemoryEntry(
            subject_type=subject_type,
            subject_no=subject_no,
            kind=kind,
            content=content,
            visibility=visibility,
            data_level=data_level,
            **kwargs,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry
