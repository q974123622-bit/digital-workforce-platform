from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/policies", tags=["policies"])


def _next_id(db: Session) -> str:
    rows = db.scalars(select(models.Policy.id).where(models.Policy.id.like("POL-%"))).all()
    max_n = max((int(n.split("-")[1], 16) for n in rows if "-" in n), default=0)
    return f"POL-{max_n + 1:08x}"


@router.get("", response_model=list[schemas.PolicyOut])
def list_policies(db: Session = Depends(get_db)):
    return db.scalars(select(models.Policy).order_by(models.Policy.priority.desc())).all()


@router.post("", response_model=schemas.PolicyOut, status_code=201)
def create_policy(payload: schemas.PolicyCreate, db: Session = Depends(get_db)):
    policy_id = payload.id or _next_id(db)
    policy = models.Policy(id=policy_id, **payload.model_dump(exclude={"id"}))
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@router.get("/{policy_id}", response_model=schemas.PolicyOut)
def get_policy(policy_id: str, db: Session = Depends(get_db)):
    policy = db.get(models.Policy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="策略不存在")
    return policy


@router.put("/{policy_id}", response_model=schemas.PolicyOut)
def update_policy(policy_id: str, payload: schemas.PolicyUpdate, db: Session = Depends(get_db)):
    policy = db.get(models.Policy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="策略不存在")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(policy, field, value)
    db.commit()
    db.refresh(policy)
    return policy


@router.delete("/{policy_id}", status_code=204)
def delete_policy(policy_id: str, db: Session = Depends(get_db)):
    policy = db.get(models.Policy, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="策略不存在")
    db.delete(policy)
    db.commit()
