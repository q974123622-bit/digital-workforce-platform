"""工作线 B：统一记忆运行适配层。

封装工作线 A 的三个接口（``capture_turn`` / ``retrieve_for_prompt`` /
``render_prompt_context``），统一处理配置开关、异常降级和审计元数据，
供直接聊天与职场/群聊共用。

铁律：
- 记忆失败绝不阻断基础聊天；读取失败返回空上下文，写入失败返回 None。
- 审计正文只记录数量 / memory IDs / 字符数，不复制完整记忆正文。
- 数据库永远保存用户原文；拼接的记忆上下文只存在于本轮模型请求。
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .. import models
from . import config
from .memory_service import capture_turn, render_prompt_context, retrieve_for_prompt


@dataclass(frozen=True)
class PreparedMemoryContext:
    """已准备好注入模型请求的记忆上下文；无命中或失败时 ``text`` 为空。"""

    text: str = ""
    memory_ids: tuple[int, ...] = ()
    chars: int = 0


def _audit(
    db: Session,
    *,
    trace_id: str,
    employee_id: str,
    action: str,
    decision: str,
    result_summary: str | None = None,
) -> None:
    """写一条记忆运行审计；审计本身失败不影响调用方。"""
    try:
        event = models.AuditEvent(
            trace_id=trace_id or "NO-TRACE",
            actor=employee_id,
            employee_id=employee_id,
            plugin_id="memory",
            action=action,
            decision=decision,
            result_summary=result_summary,
        )
        db.add(event)
        db.commit()
    except Exception:
        db.rollback()


def prepare_memory_context(
    db: Session,
    *,
    owner_employee_no: str,
    query: str,
    current_session_id: str,
    trace_id: str,
) -> PreparedMemoryContext:
    """调用 A 检索旧记忆并生成安全上下文；任何失败都返回空对象。

    读取顺序固定为 ``retrieve_for_prompt → render_prompt_context``，
    并将配置的 max items / max chars 传给 A。
    """
    if not config.memory_enabled():
        return PreparedMemoryContext()

    try:
        hits = retrieve_for_prompt(
            db,
            owner_employee_no=owner_employee_no,
            query=query,
            current_session_id=current_session_id,
            limit=config.memory_max_items(),
            max_chars=config.memory_max_chars(),
        )
        text = render_prompt_context(hits, max_chars=config.memory_max_chars())
    except Exception:
        _audit(
            db,
            trace_id=trace_id,
            employee_id=owner_employee_no,
            action="memory.read_auto",
            decision="error",
            result_summary="read_failed",
        )
        return PreparedMemoryContext()

    prepared = PreparedMemoryContext(
        text=text,
        memory_ids=tuple(hit.memory_id for hit in hits),
        chars=len(text),
    )
    if text:
        ids = ",".join(str(memory_id) for memory_id in prepared.memory_ids)
        _audit(
            db,
            trace_id=trace_id,
            employee_id=owner_employee_no,
            action="memory.read_auto",
            decision="allow",
            result_summary=f"hits={len(prepared.memory_ids)} ids={ids} chars={prepared.chars}",
        )
    return prepared


def capture_turn_safely(
    db: Session,
    *,
    owner_employee_no: str,
    source_type: str,
    source_session_id: str,
    source_ref: str,
    user_text: str,
    assistant_text: str,
    trace_id: str,
) -> int | None:
    """在成功回答落库后调用 A 沉淀记忆；任何失败都返回 None，不阻断回答。"""
    if not config.memory_enabled():
        return None

    try:
        memory_id = capture_turn(
            db,
            owner_employee_no=owner_employee_no,
            source_type=source_type,
            source_session_id=source_session_id,
            source_ref=source_ref,
            user_text=user_text,
            assistant_text=assistant_text,
            trace_id=trace_id,
        )
    except Exception:
        _audit(
            db,
            trace_id=trace_id,
            employee_id=owner_employee_no,
            action="memory.capture",
            decision="error",
            result_summary=f"source_ref={source_ref}",
        )
        return None

    if memory_id is not None:
        _audit(
            db,
            trace_id=trace_id,
            employee_id=owner_employee_no,
            action="memory.capture",
            decision="allow",
            result_summary=f"memory_id={memory_id} source_ref={source_ref}",
        )
    return memory_id
