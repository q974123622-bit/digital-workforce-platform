"""Secret / Config 安全访问（Sprint 3）。

铁律：内部 endpoint、Token、Credential 只能通过环境变量或 secure config 引用；
禁止写入 Git、写入 Prompt、写入日志。本模块只返回引用名/环境变量值，
任何真实凭据值不得入库、不得入日志。
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 加载 backend/.env（gitignored，仅本地受控环境；真实值绝不提交）
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

# 预留的环境变量名（真实值只存在于正式员工受控环境，仓库内不得出现）
INTERNAL_KB_ENDPOINT = "DWP_INTERNAL_KB_ENDPOINT"
INTERNAL_KB_CREDENTIAL_REF = "DWP_INTERNAL_KB_CREDENTIAL_REF"
INTERNAL_KB_BASE_URL = "DWP_INTERNAL_KB_BASE_URL"
INTERNAL_KB_RETRIEVAL_PATH = "DWP_INTERNAL_KB_RETRIEVAL_PATH"
INTERNAL_KB_X_ORG = "DWP_INTERNAL_KB_X_ORG"
INTERNAL_KB_X_TENANT = "DWP_INTERNAL_KB_X_TENANT"
INTERNAL_KB_X_USER = "DWP_INTERNAL_KB_X_USER"
INTERNAL_KB_AUTHORIZATION = "DWP_INTERNAL_KB_AUTHORIZATION"
INTERNAL_KB_ID_MAP = "DWP_INTERNAL_KB_ID_MAP"
ADP_ENDPOINT = "DWP_ADP_ENDPOINT"
ADP_CREDENTIAL_REF = "DWP_ADP_CREDENTIAL_REF"
RPA_ENDPOINT = "DWP_RPA_ENDPOINT"
RPA_CREDENTIAL_REF = "DWP_RPA_CREDENTIAL_REF"

# ---- Harness 超时链（唯一真相）----
#
# 单轮执行超时、MCP 工具调用超时、委派等待、工具令牌有效期、执行过期判定这五个
# 数字互相约束，散落多处靠注释口头约定没人守 —— 这里收敛成一处，任何一处要调
# 都必须重算其余几处。不变量（ops/dsh-contract-check.js 会按 contract.json 的
# timeout_invariant 校验第 1 条）：
#
#   1) delegation_slot_wait + max(role, twin) + 缓冲 <= profile.toolCallTimeoutMs / 1000
#      （否则被委派方的执行还没跑完，上游工具调用就先超时，表现为「委派静默失败、
#       发起方仍返回 completed，用户拿到没有知识库依据的答案」）
#   2) harness_token_ttl_seconds() >= max(role, twin)
#      （否则单轮跑到一半，dsh 对平台 MCP 的调用开始全量 401）
#   3) execution_stale_after_seconds() > max(role, twin)
#      （否则长任务中途被判 AGENT_EXECUTION_STALE 失败）
HARNESS_TIMEOUT_ROLE = "DWP_HARNESS_TIMEOUT_ROLE"
HARNESS_TIMEOUT_TWIN = "DWP_HARNESS_TIMEOUT_TWIN"
DELEGATION_SLOT_WAIT_SECONDS = "DWP_DELEGATION_SLOT_WAIT_SECONDS"
HARNESS_TOKEN_TTL_SECONDS = "DWP_HARNESS_TOKEN_TTL_SECONDS"
EXECUTION_STALE_AFTER_SECONDS = "DWP_EXECUTION_STALE_AFTER_SECONDS"

# 单轮超时的默认值：岗位AI员工 900 秒（15 分钟）、数字分身 1200 秒（20 分钟，含委派等待）。
# 早先的 300/360 秒对真实模型 + 多轮检索不够用，长任务会被中途掐死。
_DEFAULT_HARNESS_TIMEOUT_ROLE = 900
_DEFAULT_HARNESS_TIMEOUT_TWIN = 1200
# 令牌有效期比单轮上限多留一段余量，覆盖「任务即将结束时发起的最后一次工具调用」。
_TOKEN_TTL_GRACE_SECONDS = 300
# 执行过期判定比单轮上限多留一段余量，避免正常长任务被误判。
_STALE_GRACE_SECONDS = 600


def harness_timeout_role() -> int:
    """岗位AI员工（role_employee）的 Harness 执行超时，默认 900 秒。"""
    try:
        return max(60, int(os.environ.get(HARNESS_TIMEOUT_ROLE) or _DEFAULT_HARNESS_TIMEOUT_ROLE))
    except (TypeError, ValueError):
        return _DEFAULT_HARNESS_TIMEOUT_ROLE


def harness_timeout_twin() -> int:
    """数字分身（human_twin）的 Harness 执行超时，默认 1200 秒（需含委派等待）。"""
    try:
        return max(60, int(os.environ.get(HARNESS_TIMEOUT_TWIN) or _DEFAULT_HARNESS_TIMEOUT_TWIN))
    except (TypeError, ValueError):
        return _DEFAULT_HARNESS_TIMEOUT_TWIN


def harness_timeout_max() -> int:
    """两类身份中较大的单轮超时，供令牌有效期与过期判定推导。"""
    return max(harness_timeout_role(), harness_timeout_twin())


def harness_token_ttl_seconds() -> int:
    """Harness 工具令牌有效期（秒）= 单轮上限 + 300。

    必须 >= 单轮上限：令牌过期后 dsh 对平台 MCP 的所有调用（search_knowledge /
    附件 / load_skill / delegate）都会 401，长任务会在中途静默失去全部平台能力。
    """
    try:
        return max(
            harness_timeout_max(),
            int(os.environ.get(HARNESS_TOKEN_TTL_SECONDS) or (harness_timeout_max() + _TOKEN_TTL_GRACE_SECONDS)),
        )
    except (TypeError, ValueError):
        return harness_timeout_max() + _TOKEN_TTL_GRACE_SECONDS


def execution_stale_after_seconds() -> int:
    """活跃执行被判超时（AGENT_EXECUTION_STALE）的秒数 = 单轮上限 + 600。

    必须严格大于单轮上限：该判定会在用户每次发起新消息时执行，阈值小于单轮上限
    会把正在跑的长任务误杀成失败。
    """
    try:
        return max(
            harness_timeout_max() + 1,
            int(os.environ.get(EXECUTION_STALE_AFTER_SECONDS) or (harness_timeout_max() + _STALE_GRACE_SECONDS)),
        )
    except (TypeError, ValueError):
        return harness_timeout_max() + _STALE_GRACE_SECONDS


def delegation_slot_wait_seconds() -> int:
    """委派等待被委派方空闲的秒数，默认 120 秒；0 表示不等待（立即失败）。

    背景：`run_agent` 的 per-employee 锁是「一个员工同一时刻只能跑一个 harness 任务」
    （容器内 dsh 是一次性 CLI 调用）。历史上委派路径用 `acquire(blocking=False)`，
    目标员工一忙就直接 409 失败且不重试 —— 实测两个数字分身同时委派 AI-GENERAL 时，
    后到的那个 `delegation_run` 静默变成 failed、`evidence=[]`，而发起方仍返回
    completed，用户拿到的是**没有知识库依据**的答案。

    该值受 profile 的 `toolCallTimeoutMs` 约束（见文件顶部不变量 1）。
    """
    try:
        return max(0, min(300, int(os.environ.get(DELEGATION_SLOT_WAIT_SECONDS) or "120")))
    except (TypeError, ValueError):
        return 120


def get(name: str, default: str | None = None) -> str | None:
    """读取环境变量；未设置返回 default。值不落日志。"""
    return os.environ.get(name, default)


def credential_ref(name: str) -> str | None:
    """返回凭据引用名（环境变量名或 secure config key），不返回凭据本身。"""
    return os.environ.get(name)


def internal_kb_base_url() -> str | None:
    return os.environ.get(INTERNAL_KB_BASE_URL) or os.environ.get(INTERNAL_KB_ENDPOINT)


def internal_kb_retrieval_path() -> str:
    raw = (os.environ.get(INTERNAL_KB_RETRIEVAL_PATH) or "/marketing_agent/api/v2/rag/chunk/retrieval").strip()
    if not raw.startswith("/") or "://" in raw:
        return "/marketing_agent/api/v2/rag/chunk/retrieval"
    return raw


def internal_kb_id_map() -> dict[str, int]:
    raw = os.environ.get(INTERNAL_KB_ID_MAP) or "{}"
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        return {str(key): int(value) for key, value in parsed.items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def internal_kb_configured() -> bool:
    return bool(
        internal_kb_base_url()
        and all(
            get(name)
            for name in (
                INTERNAL_KB_X_ORG,
                INTERNAL_KB_X_TENANT,
                INTERNAL_KB_X_USER,
                INTERNAL_KB_AUTHORIZATION,
                INTERNAL_KB_ID_MAP,
            )
        )
        and internal_kb_id_map()
    )


# 企业微信（智能机器人长连接）接入配置。
# 长连接模式下由 backend 作为 WebSocket 客户端主动连接企微网关，
# 无公网回调地址、无 Token/AESKey 加解密。bot_secret 属敏感凭据，
# 优先从环境变量读取，其次从 secrets 文件读取（不落日志）。
WECOM_BOT_ENABLED = "DWP_WECOM_BOT_ENABLED"
WECOM_BOT_ID = "DWP_WECOM_BOT_ID"
WECOM_BOT_SECRET = "DWP_WECOM_BOT_SECRET"
WECOM_BOT_SECRET_FILE = "DWP_WECOM_BOT_SECRET_FILE"
WECOM_WS_URL = "DWP_WECOM_WS_URL"
WECOM_DRY_RUN = "DWP_WECOM_DRY_RUN"
WECOM_USER_BINDINGS = "DWP_WECOM_USER_BINDINGS"
WECOM_BOTS = "DWP_WECOM_BOTS"


@dataclass(frozen=True)
class WeComBotSpec:
    """单个企微智能机器人的承载声明。

    mode:
      - ``"fixed"``：机器人固定承载某个岗位员工（``employee_no`` 必填）；
      - ``"twin"``：机器人按发起人企微 userid 转接到其本人数字分身。

    alias 用于执行会话键与日志标识（ASCII 短名，如 general/investment/research/twin）；
    secret 为敏感凭据，不得写入日志或文档。
    """

    bot_id: str
    secret: str
    name: str
    alias: str
    mode: str = "twin"
    employee_no: str | None = None


def wecom_bot_enabled() -> bool:
    """企微机器人长连接总开关；默认关闭，避免未配置时误连。"""
    return os.environ.get(WECOM_BOT_ENABLED, "0") == "1"


def wecom_bot_id() -> str:
    return (os.environ.get(WECOM_BOT_ID) or "").strip()


def wecom_bot_secret() -> str:
    """bot_secret：环境变量优先，否则读 secrets 文件（不落日志）。"""
    value = (os.environ.get(WECOM_BOT_SECRET) or "").strip()
    if value:
        return value
    path = Path(os.environ.get(WECOM_BOT_SECRET_FILE, "/run/secrets/wecom_bot_secret"))
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def wecom_ws_url() -> str:
    """企微长连接网关地址，默认官方 wss 网关。"""
    return (os.environ.get(WECOM_WS_URL) or "wss://openws.work.weixin.qq.com").strip()


def wecom_dry_run() -> bool:
    """dry-run 开关：默认开启（"1"），只打印将要发送的内容，不真发消息。"""
    return os.environ.get(WECOM_DRY_RUN, "1") != "0"


def wecom_user_bindings() -> dict[str, str]:
    """企微机器人体系 staff ID -> 员工工号 全局映射（JSON）。"""
    raw = os.environ.get(WECOM_USER_BINDINGS) or "{}"
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def wecom_bots() -> list[WeComBotSpec]:
    """解析 ``DWP_WECOM_BOTS``（JSON 数组）得到机器人规格列表。

    向后兼容：未配置 ``DWP_WECOM_BOTS`` 时回退到旧单 bot 变量
    （``DWP_WECOM_BOT_ID`` + ``DWP_WECOM_BOT_SECRET``），其语义等价于一个
    ``mode="twin"`` 的机器人，保证存量部署与既有测试不破。
    """
    raw = (os.environ.get(WECOM_BOTS) or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, list):
            bots: list[WeComBotSpec] = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                bot_id = str(item.get("bot_id") or "").strip()
                secret = str(item.get("secret") or "").strip()
                if not bot_id or not secret:
                    continue
                mode = str(item.get("mode") or "twin").strip().lower()
                if mode not in ("fixed", "twin"):
                    mode = "twin"
                bots.append(
                    WeComBotSpec(
                        bot_id=bot_id,
                        secret=secret,
                        name=str(item.get("name") or bot_id),
                        alias=str(item.get("alias") or bot_id),
                        mode=mode,
                        employee_no=(str(item.get("employee_no") or "").strip()) or None,
                    )
                )
            if bots:
                return bots
    bot_id = wecom_bot_id()
    secret = wecom_bot_secret()
    if bot_id and secret:
        return [WeComBotSpec(bot_id=bot_id, secret=secret, name="数字分身", alias="twin", mode="twin")]
    return []


def wecom_bot_configured() -> bool:
    """长连接所需的全部配置是否齐备（任一机器人凭据齐全即 True）。"""
    return bool(wecom_bots())


# 盈米 AI 开放平台 External MCP 接入配置。
# api_key 属敏感凭据，优先从环境变量读取，其次从 secrets 文件读取（不落日志）。
QIEMAN_MCP_URL = "DWP_QIEMAN_MCP_URL"
QIEMAN_MCP_API_KEY = "DWP_QIEMAN_MCP_API_KEY"
QIEMAN_MCP_API_KEY_FILE = "DWP_QIEMAN_MCP_API_KEY_FILE"
QIEMAN_MCP_TIMEOUT = "DWP_QIEMAN_MCP_TIMEOUT"


def qieman_mcp_url() -> str:
    """盈米 MCP Streamable HTTP 端点地址（如 https://stargate.yingmi.com/mcp/v2）。"""
    return (os.environ.get(QIEMAN_MCP_URL) or "").strip()


