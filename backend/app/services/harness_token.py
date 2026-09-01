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


def _secret() -> bytes:
    configured = config.get("DWP_HARNESS_TOOL_SIGNING_SECRET")
    return configured.encode("utf-8") if configured else _EPHEMERAL_SECRET


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_token(
    *, employee_id: str, requester_human_no: str, trace_id: str, depth: int, ttl_seconds: int = 300
) -> str:
    now = int(time.time())
    payload = {
        "employee_id": employee_id,
        "requester_human_no": requester_human_no,
        "trace_id": trace_id,
        "depth": depth,
        "iat": now,
        "exp": now + min(max(ttl_seconds, 30), 600),
    }
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
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("无效的 Harness 工具令牌") from exc
    if claims.exp < int(time.time()):
        raise ValueError("Harness 工具令牌已过期")
    if claims.depth not in (0, 1):
        raise ValueError("无效的委派深度")
    return claims
