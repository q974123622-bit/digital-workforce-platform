"""知识源健康状态：落库、读取、环境判定与定时探测。

这一层是「可达性治理」的枢纽：
- 定时任务 / 管理端手动重测 → ``refresh_source`` / ``refresh_all`` → 探测并落库；
- 选路（``knowledge_adapter.select_adapter``）、工具清单摘除
  （``knowledge_registry.accessible_knowledge_bases``）、员工核验页状态
  （``routers/agents.py``）都只读这里的判定结果。

可用性语义（刻意「乐观」）：
- 未配置的源 = 不可用（连不上就是连不上）；
- 已配置但从未探测 / 探测结果未知 = 可用（不因为「还没测」就把功能摘掉，
  真正不可达时检索会返回可读提示，探测一旦标记 unavailable 立即摘除）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import config, knowledge_source_probe as probe
from . import knowledge_source_registry as registry

logger = logging.getLogger(__name__)

STATE_AVAILABLE = probe.STATE_AVAILABLE
STATE_UNAVAILABLE = probe.STATE_UNAVAILABLE
STATE_UNKNOWN = probe.STATE_UNKNOWN

# {engine_key: (expires_at_monotonic, {source: state})}
_SNAPSHOT_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_PROBE_LOCK = threading.Lock()


# -- 缓存 ---------------------------------------------------------------


def _engine_key(db: Session) -> str:
    try:
        return str(id(db.get_bind()))
    except Exception:  # noqa: BLE001 - 取不到 bind 时退回单例键，不影响正确性
        return "default"


def invalidate_cache() -> None:
    """清空源状态缓存（探测写入后必须调用，保证前端立刻看到新状态）。"""
    _SNAPSHOT_CACHE.clear()


def _state_snapshot(db: Session) -> dict[str, str]:
    key = _engine_key(db)
    now = time.monotonic()
    cached = _SNAPSHOT_CACHE.get(key)
    if cached is not None and now < cached[0]:
        return cached[1]
    rows = db.scalars(select(models.KnowledgeSourceHealth)).all()
    snapshot = {row.source: row.state or STATE_UNKNOWN for row in rows}
    _SNAPSHOT_CACHE[key] = (now + config.knowledge_source_ttl_seconds(), snapshot)
    return snapshot


# -- 判定 ---------------------------------------------------------------


def source_state(db: Session, source: str) -> str:
    """返回源的可用性状态（含「未配置」判定，供管理端展示）。"""
    if not registry.source_configured(source):
        return STATE_UNAVAILABLE
    return _state_snapshot(db).get(source, STATE_UNKNOWN)


def source_reachable(db: Session, source: str) -> bool:
    """最近一次探测是否**未发现故障**（未探测过也算，探测标记不可达才算 False）。

    可用性摘除只由探测结果驱动：这样「网络不通」会被摘掉，而「还没探测」
    不会把功能提前摘掉（真不可达时调用会返回可读提示，管理端也能一键重测）。
    """
    return _state_snapshot(db).get(source, STATE_UNKNOWN) != STATE_UNAVAILABLE


def source_available(db: Session, source: str) -> bool:
    """真正可用：配置齐备且最近探测未发现故障（用于选路与环境判定）。"""
    return registry.source_configured(source) and source_reachable(db, source)


def available_sources(db: Session) -> set[str]:
    return {source for source in registry.known_sources() if source_available(db, source)}


def resolve_environment(db: Session) -> str:
    """当前所处的环境：按偏好顺序取第一个可达的源所对应的环境。"""
    available = available_sources(db)
    for source in registry.preferred_source_order():
        if source in available:
            return registry.source_environment(source)
    return registry.ENV_UNKNOWN


def active_source_for_kb(db: Session, kb) -> str | None:
    """该知识库当前生效的通道；所有后端都不可达时返回 None。"""
    available = available_sources(db)
    for source in registry.preferred_source_order():
        if source in available and registry.engine_id(kb, source) is not None:
            return source
    return None


def is_kb_available(db: Session, kb) -> bool:
    """知识库是否可用（至少一个后端未被探测判定为不可达）。

    没有双后端映射的老知识库（远端 ID 仍在环境变量里）不在此处判定，
    交由适配器按既有 fail-closed 语义处理，避免升级瞬间把老库全摘掉。
    """
    if not registry.engine_map(kb):
        return True
    return any(source_reachable(db, source) for source in registry.sources_for_kb(kb))


def kb_availability(db: Session, kb) -> dict[str, Any]:
    """知识库的通道视图：双后端映射 + 当前生效通道 + 是否可用 + 不可用原因。"""
    mapping = registry.engine_map(kb)
    source = active_source_for_kb(db, kb)
    available = is_kb_available(db, kb)
    reason = ""
    if not available:
        labels = [registry.source_label(item) for item in registry.sources_for_kb(kb)]
        reason = f"{'、'.join(labels) or '登记的数据源'}最近一次检测不可达"
    return {
        "sources": mapping,
        "active_source": source,
        "available": available,
        "unavailable_reason": reason,
    }


# -- 落库 ---------------------------------------------------------------


def get_health(db: Session, source: str) -> models.KnowledgeSourceHealth | None:
    return db.scalar(
        select(models.KnowledgeSourceHealth).where(models.KnowledgeSourceHealth.source == source)
    )


def list_health(db: Session) -> list[models.KnowledgeSourceHealth]:
    return list(db.scalars(
        select(models.KnowledgeSourceHealth).order_by(models.KnowledgeSourceHealth.source)
    ).all())


def upsert_health(db: Session, result: dict[str, Any], *, commit: bool = True) -> models.KnowledgeSourceHealth:
    """写入探测结果（幂等）。"""
    row = get_health(db, result["source"])
    if row is None:
        row = models.KnowledgeSourceHealth(source=result["source"])
        db.add(row)
    row.state = str(result.get("state") or STATE_UNKNOWN)
    row.latency_ms = int(result.get("latency_ms") or 0)
    row.last_error = str(result.get("last_error") or "")[:500]
    row.detail = result.get("detail") or {}
    row.checked_at = result.get("checked_at") or datetime.now()
    row.updated_at = datetime.now()
    if commit:
        db.commit()
    invalidate_cache()
    return row


def registered_sources(db: Session) -> list[str]:
    """全部需要治理的源：两条知识通道 + 已登记的外部 MCP 插件。"""
    sources = list(registry.known_sources())
    from . import mcp_registry

    for plugin in mcp_registry.external_plugins(db):
        sources.append(probe.mcp_source(plugin.id))
    return sources


def refresh_source(db: Session, source: str, *, timeout_seconds: float | None = None) -> dict[str, Any]:
    """探测单个源并落库，返回探测结果。"""
    result = probe.probe_source(source, db=db, timeout_seconds=timeout_seconds)
    upsert_health(db, result)
    return result


def refresh_all(db: Session, *, include_mcp: bool = True) -> list[dict[str, Any]]:
    """探测全部源并落库（MCP 插件可选，避免管理端刷新过慢）。"""
    sources = list(registry.known_sources())
    if include_mcp:
        from . import mcp_registry

        sources += [probe.mcp_source(plugin.id) for plugin in mcp_registry.external_plugins(db)]
    results = []
    for source in sources:
        results.append(refresh_source(db, source))
    return results


def probe_once(*, include_mcp: bool = True) -> list[dict[str, Any]]:
    """自带会话的探测一轮（供定时任务与自检 CLI 复用）。"""
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        return refresh_all(db, include_mcp=include_mcp)
    finally:
        db.close()


# -- 定时任务 -----------------------------------------------------------


async def run_probe_loop() -> None:
    """后台定时探测。

    - 由 ``DWP_KB_SOURCE_PROBE_ENABLED`` 控制，默认关闭（离线/测试不主动访问内网）；
    - 单例保护：进程内同一时刻只跑一轮（多 worker 部署时各 worker 各测一次，
      结果写入同一张表，属幂等覆盖，不会互相干扰）；
    - **任何失败都不影响启动**：异常吞掉并记 warning。
    """
    if not config.knowledge_source_probe_enabled():
        logger.info("知识源定时探测未启用（DWP_KB_SOURCE_PROBE_ENABLED=0）")
        return
    interval = config.knowledge_source_probe_interval_seconds()
    logger.info("知识源定时探测已启用，间隔 %.0f 秒", interval)
    while True:
        try:
            await asyncio.to_thread(_run_probe_round)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 定时任务绝不能被单次失败打断
            logger.warning("知识源定时探测异常：%s", exc)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def _run_probe_round() -> None:
    if not _PROBE_LOCK.acquire(blocking=False):
        logger.info("上一轮知识源探测尚未结束，跳过本轮")
        return
    try:
        results = probe_once()
        available = [row["source"] for row in results if row["state"] == STATE_AVAILABLE]
        logger.info("知识源探测完成：可用 %s / 共 %d", available, len(results))
    finally:
        _PROBE_LOCK.release()
