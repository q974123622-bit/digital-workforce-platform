import os

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.auth import SESSION_COOKIE, current_account, hash_password, issue_session, revoke_session, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _human_avatar_url(db: Session, employee_no: str) -> str | None:
    """登录人的头像：复用本人数字分身的演示头像。

    ``human_employee`` 不存头像，演示头像资产只挂在 ``digital_employee.avatar_url``
    上（``frontend/public/avatars/``）；分身与本人是同一形象，直接复用可避免再维护一份
    自然人头像。管理端账号（如 ``SYS-ADMIN``）没有分身，返回 None，前端回退姓名首字。
    """
    twin = db.scalar(
        select(models.DigitalEmployee).where(
            models.DigitalEmployee.type == "twin",
            models.DigitalEmployee.owner_human_no == employee_no,
            models.DigitalEmployee.status == "active",
        ).order_by(models.DigitalEmployee.employee_no)
    )
    return twin.avatar_url if twin is not None else None


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
        avatar_url=_human_avatar_url(db, human.employee_no),
    )


@router.post("/login", response_model=schemas.LoginOut)
def login(payload: schemas.LoginIn, response: Response, db: Session = Depends(get_db)):
    if os.getenv("DWP_ENABLE_LEGACY_PASSWORD_LOGIN", "0") != "1":
        raise HTTPException(status_code=404, detail="密码登录已停用，请使用访问密钥")
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


@router.post("/key-login", response_model=schemas.LoginOut)
def key_login(payload: schemas.KeyLoginIn, response: Response, db: Session = Depends(get_db)):
    accounts = db.scalars(select(models.Account).where(models.Account.login_key_hash.is_not(None))).all()
    if not accounts:
        raise HTTPException(status_code=503, detail="访问密钥尚未配置")
    account = next((row for row in accounts if verify_password(payload.access_key, row.login_key_hash or "")), None)
    if account is None or account.status != "active":
        raise HTTPException(status_code=401, detail="访问密钥无效或账号已停用")
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


@router.post("/change-password", status_code=204)
def change_password(
    payload: schemas.ChangePasswordIn,
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    if os.getenv("DWP_ENABLE_LEGACY_PASSWORD_LOGIN", "0") != "1":
        raise HTTPException(status_code=404, detail="密码登录已停用")
    if not verify_password(payload.current_password, account.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    account.password_hash = hash_password(payload.new_password)
    account.must_change_password = False
    db.add(account)
    db.commit()