def qieman_mcp_api_key() -> str:
    """盈米 MCP api_key：环境变量优先，否则读 secrets 文件（不落日志）。"""
    value = (os.environ.get(QIEMAN_MCP_API_KEY) or "").strip()
    if value:
        return value
    path = Path(os.environ.get(QIEMAN_MCP_API_KEY_FILE, "/run/secrets/qieman_api_key"))
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def qieman_mcp_timeout() -> float:
    """盈米 MCP 单次请求超时（秒），默认 20 秒。"""
    try:
        return max(5.0, float(os.environ.get(QIEMAN_MCP_TIMEOUT) or "20"))
    except (TypeError, ValueError):
        return 20.0


def qieman_mcp_configured() -> bool:
    """盈米 MCP 接入是否配置齐备（URL 与 api_key 均非空）。"""
    return bool(qieman_mcp_url() and qieman_mcp_api_key())


# ---- 兴业证券 MCP（xyzq-mcp-server）----
XYZQ_MCP_URL = "DWP_XYZQ_MCP_URL"
XYZQ_MCP_TIMEOUT = "DWP_XYZQ_MCP_TIMEOUT"


def xyzq_mcp_url() -> str:
    """兴业证券 MCP Streamable HTTP 端点地址（如 https://developer.xyzq.cn/agent/a2a/mcp）。"""
    return (os.environ.get(XYZQ_MCP_URL) or "").strip()


