"""个人工作中心（职场）接口（Sprint 7）。

职场聚合 / 技能 CRUD / 会话（私聊 + 协作群聊）。
私聊与群聊统一由 Conversation 承载，消息发送走 services/group_chat 编排。
"""

import asyncio
import json
import time
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.adapters import WORKFLOW_META
from ..services.auth import enforce_actor, optional_account
from ..services.group_chat import append_user_message, process_conversation_async
from ..services import config, execution_events
from ..services.team_orchestrator import TeamTaskOrchestrator
from .employees import _to_out as _employee_out

workplace_router = APIRouter(prefix="/workplace", tags=["workplace"])
skills_router = APIRouter(prefix="/skills", tags=["skills"])
conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])
workflow_router = APIRouter(prefix="/workflows", tags=["workflows"])

_TASK_STATUS_LABEL = {
    "pending": "待执行",
    "running": "执行中",
    "approval": "待审批",
    "completed": "已完成",
    "denied": "已拒绝",
    "failed": "失败",
}


def _get_actor(db: Session, actor_no: str) -> models.HumanEmployee:
    actor = db.get(models.HumanEmployee, actor_no)
    if actor is None:
        raise HTTPException(status_code=404, detail="员工不存在")
    return actor


def _get_digital_employee(db: Session, employee_no: str) -> models.DigitalEmployee:
    emp = db.get(models.DigitalEmployee, employee_no)
    if emp is None:
        raise HTTPException(status_code=404, detail="数字员工不存在")
    if emp.status != "active":
        raise HTTPException(status_code=409, detail="数字员工当前未启用")
    return emp


def _employee_workflow_out(
    db: Session, employee_no: str
) -> schemas.WorkflowEmployeeOut | None:
    emp = db.get(models.DigitalEmployee, employee_no)
    if emp is None:
        return None
    return schemas.WorkflowEmployeeOut(
        employee_no=emp.employee_no,
        name=emp.name,
        type=emp.type,
    )


def _participants_out(db: Session, participants: list) -> list[schemas.ConversationParticipantOut]:
    out: list[schemas.ConversationParticipantOut] = []
    for participant in participants or []:
        employee_no = participant["employee_no"]
        emp = db.get(models.DigitalEmployee, employee_no)
        out.append(
            schemas.ConversationParticipantOut(
                employee_no=employee_no,
                name=emp.name if emp else employee_no,
                role=participant.get("role", "member"),
                employee_type=emp.type if emp else "",
            )
        )
    return out


def _messages_out(db: Session, conversation_id: str) -> list[schemas.ConversationMessageOut]:
    rows = db.scalars(
        select(models.ConversationMessage)
        .where(models.ConversationMessage.conversation_id == conversation_id)
        .order_by(models.ConversationMessage.seq)
    ).all()
    return [schemas.ConversationMessageOut.model_validate(row) for row in rows]


def _conversation_out(db: Session, conv: models.Conversation) -> schemas.ConversationOut:
    runs = db.scalars(
        select(models.TaskRun)
        .where(models.TaskRun.conversation_id == conv.id)
        .order_by(models.TaskRun.created_at.desc())
    ).all()
    return schemas.ConversationOut(
        id=conv.id,
        kind=conv.kind,
        title=conv.title,
        owner_human_no=conv.owner_human_no,
        participants=_participants_out(db, conv.participants),
        messages=_messages_out(db, conv.id),
        tasks=[TeamTaskOrchestrator._to_out(run) for run in runs],
        updated_at=conv.updated_at,
    )


def _summary_out(db: Session, conv: models.Conversation) -> schemas.ConversationSummaryOut:
    last = db.scalar(
        select(models.ConversationMessage)
        .where(models.ConversationMessage.conversation_id == conv.id)
        .order_by(models.ConversationMessage.seq.desc())
        .limit(1)
    )
    last_task = db.scalar(
        select(models.TaskRun)
        .where(models.TaskRun.conversation_id == conv.id)
        .order_by(models.TaskRun.created_at.desc())
        .limit(1)
    )
    if last_task is not None:
        label = _TASK_STATUS_LABEL.get(last_task.status, last_task.status)
        preview = f"协作任务{label}：{last_task.request[:24]}"
    else:
        preview = last.content if last else ""
    return schemas.ConversationSummaryOut(
        id=conv.id,
        kind=conv.kind,
        title=conv.title,
        owner_human_no=conv.owner_human_no,
        participants=_participants_out(db, conv.participants),
        last_message=preview,
        updated_at=conv.updated_at,
    )


