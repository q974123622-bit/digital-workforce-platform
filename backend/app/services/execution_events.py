"""Durable, sanitized execution progress for the workplace SSE surface."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models

ACTIVE_STATUSES = ("queued", "running", "streaming", "waiting_approval")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
ANSWER_CHUNK_SIZE = 32


def create_execution(
    db: Session, *, conversation_id: str, trigger_message_seq: int, primary_employee_id: str
) -> models.AgentExecution:
    execution_id = f"EX-{uuid4().hex[:16].upper()}"
    row = models.AgentExecution(
        id=execution_id,
        conversation_id=conversation_id,
        trigger_message_seq=trigger_message_seq,
        trace_id=f"T-EX-{uuid4().hex[:16].upper()}",
        primary_employee_id=primary_employee_id,
        status="queued",
        stage="queued",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    emit(
        db, row.id, event_type="progress", stage="queued", status="queued",
        actor_employee_id=primary_employee_id, title="任务已进入执行队列",
        detail="等待数字员工运行实例接收任务",
    )
    return row


def active_execution(db: Session, conversation_id: str) -> models.AgentExecution | None:
    return db.scalar(
        select(models.AgentExecution)
        .where(
            models.AgentExecution.conversation_id == conversation_id,
            models.AgentExecution.status.in_(ACTIVE_STATUSES),
        )
        .order_by(models.AgentExecution.started_at.desc())
        .limit(1)
    )


def execution_for_trace(db: Session, trace_id: str) -> models.AgentExecution | None:
    return db.scalar(select(models.AgentExecution).where(models.AgentExecution.trace_id == trace_id))


def emit(
    db: Session,
    execution_id: str,
    *,
    event_type: str,
    stage: str,
    status: str,
    title: str,
    detail: str = "",
    actor_employee_id: str = "",
    knowledge_base_id: str | None = None,
    target_agent_id: str | None = None,
    hit_count: int | None = None,
    payload: dict | None = None,
) -> models.AgentExecutionEvent:
    execution = db.get(models.AgentExecution, execution_id)
    if execution is None or execution.status == "cancelled":
        raise RuntimeError("执行记录不存在或已取消")
    next_seq = (db.scalar(
        select(func.max(models.AgentExecutionEvent.event_seq)).where(
            models.AgentExecutionEvent.execution_id == execution_id
        )
    ) or 0) + 1
    row = models.AgentExecutionEvent(
        execution_id=execution_id,
        event_seq=next_seq,
        event_type=event_type,
        actor_employee_id=actor_employee_id,
        stage=stage,
        status=status,
        title=title[:160],
        detail=detail[:300],
        knowledge_base_id=knowledge_base_id,
        target_agent_id=target_agent_id,
        hit_count=hit_count,
        payload=payload or {},
    )
    execution.stage = stage
    execution.status = status if status in (*ACTIVE_STATUSES, *TERMINAL_STATUSES) else execution.status
    execution.updated_at = datetime.now()
    if execution.status in TERMINAL_STATUSES:
        execution.completed_at = datetime.now()
    db.add_all([execution, row])
    db.commit()
    db.refresh(row)
    return row


def emit_answer_chunks(
    db: Session,
    execution_id: str,
    *,
    actor_employee_id: str,
    answer: str,
) -> None:
    emit(
        db, execution_id, event_type="progress", stage="answer", status="streaming",
        actor_employee_id=actor_employee_id, title="资料已整理，正在生成答复",
    )
    for offset in range(0, len(answer), ANSWER_CHUNK_SIZE):
        emit(
            db, execution_id, event_type="answer_chunk", stage="answer", status="streaming",
            actor_employee_id=actor_employee_id, title="", payload={
                "text": answer[offset:offset + ANSWER_CHUNK_SIZE],
                "offset": offset,
            },
        )


def complete(
    db: Session, execution_id: str, *, actor_employee_id: str,
    message_id: int | None, trace_id: str, tool_cards: list,
) -> None:
    emit(
        db, execution_id, event_type="answer_done", stage="completed", status="completed",
        actor_employee_id=actor_employee_id, title="执行完成", payload={
            "message_id": message_id, "trace_id": trace_id, "tool_cards": tool_cards,
        },
    )


def fail(db: Session, execution_id: str, exc: Exception) -> None:
    execution = db.get(models.AgentExecution, execution_id)
    if execution is None or execution.status == "cancelled":
        return
    name = exc.__class__.__name__
    detail = getattr(exc, "detail", "")
    detail_text = str(detail).lower()
    timed_out = name == "TimeoutExpired" or "timeout" in detail_text or "超时" in detail_text
    code = "HARNESS_TIMEOUT" if timed_out else "AGENT_EXECUTION_FAILED"
    message = "数字员工响应超时，可重新尝试" if code == "HARNESS_TIMEOUT" else "数字员工执行失败，可稍后重试"
    execution.error_code = code
    execution.error_message = message
    execution.retryable = True
    db.add(execution)
    db.commit()
    emit(
        db, execution_id, event_type="error", stage="failed", status="failed",
        actor_employee_id=execution.primary_employee_id, title="执行失败", detail=message,
        payload={"code": code, "message": message, "retryable": True},
    )


def cancel_for_conversation(db: Session, conversation_id: str) -> None:
    rows = db.scalars(
        select(models.AgentExecution).where(
            models.AgentExecution.conversation_id == conversation_id,
            models.AgentExecution.status.in_(ACTIVE_STATUSES),
        )
    ).all()
    for row in rows:
        row.status = "cancelled"
        row.stage = "cancelled"
        row.updated_at = datetime.now()
        row.completed_at = datetime.now()
        db.add(row)
    db.commit()