def xyzq_mcp_timeout() -> float:
    """兴业证券 MCP 单次请求超时（秒），默认 20 秒。"""
    try:
        return max(5.0, float(os.environ.get(XYZQ_MCP_TIMEOUT) or "20"))
    except (TypeError, ValueError):
        return 20.0


def xyzq_mcp_configured() -> bool:
    """兴业证券 MCP 接入是否配置齐备（仅需 URL，匿名无 api_key）。"""
    return bool(xyzq_mcp_url())


# ---- 兴证e家员工服务 MCP（xyzq-ehome-mcp）----
# 双鉴权头：
#   * 应用级 ``X-MCP-API-KEY``（静态、必需，运维预置在 secrets 文件里）；
#   * 用户级 ``X-MCP-JWT``（按人签发、会过期，由员工在会话内补全后加密存储到本人名下）。
# 应用级 key 属敏感凭据：环境变量优先，其次读 secrets 文件（不落日志）。
EHOME_MCP_URL = "DWP_EHOME_MCP_URL"
EHOME_MCP_API_KEY = "DWP_EHOME_MCP_API_KEY"
EHOME_MCP_API_KEY_FILE = "DWP_EHOME_MCP_API_KEY_FILE"
EHOME_MCP_TIMEOUT = "DWP_EHOME_MCP_TIMEOUT"
EHOME_MCP_APP_HEADER = "DWP_EHOME_MCP_APP_HEADER"
EHOME_MCP_USER_HEADER = "DWP_EHOME_MCP_USER_HEADER"

