from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.access_request import (
    create_plugin_request,
    decide_access_request,
    plugin_request_subject,
    resolve_plugin_for_request,
)
from ..services import skill_intro, skillhub_client
from ..services.auth import current_account, require_roles
from ..services.plugin_governance import (
    available_plugins,
    effective_plugins,
    market_plugins,
    publish_version,
    resubmit_version,
    restore_plugin,
    submit_zip,
    withdraw_plugin,
)

my_router = APIRouter(prefix="/my/plugins", tags=["my-plugins"])
admin_router = APIRouter(prefix="/admin", tags=["plugin-governance"])
admin_required = require_roles("agent_admin", "security_admin", "platform_admin")
logger = logging.getLogger(__name__)


def _version_out(db: Session, row: models.PluginVersion) -> dict:
    plugin = db.get(models.Plugin, row.plugin_id)
    return {
        "id": row.id, "plugin_id": row.plugin_id, "name": plugin.name if plugin else row.plugin_id,
        "plugin_type": plugin.plugin_type if plugin else "mcp", "scope": plugin.scope if plugin else "shared",
        "mcp_category": plugin.mcp_category if plugin else None, "version": row.version,
        "deployment_mode": row.deployment_mode, "data_level": row.data_level,
        "review_status": row.review_status, "publish_status": row.publish_status,
        "submitted_by": row.submitted_by, "reviewed_by": row.reviewed_by,
        "review_note": row.review_note, "created_at": row.created_at,
        "owner_human_no": plugin.owner_human_no if plugin else None,
    }


def _safe_tools(manifest: dict | None) -> list[dict[str, str]]:
    """Return user-facing tool metadata without schemas, endpoints, or credentials."""
    result: list[dict[str, str]] = []
    for tool in (manifest or {}).get("tools", []):
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            continue
        result.append({
            "name": tool["name"][:120],
            "description": str(tool.get("description") or "暂未提供工具说明")[:500],
        })
    return result


def _safe_detail(
    db: Session,
    plugin: models.Plugin,
    *,
    target_agent_id: str | None,
    include_governance: bool = False,
) -> dict:
    versions = db.scalars(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin.id,
    ).order_by(models.PluginVersion.id.desc())).all()
    current = next((row for row in versions if row.version == plugin.current_version), None)
    # Owners need to inspect a submitted version before it becomes current; this
    # still exposes only the deliberately reduced, credential-free DTO below.
    display_version = current or (versions[0] if versions else None)
    bindings = db.scalars(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == plugin.id,
    ).order_by(models.AgentPluginBinding.priority, models.AgentPluginBinding.id)).all()
    binding = next((row for row in bindings if row.target_agent_id == target_agent_id), None)
    runtime = None
    if display_version:
        runtime_row = db.scalar(select(models.McpRuntimeInstance).where(
            models.McpRuntimeInstance.plugin_version_id == display_version.id,
        ).order_by(models.McpRuntimeInstance.updated_at.desc()))
        if runtime_row:
            runtime = {
                "state": runtime_row.state,
                "health": runtime_row.health,
                "updated_at": runtime_row.updated_at,
            }
    effective = bool(
        binding
        and binding.admin_enabled
        and binding.employee_enabled
        and binding.decision_mode == "allow"
        and plugin.status == "active"
        and current
        and current.publish_status == "published"
    )
    # 来源判定与员工货架同源：SkillHub 导入的技能包在 manifest 里带 slug。
    skillhub_imported = any(
        row.publish_status == "published" and str((row.manifest or {}).get("slug") or "").strip()
        for row in versions
    )
    detail = {
        "plugin_id": plugin.id,
        "name": plugin.name,
        "description": plugin.description or "暂未提供插件说明",
        "plugin_type": plugin.plugin_type,
        "scope": plugin.scope,
        "mcp_category": plugin.mcp_category,
        "skill_category": plugin.skill_category,
        "featured": bool(plugin.featured),
        "source": (
            "personal" if plugin.scope == "personal"
            else "skillhub" if plugin.plugin_type == "skill" and skillhub_imported
            else "platform"
        ),
        "current_version": plugin.current_version,
        "version": ({
            "version": display_version.version,
            "data_level": display_version.data_level,
            "deployment_mode": display_version.deployment_mode,
            "review_status": display_version.review_status,
            "publish_status": display_version.publish_status,
            "published_at": display_version.published_at,
            # 提交者需要看到驳回原因才能修改后重提
            "review_note": display_version.review_note,
            "submitted_by": display_version.submitted_by,
        } if display_version else None),
        "tools": _safe_tools(display_version.manifest if display_version else None),
        "instruction_summary": (
            str((display_version.manifest or {}).get("instruction_summary") or plugin.description or "")[:500]
            if plugin.plugin_type == "skill" and display_version else None
        ),
        "authorization": {
            "admin_enabled": bool(binding and binding.admin_enabled),
            "employee_enabled": bool(binding and binding.employee_enabled),
            "decision_mode": binding.decision_mode if binding else "deny",
            "effective": effective,
        },
        "runtime": runtime,
    }
    if include_governance:
        detail.update({
            "versions": [{
                "version": row.version,
                "data_level": row.data_level,
                "deployment_mode": row.deployment_mode,
                "review_status": row.review_status,
                "publish_status": row.publish_status,
                "submitted_by": row.submitted_by,
                "reviewed_by": row.reviewed_by,
                "reviewed_at": row.reviewed_at,
                "published_at": row.published_at,
                "created_at": row.created_at,
            } for row in versions],
            "bindings": [{
                "id": row.id,
                "target_agent_id": row.target_agent_id,
                "admin_enabled": row.admin_enabled,
                "employee_enabled": row.employee_enabled,
                "decision_mode": row.decision_mode,
                "priority": row.priority,
            } for row in bindings],
        })
    return detail


