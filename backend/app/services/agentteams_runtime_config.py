"""Fail-closed local contract for the five strict Matrix identities.

The document belongs under the gitignored ``.agentteams-runtime`` directory.
This module intentionally keeps credential-bearing values private: callers that
need observability use :meth:`StrictRuntimeConfig.public_view` instead.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Mapping


_ROLES = ("manager", "hr", "it", "audit")
_MAX_RUNTIME_CONFIG_BYTES = 65_536
_SAFE_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}


@dataclass(frozen=True)
class MatrixIdentity:
    mxid: str
    token: str
    agent_id: str | None = None
    allowed_skill_ids: tuple[str, ...] = ()
    safe_tool_catalog: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class StrictRuntimeConfig:
    matrix_url: str
    room_id: str
    platform: MatrixIdentity
    agents: Mapping[str, MatrixIdentity]
    team_id: str
    team_catalog: Mapping[str, object]

    def public_view(self) -> dict:
        return {
            "matrix_url_configured": bool(self.matrix_url),
            "room_id": self.room_id,
            "configured_team_id": self.team_id,
            "platform_mxid": self.platform.mxid,
            "agents": {
                role: {"agent_id": identity.agent_id, "matrix_user_id": identity.mxid}
                for role, identity in self.agents.items()
            },
        }


def default_runtime_config_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    configured = env.get("DWP_AGENTTEAMS_RUNTIME_CONFIG")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / ".agentteams-runtime" / "runtime.json"


def load_strict_runtime_config(
    path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> StrictRuntimeConfig:
    """Read one bounded, regular JSON file and validate all five identities."""
    env = os.environ if environ is None else environ
    target = Path(path) if path is not None else default_runtime_config_path(env)
    try:
        _require_safe_path(target)
        if target.is_symlink():
            raise ValueError
        metadata = target.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size < 2 or metadata.st_size > _MAX_RUNTIME_CONFIG_BYTES:
            raise ValueError
        raw = target.read_bytes()
        if len(raw) != metadata.st_size:
            raise ValueError
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        raise ValueError("invalid_runtime_config") from None
    if not isinstance(document, dict):
        raise ValueError("invalid_runtime_config")

    matrix_url = _text(document.get("matrix_url"))
    room_id = _text(document.get("room_id"))
    agents_raw = document.get("agents")
    if not matrix_url or not room_id or not isinstance(agents_raw, dict) or set(agents_raw) != set(_ROLES):
        raise ValueError("invalid_runtime_config")
    platform = _identity(document.get("platform"), env, require_agent_id=False)
    agents = {role: _identity(agents_raw.get(role), env, require_agent_id=True) for role in _ROLES}
    all_identities = [platform, *agents.values()]
    if len({identity.mxid for identity in all_identities}) != len(all_identities):
        raise ValueError("invalid_runtime_config")
    if len({identity.token for identity in all_identities}) != len(all_identities):
        raise ValueError("invalid_runtime_config")
    if len({identity.agent_id for identity in agents.values()}) != len(agents):
        raise ValueError("invalid_runtime_config")
    team_id = _text(document.get("team_id"))
    team_catalog = document.get("team_catalog")
    if not team_id or not isinstance(team_catalog, dict):
        raise ValueError("invalid_runtime_config")
    return StrictRuntimeConfig(matrix_url, room_id, platform, agents, team_id, team_catalog)


def _identity(raw: object, environ: Mapping[str, str], *, require_agent_id: bool) -> MatrixIdentity:
    if not isinstance(raw, dict):
        raise ValueError("invalid_runtime_config")
    mxid = _text(raw.get("mxid"))
    token = _text(raw.get("token"))
    token_env = _text(raw.get("token_env"))
    if token and token_env:
        raise ValueError("invalid_runtime_config")
    if token_env:
        token = _text(environ.get(token_env))
    agent_id = _text(raw.get("agent_id")) if require_agent_id else None
    if not mxid.startswith("@") or len(mxid) > 255 or not token or len(token) > 4096 or (require_agent_id and not is_safe_agent_id(agent_id)):
        raise ValueError("invalid_runtime_config")
    allowed = _string_list(raw.get("allowed_skill_ids"), maximum=32, required=True) if require_agent_id else ()
    tools = _tool_catalog(raw.get("safe_tool_catalog"), required=True) if require_agent_id else ()
    return MatrixIdentity(mxid=mxid, token=token, agent_id=agent_id, allowed_skill_ids=allowed, safe_tool_catalog=tools)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def is_safe_agent_id(value: str | None) -> bool:
    """Permit only a portable state-file stem, never paths or DOS devices."""
    return bool(value and _SAFE_AGENT_ID.fullmatch(value) and value.upper() not in _WINDOWS_RESERVED)


def _require_safe_path(target: Path) -> None:
    """Reject link/reparse ancestors before reading the credentials document."""
    absolute = target.absolute()
    for parent in (absolute, *absolute.parents):
        try:
            metadata = parent.lstat()
        except OSError:
            if parent == absolute:
                raise
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("invalid_runtime_config")


def require_safe_path(target: Path, *, allow_missing: bool = False) -> None:
    """Public reusable guard for config/state paths and Windows reparse points."""
    if not allow_missing:
        _require_safe_path(target)
        return
    absolute = target.absolute()
    for parent in (absolute, *absolute.parents):
        try:
            metadata = parent.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("invalid_runtime_config")


def _string_list(value: object, *, maximum: int, required: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or (required and not value) or len(value) > maximum:
        raise ValueError("invalid_runtime_config")
    if any(not isinstance(item, str) or not item.strip() or len(item.strip()) > 128 for item in value):
        raise ValueError("invalid_runtime_config")
    values = tuple(item.strip() for item in value)
    if len(values) != len(set(values)):
        raise ValueError("invalid_runtime_config")
    return values


def _tool_catalog(value: object, *, required: bool) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("invalid_runtime_config")
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"name", "plugin_id", "action", "required_capability"}:
            raise ValueError("invalid_runtime_config")
        normalized = {key: _text(item.get(key)) for key in ("name", "plugin_id", "action", "required_capability")}
        if not normalized["name"] or not normalized["plugin_id"] or any(len(part) > 128 for part in normalized.values()):
            raise ValueError("invalid_runtime_config")
        if any("token" in key.lower() or "secret" in key.lower() or "password" in key.lower() for key in item):
            raise ValueError("invalid_runtime_config")
        result.append(normalized)
    if len({item["name"] for item in result}) != len(result):
        raise ValueError("invalid_runtime_config")
    return tuple(result)
