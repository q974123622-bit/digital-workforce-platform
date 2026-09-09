"""盈米 AI 开放平台 External MCP 适配器。

职责：
- 按员工粒度做授权过滤（复用 ``AgentPluginBinding`` 治理模型，与知识工具一致）；
- 进程级缓存工具清单（盈米 ``listChanged=true``，动态工具不硬编码 Schema）；
- 对外部工具名加 ``qieman__`` 前缀，避免未来多 MCP 源同名冲突；
- 统一错误转义（不暴露凭据与响应大段内容）。

安全：真实 api_key 仅通过 ``ExternalMcpClient`` 的 ``x-api-key`` 头发送，
本模块不落日志、不落数据库。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import config
from .external_mcp_client import ExternalMcpClient, ExternalMcpError

logger = logging.getLogger(__name__)

# 盈米 MCP 插件在治理体系中的唯一 ID（seed 时写入 Plugin + AgentPluginBinding）。
QIEMAN_PLUGIN_ID = "qieman-fund-mcp"

# 对外暴露给 Harness 的工具名前缀：qieman__<原始工具名>
TOOL_PREFIX = "qieman__"

# 工具清单缓存 TTL（秒）。盈米工具 listChanged=true，短 TTL 兼顾新鲜度与延迟。
TOOLS_TTL_SECONDS = 300.0


class QiemanMcpAdapter:
    """盈米 External MCP 的运行时适配器（进程级单例）。"""

    def __init__(self, client: ExternalMcpClient | None = None) -> None:
        self._client: ExternalMcpClient | None = client
        self._cached_tools: list[dict[str, Any]] = []
        self._cached_at: float = 0.0

    # -- client ----------------------------------------------------------

    def _get_client(self) -> ExternalMcpClient:
        if self._client is None:
            self._client = ExternalMcpClient(
                url=config.qieman_mcp_url(),
                api_key=config.qieman_mcp_api_key(),
                timeout_seconds=config.qieman_mcp_timeout(),
            )
        return self._client

    def configured(self) -> bool:
        # 注入了 client（测试/复用场景）视为已配置；运行时则依赖环境变量/secrets。
        return self._client is not None or config.qieman_mcp_configured()

    # -- authorization ---------------------------------------------------

    def is_authorized(self, db: Session, employee_id: str) -> bool:
        """该员工是否已绑定且启用了盈米 MCP 插件（allow 决策）。"""
        return db.scalar(select(models.AgentPluginBinding).join(
            models.Plugin, models.Plugin.id == models.AgentPluginBinding.plugin_id,
        ).where(
            models.AgentPluginBinding.target_agent_id == employee_id,
            models.AgentPluginBinding.admin_enabled.is_(True),
            models.AgentPluginBinding.employee_enabled.is_(True),
            models.AgentPluginBinding.decision_mode == "allow",
            models.Plugin.id == QIEMAN_PLUGIN_ID,
            models.Plugin.status == "active",
        )) is not None

    # -- tool discovery --------------------------------------------------

    def list_tools(self, force: bool = False) -> list[dict[str, Any]]:
        """返回盈米原始工具清单（带缓存）。连接失败时保留旧缓存并降级。"""
        if not self.configured():
            return []
        now = time.monotonic()
        if force or not self._cached_tools or (now - self._cached_at) > TOOLS_TTL_SECONDS:
            try:
                tools = self._get_client().list_tools()
                self._cached_tools = tools
                self._cached_at = now
            except ExternalMcpError as exc:
                logger.warning("qieman MCP tools/list 失败，沿用旧缓存：%s", exc)
        return self._cached_tools

    def prefixed_tools(self, force: bool = False) -> list[dict[str, Any]]:
        """返回加了 ``qieman__`` 前缀、可直接下发给 Harness 的工具列表。"""
        tools: list[dict[str, Any]] = []
        for tool in self.list_tools(force=force):
            name = tool.get("name")
            if not isinstance(name, str) or not name:
                continue
            tools.append({**tool, "name": f"{TOOL_PREFIX}{name}"})
        return tools

    # -- tool call -------------------------------------------------------

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用盈米工具（``name`` 需带 ``qieman__`` 前缀），返回 MCP 标准 result。"""
        if not name.startswith(TOOL_PREFIX):
            raise ExternalMcpError(f"非盈米工具：{name}")
        raw_name = name[len(TOOL_PREFIX):]
        result = self._get_client().call_tool(raw_name, arguments or {})
        if isinstance(result, dict) and "content" in result:
            return result
        # 防御：个别 server 直接返回裸结果，统一包装为 MCP text content。
        return {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "isError": False,
        }
