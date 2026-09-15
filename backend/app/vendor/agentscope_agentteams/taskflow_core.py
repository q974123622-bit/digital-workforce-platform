# SPDX-License-Identifier: Apache-2.0
"""Storage-independent subset of AgentScope AgentTeams taskflow.

Source: https://github.com/agentscope-ai/AgentTeams
Commit: e9cdf2e416ecaab129b9e85ac4a7a19dd018cb7e
Original: copaw/src/copaw_worker/task.py

The constants, data contracts, DAG validation, result-state mapping, and
accepted-dependency readiness behavior are copied or mechanically adapted from
the upstream Apache-2.0 implementation. File-system persistence is intentionally
left behind; the application supplies its existing SQLAlchemy adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class TaskflowError(ValueError):
    """Expected user-facing taskflow error."""


RESULT_STATUSES = {
    "SUCCESS",
    "SUCCESS_WITH_NOTES",
    "REVISION_NEEDED",
    "BLOCKED",
    "INTERRUPTED",
}
EFFECTIVE_RESULT_STATUSES = {"SUCCESS", "SUCCESS_WITH_NOTES"}
TERMINAL_TASK_STATUSES = {"completed", "revision", "blocked", "cancelled"}


@dataclass(frozen=True)
class DagTask:
    task_id: str
    title: str
    assigned_to: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"


@dataclass(frozen=True)
class TaskResult:
    status: str
    summary: str
    deliverables: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def is_effective_result(result: TaskResult) -> bool:
    """Return whether a task result is a candidate for Leader acceptance."""
    return result.status in EFFECTIVE_RESULT_STATUSES


def result_terminal_status(result_status: str, *, accepted: bool) -> str:
    """Apply the terminal mapping used by upstream accept_task_result()."""
    try:
        return {
            "SUCCESS": "completed" if accepted else "revision",
            "SUCCESS_WITH_NOTES": "completed" if accepted else "revision",
            "REVISION_NEEDED": "revision",
            "BLOCKED": "blocked",
            "INTERRUPTED": "blocked",
        }[result_status]
    except KeyError as exc:
        raise TaskflowError(f"invalid task result status: {result_status}") from exc


def validate_dag(tasks: list[DagTask]) -> None:
    ids = [task.task_id for task in tasks]
    if len(ids) != len(set(ids)):
        raise TaskflowError("duplicate task ids in graph")

    all_ids = set(ids)
    for task in tasks:
        missing = [dep for dep in task.depends_on if dep not in all_ids]
        if missing:
            raise TaskflowError(
                f"task {task.task_id} depends on unknown task(s): {', '.join(missing)}",
            )

    incoming = {task.task_id: len(task.depends_on) for task in tasks}
    outgoing: dict[str, list[str]] = {task.task_id: [] for task in tasks}
    for task in tasks:
        for dep in task.depends_on:
            outgoing[dep].append(task.task_id)

    queue = [task_id for task_id, count in incoming.items() if count == 0]
    visited = 0
    while queue:
        current = queue.pop(0)
        visited += 1
        for child in outgoing[current]:
            incoming[child] -= 1
            if incoming[child] == 0:
                queue.append(child)

    if visited != len(tasks):
        raise TaskflowError("cycle detected in DAG")


def ready_nodes(tasks: list[DagTask]) -> list[DagTask]:
    """Return pending DAG nodes whose dependencies have been accepted."""
    validate_dag(tasks)
    effective = {
        task.task_id
        for task in tasks
        if task.status == "completed"
    }
    return [
        task
        for task in tasks
        if task.status == "pending"
        and all(dependency in effective for dependency in task.depends_on)
    ]
