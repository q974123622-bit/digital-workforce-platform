"""Durable, sanitized execution progress for the workplace SSE surface."""

from __future__ import annotations

import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models

ACTIVE_STATUSES = ("queued", "running", "streaming", "waiting_approval")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
ANSWER_CHUNK_SIZE = 32

# 同一执行记录的并发写保护：主流程线程与容器内 MCP 回调（知识检索/委派）可能
# 同时向同一条 AgentExecution 写事件，若不串行化会引发 event_seq 竞态与 SQLite
# 写锁冲突。用可重入锁按 execution_id 串行化，不同执行记录之间互不阻塞。
_EXECUTION_LOCKS: dict[str, threading.RLock] = {}
_EXECUTION_LOCKS_GUARD = threading.Lock()


def _execution_lock(execution_id: str) -> threading.RLock:
    with _EXECUTION_LOCKS_GUARD:
        return _EXECUTION_LOCKS.setdefault(execution_id, threading.RLock())


def prune_history(db: Session, conversation_id: str, retain: int = 100) -> int:
    old_ids = list(db.scalars(
        select(models.AgentExecution.id).where(
            models.AgentExecution.conversation_id == conversation_id,
            models.AgentExecution.status.in_(TERMINAL_STATUSES),
        ).order_by(models.AgentExecution.started_at.desc()).offset(retain)
    ).all())
    if not old_ids:
        return 0
    db.query(models.AgentExecutionEvent).filter(
        models.AgentExecutionEvent.execution_id.in_(old_ids)
    ).delete(synchronize_session=False)
    db.query(models.AgentExecution).filter(
        models.AgentExecution.id.in_(old_ids)
    ).delete(synchronize_session=False)
    db.commit()
    return len(old_ids)


