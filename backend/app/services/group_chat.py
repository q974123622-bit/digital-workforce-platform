"""职场会话编排（Sprint 7）。

统一入口 send_conversation_message：
- 私聊：直接由唯一成员回复；
- 群聊：先由分身（组织者）判断消息是「任务」还是「闲聊」——
  - 任务：追加分身受理消息，走 TeamTaskOrchestrator 拆解 → 指派成员 → Gateway 执行 → 审批 → Leader 汇总；
  - 闲聊：只由一位成员回复一次（点名某成员则那位回，否则分身回）。

每个成员的对话仍复用 ChatOrchestrator（各自走 Policy → Gateway → 审计），
单成员失败降级为提示消息并继续，全部失败才返回 503。
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Callable
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from . import config, employee_registry, execution_events
from .chat import ChatOrchestrator, tool_card_dict
from .harness_agent import run_agent
from .identity import resolve_identity
from .llm import DeepSeekProvider, LLMProvider, LLMUnavailableError
from .memory_service import conversation_history, conversation_transcript
from .team_orchestrator import TeamTaskOrchestrator
from .runtime_adapter import DockerHarnessRuntimeAdapter, NoopRuntimeAdapter

logger = logging.getLogger(__name__)

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


class ExecutionCancelled(RuntimeError):
    """The run no longer belongs to the conversation's visible generation."""


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
    generation: int = 0,
) -> models.ConversationMessage:
    msg = models.ConversationMessage(
        conversation_id=conversation_id,
        participant_no=participant_no,
        participant_name=participant_name,
        role=role,
        content=content,
        tool_cards=tool_cards or [],
        seq=seq,
        generation=generation,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def _next_seq(db: Session, conversation_id: str, generation: int = 0) -> int:
    current = db.scalar(
        select(func.max(models.ConversationMessage.seq)).where(
            models.ConversationMessage.conversation_id == conversation_id,
            models.ConversationMessage.generation == generation,
        )
    )
    return (current or 0) + 1


def _history_messages(
    db: Session, conversation_id: str, generation: int = 0, *, with_speaker: bool = False,
) -> list[dict]:
    """把会话消息转成模型上下文（source=demo）。

    一律不加「【名字】」前缀：UI 已按参与者展示姓名，前缀只会诱导模型模仿该格式。
    with_speaker=True 时额外带 `speaker` 字段，仅供渲染成文本的路径使用（不要进 provider）。
    """
    return conversation_history(db, conversation_id, generation, with_speaker=with_speaker)


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
        seq=_next_seq(db, conversation.id, conversation.generation),
        generation=conversation.generation,
    )


def _assert_execution_writable(
    db: Session, conversation: models.Conversation, execution_id: str | None, generation: int,
) -> None:
    db.refresh(conversation)
    if conversation.generation != generation:
        raise ExecutionCancelled("会话已清空")
    if execution_id:
        execution = db.get(models.AgentExecution, execution_id)
        if execution is None or execution.conversation_generation != generation:
            raise ExecutionCancelled("执行已取消")
        # 取消状态必须以库为准：worker 会话长期持有取消前的旧对象，
        # 只看内存状态会让「停止」之后仍然把答复写进会话。
        if execution_events.is_cancelled(db, execution_id):
            raise ExecutionCancelled("执行已取消")


