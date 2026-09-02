"""Read/write API for persisted AgentTeams collaboration tasks.

The API deliberately reads the local durable collaboration state.  It never
uses a Matrix message as the query source of truth, and it only permits task
submission after a live Matrix room check has succeeded.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import config
from ..services.agentteams_collaboration import (
    AgentTeamsCollaborationService,
    CollaborationConflict,
    matrix_user_id_for_runtime,
)
from ..services.agentteams_gateway import AgentTeamsGateway


router = APIRouter(prefix="/agentteams-collaboration", tags=["agentteams-collaboration"])

_TERMINAL_STATUSES = {"completed", "failed", "timed_out", "cancelled"}


def _configured(value: str | None) -> bool:
    return bool(value and value.strip())


def _worker_health(db: Session) -> list[dict]:
    """Report stored worker bindings without claiming their runtimes are live."""
    workers = list(
        db.scalars(
            select(models.DigitalEmployee)
            .where(models.DigitalEmployee.runtime_type == "agentteams")
            .order_by(models.DigitalEmployee.employee_no)
        )
    )
    result = []
    for worker in workers:
        runtime_ref = (worker.runtime_ref or "").strip()
        matrix_user_id = matrix_user_id_for_runtime(runtime_ref) or ""
        if not runtime_ref:
            status = "missing_runtime_ref"
        elif not matrix_user_id:
            status = "invalid_matrix_user_id"
        else:
            status = "configured"
        result.append({
            "agent_id": worker.employee_no,
            "matrix_user_id": matrix_user_id,
            # A database binding is not proof that its remote AgentTeams worker
            # is connected; keep that distinction visible to API consumers.
            "status": status,
        })
    return result


def _normalise_health(raw_health: dict) -> dict:
    """Derive safe readiness facts rather than trusting a caller-provided flag.

    ``inspect_environment`` normally returns these facts itself, but both the
    public health endpoint and submission gate pass through this function.  It
    therefore remains impossible for a test double or future caller to bypass
    the submission gate by merely setting ``ready_for_demo`` to ``True``.
    """
    raw_workers = raw_health.get("workers")
    workers = []
    if isinstance(raw_workers, list):
        for raw_worker in raw_workers:
            if not isinstance(raw_worker, dict):
                continue
            agent_id = str(raw_worker.get("agent_id") or "").strip()
            matrix_user_id = str(raw_worker.get("matrix_user_id") or "").strip()
            raw_status = str(raw_worker.get("status") or "").strip()
            is_exact_mxid = (
                matrix_user_id.startswith("@")
                and matrix_user_id_for_runtime(matrix_user_id) == matrix_user_id
            )
            if raw_status in {"configured", "ready"} and agent_id and is_exact_mxid:
                status = "configured"
            elif not raw_worker.get("matrix_user_id"):
                status = "missing_runtime_ref"
            else:
                status = "invalid_matrix_user_id"
            workers.append({
                "agent_id": agent_id,
                "matrix_user_id": matrix_user_id,
                "status": status,
            })

    enabled = raw_health.get("enabled") is True
    matrix = str(raw_health.get("matrix") or "not_configured")
    room = str(raw_health.get("room") or "not_configured")
    manager = "configured" if raw_health.get("manager") in {"configured", "ready"} else "not_configured"
    platform_team = "ready" if raw_health.get("platform_team") == "ready" else "missing"

    configured_workers = [
        worker for worker in workers
        if worker["status"] == "configured"
    ]
    worker_ids = {worker["agent_id"] for worker in configured_workers}
    worker_mxids = {worker["matrix_user_id"] for worker in configured_workers}
    workers_ready = len(worker_ids) >= 2 and len(worker_mxids) >= 2

    reasons = []
    if not enabled:
        reasons.append("AgentTeams协作功能未启用")
    if matrix != "ready":
        reasons.append("Matrix登录未成功")
    if room != "ready":
        reasons.append("配置的Matrix房间未加入")
    if manager != "configured":
        reasons.append("Manager MXID未配置")
    if platform_team != "ready":
        reasons.append("平台至少需要一个Team")
    if not workers_ready:
        reasons.append("至少需要两个Worker，且均须配置不同的可解析Matrix ID")

    return {
        "enabled": enabled,
        "ready_for_demo": not reasons,
        "reason": "；".join(reasons),
        "matrix": matrix,
        "room": room,
        # Presence is the only Manager fact available here; it is not a
        # remote connection assertion.
        "manager": manager,
        # Health has no user-selected team.  This is intentionally a system
        # check; the submitted team remains validated by create_task.
        "platform_team": platform_team,
        "workers": workers,
    }


def inspect_environment(db: Session) -> dict:
    """Perform a bounded, truthful readiness check for task submission.

    No Matrix request is made while the collaboration loop is disabled.  When
    enabled, ``ready`` for Matrix/room is only returned after the gateway's
    joined-room check succeeds; Manager and worker values are explicitly
    marked as configured rather than as proven remote connections.
    """
    enabled = config.agentteams_collaboration_enabled()
    workers = _worker_health(db)
    platform_team = "ready" if db.scalar(select(models.Team.id).limit(1)) else "missing"
    manager_mxid = config.get(config.AGENTTEAMS_MANAGER_MXID)
    if not enabled:
        return _normalise_health({
            "enabled": False,
            "matrix": "disabled",
            "room": "disabled",
            "manager": "configured" if _configured(manager_mxid) else "not_configured",
            "platform_team": platform_team,
            "workers": workers,
        })

    matrix_url = config.get(config.AGENTTEAMS_MATRIX_URL)
    token = config.get(config.AGENTTEAMS_MATRIX_TOKEN)
    user = config.get(config.AGENTTEAMS_ADMIN_USER)
    password = config.get(config.AGENTTEAMS_ADMIN_PASSWORD)
    room_id = config.get(config.AGENTTEAMS_ROOM_ID)
    matrix_configured = _configured(matrix_url) and (
        _configured(token) or (_configured(user) and _configured(password))
    )

    matrix = "not_configured"
    room = "not_configured" if not _configured(room_id) else "unchecked"
    matrix_room_ready = False
    if matrix_configured and _configured(room_id):
        gateway = AgentTeamsGateway()
        try:
            gateway_health = gateway.health(room_id.strip())
        except Exception:
            matrix = "unavailable"
            room = "unavailable"
        else:
            matrix_room_ready = gateway_health.ok is True
            matrix = "ready" if matrix_room_ready else "unavailable"
            room = "ready" if matrix_room_ready else gateway_health.detail
        finally:
            gateway.close()

    return _normalise_health({
        "enabled": True,
        "matrix": matrix,
        "room": room,
        "manager": "configured" if _configured(manager_mxid) else "not_configured",
        "platform_team": platform_team,
        "workers": workers,
    })


def _service() -> AgentTeamsCollaborationService:
    return AgentTeamsCollaborationService(
        max_depth=config.agentteams_max_depth(),
        max_children=config.agentteams_max_children(),
    )


def _node_out(node: models.AgentTaskNode) -> schemas.AgentTaskNodeOut:
    return schemas.AgentTaskNodeOut.model_validate(node)


def _task_nodes(db: Session, root_task_id: str) -> list[models.AgentTaskNode]:
    return list(
        db.scalars(
            select(models.AgentTaskNode)
            .where(models.AgentTaskNode.root_task_id == root_task_id)
            .order_by(models.AgentTaskNode.depth, models.AgentTaskNode.created_at, models.AgentTaskNode.id)
        )
    )


def _require_root_task(db: Session, task_id: str) -> tuple[models.TaskRun, models.AgentTaskNode]:
    """Reject children, unrelated TaskRuns, and non-AgentTeams roots alike."""
    run = db.get(models.TaskRun, task_id)
    root = db.get(models.AgentTaskNode, task_id)
    if (
        run is None
        or run.source != "agentteams"
        or root is None
        or root.id != task_id
        or root.root_task_id != task_id
        or root.parent_task_id is not None
    ):
        raise HTTPException(status_code=404, detail="协作任务不存在")
    return run, root


def _task_out(
    db: Session, run: models.TaskRun, root: models.AgentTaskNode | None = None
) -> schemas.AgentCollaborationTaskOut:
    root = root or _require_root_task(db, run.id)[1]
    latest_event_id = db.scalar(
        select(func.max(models.AgentTaskEvent.id)).where(models.AgentTaskEvent.root_task_id == run.id)
    ) or 0
    return schemas.AgentCollaborationTaskOut(
        id=run.id,
        team_id=run.team_id,
        trace_id=run.trace_id,
        title=root.title,
        instruction=root.instruction,
        status=root.status,
        source="agentteams",
        summary=run.summary,
        nodes=[_node_out(node) for node in _task_nodes(db, run.id)],
        latest_event_id=latest_event_id,
        created_at=run.created_at,
    )


def _require_ready(db: Session) -> None:
    health = _normalise_health(inspect_environment(db))
    if health.get("ready_for_demo") is not True:
        raise HTTPException(
            status_code=503,
            detail={"message": "AgentTeams 协作环境未就绪", "health": health},
        )


def _commit_or_conflict(db: Session, action) -> models.TaskRun:
    try:
        run = action()
        db.commit()
        return run
    except CollaborationConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/health", response_model=schemas.AgentCollaborationHealthOut)
def health(db: Session = Depends(get_db)):
    return _normalise_health(inspect_environment(db))


@router.post("/tasks", response_model=schemas.AgentCollaborationTaskOut, status_code=201)
def create_task(payload: schemas.AgentCollaborationTaskCreateIn, db: Session = Depends(get_db)):
    _require_ready(db)
    service = _service()
    run = _commit_or_conflict(
        db,
        lambda: service.create_task(
            db,
            team_id=payload.team_id,
            leader_agent_id=payload.leader_agent_id,
            title=payload.title,
            instruction=payload.instruction,
            material=payload.material,
        ),
    )
    return _task_out(db, run)


@router.get("/tasks", response_model=list[schemas.AgentCollaborationTaskOut])
def list_tasks(db: Session = Depends(get_db)):
    runs = list(
        db.scalars(
            select(models.TaskRun)
            .where(models.TaskRun.source == "agentteams")
            .order_by(models.TaskRun.created_at.desc(), models.TaskRun.id.desc())
        )
    )
    return [_task_out(db, run) for run in runs]


@router.get("/tasks/{task_id}", response_model=schemas.AgentCollaborationTaskOut)
def get_task(task_id: str, db: Session = Depends(get_db)):
    run, root = _require_root_task(db, task_id)
    return _task_out(db, run, root)


@router.get("/tasks/{task_id}/graph", response_model=schemas.AgentTaskGraphOut)
def get_graph(task_id: str, db: Session = Depends(get_db)):
    _require_root_task(db, task_id)
    graph = _service().build_graph(db, task_id)
    return schemas.AgentTaskGraphOut(
        nodes=[_node_out(node) for node in _task_nodes(db, task_id)],
        parent_edges=graph["parent_edges"],
        dependency_edges=graph["dependency_edges"],
        communication_edges=graph["communication_edges"],
    )


@router.get("/tasks/{task_id}/events", response_model=list[schemas.AgentTaskEventOut])
def get_events(
    task_id: str,
    after_event_id: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
):
    _require_root_task(db, task_id)
    statement = select(models.AgentTaskEvent).where(models.AgentTaskEvent.root_task_id == task_id)
    if after_event_id is not None:
        statement = statement.where(models.AgentTaskEvent.id > after_event_id)
    return list(db.scalars(statement.order_by(models.AgentTaskEvent.id)))


@router.get("/tasks/{task_id}/messages", response_model=list[schemas.AgentMessageOut])
def get_messages(task_id: str, db: Session = Depends(get_db)):
    _require_root_task(db, task_id)
    return list(
        db.scalars(
            select(models.AgentMessage)
            .where(models.AgentMessage.root_task_id == task_id)
            .order_by(models.AgentMessage.created_at, models.AgentMessage.id)
        )
    )


@router.get("/tasks/{task_id}/result", response_model=schemas.AgentFinalResultOut)
def get_result(task_id: str, db: Session = Depends(get_db)):
    run, root = _require_root_task(db, task_id)
    return schemas.AgentFinalResultOut(
        task_id=run.id,
        status=root.status,
        summary=run.summary,
        node_results=[_node_out(node) for node in _task_nodes(db, run.id)],
    )


@router.post("/tasks/{task_id}/cancel", response_model=schemas.AgentCollaborationTaskOut)
def cancel_task(task_id: str, db: Session = Depends(get_db)):
    run, root = _require_root_task(db, task_id)
    if run.status in _TERMINAL_STATUSES or root.status in _TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="终态协作任务不能取消")

    active_nodes = [node for node in _task_nodes(db, task_id) if node.status not in _TERMINAL_STATUSES]
    if not active_nodes:
        raise HTTPException(status_code=409, detail="终态协作任务不能取消")
    now = datetime.now()
    for node in active_nodes:
        node.status = "cancelled"
        node.finished_at = now
        db.add(models.AgentMessage(
            id=f"AM-{uuid4().hex}",
            root_task_id=task_id,
            task_id=node.id,
            parent_task_id=node.parent_task_id,
            message_type="task.cancel",
            sender_agent_id="platform",
            receiver_agent_id=node.assignee_agent_id,
            body="task cancelled",
            payload={"reason": "cancelled_by_user"},
            matrix_room_id=config.get(config.AGENTTEAMS_ROOM_ID) or "",
            matrix_event_id=None,
            idempotency_key=f"{task_id}:task.cancel:{node.id}",
            direction="outbound",
            delivery_status="pending",
        ))
    run.status = "cancelled"
    next_seq = db.scalar(
        select(func.max(models.AgentTaskEvent.event_seq)).where(
            models.AgentTaskEvent.root_task_id == task_id
        )
    ) or 0
    db.add(models.AgentTaskEvent(
        root_task_id=task_id,
        task_id=task_id,
        event_seq=next_seq + 1,
        event_type="task.cancelled",
        actor_agent_id="user",
        target_agent_id=root.assignee_agent_id,
        summary="task cancelled by user",
        detail={"cancelled_nodes": [node.id for node in active_nodes]},
    ))
    db.commit()
    return _task_out(db, run, root)


@router.post("/tasks/{task_id}/retry", response_model=schemas.AgentCollaborationTaskOut)
def retry_task(task_id: str, db: Session = Depends(get_db)):
    run, root = _require_root_task(db, task_id)
    if run.status == "completed" or root.status == "completed":
        raise HTTPException(status_code=409, detail="已完成协作任务不能重试")
    if run.status not in _TERMINAL_STATUSES or root.status not in _TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="仅终态协作任务可以重试")

    _require_ready(db)
    service = _service()
    retry_run = _commit_or_conflict(
        db,
        lambda: service.create_task(
            db,
            team_id=run.team_id,
            leader_agent_id=root.assignee_agent_id,
            title=root.title,
            instruction=root.instruction,
            material=dict(root.input_payload or {}),
        ),
    )
    return _task_out(db, retry_run)
