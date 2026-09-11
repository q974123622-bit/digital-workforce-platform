"""Fail-closed local contract for the five strict Matrix identities.

The document belongs under the gitignored ``.agentteams-runtime`` directory.
This module intentionally keeps credential-bearing values private: callers that
need observability use :meth:`StrictRuntimeConfig.public_view` instead.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping


_LEGACY_ROLES = ("manager", "hr", "it", "audit")
_DATABASE_ROLES = (
    "manager", "db_write", "db_lock", "db_vacuum", "db_index", "db_query",
)
_ROLE_SETS = {frozenset(_LEGACY_ROLES), frozenset(_DATABASE_ROLES)}
_MAX_RUNTIME_CONFIG_BYTES = 65_536
_MAX_TOOL_INPUT_SCHEMA_BYTES = 2_048
_MAX_TOOL_SCHEMA_PROPERTIES = 8
_MAX_TOOL_SCHEMA_TEXT = 128
_SAFE_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
_SENSITIVE_SCHEMA_FIELD = re.compile(r"token|secret|password", re.I)
_SENSITIVE_SCHEMA_VALUE = re.compile(
    r"(?:\b(?:token|secret|password|api[_-]?key)\s*[:=]\s*\S+|\bsk-[A-Za-z0-9_-]{16,})",
    re.I,
)
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}

_DEMO_EMPLOYEE_REF_INPUT_SCHEMA = {
    "type": "object",
    "required": ["employee_ref"],
    "properties": {
        "employee_ref": {"type": "string", "const": "新员工A", "maxLength": 16},
    },
    "additionalProperties": False,
}

_ROLE_CHANGE_EMPLOYEE_REF_INPUT_SCHEMA = {
    "type": "object",
    "required": ["employee_ref"],
    "properties": {
        "employee_ref": {"type": "string", "const": "张明", "maxLength": 16},
    },
    "additionalProperties": False,
}

_ROLE_CHANGE_ACTIVATION_INPUT_SCHEMA = {
    "type": "object",
    "required": ["employee_ref", "approval_ref"],
    "properties": {
        "employee_ref": {"type": "string", "const": "张明", "maxLength": 16},
        "approval_ref": {"type": "string", "const": "AUD-RC-001", "maxLength": 32},
    },
    "additionalProperties": False,
}


def demo_employee_ref_input_schema() -> dict[str, Any]:
    """Return an isolated copy of the strict demo's single bounded input contract."""
    return deepcopy(_DEMO_EMPLOYEE_REF_INPUT_SCHEMA)


def role_change_employee_ref_input_schema() -> dict[str, Any]:
    """Return the strict input contract for the fixed role-change demo."""
    return deepcopy(_ROLE_CHANGE_EMPLOYEE_REF_INPUT_SCHEMA)


def role_change_activation_input_schema() -> dict[str, Any]:
    """Require the approval reference produced by the preceding audit task."""
    return deepcopy(_ROLE_CHANGE_ACTIVATION_INPUT_SCHEMA)


@dataclass(frozen=True)
class MatrixIdentity:
    mxid: str
    token: str
    agent_id: str | None = None
    allowed_skill_ids: tuple[str, ...] = ()
    safe_tool_catalog: tuple[dict[str, Any], ...] = ()


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
    """Read one bounded, regular JSON file and validate one supported team shape."""
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
    if (
        not matrix_url
        or not room_id
        or not isinstance(agents_raw, dict)
        or frozenset(agents_raw) not in _ROLE_SETS
    ):
        raise ValueError("invalid_runtime_config")
    roles = _LEGACY_ROLES if frozenset(agents_raw) == frozenset(_LEGACY_ROLES) else _DATABASE_ROLES
    platform = _identity(document.get("platform"), env, require_agent_id=False)
    agents = {role: _identity(agents_raw.get(role), env, require_agent_id=True) for role in roles}
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


