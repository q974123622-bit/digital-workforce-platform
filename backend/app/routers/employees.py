from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/employees", tags=["employees"])


def _grants_for(db: Session, employee_no: str) -> list[schemas.GrantOut]:
    plugins = {p.id: p for p in db.scalars(select(models.Plugin)).all()}
    rows = db.scalars(
        select(models.EmployeePluginGrant).where(models.EmployeePluginGrant.employee_id == employee_no)
    ).all()
    out = []
    for g in rows:
        plugin = plugins.get(g.plugin_id)
        out.append(
            schemas.GrantOut(
                plugin_id=g.plugin_id,
                name=plugin.name if plugin else "",
                type=plugin.type if plugin else "",
                action=g.action,
                decision_mode=g.decision_mode,
                data_level=plugin.data_level if plugin else "",
            )
        )
    return out


def _to_out(db: Session, emp: models.DigitalEmployee) -> schemas.EmployeeOut:
    return schemas.EmployeeOut(
        id=emp.employee_no,
        employee_no=emp.employee_no,
        name=emp.name,
        type=emp.type,
        source_human_no=emp.source_human_no,
        owner_human_no=emp.owner_human_no,
        department=emp.department,
        role_prompt=emp.role_prompt,
        status=emp.status,
        runtime_type=emp.runtime_type,
        runtime_ref=emp.runtime_ref,
        location=emp.location,
        internet=emp.internet,
        max_data_level=emp.max_data_level,
        allowed_domains=emp.allowed_domains or [],
        grants=_grants_for(db, emp.employee_no),
    )


def _next_employee_no(db: Session, type: str, source: str | None) -> str:
    if type == "twin":
        if not source:
            raise HTTPException(status_code=400, detail="twin 必须提供 source_human_no")
        return f"DT-{source}"
    prefix = {"virtual": "VE", "rpa": "RPA"}.get(type)
    if not prefix:
        raise HTTPException(status_code=400, detail="type 必须为 twin/virtual/rpa")
    rows = db.scalars(
        select(models.DigitalEmployee.employee_no).where(models.DigitalEmployee.employee_no.like(f"{prefix}-%"))
    ).all()
    max_n = max((int(n.split("-")[1]) for n in rows if "-" in n), default=0)
    return f"{prefix}-{max_n + 1:04d}"


@router.get("", response_model=list[schemas.EmployeeOut])
def list_employees(
    type: str | None = Query(default=None),
    department: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = select(models.DigitalEmployee).order_by(models.DigitalEmployee.employee_no)
    if type:
        q = q.where(models.DigitalEmployee.type == type)
    if department:
        q = q.where(models.DigitalEmployee.department == department)
    return [_to_out(db, e) for e in db.scalars(q).all()]


@router.post("", response_model=schemas.EmployeeOut, status_code=201)
def create_employee(payload: schemas.EmployeeCreate, db: Session = Depends(get_db)):
    employee_no = _next_employee_no(db, payload.type, payload.source_human_no)
    emp = models.DigitalEmployee(employee_no=employee_no, **payload.model_dump())
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return _to_out(db, emp)


@router.get("/{employee_no}", response_model=schemas.EmployeeOut)
def get_employee(employee_no: str, db: Session = Depends(get_db)):
    emp = db.get(models.DigitalEmployee, employee_no)
    if not emp:
        raise HTTPException(status_code=404, detail="员工不存在")
    return _to_out(db, emp)


@router.put("/{employee_no}", response_model=schemas.EmployeeOut)
def update_employee(employee_no: str, payload: schemas.EmployeeUpdate, db: Session = Depends(get_db)):
    emp = db.get(models.DigitalEmployee, employee_no)
    if not emp:
        raise HTTPException(status_code=404, detail="员工不存在")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(emp, field, value)
    db.commit()
    db.refresh(emp)
    return _to_out(db, emp)


@router.delete("/{employee_no}", status_code=204)
def delete_employee(employee_no: str, db: Session = Depends(get_db)):
    emp = db.get(models.DigitalEmployee, employee_no)
    if not emp:
        raise HTTPException(status_code=404, detail="员工不存在")
    db.delete(emp)
    db.commit()
