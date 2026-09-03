from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.capability_contract import plugin_contract, skill_contract
from ..services.auth import enforce_actor, optional_account

router = APIRouter(prefix="/plugins", tags=["plugins"])
capabilities_router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@capabilities_router.get("", response_model=list[schemas.CapabilityOut])
def list_capabilities(
    actor_no: str = Query(...),
    include_experimental: bool = Query(False),
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    """MVP 能力目录：知识能力 + 本人的个人工作方法；实验能力默认隐藏。"""
    enforce_actor(account, actor_no)
    if db.get(models.HumanEmployee, actor_no) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    plugin_query = select(models.Plugin).order_by(models.Plugin.id)
    if not include_experimental:
        plugin_query = plugin_query.where(models.Plugin.type == "knowledge")
    plugins = db.scalars(plugin_query).all()
    skills = db.scalars(
        select(models.Skill)
        .where(models.Skill.owner_human_no == actor_no)
        .order_by(models.Skill.created_at)
    ).all()
    return [
        *[schemas.CapabilityOut(**plugin_contract(plugin).to_dict()) for plugin in plugins],
        *[schemas.CapabilityOut(**skill_contract(skill).to_dict()) for skill in skills],
    ]


def _next_id(db: Session) -> str:
    rows = db.scalars(select(models.Plugin.id).where(models.Plugin.id.like("PLG-%"))).all()
    max_n = max((int(n.split("-")[1], 16) for n in rows if "-" in n), default=0)
    return f"PLG-{max_n + 1:08x}"


@router.get("", response_model=list[schemas.PluginOut])
def list_plugins(_: models.Account | None = Depends(optional_account), db: Session = Depends(get_db)):
    # Legacy administration contract: unified personal Skill records are served by /my/plugins.
    return db.scalars(select(models.Plugin).where(
        models.Plugin.id.not_like("SKILL-%")
    ).order_by(models.Plugin.id)).all()


@router.post("", response_model=schemas.PluginOut, status_code=201)
def create_plugin(
    payload: schemas.PluginCreate,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    _require_plugin_admin(account)
    plugin_id = payload.id or _next_id(db)
    plugin = models.Plugin(id=plugin_id, **payload.model_dump(exclude={"id"}))
    db.add(plugin)
    db.commit()
    db.refresh(plugin)
    return plugin


@router.get("/{plugin_id}", response_model=schemas.PluginOut)
def get_plugin(
    plugin_id: str,
    _: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    plugin = db.get(models.Plugin, plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    return plugin


@router.put("/{plugin_id}", response_model=schemas.PluginOut)
def update_plugin(
    plugin_id: str,
    payload: schemas.PluginUpdate,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    _require_plugin_admin(account)
    plugin = db.get(models.Plugin, plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(plugin, field, value)
    db.commit()
    db.refresh(plugin)
    return plugin


@router.delete("/{plugin_id}", status_code=204)
def delete_plugin(
    plugin_id: str,
    account: models.Account | None = Depends(optional_account),
    db: Session = Depends(get_db),
):
    _require_plugin_admin(account)
    plugin = db.get(models.Plugin, plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    grant = db.scalar(
        select(models.EmployeePluginGrant.id).where(
            models.EmployeePluginGrant.plugin_id == plugin_id
        ).limit(1)
    )
    if grant is not None:
        raise HTTPException(status_code=409, detail="插件仍有员工授权，不能删除；可先禁用")
    db.delete(plugin)
    db.commit()


def _require_plugin_admin(account: models.Account | None) -> None:
    # optional_account only returns None when DWP_REQUIRE_AUTH=0 (local automated tests).
    if account is not None and not {"agent_admin", "platform_admin"}.intersection(account.roles or []):
        raise HTTPException(status_code=403, detail="当前账号没有能力管理权限")