_DEFAULT_EHOME_APP_HEADER = "X-MCP-API-KEY"
_DEFAULT_EHOME_USER_HEADER = "X-MCP-JWT"


def ehome_mcp_url() -> str:
    """兴证e家员工服务 MCP Streamable HTTP 端点地址（如 http://10.32.253.43/ehome/mcp/claw）。"""
    return (os.environ.get(EHOME_MCP_URL) or "").strip()


def ehome_mcp_api_key() -> str:
    """兴证e家应用级 X-MCP-API-KEY：环境变量优先，否则读 secrets 文件（不落日志）。"""
    value = (os.environ.get(EHOME_MCP_API_KEY) or "").strip()
    if value:
        return value
    path = Path(os.environ.get(EHOME_MCP_API_KEY_FILE, "/run/secrets/ehome_mcp_api_key"))
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ehome_mcp_timeout() -> float:
    """兴证e家 MCP 单次请求超时（秒），默认 20 秒。"""
    try:
        return max(5.0, float(os.environ.get(EHOME_MCP_TIMEOUT) or "20"))
    except (TypeError, ValueError):
        return 20.0


def ehome_mcp_app_header() -> str:
    """应用级鉴权头名，默认 ``X-MCP-API-KEY``。"""
    return (os.environ.get(EHOME_MCP_APP_HEADER) or "").strip() or _DEFAULT_EHOME_APP_HEADER


