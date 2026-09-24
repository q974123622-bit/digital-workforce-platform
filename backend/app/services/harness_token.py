"""Short-lived, tamper-evident credentials for Harness tool calls."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

from . import config

_EPHEMERAL_SECRET = secrets.token_bytes(32)


@dataclass(frozen=True)
class HarnessClaims:
    employee_id: str
    requester_human_no: str
    trace_id: str
    depth: int
    exp: int
    # 发起本次任务的原始目标（委派时用于把上游任务背景透传给被委派方）。
    goal: str = ""
    # 发起本次任务的会话（委派时用于让被委派方看到该会话的最近对话）。
    conversation_id: str = ""


def _secret() -> bytes:
    configured = config.get("DWP_HARNESS_TOOL_SIGNING_SECRET")
    return configured.encode("utf-8") if configured else _EPHEMERAL_SECRET


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_token(
    *,
    employee_id: str,
    requester_human_no: str,
    trace_id: str,
    depth: int,
    ttl_seconds: int | None = None,
    goal: str = "",
    conversation_id: str = "",
) -> str:
    now = int(time.time())
    # 不传 ttl_seconds 时按单轮执行上限推导（config.harness_token_ttl_seconds）。
    # 硬上限只兜「配置写错」的极端值：令牌过期后 dsh 对平台 MCP 的所有调用会全量 401，
    # 长任务会中途静默失去平台能力，所以上限必须 >= Harness 单轮执行超时。
    ttl = config.harness_token_ttl_seconds() if ttl_seconds is None else ttl_seconds
    payload = {
        "employee_id": employee_id,
        "requester_human_no": requester_human_no,
        "trace_id": trace_id,
        "depth": depth,
        "iat": now,
        "exp": now + min(max(ttl, 30), 3600),
    }
    if goal:
        # 只透传本轮用户目标（已存在于同一容器的 task prompt 中），不引入新数据面。
        payload["goal"] = goal[:500]
    if conversation_id:
        # 会话标识（非正文）：委派方据此回查会话最近对话，不额外携带对话内容。
        payload["conversation_id"] = conversation_id[:64]
    encoded = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64(hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_token(token: str) -> HarnessClaims:
    try:
        encoded, signature = token.split(".", 1)
        expected = _b64(hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad signature")
        payload = json.loads(_unb64(encoded))
        claims = HarnessClaims(
            employee_id=str(payload["employee_id"]),
            requester_human_no=str(payload["requester_human_no"]),
            trace_id=str(payload["trace_id"]),
            depth=int(payload["depth"]),
            exp=int(payload["exp"]),
            goal=str(payload.get("goal") or ""),
            conversation_id=str(payload.get("conversation_id") or ""),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("无效的 Harness 工具令牌") from exc
    if claims.exp < int(time.time()):
        raise ValueError("Harness 工具令牌已过期")
    if claims.depth not in (0, 1):
        raise ValueError("无效的委派深度")
    return claims
