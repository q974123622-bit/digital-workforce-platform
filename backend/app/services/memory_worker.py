"""Small durable mem0 outbox worker for the single-instance SQLite MVP."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Callable

import httpx
from sqlalchemy import or_, select, update

from .. import models
from ..database import SessionLocal
from .memory_adapter import InternalMemoryAdapter

BACKOFF_SECONDS = (5, 15, 45, 120, 300)


def recover_running_jobs() -> int:
    db = SessionLocal()
    try:
        rows = db.scalars(select(models.MemorySyncJob).where(
            models.MemorySyncJob.status == "running"
        )).all()
        for row in rows:
            row.status = "pending"
            row.locked_at = None
            row.next_attempt_at = datetime.now()
            db.add(row)
        db.commit()
        return len(rows)
    finally:
        db.close()


def _claim_one(db) -> models.MemorySyncJob | None:
    now = datetime.now()
    candidate = db.scalar(select(models.MemorySyncJob).where(
        models.MemorySyncJob.status == "pending",
        or_(models.MemorySyncJob.next_attempt_at.is_(None), models.MemorySyncJob.next_attempt_at <= now),
    ).order_by(models.MemorySyncJob.created_at).limit(1))
    if candidate is None:
        return None
    claimed = db.execute(update(models.MemorySyncJob).where(
        models.MemorySyncJob.id == candidate.id,
        models.MemorySyncJob.status == "pending",
    ).values(status="running", locked_at=now, updated_at=now))
    db.commit()
    if claimed.rowcount != 1:
        return None
    return db.get(models.MemorySyncJob, candidate.id)


def process_one(*, client_factory: Callable[..., httpx.Client] = httpx.Client) -> bool:
    db = SessionLocal()
    try:
        job = _claim_one(db)
        if job is None:
            return False
        adapter = InternalMemoryAdapter()
        try:
            base, token = adapter._settings()
            row = db.get(models.MemoryRecord, job.memory_id) if job.memory_id else None
            headers = {"Authorization": token}
            with client_factory(timeout=10) as client:
                if job.operation in {"upsert", "update"}:
                    if row is None:
                        raise RuntimeError("记忆记录不存在")
                    ns = ":".join((row.tenant_id, row.requester_human_no, row.agent_id))
                    body = {"namespace": ns, "content": row.content, "memory_type": row.memory_type}
                    if job.operation == "update" and row.external_id:
                        response = client.put(f"{base}/memories/{row.external_id}", headers=headers, json=body)
                    else:
                        response = client.post(f"{base}/memories", headers=headers, json=body)
                    response.raise_for_status()
                    response_body = response.json() if response.content else {}
                    row.external_id = str(response_body.get("id") or row.external_id or "") or None
                    row.sync_status = "synced"
                    db.add(row)
                elif job.operation == "delete":
                    external_id = str((job.payload or {}).get("external_id") or "")
                    if external_id:
                        response = client.delete(f"{base}/memories/{external_id}", headers=headers)
                        response.raise_for_status()
                else:
                    raise RuntimeError("未知记忆同步操作")
            job.status = "completed"
            job.error_summary = ""
            job.locked_at = None
            job.next_attempt_at = None
        except Exception as exc:  # keep only the exception category
            job.attempts += 1
            job.error_summary = exc.__class__.__name__
            job.locked_at = None
            if job.attempts >= len(BACKOFF_SECONDS):
                job.status = "failed"
                job.next_attempt_at = None
                if job.memory_id:
                    failed_row = db.get(models.MemoryRecord, job.memory_id)
                    if failed_row is not None:
                        failed_row.sync_status = "failed"
                        db.add(failed_row)
            else:
                job.status = "pending"
                job.next_attempt_at = datetime.now() + timedelta(seconds=BACKOFF_SECONDS[job.attempts - 1])
        job.updated_at = datetime.now()
        db.add(job)
        db.commit()
        return True
    finally:
        db.close()


async def run(stop: asyncio.Event, interval_seconds: float = 5.0) -> None:
    await asyncio.to_thread(recover_running_jobs)
    while not stop.is_set():
        processed = await asyncio.to_thread(process_one)
        if processed:
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass
