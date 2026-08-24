"""子任务执行器接口（Sprint 7 C 档）。

当前默认 GatewaySubtaskExecutor：Identity → Policy → Gateway →
Capability Executor（员工 Harness 驱动 → Adapter 工具调用）→ 审计。
"""

from typing import Protocol

from sqlalchemy.orm import Session

from .. import models
from .gateway import invoke_plugin
from .runtime_adapter import RuntimeAdapter


class SubtaskExecutor(Protocol):
    """执行单个子任务；返回 {"decision": allow|approval|deny, "data": ...}。"""

    def execute(
        self,
        db: Session,
        *,
        run: models.TaskRun,
        subtask: dict,
        trace_id: str,
        approval_granted: bool = False,
    ) -> dict: ...


class GatewaySubtaskExecutor:
    """经统一网关执行：Policy 决策 + Capability Executor + 审计。"""

    def __init__(self, runtime: RuntimeAdapter | None = None):
        self.runtime = runtime

    def execute(
        self,
        db: Session,
        *,
        run: models.TaskRun,
        subtask: dict,
        trace_id: str,
        approval_granted: bool = False,
    ) -> dict:
        plugin_id = subtask["plugin_ids"][0] if subtask.get("plugin_ids") else ""
        return invoke_plugin(
            db,
            employee_id=subtask["worker_id"],
            plugin_id=plugin_id,
            action="execute",
            params=subtask.get("params") or {},
            trace_id=trace_id,
            approval_granted=approval_granted,
            runtime=self.runtime,
            execution_context={
                "task_id": run.id,
                "request": run.request,
                "subtask": subtask.get("summary", ""),
                "collaboration_summary": (
                    subtask.get("collaboration_summary")
                    or "\n".join(subtask.get("collaboration_messages") or [])
                ),
            },
        )