def member_replies(
    db: Session,
    *,
    conversation: models.Conversation,
    actor_no: str,
    content: str,
    provider: LLMProvider | None = None,
    only: str | None = None,
    execution_id: str | None = None,
    generation: int | None = None,
) -> list[models.ConversationMessage]:
    """让成员按顺序回应（假定用户消息已落库）；only 指定时仅该成员回复。

    单成员失败降级为「（XX 暂时无法响应，已跳过）」并继续；全部失败返回 503。
    """
    participants = conversation.participants or []
    if not participants:
        raise HTTPException(status_code=400, detail="会话没有成员")

    selected_provider = provider or DeepSeekProvider()
    orchestrator = ChatOrchestrator(selected_provider)
    use_harness = (
        (config.get("DWP_AGENT_EXECUTION_MODE", "harness") or "harness") == "harness"
        and provider is None
        and selected_provider.__class__.__name__ == "DeepSeekProvider"
    )
    member_names: dict[str, str] = {}
    for participant in participants:
        emp = db.get(models.DigitalEmployee, participant["employee_no"])
        member_names[participant["employee_no"]] = emp.name if emp else participant["employee_no"]

    targets = [p for p in participants if only is None or p["employee_no"] == only]
    if not targets:
        raise HTTPException(status_code=400, detail="指定的回复成员不在会话中")

    run_generation = conversation.generation if generation is None else generation
    history = _history_messages(db, conversation.id, run_generation)
    is_group = conversation.kind == "group"
    appended: list[models.ConversationMessage] = []
    failed = 0
    execution = db.get(models.AgentExecution, execution_id) if execution_id else None
    for participant in targets:
        employee_no = participant["employee_no"]
        name = member_names.get(employee_no, employee_no)
        system_context = ""
        if is_group:
            system_context = (
                "【协作空间】你正在与同事协作完成一条任务。以下是到目前为止的对话记录，"
                f"请结合自己的职责给出你的专业部分。当前成员：{', '.join(member_names.values())}。"
                "不要复述或引用其他人已经说过的内容；回复直接以内容开头，"
                "不要写任何人的名字或【】前缀。"
            )
        try:
            employee = db.get(models.DigitalEmployee, employee_no)
            if use_harness and employee_registry.is_harness_employee(employee_no):
                result = run_agent(
                    db,
                    employee_id=employee_no,
                    requester_human_no=actor_no,
                    message=content,
                    history=history,
                    trace_id=(
                        execution.trace_id if execution is not None else
                        f"T-HAR-{conversation.id}-{employee_no}-{uuid4().hex[:8]}"
                    ),
                    # 令牌带上会话标识：委派给岗位员工时对方也能看到本会话上文。
                    conversation_id=conversation.id,
                )
            else:
                result = orchestrator.handle_message(
                    db,
                    employee_no=employee_no,
                    message=content,
                    session_id=None,
                    system_context=system_context,
                    history_override=history,
                    persist=False,
                    trace_id=f"T-GRP-{conversation.id}-{employee_no}",
                    requester_human_no=actor_no,
                )
            cards = [tool_card_dict(card) for card in result.tool_cards]
            _assert_execution_writable(db, conversation, execution_id, run_generation)
            appended.append(
                _append(
                    db,
                    conversation_id=conversation.id,
                    participant_no=employee_no,
                    participant_name=name,
                    role="assistant",
                    content=result.message,
                    seq=_next_seq(db, conversation.id, run_generation),
                    tool_cards=cards,
                    generation=run_generation,
                )
            )
        except LLMUnavailableError:
            failed += 1
            _assert_execution_writable(db, conversation, execution_id, run_generation)
            appended.append(
                _append(
                    db,
                    conversation_id=conversation.id,
                    participant_no=employee_no,
                    participant_name=name,
                    role="assistant",
                    content=f"（{name} 暂时无法响应，已跳过）",
                    seq=_next_seq(db, conversation.id, run_generation),
                    generation=run_generation,
                )
            )
        history = _history_messages(db, conversation.id, run_generation)

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
    # 结合最近对话：否则「再改一下」「还是按刚才的口径」这类延续性指令永远判不出任务。
    transcript = conversation_transcript(
        db, conversation.id, conversation.generation, exclude_current=content,
    )
    persona = ChatOrchestrator(provider or DeepSeekProvider())._system_prompt(db, subject)
    system_content = (
        f"{persona}\n"
        "【任务判断】你是协作空间的组织者。判断用户消息是需要同事们协作执行的任务，"
        "还是普通闲聊。任务的特征：包含具体待办事项、需要查询/整理/执行/开通/生成等动作。"
    )
    if transcript:
        system_content += (
            "若用户消息在延续上文（如「再改一下」「还是按刚才的口径」），同样算任务。"
            "最近对话的「说话人：内容」格式仅为历史记录，不要模仿该格式。"
        )
    system_content += '只输出 JSON：{"action":"task"} 或 {"action":"chat"}，不要输出其他内容。'
    user_content = f"协作空间成员：{roster}"
    if transcript:
        user_content += f"\n最近对话：\n{transcript}"
    user_content += f"\n用户消息：{content}"
    prompt = [
        {"role": "system", "content": system_content, "source": "demo"},
        {"role": "user", "content": user_content, "source": "demo"},
    ]
    try:
        raw = (provider or DeepSeekProvider()).structured_output(prompt, {"type": "object"})
        return raw.get("action") == "task"
    except Exception:  # noqa: BLE001 - 判定失败按闲聊处理
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
    execution_id: str | None = None,
    generation: int | None = None,
) -> None:
    """执行"用户消息已落库"之后的完整编排：分类 → 任务/闲聊 → 写回结果。"""
    trigger_seq = trigger_seq if trigger_seq is not None else _latest_user_seq(db, conversation.id)

    if conversation.kind != "group":
        member_replies(
            db, conversation=conversation, actor_no=actor_no, content=content,
            provider=provider, execution_id=execution_id, generation=generation,
        )
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
                        seq=_next_seq(db, conversation.id, conversation.generation),
                        generation=conversation.generation,
                    )
            else:
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
                execution_id=execution_id,
                generation=generation,
            )


