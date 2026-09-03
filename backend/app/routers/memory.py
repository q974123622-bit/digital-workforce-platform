from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services.auth import current_account, require_roles
from ..services.memory_adapter import get_memory_adapter, namespace

my_router = APIRouter(prefix="/my/agents", tags=["my-memory"])
admin_router = APIRouter(prefix="/admin/memory", tags=["memory-admin"])


def _own_agent(account: models.Account, agent_id: str):
    if "user" not in (account.roles or []): raise HTTPException(status_code=403, detail="管理员没有个人记忆")
    if agent_id.startswith("DT-") and agent_id != f"DT-{account.human_employee_no}":
        raise HTTPException(status_code=403, detail="不能访问其他员工的数字分身记忆")


def _out(row):
    return {"id": row.id, "agent_id": row.agent_id, "memory_type": row.memory_type,
            "content": row.content, "source": row.source, "retained": row.retained,
            "expires_at": row.expires_at, "sync_status": row.sync_status, "created_at": row.created_at}


@my_router.get("/{agent_id}/memories")
def list_memories(agent_id: str, account=Depends(current_account), db: Session = Depends(get_db)):
    _own_agent(account, agent_id)
    rows = get_memory_adapter().list(db, namespace(account.human_employee_no, agent_id))
    return [_out(row) for row in rows]


class MemoryUpdate(BaseModel): content: str


def _get_owned(db, account, agent_id, memory_id):
    _own_agent(account, agent_id)
    row = db.get(models.MemoryRecord, memory_id)
    if not row or row.requester_human_no != account.human_employee_no or row.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return row


@my_router.put("/{agent_id}/memories/{memory_id}")
def update_memory(agent_id: str, memory_id: str, payload: MemoryUpdate, account=Depends(current_account), db=Depends(get_db)):
    row = _get_owned(db, account, agent_id, memory_id)
    return _out(get_memory_adapter().update(db, row, payload.content))


@my_router.delete("/{agent_id}/memories/{memory_id}", status_code=204)
def delete_memory(agent_id: str, memory_id: str, account=Depends(current_account), db=Depends(get_db)):
    row = _get_owned(db, account, agent_id, memory_id); get_memory_adapter().delete(db, row)


@my_router.delete("/{agent_id}/memories", status_code=204)
def clear_memories(agent_id: str, account=Depends(current_account), db=Depends(get_db)):
    _own_agent(account, agent_id); db.query(models.MemoryRecord).filter(
        models.MemoryRecord.requester_human_no == account.human_employee_no,
        models.MemoryRecord.agent_id == agent_id).delete(); db.commit()


@my_router.post("/{agent_id}/memories/{memory_id}/retain")
def retain(agent_id: str, memory_id: str, account=Depends(current_account), db=Depends(get_db)):
    row = _get_owned(db, account, agent_id, memory_id); row.retained = True; row.expires_at = None; db.commit(); return _out(row)


admin_required = require_roles("platform_admin", "security_admin")


@admin_router.get("/health")
def health(_: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    data = get_memory_adapter().health()
    data.update({
        "memories": db.scalar(select(func.count(models.MemoryRecord.id))) or 0,
        "pending_jobs": db.scalar(select(func.count(models.MemorySyncJob.id)).where(models.MemorySyncJob.status == "pending")) or 0,
        "failed_jobs": db.scalar(select(func.count(models.MemorySyncJob.id)).where(models.MemorySyncJob.status == "failed")) or 0,
    }); return data


@admin_router.get("/jobs")
def jobs(_: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    return [{"id": row.id, "operation": row.operation, "status": row.status, "attempts": row.attempts,
             "error_summary": row.error_summary, "created_at": row.created_at}
            for row in db.scalars(select(models.MemorySyncJob).order_by(models.MemorySyncJob.created_at.desc())).all()]


@admin_router.post("/jobs/{job_id}/retry")
def retry(job_id: str, _: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    row = db.get(models.MemorySyncJob, job_id)
    if not row: raise HTTPException(status_code=404, detail="同步任务不存在")
    if row.attempts >= 5: raise HTTPException(status_code=409, detail="已达到最大重试次数")
    row.status = "pending"; row.attempts += 1; row.updated_at = datetime.now(); db.commit()
    return {"id": row.id, "status": row.status, "attempts": row.attempts}
