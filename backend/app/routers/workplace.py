"""个人工作中心（职场）接口（Sprint 7）。

职场聚合 / 技能 CRUD / 会话（私聊 + 协作群聊）。
私聊与群聊统一由 Conversation 承载，消息发送走 services/group_chat 编排。
"""

import asyncio
import json
import time
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.auth import enforce_actor, optional_account
from ..services.group_chat import append_user_message, process_conversation_async
from ..services import (
    attachments,
    config,
    conversation_cleanup,
    conversation_search,
    execution_events,
    mcp_credentials,
    tool_input,
)
from ..services.gateway import write_audit
from ..services.team_orchestrator import TeamTaskOrchestrator
from .employees import _to_out as _employee_out

workplace_router = APIRouter(prefix="/workplace", tags=["workplace"])
skills_router = APIRouter(prefix="/skills", tags=["skills"])
conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])


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
        raise HTTPException(status_code=404, detail="AI员工不存在")
    if emp.status != "active":
        raise HTTPException(status_code=409, detail="AI员工当前未启用")
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


def _messages_out(db: Session, conversation_id: str, generation: int) -> list[schemas.ConversationMessageOut]:
    rows = db.scalars(
        select(models.ConversationMessage)
        .where(
            models.ConversationMessage.conversation_id == conversation_id,
            models.ConversationMessage.generation == generation,
        )
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
        messages=_messages_out(db, conv.id, conv.generation),
        tasks=[TeamTaskOrchestrator._to_out(run) for run in runs],
        updated_at=conv.updated_at,
    )