def process_conversation_async(
    conversation_id: str,
    actor_no: str,
    content: str,
    trigger_seq: int,
    execution_id: str | None = None,
) -> None:
    """后台异步执行会话编排（独立 DB session，供 FastAPI BackgroundTasks 调用）。"""
    from ..database import SessionLocal

    with _conversation_lock(conversation_id):
        db = SessionLocal()
        try:
            conv = db.get(models.Conversation, conversation_id)
            execution = db.get(models.AgentExecution, execution_id) if execution_id else None
            run_generation = execution.conversation_generation if execution is not None else (
                conv.generation if conv is not None else 0
            )
            trigger = db.scalar(
                select(models.ConversationMessage).where(
                    models.ConversationMessage.conversation_id == conversation_id,
                    models.ConversationMessage.seq == trigger_seq,
                    models.ConversationMessage.role == "user",
                    models.ConversationMessage.generation == run_generation,
                )
            )
            # 会话已被清空或触发消息已不存在：后台任务立即取消，不得复活数据。
            if conv is None or trigger is None or trigger.content != content:
                return
            if execution is not None:
                if execution.status == "cancelled":
                    return
                execution_events.emit(
                    db, execution.id, event_type="agent_started", stage="agent_started",
                    status="running", actor_employee_id=execution.primary_employee_id,
                    title="AI员工已受理任务", detail="正在分析任务并确认可用能力",
                )
            process_conversation(
                db,
                conv,
                actor_no,
                content,
                trigger_seq=trigger_seq,
                execution_id=execution_id,
                generation=run_generation,
            )
            _assert_execution_writable(db, conv, execution_id, run_generation)
            conv.updated_at = datetime.now()
            db.add(conv)
            db.commit()
            if execution_id:
                execution = db.get(models.AgentExecution, execution_id)
                if execution is None or execution_events.is_cancelled(db, execution_id):
                    return
                answers = db.scalars(
                    select(models.ConversationMessage).where(
                        models.ConversationMessage.conversation_id == conversation_id,
                        models.ConversationMessage.role == "assistant",
                        models.ConversationMessage.seq > trigger_seq,
                        models.ConversationMessage.generation == run_generation,
                    ).order_by(models.ConversationMessage.seq)
                ).all()
                if not answers:
                    # 一条答复都没有就不是「完成」：直接 complete 会让前端弹一个空气泡、
                    # 轨迹里也看不出发生过什么（2026-09-28 真机排查时就被这个状态误导过）。
                    # 正常路径下 run_agent 已用工作区产物兜底，走到这里说明连兜底都没有。
                    logger.warning(
                        "execution produced no answer conversation=%s execution=%s",
                        conversation_id, execution_id,
                    )
                    execution_events.fail(
                        db, execution_id, RuntimeError("本轮没有产出任何答复"))
                    return
                for answer in answers:
                    execution_events.emit_answer_chunks(
                        db, execution_id,
                        actor_employee_id=answer.participant_no,
                        answer=answer.content,
                    )
                last = answers[-1] if answers else None
                execution_events.complete(
                    db, execution_id,
                    actor_employee_id=(last.participant_no if last else execution.primary_employee_id),
                    message_id=(last.id if last else None),
                    trace_id=execution.trace_id,
                    tool_cards=((last.tool_cards or []) if last else []),
                )
        except ExecutionCancelled:
            db.rollback()
            return
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            if execution_id:
                execution_events.fail(db, execution_id, exc)
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
