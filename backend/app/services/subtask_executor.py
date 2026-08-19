"""子任务执行器接口（Sprint 7 C 档）。

执行留接口：真实 RPA / Workflow / Harness 后续实现同一接口接入；
当前默认 GatewaySubtaskExecutor（Identity → Policy → Gateway → Adapter → 审计）。
"""

from typing import Protocol

from sqlalchemy.orm import Session

from .. import models
from .gateway import invoke_plugin


class SubtaskExecutor(Protocol):
    """执行单个子任务；返回 {"decision": allow|approval|deny, "data": ...}。"""

    def execute(
        self,
        db: Session,
        *,
        run: models.TaskRun,
        subtask: dict,
        trace_id: str,
    ) -> dict: ...


class GatewaySubtaskExecutor:
    """经统一网关执行（演示默认）：Policy 决策 + Mock Adapter + 审计。

    后续真实执行（RPA / Workflow / Harness）实现 SubtaskExecutor 接口后，
    在 TeamTaskOrchestrator 构造时替换 executor 即可。
    """

    def execute(
        self,
        db: Session,
        *,
        run: models.TaskRun,
        subtask: dict,
        trace_id: str,
    ) -> dict:
        plugin_id = subtask["plugin_ids"][0] if subtask.get("plugin_ids") else ""
        return invoke_plugin(
            db,
            employee_id=subtask["worker_id"],
            plugin_id=plugin_id,
            action="execute",
            params=subtask.get("params") or {},
            trace_id=trace_id,
        )