def _conversation_is_active(db: Session, conv: models.Conversation) -> bool:
    """Keep history in storage but hide conversations for retired demo identities."""
    participants = conv.participants or []
    if not participants:
        return False
    for participant in participants:
        employee = db.get(models.DigitalEmployee, participant.get("employee_no"))
        if employee is None or employee.status != "active":
            return False
    return True


def _next_skill_id(db: Session) -> str:
    rows = db.scalars(select(models.Skill.id).where(models.Skill.id.like("SK-%"))).all()
    max_n = max((int(no.split("-")[1]) for no in rows if "-" in no), default=0)
    return f"SK-{max_n + 1:04d}"


def _next_conversation_id(db: Session) -> str:
    rows = db.scalars(select(models.Conversation.id).where(models.Conversation.id.like("CONV-%"))).all()
    max_n = max((int(no.split("-")[1]) for no in rows if "-" in no), default=0)
    return f"CONV-{max_n + 1:04d}"


def _find_direct(db: Session, actor_no: str, employee_no: str) -> models.Conversation | None:
    for conv in db.scalars(
        select(models.Conversation).where(
            models.Conversation.owner_human_no == actor_no,
            models.Conversation.kind == "direct",
        )
    ).all():
        parts = conv.participants or []
        if len(parts) == 1 and parts[0]["employee_no"] == employee_no:
            return conv
    return None


# ---- 职场聚合 ----


