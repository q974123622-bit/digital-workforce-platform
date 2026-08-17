from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/plugins", tags=["plugins"])


def _next_id(db: Session) -> str:
    rows = db.scalars(select(models.Plugin.id).where(models.Plugin.id.like("PLG-%"))).all()
    max_n = max((int(n.split("-")[1], 16) for n in rows if "-" in n), default=0)
    return f"PLG-{max_n + 1:08x}"


@router.get("", response_model=list[schemas.PluginOut])
def list_plugins(db: Session = Depends(get_db)):
    return db.scalars(select(models.Plugin).order_by(models.Plugin.id)).all()


@router.post("", response_model=schemas.PluginOut, status_code=201)
def create_plugin(payload: schemas.PluginCreate, db: Session = Depends(get_db)):
    plugin_id = payload.id or _next_id(db)
    plugin = models.Plugin(id=plugin_id, **payload.model_dump(exclude={"id"}))
    db.add(plugin)
    db.commit()
    db.refresh(plugin)
    return plugin


@router.get("/{plugin_id}", response_model=schemas.PluginOut)
def get_plugin(plugin_id: str, db: Session = Depends(get_db)):
    plugin = db.get(models.Plugin, plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    return plugin


@router.put("/{plugin_id}", response_model=schemas.PluginOut)
def update_plugin(plugin_id: str, payload: schemas.PluginUpdate, db: Session = Depends(get_db)):
    plugin = db.get(models.Plugin, plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(plugin, field, value)
    db.commit()
    db.refresh(plugin)
    return plugin


@router.delete("/{plugin_id}", status_code=204)
def delete_plugin(plugin_id: str, db: Session = Depends(get_db)):
    plugin = db.get(models.Plugin, plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    db.delete(plugin)
    db.commit()
