"""Long-term memory adapter. Mock mode is durable local DB; internal mode is secret-configured mem0."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime
from uuid import uuid4

import httpx
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


class InternalMemoryAdapter(MockMemoryAdapter):
    def _settings(self) -> tuple[str, str]:
        base = os.getenv("DWP_INTERNAL_MEM0_BASE_URL", "").rstrip("/")
        token = os.getenv("DWP_INTERNAL_MEM0_AUTHORIZATION", "")
        if not base or not token:
            raise RuntimeError("内部 mem0 配置不完整")
        return base, token

    def upsert(self, db, ns, content, memory_type, source, expires_at=None):
        row = super().upsert(db, ns, content, memory_type, source, expires_at)
        try:
            base, token = self._settings()
            with httpx.Client(timeout=10) as client:
                response = client.post(f"{base}/memories", headers={"Authorization": token}, json={
                    "namespace": ":".join(ns), "content": content, "memory_type": memory_type,
                })
                response.raise_for_status()
                body = response.json()
            row.external_id = str(body.get("id", "")) or None
            row.sync_status = "synced"
            db.commit()
        except Exception as exc:  # never persist endpoint, response or credentials
            row.sync_status = "failed"
            job = models.MemorySyncJob(
                id=f"MJ-{uuid4().hex[:14].upper()}", memory_id=row.id, operation="upsert",
                status="pending", attempts=1, error_summary=exc.__class__.__name__,
            )
            db.add(job); db.commit()
            if source == "explicit":
                raise RuntimeError("长期记忆暂时无法同步，请稍后重试") from exc
        return row

    def search(self, db, ns, query, top_k=5):
        try:
            base, token = self._settings()
            with httpx.Client(timeout=10) as client:
                response = client.post(f"{base}/memories/search", headers={"Authorization": token}, json={
                    "namespace": ":".join(ns), "query": query, "top_k": top_k,
                })
                response.raise_for_status(); body = response.json()
            rows = []
            for item in (body.get("memories") or body.get("results") or [])[:top_k]:
                rows.append(models.MemoryRecord(
                    id=str(item.get("id") or f"REMOTE-{uuid4().hex}"), tenant_id=ns[0],
                    requester_human_no=ns[1], agent_id=ns[2],
                    memory_type=str(item.get("memory_type") or "summary"),
                    content=str(item.get("content") or item.get("memory") or "")[:4000],
                    source=str(item.get("source") or "automatic"), sync_status="synced",
                ))
            return rows
        except Exception:
            # Internal mem0 unavailable: continue with only five-round short-term memory.
            return []

    def health(self):
        try:
            base, token = self._settings()
            with httpx.Client(timeout=5) as client:
                response = client.get(f"{base}/health", headers={"Authorization": token})
                response.raise_for_status()
            return {"mode": "internal", "status": "healthy"}
        except Exception:
            return {"mode": "internal", "status": "unavailable"}


def get_memory_adapter() -> MemoryAdapter:
    return InternalMemoryAdapter() if os.getenv("DWP_MEMORY_MODE", "mock") == "internal" else MockMemoryAdapter()
