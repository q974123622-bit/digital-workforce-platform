"""External MCP 动态适配器注册表。

把「MCP 源」从模块级硬编码单例（盈米 / 兴业证券）升级为「按数据库动态注册」：
凡是 ``plugin_type='mcp'`` ∧ ``status='active'`` ∧ 存在 ``deployment_mode='external'``
的已发布版本 ∧ ``runtime_meta.url`` 非空 的插件，都在运行期被构造成
``ExternalMcpAdapter``，与内置单例一起参与 ``routers/internal.py`` 的
``tools/list`` 合并与 ``tools/call`` 前缀分派。

缓存策略：
- 适配器实例按 ``plugin_id`` 进程级缓存（默认 10 秒，``DWP_MCP_REGISTRY_TTL_SECONDS``
  可覆盖），这样适配器内部的工具清单缓存（300 秒）才不会被每次请求重建冲掉；
- 登记 / 编辑地址或密钥 / 重新探测 / 发布 / 下架 五处都必须调用 ``invalidate()``，
  否则新服务最长要等 TTL 才生效。

安全：本模块只从 ``mcp_credentials`` 取明文密钥用于构造请求头，绝不落日志、不落库。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import mcp_credentials
from .external_mcp_adapter import (
    EHOME_TOOL_PREFIX,
    QIEMAN_TOOL_PREFIX,
    XYZQ_TOOL_PREFIX,
    ExternalMcpAdapter,
    ehome_adapter,
    qieman_adapter,
    xyzq_adapter,
)
from .external_mcp_client import ExternalMcpClient

logger = logging.getLogger(__name__)

# 内置 MCP 源（配置来自环境变量 / secrets 文件），始终参与合并。
BUILTIN_ADAPTERS: tuple[Any, ...] = (qieman_adapter, xyzq_adapter, ehome_adapter)

# 已被内置源占用的工具前缀，动态分配时禁止撞车。
RESERVED_TOOL_PREFIXES = frozenset({QIEMAN_TOOL_PREFIX, XYZQ_TOOL_PREFIX, EHOME_TOOL_PREFIX})

DEFAULT_TIMEOUT_SECONDS = 20.0
MIN_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 60.0

# plugin_id -> (adapter, expires_at_monotonic)
_CACHE: dict[str, tuple[ExternalMcpAdapter, float]] = {}


def registry_ttl_seconds() -> float:
    """适配器缓存 TTL（秒）；0 表示每次重建（测试可用来彻底关闭缓存）。"""
    try:
        return max(0.0, float(os.environ.get("DWP_MCP_REGISTRY_TTL_SECONDS") or "10"))
    except (TypeError, ValueError):
        return 10.0


def clamp_timeout(seconds: float | None) -> float:
    """单次请求超时区间受限，避免自助登记写入极端值拖垮进程。"""
    try:
        value = float(seconds) if seconds is not None else DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        value = DEFAULT_TIMEOUT_SECONDS
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, value))


def build_client(
    *,
    url: str,
    api_key: str,
    timeout_seconds: float,
    auth_header_name: str,
    auth_headers: dict[str, str] | None = None,
    protocol_version: str = "2024-11-05",
) -> ExternalMcpClient:
    """构造 MCP 客户端（独立函数便于测试注入 MockTransport）。

    ``auth_headers`` 供需要多个鉴权头的服务使用（如 VeRAG 的
    ``Authorization`` + ``X-Tenant``），为空时退回单头 ``auth_header_name``。
    """
    return ExternalMcpClient(
        url=url, api_key=api_key, timeout_seconds=timeout_seconds,
        auth_header_name=auth_header_name,
        auth_headers=auth_headers,
        protocol_version=protocol_version,
    )


def generate_tool_prefix() -> str:
    """生成工具前缀 ``mcp_<8位小写hex>__``（与内置的 qieman__ / xyzq__ 区分）。"""
    return f"mcp_{uuid4().hex[:8]}__"


def prefix_conflict(db: Session, prefix: str, exclude_plugin_id: str | None = None) -> bool:
    """前缀是否与内置源或已登记插件冲突。"""
    if not prefix or prefix in RESERVED_TOOL_PREFIXES:
        return True
    for plugin in db.scalars(select(models.Plugin)).all():
        if exclude_plugin_id and plugin.id == exclude_plugin_id:
            continue
        if str((plugin.runtime_meta or {}).get("tool_prefix") or "") == prefix:
            return True
    return False


def allocate_tool_prefix(db: Session, exclude_plugin_id: str | None = None) -> str:
    for _ in range(20):
        candidate = generate_tool_prefix()
        if not prefix_conflict(db, candidate, exclude_plugin_id):
            return candidate
    raise RuntimeError("无法分配唯一工具前缀")


def external_plugins(db: Session) -> list[models.Plugin]:
    """运行期可用的 External MCP 插件（active ∧ 有 external 已发布版本 ∧ url 非空）。"""
    plugins = db.scalars(select(models.Plugin).where(
        models.Plugin.plugin_type == "mcp",
        models.Plugin.status == "active",
    ).order_by(models.Plugin.id)).all()
    if not plugins:
        return []
    plugin_ids = [row.id for row in plugins]
    published_external = {
        row.plugin_id
        for row in db.scalars(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id.in_(plugin_ids),
            models.PluginVersion.publish_status == "published",
            models.PluginVersion.deployment_mode == "external",
        )).all()
    }
    result: list[models.Plugin] = []
    for plugin in plugins:
        if plugin.id not in published_external:
            continue
        meta = plugin.runtime_meta or {}
        if not str(meta.get("url") or "").strip():
            continue
        if not str(meta.get("tool_prefix") or "").strip():
            continue
        result.append(plugin)
    return result


def _build_adapter(db: Session, plugin: models.Plugin) -> ExternalMcpAdapter | None:
    """按插件运行信息构造适配器；密钥缺失时按「匿名接入」处理。"""
    meta = plugin.runtime_meta or {}
    url = str(meta.get("url") or "").strip()
    if not url:
        return None
    prefix = str(meta.get("tool_prefix") or "").strip()
    timeout = clamp_timeout(meta.get("timeout_seconds"))
    header_name = mcp_credentials.header_name_for(db, plugin.id)
    secret = mcp_credentials.load_secret(db, plugin.id)
    return ExternalMcpAdapter(
        plugin_id=plugin.id,
        tool_prefix=prefix,
        source_label=plugin.name,
        url_fn=lambda: url,
        api_key_fn=lambda: secret,
        timeout_fn=lambda: timeout,
        configured_fn=lambda: bool(url),
        auth_header_name=header_name,
        client_factory=lambda: build_client(
            url=url, api_key=secret, timeout_seconds=timeout, auth_header_name=header_name,
        ),
    )


def dynamic_adapters(db: Session) -> list[ExternalMcpAdapter]:
    """按数据库构造（并缓存）动态适配器列表。"""
    plugins = external_plugins(db)
    live_ids = {plugin.id for plugin in plugins}
    # 插件被下架/删除/失去 external 版本后，缓存条目必须立刻失效。
    for stale in [key for key in _CACHE if key not in live_ids]:
        _CACHE.pop(stale, None)

    ttl = registry_ttl_seconds()
    now = time.monotonic()
    result: list[ExternalMcpAdapter] = []
    for plugin in plugins:
        cached = _CACHE.get(plugin.id)
        if cached is not None and now < cached[1]:
            result.append(cached[0])
            continue
        adapter = _build_adapter(db, plugin)
        if adapter is None:
            _CACHE.pop(plugin.id, None)
            continue
        _CACHE[plugin.id] = (adapter, now + ttl)
        result.append(adapter)
    return result


def adapters(db: Session, builtin: Sequence[Any] | None = None) -> list[Any]:
    """运行期全部 External MCP 适配器 = 内置源 + 数据库动态注册。

    ``builtin`` 允许调用方（测试/扩展）替换内置源集合，默认取 ``BUILTIN_ADAPTERS``。
    """
    base = list(BUILTIN_ADAPTERS if builtin is None else builtin)
    seen = {getattr(adapter, "plugin_id", None) for adapter in base}
    for adapter in dynamic_adapters(db):
        if adapter.plugin_id in seen:
            continue
        base.append(adapter)
    return base


def match(adapters_seq: Sequence[Any], tool_name: str) -> Any | None:
    """按**最长前缀优先**分派工具调用。

    前缀是 ``startswith`` 匹配，若按声明顺序取首个命中，``mcp_ab__`` 会误吞
    ``mcp_abc__`` 的调用，因此必须比较前缀长度。
    """
    best: Any | None = None
    best_len = -1
    for adapter in adapters_seq:
        prefix = str(getattr(adapter, "tool_prefix", "") or "")
        if prefix and tool_name.startswith(prefix) and len(prefix) > best_len:
            best, best_len = adapter, len(prefix)
    return best


def invalidate(plugin_id: str | None = None) -> None:
    """显式失效：登记、编辑、重新探测、发布、下架后必须调用。"""
    if plugin_id is None:
        for adapter, _ in _CACHE.values():
            adapter.invalidate_tools()
        _CACHE.clear()
        return
    cached = _CACHE.pop(plugin_id, None)
    if cached is not None:
        cached[0].invalidate_tools()
