from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import runtime_manager, runtime_registry
from ..services.auth import current_account, optional_account, require_roles
from ..services.identity import resolve_identity
from ..services.knowledge_registry import plugin_id_for_level
from ..services.knowledge_source_health import is_kb_available
from ..services.plugin_governance import skill_artifact_slug
from ..services.policy import ResourceRef, evaluate

router = APIRouter(prefix="/agents", tags=["agents"])


def _profile_out(
    db: Session,
    employee: models.DigitalEmployee,
    environments: dict[str, runtime_registry.HarnessEnvironment] | None = None,
) -> schemas.AgentProfileOut:
    profile = db.get(models.AgentProfile, employee.employee_no)
    runtime = runtime_manager.ensure_runtime(db, employee.employee_no)
    environment = runtime_registry.environment_for(employee.employee_no, environments or {})
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
        runtime_image=environment.image,
        runtime_version=environment.runtime_version,
        runtime_profile_rev=environment.profile_rev,
        knowledge_base_ids=[grant.knowledge_base_id for grant in grants],
    )


@router.get("", response_model=list[schemas.AgentProfileOut])
def list_agents(_: models.Account = Depends(current_account), db: Session = Depends(get_db)):
    employees = db.scalars(
        select(models.DigitalEmployee)
        .where(models.DigitalEmployee.status == "active")
        .order_by(models.DigitalEmployee.type, models.DigitalEmployee.employee_no)
    ).all()
    # 一次容器列表查询覆盖全部行（含按AI员工灰度的 dsh 版本），失败自动降级为声明值。
    environments = runtime_registry.harness_environments()
    return [_profile_out(db, employee, environments) for employee in employees]


@router.get("/runtime-info", response_model=schemas.RuntimeInfoOut)
def runtime_info(_: models.Account = Depends(current_account)):
    """运维页顶部汇总：平台镜像（backend/frontend）与 Harness 底座镜像、dsh 版本。"""
    return schemas.RuntimeInfoOut(
        delivery_tag=runtime_registry.declared_delivery_tag(),
        images=[schemas.RuntimeImageOut(**item) for item in runtime_registry.platform_images()],
    )


