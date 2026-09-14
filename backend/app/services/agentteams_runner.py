"""Persistent transport post-office loop for AgentTeams collaboration.

The runner deliberately has no application-lifecycle side effects.  A later
lifecycle owner may call :meth:`run_forever`; direct tests use
:meth:`tick_once` with their own SQLAlchemy session.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from .. import models
from ..database import SessionLocal
from . import config
from .agentteams_collaboration import AgentTeamsCollaborationService, CollaborationConflict
from .agentteams_gateway import AgentTeamsGateway, MatrixEvent
from .agentteams_protocol import PREFIX, AgentProtocolMessage, parse_protocol_lines, render_protocol_line
from .agentteams_runtime_config import load_strict_runtime_config
from .agentteams_transport import AgentTeamsTransport, create_agentteams_transport


_CURSOR_SCOPE_PREFIX = "agentteams:transport-sync:"
_TERMINAL_STATUSES = {
    "completed", "failed", "timed_out", "cancelled", "revision_requested"
}
logger = logging.getLogger(__name__)


class AgentTeamsCollaborationRunner:
    """Moves persisted AgentTeams outbox and inbox data through a transport safely."""

    def __init__(
        self,
        gateway: AgentTeamsTransport | None = None,
        service: AgentTeamsCollaborationService | None = None,
        room_id: str | None = None,
        transport: AgentTeamsTransport | None = None,
    ):
        selected_transport = transport if transport is not None else gateway
        strict_runtime = None
        if selected_transport is not None:
            self.transport = selected_transport
        elif config.agentteams_mode() == "agentteams_strict":
            # Strict delivery shares the validated five-identity contract used
            # by health.  It must never silently fall back to legacy env vars.
            strict_runtime = load_strict_runtime_config()
            self.transport = AgentTeamsGateway(
                base_url=strict_runtime.matrix_url,
                matrix_token=strict_runtime.platform.token,
            )
        else:
            self.transport = create_agentteams_transport()
        # Compatibility alias for existing tests and callers using ``gateway=``.
        self.gateway = self.transport
        self.transport_mode = (
            "local_demo"
            if getattr(self.transport, "mode", None) == "local_demo"
            else "agentteams_strict"
        )
        self.service = service or AgentTeamsCollaborationService(
            max_depth=config.agentteams_max_depth(),
            max_children=config.agentteams_max_children(),
        )
        configured_room = room_id or (
            strict_runtime.room_id
            if strict_runtime is not None
            else config.get(config.AGENTTEAMS_ROOM_ID)
        )
        self.room_id = configured_room or (
            "local-demo" if self.transport_mode == "local_demo" else ""
        )
        self._cursor_scope = self._transport_cursor_scope()
        self._closed = False

    def tick_once(self, db: Session) -> None:
        """Run exactly one ordered send, receive, expiry pass.

        Transaction ownership remains with the caller.  ``run_forever`` commits
        a successful tick; web requests and tests can group it with their own
        database work.
        """
        self._send_pending(db)
        self._receive_new(db)
        self._expire_tasks(db)

    def run_forever(self, stop_event: threading.Event) -> None:
        """Run isolated database transactions until the lifecycle owner stops us."""
        while not stop_event.is_set():
            db = SessionLocal()
            try:
                self.tick_once(db)
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.warning(
                    "agentteams_collaboration_tick_failed error_type=%s",
                    exc.__class__.__name__,
                )
            finally:
                db.close()
            stop_event.wait(config.agentteams_collaboration_poll_seconds())

    def close(self) -> None:
        """Close the Matrix client at most once, including injected gateways."""
        if self._closed:
            return
        self._closed = True
        self.transport.close()

    def _send_pending(self, db: Session) -> None:
        delivery_filter = models.AgentMessage.delivery_status == "pending"
        if self.transport_mode == "local_demo":
            delivery_filter = or_(
                delivery_filter,
                and_(
                    models.AgentMessage.delivery_status == "sent",
                    models.AgentMessage.matrix_room_id == self.room_id,
                    models.AgentMessage.matrix_event_id.like("local-out-%"),
                ),
            )
        pending = list(
            db.scalars(
                select(models.AgentMessage)
                .where(
                    models.AgentMessage.direction == "outbound",
                    delivery_filter,
                )
                .order_by(models.AgentMessage.created_at, models.AgentMessage.id)
            )
        )
        for outbound in pending:
            if (
                outbound.message_type != "task.cancel"
                and self._root_is_cancelled(db, outbound.root_task_id)
            ):
                # A user cancellation is durable state, so an older queued
                # submission/assignment must not be released afterwards.  Keep
                # the cancellation notification itself eligible for delivery.
                outbound.delivery_status = "cancelled"
                continue
            try:
                protocol = self._outbound_protocol(db, outbound)
                matrix_event_id = self.transport.send_protocol_message(
                    self.room_id,
                    outbound.body,
                    render_protocol_line(protocol),
                )
                valid_event_id = (
                    isinstance(matrix_event_id, str)
                    and (
                        (self.transport_mode == "local_demo" and matrix_event_id.startswith("local-out-"))
                        or (self.transport_mode == "agentteams_strict" and matrix_event_id.startswith("$") and len(matrix_event_id) > 1)
                    )
                )
                if not valid_event_id:
                    raise ValueError("matrix_send_missing_event_id")
            except (RuntimeError, ValueError, OSError) as exc:
                # Do not mutate the durable outbox on an uncertain delivery.  The
                # same idempotency key will be reused by the next retry.
                logger.warning("agentteams_outbox_send_failed error_type=%s", exc.__class__.__name__)
                continue
            outbound.delivery_status = "sent"
            outbound.matrix_room_id = self.room_id
            outbound.matrix_event_id = matrix_event_id

    def _receive_new(self, db: Session) -> None:
        since = self._load_cursor(db)
        batch = self.transport.sync_messages(since, {self.room_id})

        # The savepoint keeps earlier sends in this tick eligible for commit, yet
        # rolls back every current-batch effect (and its cursor) if a technical
        # receive failure occurs.  Expected protocol rejections are isolated per
        # message below, recorded durably, and do not pin the Matrix cursor.
        with db.begin_nested():
            next_cursor = getattr(batch, "next_cursor", None)
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                raise ValueError("matrix_sync_missing_cursor")
            for event in batch.events:
                valid_event_id = (
                    isinstance(event.event_id, str)
                    and (
                        (self.transport_mode == "agentteams_strict" and event.event_id.startswith("$") and len(event.event_id) > 1)
                        or (self.transport_mode == "local_demo" and event.event_id.startswith("local-in-") and len(event.event_id) > len("local-in-"))
                    )
                )
                if not valid_event_id:
                    raise ValueError("matrix_event_missing_id")
                if self._matrix_event_seen(db, event.event_id):
                    continue
                self._process_event(db, event)
            self._save_cursor(db, next_cursor)

    def _expire_tasks(self, db: Session) -> None:
        now = datetime.now()
        accept_timeout = config.agentteams_accept_timeout_seconds()
        task_timeout = config.agentteams_task_timeout_seconds()
        unaccepted_before = now - timedelta(seconds=accept_timeout)
        root_before = now - timedelta(seconds=task_timeout)

        root_node = aliased(models.AgentTaskNode)
        assigned = list(
            db.scalars(
                select(models.AgentTaskNode)
                .join(models.TaskRun, models.TaskRun.id == models.AgentTaskNode.root_task_id)
                .join(root_node, root_node.id == models.AgentTaskNode.root_task_id)
                .where(
                    models.AgentTaskNode.status == "assigned",
                    models.AgentTaskNode.created_at <= unaccepted_before,
                    models.TaskRun.source == "agentteams",
                    models.TaskRun.status.not_in(_TERMINAL_STATUSES),
                    root_node.status.not_in(_TERMINAL_STATUSES),
                )
            )
        )
        for node in assigned:
            node.status = "timed_out"
            node.finished_at = now
            node.error_code = "accept_timeout"
            node.error_message = "task was not accepted before the deadline"
            self._append_event(
                db,
                root_task_id=node.root_task_id,
                task_id=node.id,
                event_type="task.timed_out",
                actor_agent_id="system",
                target_agent_id=node.assignee_agent_id,
                summary="task acceptance timed out",
                detail={"timeout_seconds": accept_timeout, "reason": "accept_timeout"},
            )

        roots = list(
            db.scalars(
                select(models.AgentTaskNode)
                .join(models.TaskRun, models.TaskRun.id == models.AgentTaskNode.root_task_id)
                .where(
                    models.AgentTaskNode.parent_task_id.is_(None),
                    models.AgentTaskNode.status.not_in(_TERMINAL_STATUSES),
                    models.AgentTaskNode.created_at <= root_before,
                    models.TaskRun.source == "agentteams",
                    models.TaskRun.status.not_in(_TERMINAL_STATUSES),
                )
            )
        )
        for root in roots:
            run = db.get(models.TaskRun, root.root_task_id)
            if run is None:
                continue
            root.status = "timed_out"
            root.finished_at = now
            root.error_code = "task_timeout"
            root.error_message = "root task exceeded the deadline"
            run.status = "timed_out"
            self._append_event(
                db,
                root_task_id=root.root_task_id,
                task_id=root.id,
                event_type="task.timed_out",
                actor_agent_id="system",
                target_agent_id=root.assignee_agent_id,
                summary="root task timed out",
                detail={"timeout_seconds": task_timeout, "reason": "task_timeout"},
            )

    def _process_event(self, db: Session, event: MatrixEvent) -> None:
        protocol_lines = [line for line in event.body.splitlines() if line.startswith(PREFIX)]
        if not protocol_lines:
            self._record_raw_event(db, event, message_type="matrix.text")
            return

        event_recorded = False
        for line in protocol_lines:
            try:
                messages = parse_protocol_lines(line)
            except (json.JSONDecodeError, ValidationError) as exc:
                if not event_recorded:
                    self._record_raw_event(
                        db,
                        event,
                        message_type="protocol.invalid",
                        error=exc,
                    )
                    event_recorded = True
                root_task_id, task_id = self._invalid_event_context(db, line, event.event_id)
                self._append_event(
                    db,
                    root_task_id=root_task_id,
                    task_id=task_id,
                    event_type="protocol.invalid",
                    actor_agent_id=event.sender,
                    target_agent_id=None,
                    summary="invalid AgentTeams protocol message",
                    detail={
                        "matrix_event_id": event.event_id,
                        "error": self._bounded_error(exc),
                    },
                )
                continue

            for message in messages:
                if self._root_is_terminal(db, message.root_task_id):
                    if not event_recorded:
                        self._record_raw_event(db, event, message_type="matrix.terminal_late")
                        event_recorded = True
                    continue
                try:
                    # A rejected protocol message may have reached a validation
                    # step after partially preparing ORM state.  Its own
                    # savepoint makes that rejection non-state-changing while
                    # allowing a later valid event in the same sync batch to
                    # progress and the batch cursor to advance.
                    with db.begin_nested():
                        self.service.apply_message(db, matrix_sender=event.sender, message=message)
                except CollaborationConflict as exc:
                    if not event_recorded:
                        self._record_raw_event(
                            db,
                            event,
                            message_type="protocol.rejected",
                            error=exc,
                        )
                        event_recorded = True
                    root_task_id, task_id = self._event_context(db, event.body, event.event_id)
                    self._append_event(
                        db,
                        root_task_id=root_task_id,
                        task_id=task_id,
                        event_type="protocol.rejected",
                        actor_agent_id=event.sender,
                        target_agent_id=None,
                        summary="rejected AgentTeams protocol message",
                        detail={
                            "matrix_event_id": event.event_id,
                            "error": self._bounded_error(exc),
                        },
                    )
                    continue
                inbound = db.scalar(
                    select(models.AgentMessage).where(
                        models.AgentMessage.idempotency_key == f"in:{message.idempotency_key}",
                        models.AgentMessage.direction == "inbound",
                    )
                )
                if inbound is None or inbound.matrix_event_id is not None:
                    # The protocol idempotency key was already seen in a different
                    # Matrix event.  Keep the new raw event audit-visible without
                    # overwriting the first event's deduplication identity or
                    # replaying protocol state effects.
                    if not event_recorded:
                        self._record_raw_event(db, event, message_type="matrix.duplicate")
                        event_recorded = True
                    continue
                if not event_recorded:
                    inbound.matrix_room_id = event.room_id
                    inbound.matrix_event_id = event.event_id
                    inbound.body = event.body
                    inbound.payload = {
                        **dict(inbound.payload or {}),
                        "matrix": self._matrix_payload(event),
                    }
                    event_recorded = True

        if not event_recorded:
            # Defensive fallback for a future parser that returns no messages for
            # a prefixed line.  It still becomes a durable, deduplicated audit row.
            self._record_raw_event(db, event, message_type="matrix.text")

    def _outbound_protocol(
        self, db: Session, outbound: models.AgentMessage
    ) -> AgentProtocolMessage:
        run = db.get(models.TaskRun, outbound.root_task_id)
        return AgentProtocolMessage(
            version="1.0",
            message_id=outbound.id,
            type=outbound.message_type,
            root_task_id=outbound.root_task_id,
            task_id=outbound.task_id,
            parent_task_id=outbound.parent_task_id,
            trace_id=run.trace_id if run is not None else outbound.root_task_id,
            # Strict Matrix outbox work is transmitted by the platform account.
            # Keep its logical coordinator in persisted message/event fields,
            # but make this protocol sender match the Matrix event sender so
            # Workers retain anti-spoofing.  Offline local_demo retains its
            # deterministic compatibility envelope and is never real evidence.
            sender="platform" if self.transport_mode == "agentteams_strict" else outbound.sender_agent_id,
            receiver=outbound.receiver_agent_id,
            timestamp=outbound.created_at.isoformat(),
            idempotency_key=outbound.idempotency_key,
            payload=dict(outbound.payload or {}),
        )

    def _load_cursor(self, db: Session) -> str | None:
        stored = db.scalar(
            select(models.AgentMatrixCursor).where(
                models.AgentMatrixCursor.scope_key == self._cursor_scope
            )
        )
        if stored is None or not stored.cursor:
            return None
        return stored.cursor

    def _save_cursor(self, db: Session, cursor: str) -> None:
        stored = db.scalar(
            select(models.AgentMatrixCursor).where(
                models.AgentMatrixCursor.scope_key == self._cursor_scope
            )
        )
        if stored is None:
            db.add(models.AgentMatrixCursor(scope_key=self._cursor_scope, cursor=cursor))
        else:
            stored.cursor = cursor

    @staticmethod
    def _matrix_event_seen(db: Session, event_id: str) -> bool:
        return db.scalar(
            select(models.AgentMessage.id).where(models.AgentMessage.matrix_event_id == event_id)
        ) is not None

    @staticmethod
    def _root_is_terminal(db: Session, root_task_id: str) -> bool:
        """Keep late protocol input from reopening an already terminal root."""
        run = db.get(models.TaskRun, root_task_id)
        root = db.get(models.AgentTaskNode, root_task_id)
        return (
            (run is not None and run.source == "agentteams" and run.status in _TERMINAL_STATUSES)
            or (root is not None and root.status in _TERMINAL_STATUSES)
        )

    @staticmethod
    def _root_is_cancelled(db: Session, root_task_id: str) -> bool:
        """Suppress stale outbox work after a user has cancelled its root."""
        run = db.get(models.TaskRun, root_task_id)
        root = db.get(models.AgentTaskNode, root_task_id)
        return (
            (run is not None and run.source == "agentteams" and run.status == "cancelled")
            or (root is not None and root.status == "cancelled")
        )

    def _record_raw_event(
        self,
        db: Session,
        event: MatrixEvent,
        *,
        message_type: str,
        error: Exception | None = None,
    ) -> models.AgentMessage:
        root_task_id, task_id = self._event_context(db, event.body, event.event_id)
        inbound = models.AgentMessage(
            id=f"AM-{uuid4().hex}",
            root_task_id=root_task_id,
            task_id=task_id,
            parent_task_id=None,
            message_type=message_type,
            sender_agent_id=event.sender,
            receiver_agent_id="platform",
            body=event.body,
            payload={
                "matrix": self._matrix_payload(event),
                **({"error": self._bounded_error(error)} if error is not None else {}),
            },
            matrix_room_id=event.room_id,
            matrix_event_id=event.event_id,
            idempotency_key=f"matrix:{event.event_id}",
            direction="inbound",
            delivery_status="received",
        )
        db.add(inbound)
        db.flush()
        return inbound

    def _event_context(self, db: Session, body: str, event_id: str) -> tuple[str, str]:
        for line in body.splitlines():
            if not line.startswith(PREFIX):
                continue
            try:
                raw = json.loads(line[len(PREFIX) :])
            except json.JSONDecodeError:
                break
            if not isinstance(raw, dict):
                break
            root_task_id = raw.get("root_task_id")
            task_id = raw.get("task_id")
            if (
                isinstance(root_task_id, str)
                and isinstance(task_id, str)
                and db.get(models.TaskRun, root_task_id) is not None
            ):
                node = db.get(models.AgentTaskNode, task_id)
                if node is not None and node.root_task_id == root_task_id:
                    return root_task_id, task_id
            break
        sentinel = f"matrix:{event_id}"
        return sentinel, sentinel

    def _invalid_event_context(self, db: Session, line: str, event_id: str) -> tuple[str, str]:
        return self._event_context(db, line, event_id)

    @staticmethod
    def _matrix_payload(event: MatrixEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "room_id": event.room_id,
            "sender": event.sender,
            "ts": event.ts,
            "body": event.body,
        }

    @staticmethod
    def _bounded_error(error: Exception) -> str:
        return str(error)[:512]

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
        detail: dict[str, Any],
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
        db.flush()
        return event

    def _transport_cursor_scope(self) -> str:
        account = (
            config.get(config.AGENTTEAMS_ADMIN_USER)
            or config.agentteams_bot_mxid()
        ).strip()
        return ":".join(
            (
                _CURSOR_SCOPE_PREFIX.rstrip(":"),
                self.transport_mode,
                account or "default",
                self.room_id or "default-room",
            )
        )
