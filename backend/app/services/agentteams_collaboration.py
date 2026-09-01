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


class CollaborationConflict(RuntimeError):
    """A protocol request violates collaboration ownership or state rules."""


ALLOWED: dict[str, set[str]] = {
    "planning": {"waiting_children", "failed", "timed_out", "cancelled"},
    "assigned": {"accepted", "failed", "timed_out", "cancelled"},
    "accepted": {"running", "failed", "timed_out", "cancelled"},
    "running": {"waiting_children", "completed", "failed", "timed_out", "cancelled"},
    "waiting_children": {"summarizing", "failed", "timed_out", "cancelled"},
    "summarizing": {"completed", "failed", "timed_out", "cancelled"},
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
_URL_PATTERN = re.compile(r"(?i)\b(?:https?|wss?)://[^\s\"'<>]+")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:password|passwd|pwd|token|secret|api[_-]?key|private[_-]?key|access[_-]?key|"
    r"authorization|credential|cookie|header|endpoint)\s*[:=]\s*[^,\s]+"
)
_AUTH_VALUE_PATTERN = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/-]+=*")
_STACK_TRACE_PATTERN = re.compile(
    r"(?i)(?:traceback\s*\(|stack trace|exception\s*(?::|in thread)|(?:^|\n)\s*at\s+[\w.$<>]+\(.*:\d+\))"
)


class AgentTeamsCollaborationService:
    def __init__(self, max_depth: int = 3, max_children: int = 5):
        self.max_depth = max_depth
        self.max_children = max_children

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
            payload={"title": title, "instruction": instruction, "material": dict(material)},
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
        for subtask in subtasks:
            assignee = db.get(models.DigitalEmployee, subtask["assignee_agent_id"])
            if assignee is None or not self._is_team_member(db, run.team_id, subtask["assignee_agent_id"]):
                raise CollaborationConflict("invalid_team_assignee")
            if not self._runtime_ref(assignee):
                raise CollaborationConflict("missing_runtime_ref")

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
                status="assigned",
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
            self._queue_outbound(
                db,
                root_task_id=child.root_task_id,
                task_id=child.id,
                parent_task_id=child.parent_task_id,
                message_type="task.assign",
                sender_agent_id=child.coordinator_agent_id,
                receiver_agent_id=child.assignee_agent_id,
                body=child.instruction,
                payload={
                    "title": child.title,
                    "client_task_key": child.client_task_key,
                    "depends_on": [
                        key for key in subtasks_by_key(subtasks, child.client_task_key)["depends_on"]
                    ],
                },
                idempotency_key=f"{child.id}:task.assign",
            )
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
        if not node.required:
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
        try:
            with self._gateway_transaction_guard(db):
                result = invoke_plugin(
                    db,
                    employee_id=node.assignee_agent_id,
                    plugin_id=str(message.payload["plugin_id"]),
                    action=str(message.payload["action"]),
                    params=dict(message.payload.get("params") or {}),
                    trace_id=node.trace_id,
                    knowledge_base_id=message.payload.get("knowledge_base_id"),
                    execution_context={
                        "task_id": node.id,
                        "request": root.instruction,
                        "subtask": node.instruction,
                        "collaboration_summary": message.payload.get("context", ""),
                    },
                )
            result_payload = self._safe_tool_result_payload(result)
        except HTTPException as error:
            result_payload = self._denied_tool_result_payload(error)
            event_type = "tool.denied"
            summary = "tool request denied"
        except Exception:
            # Unexpected gateway failures must not expose adapter details to Matrix
            # or alter the task lifecycle.  The append-only event remains a safe
            # operational signal for the platform.
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
                },
            )
        ]

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
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) <= _MAX_TOOL_RESULT_BYTES:
            return payload
        bounded = dict(payload)
        bounded["data"] = {"truncated": True}
        return bounded

    @staticmethod
    def _safe_audit_ids(value: Any) -> list[int]:
        values = value if isinstance(value, (list, tuple)) else [value]
        return [item for item in values[:_MAX_TOOL_RESULT_ITEMS] if isinstance(item, int) and not isinstance(item, bool)]

    @staticmethod
    def _is_sensitive_tool_field(key: str) -> bool:
        return _SECRET_FIELD_PATTERN.search(key) is not None

    @staticmethod
    def _safe_tool_text(value: str) -> str:
        sanitized = _URL_PATTERN.sub("[redacted]", value)
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
        """Keep gateway audit writes inside the caller's transaction boundary."""
        original_commit = db.commit
        db.commit = db.flush  # type: ignore[method-assign]
        try:
            yield
        finally:
            db.commit = original_commit  # type: ignore[method-assign]

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
        self._queue_outbound(
            db,
            root_task_id=parent.root_task_id,
            task_id=parent.id,
            parent_task_id=parent.parent_task_id,
            message_type="task.summary",
            sender_agent_id="platform",
            receiver_agent_id=parent.coordinator_agent_id,
            body="All required child tasks are complete; submit a task.summary.",
            payload={"request": "task.summary", "parent_task_id": parent.id},
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
        capability = raw.get("capability", "")
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
        """Resolve a stored worker name to its exact Matrix ID without fuzzy matching."""
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
