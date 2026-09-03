from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import runtime_manager
from ..services import config
from ..services.auth import current_account, optional_account, require_roles
from ..services.identity import resolve_identity
from ..services.knowledge_registry import plugin_id_for_level
from ..services.policy import ResourceRef, evaluate

router = APIRouter(prefix="/agents", tags=["agents"])


def _profile_out(db: Session, employee: models.DigitalEmployee) -> schemas.AgentProfileOut:
    profile = db.get(models.AgentProfile, employee.employee_no)
    runtime = runtime_manager.ensure_runtime(db, employee.employee_no)
    grants = db.scalars(
        select(models.AgentKnowledgeGrant).where(
            models.AgentKnowledgeGrant.employee_id == employee.employee_no,
            models.AgentKnowledgeGrant.status == "active",
        )
    ).all()
    return schemas.AgentProfileOut(
        employee_id=employee.employee_no,
        display_name=employee.name,
        identity_kind=profile.identity_kind if profile else ("human_twin" if employee.type == "twin" else "role_employee"),
        owner_human_no=employee.owner_human_no,
        department=employee.department,
        responsibilities=profile.responsibilities if profile else [],
        knowledge_domains=profile.knowledge_domains if profile else [],
        accepts_tasks=profile.accepts_tasks if profile else ["knowledge_question"],
        delegation_policy=profile.delegation_policy if profile else "none",
        fallback_employee_id=profile.fallback_employee_id if profile else None,
        persona_status=profile.persona_status if profile else "published",
        persona_version=profile.persona_version if profile else 1,
        runtime_engine=runtime.engine,
        runtime_state=runtime.state,
        container_name=runtime.container_name,
        knowledge_base_ids=[grant.knowledge_base_id for grant in grants],
    )


@router.get("", response_model=list[schemas.AgentProfileOut])
def list_agents(_: models.Account = Depends(current_account), db: Session = Depends(get_db)):
    employees = db.scalars(
        select(models.DigitalEmployee)
        .where(models.DigitalEmployee.status == "active")
        .order_by(models.DigitalEmployee.type, models.DigitalEmployee.employee_no)
    ).all()
    return [_profile_out(db, employee) for employee in employees]


_KB_EXAMPLES = {
    "KB-IT-SERVICE": ["VPN 申请需要哪些步骤？", "企业邮箱无法登录应该怎么处理？"],
    "KB-REG-INTERNAL": ["这项内部制度的执行要求是什么？"],
    "KB-REG-EXTERNAL": ["相关外部监管规则有哪些要求？"],
    "KB-SECURITIES": ["这项证券业务规则如何理解？"],
    "KB-INVESTMENT-BANKING": ["投行项目尽调通常需要关注什么？"],
}


