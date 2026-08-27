"""Skill / Plugin 统一能力契约。

Skill 是只读注入型能力，不可产生副作用；Plugin 是经 Policy/Gateway 执行的
工具能力。所有调用方只消费本模块产出的规范化契约，不直接猜测 runtime_meta。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .. import models
from .adapters import REGISTRY

CONTRACT_VERSION = "1.0"
PLUGIN_TYPES = {"knowledge", "mcp", "workflow", "rpa", "http", "memory"}
PLUGIN_ACTIONS = {
    "knowledge": ["read"],
    "mcp": ["execute"],
    "workflow": ["execute"],
    "rpa": ["execute"],
    "http": ["search"],
    "memory": ["search"],
}

_MEMORY_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
    },
    "required": ["query"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class CapabilityContract:
    contract_version: str
    id: str
    name: str
    source_type: str  # skill | plugin
    kind: str
    description: str
    status: str
    executable: bool
    actions: list[str]
    input_schema: dict[str, Any]
    executor: dict[str, Any]
    owner_human_no: str | None = None
    ready: bool = True
    issues: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["issues"] = value["issues"] or []
        return value


def _default_plugin_meta(plugin: models.Plugin) -> dict[str, Any]:
    primary = "adapter" if plugin.type in {"knowledge", "memory"} else "harness"
    return {
        "contract_version": CONTRACT_VERSION,
        "actions": PLUGIN_ACTIONS.get(plugin.type, ["execute"]),
        "input_schema": (
            _MEMORY_INPUT_SCHEMA.copy()
            if plugin.type == "memory"
            else {"type": "object", "additionalProperties": True}
        ),
        "executor": {
            "primary": primary,
            "adapter_ref": plugin.endpoint_ref,
            "tool": "adapter",
            "fallback": "demo_adapter" if primary == "harness" else "none",
        },
    }


def plugin_contract(plugin: models.Plugin) -> CapabilityContract:
    meta = _default_plugin_meta(plugin)
    supplied = plugin.runtime_meta or {}
    for key in ("contract_version", "actions", "input_schema"):
        if key in supplied:
            meta[key] = supplied[key]
    meta["executor"].update(supplied.get("executor") or {})

    issues: list[str] = []
    if str(meta["contract_version"]) != CONTRACT_VERSION:
        issues.append(f"不支持的契约版本：{meta['contract_version']}")
    if plugin.type not in PLUGIN_TYPES:
        issues.append(f"不支持的插件类型：{plugin.type}")
    adapter_ref = str(meta["executor"].get("adapter_ref") or plugin.endpoint_ref)
    primary = meta["executor"].get("primary")
    if primary not in {"adapter", "harness"}:
        issues.append(f"不支持的执行器：{primary}")
    if adapter_ref != plugin.endpoint_ref:
        issues.append("executor.adapter_ref 必须与 plugin.endpoint_ref 一致")
    if plugin.type == "memory" and adapter_ref != "memory://agent-local":
        issues.append("memory 插件必须使用逻辑 endpoint：memory://agent-local")
    if not meta.get("actions"):
        issues.append("actions 不能为空")
    needs_adapter = primary == "adapter" or meta["executor"].get("tool") == "adapter"
    if plugin.type not in {"knowledge", "memory"} and needs_adapter and adapter_ref not in REGISTRY:
        issues.append(f"Adapter 未注册：{adapter_ref}")

    return CapabilityContract(
        contract_version=str(meta["contract_version"]),
        id=plugin.id,
        name=plugin.name,
        source_type="plugin",
        kind=plugin.type,
        description=plugin.description,
        status=plugin.status,
        executable=True,
        actions=[str(action) for action in meta.get("actions") or []],
        input_schema=meta.get("input_schema") or {"type": "object"},
        executor={**meta["executor"], "adapter_ref": adapter_ref},
        ready=not issues,
        issues=issues,
    )


def skill_contract(skill: models.Skill) -> CapabilityContract:
    return CapabilityContract(
        contract_version=CONTRACT_VERSION,
        id=skill.id,
        name=skill.name,
        source_type="skill",
        kind="instruction",
        description=skill.description,
        status=skill.status,
        executable=False,
        actions=["inject"],
        input_schema={"type": "object", "properties": {}},
        executor={"primary": "prompt", "max_chars": 4000},
        owner_human_no=skill.owner_human_no,
    )
