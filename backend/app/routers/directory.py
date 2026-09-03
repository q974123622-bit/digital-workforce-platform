from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.auth import current_account, require_roles

router = APIRouter(prefix="/directory", tags=["directory"])
wecom_router = APIRouter(prefix="/integrations/wecom", tags=["wecom"])


def _directory_row(db: Session, human: models.HumanEmployee) -> schemas.DirectoryUserOut:
    binding = db.scalar(
        select(models.DirectoryBinding).where(models.DirectoryBinding.human_employee_no == human.employee_no)
    )
    twin = db.scalar(
        select(models.DigitalEmployee).where(
            models.DigitalEmployee.type == "twin",
            models.DigitalEmployee.owner_human_no == human.employee_no,
        )
    )
    return schemas.DirectoryUserOut(
        provider=binding.provider if binding else "mock",
        external_user_id=binding.external_user_id if binding else human.employee_no,
        employee_no=human.employee_no,
        name=human.name,
        department=human.department,
        employment_type=human.employment_type,
        status=human.status,
        default_twin_id=twin.employee_no if twin else None,
    )


@router.get("/users", response_model=list[schemas.DirectoryUserOut])
def list_users(
    _: models.Account = Depends(require_roles("agent_admin", "platform_admin")),
    db: Session = Depends(get_db),
):
    humans = db.scalars(select(models.HumanEmployee).where(
        models.HumanEmployee.status == "active"
    ).order_by(models.HumanEmployee.employee_no)).all()
    return [_directory_row(db, human) for human in humans]


@router.post("/sync", response_model=list[schemas.DirectoryUserOut])
def sync_mock_directory(
    _: models.Account = Depends(require_roles("agent_admin", "platform_admin")),
    db: Session = Depends(get_db),
):
    humans = db.scalars(select(models.HumanEmployee).where(models.HumanEmployee.status == "active")).all()
    for human in humans:
        exists = db.scalar(
            select(models.DirectoryBinding).where(
                models.DirectoryBinding.provider == "mock",
                models.DirectoryBinding.external_user_id == human.employee_no,
            )
        )
        if exists is None:
            db.add(
                models.DirectoryBinding(
                    provider="mock",
                    corp_id="demo-corp",
                    external_user_id=human.employee_no,
                    human_employee_no=human.employee_no,
                )
            )
    db.commit()
    return [_directory_row(db, human) for human in humans]


@wecom_router.post("/mock-callback", response_model=schemas.WeComRouteOut)
def mock_wecom_callback(payload: schemas.WeComMockIn, db: Session = Depends(get_db)):
    binding = db.scalar(
        select(models.DirectoryBinding).where(
            models.DirectoryBinding.provider == "mock",
            models.DirectoryBinding.corp_id == payload.corp_id,
            models.DirectoryBinding.external_user_id == payload.wecom_user_id,
            models.DirectoryBinding.status == "active",
        )
    )
    if binding is None:
        raise HTTPException(status_code=404, detail="企微身份尚未绑定")
    human = db.get(models.HumanEmployee, binding.human_employee_no)
    target = None
    if payload.target_agent_id:
        target = db.get(models.DigitalEmployee, payload.target_agent_id)
        if target is not None and target.type == "twin" and target.owner_human_no != binding.human_employee_no:
            raise HTTPException(status_code=403, detail="不能路由到其他员工的数字分身")
    if target is None:
        target = db.scalar(
            select(models.DigitalEmployee).where(
                models.DigitalEmployee.type == "twin",
                models.DigitalEmployee.owner_human_no == binding.human_employee_no,
            )
        )
    if human is None or target is None:
        raise HTTPException(status_code=404, detail="未找到对应员工或数字分身")
    return schemas.WeComRouteOut(
        employee_no=human.employee_no,
        human_name=human.name,
        target_agent_id=target.employee_no,
        target_agent_name=target.name,
        content=payload.content,
    )
