"""Full knowledge-agent loop executed inside one employee's Harness container."""

from __future__ import annotations

import json
import threading
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import config, execution_events, runtime_manager
from .chat import ChatResult, ToolCard
from .harness_token import issue_token
from .identity import resolve_identity
from .knowledge_registry import accessible_knowledge_bases
from .runtime_adapter import DockerHarnessRuntimeAdapter

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(employee_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(employee_id, threading.Lock())


def _prompt(
    *, employee: models.DigitalEmployee, profile: models.AgentProfile, requester: str,
    message: str, history: list[dict], knowledge: list[dict], depth: int,
) -> str:
    can_delegate = profile.identity_kind == "human_twin" and depth == 0
    envelope = {
        "identity": {
            "employee_id": employee.employee_no,
            "name": employee.name,
            "kind": profile.identity_kind,
            "persona": employee.role_prompt,
            "responsibilities": profile.responsibilities,
        },
        "requester_human_no": requester,
        "authorized_knowledge_bases": knowledge,
        "recent_conversation": history[-8:],
        "goal": message,
        "limits": {
            "knowledge_searches": 5,
            "delegation_depth": depth,
            "may_delegate_once": can_delegate,
            "delegate_only_to_role_employee": True,
        },
    }
    return (
        "你是企业内部的真实数字同事，请在本轮内自主完成任务。先理解问题，再决定是否调用知识工具；"
        "证据不足时可顺序检索其他已授权知识库，但最多五次。只能使用工具目录中出现的工具，"
        "不得猜测知识库内容。数字分身可向一名岗位数字员工委派一次；岗位数字员工不得委派。"
        "工具失败、无权限或证据不足时必须明确说明。最终直接给用户一份简洁、可核验的中文答复，"
        "不要输出内部思考过程、令牌或配置。\n\nTASK_ENVELOPE:\n"
        + json.dumps(envelope, ensure_ascii=False)
    )


def run_agent(
    db: Session,
    *,
    employee_id: str,
    requester_human_no: str,
    message: str,
    history: list[dict] | None = None,
    trace_id: str | None = None,
    depth: int = 0,
) -> ChatResult:
    employee = db.get(models.DigitalEmployee, employee_id)
    profile = db.get(models.AgentProfile, employee_id)
    subject = resolve_identity(db, employee_id)
    if employee is None or profile is None or subject is None or employee.status != "active":
        raise HTTPException(status_code=404, detail="数字员工不存在或未启用")
    if depth > 0 and profile.identity_kind != "role_employee":
        raise HTTPException(status_code=403, detail="委派目标必须是岗位数字员工")

    task_trace = trace_id or f"T-HAR-{uuid4().hex[:16].upper()}"
    execution = execution_events.execution_for_trace(db, task_trace)
    if execution is not None:
        execution_events.emit(
            db, execution.id, event_type="agent_started", stage="harness_started",
            status="running", actor_employee_id=employee_id,
            title=f"{employee.name} 已进入 Harness 执行环境",
            detail="正在根据职责和授权能力规划执行步骤",
        )
    knowledge = accessible_knowledge_bases(db, subject, requester_human_no)
    task = _prompt(
        employee=employee, profile=profile, requester=requester_human_no,
        message=message, history=history or [], knowledge=knowledge, depth=depth,
    )
    token = issue_token(
        employee_id=employee_id,
        requester_human_no=requester_human_no,
        trace_id=task_trace,
        depth=depth,
        ttl_seconds=300,
    )
    base_url = config.get("DWP_PLATFORM_TOOL_BASE_URL", "http://host.docker.internal:8000") or ""
    timeout = 120 if profile.identity_kind == "role_employee" else 240
    employee_lock = _lock(employee_id)
    if not employee_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail={"message": "数字员工正忙，请稍后重试", "retryable": True})
    try:
        runtime_manager.mark_busy(db, employee_id)
        result = DockerHarnessRuntimeAdapter(timeout=timeout).run(
            employee_id=employee_id,
            task_prompt=task,
            trace_id=task_trace,
            tool_token=token,
            tool_base_url=base_url,
        )
        if not result.ok:
            runtime_manager.mark_ready(db, employee_id, result.result or "Harness 执行失败")
            raise HTTPException(
                status_code=503,
                detail={"message": result.result or "Harness 执行失败", "retryable": True},
            )
        runtime_manager.mark_ready(db, employee_id)
        if execution is not None:
            execution_events.emit(
                db, execution.id, event_type="progress", stage="answer_preparing",
                status="running", actor_employee_id=employee_id,
                title="资料已整理，准备生成答复",
                detail="正在核对执行结果并整理为可读答案",
            )
    finally:
        employee_lock.release()

    audits = db.scalars(
        select(models.AuditEvent)
        .where(models.AuditEvent.trace_id == task_trace, models.AuditEvent.employee_id == employee_id)
        .order_by(models.AuditEvent.id)
    ).all()
    cards = [
        ToolCard(
            plugin_id=row.plugin_id or "knowledge",
            name=row.knowledge_base_id or row.plugin_id or "工具",
            decision=row.decision,
            reason=row.reason,
        )
        for row in audits
        if row.action == "read"
    ]
    return ChatResult(
        session_id=f"H-{task_trace}", trace_id=task_trace,
        message=result.result, tool_cards=cards,
    )
