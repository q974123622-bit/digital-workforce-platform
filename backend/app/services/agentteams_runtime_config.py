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
import stat
from typing import Mapping


_ROLES = ("manager", "hr", "it", "audit")
_MAX_RUNTIME_CONFIG_BYTES = 65_536


@dataclass(frozen=True)
class MatrixIdentity:
    mxid: str
    token: str
    agent_id: str | None = None


@dataclass(frozen=True)
class StrictRuntimeConfig:
    matrix_url: str
    room_id: str
    platform: MatrixIdentity
    agents: Mapping[str, MatrixIdentity]
    team_catalog: object

    def public_view(self) -> dict:
        return {
            "matrix_url_configured": bool(self.matrix_url),
            "room_id": self.room_id,
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
    team_catalog = document.get("team_catalog")
    if not isinstance(team_catalog, (dict, list)):
        raise ValueError("invalid_runtime_config")
    return StrictRuntimeConfig(matrix_url, room_id, platform, agents, team_catalog)


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
    if not mxid.startswith("@") or len(mxid) > 255 or not token or len(token) > 4096 or (require_agent_id and not agent_id):
        raise ValueError("invalid_runtime_config")
    return MatrixIdentity(mxid=mxid, token=token, agent_id=agent_id)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""
