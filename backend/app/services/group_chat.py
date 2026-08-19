"""职场会话编排（Sprint 7）。

统一入口 send_conversation_message：
- 私聊：直接由唯一成员回复；
- 群聊：先由分身（组织者）判断消息是「任务」还是「闲聊」——
  - 任务：追加分身受理消息，走 TeamTaskOrchestrator 拆解 → 指派成员 → Gateway 执行 → 审批 → Leader 汇总；
  - 闲聊：只由一位成员回复一次（点名某成员则那位回，否则分身回）。

每个成员的对话仍复用 ChatOrchestrator（各自走 Policy → Gateway → 审计），
单成员失败降级为提示消息并继续，全部失败才返回 503。
"""

import time
from datetime import datetime, timedelta
from typing import Callable
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from . import config
from .agentteams_gateway import AgentTeamsGateway, AgentTeamsUnavailableError
from .chat import ChatOrchestrator
from .gateway import write_audit
from .identity import resolve_identity
from .llm import DeepSeekProvider, LLMProvider, LLMUnavailableError
from .team_orchestrator import TeamTaskOrchestrator

_TASK_STATUS_LABEL = {
    "pending": "待执行",
    "running": "执行中",
    "approval": "待审批",
    "completed": "已完成",
    "denied": "已拒绝",
    "failed": "失败",
}


