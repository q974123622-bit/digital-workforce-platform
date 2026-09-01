"""Bounded colleague collaboration for the knowledge-first MVP.

A human twin may ask exactly one role employee. Role employees never delegate again.
The primary routing decision is produced from the live colleague directory; a small safe
fallback keeps the demo usable when the configured model is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import runtime_manager
from .chat import ChatOrchestrator, ChatResult, ToolCard
from .llm import LLMProvider


@dataclass(frozen=True)
class ColleagueDecision:
    action: str
    target_agent_id: str | None
    reason: str


def _available_colleagues(db: Session) -> list[tuple[models.DigitalEmployee, models.AgentProfile]]:
    rows = db.execute(
        select(models.DigitalEmployee, models.AgentProfile)
        .join(models.AgentProfile, models.AgentProfile.employee_id == models.DigitalEmployee.employee_no)
        .where(
            models.AgentProfile.identity_kind == "role_employee",
            models.DigitalEmployee.status == "active",
            models.DigitalEmployee.runtime_type == "harness",
        )
        .order_by(models.DigitalEmployee.employee_no)
    ).all()
    return [(employee, profile) for employee, profile in rows]


def _fallback_decision(message: str, colleagues: list[tuple[models.DigitalEmployee, models.AgentProfile]]) -> ColleagueDecision:
    lowered = message.lower()
    for employee, _ in colleagues:
        if employee.employee_no.lower() in lowered or employee.name.lower() in lowered:
            return ColleagueDecision("delegate", employee.employee_no, f"用户明确指定了{employee.name}")
    investment_words = ("证券", "投行", "投资", "融资融券", "ipo", "尽调", "承销", "保荐")
    general_words = ("内规", "外规", "制度", "监管", "it", "vpn", "邮箱", "企微", "办公", "流程", "申请")
    if any(word in lowered for word in investment_words):
        return ColleagueDecision("delegate", "AI-INVESTMENT", "问题属于投资分析同事的专业知识范围")
    if any(word in lowered for word in general_words):
        return ColleagueDecision("delegate", "AI-GENERAL", "问题属于综合制度或IT服务知识范围")
    return ColleagueDecision("answer_self", None, "优先由数字分身根据本人职责和项目资料回答")


def plan_colleague(
    db: Session,
    *,
    twin: models.DigitalEmployee,
    message: str,
    provider: LLMProvider,
) -> ColleagueDecision:
    colleagues = _available_colleagues(db)
    if not colleagues:
        return ColleagueDecision("answer_self", None, "当前没有可用的专业数字员工")
    directory = [
        {
            "agent_id": employee.employee_no,
            "name": employee.name,
            "responsibilities": profile.responsibilities,
            "knowledge_domains": profile.knowledge_domains,
            "accepts_tasks": profile.accepts_tasks,
        }
        for employee, profile in colleagues
    ]
    prompt = [
        {
            "role": "system",
            "content": (
                "你是数字分身的工作规划器。根据数字分身本人的职责和实时数字员工目录，决定由自己回答还是向一位数字员工求助。"
                "只允许 answer_self、delegate、clarify、refuse；一次最多选择一位同事，不能虚构目录外的同事。"
                "只输出JSON：action、target_agent_id、reason。"
            ),
            "source": "demo",
        },
        {
            "role": "user",
            "content": f"数字分身人设：{twin.role_prompt}\n数字员工目录：{directory}\n问题：{message}",
            "source": "demo",
        },
    ]
    allowed = {employee.employee_no for employee, _ in colleagues}
    try:
        raw = provider.structured_output(prompt, {"type": "object"})
        action = str(raw.get("action", "answer_self"))
        target = raw.get("target_agent_id")
        if action == "delegate" and target in allowed:
            return ColleagueDecision(action, str(target), str(raw.get("reason", "由专业数字员工处理")))
        if action in {"answer_self", "clarify", "refuse"}:
            return ColleagueDecision(action, None, str(raw.get("reason", "由数字分身处理")))
    except Exception:  # model outage/invalid JSON: deterministic safe fallback
        pass
    return _fallback_decision(message, colleagues)


def answer_as_twin(
    db: Session,
    *,
    conversation_id: str,
    requester_human_no: str,
    twin: models.DigitalEmployee,
    message: str,
    provider: LLMProvider,
    history: list[dict],
) -> tuple[ChatResult, models.DelegationRun]:
    decision = plan_colleague(db, twin=twin, message=message, provider=provider)
    trace_id = f"T-COL-{uuid4().hex[:12].upper()}"
    run = models.DelegationRun(
        id=f"D-{uuid4().hex[:12].upper()}",
        trace_id=trace_id,
        conversation_id=conversation_id,
        requester_human_no=requester_human_no,
        sender_employee_id=twin.employee_no,
        recipient_employee_id=decision.target_agent_id,
        action=decision.action,
        goal=message,
        reason=decision.reason,
        status="running",
    )
    db.add(run)
    db.commit()
    manage_runtime = provider.__class__.__name__ == "DeepSeekProvider"
    if manage_runtime:
        runtime_manager.mark_busy(db, twin.employee_no)
    orchestrator = ChatOrchestrator(provider)
    try:
        if decision.action == "delegate" and decision.target_agent_id:
            colleague = db.get(models.DigitalEmployee, decision.target_agent_id)
            profile = db.get(models.AgentProfile, decision.target_agent_id)
            if colleague is None or profile is None or profile.identity_kind != "role_employee":
                raise RuntimeError("规划器选择了不可用的数字员工")
            if manage_runtime:
                runtime_manager.mark_busy(db, colleague.employee_no)
            delegated = orchestrator.handle_message(
                db,
                employee_no=colleague.employee_no,
                message=message,
                session_id=None,
                system_context=(
                    f"这是{twin.name}代表员工{requester_human_no}发来的同事求助。"
                    "只处理自己的专业部分，给出可核验资料来源；不要继续委派给其他AI员工。"
                ),
                history_override=history[-8:],
                persist=False,
                trace_id=trace_id,
                requester_human_no=requester_human_no,
            )
            if manage_runtime:
                runtime_manager.mark_ready(db, colleague.employee_no)
            message_text = f"我向{colleague.name}确认了一下。\n\n{delegated.message}"
            cards = [
                ToolCard(
                    plugin_id=f"delegate:{colleague.employee_no}",
                    name=f"咨询 {colleague.name}",
                    decision="allow",
                    reason=decision.reason,
                ),
                *delegated.tool_cards,
            ]
            result = ChatResult(
                session_id=f"G-{conversation_id}",
                trace_id=trace_id,
                message=message_text,
                tool_cards=cards,
                policy_denied=delegated.policy_denied,
            )
        elif decision.action == "clarify":
            result = ChatResult(
                session_id=f"G-{conversation_id}", trace_id=trace_id,
                message="这件事有两种理解。你更想了解具体制度流程，还是某个项目里的实际做法？",
            )
        elif decision.action == "refuse":
            result = ChatResult(
                session_id=f"G-{conversation_id}", trace_id=trace_id,
                message="这件事超出了我和当前数字员工的授权范围，我不能替你查询或处理。",
            )
        else:
            result = orchestrator.handle_message(
                db,
                employee_no=twin.employee_no,
                message=message,
                session_id=None,
                history_override=history,
                persist=False,
                trace_id=trace_id,
                requester_human_no=requester_human_no,
            )
        run.status = "completed"
        run.evidence = [card.plugin_id for card in result.tool_cards]
        if manage_runtime:
            runtime_manager.mark_ready(db, twin.employee_no)
        db.add(run)
        db.commit()
        return result, run
    except Exception as exc:
        run.status = "failed"
        if manage_runtime:
            runtime_manager.mark_ready(db, twin.employee_no, str(exc))
        if manage_runtime and decision.target_agent_id:
            runtime_manager.mark_ready(db, decision.target_agent_id, str(exc))
        db.add(run)
        db.commit()
        raise
