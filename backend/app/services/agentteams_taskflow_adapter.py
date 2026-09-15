"""SQLAlchemy adapter for the source-derived AgentScope AgentTeams taskflow core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..vendor.agentscope_agentteams.taskflow_core import DagTask, ready_nodes, validate_dag


@dataclass(frozen=True)
class ReplacementTask:
    client_task_key: str
    title: str
    instruction: str
    assignee_agent_id: str
    required_capability: str
    depends_on: tuple[str, ...]
    required: bool
    material: dict[str, Any]


@dataclass(frozen=True)
class AppliedReplan:
    nodes: tuple[models.AgentTaskNode, ...]
    ready_nodes: tuple[models.AgentTaskNode, ...]
    leaf_nodes: tuple[models.AgentTaskNode, ...]


class DatabaseTaskflowAdapter:
    """Map the upstream complete-DAG semantics onto durable task rows."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def validate_replan(tasks: Iterable[ReplacementTask]) -> tuple[ReplacementTask, ...]:
        planned = tuple(tasks)
        validate_dag([
            DagTask(
                task_id=item.client_task_key,
                title=item.title,
                assigned_to=item.assignee_agent_id,
                depends_on=list(item.depends_on),
            )
            for item in planned
        ])
        return planned

    def current_dag(self, parent_id: str) -> list[DagTask]:
        children = list(self.db.scalars(
            select(models.AgentTaskNode)
            .where(models.AgentTaskNode.parent_task_id == parent_id)
            .order_by(models.AgentTaskNode.created_at, models.AgentTaskNode.id)
        ))
        superseded_ids = {
            child.revision_of_task_id
            for child in children
            if child.revision_of_task_id is not None
        }
        active = [child for child in children if child.id not in superseded_ids]
        keys_by_id = {child.id: child.client_task_key for child in active}
        result: list[DagTask] = []
        for child in active:
            dependencies = list(self.db.scalars(
                select(models.AgentTaskDependency.depends_on_task_id).where(
                    models.AgentTaskDependency.task_id == child.id
                )
            ))
            result.append(DagTask(
                task_id=child.client_task_key,
                title=child.title,
                assigned_to=child.assignee_agent_id,
                depends_on=[
                    keys_by_id[dependency_id]
                    for dependency_id in dependencies
                    if dependency_id in keys_by_id
                ],
                status=child.status,
            ))
        return result

    def apply_replan(
        self,
        replaced: models.AgentTaskNode,
        *,
        feedback: str,
        tasks: Iterable[ReplacementTask],
    ) -> AppliedReplan:
        planned = self.validate_replan(tasks)
        ready_keys = {
            item.task_id
            for item in ready_nodes([
                DagTask(
                    task_id=task.client_task_key,
                    title=task.title,
                    assigned_to=task.assignee_agent_id,
                    depends_on=list(task.depends_on),
                )
                for task in planned
            ])
        }
        nodes = tuple(
            models.AgentTaskNode(
                id=f"ATN-{uuid4().hex}",
                root_task_id=replaced.root_task_id,
                parent_task_id=replaced.parent_task_id,
                client_task_key=task.client_task_key,
                trace_id=replaced.trace_id,
                title=task.title,
                instruction=task.instruction,
                coordinator_agent_id=replaced.coordinator_agent_id,
                assignee_agent_id=task.assignee_agent_id,
                capability=task.required_capability,
                node_kind="worker_task",
                revision_of_task_id=replaced.id,
                depth=replaced.depth,
                required=task.required,
                status="assigned" if task.client_task_key in ready_keys else "blocked",
                progress=0,
                input_payload={
                    **task.material,
                    "revision_of_task_id": replaced.id,
                    "review_feedback": feedback,
                },
                output_payload={},
            )
            for task in planned
        )
        self.db.add_all(nodes)
        by_key = {node.client_task_key: node for node in nodes}
        for task in planned:
            for dependency_key in task.depends_on:
                self.db.add(models.AgentTaskDependency(
                    root_task_id=replaced.root_task_id,
                    task_id=by_key[task.client_task_key].id,
                    depends_on_task_id=by_key[dependency_key].id,
                ))
        self.db.flush()

        dependency_sources = {
            dependency_key
            for task in planned
            for dependency_key in task.depends_on
        }
        leaf_nodes = tuple(
            by_key[task.client_task_key]
            for task in planned
            if task.client_task_key not in dependency_sources
        )
        downstream = list(self.db.scalars(
            select(models.AgentTaskDependency).where(
                models.AgentTaskDependency.depends_on_task_id == replaced.id
            )
        ))
        for dependency in downstream:
            dependency.depends_on_task_id = leaf_nodes[0].id
            for leaf in leaf_nodes[1:]:
                self.db.add(models.AgentTaskDependency(
                    root_task_id=replaced.root_task_id,
                    task_id=dependency.task_id,
                    depends_on_task_id=leaf.id,
                ))

        return AppliedReplan(
            nodes=nodes,
            ready_nodes=tuple(node for node in nodes if node.status == "assigned"),
            leaf_nodes=leaf_nodes,
        )