def ehome_mcp_user_header() -> str:
    """用户级鉴权头名，默认 ``X-MCP-JWT``。"""
    return (os.environ.get(EHOME_MCP_USER_HEADER) or "").strip() or _DEFAULT_EHOME_USER_HEADER


def ehome_mcp_configured() -> bool:
    """兴证e家 MCP 接入是否配置齐备（URL 与应用级 key 均非空）。"""
    return bool(ehome_mcp_url() and ehome_mcp_api_key())


# ---- 内部知识引擎 VeRAG MCP（生产环境通道）----
# 与测试环境的 REST 通道并存：同一个平台知识库在测试环境走 REST、生产环境走 VeRAG。
# token 属敏感凭据：环境变量优先，其次读 secrets 文件（不落日志、不入库）。
VERAG_MCP_URL = "DWP_VERAG_MCP_URL"
VERAG_MCP_TOKEN = "DWP_VERAG_MCP_TOKEN"
VERAG_MCP_TOKEN_FILE = "DWP_VERAG_MCP_TOKEN_FILE"
VERAG_MCP_TENANT = "DWP_VERAG_MCP_TENANT"
VERAG_MCP_TIMEOUT = "DWP_VERAG_MCP_TIMEOUT"
VERAG_MCP_PROTOCOL_VERSION = "DWP_VERAG_MCP_PROTOCOL_VERSION"
VERAG_MCP_RETRIEVAL_TOOL = "DWP_VERAG_MCP_RETRIEVAL_TOOL"
VERAG_MCP_LIST_TOOL = "DWP_VERAG_MCP_LIST_TOOL"


def verag_mcp_url() -> str:
    """VeRAG MCP Streamable HTTP 端点地址。"""
    return (os.environ.get(VERAG_MCP_URL) or "").strip()


