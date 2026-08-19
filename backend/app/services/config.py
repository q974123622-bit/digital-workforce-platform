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
TEAM_BACKEND = "DWP_TEAM_BACKEND"  # auto | builtin（默认 auto）


def team_backend_mode() -> str:
    """团队协作后端：auto（AgentTeams 优先，失败降级内置）或 builtin。"""
    return os.environ.get(TEAM_BACKEND, "auto")


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
