"""Local authentication used until the corporate directory/SSO adapter is connected."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db

SESSION_COOKIE = "dwp_session"
SESSION_HOURS = int(os.getenv("DWP_SESSION_HOURS", "12"))


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(db: Session, account: models.Account) -> tuple[str, models.AuthSession]:
    token = secrets.token_urlsafe(32)
    row = models.AuthSession(
        token_hash=_token_hash(token),
        account_id=account.id,
        expires_at=datetime.now() + timedelta(hours=SESSION_HOURS),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return token, row


def revoke_session(db: Session, token: str | None) -> None:
    if not token:
        return
    row = db.scalar(select(models.AuthSession).where(models.AuthSession.token_hash == _token_hash(token)))
    if row is not None:
        db.delete(row)
        db.commit()


def _extract_token(cookie_token: str | None, authorization: str | None) -> str | None:
    if cookie_token:
        return cookie_token
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def current_account(
    db: Session = Depends(get_db),
    cookie_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    authorization: str | None = Header(default=None),
) -> models.Account:
    token = _extract_token(cookie_token, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    session = db.scalar(select(models.AuthSession).where(models.AuthSession.token_hash == _token_hash(token)))
    if session is None or session.expires_at <= datetime.now():
        if session is not None:
            db.delete(session)
            db.commit()
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    account = db.get(models.Account, session.account_id)
    if account is None or account.status != "active":
        raise HTTPException(status_code=403, detail="账号已停用")
    return account


def optional_account(
    db: Session = Depends(get_db),
    cookie_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    authorization: str | None = Header(default=None),
) -> models.Account | None:
    token = _extract_token(cookie_token, authorization)
    if not token:
        if os.getenv("DWP_REQUIRE_AUTH", "1") == "1":
            raise HTTPException(status_code=401, detail="请先登录")
        return None
    return current_account(db=db, cookie_token=cookie_token, authorization=authorization)


def enforce_actor(account: models.Account | None, actor_no: str) -> None:
    if account is not None and account.human_employee_no != actor_no:
        raise HTTPException(status_code=403, detail="不能以其他员工身份执行操作")


def require_roles(*roles: str):
    def dependency(account: models.Account = Depends(current_account)) -> models.Account:
        if not set(roles).intersection(account.roles or []):
            raise HTTPException(status_code=403, detail="当前账号没有管理权限")
        return account

    return dependency
