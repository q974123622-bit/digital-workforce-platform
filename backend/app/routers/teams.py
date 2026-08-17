from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/teams", tags=["teams"])


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