_KB_EXAMPLES = {
    "KB-IT-SERVICE": ["VPN 申请需要哪些步骤？", "企业邮箱无法登录应该怎么处理？"],
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
        raise HTTPException(status_code=404, detail="AI员工不存在或未启用")
    subject = resolve_identity(db, employee_id)
    if subject is None:
        raise HTTPException(status_code=409, detail="AI员工身份未配置")
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
            reason = "未授予该AI员工具体知识库权限"
        elif decision == "deny":
            status = "unauthorized"
            reason = policy.reason or "Policy 拒绝访问"
        elif not is_kb_available(db, kb):
            # 授权没问题、Policy 也放行，只是数据源暂时不可达：与「没权限」区分开，
            # 让核验页能直接看出是连通性问题（管理端可一键重测）。
            status = "source_unavailable"
            reason = "该知识库对应的内部知识源当前不可达"
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
            id="platform:delegate", name="向AI员工求助", kind="delegation", source_type="platform_tool",
            description="分身无法独立回答时，可向一名岗位AI员工委派一次。",
            actions=["delegate"], status=status, decision="allow" if delegation_enabled else "deny",
            reason=("委派深度固定为 1，目标员工不能继续委派" if status == "available" else
                    "Harness 运行环境未就绪" if status == "runtime_unavailable" else "当前身份未启用委派"),
            authorized=delegation_enabled, installed=runtime_healthy, healthy=status == "available",
            target_employee_ids=targets, example_prompts=["这道专业问题请帮我咨询合适的AI员工。"],
        ))

    # 插件与技能维度：与员工端目录、Harness 提示词、网关判定同源（AgentPluginBinding）。
    # 这里逐条列出绑定状态，使「管理员关掉授权」在核验页立即可见，而不是只显示已生效的插件。
    bindings = db.scalars(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.target_agent_id == employee_id,
    ).order_by(models.AgentPluginBinding.priority, models.AgentPluginBinding.id)).all()
    # 技能投放态的真源是文件系统，整页只算一次（避免逐条技能各扫一遍目录）。
    skill_deploy: dict[str, dict] | None = None
    for binding in bindings:
        plugin = db.get(models.Plugin, binding.plugin_id)
        if plugin is None:
            continue
        is_skill = plugin.plugin_type == "skill"
        version_no = binding.pinned_version or plugin.current_version
        version = db.scalar(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id == plugin.id,
            models.PluginVersion.version == version_no,
            models.PluginVersion.publish_status == "published",
        )) if version_no else None
        published = version is not None
        if plugin.status != "active":
            status, reason = "disabled", "插件已下架或停用"
        elif not binding.admin_enabled:
            status, reason = "unauthorized", "管理员尚未授权该插件"
        elif not binding.employee_enabled:
            status, reason = "disabled", "AI员工尚未启用（可在插件中心添加）"
        elif not published:
            status, reason = "disabled", "没有已发布版本可供使用"
        elif binding.decision_mode == "deny":
            status, reason = "disabled", "插件授权决策为禁止"
        elif binding.decision_mode == "approval":
            status, reason = "approval", "使用时需要人工审批（当前没有插件审批流）"
        elif not runtime_healthy:
            status, reason = "runtime_unavailable", "Harness 运行环境未就绪"
        else:
            status, reason = "available", "授权、版本与运行环境均已就绪"

        deploy_field: str | None = None
        deploy_detail = ""
        deployed_at: str | None = None
        installed = published
        if is_skill:
            if skill_deploy is None:
                skill_deploy = runtime_manager.deploy_state(db, employee_id)
            slug = skill_artifact_slug(plugin, version) if version is not None else plugin.id
            info = skill_deploy.get(slug)
            if info is None:
                # 未在授权范围内（未启用 / 未授权 / 已下架）：工作区里不该有包。
                deploy_field, installed = ("missing" if published else None), False
                if published:
                    deploy_detail = "尚未投放到该AI员工的工作区"
            else:
                deploy_field = str(info.get("state") or "") or None
                deploy_detail = str(info.get("detail") or "")
                deployed_at = info.get("deployed_at")
                installed = deploy_field in {"deployed", "stale"}
            if deploy_field == "stale" and status == "available":
                reason = f"{reason}；工作区内容为旧版本，待下一次投放刷新"
            elif deploy_field in {"failed", "missing"} and status == "available":
                # 授权没问题、但技能包没落到容器里 → 实际用不了，按运行环境问题呈现。
                status = "runtime_unavailable"
                reason = f"技能包{deploy_detail or '尚未投放'}"
        capabilities.append(schemas.EffectiveCapabilityItemOut(
            id=f"{'skill' if is_skill else 'plugin'}:{plugin.id}",
            name=plugin.name,
            kind="skill" if is_skill else "plugin",
            source_type="skill" if is_skill else "platform_tool",
            description=plugin.description or ("数字分身可用的工作方法技能" if is_skill else "经网关执行的受控工具"),
            actions=["inject"] if is_skill else ["execute"],
            status=status,
            decision=binding.decision_mode if binding.decision_mode in {"allow", "approval", "deny"} else "deny",
            reason=reason,
            authorized=bool(binding.admin_enabled and binding.employee_enabled and published),
            installed=installed,
            healthy=runtime_healthy and status == "available",
            data_level=plugin.data_level,
            deploy_state=deploy_field,
            deploy_detail=deploy_detail,
            deployed_at=deployed_at,
        ))

    available_count = sum(item.status in {"available", "approval"} for item in capabilities)
    return schemas.EffectiveCapabilitiesOut(
        employee_id=employee.employee_no, display_name=employee.name,
        identity_kind=profile.identity_kind if profile else ("human_twin" if employee.type == "twin" else "role_employee"),
        runtime_engine=runtime.engine, runtime_state=runtime.state, container_name=runtime.container_name,
        knowledge_mode="internal", capabilities=capabilities,
        available_count=available_count, attention_count=len(capabilities) - available_count,
    )


@router.get("/{employee_id}/runtime", response_model=schemas.AgentRuntimeOutV1)
def get_runtime(employee_id: str, _: models.Account = Depends(current_account), db: Session = Depends(get_db)):
    if db.get(models.DigitalEmployee, employee_id) is None:
        raise HTTPException(status_code=404, detail="AI员工不存在")
    return runtime_manager.ensure_runtime(db, employee_id)


@router.post("/{employee_id}/runtime/start", response_model=schemas.AgentRuntimeOutV1)
def start_runtime(
    employee_id: str,
    _: models.Account = Depends(require_roles("agent_admin", "platform_admin")),
    db: Session = Depends(get_db),
):
    if db.get(models.DigitalEmployee, employee_id) is None:
        raise HTTPException(status_code=404, detail="AI员工不存在")
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
        raise HTTPException(status_code=404, detail="AI员工不存在")
    profile = db.get(models.AgentProfile, employee_id)
    responsibilities = payload.responsibilities or (profile.responsibilities if profile else [])
    version = (db.scalar(select(func.max(models.PersonaVersion.version)).where(models.PersonaVersion.employee_id == employee_id)) or 0) + 1
    responsibility_text = "；".join(responsibilities) or "按照已配置岗位职责提供知识服务"
    content = (
        f"你是{employee.name}，是公司中的一位AI员工。\n"
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
        raise HTTPException(status_code=409, detail="AI员工资料不完整")
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
