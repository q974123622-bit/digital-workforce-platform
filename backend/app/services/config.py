"""Secret / Config 安全访问（Sprint 3）。

铁律：内部 endpoint、Token、Credential 只能通过环境变量或 secure config 引用；
禁止写入 Git、写入 Prompt、写入日志。本模块只返回引用名/环境变量值，
任何真实凭据值不得入库、不得入日志。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载 backend/.env（gitignored，仅本地受控环境；真实值绝不提交）
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

# 预留的环境变量名（真实值只存在于正式员工受控环境，仓库内不得出现）
INTERNAL_KB_ENDPOINT = "DWP_INTERNAL_KB_ENDPOINT"
INTERNAL_KB_CREDENTIAL_REF = "DWP_INTERNAL_KB_CREDENTIAL_REF"
ADP_ENDPOINT = "DWP_ADP_ENDPOINT"
ADP_CREDENTIAL_REF = "DWP_ADP_CREDENTIAL_REF"
RPA_ENDPOINT = "DWP_RPA_ENDPOINT"
RPA_CREDENTIAL_REF = "DWP_RPA_CREDENTIAL_REF"

# 知识库检索模式（mock | rag | internal，默认 mock）与嵌入服务配置
KB_MODE = "DWP_KB_MODE"
EMBED_BASE_URL = "DWP_EMBED_BASE_URL"
EMBED_API_KEY = "DWP_EMBED_API_KEY"
EMBED_MODEL = "DWP_EMBED_MODEL"
EMBED_DIMENSIONS = "DWP_EMBED_DIMENSIONS"

# AgentTeams 接入（Matrix 通道）
AGENTTEAMS_MATRIX_URL = "AGENTTEAMS_MATRIX_URL"
AGENTTEAMS_ADMIN_USER = "AGENTTEAMS_ADMIN_USER"
AGENTTEAMS_ADMIN_PASSWORD = "AGENTTEAMS_ADMIN_PASSWORD"
AGENTTEAMS_MATRIX_TOKEN = "AGENTTEAMS_MATRIX_TOKEN"
AGENTTEAMS_ROOM_ID = "AGENTTEAMS_ROOM_ID"
AGENTTEAMS_MANAGER_MXID = "AGENTTEAMS_MANAGER_MXID"
AGENTTEAMS_BOT_USER = "AGENTTEAMS_BOT_USER"
AGENTTEAMS_MATRIX_DOMAIN = "AGENTTEAMS_MATRIX_DOMAIN"
AGENTTEAMS_CONTROLLER_CONTAINER = "AGENTTEAMS_CONTROLLER_CONTAINER"
AGENTTEAMS_TEAM_NAME = "AGENTTEAMS_TEAM_NAME"
AGENTTEAMS_COLLAB_TIMEOUT = "AGENTTEAMS_COLLAB_TIMEOUT"
AGENTTEAMS_MANAGER_CONTAINER = "AGENTTEAMS_MANAGER_CONTAINER"
AGENTTEAMS_MANAGER_DM_ROOM_ID = "AGENTTEAMS_MANAGER_DM_ROOM_ID"
AGENTTEAMS_MINIO_ACCESS_KEY = "AGENTTEAMS_MINIO_ACCESS_KEY"
AGENTTEAMS_MINIO_SECRET_KEY = "AGENTTEAMS_MINIO_SECRET_KEY"
AGENTTEAMS_WORKER_MODEL = "AGENTTEAMS_WORKER_MODEL"
AGENTTEAMS_WORKER_RUNTIME = "AGENTTEAMS_WORKER_RUNTIME"
TEAM_BACKEND = "DWP_TEAM_BACKEND"  # auto | builtin（默认 auto）

# 本地记忆（工作线 B 运行配置；检索算法与表结构由工作线 A 负责）
MEMORY_ENABLED = "DWP_MEMORY_ENABLED"
MEMORY_MAX_ITEMS = "DWP_MEMORY_MAX_ITEMS"
MEMORY_MAX_CHARS = "DWP_MEMORY_MAX_CHARS"


def team_backend_mode() -> str:
    """团队协作后端：auto（AgentTeams 优先，失败降级内置）或 builtin。"""
    return os.environ.get(TEAM_BACKEND, "auto")


def agentteams_manager_mxid() -> str:
    """AgentTeams Manager 的 Matrix 全 ID；群聊任务必须 @mention 才会被处理。"""
    return os.environ.get(
        AGENTTEAMS_MANAGER_MXID, "@manager:matrix-local.agentteams.io:18080"
    )


def agentteams_bot_mxid() -> str:
    """平台机器人在 Matrix 的 MXID（用于排除平台自身消息，不当回执）。"""
    bot = os.environ.get(AGENTTEAMS_BOT_USER, "platform-bot")
    return f"@{bot}:{agentteams_matrix_domain()}"


def agentteams_matrix_domain() -> str:
    return os.environ.get(AGENTTEAMS_MATRIX_DOMAIN, "matrix-local.agentteams.io:18080")


def agentteams_controller_container() -> str:
    return os.environ.get(AGENTTEAMS_CONTROLLER_CONTAINER, "agentteams-controller")


def agentteams_team_name() -> str:
    return os.environ.get(AGENTTEAMS_TEAM_NAME, "team-onboard")


def agentteams_collaboration_timeout() -> int:
    """AgentTeams 只负责协作讨论的等待窗口；到期后由 Harness 按既定计划继续执行。"""
    try:
        return max(5, min(int(os.environ.get(AGENTTEAMS_COLLAB_TIMEOUT) or "30"), 120))
    except ValueError:
        return 30


def agentteams_worker_model() -> str:
    """AgentTeams 协作 worker 使用的模型；避免将供应商模型名硬编码进生命周期逻辑。"""
    return os.environ.get(AGENTTEAMS_WORKER_MODEL, "deepseek-chat")


def agentteams_worker_runtime() -> str:
    return os.environ.get(AGENTTEAMS_WORKER_RUNTIME, "copaw")


def get(name: str, default: str | None = None) -> str | None:
    """读取环境变量；未设置返回 default。值不落日志。"""
    return os.environ.get(name, default)


def credential_ref(name: str) -> str | None:
    """返回凭据引用名（环境变量名或 secure config key），不返回凭据本身。"""
    return os.environ.get(name)


def kb_mode() -> str:
    """知识库检索模式：mock | rag | internal；未配置默认 mock。"""
    return (os.environ.get(KB_MODE) or "mock").strip().lower()


def embedding_api_key() -> str | None:
    """嵌入服务 Key：DWP_EMBED_API_KEY，兼容读取 DASHSCOPE_API_KEY。"""
    return os.environ.get(EMBED_API_KEY) or os.environ.get("DASHSCOPE_API_KEY")


def embedding_dimensions() -> int:
    """嵌入维度，默认 1024。"""
    try:
        return int(os.environ.get(EMBED_DIMENSIONS) or "1024")
    except (TypeError, ValueError):
        return 1024


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    """读取整数环境变量；非法值或超出 [low, high] 一律回退默认值。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if not (low <= value <= high):
        return default
    return value


def memory_enabled() -> bool:
    """本地记忆自动检索/写入总开关；仅显式 0/false/no/off 视为关闭，默认开启。"""
    raw = os.environ.get(MEMORY_ENABLED)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def memory_max_items() -> int:
    """记忆注入条数上限：1～10，非法值回退默认 3。"""
    return _bounded_int(MEMORY_MAX_ITEMS, default=3, low=1, high=10)


def memory_max_chars() -> int:
    """记忆上下文字符预算：200～4000，非法值回退默认 1200。"""
    return _bounded_int(MEMORY_MAX_CHARS, default=1200, low=200, high=4000)