def _tool_catalog(value: object, *, required: bool) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("invalid_runtime_config")
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {
            "name", "plugin_id", "action", "required_capability", "input_schema",
        }:
            raise ValueError("invalid_runtime_config")
        normalized = {key: _text(item.get(key)) for key in ("name", "plugin_id", "action", "required_capability")}
        if "action" not in item:
            normalized["action"] = "read"
        if not normalized["name"] or not normalized["plugin_id"] or any(len(part) > 128 for part in normalized.values()):
            raise ValueError("invalid_runtime_config")
        if normalized["action"] not in {"read", "query", "execute"}:
            raise ValueError("invalid_runtime_config")
        if any("token" in key.lower() or "secret" in key.lower() or "password" in key.lower() for key in item):
            raise ValueError("invalid_runtime_config")
        normalized["input_schema"] = _tool_input_schema(item.get("input_schema"))
        result.append(normalized)
    if len({item["name"] for item in result}) != len(result):
        raise ValueError("invalid_runtime_config")
    return tuple(result)


def _tool_input_schema(value: object) -> dict[str, Any]:
    """Validate the intentionally small JSON-Schema subset exposed to demo Workers."""
    if not isinstance(value, dict):
        raise ValueError("invalid_runtime_config")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("invalid_runtime_config") from None
    base_keys = {"type", "required", "properties", "additionalProperties"}
    database_contract_keys = {
        "x-signal-filter-fields", "x-signal-allows-empty-filters",
    }
    keys = set(value)
    if (
        len(encoded) > _MAX_TOOL_INPUT_SCHEMA_BYTES
        or not base_keys.issubset(keys)
        or keys - base_keys - database_contract_keys
        or bool(keys & database_contract_keys) != database_contract_keys.issubset(keys)
    ):
        raise ValueError("invalid_runtime_config")
    if value.get("type") != "object" or value.get("additionalProperties") is not False:
        raise ValueError("invalid_runtime_config")

    required = value.get("required")
    properties = value.get("properties")
    if (
        not isinstance(required, list)
        or not required
        or len(required) > _MAX_TOOL_SCHEMA_PROPERTIES
        or not isinstance(properties, dict)
        or not properties
        or len(properties) > _MAX_TOOL_SCHEMA_PROPERTIES
    ):
        raise ValueError("invalid_runtime_config")
    if any(
        not isinstance(field_name, str)
        or not field_name
        or len(field_name) > 64
        or _SENSITIVE_SCHEMA_FIELD.search(field_name)
        for field_name in required
    ):
        raise ValueError("invalid_runtime_config")
    if len(required) != len(set(required)) or set(required) != set(properties):
        raise ValueError("invalid_runtime_config")

    normalized_properties: dict[str, dict[str, Any]] = {}
    for field_name in required:
        field_schema = properties.get(field_name)
        normalized_properties[field_name] = _tool_property_schema(field_schema)
    normalized = {
        "type": "object",
        "required": list(required),
        "properties": normalized_properties,
        "additionalProperties": False,
    }
    if database_contract_keys.issubset(keys):
        normalized.update(_database_schema_extensions(value, normalized_properties))
    return normalized


