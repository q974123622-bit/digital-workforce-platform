from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services.auth import current_account, require_roles
from ..services.plugin_governance import effective_plugins, publish_version, submit_zip

my_router = APIRouter(prefix="/my/plugins", tags=["my-plugins"])
admin_router = APIRouter(prefix="/admin", tags=["plugin-governance"])
admin_required = require_roles("agent_admin", "security_admin", "platform_admin")


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
    }


@my_router.get("")
def my_plugins(account: models.Account = Depends(current_account), db: Session = Depends(get_db)):
    if "user" not in (account.roles or []):
        raise HTTPException(status_code=403, detail="管理员账号不拥有个人插件目录")
    twin_id = f"DT-{account.human_employee_no}"
    return {
        "effective": effective_plugins(db, twin_id, account.human_employee_no),
        "available": [
            {"id": row.id, "name": row.name, "plugin_type": row.plugin_type, "scope": row.scope,
             "mcp_category": row.mcp_category, "current_version": row.current_version, "status": row.status}
            for row in db.scalars(select(models.Plugin).where(
                models.Plugin.status == "active",
                (models.Plugin.scope == "shared") | (models.Plugin.owner_human_no == account.human_employee_no),
            ).order_by(models.Plugin.name)).all()
        ],
    }


@my_router.post("/submissions", status_code=201)
async def submit_plugin(
    file: UploadFile = File(...), name: str = Form(...), plugin_type: str = Form(...),
    scope: str = Form("personal"), mcp_category: str | None = Form(None),
    deployment_mode: str = Form("instruction"), data_level: str = Form("L1"),
    version: str = Form("1.0.0"), target_agent_id: str | None = Form(None),
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
    )
    return _version_out(db, row)


@my_router.get("/submissions")
def my_submissions(account: models.Account = Depends(current_account), db: Session = Depends(get_db)):
    return [_version_out(db, row) for row in db.scalars(select(models.PluginVersion).where(
        models.PluginVersion.submitted_by == account.human_employee_no,
    ).order_by(models.PluginVersion.created_at.desc())).all()]


class EnableIn(BaseModel):
    agent_id: str | None = None


def _set_enabled(plugin_id: str, enabled: bool, payload: EnableIn, account: models.Account, db: Session):
    target = payload.agent_id or f"DT-{account.human_employee_no}"
    if target != f"DT-{account.human_employee_no}":
        raise HTTPException(status_code=403, detail="只能管理自己的数字分身")
    binding = db.scalar(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == plugin_id,
        models.AgentPluginBinding.target_agent_id == target,
    ))
    if not binding or not binding.admin_enabled:
        raise HTTPException(status_code=403, detail="管理员尚未授权此插件")
    binding.employee_enabled = enabled
    db.commit()
    return {"ok": True, "enabled": enabled}


@my_router.post("/{plugin_id}/enable")
def enable(plugin_id: str, payload: EnableIn, account=Depends(current_account), db=Depends(get_db)):
    return _set_enabled(plugin_id, True, payload, account, db)


@my_router.post("/{plugin_id}/disable")
def disable(plugin_id: str, payload: EnableIn, account=Depends(current_account), db=Depends(get_db)):
    return _set_enabled(plugin_id, False, payload, account, db)


@admin_router.get("/plugins")
def admin_plugins(_: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    return [{"id": row.id, "name": row.name, "plugin_type": row.plugin_type, "scope": row.scope,
             "mcp_category": row.mcp_category, "current_version": row.current_version, "status": row.status}
            for row in db.scalars(select(models.Plugin).order_by(models.Plugin.name)).all()]


@admin_router.get("/plugin-submissions")
def submissions(_: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    return [_version_out(db, row) for row in db.scalars(select(models.PluginVersion).order_by(
        models.PluginVersion.created_at.desc())).all()]


class ReviewIn(BaseModel):
    note: str = ""


def _review(version_id: int, decision: str, payload: ReviewIn, account: models.Account, db: Session):
    row = db.get(models.PluginVersion, version_id)
    if not row or row.review_status != "pending":
        raise HTTPException(status_code=409, detail="提交不存在或已审核")
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
        models.PluginVersion.plugin_id == plugin_id, models.PluginVersion.version == version,
        models.PluginVersion.publish_status.in_(["published", "superseded"])))
    if not row:
        raise HTTPException(status_code=404, detail="没有可回滚的已发布版本")
    row.review_status = "approved"
    publish_version(db, row, account.username)
    return _version_out(db, row)


class BindingIn(BaseModel):
    plugin_id: str
    target_agent_id: str
    pinned_version: str | None = None
    admin_enabled: bool = True
    decision_mode: str = "allow"
    priority: int = 100


@admin_router.get("/agent-plugin-bindings")
def list_bindings(_: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    return [{
        "id": row.id, "plugin_id": row.plugin_id, "target_agent_id": row.target_agent_id,
        "pinned_version": row.pinned_version, "admin_enabled": row.admin_enabled,
        "employee_enabled": row.employee_enabled, "decision_mode": row.decision_mode,
        "priority": row.priority, "authorized_by": row.authorized_by,
    } for row in db.scalars(select(models.AgentPluginBinding).order_by(
        models.AgentPluginBinding.target_agent_id, models.AgentPluginBinding.priority)).all()]


@admin_router.post("/agent-plugin-bindings", status_code=201)
def create_binding(payload: BindingIn, account=Depends(admin_required), db=Depends(get_db)):
    if not db.get(models.Plugin, payload.plugin_id) or not db.get(models.DigitalEmployee, payload.target_agent_id):
        raise HTTPException(status_code=404, detail="插件或数字员工不存在")
    existing = db.scalar(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == payload.plugin_id,
        models.AgentPluginBinding.target_agent_id == payload.target_agent_id))
    if existing:
        raise HTTPException(status_code=409, detail="绑定已存在")
    row = models.AgentPluginBinding(**payload.model_dump(), authorized_by=account.username,
                                    employee_enabled=True)
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, **payload.model_dump()}


@admin_router.put("/agent-plugin-bindings/{binding_id}")
def update_binding(binding_id: int, payload: BindingIn, account=Depends(admin_required), db=Depends(get_db)):
    row = db.get(models.AgentPluginBinding, binding_id)
    if not row: raise HTTPException(status_code=404, detail="绑定不存在")
    for key, value in payload.model_dump().items(): setattr(row, key, value)
    row.authorized_by = account.username; db.commit()
    return {"id": row.id, **payload.model_dump()}


@admin_router.delete("/agent-plugin-bindings/{binding_id}", status_code=204)
def delete_binding(binding_id: int, _: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    row = db.get(models.AgentPluginBinding, binding_id)
    if not row: raise HTTPException(status_code=404, detail="绑定不存在")
    db.delete(row); db.commit()
