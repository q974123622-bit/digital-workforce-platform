"""统一可达性探测：知识源与外部 MCP 工具共用一套返回结构。

设计目标（用户要求「自动化的可复用的逻辑」）：
- 三类源共用同一出口 —— ``rest`` / ``verag`` / ``mcp:<plugin_id>``；
- 返回结构固定为 ``{source, state, latency_ms, last_error, detail, checked_at}``，
  管理端、定时任务、交付自检 CLI 全部消费这一个结构；
- MCP 类源复用既有的 ``mcp_onboarding.probe``（握手 + tools/list），不重复造轮子；
- 失败**不抛异常**：任何异常都转成 ``state="unavailable"`` + 可读 ``last_error``，
  这样定时任务与启动自检永远不会被探测失败打断；
- ``detail`` 只放非敏感信息（服务名、工具数、可见库数），绝不含 URL 凭据或响应正文。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from . import config, knowledge_source_registry as registry

logger = logging.getLogger(__name__)

STATE_AVAILABLE = "available"
STATE_UNAVAILABLE = "unavailable"
STATE_UNKNOWN = "unknown"

# MCP 插件源的前缀：``mcp:<plugin_id>``。
MCP_SOURCE_PREFIX = "mcp:"

_MAX_ERROR_CHARS = 200


def mcp_source(plugin_id: str) -> str:
    return f"{MCP_SOURCE_PREFIX}{plugin_id}"


def is_mcp_source(source: str) -> bool:
    return source.startswith(MCP_SOURCE_PREFIX)


def plugin_id_of(source: str) -> str:
    return source[len(MCP_SOURCE_PREFIX):] if is_mcp_source(source) else ""


def probe_source(
    source: str,
    *,
    db: Session | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """探测单个源，返回统一的健康摘要（永不抛异常）。"""
    timeout = timeout_seconds or config.knowledge_source_probe_timeout_seconds()
    started = time.monotonic()
    try:
        detail = _probe_impl(source, db=db, timeout_seconds=timeout)
    except Exception as exc:  # noqa: BLE001 - 探测必须吞掉所有异常，转成状态
        latency_ms = int((time.monotonic() - started) * 1000)
        message = _safe_message(exc)
        logger.warning("知识源探测失败 source=%s error=%s", source, message)
        return {
            "source": source,
            "state": STATE_UNAVAILABLE,
            "latency_ms": latency_ms,
            "last_error": message,
            "detail": {},
            "checked_at": datetime.now(),
        }
    latency_ms = int((time.monotonic() - started) * 1000)
    return {
        "source": source,
        "state": STATE_AVAILABLE,
        "latency_ms": latency_ms,
        "last_error": "",
        "detail": detail,
        "checked_at": datetime.now(),
    }


def probe_sources(
    sources: list[str],
    *,
    db: Session | None = None,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    return [
        probe_source(source, db=db, timeout_seconds=timeout_seconds)
        for source in sources
    ]


def _probe_impl(source: str, *, db: Session | None, timeout_seconds: float) -> dict[str, Any]:
    if source == registry.SOURCE_REST:
        return _probe_rest(timeout_seconds)
    if source == registry.SOURCE_VERAG:
        return _probe_verag(timeout_seconds)
    if is_mcp_source(source):
        return _probe_mcp_plugin(source, db=db, timeout_seconds=timeout_seconds)
    raise ValueError(f"未知知识源：{source}")


def _probe_rest(timeout_seconds: float) -> dict[str, Any]:
    from .internal_knowledge_adapter import InternalKnowledgeAdapter

    if not config.internal_kb_endpoint_configured():
        raise RuntimeError("内部知识引擎 REST 通道未配置")
    adapter = InternalKnowledgeAdapter(timeout_seconds=timeout_seconds)
    return adapter.probe(timeout_seconds=timeout_seconds)


def _probe_verag(timeout_seconds: float) -> dict[str, Any]:
    from .verag_mcp_knowledge_adapter import VeragMcpKnowledgeAdapter

    if not config.verag_mcp_configured():
        raise RuntimeError("内部知识引擎 VeRAG MCP 通道未配置")
    adapter = VeragMcpKnowledgeAdapter(timeout_seconds=timeout_seconds)
    detail: dict[str, Any] = dict(adapter.describe())
    try:
        payload = adapter.list_knowledge_bases(page=1, page_size=1)
        detail["visible_knowledge_bases"] = _as_int(payload.get("total"))
    except Exception as exc:  # noqa: BLE001 - 清单工具失败不影响「通道可达」判定
        logger.warning("VeRAG 知识库清单探测失败：%s", _safe_message(exc))
    return detail


def _probe_mcp_plugin(
    source: str, *, db: Session | None, timeout_seconds: float,
) -> dict[str, Any]:
    from .. import models
    from . import mcp_onboarding

    plugin_id = plugin_id_of(source)
    if db is None:
        raise RuntimeError("探测已登记的 MCP 插件需要数据库会话")
    plugin = db.get(models.Plugin, plugin_id)
    if plugin is None:
        raise RuntimeError("MCP 插件不存在")
    meta = plugin.runtime_meta or {}
    url = str(meta.get("url") or "").strip()
    if not url:
        raise RuntimeError("该插件不是 URL 接入的 External MCP，无法探测")
    result = mcp_onboarding.probe(
        db, url=url, timeout_seconds=timeout_seconds, plugin_id=plugin.id,
    )
    return {
        "server_info": result.get("server_info") or {},
        "tools_count": result.get("tools_count"),
        "protocol_version": result.get("protocol_version") or "",
    }


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_message(exc: BaseException) -> str:
    """异常转可读原因：只留类型与首行，截断长度，避免带出响应正文或凭据。"""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    message = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    return message[:_MAX_ERROR_CHARS]