@my_router.get("")
def my_plugins(
    plugin_type: str | None = Query(default=None, description="按类型拆市场：skill / mcp"),
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    if "user" not in (account.roles or []):
        raise HTTPException(status_code=403, detail="管理员账号不拥有个人插件目录")
    twin_id = f"DT-{account.human_employee_no}"
    # 六态只算一次：市场拆分成「技能市场 / MCP 市场」是展示层的过滤，不是第二套授权事实。
    market = market_plugins(db, twin_id, account.human_employee_no)
    counts: dict[str, int] = {}
    for row in market:
        key = row.get("plugin_type") or "other"
        counts[key] = counts.get(key, 0) + 1
    wanted = (plugin_type or "").strip()
    return {
        "effective": effective_plugins(db, twin_id, account.human_employee_no),
        # 「可添加」= 管理员已授权但尚未启用，与 effective 严格互斥。
        "available": available_plugins(db, twin_id, account.human_employee_no),
        # 「市场」= 全部共享插件 + 本人个人插件，按对该分身的当前状态呈现六态。
        "market": [row for row in market if row.get("plugin_type") == wanted] if wanted else market,
        "market_counts": counts,
    }


@my_router.post("/submissions", status_code=201)
async def submit_plugin(
    file: UploadFile = File(...), name: str = Form(...), plugin_type: str = Form(...),
    scope: str = Form("personal"), mcp_category: str | None = Form(None),
    deployment_mode: str = Form("instruction"), data_level: str = Form("L1"),
    version: str = Form("1.0.0"), target_agent_id: str | None = Form(None),
    plugin_id: str | None = Form(None),
    account: models.Account = Depends(current_account), db: Session = Depends(get_db),
):
    if "user" not in (account.roles or []):
        raise HTTPException(status_code=403, detail="管理员不能以员工身份提交个人插件")
    own_twin = f"DT-{account.human_employee_no}"
    if target_agent_id and target_agent_id != own_twin:
        raise HTTPException(status_code=403, detail="员工只能绑定自己的数字分身")
    row = submit_zip(
        db, data=await file.read(), filename=file.filename or "plugin.zip", name=name,
        plugin_type=plugin_type, scope=scope, category=mcp_category,
        deployment_mode=deployment_mode, data_level=data_level, version=version,
        submitter=account.human_employee_no, target_agent_id=target_agent_id or own_twin,
        plugin_id=plugin_id,
    )
    return _version_out(db, row)


@my_router.post("/submissions/{version_id}/resubmit")
async def resubmit_plugin(
    version_id: int,
    file: UploadFile = File(...),
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    """被驳回的提交重新上传：复用同一版本行与版本号。"""
    if "user" not in (account.roles or []):
        raise HTTPException(status_code=403, detail="管理员账号不拥有个人插件目录")
    row = db.get(models.PluginVersion, version_id)
    if not row or row.submitted_by != account.human_employee_no:
        raise HTTPException(status_code=404, detail="提交不存在")
    row = resubmit_version(
        db, row, data=await file.read(), filename=file.filename or "plugin.zip",
    )
    return _version_out(db, row)


@my_router.get("/submissions")
def my_submissions(account: models.Account = Depends(current_account), db: Session = Depends(get_db)):
    return [_version_out(db, row) for row in db.scalars(select(models.PluginVersion).where(
        models.PluginVersion.submitted_by == account.human_employee_no,
    ).order_by(models.PluginVersion.created_at.desc())).all()]


@my_router.get("/{plugin_id}")
def my_plugin_detail(
    plugin_id: str,
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    if "user" not in (account.roles or []):
        raise HTTPException(status_code=403, detail="管理员账号不拥有个人插件目录")
    plugin = db.get(models.Plugin, plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail="插件不存在或当前账号不可见")
    # 提交者必须能看到自己提交的插件（含待审/被驳回），否则拿不到驳回原因、无法重提。
    submitted_by_me = db.scalar(select(models.PluginVersion.id).where(
        models.PluginVersion.plugin_id == plugin.id,
        models.PluginVersion.submitted_by == account.human_employee_no,
    ).limit(1)) is not None
    visible = (
        (plugin.scope == "shared" and plugin.status == "active")
        or plugin.owner_human_no == account.human_employee_no
        or submitted_by_me
    )
    if not visible:
        raise HTTPException(status_code=404, detail="插件不存在或当前账号不可见")
    return _safe_detail(db, plugin, target_agent_id=f"DT-{account.human_employee_no}")


class PluginRequestIn(BaseModel):
    """插件使用申请：理由可选，最长 500 字（与 ``AccessRequestCreate`` 口径一致）。"""

    reason: str = Field(default="", max_length=500)


@my_router.post("/{plugin_id}/request", response_model=schemas.AccessRequestOut, status_code=201)
def request_plugin(
    plugin_id: str,
    payload: PluginRequestIn,
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    """员工自助申请插件使用。

    申请人从登录态取（``DT-<自己工号>``），不接受客户端指定，避免替他人申请；
    审批通过后由 ``services/access_request.grant_access`` 自动建立授权。
    """
    if "user" not in (account.roles or []):
        raise HTTPException(status_code=403, detail="管理员账号不拥有个人插件目录")
    plugin = resolve_plugin_for_request(db, plugin_id, requester_human_no=account.human_employee_no)
    applicant_no = plugin_request_subject(db, account.human_employee_no, plugin)
    return create_plugin_request(db, applicant_no=applicant_no, plugin=plugin, reason=payload.reason)


class EnableIn(BaseModel):
    agent_id: str | None = None


def _set_enabled(plugin_id: str, enabled: bool, payload: EnableIn, account: models.Account, db: Session):
    target = payload.agent_id or f"DT-{account.human_employee_no}"
    if target != f"DT-{account.human_employee_no}":
        raise HTTPException(status_code=403, detail="只能管理自己的数字分身")
    if db.get(models.Plugin, plugin_id) is None:
        raise HTTPException(status_code=404, detail="插件不存在")
    binding = db.scalar(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == plugin_id,
        models.AgentPluginBinding.target_agent_id == target,
    ))
    if not binding or not binding.admin_enabled:
        raise HTTPException(status_code=403, detail="管理员尚未授权此插件")
    binding.employee_enabled = enabled
    db.commit()
    # 技能包的投放判据是「已授权 ∧ 已启用」：启用/停用都会改变生效集合，
    # 因此这里必须同步投放/回收，否则容器里会缺包（或残留已停用的包）。
    if db.get(models.Plugin, plugin_id).plugin_type == "skill":
        try:
            from ..services import runtime_manager

            runtime_manager.deploy_skills(db, target)
        except Exception as exc:  # noqa: BLE001 - 投放是尽力而为，不阻断启用
            logger.warning("技能包投放触发失败 plugin=%s target=%s: %s", plugin_id, target, exc)
    return {"ok": True, "enabled": enabled}


@my_router.post("/{plugin_id}/enable")
def enable(plugin_id: str, payload: EnableIn, account=Depends(current_account), db=Depends(get_db)):
    return _set_enabled(plugin_id, True, payload, account, db)


@my_router.post("/{plugin_id}/disable")
def disable(plugin_id: str, payload: EnableIn, account=Depends(current_account), db=Depends(get_db)):
    return _set_enabled(plugin_id, False, payload, account, db)


@admin_router.get("/plugins")
def admin_plugins(_: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    # 来源判定与员工货架同源：SkillHub 导入的技能包在 plugin.yaml 里带 slug，
    # 落库后存进已发布版本的 manifest['slug']。
    slugged = {
        row.plugin_id
        for row in db.scalars(select(models.PluginVersion).where(
            models.PluginVersion.publish_status == "published",
        )).all()
        if str((row.manifest or {}).get("slug") or "").strip()
    }
    result: list[dict] = []
    for row in db.scalars(select(models.Plugin).order_by(models.Plugin.name)).all():
        if row.scope == "personal":
            source = "personal"
        elif row.plugin_type == "skill" and row.id in slugged:
            source = "skillhub"
        else:
            source = "platform"
        result.append({
            "id": row.id, "name": row.name, "plugin_type": row.plugin_type, "scope": row.scope,
            "mcp_category": row.mcp_category, "skill_category": row.skill_category,
            "data_level": row.data_level, "featured": bool(row.featured),
            "current_version": row.current_version, "status": row.status,
            "description": row.description, "source": source,
            "intro": (row.runtime_meta or {}).get("intro"),
            # 个人插件必须显示属主，否则授权弹窗里无法判断该绑给哪个分身
            "owner_human_no": row.owner_human_no,
        })
    return result


@admin_router.get("/plugins/{plugin_id}")
def admin_plugin_detail(
    plugin_id: str,
    _: models.Account = Depends(admin_required),
    db: Session = Depends(get_db),
):
    plugin = db.get(models.Plugin, plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    return _safe_detail(db, plugin, target_agent_id=None, include_governance=True)


@admin_router.get("/plugin-submissions")
def submissions(_: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    return [_version_out(db, row) for row in db.scalars(select(models.PluginVersion).order_by(
        models.PluginVersion.created_at.desc())).all()]


class ReviewIn(BaseModel):
    note: str = ""


def _review(version_id: int, decision: str, payload: ReviewIn, account: models.Account, db: Session):
    row = db.get(models.PluginVersion, version_id)
    if not row:
        raise HTTPException(status_code=404, detail="提交不存在")
    if row.review_status != "pending":
        raise HTTPException(status_code=409, detail="该提交已审核，不能重复审核")
    row.review_status = decision
    row.reviewed_by = account.username
    row.reviewed_at = datetime.now()
    row.review_note = payload.note[:500]
    if decision == "rejected":
        row.publish_status = "rejected"
    db.add(models.PluginReview(plugin_version_id=row.id, decision=decision,
                               reviewer=account.username, note=payload.note[:500]))
    db.commit()
    return _version_out(db, row)


@admin_router.post("/plugin-submissions/{version_id}/approve")
def approve(version_id: int, payload: ReviewIn, account=Depends(admin_required), db=Depends(get_db)):
    return _review(version_id, "approved", payload, account, db)


@admin_router.post("/plugin-submissions/{version_id}/reject")
def reject(version_id: int, payload: ReviewIn, account=Depends(admin_required), db=Depends(get_db)):
    return _review(version_id, "rejected", payload, account, db)


@admin_router.post("/plugins/{plugin_id}/versions/{version}/publish")
def publish(plugin_id: str, version: str, account=Depends(admin_required), db=Depends(get_db)):
    row = db.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin_id, models.PluginVersion.version == version))
    if not row:
        raise HTTPException(status_code=404, detail="插件版本不存在")
    publish_version(db, row, account.username)
    return _version_out(db, row)


@admin_router.post("/plugins/{plugin_id}/versions/{version}/rollback")
def rollback(plugin_id: str, version: str, account=Depends(admin_required), db=Depends(get_db)):
    row = db.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin_id, models.PluginVersion.version == version))
    if not row:
        raise HTTPException(status_code=404, detail="插件版本不存在")
    if row.review_status != "approved":
        raise HTTPException(status_code=409, detail="该版本未审核通过，不能回滚")
    row.review_status = "approved"
    publish_version(db, row, account.username)
    return _version_out(db, row)


@admin_router.post("/plugins/{plugin_id}/withdraw")
def withdraw(plugin_id: str, _: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    """下架插件：停用全部绑定并置为 withdrawn，员工端/提示词/执行判定同时失效。"""
    plugin = db.get(models.Plugin, plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    withdraw_plugin(db, plugin)
    return _safe_detail(db, plugin, target_agent_id=None, include_governance=True)


class PluginGovernanceIn(BaseModel):
    """技能治理台的行内编辑入参；只提交需要改的字段。"""

    skill_category: str | None = Field(default=None, max_length=40)
    data_level: str | None = None
    featured: bool | None = None
    status: str | None = None  # active（恢复上架）/ withdrawn（下架）


@admin_router.put("/plugins/{plugin_id}/governance")
def update_plugin_governance(
    plugin_id: str,
    payload: PluginGovernanceIn,
    _: models.Account = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """技能治理台：改业务分类 / 数据等级 / 推荐位，以及下架与恢复上架。

    数据等级写的是 ``Plugin.data_level``（授权判定读的就是它，见 ``gateway``）；
    恢复上架不会自动打开下架时停用的绑定，授权要另行配置。
    """
    plugin = db.get(models.Plugin, plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")

    if payload.skill_category is not None:
        if plugin.plugin_type != "skill":
            raise HTTPException(status_code=422, detail="只有技能支持业务分类")
        plugin.skill_category = (payload.skill_category or "").strip()[:40] or None
    if payload.data_level is not None:
        level = (payload.data_level or "").strip().upper()
        if level not in {"L1", "L2", "L3"}:
            raise HTTPException(status_code=422, detail="数据等级必须是 L1/L2/L3")
        plugin.data_level = level
    if payload.featured is not None:
        plugin.featured = bool(payload.featured)
    if payload.status is not None:
        target = (payload.status or "").strip()
        if target == "withdrawn":
            withdraw_plugin(db, plugin)
        elif target == "active":
            restore_plugin(db, plugin)
        else:
            raise HTTPException(status_code=422, detail="状态只支持 active / withdrawn")
    db.commit()
    db.refresh(plugin)
    return {
        "plugin_id": plugin.id,
        "status": plugin.status,
        "skill_category": plugin.skill_category,
        "data_level": plugin.data_level,
        "featured": bool(plugin.featured),
    }


class PluginIntroIn(BaseModel):
    """重新生成简介：可选自定义指令（与 MCP 自助接入的口径一致）。"""

    prompt: str = Field(default="", max_length=1000)


@admin_router.post("/plugins/{plugin_id}/intro")
def regenerate_plugin_intro(
    plugin_id: str,
    payload: PluginIntroIn,
    _: models.Account = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """为技能重新生成中文简介与业务分类（失败降级为原文首句，不报错）。"""
    plugin = db.get(models.Plugin, plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    if plugin.plugin_type != "skill":
        raise HTTPException(status_code=422, detail="只有技能支持 AI 补简介")

    version = db.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin.id,
        models.PluginVersion.publish_status == "published",
    ).order_by(models.PluginVersion.id.desc()))
    manifest = (version.manifest if version else {}) or {}
    slug = str(manifest.get("slug") or "").strip()
    summary = str(manifest.get("description") or plugin.description or "")
    department = ""
    tags: tuple[str, ...] = ()
    if slug:
        # 远端详情带 tags/department，能提高分类命中率；不可达时用本地版本信息即可。
        try:
            remote = skillhub_client.get_skill(slug)
            summary = remote.summary or summary
            department = remote.department
            tags = remote.tags
        except skillhub_client.SkillHubError as exc:
            logger.info("技能治理重生成简介：远端详情不可用（%s），改用本地版本信息", exc)
    draft = skill_intro.generate_draft(
        name=plugin.name, slug=slug, summary=summary, department=department, tags=tags,
        instruction=str(manifest.get("instruction") or ""), prompt=payload.prompt or None,
    )
    skill_intro.apply_intro(plugin, draft)
    db.commit()
    return {
        "plugin_id": plugin.id,
        "description": plugin.description,
        "skill_category": plugin.skill_category,
        "generated": bool(draft.get("generated")),
        "degraded_reason": draft.get("degraded_reason"),
    }


class AccessRequestDecideIn(BaseModel):
    approve: bool = True
    note: str = Field(default="", max_length=200)


@admin_router.post("/access-requests/{request_id}/approve", response_model=schemas.AccessRequestOut)
def admin_decide_access_request(
    request_id: int,
    payload: AccessRequestDecideIn,
    account: models.Account = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """管理端审批通道（管理员账号没有分身，走不了老接口的「正式员工分身」校验）。

    通过时按资源类型写授权：插件类 upsert ``AgentPluginBinding``（员工端刷新即生效）；
    知识库类写 ``AgentKnowledgeGrant``（真实可读授权），L3 额外补只读白名单；
    独占冲突返回 409 ``knowledge_grant_conflict`` 并给出占用方。拒绝只落终态。
    """
    request = db.get(models.AccessRequest, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail={"message": "申请单不存在", "request_id": request_id})
    return decide_access_request(
        db, request, approve=payload.approve, decided_by=account.username, note=payload.note,
    )


class BindingIn(BaseModel):
    plugin_id: str
    target_agent_id: str
    pinned_version: str | None = None
    admin_enabled: bool = True
    decision_mode: str = "allow"
    priority: int = 100


class BindingPatchIn(BaseModel):
    """局部更新：只改显式提交的字段。

    必须用 ``exclude_unset=True`` 消费本模型，否则未提交的字段会被默认值覆盖
    （界面只切换「管理员授权」开关就会静默重置 decision_mode/priority/pinned_version）。
    ``pinned_version`` 显式传 null 表示清除版本锁定。
    """

    plugin_id: str | None = None
    target_agent_id: str | None = None
    pinned_version: str | None = None
    admin_enabled: bool | None = None
    decision_mode: str | None = None
    priority: int | None = None


DECISION_MODES = ("allow", "approval", "deny")


def _validate_pinned_version(db: Session, plugin_id: str, pinned_version: str | None) -> None:
    """版本锁定必须命中该插件的已发布版本。

    此前任意字符串（如 ``9.9.9``）都能写入，绑定随即因为找不到版本而静默失效。
    """
    if not pinned_version:
        return
    exists = db.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin_id,
        models.PluginVersion.version == pinned_version,
        models.PluginVersion.publish_status == "published",
    ))
    if exists is None:
        raise HTTPException(
            status_code=422,
            detail=f"版本 {pinned_version} 不是该插件的已发布版本，不能作为版本锁定",
        )


def _validate_personal_binding(db: Session, plugin_id: str, target_agent_id: str) -> None:
    """个人插件只能授权给属主本人的数字分身。"""
    plugin = db.get(models.Plugin, plugin_id)
    if plugin is None or plugin.scope != "personal" or not plugin.owner_human_no:
        return
    if target_agent_id != f"DT-{plugin.owner_human_no}":
        raise HTTPException(status_code=422, detail="个人插件只能授权给属主本人的数字分身")


def _binding_out(row: models.AgentPluginBinding) -> dict:
    return {
        "id": row.id, "plugin_id": row.plugin_id, "target_agent_id": row.target_agent_id,
        "pinned_version": row.pinned_version, "admin_enabled": row.admin_enabled,
        "employee_enabled": row.employee_enabled, "decision_mode": row.decision_mode,
        "priority": row.priority, "authorized_by": row.authorized_by,
    }


@admin_router.get("/agent-plugin-bindings")
def list_bindings(_: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    return [_binding_out(row) for row in db.scalars(select(models.AgentPluginBinding).order_by(
        models.AgentPluginBinding.target_agent_id, models.AgentPluginBinding.priority)).all()]


def _deploy_skills_after_binding(db: Session, *targets: str) -> None:
    """管理员改绑后立刻投放技能。

    少了这一步，绑给「容器由 compose 预建」的 AI 员工（岗位员工）的技能永远投不进去：
    员工侧 enable 只允许绑自己的分身，岗位员工没有那个入口，`ensure_container` 也不补投。
    投放本身是幂等且尽力而为的，失败只记日志。
    """
    from ..services import employee_registry, runtime_manager

    for target in {t for t in targets if t}:
        if not employee_registry.is_harness_employee(target):
            continue
        try:
            runtime_manager.deploy_skills(db, target)
        except Exception as exc:  # noqa: BLE001 - 投放失败不阻断绑定
            logger.warning("技能包投放触发失败 target=%s: %s", target, exc)


@admin_router.post("/agent-plugin-bindings", status_code=201)
def create_binding(payload: BindingIn, account=Depends(admin_required), db=Depends(get_db)):
    if not db.get(models.Plugin, payload.plugin_id) or not db.get(models.DigitalEmployee, payload.target_agent_id):
        raise HTTPException(status_code=404, detail="插件或AI员工不存在")
    if payload.decision_mode not in DECISION_MODES:
        raise HTTPException(status_code=422, detail="decision_mode 必须为 allow/approval/deny")
    _validate_pinned_version(db, payload.plugin_id, payload.pinned_version)
    _validate_personal_binding(db, payload.plugin_id, payload.target_agent_id)
    existing = db.scalar(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == payload.plugin_id,
        models.AgentPluginBinding.target_agent_id == payload.target_agent_id))
    if existing:
        raise HTTPException(status_code=409, detail="绑定已存在")
    row = models.AgentPluginBinding(**payload.model_dump(), authorized_by=account.username,
                                    employee_enabled=True)
    db.add(row); db.commit(); db.refresh(row)
    _deploy_skills_after_binding(db, row.target_agent_id)
    return _binding_out(row)


@admin_router.put("/agent-plugin-bindings/{binding_id}")
def update_binding(binding_id: int, payload: BindingPatchIn, account=Depends(admin_required), db=Depends(get_db)):
    row = db.get(models.AgentPluginBinding, binding_id)
    if not row:
        raise HTTPException(status_code=404, detail="绑定不存在")
    changes = payload.model_dump(exclude_unset=True)
    if "plugin_id" in changes and not db.get(models.Plugin, changes["plugin_id"]):
        raise HTTPException(status_code=404, detail="插件不存在")
    if "target_agent_id" in changes and not db.get(models.DigitalEmployee, changes["target_agent_id"]):
        raise HTTPException(status_code=404, detail="AI员工不存在")
    if "decision_mode" in changes and changes["decision_mode"] not in DECISION_MODES:
        raise HTTPException(status_code=422, detail="decision_mode 必须为 allow/approval/deny")
    next_plugin = changes.get("plugin_id", row.plugin_id)
    next_target = changes.get("target_agent_id", row.target_agent_id)
    if next_plugin != row.plugin_id or next_target != row.target_agent_id:
        duplicate = db.scalar(select(models.AgentPluginBinding).where(
            models.AgentPluginBinding.plugin_id == next_plugin,
            models.AgentPluginBinding.target_agent_id == next_target,
            models.AgentPluginBinding.id != binding_id))
        if duplicate:
            raise HTTPException(status_code=409, detail="该AI员工已绑定此插件")
        _validate_personal_binding(db, next_plugin, next_target)
    if "pinned_version" in changes:
        _validate_pinned_version(db, next_plugin, changes["pinned_version"])
    previous_target = row.target_agent_id
    for key, value in changes.items():
        setattr(row, key, value)
    row.authorized_by = account.username
    db.commit(); db.refresh(row)
    # 改绑到别的员工时两边都要重投：新员工要拿到包，旧员工要回收掉
    _deploy_skills_after_binding(db, previous_target, row.target_agent_id)
    return _binding_out(row)


@admin_router.delete("/agent-plugin-bindings/{binding_id}", status_code=204)
def delete_binding(binding_id: int, _: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    row = db.get(models.AgentPluginBinding, binding_id)
    if not row: raise HTTPException(status_code=404, detail="绑定不存在")
    db.delete(row); db.commit()
