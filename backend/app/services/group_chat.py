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
import threading
from datetime import datetime, timedelta
from typing import Callable
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import models
from . import config
from .agentteams_gateway import AgentTeamsGateway, AgentTeamsUnavailableError
from .chat import ChatOrchestrator
from .gateway import write_audit
from .identity import resolve_identity
from .llm import DeepSeekProvider, LLMProvider, LLMUnavailableError
from .team_orchestrator import TeamTaskOrchestrator, extract_employee_name
from .runtime_adapter import DockerHarnessRuntimeAdapter, NoopRuntimeAdapter

_TASK_STATUS_LABEL = {
    "pending": "待执行",
    "running": "执行中",
    "approval": "待审批",
    "completed": "已完成",
    "denied": "已拒绝",
    "failed": "失败",
}

_CONVERSATION_LOCKS: dict[str, threading.Lock] = {}
_CONVERSATION_LOCKS_GUARD = threading.Lock()


def _conversation_lock(conversation_id: str) -> threading.Lock:
    """同一进程内按会话串行编排，确保消息顺序和任务去重。"""
    with _CONVERSATION_LOCKS_GUARD:
        return _CONVERSATION_LOCKS.setdefault(conversation_id, threading.Lock())


def _default_team_orchestrator(provider: LLMProvider | None = None) -> TeamTaskOrchestrator:
    runtime = (
        DockerHarnessRuntimeAdapter()
        if config.get("DWP_HARNESS_ENABLED") == "1"
        else NoopRuntimeAdapter()
    )
    return TeamTaskOrchestrator(provider or DeepSeekProvider(), runtime=runtime)

def _employee_by_mxid(db: Session) -> dict[str, tuple[str, str]]:
    """把 AgentTeams worker MXID 映射到平台数字员工（工号, 姓名）。"""
    out: dict[str, tuple[str, str]] = {}
    domain = config.agentteams_matrix_domain()
    rows = db.scalars(
        select(models.DigitalEmployee).where(models.DigitalEmployee.runtime_ref.is_not(None))
    ).all()
    for emp in rows:
        mxid = f"@{emp.runtime_ref}:{domain}"
        out[mxid] = (emp.employee_no, emp.name)
    return out


def _feedback_status(body: str) -> str:
    """根据房间消息关键词粗判成员状态：completed / running / ack。"""
    if any(k in body for k in ("完成", "TASK_COMPLETED", "交付完成", "已提交")):
        return "completed"
    if any(k in body for k in ("收到", "开始", "处理", "认领")):
        return "running"
    return "running"


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
    user_message = append_user_message(db, conversation=conversation, actor_no=actor_no, content=content)
    process_conversation(
        db,
        conversation,
        actor_no,
        content,
        provider,
        orchestrator,
        classifier,
        trigger_seq=user_message.seq,
    )
    conversation.updated_at = datetime.now()
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def _latest_user_seq(db: Session, conversation_id: str) -> int | None:
    """最近一条用户消息的 seq（异步路径中用于关联任务触发点）。"""
    return db.scalar(
        select(func.max(models.ConversationMessage.seq)).where(
            models.ConversationMessage.conversation_id == conversation_id,
            models.ConversationMessage.role == "user",
        )
    )


