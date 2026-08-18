from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.llm import DeepSeekProvider
from ..services.team_orchestrator import TeamTaskOrchestrator

router = APIRouter(prefix="/teams", tags=["teams"])
tasks_router = APIRouter(tags=["tasks"])


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
    orchestrator = TeamTaskOrchestrator(DeepSeekProvider())
    return orchestrator.create_task(db, team_id=team_id, request=payload.request)


@router.get("/{team_id}/tasks/{task_id}", response_model=schemas.TaskRunOut)
def get_task(team_id: str, task_id: str, db: Session = Depends(get_db)):
    run = db.get(models.TaskRun, task_id)
    if run is None or run.team_id != team_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return TeamTaskOrchestrator._to_out(run)


@tasks_router.post("/tasks/{task_id}/approve", response_model=schemas.TaskRunOut)
def approve_task(task_id: str, payload: schemas.TaskApproveIn, db: Session = Depends(get_db)):
    """审批（Sprint 5）：仅 approval 挂起态可审批，否则 409。"""
    orchestrator = TeamTaskOrchestrator(DeepSeekProvider())
    return orchestrator.approve(db, task_id=task_id, approve=payload.approve, actor_no=payload.actor_no)
