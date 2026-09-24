"""External MCP 适配器（通用）。

职责：
- 按员工粒度做授权过滤（复用 ``AgentPluginBinding`` 治理模型，与知识工具一致）；
- 进程级缓存工具清单（``listChanged=true``，动态工具不硬编码 Schema）；
- 对外部工具名加前缀（如 ``qieman__`` / ``xyzq__``），避免多 MCP 源同名冲突；
- 统一错误转义（不暴露凭据与响应大段内容）。

安全：真实 api_key 仅通过 ``ExternalMcpClient`` 的鉴权头（默认 ``x-api-key``，
自助接入的服务可自定义头名）发送，本模块不落日志、不落数据库。
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

# 兴证e家员工服务 MCP 插件 ID 与工具前缀（双鉴权头：应用级 key + 按人的用户级 JWT）。
EHOME_PLUGIN_ID = "xyzq-ehome-mcp"
EHOME_TOOL_PREFIX = "ehome__"

# 兼容旧引用：TOOL_PREFIX 仍指向盈米前缀。
TOOL_PREFIX = QIEMAN_TOOL_PREFIX

# 工具清单缓存 TTL（秒）。工具 listChanged=true，短 TTL 兼顾新鲜度与延迟。
TOOLS_TTL_SECONDS = 300.0

# 兴证e家里**需要用户级身份**的工具（查「我的」数据）。其余（通讯录等公开查询）不带
# ``X-MCP-JWT`` 也能调通，避免为一次通讯录查询就要求员工粘贴令牌。
EHOME_PUBLIC_TOOLS = frozenset({"getPhone", "getEmail"})

# 兴证e家服务端 schema 与实现不一致的坑（2026-09-22 真机逐条实测，不是推测）。
# 这些只靠服务端错误文本纠正的话，模型会反复试错、用户要等好几轮，所以直接写进工具描述。
_EHOME_DATE_HINTS = {
    "getEmpCardBill": "日期用 `yyyy-MM-dd`（如 2026-09-01）。",
    "getMycheckList": "日期用 `yyyyMMdd`（如 20260901）；传 `yyyy-MM-dd` 会被服务端错解析成 2025-12-09。",
    "getXtSubmitList": "日期用 `yyyyMMdd`（如 20260901）；传 `yyyy-MM-dd` 会被服务端错解析成 2025-12-09。",
    "getXtDoneList": "日期用 `yyyyMMdd`（如 20260901）；传 `yyyy-MM-dd` 会被服务端错解析成 2025-12-09。",
    "getMeetingRoomListAndTimeByTime": "时间用 `yyyy-MM-dd HH:mm`（如 2026-09-23 09:00）。",
    "getNewsDetailList": "日期用 `yyyy-MM-dd`。",
    "getEsDetailSearchList": "日期用 `yyyy-MM-dd`。",
}
_EHOME_TOOL_HINTS = {
    "getXtSubmitList": "`keyword` 实际必填（服务端 schema 未标注）；不传会报 Java 空指针错误。",
    "getXtDoneList": "`keyword` 实际必填（服务端 schema 未标注）；不传会报 Java 空指针错误。",
    "getMeetingRoomUseInfo": (
        "`meetingRoomNames` 要传**字符串**（多个用逗号分隔，如「上海1号楼2122会议室」），"
        "传数组会报「没有找到会议室」。先用 `getMeetingroomList` 拿到本人可见的真实名称。"
    ),
}
_EHOME_COMMON_HINT = (
    "判读结果要看返回体里的 `resphead.code`：200 才是查到数据；"
    "非 200（如 40001 无此员工、10057 无匹配单据、40052 无此会议室）表示没有匹配数据或参数不对，"
    "此时 `isError` 可能是 false，不要当成成功，也不要把空结果编造成数据。"
    "缺参数时先补齐参数再重试，不要反复重试同一个请求。"
)


def _ehome_tool_hint(raw_tool_name: str) -> str:
    """给兴证e家工具描述追加平台实测出来的使用提示。"""
    parts = [_EHOME_COMMON_HINT]
    if raw_tool_name in _EHOME_DATE_HINTS:
        parts.append(_EHOME_DATE_HINTS[raw_tool_name])
    if raw_tool_name in _EHOME_TOOL_HINTS:
        parts.append(_EHOME_TOOL_HINTS[raw_tool_name])
    return "\n".join(parts)


def _ehome_required_inputs() -> list[dict[str, Any]]:
    """兴证e家声明的必填输入（供 ``tool_input`` 事前预检）。

    ``source="app_secret"`` 表示应用级凭据（运维预置、员工无需补全）；
    ``source="user_secret"`` 表示按人签发、会过期的用户级凭据，缺失/过期时弹补全气泡。
    """
    return [
        {
            "key": config.ehome_mcp_app_header(),
            "label": "兴证e家应用密钥",
            "kind": "secret",
            "source": "app_secret",
            "required": True,
            "hint": "由平台运维统一配置，员工无需填写。",
        },
        {
            "key": config.ehome_mcp_user_header(),
            "label": "兴证e家用户令牌",
            "kind": "secret",
            "source": "user_secret",
            "required": True,
            "hint": (
                "从兴证e家 App 复制你的个人令牌（按人签发、会过期）。"
                "平台加密保存到你名下，之后查询「我的」数据会自动复用。"
            ),
            # 仅这些工具需要用户级身份；其余（如通讯录查询）不必打扰员工。
            "exclude_tools": sorted(EHOME_PUBLIC_TOOLS),
        },
    ]


class ExternalMcpAdapter:
    """通用 External MCP 运行时适配器（进程级单例）。

    通过构造参数注入 ``plugin_id`` / ``tool_prefix`` / 各配置取值函数，
    使同一套授权、缓存、命名空间逻辑复用给多个 MCP 源（盈米、兴业证券、兴证e家…）。
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
        auth_header_name: str = "x-api-key",
        required_inputs_fn: Callable[[], list[dict[str, Any]]] | None = None,
        tool_description_suffix_fn: Callable[[str], str] | None = None,
        client_factory: Callable[[], ExternalMcpClient] | None = None,
        client_factory_headers: Callable[[dict[str, str]], ExternalMcpClient] | None = None,
        client: ExternalMcpClient | None = None,
    ) -> None:
        self._plugin_id = plugin_id
        self._tool_prefix = tool_prefix
        self._source_label = source_label
        self._url_fn = url_fn
        self._api_key_fn = api_key_fn
        self._timeout_fn = timeout_fn
        self._configured_fn = configured_fn
        self._auth_header_name = auth_header_name or "x-api-key"
        self._required_inputs_fn = required_inputs_fn
        self._tool_description_suffix_fn = tool_description_suffix_fn
        self._client_factory = client_factory
        self._client_factory_headers = client_factory_headers
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
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                self._client = ExternalMcpClient(
                    url=self._url_fn(),
                    api_key=self._api_key_fn(),
                    timeout_seconds=self._timeout_fn(),
                    auth_header_name=self._auth_header_name,
                )
        return self._client

    def _client_with_headers(self, auth_headers: dict[str, str]) -> ExternalMcpClient:
        """按次构造带指定鉴权头的客户端（按人凭据场景，不复用缓存实例）。"""
        if self._client_factory_headers is not None:
            return self._client_factory_headers(auth_headers)
        return ExternalMcpClient(
            url=self._url_fn(),
            api_key=self._api_key_fn(),
            timeout_seconds=self._timeout_fn(),
            auth_header_name=self._auth_header_name,
            auth_headers=auth_headers,
        )

    def configured_api_key(self) -> str:
        """配置来源的应用级凭据（env / secrets 文件）。

        内置源（盈米、兴证e家）的应用级 key 来自 ``config``；自助登记的服务没有配置来源
        （其密钥存在数据库 ``mcp_credential`` 里），返回空字符串由调用方回落到数据库。
        """
        try:
            return str(self._api_key_fn() or "").strip()
        except Exception:  # noqa: BLE001 - 配置读取异常不应影响工具调用
            return ""

    def required_inputs(self) -> list[dict[str, Any]]:
        """本适配器声明的必填输入（应用级 + 用户级）；无声明返回空列表。"""
        if self._required_inputs_fn is None:
            return []
        try:
            return [item for item in (self._required_inputs_fn() or []) if isinstance(item, dict) and item.get("key")]
        except Exception:  # noqa: BLE001 - 声明函数异常不应影响工具调用
            logger.warning("%s MCP required_inputs 声明解析失败", self._plugin_id)
            return []

    def invalidate_tools(self) -> None:
        """清空工具清单缓存（登记/编辑地址或密钥/重新探测后立即生效）。"""
        self._cached_tools = []
        self._cached_at = 0.0

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
        """返回加了前缀、可直接下发给 Harness 的工具列表。

        ``tool_description_suffix_fn`` 存在时，给每个工具描述追加平台补充的提示
        （服务端 schema 没写清的必填项、日期格式、业务返回码判读等）——这些坑只靠
        服务端返回的错误文本纠正，模型会反复试错。
        """
        tools: list[dict[str, Any]] = []
        for tool in self.list_tools(force=force):
            name = tool.get("name")
            if not isinstance(name, str) or not name:
                continue
            item = {**tool, "name": f"{self._tool_prefix}{name}"}
            suffix = ""
            if self._tool_description_suffix_fn is not None:
                try:
                    suffix = str(self._tool_description_suffix_fn(name) or "").strip()
                except Exception:  # noqa: BLE001 - 提示补充失败不应影响工具下发
                    logger.warning("%s MCP 工具描述提示生成失败：%s", self._plugin_id, name)
            if suffix:
                item["description"] = f"{item.get('description') or ''}\n\n{suffix}".strip()
            tools.append(item)
        return tools

    # -- tool call -------------------------------------------------------

    def call_tool(
        self, name: str, arguments: dict[str, Any],
        auth_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """调用工具（``name`` 需带前缀），返回 MCP 标准 result。

        ``auth_headers`` 非空时按次构造客户端（按人凭据场景，如兴证e家的
        ``X-MCP-API-KEY`` + ``X-MCP-JWT``）；为空时走既有缓存客户端路径，
        静态适配器（盈米/兴业证券/自助登记）行为完全不变。
        """
        if not name.startswith(self._tool_prefix):
            raise ExternalMcpError(f"非本适配器工具：{name}")
        raw_name = name[len(self._tool_prefix):]
        client = self._get_client() if not auth_headers else self._client_with_headers(auth_headers)
        result = client.call_tool(raw_name, arguments or {})
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

# 兴证e家员工服务：应用级 key 由运维预置（用于 tools/list 握手与公开查询），
# 用户级 JWT 由 ``tool_input.build_auth_headers`` 按请求人从加密凭据表取出后按次传入。
ehome_adapter = ExternalMcpAdapter(
    plugin_id=EHOME_PLUGIN_ID,
    tool_prefix=EHOME_TOOL_PREFIX,
    source_label="兴证e家",
    url_fn=config.ehome_mcp_url,
    api_key_fn=config.ehome_mcp_api_key,
    timeout_fn=config.ehome_mcp_timeout,
    configured_fn=config.ehome_mcp_configured,
    auth_header_name=config.ehome_mcp_app_header(),
    required_inputs_fn=_ehome_required_inputs,
    tool_description_suffix_fn=_ehome_tool_hint,
)

# 兼容旧引用：QiemanMcpAdapter 指向泛化适配器类型。
QiemanMcpAdapter = ExternalMcpAdapter