def process_conversation(
    db: Session,
    conversation: models.Conversation,
    actor_no: str,
    content: str,
    provider: LLMProvider | None = None,
    orchestrator: TeamTaskOrchestrator | None = None,
    classifier: Callable | None = None,
    trigger_seq: int | None = None,
) -> None:
    """执行"用户消息已落库"之后的完整编排：分类 → 任务/闲聊 → 写回结果。"""
    trigger_seq = trigger_seq if trigger_seq is not None else _latest_user_seq(db, conversation.id)

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
                        trigger_seq=trigger_seq,
                        orchestrator=orchestrator or _default_team_orchestrator(provider),
                    )
                    if at_task is not None:
                        conversation.updated_at = datetime.now()
                        db.add(conversation)
                        db.commit()
                        db.refresh(conversation)
                        return conversation
                orch = orchestrator or _default_team_orchestrator(provider)
                orch.create_conversation_task(
                    db,
                    conversation=conversation,
                    actor_no=actor_no,
                    request=content,
                    trigger_seq=trigger_seq,
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


def process_conversation_async(
    conversation_id: str,
    actor_no: str,
    content: str,
    trigger_seq: int,
) -> None:
    """后台异步执行会话编排（独立 DB session，供 FastAPI BackgroundTasks 调用）。"""
    from ..database import SessionLocal

    with _conversation_lock(conversation_id):
        db = SessionLocal()
        try:
            conv = db.get(models.Conversation, conversation_id)
            trigger = db.scalar(
                select(models.ConversationMessage).where(
                    models.ConversationMessage.conversation_id == conversation_id,
                    models.ConversationMessage.seq == trigger_seq,
                    models.ConversationMessage.role == "user",
                )
            )
            # 会话已被清空或触发消息已不存在：后台任务立即取消，不得复活数据。
            if conv is None or trigger is None or trigger.content != content:
                return
            process_conversation(
                db,
                conv,
                actor_no,
                content,
                trigger_seq=trigger_seq,
            )
            conv.updated_at = datetime.now()
            db.add(conv)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            # 若任务记录已经建立，将其明确置为 failed；不让 UI 永久停在 running。
            run = db.scalar(
                select(models.TaskRun).where(
                    models.TaskRun.conversation_id == conversation_id,
                    models.TaskRun.trigger_message_seq == trigger_seq,
                    models.TaskRun.status.in_(["pending", "running"]),
                )
            )
            if run is not None:
                run.status = "failed"
                run.summary = f"协作执行失败：{exc.__class__.__name__}"
                db.commit()
        finally:
            db.close()


def _try_agentteams_task(
    db: Session,
    *,
    conversation: models.Conversation,
    leader: models.DigitalEmployee | None,
    actor_no: str,
    request: str,
    trigger_seq: int | None,
    orchestrator: TeamTaskOrchestrator | None = None,
) -> models.TaskRun | None:
    """AgentTeams 协作 + Harness/Gateway 执行同一任务。

    AgentTeams 只承担角色讨论、认领与汇报展示，不直接触发有副作用的插件；
    实际执行始终由已持久化 TaskRun 进入 Policy -> Gateway -> Harness 链路。
    因此协作超时只会降级协作展示，不会把同一业务动作执行两次。
    """
    room_id = config.get(config.AGENTTEAMS_ROOM_ID)
    if not room_id:
        return None
    orchestrator = orchestrator or _default_team_orchestrator()
    gateway = AgentTeamsGateway()
    mode_message: models.ConversationMessage | None = None
    task_id = f"T-{datetime.now():%Y%m%d}-{uuid4().hex[:6].upper()}"
    actor = db.get(models.HumanEmployee, actor_no)
    actor_name = actor.name if actor else actor_no
    target_name = extract_employee_name(request)
    run: models.TaskRun | None = None
    sent = False
    try:
        run, prepared_leader = orchestrator.prepare_conversation_task(
            db,
            conversation=conversation,
            actor_no=actor_no,
            request=request,
            trigger_seq=trigger_seq,
            source="agentteams",
            task_id=task_id,
        )
        task_id = run.id
        leader = leader or prepared_leader
        participant_nos = {
            p.get("employee_no") for p in (conversation.participants or [])
        }
        member_map = {
            mxid: employee
            for mxid, employee in _employee_by_mxid(db).items()
            if employee[0] in participant_nos
        }
        plan = "；".join(
            f"{sub.get('worker_no')}={sub.get('summary')}"
            for sub in run.subtasks
        )
        for sub in run.subtasks:
            sub["collaboration_status"] = "collaborating"
            sub["collaboration_messages"] = []
            sub["execution_mode"] = "pending"
        TeamTaskOrchestrator._save(db, run)

        # 群聊房间里只有 @mention Manager 的消息才会被处理。
        # 协议要求所有反馈携带 task_id，从根上隔离共享房间中的并行任务。
        manager_mxid = config.agentteams_manager_mxid()
        send_ts = int(time.time() * 1000)
        gateway.send_message(
            room_id,
            (
                f"{manager_mxid} [平台任务 id={task_id}] "
                f"请求者={actor_name}({actor_no}) 请求={request}"
                f"{f' 目标员工={target_name}' if target_name else ''}。"
                f"平台计划={plan}。"
                "本阶段只做协作讨论、认领和风险提示，不调用外部 Workflow/RPA。"
                "每条回复必须原样携带任务 id；最终汇报使用“TASK_COMPLETED id=<任务ID>”。"
                "实际动作将由平台 Policy→Gateway→DeepSeek Harness 统一执行。"
            ),
        )
        sent = True
        write_audit(
            db,
            trace_id=task_id,
            employee_id=leader.employee_no if leader else actor_no,
            plugin_id="agentteams:send",
            action="send",
            decision="allow",
            reason="任务已发送至 AgentTeams 团队房间",
            result_summary=request[:200],
        )
        # 限时回收协作过程；到期后仍按持久化计划进入 Harness，不重复派业务动作。
        report: str | None = None
        deadline = time.time() + config.agentteams_collaboration_timeout()
        while time.time() < deadline:
            # 清空会话会删除触发消息；检测到后立即停止，禁止后台复活任务。
            alive = True
            if trigger_seq is not None:
                alive = db.scalar(
                    select(models.ConversationMessage.id).where(
                        models.ConversationMessage.conversation_id == conversation.id,
                        models.ConversationMessage.seq == trigger_seq,
                        models.ConversationMessage.role == "user",
                    )
                )
            if alive is None:
                return None
            msgs = gateway.poll_messages(room_id)
            feedback_changed = False
            for msg in msgs:
                sender = msg.get("sender", "")
                body = msg.get("body", "")
                if (msg.get("ts") or 0) < send_ts:
                    continue
                if task_id not in body:
                    continue
                if sender not in member_map:
                    continue
                emp_no, emp_name = member_map[sender]
                event_id = msg.get("event_id") or ""
                if event_id:
                    exists = db.scalar(
                        select(models.AgentTeamsEventSeen).where(
                            models.AgentTeamsEventSeen.event_id == event_id
                        )
                    )
                    if exists is not None:
                        continue
                    db.add(
                        models.AgentTeamsEventSeen(
                            event_id=event_id,
                            conversation_id=conversation.id,
                        )
                    )
                _append(
                    db,
                    conversation_id=conversation.id,
                    participant_no=emp_no,
                    participant_name=emp_name,
                    role="assistant",
                    content=body,
                    seq=_next_seq(db, conversation.id),
                )
                sub = next((s for s in run.subtasks if s.get("worker_id") == emp_no), None)
                if sub is not None:
                    messages = list(sub.get("collaboration_messages") or [])
                    messages.append(body)
                    sub["collaboration_messages"] = messages[-5:]
                    sub["collaboration_status"] = (
                        "reported" if _feedback_status(body) == "completed" else "acknowledged"
                    )
                    feedback_changed = True
            if feedback_changed:
                TeamTaskOrchestrator._save(db, run)
            report = gateway.parse_completion(
                msgs,
                request[:12],
                since_ts=send_ts,
                task_id=task_id,
                exclude_senders={config.agentteams_bot_mxid()},
            )
            if report:
                break
            time.sleep(5)
        if report and leader is not None:
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
                employee_id=leader.employee_no,
                plugin_id="agentteams:receive",
                action="receive",
                decision="allow",
                reason="已回收 AgentTeams 协作汇报，进入平台受控执行",
                result_summary=report[:200],
            )
        elif leader is not None:
            mode_message = _append(
                db,
                conversation_id=conversation.id,
                participant_no=leader.employee_no,
                participant_name=leader.name,
                role="assistant",
                content="AgentTeams 协作窗口已结束，我将按已确认的角色计划进入平台受控执行。",
                seq=_next_seq(db, conversation.id),
            )

        if report:
            for sub in run.subtasks:
                sub["collaboration_summary"] = report
            flag_modified(run, "subtasks")
            db.add(run)
            db.commit()
            db.refresh(run)

        orchestrator.run_prepared_task(
            db,
            run,
            leader_employee_id=leader.employee_no if leader else actor_no,
        )
        db.refresh(run)
        if mode_message is not None:
            modes = {str(sub.get("runtime_mode") or "") for sub in run.subtasks}
            if modes == {"harness"}:
                execution_notice = "AgentTeams 协作窗口已结束；实际运行时：DeepSeek Harness。业务动作已通过 Policy/Gateway 调用员工获授权工具。"
            elif {"harness", "demo_adapter"}.issubset(modes):
                execution_notice = "AgentTeams 协作窗口已结束；实际运行时：DeepSeek Harness + Demo Adapter 降级（部分员工 Harness 不可用）。各业务动作仍通过 Policy/Gateway 受控执行。"
            elif "demo_adapter" in modes:
                execution_notice = "AgentTeams 协作窗口已结束；实际运行时：Demo Adapter 降级（Harness 未启用或不可用）。业务动作仍通过 Policy/Gateway 受控执行。"
            else:
                execution_notice = "AgentTeams 协作窗口已结束；实际运行时已记录在各数字员工子任务中。"
            mode_message.content = execution_notice
            db.add(mode_message)
            db.commit()
        return run
    except AgentTeamsUnavailableError as exc:
        import sys

        print(f"[group_chat] agentteams 路径降级: {exc!r}", file=sys.stderr)
        if run is not None and not sent:
            # Matrix 尚未接受任务，没有双重执行风险；沿用同一 TaskRun 走内置/Harness。
            run.source = "builtin"
            for sub in run.subtasks:
                sub["collaboration_status"] = "unavailable"
            TeamTaskOrchestrator._save(db, run)
            orchestrator.run_prepared_task(
                db,
                run,
                leader_employee_id=leader.employee_no if leader else actor_no,
            )
            db.refresh(run)
            return run
        if run is not None:
            run.status = "failed"
            run.summary = "AgentTeams 任务已发送，但协作通道中断；为避免重复执行，未自动降级。"
            TeamTaskOrchestrator._save(db, run)
            return run
        return None
    except Exception as exc:  # noqa: BLE001
        import sys

        print(f"[group_chat] 协作执行异常: {exc!r}", file=sys.stderr)
        if run is not None:
            run.status = "failed"
            run.summary = f"协作链路异常：{exc.__class__.__name__}"
            TeamTaskOrchestrator._save(db, run)
            return run
        return None
    finally:
        gateway.close()


def _find_recent_task(
    db: Session,
    conversation_id: str,
    request: str,
) -> models.TaskRun | None:
    """同会话、同请求、2 分钟内的运行中/待审批任务（避免重复建任务）。"""
    cutoff = datetime.now() - timedelta(minutes=2)
    return db.scalar(
        select(models.TaskRun)
        .where(
            models.TaskRun.conversation_id == conversation_id,
            models.TaskRun.request == request,
            models.TaskRun.status.in_(["running", "approval"]),
            models.TaskRun.created_at >= cutoff,
        )
        .order_by(models.TaskRun.created_at.desc())
        .limit(1)
    )