def verag_mcp_token() -> str:
    """VeRAG Bearer token：环境变量优先，否则读 secrets 文件（不落日志）。"""
    value = (os.environ.get(VERAG_MCP_TOKEN) or "").strip()
    if value:
        return value
    path = Path(os.environ.get(VERAG_MCP_TOKEN_FILE, "/run/secrets/verag_mcp_token"))
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def verag_mcp_tenant() -> str:
    """VeRAG 租户头 ``X-Tenant``，默认 6（生产租户）。"""
    return (os.environ.get(VERAG_MCP_TENANT) or "6").strip() or "6"


def verag_mcp_timeout() -> float:
    """VeRAG MCP 单次请求超时（秒），默认 20 秒。"""
    try:
        return max(5.0, min(60.0, float(os.environ.get(VERAG_MCP_TIMEOUT) or "20")))
    except (TypeError, ValueError):
        return 20.0


def verag_mcp_protocol_version() -> str:
    """VeRAG 接受的 MCP 协议版本，默认 2024-11-05。"""
    return (os.environ.get(VERAG_MCP_PROTOCOL_VERSION) or "2024-11-05").strip() or "2024-11-05"


def verag_mcp_retrieval_tool() -> str:
    """VeRAG 检索工具名，默认 ``multi_retrieval``（支持 kb_ids 数组）。"""
    return (os.environ.get(VERAG_MCP_RETRIEVAL_TOOL) or "multi_retrieval").strip() or "multi_retrieval"


def verag_mcp_list_tool() -> str:
    """VeRAG 知识库清单工具名，默认 ``list_knowledge_bases``。"""
    return (os.environ.get(VERAG_MCP_LIST_TOOL) or "list_knowledge_bases").strip() or "list_knowledge_bases"


def verag_mcp_auth_headers() -> dict[str, str]:
    """VeRAG 的双鉴权头：``Authorization: Bearer`` + ``X-Tenant``（值不落日志）。"""
    token = verag_mcp_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}", "X-Tenant": verag_mcp_tenant()}


def verag_mcp_configured() -> bool:
    """VeRAG 通道是否配置齐备（URL 与 token 均非空）。"""
    return bool(verag_mcp_url() and verag_mcp_token())


# ---- 用户级 MCP 凭据到期提醒 ----
# 按人签发的凭据（如兴证e家的 X-MCP-JWT）会过期：过期后调用才失败代价太大，
# 因此在到期前就在员工侧插件中心常驻提醒。阈值按天，默认 7 天。
MCP_CREDENTIAL_WARN_DAYS = "DWP_MCP_CREDENTIAL_WARN_DAYS"
_DEFAULT_MCP_CREDENTIAL_WARN_DAYS = 7


def mcp_credential_warn_days() -> int:
    """用户级 MCP 凭据「即将过期」的提前提醒天数，默认 7，下限 1。"""
    try:
        return max(1, int(os.environ.get(MCP_CREDENTIAL_WARN_DAYS) or _DEFAULT_MCP_CREDENTIAL_WARN_DAYS))
    except (TypeError, ValueError):
        return _DEFAULT_MCP_CREDENTIAL_WARN_DAYS


# ---- 知识源探测与选路 ----
KB_SOURCE_ENV = "DWP_KB_SOURCE_ENV"
KB_SOURCE_PROBE_ENABLED = "DWP_KB_SOURCE_PROBE_ENABLED"
KB_SOURCE_PROBE_INTERVAL_SECONDS = "DWP_KB_SOURCE_PROBE_INTERVAL_SECONDS"
KB_SOURCE_PROBE_TIMEOUT_SECONDS = "DWP_KB_SOURCE_PROBE_TIMEOUT_SECONDS"
KB_SOURCE_TTL_SECONDS = "DWP_KB_SOURCE_TTL_SECONDS"
INTERNAL_KB_LIST_PATH = "DWP_INTERNAL_KB_LIST_PATH"

# 环境偏好：auto（默认，生产优先）/ test（测试优先）/ prod（生产优先）。
_KB_SOURCE_ENV_VALUES = ("auto", "test", "prod")


