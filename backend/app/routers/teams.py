from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import SessionLocal, get_db
from ..services import config
from ..services.llm import DeepSeekProvider
from ..services.runtime_adapter import DockerHarnessRuntimeAdapter, NoopRuntimeAdapter
from ..services.team_orchestrator import TeamTaskOrchestrator

router = APIRouter(prefix="/teams", tags=["teams"])
tasks_router = APIRouter(tags=["tasks"])


def _team_orchestrator() -> TeamTaskOrchestrator:
    """DWP_HARNESS_ENABLED=1 时用 Docker Harness 真实执行；否则 demo 模式（演示稳定）。"""
    if config.get("DWP_HARNESS_ENABLED") == "1":
        runtime = DockerHarnessRuntimeAdapter()
    else:
        runtime = NoopRuntimeAdapter()
    return TeamTaskOrchestrator(DeepSeekProvider(), runtime=runtime)


def _approve_continue_async(task_id: str) -> None:
    """审批通过后后台续跑执行（独立 DB session，避免审批请求再次阻塞）。"""
    db = SessionLocal()
    try:
        run = db.get(models.TaskRun, task_id)
        if run is not None and run.status == "running":
            _team_orchestrator()._run_loop(db, run)
    finally:
        db.close()


def _to_out(db: Session, team: models.Team) -> schemas.TeamOut:
    members = db.scalars(
        select(models.TeamMember).where(models.TeamMember.team_id == team.id).order_by(models.TeamMember.id)
    ).all()
    return schemas.TeamOut(
        id=team.id,
        name=team.name,
        leader_employee_id=team.leader_employee_id,
        description=team.description,
        members=[schemas.TeamMemberOut(employee_id=m.employee_id, role=m.role) for m in members],
    )


@router.get("", response_model=list[schemas.TeamOut])
def list_teams(db: Session = Depends(get_db)):
    teams = db.scalars(select(models.Team).order_by(models.Team.id)).all()
    return [_to_out(db, t) for t in teams]


@router.get("/{team_id}", response_model=schemas.TeamOut)
def get_team(team_id: str, db: Session = Depends(get_db)):
    team = db.get(models.Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="团队不存在")
    return _to_out(db, team)


@router.post("/{team_id}/tasks", response_model=schemas.TaskRunOut, status_code=201)
def create_task(team_id: str, payload: schemas.TaskCreateIn, db: Session = Depends(get_db)):
    """发起任务（Sprint 5）：模板拆解 + Worker 执行（走 Gateway） + 审批挂起 + Leader 汇总。"""
    orchestrator = _team_orchestrator()
    return orchestrator.create_task(db, team_id=team_id, request=payload.request)


@router.get("/{team_id}/tasks/{task_id}", response_model=schemas.TaskRunOut)
def get_task(team_id: str, task_id: str, db: Session = Depends(get_db)):
    run = db.get(models.TaskRun, task_id)
    if run is None or run.team_id != team_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return TeamTaskOrchestrator._to_out(run)


@tasks_router.post("/tasks/{task_id}/approve", response_model=schemas.TaskRunOut)
def approve_task(
    task_id: str,
    payload: schemas.TaskApproveIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """审批：受理即返回，通过后由后台继续执行（Sprint 11 异步化）。"""
    orchestrator = _team_orchestrator()
    out = orchestrator.apply_approval(
        db, task_id=task_id, approve=payload.approve, actor_no=payload.actor_no
    )
    if out.status == "running":
        background_tasks.add_task(_approve_continue_async, task_id)
    return out