def _summary_out(
    db: Session, conv: models.Conversation, last_message: str | None = None
) -> schemas.ConversationSummaryOut:
    """会话摘要。last_message 非 None 时直接用调用方批量算好的预览，省掉两次查询。

    检索结果走这条路径（一次查全部命中会话的最新消息），避免逐会话查询造成 N+1。
    """
    if last_message is None:
        last = db.scalar(
            select(models.ConversationMessage)
            .where(
                models.ConversationMessage.conversation_id == conv.id,
                models.ConversationMessage.generation == conv.generation,
                # 已被重跑取代的回复不作列表预览：新回答落库前预览会退回提问本身。
                models.ConversationMessage.superseded_at.is_(None),
            )
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
    else:
        preview = last_message
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
    return conversation_search.conversation_is_active(db, conv)


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
    """员工登录后的职场首页：本人信息 + 我的分身 + 可用AI员工 + 技能 + 最近会话。"""
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


def _sync_skill_plugin(db: Session, skill: models.Skill, *, new_version: bool = False) -> None:
    """Compatibility bridge: the old inline editor now writes the unified Plugin model."""
    plugin_id = f"SKILL-{skill.id}"
    plugin = db.get(models.Plugin, plugin_id)
    if plugin is None:
        plugin = models.Plugin(
            id=plugin_id, name=skill.name, type="skill", plugin_type="skill", scope="personal",
            owner_human_no=skill.owner_human_no, endpoint_ref="mock://", data_level="L1",
            status="active", description=skill.description,
        )
        db.add(plugin); db.flush()
    plugin.name = skill.name; plugin.description = skill.description
    plugin.type = "skill"; plugin.plugin_type = "skill"
    version = "1.0.0"
    if new_version and plugin.current_version:
        parts = plugin.current_version.split(".")
        try: version = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
        except (IndexError, ValueError): version = datetime.now().strftime("1.0.%Y%m%d%H%M%S")
    elif plugin.current_version:
        version = plugin.current_version
    existing = db.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin_id, models.PluginVersion.version == version))
    if existing is None or new_version:
        for old in db.scalars(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id == plugin_id,
            models.PluginVersion.publish_status == "published")).all():
            old.publish_status = "superseded"
        db.add(models.PluginVersion(
            plugin_id=plugin_id, version=version, deployment_mode="instruction", artifact_path="",
            sha256="inline-editor", manifest={"instruction_summary": skill.description, "instruction": skill.content[:8000]},
            data_level="L1", review_status="approved", publish_status="published",
            submitted_by=skill.owner_human_no, reviewed_by="automatic-policy",
            reviewed_at=datetime.now(), published_at=datetime.now(),
        ))
    plugin.current_version = version
    # 已被管理员下架的技能不因员工编辑而自动复活：仍需管理员重新发布。
    if plugin.status != "withdrawn":
        plugin.status = "active"
    binding = db.scalar(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == plugin_id,
        models.AgentPluginBinding.target_agent_id == f"DT-{skill.owner_human_no}"))
    if binding is None:
        binding = models.AgentPluginBinding(
            plugin_id=plugin_id, target_agent_id=f"DT-{skill.owner_human_no}",
            authorized_by="automatic-policy", admin_enabled=True,
        ); db.add(binding)
    binding.employee_enabled = skill.status == "active"
    db.commit()


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
    _sync_skill_plugin(db, skill)
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
    changed = any(field in payload.model_dump(exclude_unset=True) for field in ("name", "description", "content"))
    _sync_skill_plugin(db, skill, new_version=changed)
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
    plugin = db.get(models.Plugin, f"SKILL-{skill.id}")
    if plugin is not None:
        plugin.status = "withdrawn"
        for binding in db.scalars(select(models.AgentPluginBinding).where(
            models.AgentPluginBinding.plugin_id == plugin.id)).all():
            binding.admin_enabled = False
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
            raise HTTPException(status_code=400, detail="私聊必须恰好选择 1 位AI员工")
        target_no = employee_nos[0]
        target = _get_digital_employee(db, target_no)
        if target.type == "twin" and target.owner_human_no != payload.actor_no:
            raise HTTPException(status_code=400, detail="不能与别人的数字分身私聊")
        existing = _find_direct(db, payload.actor_no, target_no)
        if existing is not None:
            if existing.hidden_at is not None:
                # 删除过的私聊重新发起时复用原会话：编号不增长，且内容已在删除时清空。
                existing.hidden_at = None
                existing.updated_at = datetime.now()
                db.add(existing)
                db.commit()
                db.refresh(existing)
            return _conversation_out(db, existing)  # 幂等复用
        participants = [{"employee_no": target_no, "role": "member"}]
        title = ""
    elif payload.kind == "group":
        participants = [{"employee_no": twin.employee_no, "role": "organizer"}]
        seen = {twin.employee_no}
        for no in employee_nos:
            emp = _get_digital_employee(db, no)
            if emp.type not in ("virtual", "rpa"):
                raise HTTPException(status_code=400, detail=f"群聊成员必须是 AI员工（类型 virtual 或 rpa）：{no}")
            if no in seen:
                raise HTTPException(status_code=400, detail=f"成员重复：{no}")
            seen.add(no)
            participants.append({"employee_no": no, "role": "member"})
        if len(participants) <= 1:
            raise HTTPException(status_code=400, detail="群聊至少需要 1 位AI员工")
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


