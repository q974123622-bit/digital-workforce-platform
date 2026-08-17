"""Secret / Config 安全访问（Sprint 3）。

铁律：内部 endpoint、Token、Credential 只能通过环境变量或 secure config 引用；
禁止写入 Git、写入 Prompt、写入日志。本模块只返回引用名/环境变量值，
任何真实凭据值不得入库、不得入日志。
"""

import os

# 预留的环境变量名（真实值只存在于正式员工受控环境，仓库内不得出现）
INTERNAL_KB_ENDPOINT = "DWP_INTERNAL_KB_ENDPOINT"
INTERNAL_KB_CREDENTIAL_REF = "DWP_INTERNAL_KB_CREDENTIAL_REF"
ADP_ENDPOINT = "DWP_ADP_ENDPOINT"
ADP_CREDENTIAL_REF = "DWP_ADP_CREDENTIAL_REF"
RPA_ENDPOINT = "DWP_RPA_ENDPOINT"
RPA_CREDENTIAL_REF = "DWP_RPA_CREDENTIAL_REF"


def get(name: str, default: str | None = None) -> str | None:
    """读取环境变量；未设置返回 default。值不落日志。"""
    return os.environ.get(name, default)


def credential_ref(name: str) -> str | None:
    """返回凭据引用名（环境变量名或 secure config key），不返回凭据本身。"""
    return os.environ.get(name)