@workplace_router.get("", response_model=schemas.WorkplaceHomeOut)
def workplace_home(
    actor_no: str = Query(...),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """员工登录后的职场首页：本人信息 + 我的分身 + 可用数字员工 + 技能 + 最近会话。"""
    enforce_actor(account, actor_no)
    actor = _get_actor(db, actor_no)
    twin = db.scalar(
        select(models.DigitalEmployee).where(
            models.DigitalEmployee.type == "twin",
            models.DigitalEmployee.owner_human_no == actor_no,
        )
    )
    if twin is None:
        raise HTTPException(status_code=404, detail="未找到该员工的数字分身")

    available = db.scalars(
        select(models.DigitalEmployee)
        .where(
            models.DigitalEmployee.type.in_(["virtual", "rpa"]),
            models.DigitalEmployee.status == "active",
        )
        .order_by(models.DigitalEmployee.employee_no)
    ).all()
    skills = db.scalars(
        select(models.Skill).where(models.Skill.owner_human_no == actor_no).order_by(models.Skill.created_at)
    ).all()
    conversations = db.scalars(
        select(models.Conversation)
        .where(models.Conversation.owner_human_no == actor_no)
        .order_by(models.Conversation.updated_at.desc())
    ).all()
    conversations = [conv for conv in conversations if _conversation_is_active(db, conv)][:5]

    return schemas.WorkplaceHomeOut(
        actor=schemas.ActorOut(
            employee_no=actor.employee_no,
            name=actor.name,
            department=actor.department,
            employment_type=actor.employment_type,
        ),
        twin=_employee_out(db, twin),
        available_employees=[_employee_out(db, emp) for emp in available],
        skills=skills,
        recent_conversations=[_summary_out(db, conv) for conv in conversations],
    )


# ---- 技能 CRUD ----


@skills_router.post("", response_model=schemas.SkillOut, status_code=201)
def create_skill(
    payload: schemas.SkillCreate,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    enforce_actor(account, payload.actor_no)
    _get_actor(db, payload.actor_no)
    skill = models.Skill(
        id=_next_skill_id(db),
        owner_human_no=payload.actor_no,
        name=payload.name,
        description=payload.description,
        content=payload.content,
        status="active",
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return skill


@skills_router.get("", response_model=list[schemas.SkillOut])
def list_skills(
    actor_no: str = Query(...),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    enforce_actor(account, actor_no)
    _get_actor(db, actor_no)
    return db.scalars(
        select(models.Skill).where(models.Skill.owner_human_no == actor_no).order_by(models.Skill.created_at)
    ).all()


@skills_router.put("/{skill_id}", response_model=schemas.SkillOut)
def update_skill(
    skill_id: str,
    payload: schemas.SkillUpdate,
    actor_no: str = Query(...),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    enforce_actor(account, actor_no)
    _get_actor(db, actor_no)
    skill = db.get(models.Skill, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="技能不存在")
    if skill.owner_human_no != actor_no:
        raise HTTPException(status_code=403, detail="无权修改其他员工的技能")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(skill, field, value)
    db.commit()
    db.refresh(skill)
    return skill


@skills_router.delete("/{skill_id}", status_code=204)
def delete_skill(
    skill_id: str,
    actor_no: str = Query(...),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    enforce_actor(account, actor_no)
    _get_actor(db, actor_no)
    skill = db.get(models.Skill, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="技能不存在")
    if skill.owner_human_no != actor_no:
        raise HTTPException(status_code=403, detail="无权删除其他员工的技能")
    db.delete(skill)
    db.commit()


# ---- 会话（私聊 + 协作群聊）----


@conversations_router.post("", response_model=schemas.ConversationOut, status_code=201)
def create_conversation(
    payload: schemas.ConversationCreate,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    enforce_actor(account, payload.actor_no)
    actor = _get_actor(db, payload.actor_no)
    twin = db.scalar(
        select(models.DigitalEmployee).where(
            models.DigitalEmployee.type == "twin",
            models.DigitalEmployee.owner_human_no == payload.actor_no,
        )
    )
    if twin is None:
        raise HTTPException(status_code=404, detail="未找到该员工的数字分身")

    employee_nos = payload.participant_employee_nos
    if payload.kind == "direct":
        if len(employee_nos) != 1:
            raise HTTPException(status_code=400, detail="私聊必须恰好选择 1 位数字员工")
        target_no = employee_nos[0]
        target = _get_digital_employee(db, target_no)
        if target.type == "twin" and target.owner_human_no != payload.actor_no:
            raise HTTPException(status_code=400, detail="不能与别人的数字分身私聊")
        existing = _find_direct(db, payload.actor_no, target_no)
        if existing is not None:
            return _conversation_out(db, existing)  # 幂等复用
        participants = [{"employee_no": target_no, "role": "member"}]
        title = ""
    elif payload.kind == "group":
        participants = [{"employee_no": twin.employee_no, "role": "organizer"}]
        seen = {twin.employee_no}
        for no in employee_nos:
            emp = _get_digital_employee(db, no)
            if emp.type not in ("virtual", "rpa"):
                raise HTTPException(status_code=400, detail=f"群聊成员必须是数字员工（虚拟员工/RPA）：{no}")
            if no in seen:
                raise HTTPException(status_code=400, detail=f"成员重复：{no}")
            seen.add(no)
            participants.append({"employee_no": no, "role": "member"})
        if len(participants) <= 1:
            raise HTTPException(status_code=400, detail="群聊至少需要 1 位数字员工")
        title = payload.title or "协作空间"
    else:
        raise HTTPException(status_code=400, detail="kind 必须为 direct 或 group")

    conv = models.Conversation(
        id=_next_conversation_id(db),
        kind=payload.kind,
        title=title,
        owner_human_no=actor.employee_no,
        participants=participants,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return _conversation_out(db, conv)


@conversations_router.get("", response_model=list[schemas.ConversationSummaryOut])
def list_conversations(
    actor_no: str = Query(...),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    enforce_actor(account, actor_no)
    _get_actor(db, actor_no)
    rows = db.scalars(
        select(models.Conversation)
        .where(models.Conversation.owner_human_no == actor_no)
        .order_by(models.Conversation.updated_at.desc())
    ).all()
    return [_summary_out(db, conv) for conv in rows if _conversation_is_active(db, conv)]


@conversations_router.get("/{conversation_id}", response_model=schemas.ConversationOut)
def get_conversation(
    conversation_id: str,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if account is not None and conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权查看该会话")
    return _conversation_out(db, conv)


@conversations_router.post("/{conversation_id}/messages", response_model=schemas.ConversationOut)
def send_message(
    conversation_id: str,
    payload: schemas.ConversationSendIn,
    background_tasks: BackgroundTasks,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    enforce_actor(account, payload.actor_no)
    if conv.owner_human_no != payload.actor_no:
        raise HTTPException(status_code=403, detail="无权向该会话发送消息")
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息不能为空")
    # 只落用户消息并立即返回；后台异步执行分类/派发/执行，避免请求阻塞导致前端卡死
    user_message = append_user_message(db, conversation=conv, actor_no=payload.actor_no, content=content)
    conv.updated_at = datetime.now()
    db.add(conv)
    db.commit()
    db.refresh(conv)
    background_tasks.add_task(
        process_conversation_async,
        conv.id,
        payload.actor_no,
        content,
        user_message.seq,
    )
    return _conversation_out(db, conv)


@conversations_router.post("/{conversation_id}/runs", response_model=schemas.ConversationRunOut)
def start_conversation_run(
    conversation_id: str,
    payload: schemas.ConversationSendIn,
    background_tasks: BackgroundTasks,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """Start one durable conversation execution; progress is consumed over SSE."""
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    enforce_actor(account, payload.actor_no)
    if conv.owner_human_no != payload.actor_no:
        raise HTTPException(status_code=403, detail="无权向该会话发送消息")
    active = execution_events.active_execution(db, conversation_id)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "当前会话已有任务正在执行", "retryable": True, "execution_id": active.id},
        )
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息不能为空")
    user_message = append_user_message(db, conversation=conv, actor_no=payload.actor_no, content=content)
    primary = (conv.participants or [{}])[0].get("employee_no", "")
    execution = execution_events.create_execution(
        db,
        conversation_id=conversation_id,
        trigger_message_seq=user_message.seq,
        primary_employee_id=primary,
    )
    conv.updated_at = datetime.now()
    db.add(conv)
    db.commit()
    background_tasks.add_task(
        process_conversation_async,
        conv.id,
        payload.actor_no,
        content,
        user_message.seq,
        execution.id,
    )
    return schemas.ConversationRunOut(
        execution_id=execution.id,
        trigger_message_seq=user_message.seq,
        conversation=_conversation_out(db, conv),
    )


@conversations_router.get("/{conversation_id}/runs/active", response_model=schemas.AgentExecutionOut | None)
def get_active_conversation_run(
    conversation_id: str,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if account is not None and conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权查看该会话")
    return execution_events.active_execution(db, conversation_id)


@conversations_router.get("/{conversation_id}/runs/latest", response_model=schemas.AgentExecutionDetailOut | None)
def get_latest_conversation_run(
    conversation_id: str,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """Return the latest durable execution and its sanitized, replayable events."""
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if account is not None and conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权查看该会话")
    execution = db.scalar(
        select(models.AgentExecution).where(
            models.AgentExecution.conversation_id == conversation_id,
        ).order_by(models.AgentExecution.started_at.desc()).limit(1)
    )
    if execution is None:
        return None
    events = db.scalars(
        select(models.AgentExecutionEvent).where(
            models.AgentExecutionEvent.execution_id == execution.id,
        ).order_by(models.AgentExecutionEvent.event_seq)
    ).all()
    return schemas.AgentExecutionDetailOut(execution=execution, events=events)


@conversations_router.get("/{conversation_id}/runs/history", response_model=list[schemas.AgentExecutionDetailOut])
def get_conversation_run_history(
    conversation_id: str,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """Return durable safe execution traces for every retained turn in a conversation."""
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if account is not None and conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权查看该会话")
    executions = db.scalars(
        select(models.AgentExecution).where(
            models.AgentExecution.conversation_id == conversation_id,
        ).order_by(models.AgentExecution.started_at)
    ).all()
    if not executions:
        return []
    execution_ids = [execution.id for execution in executions]
    event_rows = db.scalars(
        select(models.AgentExecutionEvent).where(
            models.AgentExecutionEvent.execution_id.in_(execution_ids),
        ).order_by(models.AgentExecutionEvent.execution_id, models.AgentExecutionEvent.event_seq)
    ).all()
    grouped: dict[str, list[models.AgentExecutionEvent]] = {execution_id: [] for execution_id in execution_ids}
    for event in event_rows:
        grouped[event.execution_id].append(event)
    return [
        schemas.AgentExecutionDetailOut(execution=execution, events=grouped[execution.id])
        for execution in executions
    ]


def _sse(event: str, event_id: str, data: dict) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_char_delay() -> float:
    try:
        milliseconds = int(config.get("DWP_STREAM_CHAR_DELAY_MS", "16") or "16")
    except ValueError:
        milliseconds = 16
    return max(0, min(milliseconds, 100)) / 1000


@conversations_router.get("/{conversation_id}/runs/{execution_id}/events")
async def stream_conversation_run(
    conversation_id: str,
    execution_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after_event_id: str | None = Query(default=None),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    conv = db.get(models.Conversation, conversation_id)
    execution = db.get(models.AgentExecution, execution_id)
    if conv is None or execution is None or execution.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    if account is not None and conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权查看该执行记录")

    try:
        raw_seq, _, raw_char = (last_event_id or after_event_id or "0").partition(".")
        cursor_seq = max(0, int(raw_seq or "0"))
        cursor_char = int(raw_char) if raw_char else -1
    except ValueError:
        cursor_seq, cursor_char = 0, -1

    async def generate():
        from ..database import SessionLocal

        nonlocal cursor_seq, cursor_char
        last_activity = time.monotonic()
        while True:
            stream_db = SessionLocal()
            try:
                current = stream_db.get(models.AgentExecution, execution_id)
                seq_filter = (
                    models.AgentExecutionEvent.event_seq >= cursor_seq
                    if cursor_char >= 0 else
                    models.AgentExecutionEvent.event_seq > cursor_seq
                )
                query = select(models.AgentExecutionEvent).where(
                    models.AgentExecutionEvent.execution_id == execution_id,
                    seq_filter,
                ).order_by(models.AgentExecutionEvent.event_seq)
                events = stream_db.scalars(query).all()
            finally:
                stream_db.close()
            if current is not None and current.status == "cancelled":
                break
            emitted = False
            for row in events:
                if row.event_type == "answer_chunk":
                    text = str((row.payload or {}).get("text", ""))
                    start = cursor_char + 1 if row.event_seq == cursor_seq and cursor_char >= 0 else 0
                    base_offset = int((row.payload or {}).get("offset", 0))
                    for index in range(start, len(text)):
                        cursor_seq, cursor_char = row.event_seq, index
                        yield _sse("answer_delta", f"{row.event_seq}.{index}", {
                            "execution_id": execution_id,
                            "employee_id": row.actor_employee_id,
                            "delta": text[index],
                            "offset": base_offset + index,
                        })
                        emitted = True
                        last_activity = time.monotonic()
                        await asyncio.sleep(_stream_char_delay())
                    cursor_seq, cursor_char = row.event_seq, -1
                else:
                    cursor_seq, cursor_char = row.event_seq, -1
                    data = {
                        "execution_id": execution_id,
                        "stage": row.stage,
                        "status": row.status,
                        "title": row.title,
                        "detail": row.detail,
                        "employee_id": row.actor_employee_id,
                        "knowledge_base_id": row.knowledge_base_id,
                        "target_agent_id": row.target_agent_id,
                        "hit_count": row.hit_count,
                        "created_at": row.created_at.isoformat(),
                        **(row.payload or {}),
                    }
                    event_name = row.event_type if row.event_type in {"error", "answer_done"} else "progress"
                    yield _sse(event_name, str(row.event_seq), data)
                    emitted = True
                    last_activity = time.monotonic()
            if current is None or (current.status in execution_events.TERMINAL_STATUSES and not events):
                break
            if not emitted and time.monotonic() - last_activity >= 15:
                yield ": heartbeat\n\n"
                last_activity = time.monotonic()
            await asyncio.sleep(0.25)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@conversations_router.post("/{conversation_id}/participants", response_model=schemas.ConversationOut)
def add_participant(
    conversation_id: str,
    payload: schemas.ConversationAddParticipantIn,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if account is not None and conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权修改该会话")
    if conv.kind != "group":
        raise HTTPException(status_code=400, detail="只有群聊可以添加成员")
    emp = _get_digital_employee(db, payload.employee_no)
    if emp.type not in ("virtual", "rpa"):
        raise HTTPException(status_code=400, detail="只能添加数字员工（虚拟员工/RPA）")
    participants = list(conv.participants or [])
    if any(p["employee_no"] == payload.employee_no for p in participants):
        raise HTTPException(status_code=400, detail="成员已在会话中")
    participants.append({"employee_no": payload.employee_no, "role": "member"})
    conv.participants = participants
    db.commit()
    db.refresh(conv)
    return _conversation_out(db, conv)


@conversations_router.delete("/{conversation_id}", response_model=schemas.ClearConversationOut)
def clear_conversation(
    conversation_id: str,
    actor_no: str = Query(...),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """清空本地会话数据；后台任务通过触发消息存在性检测自动取消。"""
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    enforce_actor(account, actor_no)
    if conv.owner_human_no != actor_no:
        raise HTTPException(status_code=403, detail="无权清空该会话")
    execution_events.cancel_for_conversation(db, conversation_id)
    db.query(models.ConversationMessage).filter(
        models.ConversationMessage.conversation_id == conversation_id
    ).delete()
    db.query(models.TaskRun).filter(models.TaskRun.conversation_id == conversation_id).delete()
    db.query(models.AgentTeamsEventSeen).filter(
        models.AgentTeamsEventSeen.conversation_id == conversation_id
    ).delete()
    db.commit()
    return schemas.ClearConversationOut(ok=True)


# ---- 工作流目录（职场「工作流」卡片）----


@workflow_router.get("", response_model=list[schemas.WorkflowOut])
def list_workflows(db: Session = Depends(get_db)):
    """Mock 工作流/RPA 目录：步骤、示例指令与授权成员（仅展示，不执行）。"""
    plugins = db.scalars(
        select(models.Plugin)
        .where(
            models.Plugin.type.in_(["workflow", "rpa"]),
            models.Plugin.status == "active",
        )
        .order_by(models.Plugin.type, models.Plugin.id)
    ).all()
    grant_map: dict[str, list[str]] = {}
    for grant in db.scalars(select(models.EmployeePluginGrant)).all():
        if grant.decision_mode in ("allow", "approval"):
            grant_map.setdefault(grant.plugin_id, []).append(grant.employee_id)

    out: list[schemas.WorkflowOut] = []
    for plugin in plugins:
        meta = WORKFLOW_META.get(plugin.id, {})
        employees = []
        for employee_no in grant_map.get(plugin.id, []):
            emp = db.get(models.DigitalEmployee, employee_no)
            if emp is not None:
                out_emp = _employee_workflow_out(db, employee_no)
                if out_emp is not None:
                    employees.append(out_emp)
        out.append(
            schemas.WorkflowOut(
                plugin_id=plugin.id,
                name=plugin.name,
                type=plugin.type,
                data_level=plugin.data_level,
                description=plugin.description,
                steps=meta.get("steps", []),
                demo_prompt=meta.get("demo_prompt", ""),
                authorized_employees=employees,
                owner_employee=(
                    _employee_workflow_out(db, meta["owner_employee"])
                    if meta.get("owner_employee")
                    else None
                ),
            )
        )
    return out
