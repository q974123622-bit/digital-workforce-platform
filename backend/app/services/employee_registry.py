"""Harness 员工白名单的唯一来源。

历史教训：这份名单原先散落在 6 处 —— `seed.ensure_mvp_seed`、`knowledge_catalog.GRANT_SPECS`、
`routers/employees.py`、`services/group_chat.py`、`services/wecom_adapter._HARNESS_EMPLOYEE_IDS`
以及 `deploy/offline/compose.yaml`。每加一个岗位都要逐处补，漏一处就是静默故障：
员工在库里、界面看得到，但对话不走容器（或不建容器）。Python 侧统一读这里。

**改这份名单时必须同步三处声明式文件**（它们不能 import Python）：
`deploy/offline/compose.yaml`（每个 harness 服务一个 service，4 条挂载要显式列全）、
`deploy/offline/start.sh`（目录创建与自检循环）、`deploy/offline/verify.sh`（自检）。
`backend/tests/test_employee_registry.py` 会断言 Python 侧与 compose 的服务名一一对应。
"""

from __future__ import annotations

# 岗位AI员工：承担具体业务，走容器执行
ROLE_EMPLOYEE_IDS: tuple[str, ...] = (
    "AI-GENERAL",
    "AI-INVESTMENT",
    "AI-RESEARCH",
    "AI-FUND",
    "AI-XYZQ",
    "AI-AUDIT",
)

# 数字分身：真人自己的分身，同样走容器
TWIN_EMPLOYEE_IDS: tuple[str, ...] = (
    "DT-E10281",
    "DT-E20999",
)

HARNESS_EMPLOYEE_IDS = frozenset(ROLE_EMPLOYEE_IDS + TWIN_EMPLOYEE_IDS)

# 命名口径（两套，别混）：compose 服务名 harness-<id>，容器名 dwp-harness-<id>
SERVICE_PREFIX = "harness-"
CONTAINER_PREFIX = "dwp-harness-"


def is_harness_employee(employee_id: str) -> bool:
    return employee_id in HARNESS_EMPLOYEE_IDS


def harness_service_name(employee_id: str) -> str:
    return SERVICE_PREFIX + employee_id.lower()


def harness_container_name(employee_id: str) -> str:
    return CONTAINER_PREFIX + employee_id.lower()


def harness_service_names() -> list[str]:
    return [harness_service_name(item) for item in HARNESS_EMPLOYEE_IDS]


__all__ = [
    "ROLE_EMPLOYEE_IDS", "TWIN_EMPLOYEE_IDS", "HARNESS_EMPLOYEE_IDS",
    "SERVICE_PREFIX", "CONTAINER_PREFIX", "is_harness_employee",
    "harness_service_name", "harness_container_name", "harness_service_names",
]