def knowledge_source_env_preference() -> str:
    """环境偏好；非法值回落到 ``auto``。"""
    raw = (os.environ.get(KB_SOURCE_ENV) or "auto").strip().lower()
    return raw if raw in _KB_SOURCE_ENV_VALUES else "auto"


def knowledge_source_probe_enabled() -> bool:
    """定时探测总开关；默认关闭（离线/测试环境不主动访问内网）。"""
    return (os.environ.get(KB_SOURCE_PROBE_ENABLED) or "0").strip() == "1"


def knowledge_source_probe_interval_seconds() -> float:
    """定时探测间隔（秒），默认 300，最小 30。"""
    try:
        return max(30.0, float(os.environ.get(KB_SOURCE_PROBE_INTERVAL_SECONDS) or "300"))
    except (TypeError, ValueError):
        return 300.0


def knowledge_source_probe_timeout_seconds() -> float:
    """单次探测超时（秒），默认 15，区间 5~60。"""
    try:
        return max(5.0, min(60.0, float(os.environ.get(KB_SOURCE_PROBE_TIMEOUT_SECONDS) or "15")))
    except (TypeError, ValueError):
        return 15.0


def knowledge_source_ttl_seconds() -> float:
    """源健康/环境判定结果的进程内缓存 TTL（秒），默认 10，0 表示不缓存。"""
    try:
        return max(0.0, min(300.0, float(os.environ.get(KB_SOURCE_TTL_SECONDS) or "10")))
    except (TypeError, ValueError):
        return 10.0


def internal_kb_list_path() -> str:
    """内部知识引擎知识库列表路径（探测与目录同步共用）。"""
    raw = (os.environ.get(INTERNAL_KB_LIST_PATH) or "/marketing_agent/api/v2/rag/kb/list").strip()
    if not raw.startswith("/") or "://" in raw:
        return "/marketing_agent/api/v2/rag/kb/list"
    return raw


def internal_kb_endpoint_configured() -> bool:
    """REST 通道的地址与鉴权头是否齐备（不要求 ID_MAP：映射已改由知识库行承载）。"""
    return bool(
        internal_kb_base_url()
        and all(
            get(name)
            for name in (
                INTERNAL_KB_X_ORG,
                INTERNAL_KB_X_TENANT,
                INTERNAL_KB_X_USER,
                INTERNAL_KB_AUTHORIZATION,
            )
        )
    )


# ---- 内网 SkillHub 技能市场 ----
# 门户地址免鉴权（实测 http://10.49.70.2）；未配置地址即视为未接入，
# 管理端接口返回 503，避免离线环境反复探测一个不存在的服务。
SKILLHUB_BASE_URL = "DWP_SKILLHUB_BASE_URL"
SKILLHUB_ENABLED = "DWP_SKILLHUB_ENABLED"
SKILLHUB_TIMEOUT = "DWP_SKILLHUB_TIMEOUT"
SKILLHUB_CACHE_TTL = "DWP_SKILLHUB_CACHE_TTL"


def skillhub_base_url() -> str:
    """SkillHub 门户根地址（不含 API 前缀），未配置返回空串。"""
    return (os.environ.get(SKILLHUB_BASE_URL) or "").strip().rstrip("/")


def skillhub_enabled() -> bool:
    """SkillHub 接入开关：显式置 0 可强制关闭，否则以是否配置地址为准。"""
    if (os.environ.get(SKILLHUB_ENABLED) or "1").strip() == "0":
        return False
    return bool(skillhub_base_url())


def skillhub_timeout() -> float:
    """SkillHub 单次请求超时（秒），默认 30，区间 5~120。"""
    try:
        return max(5.0, min(120.0, float(os.environ.get(SKILLHUB_TIMEOUT) or "30")))
    except (TypeError, ValueError):
        return 30.0


def skillhub_cache_ttl() -> float:
    """SkillHub 目录/分类的进程内缓存 TTL（秒），默认 60，0 表示不缓存。"""
    try:
        return max(0.0, min(600.0, float(os.environ.get(SKILLHUB_CACHE_TTL) or "60")))
    except (TypeError, ValueError):
        return 60.0