# ⚠️ 路由顺序是硬约束：/search 必须声明在 GET /{conversation_id} 之前，
# 否则请求会被当成 conversation_id="search" 匹配掉（表现为 404 或返回空对象）。
# 测试 test_search_conversation_by_message_content 直接打 /conversations/search 锁死这一点。
@conversations_router.get("/search", response_model=list[schemas.ConversationSearchHitOut])
def search_conversations(
    actor_no: str = Query(...),
    q: str = Query("", max_length=64),
    limit: int = Query(20, ge=1, le=50),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """在会话名、成员名与历史消息正文中检索（只返回自己的会话）。

    纯读接口：检索服务内部异常一律降级为空结果，不影响 /conversations 列表。
    """
    enforce_actor(account, actor_no)
    _get_actor(db, actor_no)
    hits = conversation_search.search_conversations(db, actor_no, q, limit=limit)
    return [
        schemas.ConversationSearchHitOut(
            conversation=_summary_out(db, hit["conversation"], last_message=hit["last_message"]),
            matched_in=hit["matched_in"],
            match_count=hit["match_count"],
            match_seq=hit["match_seq"],
            match_role=hit["match_role"],
            match_speaker=hit["match_speaker"],
            snippet=hit["snippet"],
        )
        for hit in hits
    ]


@conversations_router.get("/{conversation_id}", response_model=schemas.ConversationOut)
def get_conversation(
    conversation_id: str,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    conv = db.get(models.Conversation, conversation_id)
    if conv is None or conv.hidden_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if account is not None and conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权查看该会话")
    return _conversation_out(db, conv)


def _begin_conversation_run(
    db: Session, background_tasks: BackgroundTasks, conv: models.Conversation,
    actor_no: str, content: str, *, attachment_ids: list[str] | None = None,
    retry_of_execution_id: str | None = None, reuse_message_seq: int | None = None,
) -> tuple[models.ConversationMessage, models.AgentExecution]:
    """落一条提问并开跑。reuse_message_seq 非空时改为「就地重跑」：

    不追加新的用户消息，直接改写该 seq 上已有的提问内容，并复用首次发送时
    已绑定的附件（`bind_to_message` 对已绑定附件会 409，不能重新走绑定）。
    """
    if conv.hidden_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    execution_events.fail_orphaned_executions(
        db, stale_after_seconds=config.execution_stale_after_seconds(), conversation_id=conv.id,
    )
    active = execution_events.active_execution(db, conv.id)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "当前会话已有任务正在执行", "retryable": True, "execution_id": active.id},
        )
    text = content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")
    bound: list[models.ConversationAttachment] = []
    if reuse_message_seq is None:
        user_message = append_user_message(db, conversation=conv, actor_no=actor_no, content=text)
        try:
            bound = attachments.bind_to_message(
                db, conversation=conv, owner_human_no=actor_no,
                message_seq=user_message.seq, attachment_ids=attachment_ids or [],
            )
            user_message.attachment_ids = [row.id for row in bound]
            db.add(user_message)
            db.commit()
        except Exception:
            db.delete(user_message)
            db.commit()
            raise
    else:
        user_message = db.scalar(select(models.ConversationMessage).where(
            models.ConversationMessage.conversation_id == conv.id,
            models.ConversationMessage.seq == reuse_message_seq,
            models.ConversationMessage.generation == conv.generation,
            models.ConversationMessage.role == "user",
        ))
        if user_message is None:
            raise HTTPException(status_code=409, detail="原始问题已被清空，无法重新执行")
        if user_message.attachment_ids:
            bound = list(db.scalars(select(models.ConversationAttachment).where(
                models.ConversationAttachment.id.in_(user_message.attachment_ids),
                models.ConversationAttachment.conversation_id == conv.id,
                models.ConversationAttachment.generation == conv.generation,
                models.ConversationAttachment.deleted.is_(False),
                models.ConversationAttachment.status == "ready",
            )).all())
    primary = (conv.participants or [{}])[0].get("employee_no", "")
    try:
        execution = execution_events.create_execution(
            db, conversation_id=conv.id, trigger_message_seq=user_message.seq,
            primary_employee_id=primary, conversation_generation=conv.generation,
            retry_of_execution_id=retry_of_execution_id,
        )
    except RuntimeError as exc:
        if str(exc) == "ACTIVE_EXECUTION_CONFLICT":
            # 就地重跑时附件本就绑在这条消息上、消息也必须保留，不能回滚。
            if reuse_message_seq is None:
                for row in bound:
                    row.message_seq = None
                db.delete(user_message)
                db.commit()
            raise HTTPException(
                status_code=409,
                detail={"message": "当前会话已有任务正在执行", "retryable": True},
            ) from exc
        raise
    if reuse_message_seq is not None:
        # 就地重跑：先建执行行、后改写提问。顺序反过来的话，改写已经 commit，
        # 而 create_execution 冲突时又必须保留消息 → 提问会停在一个从未跑过的状态。
        user_message.content = text
        db.add(user_message)
        _supersede_replies(db, conv, user_message.seq)
        db.commit()
        db.refresh(user_message)
    for row in bound:
        execution_events.emit(
            db, execution.id, event_type="attachment_received", stage="attachment",
            status="queued", actor_employee_id=primary,
            title=f"已接收附件 {row.filename}",
            detail=(
                f"文件已解析，共 {row.page_count} 页" if row.page_count is not None
                else f"文件已解析，共 {row.sheet_count} 个工作表" if row.sheet_count is not None
                else f"文件已解析，共 {row.chunk_count} 个内容片段"
            ),
        )
    conv.updated_at = datetime.now()
    db.add(conv)
    db.commit()
    background_tasks.add_task(
        process_conversation_async, conv.id, actor_no, text, user_message.seq, execution.id,
    )
    return user_message, execution