def _append(
    db: Session,
    *,
    conversation_id: str,
    participant_no: str,
    participant_name: str,
    role: str,
    content: str,
    seq: int,
    tool_cards: list | None = None,
) -> models.ConversationMessage:
    msg = models.ConversationMessage(
        conversation_id=conversation_id,
        participant_no=participant_no,
        participant_name=participant_name,
        role=role,
        content=content,
        tool_cards=tool_cards or [],
        seq=seq,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def _next_seq(db: Session, conversation_id: str) -> int:
    current = db.scalar(
        select(func.max(models.ConversationMessage.seq)).where(
            models.ConversationMessage.conversation_id == conversation_id
        )
    )
    return (current or 0) + 1


def _history_messages(db: Session, conversation_id: str) -> list[dict]:
    """把会话消息转成模型上下文（source=demo）。

    一律不加「【名字】」前缀：UI 已按参与者展示姓名，前缀只会诱导模型模仿该格式。
    """
    rows = db.scalars(
        select(models.ConversationMessage)
        .where(models.ConversationMessage.conversation_id == conversation_id)
        .order_by(models.ConversationMessage.seq)
    ).all()
    out: list[dict] = []
    for row in rows:
        role = "user" if row.role == "user" else "assistant"
        out.append({"role": role, "content": row.content, "source": "demo"})
    return out


def append_user_message(
    db: Session,
    *,
    conversation: models.Conversation,
    actor_no: str,
    content: str,
) -> models.ConversationMessage:
    """把员工本人的消息写入会话（统一落库点）。"""
    actor = db.get(models.HumanEmployee, actor_no)
    if actor is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _append(
        db,
        conversation_id=conversation.id,
        participant_no=actor_no,
        participant_name=actor.name,
        role="user",
        content=content,
        seq=_next_seq(db, conversation.id),
    )


def member_replies(
    db: Session,
    *,
    conversation: models.Conversation,
    actor_no: str,
    content: str,
    provider: LLMProvider | None = None,
    only: str | None = None,
) -> list[models.ConversationMessage]:
    """让成员按顺序回应（假定用户消息已落库）；only 指定时仅该成员回复。

    单成员失败降级为「（XX 暂时无法响应，已跳过）」并继续；全部失败返回 503。
    """
    participants = conversation.participants or []
    if not participants:
        raise HTTPException(status_code=400, detail="会话没有成员")

    orchestrator = ChatOrchestrator(provider or DeepSeekProvider())
    member_names: dict[str, str] = {}
    for participant in participants:
        emp = db.get(models.DigitalEmployee, participant["employee_no"])
        member_names[participant["employee_no"]] = emp.name if emp else participant["employee_no"]

    targets = [p for p in participants if only is None or p["employee_no"] == only]
    if not targets:
        raise HTTPException(status_code=400, detail="指定的回复成员不在会话中")

    history = _history_messages(db, conversation.id)
    is_group = conversation.kind == "group"
    appended: list[models.ConversationMessage] = []
    failed = 0
    for participant in targets:
        employee_no = participant["employee_no"]
        name = member_names.get(employee_no, employee_no)
        system_context = ""
        if is_group:
            system_context = (
                "【协作空间】你正在与同事协作完成一条任务。以下是到目前为止的对话记录，"
                f"请结合自己的职责给出你的专业部分。当前成员：{', '.join(member_names.values())}。"
                "不要复述或引用其他人已经说过的内容；回复直接以内容开头，"
                "不要写任何人的名字或【】前缀，不要使用任何 Markdown 符号。"
            )
        try:
            result = orchestrator.handle_message(
                db,
                employee_no=employee_no,
                message=content,
                session_id=None,
                system_context=system_context,
                history_override=history,
                persist=False,
                trace_id=f"T-GRP-{conversation.id}-{employee_no}",
            )
            cards = [
                {
                    "plugin_id": card.plugin_id,
                    "name": card.name,
                    "decision": card.decision,
                    "policy_id": card.policy_id,
                    "reason": card.reason,
                }
                for card in result.tool_cards
            ]
            appended.append(
                _append(
                    db,
                    conversation_id=conversation.id,
                    participant_no=employee_no,
                    participant_name=name,
                    role="assistant",
                    content=result.message,
                    seq=_next_seq(db, conversation.id),
                    tool_cards=cards,
                )
            )
        except LLMUnavailableError:
            failed += 1
            appended.append(
                _append(
                    db,
                    conversation_id=conversation.id,
                    participant_no=employee_no,
                    participant_name=name,
                    role="assistant",
                    content=f"（{name} 暂时无法响应，已跳过）",
                    seq=_next_seq(db, conversation.id),
                )
            )
        history = _history_messages(db, conversation.id)

    if failed == len(targets):
        raise HTTPException(status_code=503, detail="LLM_UNAVAILABLE：成员暂时无法响应")
    return appended


def send_group_message(
    db: Session,
    *,
    conversation: models.Conversation,
    actor_no: str,
    content: str,
    provider: LLMProvider | None = None,
) -> models.Conversation:
    """兼容入口：落用户消息并让所有成员顺序回复。"""
    append_user_message(db, conversation=conversation, actor_no=actor_no, content=content)
    member_replies(db, conversation=conversation, actor_no=actor_no, content=content, provider=provider)
    conversation.updated_at = datetime.now()
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def classify_task_request(
    db: Session,
    *,
    conversation: models.Conversation,
    content: str,
    provider: LLMProvider | None = None,
) -> bool:
    """分身判断消息是「任务」还是「闲聊」；LLM 失败默认 False（闲聊）。"""
    leader = _organizer(db, conversation)
    if leader is None:
        return False
    subject = resolve_identity(db, leader.employee_no)
    if subject is None:
        return False

    roster = "、".join(
        f"{p['name']}（{p['employee_no']}）" for p in _participant_names(db, conversation)
    )
    persona = ChatOrchestrator(provider or DeepSeekProvider())._system_prompt(db, subject)
    prompt = [
        {
            "role": "system",
            "content": (
                f"{persona}\n"
                "【任务判断】你是协作空间的组织者。判断用户消息是需要同事们协作执行的任务，"
                "还是普通闲聊。任务的特征：包含具体待办事项、需要查询/整理/执行/开通/生成等动作。"
                '只输出 JSON：{"action":"task"} 或 {"action":"chat"}，不要输出其他内容。'
            ),
            "source": "demo",
        },
        {
            "role": "user",
            "content": f"协作空间成员：{roster}\n用户消息：{content}",
            "source": "demo",
        },
    ]
    try:
        raw = (provider or DeepSeekProvider()).structured_output(prompt, {"type": "object"})
        return raw.get("action") == "task"
    except (LLMUnavailableError, Exception):
        return False


def _organizer(db: Session, conversation: models.Conversation) -> models.DigitalEmployee | None:
    """组织者 = 分身（role=organizer 或 type=twin）。"""
    for participant in conversation.participants or []:
        emp = db.get(models.DigitalEmployee, participant["employee_no"])
        if emp is not None and (emp.type == "twin" or participant.get("role") == "organizer"):
            return emp
    return None


def _participant_names(db: Session, conversation: models.Conversation) -> list[dict]:
    out: list[dict] = []
    for participant in conversation.participants or []:
        emp = db.get(models.DigitalEmployee, participant["employee_no"])
        out.append({"employee_no": participant["employee_no"], "name": emp.name if emp else participant["employee_no"]})
    return out


def _mention_target(db: Session, conversation: models.Conversation, content: str) -> str | None:
    """闲聊时点名回复：消息命中某成员的名字或工号则那位回，否则 None（分身回）。"""
    for participant in _participant_names(db, conversation):
        if participant["name"] and participant["name"] in content:
            return participant["employee_no"]
        if participant["employee_no"] in content:
            return participant["employee_no"]
    return None


def send_conversation_message(
    db: Session,
    *,
    conversation: models.Conversation,
    actor_no: str,
    content: str,
    provider: LLMProvider | None = None,
    orchestrator: TeamTaskOrchestrator | None = None,
    classifier: Callable | None = None,
) -> models.Conversation:
    """统一发送入口：私聊单成员回复；群聊由分身判断任务/闲聊。"""
    user_msg = append_user_message(db, conversation=conversation, actor_no=actor_no, content=content)

    if conversation.kind != "group":
        member_replies(db, conversation=conversation, actor_no=actor_no, content=content, provider=provider)
    else:
        decide = classifier if classifier is not None else classify_task_request
        is_task = decide(db, conversation=conversation, content=content, provider=provider)
        if is_task:
            leader = _organizer(db, conversation)
            existing = _find_recent_task(db, conversation.id, content)
            if existing is not None:
                # 同请求去重：不重复建任务，只提示已有任务状态
                label = _TASK_STATUS_LABEL.get(existing.status, existing.status)
                if leader is not None:
                    _append(
                        db,
                        conversation_id=conversation.id,
                        participant_no=leader.employee_no,
                        participant_name=leader.name,
                        role="assistant",
                        content=f"这条和刚才重复啦，任务 {existing.id} 状态：{label}，看上方卡片即可。",
                        seq=_next_seq(db, conversation.id),
                    )
            else:
                # AgentTeams 优先（auto），失败自动降级内置编排
                if config.team_backend_mode() != "builtin":
                    at_task = _try_agentteams_task(
                        db,
                        conversation=conversation,
                        leader=leader,
                        actor_no=actor_no,
                        request=content,
                        trigger_seq=user_msg.seq,
                    )
                    if at_task is not None:
                        conversation.updated_at = datetime.now()
                        db.add(conversation)
                        db.commit()
                        db.refresh(conversation)
                        return conversation
                orch = orchestrator or TeamTaskOrchestrator(provider or DeepSeekProvider())
                orch.create_conversation_task(
                    db,
                    conversation=conversation,
                    actor_no=actor_no,
                    request=content,
                    trigger_seq=user_msg.seq,
                )
        else:
            target = _mention_target(db, conversation, content)
            if target is None:
                leader = _organizer(db, conversation)
                target = leader.employee_no if leader is not None else None
            member_replies(
                db,
                conversation=conversation,
                actor_no=actor_no,
                content=content,
                provider=provider,
                only=target,
            )

    conversation.updated_at = datetime.now()
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def _try_agentteams_task(
    db: Session,
    *,
    conversation: models.Conversation,
    leader: models.DigitalEmployee | None,
    actor_no: str,
    request: str,
    trigger_seq: int | None,
) -> models.TaskRun | None:
    """尝试经 AgentTeams 团队房间执行任务；任何失败返回 None（上层降级内置编排）。"""
    room_id = config.get(config.AGENTTEAMS_ROOM_ID)
    if not room_id:
        return None
    gateway = AgentTeamsGateway()
    try:
        gateway.send_message(room_id, f"[平台任务] {request}（请求者 {actor_no}）")
        write_audit(
            db,
            trace_id=f"AT-{int(time.time() * 1000)}",
            employee_id=leader.employee_no if leader else actor_no,
            plugin_id="agentteams:send",
            action="send",
            decision="allow",
            reason="任务已发送至 AgentTeams 团队房间",
            result_summary=request[:200],
        )
        # 限时轮询回收完成汇报（最多 90 秒，每 5 秒一次）
        report: str | None = None
        deadline = time.time() + 90
        while time.time() < deadline:
            msgs = gateway.poll_messages(room_id)
            report = gateway.parse_completion(msgs, request[:12])
            if report:
                break
            time.sleep(5)
        if not report:
            return None
        task_id = f"T-{datetime.now():%Y%m%d}-{uuid4().hex[:6].upper()}"
        run = models.TaskRun(
            id=task_id,
            team_id=conversation.id,
            conversation_id=conversation.id,
            trigger_message_seq=trigger_seq,
            trace_id=task_id,
            request=request,
            status="completed",
            subtasks=[
                {
                    "worker_id": leader.employee_no if leader else "agentteams",
                    "worker_no": leader.employee_no if leader else "agentteams",
                    "summary": request,
                    "plugin_ids": [],
                    "status": "completed",
                    "result": report,
                    "approval": None,
                }
            ],
            summary=report,
            source="agentteams",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        if leader is not None:
            _append(
                db,
                conversation_id=conversation.id,
                participant_no=leader.employee_no,
                participant_name=leader.name,
                role="assistant",
                content=report,
                seq=_next_seq(db, conversation.id),
            )
        write_audit(
            db,
            trace_id=task_id,
            employee_id=leader.employee_no if leader else actor_no,
            plugin_id="agentteams:receive",
            action="receive",
            decision="allow",
            reason="已回收 AgentTeams 团队执行汇报",
            result_summary=report[:200],
        )
        return run
    except (AgentTeamsUnavailableError, Exception):  # noqa: BLE001
        return None
    finally:
        gateway.close()


def _find_recent_task(
    db: Session,
    conversation_id: str,
    request: str,
) -> models.TaskRun | None:
    """同会话、同请求、10 分钟内的未失败任务（避免重复建任务）。"""
    cutoff = datetime.now() - timedelta(minutes=10)
    return db.scalar(
        select(models.TaskRun)
        .where(
            models.TaskRun.conversation_id == conversation_id,
            models.TaskRun.request == request,
            models.TaskRun.status.in_(["pending", "running", "approval", "completed"]),
            models.TaskRun.created_at >= cutoff,
        )
        .order_by(models.TaskRun.created_at.desc())
        .limit(1)
    )