def create_execution(
    db: Session, *, conversation_id: str, trigger_message_seq: int, primary_employee_id: str,
    conversation_generation: int = 0, retry_of_execution_id: str | None = None,
) -> models.AgentExecution:
    prune_history(db, conversation_id)
    execution_id = f"EX-{uuid4().hex[:16].upper()}"
    row = models.AgentExecution(
        id=execution_id,
        conversation_id=conversation_id,
        trigger_message_seq=trigger_message_seq,
        trace_id=f"T-EX-{uuid4().hex[:16].upper()}",
        primary_employee_id=primary_employee_id,
        conversation_generation=conversation_generation,
        retry_of_execution_id=retry_of_execution_id,
        status="queued",
        stage="queued",
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise RuntimeError("ACTIVE_EXECUTION_CONFLICT")
    db.refresh(row)
    emit(
        db, row.id, event_type="progress", stage="queued", status="queued",
        actor_employee_id=primary_employee_id, title="任务已进入执行队列",
        detail="等待AI员工运行实例接收任务",
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


def fail_orphaned_executions(
    db: Session, *, stale_after_seconds: int | None = None, conversation_id: str | None = None,
) -> int:
    """Fail work that cannot have a live in-process worker after restart or timeout.

    ``conversation_id`` 非空时只处理该会话的活跃执行。调用方（用户每次发起新消息
    前的清理）必须传它：不带过滤会把**其他会话**正在跑的长任务一并判成
    ``AGENT_EXECUTION_STALE`` 失败。不传则保持原语义（启动恢复时清理全部孤儿）。
    """
    query = select(models.AgentExecution).where(models.AgentExecution.status.in_(ACTIVE_STATUSES))
    if conversation_id is not None:
        query = query.where(models.AgentExecution.conversation_id == conversation_id)
    if stale_after_seconds is not None:
        query = query.where(
            models.AgentExecution.updated_at < datetime.now() - timedelta(seconds=stale_after_seconds)
        )
    rows = db.scalars(query).all()
    for row in rows:
        row.error_code = "AGENT_PROCESS_RESTARTED" if stale_after_seconds is None else "AGENT_EXECUTION_STALE"
        row.error_message = "服务已重启，本次执行未完成，可重新尝试" if stale_after_seconds is None else "本次执行已超时，可重新尝试"
        row.retryable = True
        db.add(row)
        db.commit()
        emit(
            db, row.id, event_type="error", stage="failed", status="failed",
            actor_employee_id=row.primary_employee_id, title="执行未完成",
            detail=row.error_message,
            payload={"code": row.error_code, "message": row.error_message, "retryable": True},
        )
    return len(rows)


def execution_for_trace(db: Session, trace_id: str) -> models.AgentExecution | None:
    return db.scalar(select(models.AgentExecution).where(models.AgentExecution.trace_id == trace_id))


def _next_event_seq(db: Session, execution_id: str) -> int:
    return (db.scalar(
        select(func.max(models.AgentExecutionEvent.event_seq)).where(
            models.AgentExecutionEvent.execution_id == execution_id
        )
    ) or 0) + 1


def _stored_status(db: Session, execution_id: str) -> str | None:
    """直接读库里的执行状态，绕过 ORM identity map。

    worker 会话是长期存活的，`db.get()` 会返回取消/完成**之前**加载的旧对象；
    只看内存状态会让「取消」被下一次事件静默翻回 running、甚至翻回 completed
    （实测：点停止后 worker 仍把答复落库并把状态改回 completed）。
    """
    return db.scalar(
        select(models.AgentExecution.status).where(models.AgentExecution.id == execution_id)
    )


def is_cancelled(db: Session, execution_id: str) -> bool:
    """以库里的状态为准判断执行是否已被取消（供 worker 写入前校验）。"""
    return _stored_status(db, execution_id) == "cancelled"


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
    with _execution_lock(execution_id):
        execution = db.get(models.AgentExecution, execution_id)
        if execution is None:
            raise RuntimeError("执行记录不存在")
        # 状态一律以库为准（见 _stored_status）：内存里的旧对象会把取消/终态翻回去。
        current_status = _stored_status(db, execution_id) or execution.status
        if current_status == "cancelled":
            raise RuntimeError("执行记录不存在或已取消")
        # 终态锁定：已完成/已失败的执行，迟到的非终态事件只落轨迹、不改状态。
        # 否则「完成/失败」瞬间仍在飞的工具回调（如 search_knowledge）会把终态翻回
        # running，前端 SSE 的终态退出条件不再成立，输入框会一直锁死。
        terminal_locked = current_status in TERMINAL_STATUSES
        row = models.AgentExecutionEvent(
            execution_id=execution_id,
            event_seq=_next_event_seq(db, execution_id),
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
        if not terminal_locked:
            execution.stage = stage
            execution.status = status if status in (*ACTIVE_STATUSES, *TERMINAL_STATUSES) else execution.status
            execution.updated_at = datetime.now()
            if execution.status in TERMINAL_STATUSES:
                execution.completed_at = datetime.now()
        db.add_all([execution, row])
        db.commit()
        db.refresh(row)
        return row


def cancel_execution(db: Session, execution_id: str) -> models.AgentExecution | None:
    """停止单条执行（幂等）：置 cancelled 并终止容器内进程。

    与 ``cancel_for_conversation``（清空/删除会话时批量取消）不同，这里只处理一条执行，
    供前端「停止执行」按钮使用；已是终态时原样返回，不报错（重复点击安全）。
    """
    with _execution_lock(execution_id):
        execution = db.get(models.AgentExecution, execution_id)
        if execution is None:
            return None
        if execution.status not in ACTIVE_STATUSES:
            return execution
        now = datetime.now()
        execution.status = "cancelled"
        execution.stage = "cancelled"
        execution.updated_at = now
        execution.completed_at = now
        # 取消事件必须绕开 emit 的「已取消不再写入」守卫，否则前端收不到终止信号。
        db.add(models.AgentExecutionEvent(
            execution_id=execution_id,
            event_seq=_next_event_seq(db, execution_id),
            event_type="cancelled",
            actor_employee_id=execution.primary_employee_id,
            stage="cancelled",
            status="cancelled",
            title="已停止执行",
            detail="执行已按你的操作终止，未完成的步骤不会继续",
            payload={},
        ))
        db.add(execution)
        db.commit()
    # 终止容器内仍在跑的 dsh 进程并释放运行时，否则取消后新任务会一直排队。
    from . import runtime_manager
    if execution.primary_employee_id:
        try:
            runtime_manager.terminate_task(execution.primary_employee_id)
            runtime_manager.mark_ready(db, execution.primary_employee_id)
        except Exception:  # noqa: BLE001 - 回收失败不应让取消操作报错
            pass
    db.refresh(execution)
    return execution


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


def _json_safe_tool_cards(tool_cards: list) -> list[dict]:
    """把 ToolCard 等 dataclass 对象转成可 JSON 序列化的 dict。

    ``complete()`` 的 tool_cards 来自 Harness 执行结果，是 ``ToolCard`` 对象列表；
    若直接写入 payload（JSON 列），会在 commit 时抛
    ``TypeError: Object of type ToolCard is not JSON serializable``。
    """
    out: list[dict] = []
    for card in tool_cards:
        if is_dataclass(card) and not isinstance(card, type):
            out.append(asdict(card))
        elif isinstance(card, dict):
            out.append(card)
        else:
            out.append({"raw": str(card)})
    return out


def complete(
    db: Session, execution_id: str, *, actor_employee_id: str,
    message_id: int | None, trace_id: str, tool_cards: list,
) -> None:
    # 已停止的执行不得被 worker 收尾成 completed（否则用户看到「已停止」又自己变回完成）。
    if is_cancelled(db, execution_id):
        return
    emit(
        db, execution_id, event_type="answer_done", stage="completed", status="completed",
        actor_employee_id=actor_employee_id, title="执行完成", payload={
            "message_id": message_id, "trace_id": trace_id,
            "tool_cards": _json_safe_tool_cards(tool_cards),
        },
    )


def fail(db: Session, execution_id: str, exc: Exception) -> None:
    with _execution_lock(execution_id):
        # 异常发生时当前事务可能已处于失败状态（如 SQLite 撞锁后 commit 抛错），
        # 先回滚清理，否则后续补救写入会因 session 坏状态而再次失败、被静默吞掉。
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 - 回滚失败不应阻断补救写入
            pass
        execution = db.get(models.AgentExecution, execution_id)
        if execution is None:
            return
        # 以库为准：worker 会话里可能还持有取消前的旧对象。
        if (_stored_status(db, execution_id) or execution.status) == "cancelled":
            return
        name = exc.__class__.__name__
        detail = getattr(exc, "detail", "")
        detail_text = str(detail).lower()
        timed_out = name == "TimeoutExpired" or "timeout" in detail_text or "超时" in detail_text
        code = "HARNESS_TIMEOUT" if timed_out else "AGENT_EXECUTION_FAILED"
        message = "AI员工响应超时，可重新尝试" if code == "HARNESS_TIMEOUT" else "AI员工执行失败，可稍后重试"
        execution.error_code = code
        execution.error_message = message
        execution.retryable = True
        db.add(execution)
        db.commit()
        # 把真实异常类型与信息一并落库，避免"抱歉"掩盖故障根因（如 database is locked）。
        emit(
            db, execution_id, event_type="error", stage="failed", status="failed",
            actor_employee_id=execution.primary_employee_id, title="执行失败", detail=message,
            payload={
                "code": code, "message": message, "retryable": True,
                "exc_type": name, "exc_detail": str(exc)[:300],
            },
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
    # 终止仍占用运行时的 dsh 进程并释放员工运行时，避免取消后新任务一直排队。
    from . import runtime_manager
    for row in rows:
        if not row.primary_employee_id:
            continue
        try:
            runtime_manager.terminate_task(row.primary_employee_id)
            runtime_manager.mark_ready(db, row.primary_employee_id)
        except Exception:
            continue