@conversations_router.post("/{conversation_id}/messages", response_model=schemas.ConversationOut)
def send_message(
    conversation_id: str,
    payload: schemas.ConversationSendIn,
    background_tasks: BackgroundTasks,
    response: Response,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    enforce_actor(account, payload.actor_no)
    if conv.owner_human_no != payload.actor_no:
        raise HTTPException(status_code=403, detail="无权向该会话发送消息")
    _, execution = _begin_conversation_run(
        db, background_tasks, conv, payload.actor_no, payload.content,
        attachment_ids=payload.attachment_ids,
    )
    response.headers["Deprecation"] = "true"
    response.headers["X-DWP-Execution-ID"] = execution.id
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
    user_message, execution = _begin_conversation_run(
        db, background_tasks, conv, payload.actor_no, payload.content,
        attachment_ids=payload.attachment_ids,
    )
    return schemas.ConversationRunOut(
        execution_id=execution.id,
        trigger_message_seq=user_message.seq,
        conversation=_conversation_out(db, conv),
    )


def _editable_user_message(
    db: Session, conv: models.Conversation, message_seq: int,
) -> models.ConversationMessage:
    """取「可编辑的最新一轮提问」。

    只允许改本人最新一条 user 消息。该轮是否已有回复不再是拒绝理由——重跑会把
    旧回复软作废（见 `_supersede_replies`），所以「问 → 答 → 改问 → 重答」是
    允许的；更早的轮次仍拒绝，否则后面的问答会整体失配。
    """
    message = db.scalar(select(models.ConversationMessage).where(
        models.ConversationMessage.conversation_id == conv.id,
        models.ConversationMessage.seq == message_seq,
        models.ConversationMessage.generation == conv.generation,
    ))
    if message is None or message.role != "user":
        raise HTTPException(status_code=404, detail="消息不存在")
    latest = db.scalar(
        select(models.ConversationMessage)
        .where(
            models.ConversationMessage.conversation_id == conv.id,
            models.ConversationMessage.generation == conv.generation,
            models.ConversationMessage.role == "user",
        )
        .order_by(models.ConversationMessage.seq.desc())
        .limit(1)
    )
    if latest is None or latest.seq != message.seq:
        raise HTTPException(status_code=409, detail="只能编辑最新一轮提问")
    return message


def _turn_has_reply(db: Session, conv: models.Conversation, message_seq: int) -> bool:
    """该轮是否已有「仍然有效」的回复（已被取代的不算）。"""
    return db.scalar(select(models.ConversationMessage).where(
        models.ConversationMessage.conversation_id == conv.id,
        models.ConversationMessage.generation == conv.generation,
        models.ConversationMessage.role == "assistant",
        models.ConversationMessage.seq > message_seq,
        models.ConversationMessage.superseded_at.is_(None),
    ).limit(1)) is not None


def _supersede_replies(db: Session, conv: models.Conversation, message_seq: int) -> int:
    """把该轮已有回复标记为「已被取代」（软作废，不删除）。

    旧答案在界面上折叠成「上一版回复」仍可回看，只是不再进入模型上下文。
    这里只改字段、不 commit，由调用方决定提交时机。
    """
    rows = db.scalars(select(models.ConversationMessage).where(
        models.ConversationMessage.conversation_id == conv.id,
        models.ConversationMessage.generation == conv.generation,
        models.ConversationMessage.role == "assistant",
        models.ConversationMessage.seq > message_seq,
        models.ConversationMessage.superseded_at.is_(None),
    )).all()
    now = datetime.now()
    for row in rows:
        row.superseded_at = now
        db.add(row)
    return len(rows)


def _assert_no_active_run(db: Session, conv: models.Conversation) -> None:
    execution_events.fail_orphaned_executions(
        db, stale_after_seconds=config.execution_stale_after_seconds(), conversation_id=conv.id,
    )
    active = execution_events.active_execution(db, conv.id)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "当前会话已有任务正在执行", "retryable": True, "execution_id": active.id},
        )


