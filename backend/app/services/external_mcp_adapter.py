"""External MCP 适配器（通用）。

职责：
- 按员工粒度做授权过滤（复用 ``AgentPluginBinding`` 治理模型，与知识工具一致）；
- 进程级缓存工具清单（``listChanged=true``，动态工具不硬编码 Schema）；
- 对外部工具名加前缀（如 ``qieman__`` / ``xyzq__``），避免多 MCP 源同名冲突；
- 统一错误转义（不暴露凭据与响应大段内容）。

安全：真实 api_key 仅通过 ``ExternalMcpClient`` 的 ``x-api-key`` 头发送，
本模块不落日志、不落数据库。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import config
from .external_mcp_client import ExternalMcpClient, ExternalMcpError

logger = logging.getLogger(__name__)

# 盈米 MCP 插件在治理体系中的唯一 ID（seed 时写入 Plugin + AgentPluginBinding）。
QIEMAN_PLUGIN_ID = "qieman-fund-mcp"
QIEMAN_TOOL_PREFIX = "qieman__"

# 兴业证券 MCP 插件 ID 与工具前缀。
XYZQ_PLUGIN_ID = "xyzq-mcp-server"
XYZQ_TOOL_PREFIX = "xyzq__"

# 兼容旧引用：TOOL_PREFIX 仍指向盈米前缀。
TOOL_PREFIX = QIEMAN_TOOL_PREFIX

# 工具清单缓存 TTL（秒）。工具 listChanged=true，短 TTL 兼顾新鲜度与延迟。
TOOLS_TTL_SECONDS = 300.0


class ExternalMcpAdapter:
    """通用 External MCP 运行时适配器（进程级单例）。

    通过构造参数注入 ``plugin_id`` / ``tool_prefix`` / 各配置取值函数，
    使同一套授权、缓存、命名空间逻辑复用给多个 MCP 源（盈米、兴业证券…）。
    """

    def __init__(
        self,
        *,
        plugin_id: str,
        tool_prefix: str,
        source_label: str,
        url_fn: Callable[[], str],
        api_key_fn: Callable[[], str],
        timeout_fn: Callable[[], float],
        configured_fn: Callable[[], bool] | None = None,
        client: ExternalMcpClient | None = None,
    ) -> None:
        self._plugin_id = plugin_id
        self._tool_prefix = tool_prefix
        self._source_label = source_label
        self._url_fn = url_fn
        self._api_key_fn = api_key_fn
        self._timeout_fn = timeout_fn
        self._configured_fn = configured_fn
        self._client = client
        self._cached_tools: list[dict[str, Any]] = []
        self._cached_at = 0.0

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    @property
    def tool_prefix(self) -> str:
        return self._tool_prefix

    @property
    def source_label(self) -> str:
        return self._source_label

    # -- client ----------------------------------------------------------

    def _get_client(self) -> ExternalMcpClient:
        if self._client is None:
            self._client = ExternalMcpClient(
                url=self._url_fn(),
                api_key=self._api_key_fn(),
                timeout_seconds=self._timeout_fn(),
            )
        return self._client

    def configured(self) -> bool:
        # 注入了 client（测试/复用场景）视为已配置；运行时则依赖配置函数。
        if self._client is not None:
            return True
        if self._configured_fn is not None:
            return self._configured_fn()
        return bool(self._url_fn() and self._api_key_fn())

    # -- authorization ---------------------------------------------------

    def is_authorized(self, db: Session, employee_id: str) -> bool:
        """该员工是否已绑定且启用了本适配器对应的 MCP 插件（allow 决策）。"""
        return db.scalar(select(models.AgentPluginBinding).join(
            models.Plugin, models.Plugin.id == models.AgentPluginBinding.plugin_id,
        ).where(
            models.AgentPluginBinding.target_agent_id == employee_id,
            models.AgentPluginBinding.admin_enabled.is_(True),
            models.AgentPluginBinding.employee_enabled.is_(True),
            models.AgentPluginBinding.decision_mode == "allow",
            models.Plugin.id == self._plugin_id,
            models.Plugin.status == "active",
        )) is not None

    # -- tool discovery --------------------------------------------------

    def list_tools(self, force: bool = False) -> list[dict[str, Any]]:
        """返回原始工具清单（带缓存）。连接失败时保留旧缓存并降级。"""
        if not self.configured():
            return []
        now = time.monotonic()
        if force or not self._cached_tools or (now - self._cached_at) > TOOLS_TTL_SECONDS:
            try:
                tools = self._get_client().list_tools()
                self._cached_tools = tools
                self._cached_at = now
            except ExternalMcpError as exc:
                logger.warning("%s MCP tools/list 失败，沿用旧缓存：%s", self._plugin_id, exc)
        return self._cached_tools

    def prefixed_tools(self, force: bool = False) -> list[dict[str, Any]]:
        """返回加了前缀、可直接下发给 Harness 的工具列表。"""
        tools: list[dict[str, Any]] = []
        for tool in self.list_tools(force=force):
            name = tool.get("name")
            if not isinstance(name, str) or not name:
                continue
            tools.append({**tool, "name": f"{self._tool_prefix}{name}"})
        return tools

    # -- tool call -------------------------------------------------------

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用工具（``name`` 需带前缀），返回 MCP 标准 result。"""
        if not name.startswith(self._tool_prefix):
            raise ExternalMcpError(f"非本适配器工具：{name}")
        raw_name = name[len(self._tool_prefix):]
        result = self._get_client().call_tool(raw_name, arguments or {})
        if isinstance(result, dict) and "content" in result:
            return result
        # 防御：个别 server 直接返回裸结果，统一包装为 MCP text content。
        return {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "isError": False,
        }


# 进程级单例：盈米 + 兴业证券两个 External MCP 源。
qieman_adapter = ExternalMcpAdapter(
    plugin_id=QIEMAN_PLUGIN_ID,
    tool_prefix=QIEMAN_TOOL_PREFIX,
    source_label="盈米",
    url_fn=config.qieman_mcp_url,
    api_key_fn=config.qieman_mcp_api_key,
    timeout_fn=config.qieman_mcp_timeout,
    configured_fn=config.qieman_mcp_configured,
)

xyzq_adapter = ExternalMcpAdapter(
    plugin_id=XYZQ_PLUGIN_ID,
    tool_prefix=XYZQ_TOOL_PREFIX,
    source_label="兴业证券",
    url_fn=config.xyzq_mcp_url,
    api_key_fn=lambda: "",  # 兴业证券匿名可调，无 api_key
    timeout_fn=config.xyzq_mcp_timeout,
    configured_fn=config.xyzq_mcp_configured,
)

# 兼容旧引用：QiemanMcpAdapter 指向泛化适配器类型。
QiemanMcpAdapter = ExternalMcpAdapter
