from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.auth import SESSION_COOKIE, current_account, issue_session, revoke_session, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _account_out(db: Session, account: models.Account) -> schemas.AccountOut:
    human = db.get(models.HumanEmployee, account.human_employee_no)
    if human is None:
        raise HTTPException(status_code=409, detail="账号未绑定有效员工身份")
    return schemas.AccountOut(
        username=account.username,
        employee_no=human.employee_no,
        name=human.name,
        department=human.department,
        employment_type=human.employment_type,
        roles=account.roles or [],
        must_change_password=account.must_change_password,
    )


@router.post("/login", response_model=schemas.LoginOut)
def login(payload: schemas.LoginIn, response: Response, db: Session = Depends(get_db)):
    account = db.scalar(select(models.Account).where(models.Account.username == payload.username.strip()))
    if account is None or not verify_password(payload.password, account.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if account.status != "active":
        raise HTTPException(status_code=403, detail="账号已停用")
    token, session = issue_session(db, account)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=12 * 60 * 60,
        path="/",
    )
    return schemas.LoginOut(account=_account_out(db, account), expires_at=session.expires_at)


@router.get("/me", response_model=schemas.AccountOut)
def me(account: models.Account = Depends(current_account), db: Session = Depends(get_db)):
    return _account_out(db, account)


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
):
    revoke_session(db, token)
    response.delete_cookie(SESSION_COOKIE, path="/")