@conversations_router.patch(
    "/{conversation_id}/messages/{message_seq}", response_model=schemas.ConversationMessageOut,
)
def edit_conversation_message(
    conversation_id: str,
    message_seq: int,
    payload: schemas.ConversationMessagePatchIn,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """就地改写最新一轮提问，不触发执行。"""
    conv = db.get(models.Conversation, conversation_id)
    if conv is None or conv.hidden_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    enforce_actor(account, payload.actor_no)
    if conv.owner_human_no != payload.actor_no:
        raise HTTPException(status_code=403, detail="无权修改该会话的消息")
    message = _editable_user_message(db, conv, message_seq)
    text = payload.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")
    if _turn_has_reply(db, conv, message_seq):
        # 只改文字而不重跑，回复会挂在一个它并没有回答过的问题下面；重跑会把旧回复
        # 软作废，所以这里引导用户改走「保存并重跑」。
        raise HTTPException(status_code=409, detail="该轮已收到回复，请用「保存并重跑」")
    _assert_no_active_run(db, conv)
    message.content = text
    conv.updated_at = datetime.now()
    db.add(message)
    db.add(conv)
    db.commit()
    db.refresh(message)
    write_audit(
        db, trace_id="", employee_id=payload.actor_no, plugin_id="",
        action="conversation_message_edit", decision="allow",
        reason=f"conversation={conv.id} seq={message_seq}", result_summary=text[:200],
    )
    return schemas.ConversationMessageOut.model_validate(message)


@conversations_router.post(
    "/{conversation_id}/messages/{message_seq}/runs", response_model=schemas.ConversationRunOut,
)
def rerun_conversation_message(
    conversation_id: str,
    message_seq: int,
    payload: schemas.ConversationMessageRunIn,
    background_tasks: BackgroundTasks,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """就地编辑最新一轮提问并重跑：复用原 seq，不追加新的用户消息。"""
    conv = db.get(models.Conversation, conversation_id)
    if conv is None or conv.hidden_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    enforce_actor(account, payload.actor_no)
    if conv.owner_human_no != payload.actor_no:
        raise HTTPException(status_code=403, detail="无权向该会话发送消息")
    message = _editable_user_message(db, conv, message_seq)
    text = (payload.content if payload.content is not None else message.content).strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")
    previous = db.scalar(
        select(models.AgentExecution)
        .where(
            models.AgentExecution.conversation_id == conv.id,
            models.AgentExecution.conversation_generation == conv.generation,
            models.AgentExecution.trigger_message_seq == message.seq,
        )
        .order_by(models.AgentExecution.started_at.desc(), models.AgentExecution.id.desc())
        .limit(1)
    )
    user_message, execution = _begin_conversation_run(
        db, background_tasks, conv, payload.actor_no, text,
        retry_of_execution_id=previous.id if previous is not None else None,
        reuse_message_seq=message.seq,
    )
    return schemas.ConversationRunOut(
        execution_id=execution.id,
        trigger_message_seq=user_message.seq,
        conversation=_conversation_out(db, conv),
    )


@conversations_router.post(
    "/{conversation_id}/runs/{execution_id}/retry", response_model=schemas.ConversationRunOut,
)
def retry_conversation_run(
    conversation_id: str,
    execution_id: str,
    background_tasks: BackgroundTasks,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    conv = db.get(models.Conversation, conversation_id)
    original = db.get(models.AgentExecution, execution_id)
    if conv is None or original is None or original.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    if account is not None and conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权重试该执行")
    trigger = db.scalar(select(models.ConversationMessage).where(
        models.ConversationMessage.conversation_id == conversation_id,
        models.ConversationMessage.seq == original.trigger_message_seq,
        models.ConversationMessage.generation == original.conversation_generation,
        models.ConversationMessage.role == "user",
    ))
    if trigger is None or original.conversation_generation != conv.generation:
        raise HTTPException(status_code=409, detail="原始问题已被清空，无法重新执行")
    if trigger.attachment_ids:
        available = db.scalars(select(models.ConversationAttachment).where(
            models.ConversationAttachment.id.in_(trigger.attachment_ids),
            models.ConversationAttachment.conversation_id == conversation_id,
            models.ConversationAttachment.generation == conv.generation,
            models.ConversationAttachment.deleted.is_(False),
            models.ConversationAttachment.status == "ready",
        )).all()
        if len(available) != len(set(trigger.attachment_ids)):
            raise HTTPException(status_code=409, detail="原执行使用的附件已删除或失效，无法重新执行")
    user_message, execution = _begin_conversation_run(
        db, background_tasks, conv, conv.owner_human_no, trigger.content,
        retry_of_execution_id=original.id,
    )
    if trigger.attachment_ids:
        user_message.attachment_ids = list(trigger.attachment_ids)
        db.add(user_message)
        db.commit()
    return schemas.ConversationRunOut(
        execution_id=execution.id, trigger_message_seq=user_message.seq,
        conversation=_conversation_out(db, conv),
    )


@conversations_router.post(
    "/{conversation_id}/runs/{execution_id}/cancel", response_model=schemas.AgentExecutionOut,
)
def cancel_conversation_run(
    conversation_id: str,
    execution_id: str,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """停止本次执行：终态为 cancelled，不可续跑（dsh 是一次性 CLI 调用，无暂停语义）。

    只停止这一条执行，不波及同会话其它历史执行；重复点击幂等。
    """
    conv = db.get(models.Conversation, conversation_id)
    execution = db.get(models.AgentExecution, execution_id)
    if conv is None or execution is None or execution.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    if conv.hidden_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if account is not None and conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权停止该执行")
    cancelled = execution_events.cancel_execution(db, execution_id)
    if cancelled is None:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return cancelled


@conversations_router.get(
    "/{conversation_id}/input-requests", response_model=list[schemas.ToolInputRequestOut],
)
def list_conversation_input_requests(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """会话内全部「待补全」记录（前端按 execution_id 分组渲染补全气泡）。"""
    conv = db.get(models.Conversation, conversation_id)
    if conv is None or conv.hidden_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if account is not None and conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权查看该会话")
    return tool_input.list_for_conversation(db, conversation_id, limit=limit)


@conversations_router.get(
    "/{conversation_id}/runs/{execution_id}/input-requests",
    response_model=list[schemas.ToolInputRequestOut],
)
def list_run_input_requests(
    conversation_id: str,
    execution_id: str,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """单条执行下的「待补全」记录。"""
    conv = db.get(models.Conversation, conversation_id)
    execution = db.get(models.AgentExecution, execution_id)
    if conv is None or execution is None or execution.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    if account is not None and conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权查看该执行")
    return tool_input.list_for_execution(db, execution_id)


@conversations_router.post(
    "/{conversation_id}/runs/{execution_id}/input", response_model=schemas.ConversationRunOut,
)
def submit_run_input(
    conversation_id: str,
    execution_id: str,
    payload: schemas.ToolInputSubmitIn,
    background_tasks: BackgroundTasks,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """提交缺失信息并自动续跑一轮。

    凭据类字段加密存到「本人 + 该 MCP」名下（明文不落库、不回显、不进对话与审计）；
    参数类字段拼进续跑内容（非敏感业务参数）。提交后以「原提问 + 补充说明」自动重跑，
    用户不需要再发一句话。上一轮仍在执行时透传 409（retryable），由前端提示稍后提交。
    """
    conv = db.get(models.Conversation, conversation_id)
    execution = db.get(models.AgentExecution, execution_id)
    if conv is None or conv.hidden_at is not None or execution is None or execution.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    enforce_actor(account, payload.actor_no)
    if conv.owner_human_no != payload.actor_no:
        raise HTTPException(status_code=403, detail="无权提交该执行的信息补全")
    request = tool_input.get_request(db, payload.request_id)
    if (
        request is None
        or request.conversation_id != conversation_id
        or request.execution_id != execution_id
    ):
        raise HTTPException(status_code=404, detail="补全请求不存在")
    if request.status != tool_input.STATUS_PENDING:
        raise HTTPException(status_code=409, detail="该补全请求已处理，无需重复提交")

    fields = list(request.fields or [])
    values = {str(key): str(value) for key, value in (payload.values or {}).items()}
    missing_required = [
        str(field.get("key") or "") for field in fields
        if tool_input.field_needs_value(field)
        and not (values.get(str(field.get("key") or "")) or "").strip()
    ]
    if missing_required:
        raise HTTPException(status_code=422, detail=f"请先填写：{'、'.join(missing_required)}")

    # 凭据类：加密存到本人名下（按人隔离，重复提交即覆盖轮换）。
    for field in fields:
        if str(field.get("kind") or "") != tool_input.KIND_SECRET:
            continue
        key = str(field.get("key") or "")
        value = (values.get(key) or "").strip()
        if not key or not value:
            continue
        mcp_credentials.save_user_credential(
            db, plugin_id=request.plugin_id, human_no=payload.actor_no,
            header_name=key, secret=value, updated_by=payload.actor_no,
            expires_at=mcp_credentials.jwt_expiry(value),
        )
    db.commit()

    trigger = db.scalar(select(models.ConversationMessage).where(
        models.ConversationMessage.conversation_id == conversation_id,
        models.ConversationMessage.seq == execution.trigger_message_seq,
        models.ConversationMessage.generation == execution.conversation_generation,
        models.ConversationMessage.role == "user",
    ))
    original = trigger.content if trigger is not None else "请继续刚才的任务"
    content = tool_input.build_resume_content(original, fields, values)

    # 先起新一轮执行、成功后再标记已提交：上一轮仍在跑时 _begin_conversation_run 会 409，
    # 此时保持 pending，用户稍后重试即可（凭据已保存，重试不会丢）。
    user_message, new_execution = _begin_conversation_run(
        db, background_tasks, conv, payload.actor_no, content,
        retry_of_execution_id=execution_id,
    )
    masked = tool_input.masked_values(fields, values)
    tool_input.mark_submitted(db, request, submitted_values=masked, resolved_by=payload.actor_no)
    db.commit()
    write_audit(
        db, trace_id=execution.trace_id, employee_id=payload.actor_no,
        plugin_id=request.plugin_id, action="input_submit", decision="allow",
        reason=(
            f"补全 {request.tool_name} 的缺失信息："
            + "、".join(str(field.get("key") or "") for field in fields)
        ),
        # 只记掩码：凭据明文绝不进审计。
        result_summary=json.dumps(masked, ensure_ascii=False),
    )
    return schemas.ConversationRunOut(
        execution_id=new_execution.id,
        trigger_message_seq=user_message.seq,
        conversation=_conversation_out(db, conv),
    )


@conversations_router.post(
    "/{conversation_id}/runs/{execution_id}/input-requests/{request_id}/ignore",
    response_model=schemas.ToolInputRequestOut,
)
def ignore_run_input(
    conversation_id: str,
    execution_id: str,
    request_id: str,
    payload: schemas.ToolInputIgnoreIn,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """忽略一条「待补全」：标记 cancelled，刷新后不再重复弹出。

    必须落库：只在前端本地记住「忽略过」的话，用户刷新页面气泡会重新冒出来。
    忽略只是不再提示，不会删除任何已保存的凭据。
    """
    conv = db.get(models.Conversation, conversation_id)
    execution = db.get(models.AgentExecution, execution_id)
    if conv is None or conv.hidden_at is not None or execution is None or execution.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    enforce_actor(account, payload.actor_no)
    if conv.owner_human_no != payload.actor_no:
        raise HTTPException(status_code=403, detail="无权处理该执行的信息补全")
    request = tool_input.get_request(db, request_id)
    if (
        request is None
        or request.conversation_id != conversation_id
        or request.execution_id != execution_id
    ):
        raise HTTPException(status_code=404, detail="补全请求不存在")
    if request.status != tool_input.STATUS_PENDING:
        raise HTTPException(status_code=409, detail="该补全请求已处理，无需重复忽略")
    tool_input.mark_cancelled(db, request, resolved_by=payload.actor_no)
    db.commit()
    db.refresh(request)
    return request


@conversations_router.get("/{conversation_id}/runs/active", response_model=schemas.AgentExecutionOut | None)
def get_active_conversation_run(
    conversation_id: str,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    conv = db.get(models.Conversation, conversation_id)
    if conv is None or conv.hidden_at is not None:
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
            models.AgentExecution.conversation_generation == conv.generation,
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
    limit: int = Query(default=20, ge=1, le=100),
    before_execution_id: str | None = Query(default=None),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """Return durable safe execution traces for every retained turn in a conversation."""
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if account is not None and conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权查看该会话")
    query = select(models.AgentExecution).where(
        models.AgentExecution.conversation_id == conversation_id,
        models.AgentExecution.conversation_generation == conv.generation,
    )
    if before_execution_id:
        cursor = db.get(models.AgentExecution, before_execution_id)
        if cursor is None or cursor.conversation_id != conversation_id or cursor.conversation_generation != conv.generation:
            raise HTTPException(status_code=400, detail="无效的执行历史游标")
        query = query.where(or_(
            models.AgentExecution.started_at < cursor.started_at,
            and_(
                models.AgentExecution.started_at == cursor.started_at,
                models.AgentExecution.id < cursor.id,
            ),
        ))
    executions = list(reversed(db.scalars(
        query.order_by(models.AgentExecution.started_at.desc(), models.AgentExecution.id.desc()).limit(limit)
    ).all()))
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
            # 已停止的执行：不再继续吐字，但必须把终态事件（cancelled）送出去，
            # 否则前端只能靠本地状态收敛，重连时会一直停在 running。
            cancelled_now = current is not None and current.status == "cancelled"
            emitted = False
            for row in events:
                if row.event_type == "answer_chunk":
                    if cancelled_now:
                        cursor_seq, cursor_char = row.event_seq, -1
                        continue
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
                        # 事件类型透给前端：前端要据此识别 input_required（弹补全气泡）等特殊事件。
                        "event_type": row.event_type,
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
                    event_name = row.event_type if row.event_type in {"error", "answer_done", "cancelled"} else "progress"
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
        raise HTTPException(status_code=400, detail="只能添加 AI员工（类型 virtual 或 rpa）")
    participants = list(conv.participants or [])
    if any(p["employee_no"] == payload.employee_no for p in participants):
        raise HTTPException(status_code=400, detail="成员已在会话中")
    participants.append({"employee_no": payload.employee_no, "role": "member"})
    conv.participants = participants
    db.commit()
    db.refresh(conv)
    return _conversation_out(db, conv)


@conversations_router.post("/{conversation_id}/clear", response_model=schemas.ClearConversationOut)
def clear_conversation(
    conversation_id: str,
    actor_no: str = Query(...),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """清空会话内容（消息/任务/执行轨迹/附件/记忆摘要），保留会话卡片。"""
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    enforce_actor(account, actor_no)
    if conv.owner_human_no != actor_no:
        raise HTTPException(status_code=403, detail="无权清空该会话")
    conversation_cleanup.purge_conversation_content(db, conv)
    return schemas.ClearConversationOut(ok=True)


@conversations_router.delete("/{conversation_id}", response_model=schemas.ClearConversationOut)
def delete_conversation(
    conversation_id: str,
    actor_no: str = Query(...),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """删除会话卡片：先物理清空内容，再标记为已隐藏（私聊可在重新发起时复用）。"""
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    enforce_actor(account, actor_no)
    if conv.owner_human_no != actor_no:
        raise HTTPException(status_code=403, detail="无权删除该会话")
    conversation_cleanup.purge_conversation_content(db, conv)
    conv.hidden_at = datetime.now()
    db.add(conv)
    db.commit()
    return schemas.ClearConversationOut(ok=True)
