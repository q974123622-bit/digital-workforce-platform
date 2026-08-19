"""个人工作中心（职场）接口（Sprint 7）。

职场聚合 / 技能 CRUD / 会话（私聊 + 协作群聊）。
私聊与群聊统一由 Conversation 承载，消息发送走 services/group_chat 编排。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.adapters import WORKFLOW_META
from ..services.group_chat import send_conversation_message
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
    return emp


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
def workplace_home(actor_no: str = Query(...), db: Session = Depends(get_db)):
    """员工登录后的职场首页：本人信息 + 我的分身 + 可用数字员工 + 技能 + 最近会话。"""
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
        .limit(5)
    ).all()

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
def create_skill(payload: schemas.SkillCreate, db: Session = Depends(get_db)):
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
def list_skills(actor_no: str = Query(...), db: Session = Depends(get_db)):
    _get_actor(db, actor_no)
    return db.scalars(
        select(models.Skill).where(models.Skill.owner_human_no == actor_no).order_by(models.Skill.created_at)
    ).all()


@skills_router.put("/{skill_id}", response_model=schemas.SkillOut)
def update_skill(skill_id: str, payload: schemas.SkillUpdate, db: Session = Depends(get_db)):
    skill = db.get(models.Skill, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="技能不存在")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(skill, field, value)
    db.commit()
    db.refresh(skill)
    return skill


@skills_router.delete("/{skill_id}", status_code=204)
def delete_skill(skill_id: str, db: Session = Depends(get_db)):
    skill = db.get(models.Skill, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="技能不存在")
    db.delete(skill)
    db.commit()


# ---- 会话（私聊 + 协作群聊）----


@conversations_router.post("", response_model=schemas.ConversationOut, status_code=201)
def create_conversation(payload: schemas.ConversationCreate, db: Session = Depends(get_db)):
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
def list_conversations(actor_no: str = Query(...), db: Session = Depends(get_db)):
    _get_actor(db, actor_no)
    rows = db.scalars(
        select(models.Conversation)
        .where(models.Conversation.owner_human_no == actor_no)
        .order_by(models.Conversation.updated_at.desc())
    ).all()
    return [_summary_out(db, conv) for conv in rows]


@conversations_router.get("/{conversation_id}", response_model=schemas.ConversationOut)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)):
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return _conversation_out(db, conv)


@conversations_router.post("/{conversation_id}/messages", response_model=schemas.ConversationOut)
def send_message(conversation_id: str, payload: schemas.ConversationSendIn, db: Session = Depends(get_db)):
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.owner_human_no != payload.actor_no:
        raise HTTPException(status_code=403, detail="无权向该会话发送消息")
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息不能为空")
    send_conversation_message(db, conversation=conv, actor_no=payload.actor_no, content=content)
    return _conversation_out(db, conv)


@conversations_router.post("/{conversation_id}/participants", response_model=schemas.ConversationOut)
def add_participant(
    conversation_id: str,
    payload: schemas.ConversationAddParticipantIn,
    db: Session = Depends(get_db),
):
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
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
    db: Session = Depends(get_db),
):
    """清空会话：删除本会话的全部消息与协作任务（演示清洁用）。"""
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.owner_human_no != actor_no:
        raise HTTPException(status_code=403, detail="无权清空该会话")
    db.query(models.ConversationMessage).filter(
        models.ConversationMessage.conversation_id == conversation_id
    ).delete()
    db.query(models.TaskRun).filter(models.TaskRun.conversation_id == conversation_id).delete()
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
                employees.append(
                    schemas.WorkflowEmployeeOut(
                        employee_no=emp.employee_no,
                        name=emp.name,
                        type=emp.type,
                    )
                )
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
            )
        )
    return out
