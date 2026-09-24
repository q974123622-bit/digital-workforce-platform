"""MCP 自助接入：连接探测 → 工具解析 → 制品生成 → 落库发布。

与既有的 ZIP 上传链路（``plugin_governance.submit_zip``）**并存但互不干扰**：
本模块只服务「填 URL + 鉴权方式」的运行时登记，产出同一套
``Plugin`` / ``PluginVersion`` / ``AgentPluginBinding`` 治理对象，
因此审核、发布、授权、员工端「可添加」全部复用既有状态机。

安全：
- 探测只回服务信息、能力声明与工具元数据，**绝不回显密钥**；
- 制品文件只写工具 Schema 与文案，不含任何凭据（``_contains_secret`` 口径不适用，
  因为本路径根本不写密钥字段）；
- 本环境 MCP 就是内网地址（10.x），因此**不禁内网**，只限制协议、超时与体积。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import yaml
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import mcp_credentials, mcp_registry
from .external_mcp_client import ExternalMcpError
from .plugin_governance import (
    MCP_CATEGORIES,
    artifact_root,
    next_plugin_id,
    publish_version,
    validate_version,
)

logger = logging.getLogger(__name__)

# 探测结果的体量上限：避免超大 MCP server 把响应撑爆或把 prompt 撑爆。
PROBE_MAX_TOOLS = 60
PROBE_DESCRIPTION_MAX = 1000
PROBE_PARAM_MAX = 12
PROBE_SCHEMA_MAX_CHARS = 6000
URL_MAX_LENGTH = 500
TOOL_DESCRIPTION_MAX = 1000
DATA_LEVELS = ("L1", "L2", "L3")
SCOPES = ("personal", "shared")


# -- 入参校验 ----------------------------------------------------------------


def validate_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="MCP 服务地址不能为空")
    if len(value) > URL_MAX_LENGTH:
        raise HTTPException(status_code=422, detail=f"MCP 服务地址不能超过 {URL_MAX_LENGTH} 字符")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="MCP 服务地址必须以 http:// 或 https:// 开头")
    if not parsed.netloc:
        raise HTTPException(status_code=422, detail="MCP 服务地址缺少主机名")
    return value


def validate_category(category: str | None) -> str:
    value = (category or "other").strip() or "other"
    if value not in MCP_CATEGORIES:
        raise HTTPException(status_code=422, detail="MCP 分类不合法")
    return value


def validate_data_level(data_level: str | None) -> str:
    value = (data_level or "L1").strip() or "L1"
    if value not in DATA_LEVELS:
        raise HTTPException(status_code=422, detail="数据等级必须为 L1/L2/L3")
    return value


def validate_scope(scope: str | None) -> str:
    value = (scope or "shared").strip() or "shared"
    if value not in SCOPES:
        raise HTTPException(status_code=422, detail="插件范围必须为 personal 或 shared")
    return value


# -- 工具清单摘要 ------------------------------------------------------------


def summarize_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """把 MCP 原始工具元数据压成前端可展示的摘要（不含凭据）。"""
    name = str(tool.get("name") or "").strip()
    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    parameters: list[dict[str, Any]] = []
    for key, spec in list(properties.items())[:PROBE_PARAM_MAX]:
        spec = spec if isinstance(spec, dict) else {}
        parameters.append({
            "name": str(key)[:120],
            "type": str(spec.get("type") or "any")[:32],
            "description": str(spec.get("description") or "")[:200],
        })
    required = [str(item)[:120] for item in (schema.get("required") or []) if isinstance(item, str)]
    return {
        "name": name[:120],
        "description": str(tool.get("description") or "")[:PROBE_DESCRIPTION_MAX],
        "parameters": parameters,
        "required": required,
        "parameters_truncated": len(properties) > PROBE_PARAM_MAX,
    }


def _schema_of(tool: dict[str, Any]) -> dict[str, Any]:
    """取工具入参 Schema：完整 Schema 优先，其次探测摘要的 ``input_schema``，
    最后按 ``parameters`` 重建骨架（保证落库制品始终有可用 Schema）。"""
    for key in ("inputSchema", "input_schema"):
        value = tool.get(key)
        if isinstance(value, dict) and value:
            return value
    properties: dict[str, Any] = {}
    for item in tool.get("parameters") or []:
        if isinstance(item, dict) and item.get("name"):
            properties[str(item["name"])[:120]] = {
                "type": str(item.get("type") or "string")[:32],
                "description": str(item.get("description") or "")[:200],
            }
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    required = [str(item)[:120] for item in (tool.get("required") or []) if isinstance(item, str)]
    if required:
        schema["required"] = required
    return schema


def normalize_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """落库/制品用的工具清单：保留完整 inputSchema（与 ZIP 通道口径一致）。"""
    result: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        result.append({
            "name": name[:120],
            "description": str(tool.get("description") or "")[:TOOL_DESCRIPTION_MAX],
            "inputSchema": _schema_of(tool),
        })
    return result


# -- 探测 --------------------------------------------------------------------


def resolve_probe_auth(
    db: Session, *, plugin_id: str | None, header_name: str | None, api_key: str | None,
) -> tuple[str, str]:
    """确定探测用鉴权；``api_key`` 为 None 且给了 ``plugin_id`` 时复用已存密钥。"""
    if api_key is None and plugin_id:
        secret = mcp_credentials.load_secret(db, plugin_id)
        header = mcp_credentials.header_name_for(db, plugin_id)
        return header, secret
    return mcp_credentials.normalize_header_name(header_name), (api_key or "").strip()


def probe(
    db: Session, *,
    url: str,
    header_name: str | None = None,
    api_key: str | None = None,
    timeout_seconds: float | None = None,
    plugin_id: str | None = None,
    include_full_tools: bool = False,
) -> dict[str, Any]:
    """连接外部 MCP 服务，完成握手并解析工具清单。**不落库**。

    ``include_full_tools=True`` 时额外返回 ``tools_full``（保留完整 inputSchema 的
    落库口径），供「编辑后比对工具清单是否变化」使用；HTTP 探测接口不开这个开关，
    避免把完整 Schema 塞进响应体。
    """
    target_url = validate_url(url)
    header, secret = resolve_probe_auth(
        db, plugin_id=plugin_id, header_name=header_name, api_key=api_key,
    )
    timeout = mcp_registry.clamp_timeout(timeout_seconds)
    client = mcp_registry.build_client(
        url=target_url, api_key=secret, timeout_seconds=timeout, auth_header_name=header,
    )
    started = datetime.now()
    # initialize 内部已包含 notifications/initialized 握手；list_tools 复用同一会话。
    handshake = client.initialize()
    tools = client.list_tools()
    latency_ms = int((datetime.now() - started).total_seconds() * 1000)
    summarized = [summarize_tool(tool) for tool in tools if isinstance(tool, dict)]
    payload: dict[str, Any] = {
        "url": target_url,
        "protocol_version": str(handshake.get("protocolVersion") or ""),
        "server_info": handshake.get("serverInfo") if isinstance(handshake.get("serverInfo"), dict) else {},
        "capabilities": handshake.get("capabilities") if isinstance(handshake.get("capabilities"), dict) else {},
        "tools": summarized[:PROBE_MAX_TOOLS],
        "tools_count": len(tools),
        "tools_truncated": len(tools) > PROBE_MAX_TOOLS,
        "latency_ms": latency_ms,
        "authenticated": bool(secret),
        "auth_header_name": header,
        "checked_at": datetime.now(),
    }
    if include_full_tools:
        # 与登记时的截断口径保持一致，否则工具数超上限时会误判「清单变了」。
        payload["tools_full"] = normalize_tools(
            [tool for tool in tools if isinstance(tool, dict)][:PROBE_MAX_TOOLS]
        )
    return payload


# -- 制品与落库 --------------------------------------------------------------


def write_artifact(manifest: dict[str, Any]) -> tuple[str, str]:
    """把探测结果序列化成 ``plugin.yaml`` 制品（``publish_version`` 要求文件真实存在）。"""
    submission_id = f"MCP-{uuid4().hex[:12].upper()}"
    relative = Path("staging") / submission_id / "plugin.yaml"
    target = artifact_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False).encode("utf-8")
    target.write_bytes(data)
    return relative.as_posix(), hashlib.sha256(data).hexdigest()


def create_mcp_plugin(
    db: Session, *,
    url: str,
    header_name: str | None,
    api_key: str | None,
    timeout_seconds: float | None,
    name: str,
    description: str,
    category: str | None,
    data_level: str,
    scope: str,
    tools: list[dict[str, Any]],
    version: str,
    submitter: str,
    auto_publish: bool,
    reviewer: str,
    server_info: dict[str, Any] | None = None,
    probe_meta: dict[str, Any] | None = None,
) -> models.PluginVersion:
    """登记一个 External MCP 插件。

    ``auto_publish=True``（管理端登记即发布）会复用 ``publish_version``，
    从而自动获得「personal 插件为属主分身建 admin_enabled/employee_enabled=False 绑定」
    等既有行为；``False``（员工端）只落 pending，走既有审批接口。
    """
    target_url = validate_url(url)
    clean_name = (name or "").strip()
    if not clean_name:
        raise HTTPException(status_code=422, detail="插件名称不能为空")
    clean_category = validate_category(category)
    clean_level = validate_data_level(data_level)
    clean_scope = validate_scope(scope)
    clean_version = validate_version(version)
    normalized_tools = normalize_tools(tools)
    if not normalized_tools:
        raise HTTPException(status_code=422, detail="MCP 服务未暴露任何工具，无法登记")
    timeout = mcp_registry.clamp_timeout(timeout_seconds)
    header = mcp_credentials.normalize_header_name(header_name)
    secret = (api_key or "").strip()

    plugin_id = next_plugin_id("mcp")
    tool_prefix = mcp_registry.allocate_tool_prefix(db)
    manifest = {
        "name": clean_name,
        "description": (description or "").strip()[:2000],
        "mcp_category": clean_category,
        "transport": "streamable_http",
        "tools": normalized_tools,
    }
    artifact_path, digest = write_artifact(manifest)

    now = datetime.now()
    plugin = models.Plugin(
        id=plugin_id, name=clean_name, type="mcp", plugin_type="mcp", scope=clean_scope,
        owner_human_no=submitter if clean_scope == "personal" else None,
        mcp_category=clean_category, current_version=None,
        endpoint_ref=target_url, data_level=clean_level,
        status="draft", description=manifest["description"],
        runtime_meta={
            "transport": "streamable_http",
            "url": target_url,
            "timeout_seconds": timeout,
            "tool_prefix": tool_prefix,
            "auth_header_name": header,
            "authenticated": bool(secret),
            "server_info": server_info or {},
            "probe": {
                "tools_count": len(normalized_tools),
                "latency_ms": int((probe_meta or {}).get("latency_ms") or 0),
                "checked_at": now.isoformat(),
                "source": "onboarding",
            },
        },
    )
    row = models.PluginVersion(
        plugin_id=plugin_id, version=clean_version, deployment_mode="external",
        artifact_path=artifact_path, sha256=digest, manifest=manifest, data_level=clean_level,
        review_status="approved" if auto_publish else "pending",
        publish_status="submitted", submitted_by=submitter,
        reviewed_by=reviewer if auto_publish else None,
        reviewed_at=now if auto_publish else None,
    )
    db.add(plugin)
    db.add(row)
    if secret:
        mcp_credentials.save_credential(
            db, plugin_id=plugin_id, header_name=header, secret=secret, updated_by=submitter,
        )
    db.flush()
    db.commit()
    if auto_publish:
        publish_version(db, row, reviewer)
        db.refresh(row)
    mcp_registry.invalidate(plugin_id)
    return row


def refresh_probe(db: Session, plugin: models.Plugin) -> dict[str, Any]:
    """重新探测已登记插件，并把连通状态写回 ``runtime_meta`` / ``McpRuntimeInstance``。

    探测失败时**先把失败摘要落库**（状态置不可达 + 记录可读原因与时间）**再原样重抛**：
    管理端仍拿到 502 与可读原因，列表页刷新后也能看到「哪一次探测、为什么失败」，
    而不是继续停在旧的「连通正常」。失败**不清空**既有工具清单与工具数。
    """
    meta = dict(plugin.runtime_meta or {})
    url = str(meta.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=409, detail="该插件不是 URL 接入的 External MCP，无法重新探测")
    try:
        result = probe(
            db, url=url, timeout_seconds=meta.get("timeout_seconds"), plugin_id=plugin.id,
            include_full_tools=True,
        )
    except ExternalMcpError as exc:
        _record_probe_failure(db, plugin, meta, exc)
        raise
    meta["probe"] = {
        "tools_count": result["tools_count"],
        "latency_ms": result["latency_ms"],
        "checked_at": result["checked_at"].isoformat(),
        "source": "recheck",
        "state": "reachable",
    }
    if result.get("server_info"):
        meta["server_info"] = result["server_info"]
    plugin.runtime_meta = meta
    _sync_runtime_instance(db, plugin, reachable=True, tools_count=result["tools_count"])
    db.commit()
    mcp_registry.invalidate(plugin.id)
    return result


def _record_probe_failure(
    db: Session, plugin: models.Plugin, meta: dict[str, Any], exc: Exception,
) -> None:
    """探测失败落库：写回失败摘要并把运行实例置为不可达，然后由调用方原样重抛。

    保留既有 ``tools_count`` / ``latency_ms``（上一次成功的探测结果仍然有效），
    只覆盖状态、原因与检查时间。
    """
    previous = meta.get("probe") if isinstance(meta.get("probe"), dict) else {}
    message = str(exc).strip()[:300] or "连接外部 MCP 服务失败"
    meta["probe"] = {
        "tools_count": previous.get("tools_count"),
        "latency_ms": previous.get("latency_ms"),
        "checked_at": datetime.now().isoformat(),
        "source": "recheck",
        "state": "unavailable",
        "last_error": message,
    }
    plugin.runtime_meta = meta
    _sync_runtime_instance(db, plugin, reachable=False, error=message)
    db.commit()
    mcp_registry.invalidate(plugin.id)


def _sync_runtime_instance(
    db: Session, plugin: models.Plugin, *, reachable: bool, tools_count: int | None = None,
    error: str = "",
) -> None:
    """把 External MCP 的真实连通状态写进运行实例（hosted 仍保持未接入）。"""
    if not plugin.current_version:
        return
    version = db.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin.id,
        models.PluginVersion.version == plugin.current_version,
    ))
    if version is None or version.deployment_mode != "external":
        return
    instance = db.scalar(select(models.McpRuntimeInstance).where(
        models.McpRuntimeInstance.plugin_version_id == version.id,
    ).order_by(models.McpRuntimeInstance.updated_at.desc()))
    if instance is None:
        instance = models.McpRuntimeInstance(plugin_version_id=version.id)
        db.add(instance)
    instance.state = "running" if reachable else "unavailable"
    instance.health = "healthy" if reachable else "unhealthy"
    instance.last_error = "" if reachable else (error.strip()[:300] or "连接外部 MCP 服务失败")
    instance.updated_at = datetime.now()


def list_registered(
    db: Session, *, owner_human_no: str | None = None,
) -> list[dict[str, Any]]:
    """已登记的 URL 接入 MCP 服务清单（**不含任何凭据**）。"""
    rows = db.scalars(select(models.Plugin).where(
        models.Plugin.plugin_type == "mcp",
    ).order_by(models.Plugin.name)).all()
    result: list[dict[str, Any]] = []
    for plugin in rows:
        meta = plugin.runtime_meta or {}
        url = str(meta.get("url") or "").strip()
        if not url:
            continue
        if owner_human_no is not None and plugin.owner_human_no != owner_human_no:
            continue
        probe_meta = meta.get("probe") if isinstance(meta.get("probe"), dict) else {}
        runtime = db.scalar(select(models.McpRuntimeInstance).join(
            models.PluginVersion, models.PluginVersion.id == models.McpRuntimeInstance.plugin_version_id,
        ).where(
            models.PluginVersion.plugin_id == plugin.id,
            models.PluginVersion.publish_status == "published",
        ).order_by(models.McpRuntimeInstance.updated_at.desc()))
        pending = db.scalar(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id == plugin.id,
            models.PluginVersion.review_status == "pending",
        ).order_by(models.PluginVersion.id.desc()))
        result.append({
            "plugin_id": plugin.id,
            "name": plugin.name,
            "description": plugin.description,
            "scope": plugin.scope,
            "owner_human_no": plugin.owner_human_no,
            "mcp_category": plugin.mcp_category,
            "data_level": plugin.data_level,
            "status": plugin.status,
            "current_version": plugin.current_version,
            "url": url,
            "timeout_seconds": meta.get("timeout_seconds"),
            "tool_prefix": meta.get("tool_prefix"),
            "authenticated": bool(meta.get("authenticated")),
            "auth_header_name": meta.get("auth_header_name"),
            "secret_hint": mcp_credentials.hint_for(db, plugin.id),
            "tools_count": probe_meta.get("tools_count"),
            "latency_ms": probe_meta.get("latency_ms"),
            "checked_at": probe_meta.get("checked_at"),
            "server_info": meta.get("server_info") or {},
            "runtime_state": runtime.state if runtime else None,
            "runtime_health": runtime.health if runtime else None,
            "pending_version_id": pending.id if pending else None,
            **probe_digest(probe_meta, runtime),
        })
    return result


def probe_digest(
    probe_meta: dict[str, Any], runtime: models.McpRuntimeInstance | None,
) -> dict[str, Any]:
    """列表页用的探测摘要：状态、可读失败原因、检查时间（**不含凭据**）。

    优先取本次探测写入的摘要；历史数据没有 ``state`` 时按运行实例状态回推，
    这样老插件升级后也能显示「连接失败 + 原因」而不是空白。
    """
    state = str(probe_meta.get("state") or "").strip()
    if not state and runtime is not None:
        state = "reachable" if runtime.state == "running" else (
            "unavailable" if runtime.state == "unavailable" else ""
        )
    last_error = str(probe_meta.get("last_error") or "").strip()
    if not last_error and runtime is not None and runtime.state != "running":
        last_error = str(runtime.last_error or "").strip()
    return {
        "probe_state": state or None,
        "last_error": last_error,
        "probe_checked_at": probe_meta.get("checked_at"),
    }


# -- 目录管理：编辑已登记服务 --------------------------------------------------


def _tool_signature(tools: Any) -> str:
    """工具清单指纹：只取「工具名 + 说明 + 入参 Schema」，用于判断是否需要出新版本。"""
    items = tools if isinstance(tools, list) else []
    payload = sorted(
        (
            str(tool.get("name") or ""),
            str(tool.get("description") or ""),
            json.dumps(tool.get("inputSchema") or {}, sort_keys=True, ensure_ascii=False),
        )
        for tool in items
        if isinstance(tool, dict)
    )
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def _next_version_number(db: Session, plugin: models.Plugin, current: str) -> str:
    """按末位数字递增出一个未占用的版本号（工具清单变化时的自动新版本）。"""
    existing = {row.version for row in db.scalars(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin.id,
    ))}
    parts = (current or "1.0.0").split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except (ValueError, IndexError):
        parts = ["1", "0", "1"]
    candidate = validate_version(".".join(parts))
    while candidate in existing:
        tail = candidate.split(".")
        try:
            tail[-1] = str(int(tail[-1]) + 1)
        except (ValueError, IndexError):
            tail = ["1", "0", "1"]
        candidate = validate_version(".".join(tail))
    return candidate


def _publish_tool_update(
    db: Session, plugin: models.Plugin, tools: list[dict[str, Any]], actor: str,
) -> str | None:
    """工具清单与当前已发布版本不一致时，自动出一个新版本并直接发布。

    与「管理端登记即发布」同口径：新版本 ``review_status='approved'`` 后走
    ``publish_version``，从而继承「旧版本置 superseded / current_version 前移 /
    运行时实例重建」等既有行为。清单一致时返回 None，避免无意义地堆版本。
    """
    version = db.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin.id,
        models.PluginVersion.publish_status == "published",
    ).order_by(models.PluginVersion.id.desc()))
    if version is None:
        return None
    normalized = normalize_tools(tools)
    if not normalized:
        return None
    previous_manifest = version.manifest or {}
    if _tool_signature(previous_manifest.get("tools")) == _tool_signature(normalized):
        return None
    new_no = _next_version_number(db, plugin, version.version)
    manifest = {
        "name": plugin.name,
        "description": plugin.description or "",
        "mcp_category": plugin.mcp_category or "other",
        "transport": str(previous_manifest.get("transport") or "streamable_http"),
        "tools": normalized,
    }
    artifact_path, digest = write_artifact(manifest)
    now = datetime.now()
    row = models.PluginVersion(
        plugin_id=plugin.id, version=new_no, deployment_mode="external",
        artifact_path=artifact_path, sha256=digest, manifest=manifest,
        data_level=plugin.data_level, review_status="approved", publish_status="submitted",
        submitted_by=actor, reviewed_by=actor, reviewed_at=now,
    )
    db.add(row)
    db.flush()
    publish_version(db, row, actor)
    db.refresh(row)
    logger.info("MCP 工具清单变化，已自动发布新版本 plugin=%s version=%s", plugin.id, new_no)
    return new_no


def update_mcp_plugin(
    db: Session,
    plugin: models.Plugin,
    *,
    name: str | None = None,
    description: str | None = None,
    category: str | None = None,
    data_level: str | None = None,
    url: str | None = None,
    timeout_seconds: float | None = None,
    header_name: str | None = None,
    api_key: str | None = None,
    actor: str,
) -> dict[str, Any]:
    """编辑已登记的 External MCP 服务：信息、地址、超时、鉴权头与密钥。

    ``api_key`` 语义与探测接口对齐：``None`` 不动、``""`` 显式转匿名（删除凭据）、
    非空则轮换。地址/超时/鉴权头/密钥任一变化都会立即重测（失败摘要照 1.1 落库）；
    工具清单与当前已发布版本不一致时自动出一个新版本并发布。

    返回 ``{"probe": <本次探测结果或 None>, "new_version": <新版本号或 None>}``；
    调用方负责出参整形，**永不回显密钥**。
    """
    meta = dict(plugin.runtime_meta or {})
    runtime_changed = False

    if name is not None:
        clean_name = (name or "").strip()
        if not clean_name:
            raise HTTPException(status_code=422, detail="插件名称不能为空")
        plugin.name = clean_name[:120]
    if description is not None:
        plugin.description = (description or "").strip()[:2000]
    if category is not None:
        plugin.mcp_category = validate_category(category)
    if data_level is not None:
        plugin.data_level = validate_data_level(data_level)
    if url is not None:
        target = validate_url(url)
        if target != str(meta.get("url") or "").strip():
            runtime_changed = True
        meta["url"] = target
    if timeout_seconds is not None:
        timeout = mcp_registry.clamp_timeout(timeout_seconds)
        if timeout != mcp_registry.clamp_timeout(meta.get("timeout_seconds")):
            runtime_changed = True
        meta["timeout_seconds"] = timeout

    header = (
        mcp_credentials.normalize_header_name(header_name)
        if header_name is not None
        else mcp_credentials.header_name_for(db, plugin.id)
    )
    if header_name is not None:
        if header != mcp_credentials.header_name_for(db, plugin.id):
            runtime_changed = True
        meta["auth_header_name"] = header
    if api_key is not None:
        value = (api_key or "").strip()
        mcp_credentials.save_credential(
            db, plugin_id=plugin.id, header_name=header, secret=value, updated_by=actor,
        )
        meta["authenticated"] = bool(value)
        runtime_changed = True
    elif header_name is not None:
        credential = db.get(models.McpCredential, plugin.id)
        if credential is not None:
            credential.header_name = header
    plugin.runtime_meta = meta
    db.flush()

    probe_result = None
    if runtime_changed:
        # 失败时 refresh_probe 会先把失败摘要落库再原样抛出，编辑本身已随之提交。
        probe_result = refresh_probe(db, plugin)
    new_version = None
    if probe_result is not None:
        # 用保留完整 Schema 的 tools_full 比对（tools 是前端展示摘要，丢了 inputSchema）
        new_version = _publish_tool_update(
            db, plugin, probe_result.get("tools_full") or probe_result.get("tools") or [], actor,
        )
    db.commit()
    mcp_registry.invalidate(plugin.id)
    return {"probe": probe_result, "new_version": new_version}