def _database_schema_extensions(
    value: dict[str, Any],
    properties: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Validate the signal/filter relationship emitted by the shared DB contract."""
    signal_schema = properties.get("signal")
    filters_schema = properties.get("filters")
    if not signal_schema or not filters_schema:
        raise ValueError("invalid_runtime_config")
    signals = signal_schema.get("enum")
    filter_properties = filters_schema.get("properties")
    field_map = value.get("x-signal-filter-fields")
    empty_map = value.get("x-signal-allows-empty-filters")
    if (
        not isinstance(signals, list)
        or not isinstance(filter_properties, dict)
        or not isinstance(field_map, dict)
        or not isinstance(empty_map, dict)
        or set(field_map) != set(signals)
        or set(empty_map) != set(signals)
    ):
        raise ValueError("invalid_runtime_config")

    normalized_fields: dict[str, list[str]] = {}
    normalized_empty: dict[str, bool] = {}
    allowed_fields = set(filter_properties)
    for signal in signals:
        fields = field_map.get(signal)
        allows_empty = empty_map.get(signal)
        if (
            not isinstance(fields, list)
            or not fields
            or len(fields) > _MAX_TOOL_SCHEMA_PROPERTIES
            or len(fields) != len(set(fields))
            or any(not isinstance(field, str) or field not in allowed_fields for field in fields)
            or not isinstance(allows_empty, bool)
        ):
            raise ValueError("invalid_runtime_config")
        normalized_fields[signal] = list(fields)
        normalized_empty[signal] = allows_empty
    return {
        "x-signal-filter-fields": normalized_fields,
        "x-signal-allows-empty-filters": normalized_empty,
    }


def _tool_property_schema(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("invalid_runtime_config")
    if set(value) == {"type", "const", "maxLength"}:
        const = value.get("const")
        maximum = value.get("maxLength")
        if (
            value.get("type") != "string"
            or not isinstance(const, str)
            or not const
            or len(const) > _MAX_TOOL_SCHEMA_TEXT
            or _SENSITIVE_SCHEMA_VALUE.search(const)
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or maximum < 1
            or maximum > _MAX_TOOL_SCHEMA_TEXT
            or len(const) > maximum
        ):
            raise ValueError("invalid_runtime_config")
        return {"type": "string", "const": const, "maxLength": maximum}
    if set(value) == {"type", "enum"}:
        choices = value.get("enum")
        if (
            value.get("type") != "string"
            or not isinstance(choices, list)
            or not choices
            or len(choices) > 16
            or any(
                not isinstance(choice, str)
                or not choice
                or len(choice) > _MAX_TOOL_SCHEMA_TEXT
                or _SENSITIVE_SCHEMA_VALUE.search(choice)
                for choice in choices
            )
            or len(choices) != len(set(choices))
        ):
            raise ValueError("invalid_runtime_config")
        return {"type": "string", "enum": list(choices)}
    if set(value) == {"type", "minimum", "maximum"}:
        minimum = value.get("minimum")
        maximum = value.get("maximum")
        if (
            value.get("type") != "integer"
            or isinstance(minimum, bool)
            or not isinstance(minimum, int)
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or minimum < 0
            or maximum > 1000
            or minimum > maximum
        ):
            raise ValueError("invalid_runtime_config")
        return {"type": "integer", "minimum": minimum, "maximum": maximum}
    if set(value) == {"type", "additionalProperties", "maxProperties"}:
        additional = value.get("additionalProperties")
        maximum = value.get("maxProperties")
        if (
            value.get("type") != "object"
            or additional != {"type": "string"}
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or not 1 <= maximum <= 8
        ):
            raise ValueError("invalid_runtime_config")
        return {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "maxProperties": maximum,
        }
    if set(value) == {"type", "properties", "additionalProperties", "maxProperties"}:
        properties = value.get("properties")
        maximum = value.get("maxProperties")
        if (
            value.get("type") != "object"
            or value.get("additionalProperties") is not False
            or not isinstance(properties, dict)
            or not properties
            or len(properties) > _MAX_TOOL_SCHEMA_PROPERTIES
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or not 1 <= maximum <= _MAX_TOOL_SCHEMA_PROPERTIES
        ):
            raise ValueError("invalid_runtime_config")
        normalized_properties: dict[str, dict[str, Any]] = {}
        for field_name, field_schema in properties.items():
            if (
                not isinstance(field_name, str)
                or not field_name
                or len(field_name) > 64
                or _SENSITIVE_SCHEMA_FIELD.search(field_name)
                or not isinstance(field_schema, dict)
                or set(field_schema) != {"type", "minLength", "maxLength"}
            ):
                raise ValueError("invalid_runtime_config")
            minimum = field_schema.get("minLength")
            text_maximum = field_schema.get("maxLength")
            if (
                field_schema.get("type") != "string"
                or isinstance(minimum, bool)
                or not isinstance(minimum, int)
                or isinstance(text_maximum, bool)
                or not isinstance(text_maximum, int)
                or minimum < 1
                or text_maximum > _MAX_TOOL_SCHEMA_TEXT
                or minimum > text_maximum
            ):
                raise ValueError("invalid_runtime_config")
            normalized_properties[field_name] = {
                "type": "string",
                "minLength": minimum,
                "maxLength": text_maximum,
            }
        return {
            "type": "object",
            "properties": normalized_properties,
            "additionalProperties": False,
            "maxProperties": maximum,
        }
    raise ValueError("invalid_runtime_config")
