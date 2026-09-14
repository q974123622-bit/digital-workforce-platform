"""Long-term memory adapter. Mock mode is durable local DB; internal mode is secret-configured mem0."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import models


def namespace(requester: str, agent_id: str) -> tuple[str, str, str]:
    return (os.getenv("DWP_TENANT_ID", "default"), requester, agent_id)


class MemoryAdapter(ABC):
    @abstractmethod
    def search(self, db: Session, ns: tuple[str, str, str], query: str, top_k: int = 5) -> list[models.MemoryRecord]: ...
    @abstractmethod
    def upsert(self, db: Session, ns: tuple[str, str, str], content: str, memory_type: str,
               source: str, expires_at: datetime | None = None) -> models.MemoryRecord: ...
    @abstractmethod
    def list(self, db: Session, ns: tuple[str, str, str]) -> list[models.MemoryRecord]: ...
    @abstractmethod
    def update(self, db: Session, row: models.MemoryRecord, content: str) -> models.MemoryRecord: ...
    @abstractmethod
    def delete(self, db: Session, row: models.MemoryRecord) -> None: ...
    @abstractmethod
    def health(self) -> dict: ...


class MockMemoryAdapter(MemoryAdapter):
    def search(self, db, ns, query, top_k=5):
        tenant, requester, agent = ns
        now = datetime.now()
        rows = db.scalars(select(models.MemoryRecord).where(
            models.MemoryRecord.tenant_id == tenant,
            models.MemoryRecord.requester_human_no == requester,
            models.MemoryRecord.agent_id == agent,
            or_(models.MemoryRecord.expires_at.is_(None), models.MemoryRecord.expires_at > now),
        ).order_by(models.MemoryRecord.retained.desc(), models.MemoryRecord.updated_at.desc())).all()
        terms = [part.lower() for part in query.split() if len(part) > 1]
        rows.sort(key=lambda row: sum(term in row.content.lower() for term in terms), reverse=True)
        return rows[:top_k]

    def upsert(self, db, ns, content, memory_type, source, expires_at=None):
        tenant, requester, agent = ns
        row = models.MemoryRecord(
            id=f"MEM-{uuid4().hex[:16].upper()}", tenant_id=tenant,
            requester_human_no=requester, agent_id=agent, memory_type=memory_type,
            content=content[:4000], source=source, expires_at=expires_at,
            sync_status="local" if os.getenv("DWP_MEMORY_MODE", "mock") == "mock" else "pending",
        )
        db.add(row); db.commit(); db.refresh(row)
        return row

    def list(self, db, ns):
        tenant, requester, agent = ns
        return db.scalars(select(models.MemoryRecord).where(
            models.MemoryRecord.tenant_id == tenant,
            models.MemoryRecord.requester_human_no == requester,
            models.MemoryRecord.agent_id == agent,
        ).order_by(models.MemoryRecord.updated_at.desc())).all()

    def update(self, db, row, content):
        row.content = content[:4000]; row.updated_at = datetime.now(); db.commit(); db.refresh(row); return row

    def delete(self, db, row):
        db.delete(row); db.commit()

    def health(self):
        return {"mode": "mock", "status": "healthy"}


def get_memory_adapter() -> MemoryAdapter:
    # MVP 仅保留本地 mock 记忆（前端记忆页展示用）；真实 mem0 适配器已移除。
    return MockMemoryAdapter()
