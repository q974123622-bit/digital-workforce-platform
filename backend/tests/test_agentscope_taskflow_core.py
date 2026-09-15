from __future__ import annotations

import pytest

from app.vendor.agentscope_agentteams.taskflow_core import (
    DagTask,
    TaskResult,
    TaskflowError,
    is_effective_result,
    ready_nodes,
    result_terminal_status,
    validate_dag,
)


@pytest.mark.parametrize(
    ("status", "accepted", "expected"),
    [
        ("SUCCESS", True, "completed"),
        ("SUCCESS", False, "revision"),
        ("SUCCESS_WITH_NOTES", True, "completed"),
        ("SUCCESS_WITH_NOTES", False, "revision"),
        ("REVISION_NEEDED", True, "revision"),
        ("BLOCKED", True, "blocked"),
        ("INTERRUPTED", True, "blocked"),
    ],
)
def test_result_terminal_status_matches_upstream_result_gate(
    status: str, accepted: bool, expected: str
) -> None:
    assert result_terminal_status(status, accepted=accepted) == expected


def test_effective_result_is_only_success_or_success_with_notes() -> None:
    assert is_effective_result(TaskResult(status="SUCCESS", summary="done")) is True
    assert (
        is_effective_result(
            TaskResult(status="SUCCESS_WITH_NOTES", summary="done with notes")
        )
        is True
    )
    assert (
        is_effective_result(TaskResult(status="REVISION_NEEDED", summary="repair"))
        is False
    )
    assert is_effective_result(TaskResult(status="BLOCKED", summary="blocked")) is False
    assert (
        is_effective_result(TaskResult(status="INTERRUPTED", summary="runtime stopped"))
        is False
    )


def test_ready_nodes_require_completed_dependencies() -> None:
    tasks = [
        DagTask("collect", "Collect", "worker-a", status="completed"),
        DagTask("verify", "Verify", "worker-b", depends_on=["collect"]),
        DagTask("publish", "Publish", "worker-c", depends_on=["verify"]),
        DagTask("blocked", "Blocked", "worker-d", status="blocked"),
    ]

    assert [task.task_id for task in ready_nodes(tasks)] == ["verify"]


def test_validate_dag_rejects_unknown_dependency_without_mutating_input() -> None:
    tasks = [DagTask("verify", "Verify", "worker-b", depends_on=["missing"])]

    with pytest.raises(TaskflowError, match="depends on unknown task"):
        validate_dag(tasks)

    assert tasks[0].depends_on == ["missing"]


def test_validate_dag_rejects_cycle() -> None:
    tasks = [
        DagTask("a", "A", "worker-a", depends_on=["b"]),
        DagTask("b", "B", "worker-b", depends_on=["a"]),
    ]

    with pytest.raises(TaskflowError, match="cycle detected in DAG"):
        validate_dag(tasks)


def test_validate_dag_rejects_duplicate_task_ids() -> None:
    tasks = [
        DagTask("a", "A", "worker-a"),
        DagTask("a", "Again", "worker-b"),
    ]

    with pytest.raises(TaskflowError, match="duplicate task ids in graph"):
        validate_dag(tasks)
