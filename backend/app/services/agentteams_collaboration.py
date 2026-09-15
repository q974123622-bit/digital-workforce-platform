"""Persistent, local orchestration for recursive AgentTeams task collaboration.

The service deliberately has no Matrix client dependency.  It validates protocol
senders against the stored runtime reference and records outbound work in the
outbox for a later runner to deliver.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any, Iterable, Literal, Mapping
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from . import config
from .agentteams_hierarchy import TeamHierarchy, TeamHierarchyError
from .agentteams_protocol import AgentProtocolMessage
from .agentteams_runtime_config import load_strict_runtime_config
from .agentteams_taskflow_adapter import DatabaseTaskflowAdapter, ReplacementTask
from ..vendor.agentscope_agentteams.taskflow_core import TaskflowError
from .gateway import invoke_plugin
from .identity import resolve_identity, resolve_twin_identity
from .knowledge_registry import plugin_id_for_level, resolve as resolve_knowledge_base
from .policy import DECISION_DENY, ResourceRef, evaluate


class CollaborationConflict(RuntimeError):
    """A protocol request violates collaboration ownership or state rules."""


def matrix_user_id_for_runtime(runtime_ref: str | None) -> str | None:
    """Resolve a stored runtime reference to its exact, authorization-safe MXID."""
    if runtime_ref is None:
        return None
    reference = runtime_ref.strip()
    if not reference:
        return None
    if reference.startswith("@"):
        localpart, separator, domain = reference[1:].partition(":")
        if not separator or not localpart or not domain or any(char.isspace() for char in reference):
            return None
        return reference
    domain = config.agentteams_matrix_domain().strip()
    if (
        not domain
        or any(char.isspace() for char in reference)
        or any(char.isspace() for char in domain)
        or any(char in reference for char in "@:/")
        or "@" in domain
    ):
        return None
    return f"@{reference}:{domain}"


ALLOWED: dict[str, set[str]] = {
    "planning": {"waiting_children", "failed", "timed_out", "cancelled"},
    "blocked": {"assigned", "failed", "timed_out", "cancelled"},
    "assigned": {"accepted", "failed", "timed_out", "cancelled"},
    "accepted": {"running", "failed", "timed_out", "cancelled"},
    "running": {"waiting_children", "submitted", "failed", "timed_out", "cancelled"},
    "submitted": {"reviewing", "failed", "timed_out", "cancelled"},
    "reviewing": {"completed", "revision_requested", "failed", "timed_out", "cancelled"},
    "waiting_children": {"summarizing", "failed", "timed_out", "cancelled"},
    "summarizing": {"waiting_children", "submitted", "completed", "failed", "timed_out", "cancelled"},
}
_INBOUND_IDEMPOTENCY_PREFIX = "in:"
_MAX_TOOL_RESULT_DEPTH = 4
_MAX_TOOL_RESULT_ITEMS = 20
_MAX_TOOL_RESULT_STRING_LENGTH = 512
_MAX_TOOL_RESULT_BYTES = 4096
_MAX_SAFE_NODE_RESULT_BYTES = 1024
_MAX_SAFE_RESULT_COLLECTION_BYTES = 4096
_MAX_SUMMARY_AUDIT_IDS = 16
_MAX_SUBTASK_TITLE_LENGTH = 256
_MAX_SUBTASK_INSTRUCTION_LENGTH = 512
_MAX_SUBTASK_CAPABILITY_LENGTH = 128
_MAX_MATERIAL_DEPTH = 5
_MAX_MATERIAL_ITEMS = 32
_MAX_MATERIAL_STRING_LENGTH = 1024
_MAX_MATERIAL_KEY_LENGTH = 128
_MAX_MATERIAL_CANONICAL_BYTES = 4096
_MAX_MATERIAL_INTEGER_BITS = 8192
# A compact canonical JSON document has at least one byte per visited value;
# keep the shape guard no stricter than the 4 KiB payload contract.
_MAX_MATERIAL_VISITED_NODES = _MAX_MATERIAL_CANONICAL_BYTES
_SAFE_CLIENT_TASK_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_SECRET_FIELD_PATTERN = re.compile(
    r"(?:password|passwd|pwd|token|secret|key|authorization|credential|cookie|header|endpoint|url|uri|"
    r"stack|traceback|exception)",
    re.IGNORECASE,
)
_URI_PATTERN = re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,31}://[^\s\"'<>]+")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:password|passwd|pwd|token|secret|api[_-]?key|private[_-]?key|access[_-]?key|"
    r"authorization|credential|cookie|header|endpoint)\s*[:=]\s*[^,\s]+"
)
_AUTH_VALUE_PATTERN = re.compile(
    r"(?i)\b(?:bearer|basic|token|api[_-]?key|x-api-key)\s+[A-Za-z0-9._~+/-]+=*"
)
_STACK_TRACE_PATTERN = re.compile(
    r"(?i)(?:traceback\s*\(|stack trace|exception\s*(?::|in thread)|(?:^|\n)\s*at\s+[\w.$<>]+\(.*:\d+\))"
)


@dataclass
class _MaterialBudget:
    visited_nodes: int = 0
    estimated_bytes: int = 0

    def consume_node(self) -> None:
        self.visited_nodes += 1
        if self.visited_nodes > _MAX_MATERIAL_VISITED_NODES:
            raise CollaborationConflict("material_too_large")

    def consume_bytes(self, amount: int) -> None:
        self.estimated_bytes += amount
        if self.estimated_bytes > _MAX_MATERIAL_CANONICAL_BYTES:
            raise CollaborationConflict("material_too_large")


class AgentTeamsCollaborationService:
    def __init__(
        self,
        max_depth: int = 3,
        max_children: int = 5,
        max_rounds: int | None = None,
        max_nodes: int | None = None,
        capability_catalog_source: Literal["configured", "database"] = "configured",
    ):
        if capability_catalog_source not in {"configured", "database"}:
            raise ValueError("invalid_capability_catalog_source")
        self.max_depth = max_depth
        self.max_children = max_children
        self.max_rounds = 5 if max_rounds is None else max_rounds
        self.max_nodes = 20 if max_nodes is None else max_nodes
        self.capability_catalog_source = capability_catalog_source

    def create_task(
        self,
        db: Session,
        *,
        team_id: str,
        leader_agent_id: str,
        title: str,
        instruction: str,
        material: dict,
        owner_human_no: str = "",
        thinking_enabled: bool = True,
        team_selection: Mapping[str, str] | None = None,
    ) -> models.TaskRun:
        team = db.get(models.Team, team_id)
        if team is None:
            raise CollaborationConflict("unknown_team")
        leader = db.get(models.DigitalEmployee, leader_agent_id)
        if leader is None:
            raise CollaborationConflict("unknown_leader")
        if not self._is_team_member(db, team, leader_agent_id):
            raise CollaborationConflict("invalid_team_leader")
        hierarchy = self._team_hierarchy(db, team.id)
        if leader_agent_id != hierarchy.manager_id:
            raise CollaborationConflict("invalid_team_leader")
        if not self._runtime_ref(leader):
            raise CollaborationConflict("missing_runtime_ref")
        if not isinstance(material, dict):
            raise CollaborationConflict("invalid_material")
        if not isinstance(thinking_enabled, bool):
            raise CollaborationConflict("invalid_thinking_mode")

        task_id = f"AT-{uuid4().hex}"
        run = models.TaskRun(
            id=task_id,
            team_id=team.id,
            owner_human_no=str(owner_human_no or "").strip(),
            trace_id=task_id,
            request=instruction,
            status="planning",
            subtasks=[],
            summary="",
            source="agentteams",
        )
        root = models.AgentTaskNode(
            id=task_id,
            root_task_id=task_id,
            parent_task_id=None,
            client_task_key="root",
            trace_id=task_id,
            title=title,
            instruction=instruction,
            coordinator_agent_id=leader.employee_no,
            assignee_agent_id=leader.employee_no,
            capability="coordinate",
            node_kind="mission",
            revision_of_task_id=None,
            depth=0,
            required=True,
            status="planning",
            progress=0,
            input_payload={
                **dict(material),
                "_agentteams_runtime": {"thinking_enabled": thinking_enabled},
                **({"_agentteams_team_selection": dict(team_selection)} if team_selection else {}),
            },
            output_payload={},
        )
        db.add_all([run, root])
        if team_selection:
            self._append_event(
                db,
                root_task_id=task_id,
                task_id=task_id,
                event_type="team.selected",
                actor_agent_id=leader.employee_no,
                target_agent_id=leader.employee_no,
                summary="Manager 已选择 Team Leader",
                detail=dict(team_selection),
            )
        self._append_event(
            db,
            root_task_id=task_id,
            task_id=task_id,
            event_type="task.created",
            actor_agent_id=leader.employee_no,
            target_agent_id=leader.employee_no,
            summary="task created",
            detail={"title": title},
        )
        catalog, catalog_version = self._next_catalog_snapshot(db, root, team.id)
        submit_payload = {
            "title": title,
            "instruction": instruction,
            "material": dict(material),
            "thinking_enabled": thinking_enabled,
            "team_capability_catalog": catalog,
            "team_catalog_version": catalog_version,
        }
        relationship_graph = self._team_relationship_graph(
            db, team.id, leader.employee_no, catalog=catalog
        )
        if relationship_graph:
            submit_payload["team_relationship_graph"] = relationship_graph
        self._queue_outbound(
            db,
            root_task_id=task_id,
            task_id=task_id,
            parent_task_id=None,
            message_type="task.submit",
            sender_agent_id="platform",
            receiver_agent_id=leader.employee_no,
            body=instruction,
            payload=submit_payload,
            idempotency_key=f"{task_id}:task.submit",
        )
        # Do not commit: callers own the transaction and their test rollback policy.
        db.flush()
        return run

    def apply_message(
        self,
        db: Session,
        *,
        matrix_sender: str,
        message: AgentProtocolMessage,
    ) -> list[models.AgentTaskEvent]:
        run, node = self._locked_context(db, message)
        if message.type in {"task.delegate", "task.replan"}:
            self._prevalidate_delegate_subtasks(message.payload)
        if not self._reserve_protocol(db, message):
            return []

        if message.type == "task.delegate":
            self._authorize(db, run, node, matrix_sender, message, roles={"assignee"})
            events = self._apply_delegate(db, run, node, message)
        elif message.type == "task.accepted":
            self._authorize(db, run, node, matrix_sender, message, roles={"assignee"})
            events = self._apply_accepted(db, node, message)
        elif message.type == "task.progress":
            self._authorize(db, run, node, matrix_sender, message, roles={"assignee"})
            events = self._apply_progress(db, node, message)
        elif message.type == "task.result":
            self._authorize(db, run, node, matrix_sender, message, roles={"assignee"})
            events = self._apply_result(db, node, message)
        elif message.type == "task.result.accepted":
            self._authorize(db, run, node, matrix_sender, message, roles={"coordinator"})
            events = self._apply_result_accepted(db, node, message)
        elif message.type == "task.result.rejected":
            self._authorize(db, run, node, matrix_sender, message, roles={"coordinator"})
            events = self._apply_result_rejected(db, node, message)
        elif message.type == "task.replan":
            self._authorize(db, run, node, matrix_sender, message, roles={"coordinator"})
            events = self._apply_replan(db, run, node, message)
        elif message.type == "task.blocked":
            self._authorize(db, run, node, matrix_sender, message, roles={"coordinator"})
            events = self._apply_blocked(db, node, message)
        elif message.type == "task.failed":
            self._authorize(db, run, node, matrix_sender, message, roles={"assignee"})
            events = self._apply_failed(db, run, node, message)
        elif message.type == "task.summary":
            self._authorize(db, run, node, matrix_sender, message, roles={"coordinator"})
            events = self._apply_summary(db, run, node, message)
        elif message.type == "tool.request":
            if message.receiver != "platform":
                raise CollaborationConflict("invalid_tool_receiver")
            self._authorize(db, run, node, matrix_sender, message, roles={"assignee"})
            events = self._apply_tool_request(db, run, node, message)
        elif message.type == "agent.message":
            self._authorize(db, run, node, matrix_sender, message, roles={"assignee", "coordinator"})
            events = [
                self._append_event(
                    db,
                    root_task_id=node.root_task_id,
                    task_id=node.id,
                    event_type="agent.message",
                    actor_agent_id=message.sender,
                    target_agent_id=message.receiver,
                    summary=self._message_text(message.payload),
                    detail=dict(message.payload),
                )
            ]
        else:
            raise CollaborationConflict("unsupported_message_type")

        db.flush()
        return events

    def build_graph(self, db: Session, root_task_id: str) -> dict:
        run, root = self._root(db, root_task_id)
        coordination = self._coordination(root)
        nodes = list(
            db.scalars(
                select(models.AgentTaskNode)
                .where(models.AgentTaskNode.root_task_id == root_task_id)
                .order_by(models.AgentTaskNode.depth, models.AgentTaskNode.created_at, models.AgentTaskNode.id)
            )
        )
        dependencies = list(
            db.scalars(
                select(models.AgentTaskDependency)
                .where(models.AgentTaskDependency.root_task_id == root_task_id)
                .order_by(models.AgentTaskDependency.task_id, models.AgentTaskDependency.depends_on_task_id)
            )
        )
        messages = list(
            db.scalars(
                select(models.AgentMessage)
                .where(models.AgentMessage.root_task_id == root_task_id)
                .order_by(models.AgentMessage.created_at, models.AgentMessage.id)
            )
        )
        message_edges = [
            {
                "id": item.id,
                "task_id": item.task_id,
                "parent_task_id": item.parent_task_id,
                "sender_agent_id": item.sender_agent_id,
                "receiver_agent_id": item.receiver_agent_id,
                "message_type": item.message_type,
                "direction": item.direction,
                "delivery_status": item.delivery_status,
            }
            for item in messages
        ]
        return {
            "root_task_id": run.id,
            "root_status": root.status,
            "current_round": coordination["round"],
            "selected_skill_ids": coordination["selected_skill_ids"],
            "nodes": [self._node_data(node) for node in nodes],
            "parent_edges": [
                {"parent_task_id": node.parent_task_id, "task_id": node.id}
                for node in nodes
                if node.parent_task_id is not None
            ],
            "dependency_edges": [
                {"task_id": dependency.task_id, "depends_on_task_id": dependency.depends_on_task_id}
                for dependency in dependencies
            ],
            "message_edges": message_edges,
            "communication_edges": message_edges,
        }

    def build_result(self, db: Session, root_task_id: str) -> dict:
        run, root = self._root(db, root_task_id)
        nodes = list(
            db.scalars(
                select(models.AgentTaskNode)
                .where(models.AgentTaskNode.root_task_id == root_task_id)
                .order_by(models.AgentTaskNode.depth, models.AgentTaskNode.created_at, models.AgentTaskNode.id)
            )
        )
        return {
            "root_task_id": run.id,
            "status": root.status,
            "summary": run.summary,
            "node_results": [
                {
                    "task_id": node.id,
                    "parent_task_id": node.parent_task_id,
                    "client_task_key": node.client_task_key,
                    "status": node.status,
                    "required": node.required,
                    "progress": node.progress,
                    "result": dict(node.output_payload or {}),
                    "error_code": node.error_code,
                    "error_message": node.error_message,
                }
                for node in nodes
            ],
        }

    def _apply_delegate(
        self,
        db: Session,
        run: models.TaskRun,
        parent: models.AgentTaskNode,
        message: AgentProtocolMessage,
    ) -> list[models.AgentTaskEvent]:
        # Validation is intentionally complete before any child, dependency, event,
        # outbox, or parent-status row is added to the session.
        if parent.depth >= self.max_depth:
            raise CollaborationConflict("max_depth_exceeded")
        raw_subtasks = message.payload.get("subtasks")
        if not isinstance(raw_subtasks, list) or not 1 <= len(raw_subtasks) <= self.max_children:
            raise CollaborationConflict("invalid_subtask_count")
        if parent.status not in ALLOWED or "waiting_children" not in ALLOWED[parent.status]:
            raise CollaborationConflict("invalid_transition")

        subtasks = [self._validate_subtask(item) for item in raw_subtasks]
        if not any(item["required"] for item in subtasks):
            raise CollaborationConflict("required_subtask_required")
        keys = [item["client_task_key"] for item in subtasks]
        if len(keys) != len(set(keys)):
            raise CollaborationConflict("duplicate_client_task_key")
        if any(dep not in keys for item in subtasks for dep in item["depends_on"]):
            raise CollaborationConflict("unknown_dependency")
        if any(len(item["depends_on"]) != len(set(item["depends_on"])) for item in subtasks):
            raise CollaborationConflict("duplicate_dependency")
        self._assert_acyclic(subtasks)
        existing_keys = set(
            db.scalars(
                select(models.AgentTaskNode.client_task_key).where(
                    models.AgentTaskNode.parent_task_id == parent.id
                )
            )
        )
        if existing_keys.intersection(keys):
            raise CollaborationConflict("duplicate_client_task_key")
        if self._has_semantic_duplicate(db, parent, subtasks):
            raise CollaborationConflict("duplicate_semantic_subtask")
        total_nodes = db.scalar(
            select(func.count(models.AgentTaskNode.id)).where(
                models.AgentTaskNode.root_task_id == parent.root_task_id
            )
        ) or 0
        if total_nodes + len(subtasks) > self.max_nodes:
            raise CollaborationConflict("max_nodes_exceeded")
        coordination = self._validated_next_coordination(parent, message.payload)
        ancestor_assignees = self._parent_chain_assignees(db, parent)
        catalog = self._team_capability_catalog(
            db, run.team_id, owner_human_no=run.owner_human_no
        )
        capability_catalog = {
            item["agent_id"]: set(item["capabilities"])
            for item in catalog
        }
        for subtask in subtasks:
            assignee = db.get(models.DigitalEmployee, subtask["assignee_agent_id"])
            if assignee is None or not self._is_team_member(db, run.team_id, subtask["assignee_agent_id"]):
                raise CollaborationConflict("invalid_team_assignee")
            if not self._runtime_ref(assignee):
                raise CollaborationConflict("missing_runtime_ref")
            if assignee.status != "active":
                raise CollaborationConflict("inactive_assignee")
            if subtask["assignee_agent_id"] in ancestor_assignees:
                raise CollaborationConflict("parent_chain_cycle")
            if subtask["requires_capability"] and subtask["capability"] not in capability_catalog.get(
                subtask["assignee_agent_id"], set()
            ):
                raise CollaborationConflict("capability_not_available")
        hierarchy = self._team_hierarchy(db, run.team_id)
        try:
            hierarchy.validate_delegation(
                parent.assignee_agent_id,
                [subtask["assignee_agent_id"] for subtask in subtasks],
            )
        except TeamHierarchyError as exc:
            raise CollaborationConflict(str(exc)) from None
        child_node_kind = (
            "team_assignment"
            if hierarchy.level_of(parent.assignee_agent_id) == "manager"
            else "worker_task"
        )

        children: list[models.AgentTaskNode] = []
        for subtask in subtasks:
            child = models.AgentTaskNode(
                id=f"ATN-{uuid4().hex}",
                root_task_id=parent.root_task_id,
                parent_task_id=parent.id,
                client_task_key=subtask["client_task_key"],
                trace_id=parent.trace_id,
                title=subtask["title"],
                instruction=subtask["instruction"],
                coordinator_agent_id=parent.assignee_agent_id,
                assignee_agent_id=subtask["assignee_agent_id"],
                capability=subtask["capability"],
                node_kind=child_node_kind,
                revision_of_task_id=None,
                depth=parent.depth + 1,
                required=subtask["required"],
                status="blocked" if subtask["depends_on"] else "assigned",
                progress=0,
                input_payload=subtask["material"],
                output_payload={},
            )
            children.append(child)
        db.add_all(children)
        child_by_key = {child.client_task_key: child for child in children}
        for subtask in subtasks:
            for dependency_key in subtask["depends_on"]:
                db.add(models.AgentTaskDependency(
                    root_task_id=parent.root_task_id,
                    task_id=child_by_key[subtask["client_task_key"]].id,
                    depends_on_task_id=child_by_key[dependency_key].id,
                ))

        parent.status = "waiting_children"
        if coordination is not None:
            parent.input_payload = {
                **dict(parent.input_payload or {}),
                "coordination": coordination,
            }
        events = [
            self._append_event(
                db,
                root_task_id=parent.root_task_id,
                task_id=parent.id,
                event_type="task.delegate",
                actor_agent_id=message.sender,
                target_agent_id=parent.assignee_agent_id,
                summary="subtasks delegated",
                detail={
                    "client_task_keys": keys,
                    "node_kinds": {
                        child.client_task_key: child.node_kind for child in children
                    },
                    **({"round": coordination["round"]} if coordination is not None else {}),
                },
            ),
            self._append_event(
                db,
                root_task_id=parent.root_task_id,
                task_id=parent.id,
                event_type="task.waiting_children",
                actor_agent_id=message.sender,
                target_agent_id=parent.assignee_agent_id,
                summary="waiting for delegated children",
                detail={},
            ),
        ]
        for child in children:
            if child.status == "blocked":
                events.append(
                    self._append_event(
                        db,
                        root_task_id=child.root_task_id,
                        task_id=child.id,
                        event_type="task.blocked",
                        actor_agent_id=message.sender,
                        target_agent_id=child.assignee_agent_id,
                        summary="task blocked until dependencies complete",
                        detail={"parent_task_id": parent.id},
                    )
                )
            else:
                self._queue_assignment(db, child, subtasks_by_key(subtasks, child.client_task_key)["depends_on"])
                events.append(
                    self._append_event(
                        db,
                        root_task_id=child.root_task_id,
                        task_id=child.id,
                        event_type="task.assigned",
                        actor_agent_id=message.sender,
                        target_agent_id=child.assignee_agent_id,
                        summary="task assigned",
                        detail={"parent_task_id": parent.id},
                    )
                )
        return events

    def _apply_accepted(
        self, db: Session, node: models.AgentTaskNode, message: AgentProtocolMessage
    ) -> list[models.AgentTaskEvent]:
        self._transition(node, "accepted")
        node.started_at = datetime.now()
        return [self._event_for_message(db, node, message, "task.accepted", "task accepted")]

    def _apply_progress(
        self, db: Session, node: models.AgentTaskNode, message: AgentProtocolMessage
    ) -> list[models.AgentTaskEvent]:
        progress = message.payload.get("progress")
        if isinstance(progress, bool) or not isinstance(progress, int) or not 0 <= progress <= 100:
            raise CollaborationConflict("invalid_progress")
        if node.status == "accepted":
            self._transition(node, "running")
        elif node.status != "running":
            raise CollaborationConflict("invalid_transition")
        node.progress = progress
        return [self._event_for_message(db, node, message, "task.progress", "task progress updated")]

    def _apply_result(
        self, db: Session, node: models.AgentTaskNode, message: AgentProtocolMessage
    ) -> list[models.AgentTaskEvent]:
        self._transition(node, "submitted")
        node.progress = 100
        node.output_payload = dict(message.payload)
        node.finished_at = None
        events = [
            self._append_event(
                db,
                root_task_id=node.root_task_id,
                task_id=node.id,
                event_type="task.result.submitted",
                actor_agent_id=message.sender,
                target_agent_id=node.coordinator_agent_id,
                summary="task result submitted for review",
                detail=dict(message.payload),
            )
        ]
        review_payload = {
            "title": self._safe_cross_agent_text(node.title, _MAX_SUBTASK_TITLE_LENGTH),
            "instruction": self._safe_cross_agent_text(
                node.instruction, _MAX_SUBTASK_INSTRUCTION_LENGTH
            ),
            "required_capability": self._safe_cross_agent_text(
                node.capability, _MAX_SUBTASK_CAPABILITY_LENGTH
            ),
            "assignee_agent_id": node.assignee_agent_id,
            "node_kind": node.node_kind,
            "revision_of_task_id": node.revision_of_task_id,
            "thinking_enabled": self._task_thinking_enabled(
                db.get(models.AgentTaskNode, node.root_task_id)
            ),
            "result_status": "SUCCESS",
            "result": self._sanitize_tool_result_data(dict(message.payload)),
            "evidence": self._review_tool_evidence(db, node),
        }
        review_digest = hashlib.sha256(
            message.idempotency_key.encode("utf-8", errors="ignore")
        ).hexdigest()[:32]
        self._queue_outbound(
            db,
            root_task_id=node.root_task_id,
            task_id=node.id,
            parent_task_id=node.parent_task_id,
            message_type="task.result.submitted",
            sender_agent_id="platform",
            receiver_agent_id=node.coordinator_agent_id,
            body="Review the submitted task result.",
            payload=review_payload,
            idempotency_key=f"{node.id}:task.result.review:{review_digest}",
        )
        self._transition(node, "reviewing")
        events.append(
            self._append_event(
                db,
                root_task_id=node.root_task_id,
                task_id=node.id,
                event_type="task.result.reviewing",
                actor_agent_id="platform",
                target_agent_id=node.coordinator_agent_id,
                summary="task result awaiting coordinator review",
                detail={},
            )
        )
        return events

    def _apply_result_accepted(
        self, db: Session, node: models.AgentTaskNode, message: AgentProtocolMessage
    ) -> list[models.AgentTaskEvent]:
        feedback = message.payload.get("feedback")
        if (
            not isinstance(feedback, str)
            or not feedback.strip()
            or not self._is_safe_cross_agent_text(
                feedback.strip(), _MAX_SUBTASK_INSTRUCTION_LENGTH
            )
        ):
            raise CollaborationConflict("invalid_review_feedback")
        feedback = feedback.strip()
        self._transition(node, "completed")
        node.progress = 100
        node.finished_at = datetime.now()
        events = [
            self._append_event(
                db,
                root_task_id=node.root_task_id,
                task_id=node.id,
                event_type="task.result.accepted",
                actor_agent_id=message.sender,
                target_agent_id=node.assignee_agent_id,
                summary="task result accepted",
                detail={"feedback": feedback},
            ),
            self._append_event(
                db,
                root_task_id=node.root_task_id,
                task_id=node.id,
                event_type="task.completed",
                actor_agent_id=message.sender,
                target_agent_id=node.assignee_agent_id,
                summary="task completed after review",
                detail={},
            ),
        ]
        events.extend(self._release_ready_dependents(db, node))
        events.extend(self._maybe_request_parent_summary(db, node, message.sender))
        return events

    def _apply_result_rejected(
        self, db: Session, node: models.AgentTaskNode, message: AgentProtocolMessage
    ) -> list[models.AgentTaskEvent]:
        feedback = message.payload.get("feedback")
        if (
            not isinstance(feedback, str)
            or not feedback.strip()
            or not self._is_safe_cross_agent_text(
                feedback.strip(), _MAX_SUBTASK_INSTRUCTION_LENGTH
            )
        ):
            raise CollaborationConflict("invalid_review_feedback")
        feedback = feedback.strip()
        self._transition(node, "revision_requested")
        node.error_code = "result_rejected"
        node.error_message = feedback
        node.finished_at = datetime.now()

        incoming_dependencies = list(
            db.scalars(
                select(models.AgentTaskDependency).where(
                    models.AgentTaskDependency.task_id == node.id
                )
            )
        )
        prerequisites = [
            db.get(models.AgentTaskNode, dependency.depends_on_task_id)
            for dependency in incoming_dependencies
        ]
        prior_revision_count = db.scalar(
            select(func.count(models.AgentTaskNode.id)).where(
                models.AgentTaskNode.parent_task_id == node.parent_task_id,
                models.AgentTaskNode.revision_of_task_id.is_not(None),
            )
        ) or 0
        suffix = f":r{prior_revision_count + 1}"
        revision_key = f"{node.client_task_key[:64 - len(suffix)]}{suffix}"
        revision = models.AgentTaskNode(
            id=f"ATN-{uuid4().hex}",
            root_task_id=node.root_task_id,
            parent_task_id=node.parent_task_id,
            client_task_key=revision_key,
            trace_id=node.trace_id,
            title=node.title,
            instruction=node.instruction,
            coordinator_agent_id=node.coordinator_agent_id,
            assignee_agent_id=node.assignee_agent_id,
            capability=node.capability,
            node_kind=node.node_kind,
            revision_of_task_id=node.id,
            depth=node.depth,
            required=node.required,
            status=(
                "assigned"
                if all(item is not None and item.status == "completed" for item in prerequisites)
                else "blocked"
            ),
            progress=0,
            input_payload={
                **dict(node.input_payload or {}),
                "revision_of_task_id": node.id,
                "review_feedback": feedback,
            },
            output_payload={},
        )
        db.add(revision)
        for dependency in incoming_dependencies:
            db.add(
                models.AgentTaskDependency(
                    root_task_id=node.root_task_id,
                    task_id=revision.id,
                    depends_on_task_id=dependency.depends_on_task_id,
                )
            )
        db.flush()
        downstream_dependencies = list(
            db.scalars(
                select(models.AgentTaskDependency).where(
                    models.AgentTaskDependency.depends_on_task_id == node.id
                )
            )
        )
        for dependency in downstream_dependencies:
            dependency.depends_on_task_id = revision.id

        events = [
            self._append_event(
                db,
                root_task_id=node.root_task_id,
                task_id=node.id,
                event_type="task.result.rejected",
                actor_agent_id=message.sender,
                target_agent_id=node.assignee_agent_id,
                summary="task result rejected",
                detail={"feedback": feedback, "revision_task_id": revision.id},
            ),
            self._append_event(
                db,
                root_task_id=node.root_task_id,
                task_id=revision.id,
                event_type="task.revision.created",
                actor_agent_id=message.sender,
                target_agent_id=revision.assignee_agent_id,
                summary="revision task created",
                detail={
                    "revision_of_task_id": node.id,
                    "revision_task_id": revision.id,
                },
            ),
        ]
        if revision.status == "assigned":
            self._queue_assignment(
                db,
                revision,
                [item.client_task_key for item in prerequisites if item is not None],
                dependency_results=self._safe_dependency_results(db, revision.id),
            )
        return events

    def _apply_failed(
        self,
        db: Session,
        run: models.TaskRun,
        node: models.AgentTaskNode,
        message: AgentProtocolMessage,
    ) -> list[models.AgentTaskEvent]:
        self._transition(node, "failed")
        node.error_code = str(message.payload.get("error_code") or "failed")
        node.error_message = str(message.payload.get("error_message") or "")
        node.finished_at = datetime.now()
        events = [self._event_for_message(db, node, message, "task.failed", "task failed")]
        if (
            node.node_kind == "worker_task"
            and node.parent_task_id is not None
            and node.coordinator_agent_id != node.assignee_agent_id
        ):
            root = db.get(models.AgentTaskNode, node.root_task_id)
            parent = db.get(models.AgentTaskNode, node.parent_task_id)
            if root is None or parent is None or parent.root_task_id != node.root_task_id:
                raise CollaborationConflict("invalid_parent_task")
            catalog, catalog_version = self._next_catalog_snapshot(
                db, root, run.team_id
            )
            recovery_payload: dict[str, Any] = {
                "title": self._safe_cross_agent_text(
                    node.title, _MAX_SUBTASK_TITLE_LENGTH
                ),
                "instruction": self._safe_cross_agent_text(
                    node.instruction, _MAX_SUBTASK_INSTRUCTION_LENGTH
                ),
                "required_capability": self._safe_cross_agent_text(
                    node.capability, _MAX_SUBTASK_CAPABILITY_LENGTH
                ),
                "assignee_agent_id": node.assignee_agent_id,
                "node_kind": node.node_kind,
                "revision_of_task_id": node.revision_of_task_id,
                "result_status": "INTERRUPTED",
                "result": self._sanitize_tool_result_data(
                    dict(node.output_payload or {})
                ),
                "evidence": self._review_tool_evidence(db, node),
                "failure": {
                    "error_code": self._safe_tool_text(node.error_code),
                    "error_message": self._safe_tool_text(node.error_message),
                },
                "current_dag": self._department_dag_payload(db, parent.id),
                "thinking_enabled": self._task_thinking_enabled(root),
                "team_capability_catalog": catalog,
                "team_catalog_version": catalog_version,
                "remaining_recovery_attempts": self._remaining_recovery_attempts(
                    db, node
                ),
            }
            relationship_graph = self._team_relationship_graph(
                db,
                run.team_id,
                node.coordinator_agent_id,
                catalog=catalog,
            )
            if relationship_graph:
                recovery_payload["team_relationship_graph"] = relationship_graph
            recovery_digest = hashlib.sha256(
                message.idempotency_key.encode("utf-8", errors="ignore")
            ).hexdigest()[:32]
            self._queue_outbound(
                db,
                root_task_id=node.root_task_id,
                task_id=node.id,
                parent_task_id=node.parent_task_id,
                message_type="task.recovery.requested",
                sender_agent_id="platform",
                receiver_agent_id=node.coordinator_agent_id,
                body="Review the interrupted Worker task and decide the next graph shape.",
                payload=recovery_payload,
                idempotency_key=(
                    f"{node.id}:task.recovery.requested:{recovery_digest}"
                ),
            )
            events.append(
                self._append_event(
                    db,
                    root_task_id=node.root_task_id,
                    task_id=node.id,
                    event_type="task.recovery.requested",
                    actor_agent_id="platform",
                    target_agent_id=node.coordinator_agent_id,
                    summary="interrupted task returned to Leader for replanning",
                    detail={
                        "result_status": "INTERRUPTED",
                        "error_code": self._safe_tool_text(node.error_code),
                    },
                )
            )
            return events
        if node.required:
            events.extend(self._propagate_required_failure(db, node, message.sender))
        else:
            events.extend(self._maybe_request_parent_summary(db, node, message.sender))
        events.extend(self._cancel_dependency_descendants(db, node, message.sender))
        return events

    def _apply_replan(
        self,
        db: Session,
        run: models.TaskRun,
        node: models.AgentTaskNode,
        message: AgentProtocolMessage,
    ) -> list[models.AgentTaskEvent]:
        feedback, tasks = self._validated_replan(db, run, node, message.payload)
        if node.status == "reviewing":
            self._transition(node, "revision_requested")
        elif node.status != "failed":
            raise CollaborationConflict("invalid_transition")
        node.error_code = "leader_replanned"
        node.error_message = feedback
        node.finished_at = node.finished_at or datetime.now()
        try:
            applied = DatabaseTaskflowAdapter(db).apply_replan(
                node,
                feedback=feedback,
                tasks=tasks,
            )
        except TaskflowError as exc:
            raise CollaborationConflict("invalid_recovery_dag") from exc

        events = [
            self._append_event(
                db,
                root_task_id=node.root_task_id,
                task_id=node.id,
                event_type="task.replan",
                actor_agent_id=message.sender,
                target_agent_id=node.coordinator_agent_id,
                summary="Leader replaced interrupted work with a new task graph",
                detail={
                    "feedback": feedback,
                    "replacement_task_ids": [item.id for item in applied.nodes],
                },
            )
        ]
        depends_by_key = {task.client_task_key: list(task.depends_on) for task in tasks}
        for replacement in applied.nodes:
            events.append(self._append_event(
                db,
                root_task_id=replacement.root_task_id,
                task_id=replacement.id,
                event_type="task.revision.created",
                actor_agent_id=message.sender,
                target_agent_id=replacement.assignee_agent_id,
                summary="Leader recovery task created",
                detail={
                    "revision_of_task_id": node.id,
                    "replacement_task_id": replacement.id,
                },
            ))
            if replacement.status == "assigned":
                self._queue_assignment(
                    db,
                    replacement,
                    depends_by_key[replacement.client_task_key],
                    dependency_results=self._safe_dependency_results(db, replacement.id),
                )
                events.append(self._append_event(
                    db,
                    root_task_id=replacement.root_task_id,
                    task_id=replacement.id,
                    event_type="task.assigned",
                    actor_agent_id=message.sender,
                    target_agent_id=replacement.assignee_agent_id,
                    summary="Leader recovery task assigned",
                    detail={"revision_of_task_id": node.id},
                ))
            else:
                events.append(self._append_event(
                    db,
                    root_task_id=replacement.root_task_id,
                    task_id=replacement.id,
                    event_type="task.blocked",
                    actor_agent_id=message.sender,
                    target_agent_id=replacement.assignee_agent_id,
                    summary="recovery task blocked until dependencies complete",
                    detail={"revision_of_task_id": node.id},
                ))
        return events

    def _apply_blocked(
        self,
        db: Session,
        node: models.AgentTaskNode,
        message: AgentProtocolMessage,
    ) -> list[models.AgentTaskEvent]:
        feedback = self._validated_review_feedback(message.payload)
        if node.node_kind != "worker_task":
            raise CollaborationConflict("invalid_recovery_target")
        if node.status == "reviewing":
            self._transition(node, "failed")
        elif node.status != "failed":
            raise CollaborationConflict("invalid_transition")
        node.error_code = self._safe_tool_text(
            str(message.payload.get("reason_code") or "leader_blocked")
        )
        node.error_message = feedback
        node.finished_at = node.finished_at or datetime.now()
        events = [self._append_event(
            db,
            root_task_id=node.root_task_id,
            task_id=node.id,
            event_type="task.blocked",
            actor_agent_id=message.sender,
            target_agent_id=node.coordinator_agent_id,
            summary="Leader reported the Worker task as unrecoverable",
            detail={"feedback": feedback, "reason_code": node.error_code},
        )]
        events.extend(self._cancel_dependency_descendants(db, node, message.sender))
        if node.required:
            events.extend(self._propagate_required_failure(db, node, message.sender))
        else:
            events.extend(self._maybe_request_parent_summary(db, node, message.sender))
        return events

    def _validated_replan(
        self,
        db: Session,
        run: models.TaskRun,
        node: models.AgentTaskNode,
        payload: Mapping[str, Any],
    ) -> tuple[str, tuple[ReplacementTask, ...]]:
        feedback = self._validated_review_feedback(payload)
        if node.node_kind != "worker_task" or node.parent_task_id is None:
            raise CollaborationConflict("invalid_recovery_target")
        if self._remaining_recovery_attempts(db, node) < 1:
            raise CollaborationConflict("recovery_attempts_exhausted")
        raw_subtasks = payload.get("subtasks")
        if not isinstance(raw_subtasks, list) or not 1 <= len(raw_subtasks) <= self.max_children:
            raise CollaborationConflict("invalid_subtask_count")
        subtasks = [self._validate_subtask(item) for item in raw_subtasks]
        if not any(item["required"] for item in subtasks):
            raise CollaborationConflict("required_subtask_required")
        keys = [item["client_task_key"] for item in subtasks]
        if len(keys) != len(set(keys)):
            raise CollaborationConflict("duplicate_client_task_key")
        if any(dependency not in keys for item in subtasks for dependency in item["depends_on"]):
            raise CollaborationConflict("unknown_dependency")
        if any(len(item["depends_on"]) != len(set(item["depends_on"])) for item in subtasks):
            raise CollaborationConflict("duplicate_dependency")
        self._assert_acyclic(subtasks)
        existing_keys = set(db.scalars(
            select(models.AgentTaskNode.client_task_key).where(
                models.AgentTaskNode.parent_task_id == node.parent_task_id
            )
        ))
        if existing_keys.intersection(keys):
            raise CollaborationConflict("duplicate_client_task_key")
        total_nodes = db.scalar(select(func.count(models.AgentTaskNode.id)).where(
            models.AgentTaskNode.root_task_id == node.root_task_id
        )) or 0
        if total_nodes + len(subtasks) > self.max_nodes:
            raise CollaborationConflict("max_nodes_exceeded")

        parent = db.get(models.AgentTaskNode, node.parent_task_id)
        if parent is None or parent.root_task_id != node.root_task_id:
            raise CollaborationConflict("invalid_parent_task")
        if parent.assignee_agent_id != node.coordinator_agent_id:
            raise CollaborationConflict("invalid_recovery_coordinator")
        catalog = self._team_capability_catalog(
            db, run.team_id, owner_human_no=run.owner_human_no
        )
        capabilities_by_agent = {
            item["agent_id"]: set(item["capabilities"]) for item in catalog
        }
        ancestor_assignees = self._parent_chain_assignees(db, parent)
        for item in subtasks:
            assignee = db.get(models.DigitalEmployee, item["assignee_agent_id"])
            if assignee is None or not self._is_team_member(
                db, run.team_id, item["assignee_agent_id"]
            ):
                raise CollaborationConflict("invalid_team_assignee")
            if not self._runtime_ref(assignee):
                raise CollaborationConflict("missing_runtime_ref")
            if assignee.status != "active":
                raise CollaborationConflict("inactive_assignee")
            if item["assignee_agent_id"] in ancestor_assignees:
                raise CollaborationConflict("parent_chain_cycle")
            if item["requires_capability"] and item["capability"] not in capabilities_by_agent.get(
                item["assignee_agent_id"], set()
            ):
                raise CollaborationConflict("capability_not_available")
        try:
            self._team_hierarchy(db, run.team_id).validate_delegation(
                parent.assignee_agent_id,
                [item["assignee_agent_id"] for item in subtasks],
            )
        except TeamHierarchyError as exc:
            raise CollaborationConflict(str(exc)) from None

        planned = tuple(ReplacementTask(
            client_task_key=item["client_task_key"],
            title=item["title"],
            instruction=item["instruction"],
            assignee_agent_id=item["assignee_agent_id"],
            required_capability=item["capability"],
            depends_on=tuple(item["depends_on"]),
            required=item["required"],
            material=dict(item["material"]),
        ) for item in subtasks)
        try:
            DatabaseTaskflowAdapter.validate_replan(planned)
        except TaskflowError as exc:
            raise CollaborationConflict("invalid_recovery_dag") from exc
        return feedback, planned

    def _validated_review_feedback(self, payload: Mapping[str, Any]) -> str:
        feedback = payload.get("feedback")
        if (
            not isinstance(feedback, str)
            or not feedback.strip()
            or not self._is_safe_cross_agent_text(
                feedback.strip(), _MAX_SUBTASK_INSTRUCTION_LENGTH
            )
        ):
            raise CollaborationConflict("invalid_review_feedback")
        return feedback.strip()

    def _review_tool_evidence(
        self, db: Session, node: models.AgentTaskNode
    ) -> list[dict[str, Any]]:
        messages = list(
            db.scalars(
                select(models.AgentMessage)
                .where(
                    models.AgentMessage.root_task_id == node.root_task_id,
                    models.AgentMessage.task_id == node.id,
                    models.AgentMessage.message_type == "tool.result",
                    models.AgentMessage.sender_agent_id == "platform",
                    models.AgentMessage.receiver_agent_id == node.assignee_agent_id,
                    models.AgentMessage.direction == "outbound",
                    models.AgentMessage.delivery_status.in_(["pending", "sent"]),
                )
                .order_by(models.AgentMessage.created_at, models.AgentMessage.id)
            )
        )
        return self._bound_result_collection(
            [
                self._sanitize_tool_result_data(dict(item.payload or {}))
                for item in messages
            ]
        )

    def _department_dag_payload(
        self, db: Session, parent_id: str
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for child in self._active_children(db, parent_id):
            dependency_ids = list(
                db.scalars(
                    select(models.AgentTaskDependency.depends_on_task_id).where(
                        models.AgentTaskDependency.task_id == child.id
                    )
                )
            )
            dependencies = [db.get(models.AgentTaskNode, item) for item in dependency_ids]
            result.append(
                {
                    "client_task_key": self._safe_client_task_key(
                        child.client_task_key
                    ),
                    "title": self._safe_tool_text(child.title),
                    "assignee_agent_id": child.assignee_agent_id,
                    "required_capability": self._safe_tool_text(child.capability),
                    "status": child.status,
                    "depends_on": [
                        self._safe_client_task_key(item.client_task_key)
                        for item in dependencies
                        if item is not None
                    ],
                }
            )
        return result

    def _remaining_recovery_attempts(
        self, db: Session, node: models.AgentTaskNode
    ) -> int:
        chain_ids = {node.id}
        current = node
        while current.revision_of_task_id:
            chain_ids.add(current.revision_of_task_id)
            previous = db.get(models.AgentTaskNode, current.revision_of_task_id)
            if previous is None:
                break
            current = previous
        attempts = db.scalar(
            select(func.count(models.AgentTaskNode.id)).where(
                models.AgentTaskNode.root_task_id == node.root_task_id,
                models.AgentTaskNode.revision_of_task_id.in_(chain_ids),
            )
        ) or 0
        return max(0, self.max_rounds - attempts)

    def _apply_summary(
        self,
        db: Session,
        run: models.TaskRun,
        node: models.AgentTaskNode,
        message: AgentProtocolMessage,
    ) -> list[models.AgentTaskEvent]:
        self._transition(node, "completed")
        summary = self._message_text(message.payload)
        node.output_payload = dict(message.payload)
        node.finished_at = datetime.now()
        events = [self._event_for_message(db, node, message, "task.summary", "task summary received")]
        if node.id == run.id:
            # A root TaskRun is never completed by a worker result or an ordinary
            # message.  Only its coordinator's valid summary reaches this branch.
            run.status = "completed"
            run.summary = summary
        else:
            events.extend(self._maybe_request_parent_summary(db, node, message.sender))
        return events

    def _apply_tool_request(
        self,
        db: Session,
        run: models.TaskRun,
        node: models.AgentTaskNode,
        message: AgentProtocolMessage,
    ) -> list[models.AgentTaskEvent]:
        """Execute an assigned worker request through the platform gateway only."""
        root = db.get(models.AgentTaskNode, run.id)
        if root is None or root.root_task_id != run.id:
            raise CollaborationConflict("invalid_root_task")

        event_type = "tool.result"
        summary = "tool request completed"
        detail: dict[str, Any] = {}
        plugin_id, knowledge_base_id, denial_reason = self._resolve_tool_target(db, message)
        if denial_reason is not None:
            result_payload = {"ok": False, "decision": "deny", "audit_ids": []}
            event_type = "tool.denied"
            summary = "tool request denied"
            detail["reason"] = denial_reason
        else:
            policy_error: HTTPException | None = None
            try:
                # The nested savepoint bounds all ORM writes made through this
                # Session by the gateway. It cannot roll back a runtime/adapter
                # external side effect or an adapter-owned independent Session;
                # it does keep this runner's outbox and cursor transaction safe.
                with db.begin_nested():
                    with self._gateway_transaction_guard(db):
                        try:
                            result = invoke_plugin(
                                db,
                                employee_id=node.assignee_agent_id,
                                plugin_id=plugin_id,
                                action=str(message.payload["action"]),
                                params=dict(message.payload.get("params") or {}),
                                trace_id=node.trace_id,
                                knowledge_base_id=knowledge_base_id,
                                execution_context={
                                    "task_id": node.id,
                                    "team_id": run.team_id,
                                    "owner_human_no": run.owner_human_no,
                                    "request": root.instruction,
                                    "subtask": node.instruction,
                                    "collaboration_summary": message.payload.get("context", ""),
                                },
                            )
                        except HTTPException as error:
                            # A policy response is a completed, auditable gateway
                            # outcome, so retain its audit row and send a deny result.
                            policy_error = error
                if policy_error is None:
                    result_payload = self._safe_tool_result_payload(result)
                else:
                    result_payload = self._denied_tool_result_payload(policy_error)
                    event_type = "tool.denied"
                    summary = "tool request denied"
            except Exception:
                # Any unexpected gateway exception exits the nested savepoint and
                # rolls back same-session partial writes before this safe result is
                # persisted outside it. No exception text reaches Matrix.
                result_payload = {"ok": False, "decision": "failed", "audit_ids": []}
                event_type = "tool.failed"
                summary = "tool request failed"

        catalog, catalog_version = self._next_catalog_snapshot(db, root, run.team_id)
        result_payload = {
            **result_payload,
            "team_capability_catalog": catalog,
            "team_catalog_version": catalog_version,
        }
        self._queue_outbound(
            db,
            root_task_id=node.root_task_id,
            task_id=node.id,
            parent_task_id=node.parent_task_id,
            message_type="tool.result",
            sender_agent_id="platform",
            receiver_agent_id=node.assignee_agent_id,
            body="tool result",
            payload=result_payload,
            idempotency_key=self._tool_result_idempotency_key(node, message),
        )
        return [
            self._append_event(
                db,
                root_task_id=node.root_task_id,
                task_id=node.id,
                event_type=event_type,
                actor_agent_id=message.sender,
                target_agent_id=node.assignee_agent_id,
                summary=summary,
                detail={
                    "decision": result_payload["decision"],
                    "audit_ids": list(result_payload["audit_ids"]),
                    **detail,
                },
            )
        ]

    @staticmethod
    def _resolve_tool_target(
        db: Session, message: AgentProtocolMessage
    ) -> tuple[str, str | None, str | None]:
        """Bind knowledge requests to server-owned resource level and plugin."""
        requested_plugin = message.payload.get("plugin_id")
        plugin_id = str(requested_plugin) if requested_plugin is not None else ""
        if "knowledge_base_id" not in message.payload:
            return plugin_id, None, None

        raw_kb_id = message.payload.get("knowledge_base_id")
        if not isinstance(raw_kb_id, str) or not raw_kb_id.strip():
            return plugin_id, None, "invalid_knowledge_base_id"
        knowledge_base = resolve_knowledge_base(db, raw_kb_id)
        if knowledge_base is None:
            return plugin_id, None, "knowledge_base_not_found"
        plugin = db.get(models.Plugin, plugin_id)
        if (
            plugin is None
            or plugin.type != "knowledge"
            or message.payload.get("action") != "read"
        ):
            return plugin_id, None, "knowledge_base_requires_knowledge_read"
        return plugin_id_for_level(knowledge_base.data_level), knowledge_base.id, None

    @classmethod
    def _safe_tool_result_payload(cls, result: Any) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            return {"ok": False, "decision": "failed", "audit_ids": []}
        decision = result.get("decision")
        safe_decision = decision if decision in {"allow", "deny", "approval"} else "failed"
        payload: dict[str, Any] = {
            "ok": result.get("ok") is True,
            "decision": safe_decision,
            "audit_ids": cls._safe_audit_ids(result.get("audit_ids")),
        }
        for field in ("tool_name", "tool_type", "execution_mode", "runtime_mode"):
            value = result.get(field)
            if isinstance(value, str) and value:
                payload[field] = cls._safe_tool_text(value)
        if "data" in result:
            payload["data"] = cls._sanitize_tool_result_data(result["data"])
        return cls._bound_tool_result_payload(payload)

    @classmethod
    def _denied_tool_result_payload(cls, error: HTTPException) -> dict[str, Any]:
        detail = error.detail
        audit_values: Any = []
        if isinstance(detail, Mapping):
            audit_values = detail.get("audit_ids", detail.get("audit_id", []))
        return {"ok": False, "decision": "deny", "audit_ids": cls._safe_audit_ids(audit_values)}

    @classmethod
    def _sanitize_tool_result_data(cls, value: Any, depth: int = 0) -> Any:
        if depth >= _MAX_TOOL_RESULT_DEPTH:
            return "[truncated]"
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, str):
            return cls._safe_tool_text(value)
        if isinstance(value, Mapping):
            sanitized: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= _MAX_TOOL_RESULT_ITEMS:
                    sanitized["truncated"] = True
                    break
                key_text = str(key)[:80]
                if cls._is_sensitive_tool_field(key_text):
                    continue
                sanitized[key_text] = cls._sanitize_tool_result_data(item, depth + 1)
            return sanitized
        if isinstance(value, (list, tuple)):
            sanitized_items = [
                cls._sanitize_tool_result_data(item, depth + 1)
                for item in value[:_MAX_TOOL_RESULT_ITEMS]
            ]
            if len(value) > _MAX_TOOL_RESULT_ITEMS:
                sanitized_items.append("[truncated]")
            return sanitized_items
        return "[unsupported result type]"

    @classmethod
    def _bound_tool_result_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        if cls._serialized_tool_result_size(payload) <= _MAX_TOOL_RESULT_BYTES:
            return payload
        minimal = {
            "ok": payload["ok"],
            "decision": payload["decision"],
            "audit_ids": payload["audit_ids"],
            "truncated": True,
        }
        if cls._serialized_tool_result_size(minimal) <= _MAX_TOOL_RESULT_BYTES:
            return minimal
        return {
            "ok": payload["ok"],
            "decision": payload["decision"],
            "audit_ids": [],
            "truncated": True,
        }

    @staticmethod
    def _serialized_tool_result_size(payload: dict[str, Any]) -> int:
        try:
            return len(json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8"))
        except (TypeError, ValueError, UnicodeEncodeError):
            return _MAX_TOOL_RESULT_BYTES + 1

    @staticmethod
    def _safe_audit_ids(value: Any) -> list[int]:
        values = value if isinstance(value, (list, tuple)) else [value]
        return [
            item for item in values[:_MAX_TOOL_RESULT_ITEMS]
            if isinstance(item, int) and not isinstance(item, bool) and -(2 ** 63) <= item < 2 ** 63
        ]

    @staticmethod
    def _is_sensitive_tool_field(key: str) -> bool:
        return _SECRET_FIELD_PATTERN.search(key) is not None

    @staticmethod
    def _safe_tool_text(value: str) -> str:
        sanitized = value.encode("utf-8", errors="replace").decode("utf-8")
        sanitized = _URI_PATTERN.sub("[redacted]", sanitized)
        sanitized = _SECRET_ASSIGNMENT_PATTERN.sub("[redacted]", sanitized)
        sanitized = _AUTH_VALUE_PATTERN.sub("[redacted]", sanitized)
        if _STACK_TRACE_PATTERN.search(sanitized):
            return "[redacted]"
        return sanitized[:_MAX_TOOL_RESULT_STRING_LENGTH]

    @staticmethod
    def _tool_result_idempotency_key(node: models.AgentTaskNode, message: AgentProtocolMessage) -> str:
        digest = hashlib.sha256(message.idempotency_key.encode("utf-8", errors="ignore")).hexdigest()
        return f"{node.id}:tool.result:{digest[:32]}"

    @staticmethod
    @contextmanager
    def _gateway_transaction_guard(db: Session):
        """Keep legacy gateway commits within the caller-owned inner savepoint."""
        original_commit = db.commit
        db.commit = db.flush  # type: ignore[method-assign]
        try:
            yield
        finally:
            db.commit = original_commit  # type: ignore[method-assign]

    def _release_ready_dependents(
        self, db: Session, completed_node: models.AgentTaskNode
    ) -> list[models.AgentTaskEvent]:
        """Assign only blocked siblings whose every prerequisite now completed."""
        dependents = list(
            db.scalars(
                select(models.AgentTaskNode)
                .join(
                    models.AgentTaskDependency,
                    models.AgentTaskDependency.task_id == models.AgentTaskNode.id,
                )
                .where(
                    models.AgentTaskDependency.depends_on_task_id == completed_node.id,
                    models.AgentTaskNode.status == "blocked",
                )
            )
        )
        events: list[models.AgentTaskEvent] = []
        for dependent in dependents:
            prerequisite_ids = list(
                db.scalars(
                    select(models.AgentTaskDependency.depends_on_task_id).where(
                        models.AgentTaskDependency.task_id == dependent.id
                    )
                )
            )
            prerequisites = [db.get(models.AgentTaskNode, item) for item in prerequisite_ids]
            if any(item is None or item.status != "completed" for item in prerequisites):
                continue
            self._transition(dependent, "assigned")
            self._queue_assignment(
                db,
                dependent,
                [item.client_task_key for item in prerequisites if item is not None],
                dependency_results=self._safe_dependency_results(db, dependent.id),
            )
            events.append(
                self._append_event(
                    db,
                    root_task_id=dependent.root_task_id,
                    task_id=dependent.id,
                    event_type="task.unblocked",
                    actor_agent_id="platform",
                    target_agent_id=dependent.assignee_agent_id,
                    summary="dependencies complete; task assigned",
                    detail={"dependency_task_ids": prerequisite_ids},
                )
            )
        return events

    def _safe_dependency_results(self, db: Session, task_id: str) -> list[dict[str, Any]]:
        dependency_ids = list(
            db.scalars(
                select(models.AgentTaskDependency.depends_on_task_id).where(
                    models.AgentTaskDependency.task_id == task_id
                )
            )
        )
        dependencies = [db.get(models.AgentTaskNode, dependency_id) for dependency_id in dependency_ids]
        return self._bound_result_collection([self._safe_node_result(item) for item in dependencies if item is not None])

    def _safe_child_results(self, db: Session, parent_id: str) -> list[dict[str, Any]]:
        children = self._active_children(db, parent_id)
        return self._bound_result_collection([self._safe_node_result(child) for child in children])

    def _completed_direct_child_audit_ids(self, db: Session, parent_id: str) -> list[int]:
        children = [
            child
            for child in self._active_children(db, parent_id)
            if child.status == "completed"
        ]
        audit_ids: list[int] = []
        seen: set[int] = set()
        for child in children:
            payload = child.output_payload if isinstance(child.output_payload, Mapping) else {}
            values = payload.get("audit_ids")
            if not isinstance(values, list):
                continue
            for audit_id in values:
                if (
                    not isinstance(audit_id, int)
                    or isinstance(audit_id, bool)
                    or not 0 < audit_id < 2 ** 63
                    or audit_id in seen
                ):
                    continue
                seen.add(audit_id)
                audit_ids.append(audit_id)
                if len(audit_ids) >= _MAX_SUMMARY_AUDIT_IDS:
                    return audit_ids
        return audit_ids

    def _safe_node_result(self, node: models.AgentTaskNode) -> dict[str, Any]:
        result: dict[str, Any] = {
            "client_task_key": self._safe_client_task_key(node.client_task_key),
            "status": node.status,
            "result": self._sanitize_tool_result_data(dict(node.output_payload or {})),
        }
        if node.error_code:
            result["error_code"] = self._safe_tool_text(node.error_code)
        if node.error_message:
            result["error_message"] = self._safe_tool_text(node.error_message)
        if self._serialized_tool_result_size(result) <= _MAX_SAFE_NODE_RESULT_BYTES:
            return result
        payload = node.output_payload or {}
        minimal_result = {
            "summary": self._safe_tool_text(self._message_text(payload)),
            "audit_ids": self._safe_audit_ids(payload.get("audit_ids", [])),
        }
        approval_ref = payload.get("approval_ref")
        if isinstance(approval_ref, str) and self._safe_tool_text(approval_ref) == approval_ref:
            minimal_result["approval_ref"] = approval_ref
        return {
            "client_task_key": self._safe_client_task_key(node.client_task_key),
            "status": node.status,
            "result": minimal_result,
            "truncated": True,
        }

    def _bound_result_collection(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for result in results:
            if self._serialized_tool_result_size([*kept, result]) > _MAX_SAFE_RESULT_COLLECTION_BYTES:
                kept.append({"truncated": True})
                break
            kept.append(result)
        return kept

    def _queue_assignment(
        self,
        db: Session,
        child: models.AgentTaskNode,
        dependency_keys: list[str],
        *,
        dependency_results: list[dict[str, Any]] | None = None,
    ) -> None:
        root = db.get(models.AgentTaskNode, child.root_task_id)
        run = db.get(models.TaskRun, child.root_task_id)
        if root is None or run is None:
            raise CollaborationConflict("unknown_root_task")
        catalog, catalog_version = self._next_catalog_snapshot(db, root, run.team_id)
        payload: dict[str, Any] = {
            "title": self._safe_cross_agent_text(child.title, _MAX_SUBTASK_TITLE_LENGTH),
            "instruction": self._safe_cross_agent_text(child.instruction, _MAX_SUBTASK_INSTRUCTION_LENGTH),
            "client_task_key": self._safe_client_task_key(child.client_task_key),
            "required_capability": self._safe_cross_agent_text(
                child.capability, _MAX_SUBTASK_CAPABILITY_LENGTH
            ),
            "depends_on": [self._safe_client_task_key(key) for key in dependency_keys],
            "parent_chain_agent_ids": self._assignment_parent_chain(db, child),
            "thinking_enabled": self._task_thinking_enabled(root),
            "team_capability_catalog": catalog,
            "team_catalog_version": catalog_version,
            "material": dict(child.input_payload or {}),
        }
        relationship_graph = self._team_relationship_graph(
            db, run.team_id, child.assignee_agent_id, catalog=catalog
        )
        if relationship_graph:
            payload["team_relationship_graph"] = relationship_graph
        if child.revision_of_task_id:
            payload["revision_of_task_id"] = child.revision_of_task_id
            payload["review_feedback"] = self._safe_cross_agent_text(
                dict(child.input_payload or {}).get("review_feedback"),
                _MAX_SUBTASK_INSTRUCTION_LENGTH,
            )
        if dependency_results:
            payload["dependency_results"] = dependency_results
        self._queue_outbound(
            db,
            root_task_id=child.root_task_id,
            task_id=child.id,
            parent_task_id=child.parent_task_id,
            message_type="task.assign",
            sender_agent_id=child.coordinator_agent_id,
            receiver_agent_id=child.assignee_agent_id,
            body=self._safe_cross_agent_text(child.instruction, _MAX_SUBTASK_INSTRUCTION_LENGTH),
            payload=payload,
            idempotency_key=f"{child.id}:task.assign",
        )

    @staticmethod
    def _assignment_parent_chain(db: Session, child: models.AgentTaskNode) -> list[str]:
        """Platform-owned root-to-parent assignee chain for Worker cycle checks."""
        chain: list[str] = []
        current_id = child.parent_task_id
        while current_id is not None:
            current = db.get(models.AgentTaskNode, current_id)
            if current is None or current.root_task_id != child.root_task_id:
                raise CollaborationConflict("invalid_parent_task")
            if current.assignee_agent_id in chain:
                raise CollaborationConflict("parent_chain_cycle")
            chain.append(current.assignee_agent_id)
            current_id = current.parent_task_id
        chain.reverse()
        if not chain or child.coordinator_agent_id not in chain:
            raise CollaborationConflict("invalid_parent_task")
        return chain

    def _next_catalog_snapshot(
        self, db: Session, root: models.AgentTaskNode, team_id: str
    ) -> tuple[list[dict[str, Any]], int]:
        """Persist a root-local, transaction-bound sequence for live team authority."""
        coordination = self._coordination(root)
        version = coordination.get("team_catalog_version", 0) + 1
        root.input_payload = {
            **dict(root.input_payload or {}),
            "coordination": {**coordination, "team_catalog_version": version},
        }
        run = db.get(models.TaskRun, root.root_task_id)
        owner_human_no = run.owner_human_no if run is not None else ""
        return self._team_capability_catalog(
            db, team_id, owner_human_no=owner_human_no
        ), version

    def _propagate_required_failure(
        self, db: Session, failed_node: models.AgentTaskNode, actor_agent_id: str
    ) -> list[models.AgentTaskEvent]:
        """Fail required ancestors so a final summary cannot mask missing evidence."""
        events: list[models.AgentTaskEvent] = []
        current = failed_node
        while current.parent_task_id is not None:
            parent = db.get(models.AgentTaskNode, current.parent_task_id)
            if parent is None or parent.root_task_id != failed_node.root_task_id:
                raise CollaborationConflict("invalid_parent_task")
            if parent.status != "failed":
                self._transition(parent, "failed")
                parent.error_code = "required_child_failed"
                parent.error_message = "A required child task failed."
                parent.finished_at = datetime.now()
                events.append(
                    self._append_event(
                        db,
                        root_task_id=parent.root_task_id,
                        task_id=parent.id,
                        event_type="task.failed",
                        actor_agent_id=actor_agent_id,
                        target_agent_id=parent.assignee_agent_id,
                        summary="required child failed",
                        detail={"failed_child_task_id": current.id},
                    )
                )
            current = parent
            if not current.required:
                break
        root = db.get(models.AgentTaskNode, failed_node.root_task_id)
        run = db.get(models.TaskRun, failed_node.root_task_id)
        if root is not None and root.status == "failed" and run is not None:
            run.status = "failed"
        return events

    def _cancel_dependency_descendants(
        self, db: Session, failed_node: models.AgentTaskNode, actor_agent_id: str
    ) -> list[models.AgentTaskEvent]:
        """End every transitive dependant instead of leaving blocked task nodes behind."""
        pending = [failed_node.id]
        events: list[models.AgentTaskEvent] = []
        while pending:
            predecessor_id = pending.pop()
            dependent_ids = list(db.scalars(select(models.AgentTaskDependency.task_id).where(
                models.AgentTaskDependency.depends_on_task_id == predecessor_id
            )))
            for dependent_id in dependent_ids:
                dependent = db.get(models.AgentTaskNode, dependent_id)
                if dependent is None or dependent.status in {
                    "completed", "failed", "cancelled", "timed_out", "revision_requested"
                }:
                    continue
                self._transition(dependent, "cancelled")
                dependent.error_code = "dependency_failed"
                dependent.error_message = "A prerequisite task did not complete."
                dependent.finished_at = datetime.now()
                events.append(self._append_event(
                    db, root_task_id=dependent.root_task_id, task_id=dependent.id,
                    event_type="task.cancelled", actor_agent_id=actor_agent_id,
                    target_agent_id=dependent.assignee_agent_id,
                    summary="cancelled because a dependency failed", detail={"depends_on_task_id": predecessor_id},
                ))
                if dependent.required:
                    events.extend(self._propagate_required_failure(db, dependent, actor_agent_id))
                else:
                    events.extend(self._maybe_request_parent_summary(db, dependent, actor_agent_id))
                pending.append(dependent.id)
        return events

    def _maybe_request_parent_summary(
        self, db: Session, child: models.AgentTaskNode, actor_agent_id: str
    ) -> list[models.AgentTaskEvent]:
        if child.parent_task_id is None:
            return []
        parent = db.get(models.AgentTaskNode, child.parent_task_id)
        if parent is None or parent.root_task_id != child.root_task_id:
            raise CollaborationConflict("invalid_parent_task")
        if parent.status != "waiting_children":
            return []
        active_children = self._active_children(db, parent.id)
        required_children = [item for item in active_children if item.required]
        if not required_children or any(item.status != "completed" for item in required_children):
            return []
        self._transition(parent, "summarizing")
        root = db.get(models.AgentTaskNode, parent.root_task_id)
        run = db.get(models.TaskRun, parent.root_task_id)
        if root is None or run is None:
            raise CollaborationConflict("unknown_root_task")
        coordination = self._coordination(root) if root is not None else {"round": 0, "selected_skill_ids": []}
        catalog, catalog_version = self._next_catalog_snapshot(db, root, run.team_id)
        summary_payload: dict[str, Any] = {
            "request": "task.summary",
            "parent_task_id": parent.id,
            "child_results": self._safe_child_results(db, parent.id),
            "audit_ids": self._completed_direct_child_audit_ids(db, parent.id),
            "existing_child_task_keys": [
                item.client_task_key for item in active_children
            ],
            "current_round": coordination["round"],
            "thinking_enabled": self._task_thinking_enabled(root),
            "team_capability_catalog": catalog,
            "team_catalog_version": catalog_version,
            "remaining_limits": {
                "rounds": max(0, 1 + self.max_rounds - coordination["round"]),
                "nodes": max(0, self.max_nodes - self._root_node_count(db, parent.root_task_id)),
                "children": self.max_children,
                "depth": max(0, self.max_depth - parent.depth),
            },
        }
        relationship_graph = self._team_relationship_graph(
            db, run.team_id, parent.assignee_agent_id, catalog=catalog
        )
        if relationship_graph:
            summary_payload["team_relationship_graph"] = relationship_graph
        self._queue_outbound(
            db,
            root_task_id=parent.root_task_id,
            task_id=parent.id,
            parent_task_id=parent.parent_task_id,
            message_type="task.summary",
            sender_agent_id="platform",
            # The assignee owns this node's runtime context and must combine
            # its child results before returning a result to its coordinator.
            # For the root node assignee and coordinator are the same Manager.
            receiver_agent_id=parent.assignee_agent_id,
            body="All required child tasks are complete; submit a task.summary.",
            payload=summary_payload,
            idempotency_key=f"{parent.id}:task.summary.request:round-{coordination['round']}",
        )
        return [
            self._append_event(
                db,
                root_task_id=parent.root_task_id,
                task_id=parent.id,
                event_type="task.summarizing",
                actor_agent_id=actor_agent_id,
                target_agent_id=parent.coordinator_agent_id,
                summary="required children complete; summary requested",
                detail={},
            )
        ]

    @staticmethod
    def _active_children(db: Session, parent_id: str) -> list[models.AgentTaskNode]:
        """Return only the newest leaf in each immutable revision chain."""
        children = list(
            db.scalars(
                select(models.AgentTaskNode)
                .where(models.AgentTaskNode.parent_task_id == parent_id)
                .order_by(models.AgentTaskNode.created_at, models.AgentTaskNode.id)
            )
        )
        superseded_ids = {
            child.revision_of_task_id
            for child in children
            if child.revision_of_task_id is not None
        }
        return [child for child in children if child.id not in superseded_ids]

    def _locked_context(
        self, db: Session, message: AgentProtocolMessage
    ) -> tuple[models.TaskRun, models.AgentTaskNode]:
        """Resolve IDs, then validate against locked, fresh ORM rows."""
        # Preserve caller-owned updates made in this same transaction before
        # populate_existing replaces identity-map state with the database row.
        db.flush()
        run_id = db.scalar(select(models.TaskRun.id).where(
            models.TaskRun.id == message.root_task_id,
            models.TaskRun.source == "agentteams",
        ))
        if run_id is None:
            raise CollaborationConflict("unknown_root_task")
        node_id = db.scalar(select(models.AgentTaskNode.id).where(
            models.AgentTaskNode.id == message.task_id,
        ))
        if node_id is None:
            raise CollaborationConflict("invalid_task_scope")
        root, node = self._lock_root_and_node(db, message.root_task_id, node_id)
        run = db.scalar(
            select(models.TaskRun)
            .where(models.TaskRun.id == run_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if run is None or run.source != "agentteams":
            raise CollaborationConflict("unknown_root_task")
        if node.root_task_id != run.id or root.root_task_id != run.id:
            raise CollaborationConflict("invalid_task_scope")
        if node.trace_id != run.trace_id or message.trace_id != run.trace_id:
            raise CollaborationConflict("invalid_trace")
        if message.parent_task_id != node.parent_task_id:
            raise CollaborationConflict("invalid_parent_task")
        return run, node

    def _authorize(
        self,
        db: Session,
        run: models.TaskRun,
        node: models.AgentTaskNode,
        matrix_sender: str,
        message: AgentProtocolMessage,
        *,
        roles: set[str],
    ) -> None:
        employee = self._employee_for_matrix_sender(db, matrix_sender)
        if employee is None:
            raise CollaborationConflict("matrix_sender_mismatch")
        if employee.employee_no != message.sender:
            raise CollaborationConflict("matrix_sender_mismatch")
        if not self._is_team_member(db, run.team_id, employee.employee_no):
            raise CollaborationConflict("invalid_sender")
        allowed = set()
        if "assignee" in roles:
            allowed.add(node.assignee_agent_id)
        if "coordinator" in roles:
            allowed.add(node.coordinator_agent_id)
        if message.sender not in allowed:
            raise CollaborationConflict("unauthorized_sender")

    def _validated_next_coordination(
        self, parent: models.AgentTaskNode, payload: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Validate Manager-round metadata before the task tree is mutated."""
        if parent.parent_task_id is not None:
            if parent.status == "summarizing":
                raise CollaborationConflict("follow_up_requires_root_manager")
            return None
        existing = self._coordination(parent)
        if parent.status == "planning":
            next_round = 1
        elif parent.status == "summarizing":
            next_round = existing["round"] + 1
        else:
            return None
        if next_round > 1 + self.max_rounds:
            raise CollaborationConflict("max_rounds_exceeded")
        selected = payload.get("selected_skill_ids", existing["selected_skill_ids"])
        if not isinstance(selected, list) or not all(isinstance(item, str) and item.strip() for item in selected):
            raise CollaborationConflict("invalid_selected_skill_ids")
        if len(selected) != len(set(selected)):
            raise CollaborationConflict("invalid_selected_skill_ids")
        return {"round": next_round, "selected_skill_ids": list(selected), "team_catalog_version": existing["team_catalog_version"]}

    @staticmethod
    def _coordination(node: models.AgentTaskNode | None) -> dict[str, Any]:
        raw = dict(node.input_payload or {}).get("coordination") if node is not None else None
        if not isinstance(raw, Mapping):
            return {"round": 0, "selected_skill_ids": [], "team_catalog_version": 0}
        round_number = raw.get("round", 0)
        selected = raw.get("selected_skill_ids", [])
        catalog_version = raw.get("team_catalog_version", 0)
        if isinstance(round_number, bool) or not isinstance(round_number, int) or round_number < 0:
            round_number = 0
        if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
            selected = []
        if isinstance(catalog_version, bool) or not isinstance(catalog_version, int) or catalog_version < 0:
            catalog_version = 0
        return {"round": round_number, "selected_skill_ids": list(selected), "team_catalog_version": catalog_version}

    @staticmethod
    def _task_thinking_enabled(root: models.AgentTaskNode | None) -> bool:
        runtime_options = (
            dict(root.input_payload or {}).get("_agentteams_runtime")
            if root is not None
            else None
        )
        if not isinstance(runtime_options, Mapping):
            return True
        value = runtime_options.get("thinking_enabled", True)
        return value if isinstance(value, bool) else True

    @staticmethod
    def _root_node_count(db: Session, root_task_id: str) -> int:
        return db.scalar(
            select(func.count(models.AgentTaskNode.id)).where(
                models.AgentTaskNode.root_task_id == root_task_id
            )
        ) or 0

    def _parent_chain_assignees(
        self, db: Session, parent: models.AgentTaskNode
    ) -> set[str]:
        assignees: set[str] = set()
        current: models.AgentTaskNode | None = parent
        while current is not None:
            assignees.add(current.assignee_agent_id)
            if current.parent_task_id is None:
                break
            current = db.get(models.AgentTaskNode, current.parent_task_id)
        return assignees

    def _has_semantic_duplicate(
        self, db: Session, parent: models.AgentTaskNode, subtasks: list[dict[str, Any]]
    ) -> bool:
        existing = list(db.scalars(select(models.AgentTaskNode).where(
            models.AgentTaskNode.parent_task_id == parent.id
        )))
        fingerprints = {self._node_semantic_fingerprint(db, node) for node in existing}
        candidates = [self._subtask_semantic_fingerprint(item) for item in subtasks]
        return len(candidates) != len(set(candidates)) or bool(fingerprints.intersection(candidates))

    @staticmethod
    def _subtask_semantic_fingerprint(subtask: Mapping[str, Any]) -> str:
        material_hash = subtask.get("material_hash")
        if not isinstance(material_hash, str):
            material_hash = AgentTeamsCollaborationService._material_hash(subtask["material"])
        payload = {
            "assignee": subtask["assignee_agent_id"], "instruction": " ".join(subtask["instruction"].casefold().split()),
            "capability": subtask["capability"].casefold(), "required": subtask["required"],
            "depends_on": sorted(subtask["depends_on"]),
            "material_hash": material_hash,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _node_semantic_fingerprint(self, db: Session, node: models.AgentTaskNode) -> str:
        dependency_ids = list(db.scalars(select(models.AgentTaskDependency.depends_on_task_id).where(
            models.AgentTaskDependency.task_id == node.id
        )))
        dependencies = [db.get(models.AgentTaskNode, item) for item in dependency_ids]
        return self._subtask_semantic_fingerprint({
            "assignee_agent_id": node.assignee_agent_id, "instruction": node.instruction,
            "capability": node.capability, "required": node.required,
            "depends_on": [item.client_task_key for item in dependencies if item is not None],
            "material": dict(node.input_payload or {}),
        })

    @staticmethod
    def _capability_allowed_for_owner(
        db: Session,
        *,
        employee_id: str,
        team_id: str,
        plugin_id: str,
        action: str,
        owner_human_no: str,
    ) -> bool:
        """Use the same policy evaluator as Gateway for a task owner's twin."""
        if not owner_human_no:
            return True
        executor = resolve_identity(db, employee_id)
        permission_subject = resolve_twin_identity(db, owner_human_no)
        plugin = db.get(models.Plugin, plugin_id)
        if executor is None or permission_subject is None or plugin is None:
            return False
        result = evaluate(
            db,
            executor,
            ResourceRef(type=plugin.type, id=plugin.id, data_level=plugin.data_level),
            action,
            context={"team_id": team_id},
            permission_subject=permission_subject,
        )
        return result.decision != DECISION_DENY

    def _team_capability_catalog(
        self, db: Session, team_id: str, *, owner_human_no: str = ""
    ) -> list[dict[str, Any]]:
        """Expose the Team, twin permission and VE capability intersection."""
        if self.capability_catalog_source == "configured" and config.agentteams_mode() == "agentteams_strict":
            return self._strict_team_capability_catalog(
                db, team_id, owner_human_no=owner_human_no
            )
        team = db.get(models.Team, team_id)
        if team is None:
            raise CollaborationConflict("unknown_team")
        members = list(db.scalars(
            select(models.TeamMember).where(models.TeamMember.team_id == team_id)
        ))
        member_ids = {team.leader_employee_id, *(member.employee_id for member in members)}
        structural_capabilities: dict[str, set[str]] = {}
        for member in members:
            raw_capabilities = member.capabilities or []
            if (
                not isinstance(raw_capabilities, list)
                or len(raw_capabilities) > 32
                or any(
                    not isinstance(value, str) or not value.strip() or len(value.strip()) > 128
                    for value in raw_capabilities
                )
            ):
                raise CollaborationConflict("invalid_team_hierarchy")
            structural_capabilities[member.employee_id] = {
                value.strip() for value in raw_capabilities
            }
        team_capability_ids = set(db.scalars(
            select(models.TeamPluginGrant.plugin_id).where(
                models.TeamPluginGrant.team_id == team_id,
                models.TeamPluginGrant.decision_mode == "allow",
            )
        ))
        catalog: list[dict[str, Any]] = []
        for employee_id in sorted(member_ids):
            employee = db.get(models.DigitalEmployee, employee_id)
            if employee is None or employee.status != "active" or not self._runtime_ref(employee):
                continue
            grants = list(db.scalars(
                select(models.EmployeePluginGrant).where(
                    models.EmployeePluginGrant.employee_id == employee_id,
                    models.EmployeePluginGrant.decision_mode == "allow",
                )
            ))
            capability_ids = sorted(structural_capabilities.get(employee_id, set()).union({
                grant.plugin_id for grant in grants
                if grant.plugin_id in team_capability_ids
                and self._capability_allowed_for_owner(
                    db,
                    employee_id=employee_id,
                    team_id=team_id,
                    plugin_id=grant.plugin_id,
                    action=grant.action,
                    owner_human_no=owner_human_no,
                )
            }))
            catalog.append({
                "agent_id": employee.employee_no,
                "name": employee.name,
                "department": employee.department,
                "runtime_bound": True,
                "capabilities": capability_ids,
                "tool_summaries": capability_ids,
            })
        return catalog

    def _strict_team_capability_catalog(
        self, db: Session, team_id: str, *, owner_human_no: str = ""
    ) -> list[dict[str, Any]]:
        """Build a public capability view from the validated strict runtime only."""
        try:
            runtime = load_strict_runtime_config()
        except ValueError as exc:
            raise CollaborationConflict("invalid_runtime_config") from exc
        if runtime.team_id != team_id:
            raise CollaborationConflict("runtime_team_mismatch")

        team = db.get(models.Team, team_id)
        if team is None:
            raise CollaborationConflict("unknown_team")
        member_ids = {team.leader_employee_id}
        members = list(db.scalars(
            select(models.TeamMember).where(models.TeamMember.team_id == team_id)
        ))
        member_ids.update(member.employee_id for member in members)
        structural_capabilities = {
            member.employee_id: {
                capability.strip()
                for capability in (member.capabilities or [])
                if isinstance(capability, str) and capability.strip()
            }
            for member in members
        }

        catalog: list[dict[str, Any]] = []
        for identity in sorted(runtime.agents.values(), key=lambda item: item.agent_id or ""):
            agent_id = identity.agent_id
            if not agent_id:
                raise CollaborationConflict("invalid_runtime_config")
            raw_entry = runtime.team_catalog.get(agent_id, {})
            if not isinstance(raw_entry, Mapping):
                raise CollaborationConflict("invalid_runtime_config")
            raw_capabilities = raw_entry.get("capabilities", [])
            if (
                not isinstance(raw_capabilities, list)
                or len(raw_capabilities) > 32
                or any(not isinstance(value, str) or not value.strip() or len(value.strip()) > 128 for value in raw_capabilities)
            ):
                raise CollaborationConflict("invalid_runtime_config")
            capabilities = sorted({value.strip() for value in raw_capabilities})
            if len(capabilities) != len(raw_capabilities):
                raise CollaborationConflict("invalid_runtime_config")

            employee = db.get(models.DigitalEmployee, agent_id)
            if (
                employee is None
                or employee.status != "active"
                or employee.runtime_type != "agentteams"
                or agent_id not in member_ids
                or matrix_user_id_for_runtime(employee.runtime_ref) != identity.mxid
            ):
                continue
            if owner_human_no:
                permitted: list[str] = []
                tool_bindings: dict[str, list[tuple[str, str]]] = {}
                for tool in identity.safe_tool_catalog:
                    required_capability = str(tool.get("required_capability") or "").strip()
                    plugin_id = str(tool.get("plugin_id") or "").strip()
                    action = str(tool.get("action") or "read").strip()
                    if required_capability and plugin_id and action:
                        tool_bindings.setdefault(required_capability, []).append((plugin_id, action))
                for capability in capabilities:
                    # Leader coordination capabilities describe the persisted
                    # organisation graph, not a plugin invocation.  They must
                    # survive personal/team/global Tool filtering; otherwise a
                    # later catalog refresh can silently remove a Leader and
                    # make a valid Manager follow-up impossible.
                    if capability in structural_capabilities.get(agent_id, set()) or any(
                        self._capability_allowed_for_owner(
                            db,
                            employee_id=agent_id,
                            team_id=team_id,
                            plugin_id=plugin_id,
                            action=action,
                            owner_human_no=owner_human_no,
                        )
                        for plugin_id, action in tool_bindings.get(capability, [])
                    ):
                        permitted.append(capability)
                capabilities = permitted
            catalog.append({
                "agent_id": agent_id,
                "name": employee.name,
                "department": employee.department,
                "runtime_bound": True,
                "capabilities": capabilities,
                "tool_summaries": capabilities,
            })
        return catalog

    def _team_relationship_graph(
        self,
        db: Session,
        team_id: str,
        agent_id: str,
        *,
        catalog: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Project the persisted hierarchy; Matrix may confirm but never widen it."""
        hierarchy = self._team_hierarchy(db, team_id)
        current_catalog = catalog
        if current_catalog is None:
            current_catalog = self._team_capability_catalog(db, team_id)
        capabilities_by_agent: dict[str, dict[str, object]] = {
            str(item.get("agent_id")): {
                "capabilities": sorted({
                    capability
                    for capability in item.get("capabilities", [])
                    if isinstance(capability, str)
                })
            }
            for item in current_catalog
            if isinstance(item, Mapping)
            and isinstance(item.get("agent_id"), str)
            and isinstance(item.get("capabilities"), list)
        }
        if config.agentteams_mode() == "agentteams_strict":
            try:
                runtime = load_strict_runtime_config()
            except ValueError as exc:
                if self.capability_catalog_source == "configured":
                    raise CollaborationConflict("invalid_runtime_config") from exc
            else:
                if runtime.team_id == team_id:
                    try:
                        runtime_hierarchy = TeamHierarchy.from_relationships(
                            manager_id=hierarchy.manager_id,
                            member_ids=set(runtime.team_relationships),
                            relationships=runtime.team_relationships,
                        )
                    except TeamHierarchyError as exc:
                        raise CollaborationConflict("invalid_runtime_config") from exc
                    if (
                        runtime_hierarchy.member_ids != hierarchy.member_ids
                        or any(
                            set(runtime_hierarchy.direct_reports_of(member_id))
                            != set(hierarchy.direct_reports_of(member_id))
                            for member_id in hierarchy.member_ids
                        )
                    ):
                        raise CollaborationConflict("runtime_hierarchy_mismatch")
                    hierarchy = runtime_hierarchy
                elif self.capability_catalog_source == "configured":
                    raise CollaborationConflict("runtime_team_mismatch")
        try:
            return hierarchy.project_for(agent_id, capabilities_by_agent)
        except TeamHierarchyError as exc:
            raise CollaborationConflict(str(exc)) from None

    def _team_hierarchy(self, db: Session, team_id: str) -> TeamHierarchy:
        team = db.get(models.Team, team_id)
        if team is None:
            raise CollaborationConflict("unknown_team")
        members = list(db.scalars(
            select(models.TeamMember).where(models.TeamMember.team_id == team_id)
        ))
        if not members or len({member.employee_id for member in members}) != len(members):
            raise CollaborationConflict("invalid_team_hierarchy")
        parent_by_agent = {
            member.employee_id: member.reports_to_employee_id for member in members
        }
        relationships: dict[str, dict[str, object]] = {}
        for member in members:
            employee = db.get(models.DigitalEmployee, member.employee_id)
            if employee is None:
                raise CollaborationConflict("invalid_team_hierarchy")
            relationships[member.employee_id] = {
                "role": employee.name,
                "reports_to": (
                    [member.reports_to_employee_id]
                    if member.reports_to_employee_id
                    else []
                ),
                "direct_reports": sorted(
                    agent_id
                    for agent_id, parent_id in parent_by_agent.items()
                    if parent_id == member.employee_id
                ),
            }
        try:
            hierarchy = TeamHierarchy.from_relationships(
                manager_id=team.leader_employee_id,
                member_ids=set(parent_by_agent),
                relationships=relationships,
            )
        except TeamHierarchyError as exc:
            raise CollaborationConflict(str(exc)) from None
        if any(
            member.role != hierarchy.level_of(member.employee_id)
            for member in members
        ):
            raise CollaborationConflict("invalid_team_hierarchy")
        return hierarchy

    @staticmethod
    def _validate_subtask(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise CollaborationConflict("invalid_subtask")
        key = raw.get("client_task_key")
        title = raw.get("title")
        instruction = raw.get("instruction")
        assignee = raw.get("assignee_agent_id")
        dependencies = raw.get("depends_on", [])
        material = raw.get("material", raw.get("input_payload", {}))
        required = raw.get("required", True)
        strict_mode = config.agentteams_mode() == "agentteams_strict"
        if strict_mode and ("required_capability" not in raw or not isinstance(raw.get("required_capability"), str) or not raw.get("required_capability").strip()):
            raise CollaborationConflict("required_capability_required")
        capability = raw.get("required_capability", raw.get("capability", ""))
        node_kind = raw.get("node_kind", "worker_task")
        if not all(isinstance(value, str) and value.strip() for value in (key, title, instruction, assignee)):
            raise CollaborationConflict("invalid_subtask")
        if not isinstance(dependencies, list) or not all(isinstance(value, str) and value for value in dependencies):
            raise CollaborationConflict("invalid_subtask")
        if (
            not isinstance(material, Mapping)
            or not isinstance(required, bool)
            or not isinstance(capability, str)
            or node_kind not in {"team_assignment", "worker_task"}
        ):
            raise CollaborationConflict("invalid_subtask")
        if not _SAFE_CLIENT_TASK_KEY_PATTERN.fullmatch(key) or any(
            not _SAFE_CLIENT_TASK_KEY_PATTERN.fullmatch(value) for value in dependencies
        ):
            raise CollaborationConflict("invalid_client_task_key")
        if not all((
            AgentTeamsCollaborationService._is_safe_cross_agent_text(title, _MAX_SUBTASK_TITLE_LENGTH),
            AgentTeamsCollaborationService._is_safe_cross_agent_text(instruction, _MAX_SUBTASK_INSTRUCTION_LENGTH),
            AgentTeamsCollaborationService._is_safe_cross_agent_text(capability, _MAX_SUBTASK_CAPABILITY_LENGTH),
        )):
            raise CollaborationConflict("unsafe_subtask_metadata")
        normalized_material = AgentTeamsCollaborationService._normalize_material(material)
        material_hash = AgentTeamsCollaborationService._canonical_material_hash(normalized_material)
        return {
            "client_task_key": key,
            "title": title,
            "instruction": instruction,
            "assignee_agent_id": assignee,
            "depends_on": list(dependencies),
            "material": normalized_material,
            "material_hash": material_hash,
            "required": required,
            "capability": capability,
            "node_kind": node_kind,
            "requires_capability": strict_mode or "required_capability" in raw,
        }

    @staticmethod
    def _assert_acyclic(subtasks: Iterable[Mapping[str, Any]]) -> None:
        graph = {item["client_task_key"]: set(item["depends_on"]) for item in subtasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise CollaborationConflict("dependency_cycle")
            if key in visited:
                return
            visiting.add(key)
            for dependency in graph[key]:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in graph:
            visit(key)

    @staticmethod
    def _transition(node: models.AgentTaskNode, next_status: str) -> None:
        if next_status not in ALLOWED.get(node.status, set()):
            raise CollaborationConflict("invalid_transition")
        node.status = next_status

    def _event_for_message(
        self,
        db: Session,
        node: models.AgentTaskNode,
        message: AgentProtocolMessage,
        event_type: str,
        summary: str,
    ) -> models.AgentTaskEvent:
        return self._append_event(
            db,
            root_task_id=node.root_task_id,
            task_id=node.id,
            event_type=event_type,
            actor_agent_id=message.sender,
            target_agent_id=node.assignee_agent_id,
            summary=summary,
            detail=dict(message.payload),
        )

    @staticmethod
    def _append_event(
        db: Session,
        *,
        root_task_id: str,
        task_id: str,
        event_type: str,
        actor_agent_id: str,
        target_agent_id: str | None,
        summary: str,
        detail: dict,
    ) -> models.AgentTaskEvent:
        previous = db.scalar(
            select(func.max(models.AgentTaskEvent.event_seq)).where(
                models.AgentTaskEvent.root_task_id == root_task_id
            )
        )
        event = models.AgentTaskEvent(
            root_task_id=root_task_id,
            task_id=task_id,
            event_seq=(previous or 0) + 1,
            event_type=event_type,
            actor_agent_id=actor_agent_id,
            target_agent_id=target_agent_id,
            summary=summary,
            detail=detail,
        )
        db.add(event)
        # The next append in this transaction must observe this sequence number.
        # Flushing preserves append-only order without taking ownership of commit.
        db.flush()
        return event

    @staticmethod
    def _queue_outbound(
        db: Session,
        *,
        root_task_id: str,
        task_id: str,
        parent_task_id: str | None,
        message_type: str,
        sender_agent_id: str,
        receiver_agent_id: str,
        body: str,
        payload: dict,
        idempotency_key: str,
    ) -> models.AgentMessage:
        outbound = models.AgentMessage(
            id=f"AM-{uuid4().hex}",
            root_task_id=root_task_id,
            task_id=task_id,
            parent_task_id=parent_task_id,
            message_type=message_type,
            sender_agent_id=sender_agent_id,
            receiver_agent_id=receiver_agent_id,
            body=body,
            payload=payload,
            matrix_room_id="",
            matrix_event_id=None,
            idempotency_key=idempotency_key,
            direction="outbound",
            delivery_status="pending",
        )
        db.add(outbound)
        return outbound

    @staticmethod
    def _record_protocol(db: Session, message: AgentProtocolMessage) -> None:
        db.add(models.AgentMessage(
            id=f"AM-{uuid4().hex}",
            root_task_id=message.root_task_id,
            task_id=message.task_id,
            parent_task_id=message.parent_task_id,
            message_type=message.type,
            sender_agent_id=message.sender,
            receiver_agent_id=message.receiver,
            body=AgentTeamsCollaborationService._message_text(message.payload),
            payload=message.model_dump(mode="json"),
            matrix_room_id="",
            matrix_event_id=None,
            idempotency_key=AgentTeamsCollaborationService._inbound_idempotency_key(message.idempotency_key),
            direction="inbound",
            delivery_status="received",
        ))

    def _reserve_protocol(self, db: Session, message: AgentProtocolMessage) -> bool:
        """Atomically claim the inbound idempotency key before any state change."""
        try:
            with db.begin_nested():
                self._record_protocol(db, message)
                db.flush()
        except IntegrityError:
            return False
        return True

    @staticmethod
    def _lock_root_and_node(
        db: Session, root_task_id: str, node_id: str
    ) -> tuple[models.AgentTaskNode, models.AgentTaskNode]:
        # Always lock root before the current node.  populate_existing prevents a
        # prior identity-map read from authorizing a stale transition or round.
        root = db.scalar(
            select(models.AgentTaskNode)
            .where(models.AgentTaskNode.id == root_task_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if root is None:
            raise CollaborationConflict("unknown_root_task")
        if node_id == root_task_id:
            return root, root
        node = db.scalar(
            select(models.AgentTaskNode)
            .where(models.AgentTaskNode.id == node_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if node is None:
            raise CollaborationConflict("invalid_task_scope")
        return root, node

    def _prevalidate_delegate_subtasks(self, payload: Mapping[str, Any]) -> None:
        """Reject unsafe material before persisting the inbound protocol envelope."""
        raw_subtasks = payload.get("subtasks")
        if not isinstance(raw_subtasks, list) or not 1 <= len(raw_subtasks) <= self.max_children:
            return
        for raw_subtask in raw_subtasks:
            self._validate_subtask(raw_subtask)

    @staticmethod
    def _normalize_material(
        value: Any,
        depth: int = 0,
        *,
        budget: _MaterialBudget | None = None,
        active_container_ids: set[int] | None = None,
    ) -> Any:
        try:
            budget = budget or _MaterialBudget()
            active_container_ids = active_container_ids if active_container_ids is not None else set()
            if depth > _MAX_MATERIAL_DEPTH:
                raise CollaborationConflict("material_too_large")
            budget.consume_node()
            if value is None:
                budget.consume_bytes(4)
                return value
            if type(value) is bool:
                budget.consume_bytes(5 if value else 4)
                return value
            if type(value) is int:
                if value.bit_length() > _MAX_MATERIAL_INTEGER_BITS:
                    raise CollaborationConflict("material_too_large")
                budget.consume_bytes(len(str(value).encode("utf-8")))
                return value
            if type(value) is float:
                if not math.isfinite(value):
                    raise CollaborationConflict("invalid_material")
                budget.consume_bytes(len(repr(value).encode("utf-8")))
                return value
            if type(value) is str:
                if len(value) > _MAX_MATERIAL_STRING_LENGTH:
                    raise CollaborationConflict("material_too_large")
                budget.consume_bytes(len(value.encode("utf-8")) + 2)
                return value
            if type(value) is list:
                if len(value) > _MAX_MATERIAL_ITEMS:
                    raise CollaborationConflict("material_too_large")
                container_id = id(value)
                if container_id in active_container_ids:
                    raise CollaborationConflict("invalid_material")
                active_container_ids.add(container_id)
                try:
                    budget.consume_bytes(2 + max(0, len(value) - 1))
                    return [
                        AgentTeamsCollaborationService._normalize_material(
                            item, depth + 1, budget=budget,
                            active_container_ids=active_container_ids,
                        )
                        for item in value
                    ]
                finally:
                    active_container_ids.remove(container_id)
            if type(value) is dict:
                if len(value) > _MAX_MATERIAL_ITEMS:
                    raise CollaborationConflict("material_too_large")
                container_id = id(value)
                if container_id in active_container_ids:
                    raise CollaborationConflict("invalid_material")
                active_container_ids.add(container_id)
                normalized: dict[str, Any] = {}
                try:
                    budget.consume_bytes(2 + max(0, len(value) - 1))
                    for key, item in value.items():
                        if type(key) is not str:
                            raise CollaborationConflict("invalid_material")
                        if len(key) > _MAX_MATERIAL_KEY_LENGTH:
                            raise CollaborationConflict("material_too_large")
                        budget.consume_bytes(len(key.encode("utf-8")) + 3)
                        normalized[key] = AgentTeamsCollaborationService._normalize_material(
                            item, depth + 1, budget=budget,
                            active_container_ids=active_container_ids,
                        )
                    return normalized
                finally:
                    active_container_ids.remove(container_id)
            raise CollaborationConflict("invalid_material")
        except CollaborationConflict:
            raise
        except (ValueError, TypeError, RecursionError, OverflowError) as error:
            raise CollaborationConflict("invalid_material") from error

    @staticmethod
    def _material_hash(material: Any) -> str:
        return AgentTeamsCollaborationService._canonical_material_hash(
            AgentTeamsCollaborationService._normalize_material(material)
        )

    @staticmethod
    def _canonical_material_hash(normalized: Any) -> str:
        try:
            canonical = json.dumps(
                normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            )
            if len(canonical.encode("utf-8")) > _MAX_MATERIAL_CANONICAL_BYTES:
                raise CollaborationConflict("material_too_large")
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError, OverflowError) as error:
            raise CollaborationConflict("invalid_material") from error
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_safe_cross_agent_text(value: str, limit: int) -> bool:
        return len(value) <= limit and AgentTeamsCollaborationService._safe_tool_text(value) == value

    @staticmethod
    def _safe_cross_agent_text(value: Any, limit: int) -> str:
        if not isinstance(value, str):
            return "[redacted]"
        safe = AgentTeamsCollaborationService._safe_tool_text(value)
        return safe if len(value) <= limit and safe == value else "[redacted]"

    @staticmethod
    def _safe_client_task_key(value: Any) -> str:
        return value if isinstance(value, str) and _SAFE_CLIENT_TASK_KEY_PATTERN.fullmatch(value) else "[redacted]"

    @staticmethod
    def _protocol_seen(db: Session, idempotency_key: str) -> bool:
        return db.scalar(
            select(models.AgentMessage.id).where(
                models.AgentMessage.idempotency_key
                == AgentTeamsCollaborationService._inbound_idempotency_key(idempotency_key),
                models.AgentMessage.direction == "inbound",
            )
        ) is not None

    @staticmethod
    def _inbound_idempotency_key(protocol_key: str) -> str:
        return f"{_INBOUND_IDEMPOTENCY_PREFIX}{protocol_key}"

    def _employee_for_matrix_sender(
        self, db: Session, matrix_sender: str
    ) -> models.DigitalEmployee | None:
        matches = [
            employee
            for employee in db.scalars(select(models.DigitalEmployee))
            if self._matrix_user_id_for_runtime(self._runtime_ref(employee)) == matrix_sender
        ]
        if len(matches) > 1:
            raise CollaborationConflict("matrix_sender_ambiguous")
        return matches[0] if matches else None

    @staticmethod
    def _matrix_user_id_for_runtime(runtime_ref: str | None) -> str | None:
        return matrix_user_id_for_runtime(runtime_ref)

    @staticmethod
    def _runtime_ref(employee: models.DigitalEmployee) -> str | None:
        runtime_ref = (employee.runtime_ref or "").strip()
        return runtime_ref or None

    def _is_team_member(self, db: Session, team: models.Team | str, employee_id: str) -> bool:
        team_id = team.id if isinstance(team, models.Team) else team
        if isinstance(team, models.Team) and team.leader_employee_id == employee_id:
            return True
        if not isinstance(team, models.Team):
            stored_team = db.get(models.Team, team_id)
            if stored_team is not None and stored_team.leader_employee_id == employee_id:
                return True
        return db.scalar(
            select(models.TeamMember.id).where(
                models.TeamMember.team_id == team_id,
                models.TeamMember.employee_id == employee_id,
            )
        ) is not None

    @staticmethod
    def _message_text(payload: Mapping[str, Any]) -> str:
        for key in ("summary", "text", "result", "message"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        return ""

    @staticmethod
    def _node_data(node: models.AgentTaskNode) -> dict:
        return {
            "id": node.id,
            "root_task_id": node.root_task_id,
            "parent_task_id": node.parent_task_id,
            "client_task_key": node.client_task_key,
            "title": node.title,
            "instruction": node.instruction,
            "coordinator_agent_id": node.coordinator_agent_id,
            "assignee_agent_id": node.assignee_agent_id,
            "node_kind": node.node_kind,
            "revision_of_task_id": node.revision_of_task_id,
            "depth": node.depth,
            "required": node.required,
            "status": node.status,
            "progress": node.progress,
            "input_payload": dict(node.input_payload or {}),
            "output_payload": dict(node.output_payload or {}),
            "error_code": node.error_code,
            "error_message": node.error_message,
        }

    @staticmethod
    def _root(db: Session, root_task_id: str) -> tuple[models.TaskRun, models.AgentTaskNode]:
        run = db.get(models.TaskRun, root_task_id)
        root = db.get(models.AgentTaskNode, root_task_id)
        if run is None or run.source != "agentteams" or root is None or root.root_task_id != run.id:
            raise CollaborationConflict("unknown_root_task")
        return run, root


def subtasks_by_key(subtasks: Iterable[Mapping[str, Any]], key: str) -> Mapping[str, Any]:
    return next(item for item in subtasks if item["client_task_key"] == key)


_DEFAULT_SERVICE = AgentTeamsCollaborationService()


def create_task(db: Session, **kwargs: Any) -> models.TaskRun:
    return _DEFAULT_SERVICE.create_task(db, **kwargs)


def apply_message(db: Session, **kwargs: Any) -> list[models.AgentTaskEvent]:
    return _DEFAULT_SERVICE.apply_message(db, **kwargs)


def build_graph(db: Session, root_task_id: str) -> dict:
    return _DEFAULT_SERVICE.build_graph(db, root_task_id)


def build_result(db: Session, root_task_id: str) -> dict:
    return _DEFAULT_SERVICE.build_result(db, root_task_id)
