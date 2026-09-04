"""Persistent, local orchestration for recursive AgentTeams task collaboration.

The service deliberately has no Matrix client dependency.  It validates protocol
senders against the stored runtime reference and records outbound work in the
outbox for a later runner to deliver.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from . import config
from .agentteams_protocol import AgentProtocolMessage
from .gateway import invoke_plugin
from .knowledge_registry import plugin_id_for_level, resolve as resolve_knowledge_base


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
    "running": {"waiting_children", "completed", "failed", "timed_out", "cancelled"},
    "waiting_children": {"summarizing", "failed", "timed_out", "cancelled"},
    "summarizing": {"waiting_children", "completed", "failed", "timed_out", "cancelled"},
}
_INBOUND_IDEMPOTENCY_PREFIX = "in:"
_MAX_TOOL_RESULT_DEPTH = 4
_MAX_TOOL_RESULT_ITEMS = 20
_MAX_TOOL_RESULT_STRING_LENGTH = 512
_MAX_TOOL_RESULT_BYTES = 4096
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


class AgentTeamsCollaborationService:
    def __init__(
        self,
        max_depth: int = 3,
        max_children: int = 5,
        max_rounds: int | None = None,
        max_nodes: int | None = None,
    ):
        self.max_depth = max_depth
        self.max_children = max_children
        self.max_rounds = 3 if max_rounds is None else max_rounds
        self.max_nodes = 20 if max_nodes is None else max_nodes

    def create_task(
        self,
        db: Session,
        *,
        team_id: str,
        leader_agent_id: str,
        title: str,
        instruction: str,
        material: dict,
    ) -> models.TaskRun:
        team = db.get(models.Team, team_id)
        if team is None:
            raise CollaborationConflict("unknown_team")
        leader = db.get(models.DigitalEmployee, leader_agent_id)
        if leader is None:
            raise CollaborationConflict("unknown_leader")
        if not self._is_team_member(db, team, leader_agent_id):
            raise CollaborationConflict("invalid_team_leader")
        if not self._runtime_ref(leader):
            raise CollaborationConflict("missing_runtime_ref")
        if not isinstance(material, dict):
            raise CollaborationConflict("invalid_material")

        task_id = f"AT-{uuid4().hex}"
        run = models.TaskRun(
            id=task_id,
            team_id=team.id,
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
            depth=0,
            required=True,
            status="planning",
            progress=0,
            input_payload=dict(material),
            output_payload={},
        )
        db.add_all([run, root])
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
        self._queue_outbound(
            db,
            root_task_id=task_id,
            task_id=task_id,
            parent_task_id=None,
            message_type="task.submit",
            sender_agent_id="platform",
            receiver_agent_id=leader.employee_no,
            body=instruction,
            payload={
                "title": title,
                "instruction": instruction,
                "material": dict(material),
                "team_capability_catalog": self._team_capability_catalog(db, team.id),
            },
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
        run, node = self._validate_context(db, message)
        if self._protocol_seen(db, message.idempotency_key):
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
        elif message.type == "task.failed":
            self._authorize(db, run, node, matrix_sender, message, roles={"assignee"})
            events = self._apply_failed(db, node, message)
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

        self._record_protocol(db, message)
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
        total_nodes = db.scalar(
            select(func.count(models.AgentTaskNode.id)).where(
                models.AgentTaskNode.root_task_id == parent.root_task_id
            )
        ) or 0
        if total_nodes + len(subtasks) > self.max_nodes:
            raise CollaborationConflict("max_nodes_exceeded")
        coordination = self._validated_next_coordination(parent, message.payload)
        ancestor_assignees = self._parent_chain_assignees(db, parent)
        capability_catalog = {
            item["agent_id"]: set(item["capabilities"])
            for item in self._team_capability_catalog(db, run.team_id)
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
                detail={"client_task_keys": keys},
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
        self._transition(node, "completed")
        node.progress = 100
        node.output_payload = dict(message.payload)
        node.finished_at = datetime.now()
        events = [self._event_for_message(db, node, message, "task.completed", "task completed")]
        events.extend(self._release_ready_dependents(db, node))
        events.extend(self._maybe_request_parent_summary(db, node, message.sender))
        return events

    def _apply_failed(
        self, db: Session, node: models.AgentTaskNode, message: AgentProtocolMessage
    ) -> list[models.AgentTaskEvent]:
        self._transition(node, "failed")
        node.error_code = str(message.payload.get("error_code") or "failed")
        node.error_message = str(message.payload.get("error_message") or "")
        node.finished_at = datetime.now()
        events = [self._event_for_message(db, node, message, "task.failed", "task failed")]
        if node.required:
            events.extend(self._propagate_required_failure(db, node, message.sender))
        else:
            events.extend(self._maybe_request_parent_summary(db, node, message.sender))
        return events

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
        return [self._safe_node_result(item) for item in dependencies if item is not None]

    def _safe_child_results(self, db: Session, parent_id: str) -> list[dict[str, Any]]:
        children = list(
            db.scalars(
                select(models.AgentTaskNode)
                .where(models.AgentTaskNode.parent_task_id == parent_id)
                .order_by(models.AgentTaskNode.created_at, models.AgentTaskNode.id)
            )
        )
        return [self._safe_node_result(child) for child in children]

    def _safe_node_result(self, node: models.AgentTaskNode) -> dict[str, Any]:
        result: dict[str, Any] = {
            "client_task_key": node.client_task_key,
            "status": node.status,
            "result": self._sanitize_tool_result_data(dict(node.output_payload or {})),
        }
        if node.error_code:
            result["error_code"] = self._safe_tool_text(node.error_code)
        if node.error_message:
            result["error_message"] = self._safe_tool_text(node.error_message)
        return result

    def _queue_assignment(
        self,
        db: Session,
        child: models.AgentTaskNode,
        dependency_keys: list[str],
        *,
        dependency_results: list[dict[str, Any]] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "title": child.title,
            "client_task_key": child.client_task_key,
            "depends_on": dependency_keys,
        }
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
            body=child.instruction,
            payload=payload,
            idempotency_key=f"{child.id}:task.assign",
        )

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
        required_children = list(
            db.scalars(
                select(models.AgentTaskNode).where(
                    models.AgentTaskNode.parent_task_id == parent.id,
                    models.AgentTaskNode.required.is_(True),
                )
            )
        )
        if not required_children or any(item.status != "completed" for item in required_children):
            return []
        self._transition(parent, "summarizing")
        root = db.get(models.AgentTaskNode, parent.root_task_id)
        coordination = self._coordination(root) if root is not None else {"round": 0, "selected_skill_ids": []}
        self._queue_outbound(
            db,
            root_task_id=parent.root_task_id,
            task_id=parent.id,
            parent_task_id=parent.parent_task_id,
            message_type="task.summary",
            sender_agent_id="platform",
            receiver_agent_id=parent.coordinator_agent_id,
            body="All required child tasks are complete; submit a task.summary.",
            payload={
                "request": "task.summary",
                "parent_task_id": parent.id,
                "child_results": self._safe_child_results(db, parent.id),
                "current_round": coordination["round"],
                "remaining_limits": {
                    "rounds": max(0, self.max_rounds - coordination["round"]),
                    "nodes": max(0, self.max_nodes - self._root_node_count(db, parent.root_task_id)),
                    "children": self.max_children,
                    "depth": max(0, self.max_depth - parent.depth),
                },
            },
            idempotency_key=f"{parent.id}:task.summary.request",
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

    def _validate_context(
        self, db: Session, message: AgentProtocolMessage
    ) -> tuple[models.TaskRun, models.AgentTaskNode]:
        run = db.get(models.TaskRun, message.root_task_id)
        if run is None or run.source != "agentteams":
            raise CollaborationConflict("unknown_root_task")
        node = db.get(models.AgentTaskNode, message.task_id)
        if node is None or node.root_task_id != run.id:
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
        if next_round > self.max_rounds:
            raise CollaborationConflict("max_rounds_exceeded")
        selected = payload.get("selected_skill_ids", existing["selected_skill_ids"])
        if not isinstance(selected, list) or not all(isinstance(item, str) and item.strip() for item in selected):
            raise CollaborationConflict("invalid_selected_skill_ids")
        if len(selected) != len(set(selected)):
            raise CollaborationConflict("invalid_selected_skill_ids")
        return {"round": next_round, "selected_skill_ids": list(selected)}

    @staticmethod
    def _coordination(node: models.AgentTaskNode | None) -> dict[str, Any]:
        raw = dict(node.input_payload or {}).get("coordination") if node is not None else None
        if not isinstance(raw, Mapping):
            return {"round": 0, "selected_skill_ids": []}
        round_number = raw.get("round", 0)
        selected = raw.get("selected_skill_ids", [])
        if isinstance(round_number, bool) or not isinstance(round_number, int) or round_number < 0:
            round_number = 0
        if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
            selected = []
        return {"round": round_number, "selected_skill_ids": list(selected)}

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

    def _team_capability_catalog(self, db: Session, team_id: str) -> list[dict[str, Any]]:
        """Expose only active, runtime-bound current-team members and granted tools."""
        team = db.get(models.Team, team_id)
        if team is None:
            raise CollaborationConflict("unknown_team")
        member_ids = {team.leader_employee_id}
        member_ids.update(db.scalars(
            select(models.TeamMember.employee_id).where(models.TeamMember.team_id == team_id)
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
            capability_ids = sorted({grant.plugin_id for grant in grants})
            catalog.append({
                "agent_id": employee.employee_no,
                "name": employee.name,
                "department": employee.department,
                "runtime_bound": True,
                "capabilities": capability_ids,
                "tool_summaries": capability_ids,
            })
        return catalog

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
        capability = raw.get("required_capability", raw.get("capability", ""))
        if not all(isinstance(value, str) and value.strip() for value in (key, title, instruction, assignee)):
            raise CollaborationConflict("invalid_subtask")
        if not isinstance(dependencies, list) or not all(isinstance(value, str) and value for value in dependencies):
            raise CollaborationConflict("invalid_subtask")
        if not isinstance(material, Mapping) or not isinstance(required, bool) or not isinstance(capability, str):
            raise CollaborationConflict("invalid_subtask")
        return {
            "client_task_key": key,
            "title": title,
            "instruction": instruction,
            "assignee_agent_id": assignee,
            "depends_on": list(dependencies),
            "material": dict(material),
            "required": required,
            "capability": capability,
            "requires_capability": "required_capability" in raw,
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