@router.get("/{employee_id}/effective-capabilities", response_model=schemas.EffectiveCapabilitiesOut)
def get_effective_capabilities(
    employee_id: str,
    _: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """Return what an agent can use now, not merely what exists in the catalog."""
    employee = db.get(models.DigitalEmployee, employee_id)
    if employee is None or employee.status != "active":
        raise HTTPException(status_code=404, detail="数字员工不存在或未启用")
    subject = resolve_identity(db, employee_id)
    if subject is None:
        raise HTTPException(status_code=409, detail="数字员工身份未配置")
    profile = db.get(models.AgentProfile, employee_id)
    runtime = runtime_manager.ensure_runtime(db, employee_id)
    runtime_healthy = runtime.state in {"ready", "busy"}

    grant_rows = db.scalars(
        select(models.AgentKnowledgeGrant).where(models.AgentKnowledgeGrant.employee_id == employee_id)
    ).all()
    knowledge_ids = {row.knowledge_base_id for row in grant_rows if row.action == "read" and row.status == "active"}
    capabilities: list[schemas.EffectiveCapabilityItemOut] = []
    knowledge_bases = db.scalars(select(models.KnowledgeBase).order_by(models.KnowledgeBase.id)).all()
    for kb in knowledge_bases:
        explicitly_granted = kb.id in knowledge_ids
        policy = evaluate(
            db,
            subject,
            ResourceRef(type="knowledge", id=plugin_id_for_level(kb.data_level), data_level=kb.data_level),
            "read",
        )
        decision = policy.decision if explicitly_granted and kb.status == "active" else "deny"
        authorized = decision in {"allow", "approval"}
        if kb.status != "active":
            status = "disabled"
            reason = "知识库已停用"
        elif not explicitly_granted:
            status = "unauthorized"
            reason = "未授予该数字员工具体知识库权限"
        elif decision == "deny":
            status = "unauthorized"
            reason = policy.reason or "Policy 拒绝访问"
        elif not runtime_healthy:
            status = "runtime_unavailable"
            reason = "Harness 运行环境未就绪"
        elif decision == "approval":
            status = "approval"
            reason = policy.reason or "访问前需要审批"
        else:
            status = "available"
            reason = "知识授权、Policy 与 Harness 均已就绪"
        capabilities.append(schemas.EffectiveCapabilityItemOut(
            id=f"knowledge:{kb.id}", name=kb.name, kind="knowledge", source_type="knowledge_base",
            description=kb.description, actions=["read"], status=status, decision=decision,
            reason=reason, authorized=authorized, installed=runtime_healthy,
            healthy=runtime_healthy and authorized, data_level=kb.data_level,
            knowledge_base_id=kb.id, example_prompts=_KB_EXAMPLES.get(kb.id, [f"请查询{kb.name}并说明相关规定。"]),
        ))

    if employee.type == "twin":
        targets = [row.employee_no for row in db.scalars(
            select(models.DigitalEmployee).where(
                models.DigitalEmployee.status == "active",
                models.DigitalEmployee.type == "virtual",
            ).order_by(models.DigitalEmployee.employee_no)
        ).all()]
        delegation_enabled = bool(profile and profile.delegation_policy == "bounded_single" and targets)
        status = "available" if delegation_enabled and runtime_healthy else (
            "runtime_unavailable" if delegation_enabled else "unauthorized"
        )
        capabilities.append(schemas.EffectiveCapabilityItemOut(
            id="platform:delegate", name="向数字员工求助", kind="delegation", source_type="platform_tool",
            description="分身无法独立回答时，可向一名岗位数字员工委派一次。",
            actions=["delegate"], status=status, decision="allow" if delegation_enabled else "deny",
            reason=("委派深度固定为 1，目标员工不能继续委派" if status == "available" else
                    "Harness 运行环境未就绪" if status == "runtime_unavailable" else "当前身份未启用委派"),
            authorized=delegation_enabled, installed=runtime_healthy, healthy=status == "available",
            target_employee_ids=targets, example_prompts=["这道专业问题请帮我咨询合适的数字员工。"],
        ))

    available_count = sum(item.status in {"available", "approval"} for item in capabilities)
    return schemas.EffectiveCapabilitiesOut(
        employee_id=employee.employee_no, display_name=employee.name,
        identity_kind=profile.identity_kind if profile else ("human_twin" if employee.type == "twin" else "role_employee"),
        runtime_engine=runtime.engine, runtime_state=runtime.state, container_name=runtime.container_name,
        knowledge_mode=config.kb_mode(), capabilities=capabilities,
        available_count=available_count, attention_count=len(capabilities) - available_count,
    )


@router.get("/{employee_id}/runtime", response_model=schemas.AgentRuntimeOutV1)
def get_runtime(employee_id: str, _: models.Account = Depends(current_account), db: Session = Depends(get_db)):
    if db.get(models.DigitalEmployee, employee_id) is None:
        raise HTTPException(status_code=404, detail="数字员工不存在")
    return runtime_manager.ensure_runtime(db, employee_id)


@router.post("/{employee_id}/runtime/start", response_model=schemas.AgentRuntimeOutV1)
def start_runtime(
    employee_id: str,
    _: models.Account = Depends(require_roles("agent_admin", "platform_admin")),
    db: Session = Depends(get_db),
):
    if db.get(models.DigitalEmployee, employee_id) is None:
        raise HTTPException(status_code=404, detail="数字员工不存在")
    try:
        return runtime_manager.start(db, employee_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{employee_id}/runtime/stop", response_model=schemas.AgentRuntimeOutV1)
def stop_runtime(
    employee_id: str,
    _: models.Account = Depends(require_roles("agent_admin", "platform_admin")),
    db: Session = Depends(get_db),
):
    try:
        return runtime_manager.stop(db, employee_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{employee_id}/persona/drafts", response_model=schemas.PersonaVersionOut)
def create_persona_draft(
    employee_id: str,
    payload: schemas.PersonaDraftIn,
    account: models.Account = Depends(require_roles("agent_admin", "platform_admin")),
    db: Session = Depends(get_db),
):
    employee = db.get(models.DigitalEmployee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="数字员工不存在")
    profile = db.get(models.AgentProfile, employee_id)
    responsibilities = payload.responsibilities or (profile.responsibilities if profile else [])
    version = (db.scalar(select(func.max(models.PersonaVersion.version)).where(models.PersonaVersion.employee_id == employee_id)) or 0) + 1
    responsibility_text = "；".join(responsibilities) or "按照已配置岗位职责提供知识服务"
    content = (
        f"你是{employee.name}，是公司中的一位数字员工。\n"
        f"你所在的部门是{employee.department or '公司共享服务部门'}。\n"
        f"你的主要职责是：{responsibility_text}。\n"
        f"{payload.project_context.strip()}\n"
        "工作时先理解同事真正要解决的问题，再查找有权限的正式资料；资料不足时明确说明，不编造。"
    ).strip()
    row = models.PersonaVersion(
        employee_id=employee_id,
        version=version,
        status="draft",
        content=content,
        source_refs=payload.source_refs,
    )
    db.add(row)
    if profile is not None:
        profile.persona_status = "draft"
    db.commit()
    db.refresh(row)
    return row


@router.post("/persona-drafts/{draft_id}/approve", response_model=schemas.PersonaVersionOut)
def approve_persona_draft(
    draft_id: int,
    account: models.Account = Depends(require_roles("agent_admin", "platform_admin")),
    db: Session = Depends(get_db),
):
    row = db.get(models.PersonaVersion, draft_id)
    if row is None or row.status != "draft":
        raise HTTPException(status_code=404, detail="待审核人设草稿不存在")
    employee = db.get(models.DigitalEmployee, row.employee_id)
    profile = db.get(models.AgentProfile, row.employee_id)
    if employee is None or profile is None:
        raise HTTPException(status_code=409, detail="数字员工资料不完整")
    published = db.scalars(
        select(models.PersonaVersion).where(
            models.PersonaVersion.employee_id == row.employee_id,
            models.PersonaVersion.status == "published",
        )
    ).all()
    for old in published:
        old.status = "superseded"
    row.status = "published"
    row.reviewed_by = account.human_employee_no
    employee.role_prompt = row.content
    profile.persona_status = "published"
    profile.persona_version = row.version
    db.commit()
    db.refresh(row)
    return row
