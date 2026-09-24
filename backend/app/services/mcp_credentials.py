"""自助接入 MCP 的鉴权凭据加解密与掩码。

设计约束：
- **明文永不落库**：``mcp_credential`` 只存密文与掩码，接口只回掩码；
- **零新增依赖**：仓库 requirements 里没有 ``cryptography``，因此用
  HMAC-SHA256 构造的流密码（keystream = HMAC(key, nonce‖counter)）做
  Encrypt-then-MAC，等价于一个无外部依赖的对称加密容器；
- **密钥来源**：环境变量 ``DWP_MCP_CREDENTIAL_KEY`` → secrets 文件
  （``DWP_MCP_CREDENTIAL_KEY_FILE``，默认 ``/run/secrets/mcp_credential_key``）。
  两者都缺失时**拒绝保存密钥**，但仍允许「匿名接入」（无鉴权服务）。
- 任何错误信息都不得回显密钥内容。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets as _secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models

CREDENTIAL_KEY_ENV = "DWP_MCP_CREDENTIAL_KEY"
CREDENTIAL_KEY_FILE_ENV = "DWP_MCP_CREDENTIAL_KEY_FILE"
DEFAULT_CREDENTIAL_KEY_FILE = "/run/secrets/mcp_credential_key"

_NONCE_LEN = 16
_TAG_LEN = 32
_DEFAULT_HEADER = "x-api-key"
_HINT_TAIL = 4


def _raw_key() -> str:
    """读取凭据加密密钥原文（环境变量优先，其次 secrets 文件）。不落日志。"""
    value = (os.environ.get(CREDENTIAL_KEY_ENV) or "").strip()
    if value:
        return value
    path = Path(os.environ.get(CREDENTIAL_KEY_FILE_ENV, DEFAULT_CREDENTIAL_KEY_FILE))
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def encryption_available() -> bool:
    """凭据加密是否可用（密钥是否已配置）。"""
    return bool(_raw_key())


def _derive_key() -> bytes:
    raw = _raw_key()
    if not raw:
        raise HTTPException(
            status_code=409,
            detail=(
                "未配置凭据加密密钥（DWP_MCP_CREDENTIAL_KEY 或 /run/secrets/mcp_credential_key），"
                "无法保存鉴权密钥；如需接入无鉴权的 MCP 服务，请把鉴权值留空"
            ),
        )
    return hmac.new(raw.encode("utf-8"), b"dwp-mcp-credential-v1", hashlib.sha256).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def encrypt(plaintext: str) -> str:
    """加密明文密钥，返回 base64(nonce‖ciphertext‖tag)。"""
    key = _derive_key()
    data = plaintext.encode("utf-8")
    nonce = _secrets.token_bytes(_NONCE_LEN)
    stream = _keystream(key, nonce, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(key, b"tag" + nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + ciphertext + tag).decode("ascii")


def decrypt(token: str) -> str:
    """解密；密文被篡改或格式非法时返回空串（调用方按「无凭据」处理）。"""
    if not token:
        return ""
    try:
        blob = base64.urlsafe_b64decode(token.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return ""
    if len(blob) < _NONCE_LEN + _TAG_LEN:
        return ""
    nonce, ciphertext, tag = blob[:_NONCE_LEN], blob[_NONCE_LEN:-_TAG_LEN], blob[-_TAG_LEN:]
    try:
        key = _derive_key()
    except HTTPException:
        return ""
    expected = hmac.new(key, b"tag" + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        return ""
    stream = _keystream(key, nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, stream)).decode("utf-8", errors="replace")


def mask(secret: str) -> str:
    """掩码：只保留末 4 位，短密钥整体打码。"""
    value = (secret or "").strip()
    if not value:
        return ""
    if len(value) <= _HINT_TAIL:
        return "*" * len(value)
    return "*" * 4 + value[-_HINT_TAIL:]


def normalize_header_name(name: str | None) -> str:
    """鉴权头名归一化：默认 x-api-key，限制为合法 HTTP token 字符。"""
    value = (name or "").strip() or _DEFAULT_HEADER
    if not all(ch.isalnum() or ch in "-_" for ch in value):
        raise HTTPException(status_code=422, detail="鉴权头名称只能包含字母、数字、连字符和下划线")
    return value[:64]


def save_credential(
    db: Session, *, plugin_id: str, header_name: str, secret: str, updated_by: str,
) -> models.McpCredential:
    """写入/更新凭据（空密钥表示匿名接入，删除既有凭据）。"""
    row = db.get(models.McpCredential, plugin_id)
    header = normalize_header_name(header_name)
    value = (secret or "").strip()
    if not value:
        if row is not None:
            db.delete(row)
        db.flush()
        return row  # type: ignore[return-value]
    ciphertext = encrypt(value)
    if row is None:
        row = models.McpCredential(
            plugin_id=plugin_id, header_name=header, secret_ciphertext=ciphertext,
            secret_hint=mask(value), updated_by=updated_by,
        )
        db.add(row)
    else:
        row.header_name = header
        row.secret_ciphertext = ciphertext
        row.secret_hint = mask(value)
        row.updated_by = updated_by
    row.updated_at = datetime.now()
    db.flush()
    return row


def load_secret(db: Session, plugin_id: str) -> str:
    """取回明文密钥（仅运行期在内存中使用，不外传）。"""
    row = db.get(models.McpCredential, plugin_id)
    if row is None or not row.secret_ciphertext:
        return ""
    return decrypt(row.secret_ciphertext)


def header_name_for(db: Session, plugin_id: str) -> str:
    row = db.get(models.McpCredential, plugin_id)
    return normalize_header_name(row.header_name if row else _DEFAULT_HEADER)


def hint_for(db: Session, plugin_id: str) -> str:
    row = db.get(models.McpCredential, plugin_id)
    return row.secret_hint if row else ""


# ---- 按人的用户级凭据（如兴证e家的 X-MCP-JWT）----
#
# 与 ``McpCredential``（一插件一密钥）不同：用户级凭据按人签发、会过期，
# 主键是 ``(plugin_id, human_no)``。同样只存密文与掩码，明文永不落库。


def jwt_expiry(token: str) -> datetime | None:
    """解析 JWT payload 的 ``exp``（UTC naive datetime）；无法解析返回 None。

    只做 base64url 解码读声明，不校验签名（服务端会校验）。用途是**提前判定过期**，
    避免带着必然失败的令牌去调用工具。返回值与 ``datetime.now()`` 同一时区口径。
    """
    parts = (token or "").strip().split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload + padding)
        claims = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    exp = claims.get("exp")
    if exp is None:
        return None
    try:
        return datetime.fromtimestamp(float(exp), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def save_user_credential(
    db: Session, *, plugin_id: str, human_no: str, header_name: str,
    secret: str, updated_by: str, expires_at: datetime | None = None,
) -> models.McpUserCredential | None:
    """写入/更新某人的用户级凭据；空密钥表示清除该人凭据（返回 None）。"""
    row = db.get(models.McpUserCredential, (plugin_id, human_no))
    header = normalize_header_name(header_name)
    value = (secret or "").strip()
    if not value:
        if row is not None:
            db.delete(row)
        db.flush()
        return None
    ciphertext = encrypt(value)
    if row is None:
        row = models.McpUserCredential(
            plugin_id=plugin_id, human_no=human_no, header_name=header,
            secret_ciphertext=ciphertext, secret_hint=mask(value),
            expires_at=expires_at, updated_by=updated_by,
        )
        db.add(row)
    else:
        row.header_name = header
        row.secret_ciphertext = ciphertext
        row.secret_hint = mask(value)
        row.expires_at = expires_at
        row.updated_by = updated_by
    row.updated_at = datetime.now()
    db.flush()
    return row


def user_credential_row(
    db: Session, *, plugin_id: str, human_no: str,
) -> models.McpUserCredential | None:
    return db.get(models.McpUserCredential, (plugin_id, human_no))


def load_user_credential(db: Session, *, plugin_id: str, human_no: str) -> str:
    """取回某人的明文用户级凭据（仅运行期在内存中使用，不外传）。"""
    row = db.get(models.McpUserCredential, (plugin_id, human_no))
    if row is None or not row.secret_ciphertext:
        return ""
    return decrypt(row.secret_ciphertext)


def user_credential_hint(db: Session, *, plugin_id: str, human_no: str) -> str:
    row = db.get(models.McpUserCredential, (plugin_id, human_no))
    return row.secret_hint if row else ""


def delete_user_credential(db: Session, *, plugin_id: str, human_no: str) -> None:
    row = db.get(models.McpUserCredential, (plugin_id, human_no))
    if row is not None:
        db.delete(row)
        db.flush()


def user_credential_expired(row: models.McpUserCredential | None, *, now: datetime | None = None) -> bool:
    """凭据是否已过期；未声明 ``expires_at`` 视为不过期（由服务端兜底判定）。"""
    if row is None or row.expires_at is None:
        return False
    return row.expires_at <= (now or datetime.now())


def credential_state(
    row: models.McpUserCredential | None, *, warn_days: int, now: datetime | None = None,
) -> dict[str, Any]:
    """用户级凭据的到期状态（**只读字段，不返回任何凭据内容**）。

    - ``expired``：已过期（到期时间早于当前时间）
    - ``expiring``：剩余天数 ≤ ``warn_days``，还没过期
    - ``ok``：还早，不提醒
    - ``unknown``：没有凭据或凭据未声明 ``expires_at``（不告警）

    时间口径与 ``datetime.now()`` 一致（后端存的是 naive UTC）；剩余天数不足一天
    但尚未过期时按 1 天显示，避免出现「0 天后过期」。
    """
    moment = now or datetime.now()
    if row is None or row.expires_at is None:
        return {"state": "unknown", "days_left": None, "expires_at": None}
    expires = row.expires_at
    if expires <= moment:
        return {"state": "expired", "days_left": 0, "expires_at": expires}
    days_left = int((expires - moment).total_seconds() // 86400)
    days_left = max(1, days_left)
    state = "expiring" if days_left <= max(1, int(warn_days)) else "ok"
    return {"state": state, "days_left": days_left, "expires_at": expires}
