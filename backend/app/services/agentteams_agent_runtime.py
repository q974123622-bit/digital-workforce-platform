"""Independent, role-neutral Matrix protocol runtime for AgentTeams.

This process deliberately owns no database session and never executes a Tool.
It receives a bounded protocol envelope from one Matrix account, asks the
decision engine for a safe next action, and sends protocol envelopes back to
the platform using that same account.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Callable, Literal, Mapping, Protocol

from pydantic import ValidationError

from .agentteams_decisions import (
    AgentDecisionError,
    AgentDecisionEngine,
    ManagerContext,
    PlannedSubtask,
    SkillSelectionContext,
    WorkerContext,
)
from .agentteams_gateway import MatrixEvent
from .agentteams_protocol import AgentProtocolMessage, parse_protocol_lines, render_protocol_line
from .agentteams_runtime_config import is_safe_agent_id, require_safe_path
from .llm import LLMUnavailableError


Role = Literal["manager", "hr", "it", "audit"]
_ROLES = {"manager", "hr", "it", "audit"}
_ROLE_PROFESSIONAL_SKILLS = {
    "hr": {"hr-onboarding-assistant"},
    "it": {"it-service-assistant"},
    "audit": {"audit-evidence-review"},
}
_SAFE_ERROR_CODES = {
    "skill_selection_failed", "planning_failed", "decision_failed", "summary_failed",
    "completion_without_tool_evidence", "invalid_model_decision", "invalid_team_catalog",
    "invalid_parent_chain", "llm_unavailable", "network_error", "http_error",
    "rate_limited", "client_error", "server_error",
    "invalid_response_json", "invalid_structured_json", "runtime_error",
}
_MAX_TRACKED = 256
_MAX_TASK_CONTEXTS = 32
_MAX_EVIDENCE_ITEMS = 8
_MAX_EVIDENCE_BYTES = 4096
_MAX_TEXT = 512
_MAX_STATE_BYTES = 16_384
_MAX_PENDING_ENVELOPE_BYTES = 8_192
_PENDING_VERSION = 1
_SENSITIVE_TEXT = re.compile(r"(?:\b(?:bearer|basic)\s+[a-z0-9._~+/=-]+|\b(?:https?|wss?|file|ftp)://\S+|\b(?:api[_-]?key|password|token|authorization|credential)\s*[:=]\s*\S+|\b[a-z_][a-z0-9_]*\s*=\s*\S+|traceback\s*\(|\bfile\s+['\"]?[a-z]:[/\\])", re.I)
_OUTBOUND_TYPES = {"agent.message", "task.accepted", "task.progress", "task.delegate", "task.summary", "task.failed", "tool.request", "task.result"}


def heartbeat_signature(agent_id: str, mxid: str, heartbeat_at: str, token: str) -> str:
    """Authenticate the minimal liveness record without persisting its key."""
    message = f"{agent_id}\n{mxid}\n{heartbeat_at}".encode("utf-8")
    return hmac.new(token.encode("utf-8"), message, hashlib.sha256).hexdigest()


class MatrixProtocolTransport(Protocol):
    def sync_messages(self, since: str | None, room_ids: set[str]): ...

    def send_protocol_message(self, room_id: str, text: str, protocol_line: str) -> str: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class AgentRuntimeConfig:
    """All authority for exactly one independent Agent process."""

    agent_id: str
    role: Role
    matrix_mxid: str
    matrix_token: str
    room_id: str
    trusted_sender_mxids: Mapping[str, str]
    allowed_skill_ids: set[str]
    team_catalog: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    safe_tool_catalog: list[dict[str, Any]] = field(default_factory=list)
    scenario_skill_ids: set[str] = field(default_factory=lambda: {"agentteams-onboarding-readiness"})
    professional_skill_ids: set[str] | None = None
    state_dir: Path = field(default_factory=lambda: Path(".agentteams-runtime") / "state")
    pause_seconds: float = 3.0

    def __post_init__(self) -> None:
        if self.role not in _ROLES or not is_safe_agent_id(self.agent_id) or not self.matrix_mxid.startswith("@") or not self.matrix_token or not self.room_id:
            raise ValueError("invalid_runtime_config")
        if self.trusted_sender_mxids.get(self.agent_id) != self.matrix_mxid:
            raise ValueError("invalid_runtime_config")
        if not self.trusted_sender_mxids.get("platform"):
            raise ValueError("invalid_runtime_config")
        if not self.allowed_skill_ids:
            raise ValueError("invalid_runtime_config")
        object.__setattr__(self, "state_dir", Path(self.state_dir))
        try:
            require_safe_path(Path(self.state_dir), allow_missing=True)
        except ValueError:
            raise ValueError("invalid_runtime_config") from None
        professional = self.professional_skill_ids
        if professional is None and self.role != "manager":
            professional = set(_ROLE_PROFESSIONAL_SKILLS[self.role])
        object.__setattr__(self, "professional_skill_ids", set(professional or set()))


class AgentRuntime:
    """A finite receive/decide/send loop with crash-safe local progress."""

    def __init__(
        self,
        config: AgentRuntimeConfig,
        gateway: MatrixProtocolTransport,
        decisions: AgentDecisionEngine,
        *,
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.gateway = gateway
        self.decisions = decisions
        self.pause = pause
        self._state_path = config.state_dir / f"{config.agent_id}.json"
        self._state = self._load_state()
        self._active_pending: dict[str, Any] | None = None

    def run_once(self) -> int:
        """Handle one ordered Matrix sync batch and return handled protocol count.

        The persisted Matrix cursor moves only once every event in the batch has
        completed safely. A pending outbox is committed before a response is
        sent, so a retry can replay the exact envelopes without another model
        call.
        """
        heartbeat_at = datetime.now(timezone.utc).isoformat()
        self._state.update({
            "agent_id": self.config.agent_id,
            "mxid": self.config.matrix_mxid,
            "heartbeat_at": heartbeat_at,
            "heartbeat_signature": heartbeat_signature(
                self.config.agent_id, self.config.matrix_mxid, heartbeat_at, self.config.matrix_token,
            ),
        })
        self._save_state()
        replayed = 0
        if self._state.get("pending_outbox"):
            replayed = self._replay_pending()
            if not replayed:
                return 0
        try:
            batch = self.gateway.sync_messages(self._state.get("cursor"), {self.config.room_id})
        except Exception:
            return 0
        next_cursor = getattr(batch, "next_cursor", None)
        if not isinstance(next_cursor, str) or not next_cursor:
            return 0
        events = sorted(
            (item for item in getattr(batch, "events", []) if item.room_id == self.config.room_id),
            key=lambda item: (item.ts, item.event_id),
        )
        handled = replayed
        for event in events:
            if not isinstance(event.event_id, str) or not event.event_id:
                continue
            if event.event_id in self._state["processed_event_ids"]:
                continue
            trial = deepcopy(self._state)
            self._active_pending = {
                "event_id": event.event_id,
                "trial": trial,
                "source": None,
            }
            outcome = self._process_event(event, trial)
            self._active_pending = None
            if outcome is None:
                # A network failure left this event wholly uncommitted.
                return handled
            if outcome:
                handled += outcome
            self._mark_processed(trial, event.event_id)
            trial.pop("pending_outbox", None)
            self._state = trial
            self._save_state()
        # Only a fully-consumed batch advances the opaque Matrix token.
        self._state["cursor"] = next_cursor
        self._save_state()
        return handled

    def _replay_pending(self) -> int:
        pending = self._state.get("pending_outbox")
        messages = self._validated_pending_messages(pending)
        if messages is None:
            self._state["pending_outbox"] = None
            self._security(self._state, "pending_corrupt")
            self._save_state()
            return 0
        if not self._send_direct(messages):
            return 0
        trial = deepcopy(dict(pending["trial_state"]))
        source = pending["source"]
        self._mark_idempotency(trial, source["idempotency_key"])
        # A protocol envelope, rather than its entire Matrix event, has now
        # completed.  Keep the old cursor and let the next sync skip this line
        # by idempotency while it handles its siblings and later events.
        trial["cursor"] = self._state.get("cursor")
        trial["pending_outbox"] = None
        self._state = trial
        self._save_state()
        return 1

    def run_forever(self, stop_event: Any) -> None:
        while not stop_event.is_set():
            self.run_once()
            stop_event.wait(1.0)

    def close(self) -> None:
        self.gateway.close()

    def _process_event(self, event: MatrixEvent, state: dict[str, Any]) -> int | bool | None:
        if event.sender == self.config.matrix_mxid:
            return False
        count = 0
        # A Matrix event is an untrusted chat body.  Parse each envelope on its
        # own so one malformed line cannot suppress a later valid command.
        for line in event.body.splitlines():
            if not line.startswith("DWP_EVENT "):
                continue
            try:
                messages = parse_protocol_lines(line)
            except (json.JSONDecodeError, ValidationError, ValueError):
                self._security(state, "parse_error")
                continue
            for message in messages:
                outcome = self._process_protocol_message(message, event, state)
                if outcome is None:
                    return None
                count += outcome
        return count

    def _process_protocol_message(self, message: AgentProtocolMessage, event: MatrixEvent, state: dict[str, Any]) -> int | None:
        if message.receiver != self.config.agent_id:
            return 0
        expected_mxid = self.config.trusted_sender_mxids.get(message.sender)
        if expected_mxid != event.sender:
            self._security(state, "spoofed_sender")
            return 0
        if message.idempotency_key in state["processed_idempotency_keys"]:
            return 0
        if self._active_pending is not None:
            self._active_pending["source"] = self._pending_source(event, message)
        try:
            responses = self._handle_message(message, event, state)
        except LLMUnavailableError as exc:
            responses = [self._failure(message, self._safe_code(exc.code))]
        except AgentDecisionError as exc:
            responses = [self._failure(message, self._safe_code(exc.code))]
        except Exception:
            responses = [self._failure(message, "runtime_error")]
        if not self._send_responses(responses):
            return None
        self._mark_idempotency(state, message.idempotency_key)
        self._persist_protocol_progress(state)
        return 1

    def _handle_message(
        self, message: AgentProtocolMessage, event: MatrixEvent, state: dict[str, Any]
    ) -> list[AgentProtocolMessage]:
        if self.config.role == "manager":
            if message.type == "task.submit" and message.sender == "platform":
                return self._manager_submit(message, state)
            if message.type == "task.summary" and message.sender == "platform" and message.payload.get("request") == "task.summary":
                return self._manager_review(message, state)
            return []
        if message.type == "task.assign":
            return self._worker_assign(message, state)
        if message.type == "tool.result" and message.sender == "platform":
            return self._worker_continue(message, state, evidence_kind="tool.result")
        if message.type == "task.summary" and message.sender == "platform" and message.payload.get("request") == "task.summary":
            return self._worker_continue(message, state, evidence_kind="child.summary")
        return []

    def _manager_submit(self, message: AgentProtocolMessage, state: dict[str, Any]) -> list[AgentProtocolMessage]:
        goal = self._goal(message)
        catalog, catalog_version = self._fresh_team_catalog(message.payload)
        self._visible_pause()
        selection = self.decisions.select_skills(SkillSelectionContext(
            user_goal=goal,
            runtime_skill_id="agentteams-manager-coordination",
            allowed_skill_ids=set(self.config.scenario_skill_ids).intersection(self.config.allowed_skill_ids),
        ))
        selected = self._safe_selected(selection.selected_skill_ids, self.config.scenario_skill_ids)
        skill_message = self._reply(message, "agent.message", "platform", {
            "kind": "skill.selected", "selected_skill_ids": selected,
        }, 0)
        self._visible_pause()
        decision = self.decisions.plan(self._manager_context(
            goal=goal, selected=selected, catalog=catalog, child_results=[], existing_child_task_keys=[], current_round=1, limits={}
        ))
        self._remember_manager(state, message, goal, selected, catalog_version)
        return [skill_message, *self._manager_decision(message, decision, catalog, 1)]

    def _manager_review(self, message: AgentProtocolMessage, state: dict[str, Any]) -> list[AgentProtocolMessage]:
        stored = state["tasks"].get(message.task_id, {})
        selected = self._safe_selected(stored.get("selected_skill_ids", []), self.config.scenario_skill_ids)
        if not selected:
            raise AgentDecisionError("planning_failed")
        catalog, catalog_version = self._fresh_team_catalog(
            message.payload, previous_version=stored.get("team_catalog_version")
        )
        current_round = self._positive_int(message.payload.get("current_round"), 1)
        self._visible_pause()
        decision = self.decisions.plan(self._manager_context(
            goal=self._goal(message, stored.get("goal")), selected=selected, catalog=catalog,
            child_results=self._bounded_collection(message.payload.get("child_results")),
            existing_child_task_keys=message.payload.get("existing_child_task_keys"),
            current_round=current_round, limits=message.payload.get("remaining_limits", {}),
        ))
        self._remember_manager(state, message, self._goal(message, stored.get("goal")), selected, catalog_version)
        return self._manager_decision(message, decision, catalog, current_round)

    def _manager_decision(self, message: AgentProtocolMessage, decision: Any, catalog: Mapping[str, Any], round_no: int) -> list[AgentProtocolMessage]:
        selected = self._safe_selected(decision.selected_skill_ids, self.config.scenario_skill_ids)
        if decision.action == "delegate":
            subtasks = [self._safe_subtask(item, catalog) for item in decision.subtasks]
            if not subtasks:
                raise AgentDecisionError("planning_failed")
            return [self._reply(message, "task.delegate", "platform", {
                "subtasks": subtasks, "selected_skill_ids": selected, "round": round_no,
            }, 1)]
        if decision.action == "summarize":
            summary = decision.summary
            return [self._reply(message, "task.summary", "platform", {
                "summary": self._text(summary.summary),
                "completed_items": self._bounded_text_list(summary.completed_items),
                "risks": self._bounded_text_list(summary.risks),
                "unknowns": self._bounded_text_list(summary.unknowns),
                "next_actions": self._bounded_text_list(summary.next_actions),
            }, 1)]
        return [self._failure(message, "planning_failed", sequence=1)]

    def _worker_assign(self, message: AgentProtocolMessage, state: dict[str, Any]) -> list[AgentProtocolMessage]:
        self._visible_pause()
        accepted = self._reply(message, "task.accepted", "platform", {}, 0)
        self._visible_pause()
        progress = self._reply(message, "task.progress", "platform", {"progress": 10}, 1)
        allowed = set(self.config.professional_skill_ids or set()).intersection(self.config.allowed_skill_ids)
        if not allowed:
            raise AgentDecisionError("skill_selection_failed")
        self._visible_pause()
        selection = self.decisions.select_skills(SkillSelectionContext(
            user_goal=self._goal(message), runtime_skill_id="employee-collaboration", allowed_skill_ids=allowed,
        ))
        selected = self._safe_selected(selection.selected_skill_ids, allowed)
        context = self._new_worker_context(message, selected)
        state["tasks"][message.task_id] = context
        self._trim_tasks(state)
        selected_message = self._reply(message, "agent.message", "platform", {
            "kind": "skill.selected", "selected_skill_ids": selected,
        }, 2)
        self._visible_pause()
        return [accepted, progress, selected_message, *self._worker_decide(message, state, context, 3)]

    def _worker_continue(self, message: AgentProtocolMessage, state: dict[str, Any], *, evidence_kind: str) -> list[AgentProtocolMessage]:
        context = state["tasks"].get(message.task_id)
        if not isinstance(context, dict):
            raise AgentDecisionError("decision_failed")
        if evidence_kind in {"child.summary", "tool.result"}:
            catalog, version = self._fresh_team_catalog(message.payload, previous_version=context.get("team_catalog_version"))
            context["team_catalog"] = catalog
            context["team_catalog_version"] = version
        evidence = {"kind": evidence_kind, "value": self._bounded_value(message.payload)}
        context["evidence"] = self._append_evidence(context.get("evidence", []), evidence)
        self._visible_pause()
        return self._worker_decide(message, state, context, 0)

    def _worker_decide(self, message: AgentProtocolMessage, state: dict[str, Any], context: dict[str, Any], sequence: int) -> list[AgentProtocolMessage]:
        decision = self.decisions.decide_work(WorkerContext(
            current_agent_id=self.config.agent_id,
            task=dict(context["task"]), professional_skill_ids=["employee-collaboration", *context["selected_skill_ids"]],
            safe_tool_catalog=[self._bounded_value(item) for item in self.config.safe_tool_catalog],
            current_team_capability_catalog=self._worker_team_capability_catalog(
                context.get("team_catalog")
            ),
            dependency_results=self._bounded_collection(context["task"].get("dependency_results")),
            parent_chain_agent_ids=self._bounded_text_list(context["parent_chain_agent_ids"]),
            previous_results=self._bounded_collection(context.get("evidence", [])),
        ))
        if decision.action == "tool":
            tool = self._tool(decision.tool_name)
            if tool is None:
                raise AgentDecisionError("decision_failed")
            return [self._reply(message, "tool.request", "platform", {
                "tool_name": decision.tool_name, "plugin_id": tool["plugin_id"], "action": tool["action"],
                **({"required_capability": tool["required_capability"]} if tool.get("required_capability") else {}),
                "params": self._bounded_value(decision.tool_arguments or {}),
            }, sequence)]
        if decision.action == "ask":
            recipient = decision.recipient_agent_id
            if recipient not in self.config.trusted_sender_mxids or recipient in {"platform", self.config.agent_id}:
                raise AgentDecisionError("decision_failed")
            return [self._reply(message, "agent.message", recipient, {"kind": "peer.ask", "text": self._text(decision.message)}, sequence)]
        if decision.action == "delegate":
            subtask = self._safe_subtask(decision.subtask, context.get("team_catalog", {}), parent_chain=context["parent_chain_agent_ids"])
            return [self._reply(message, "task.delegate", "platform", {"subtasks": [subtask]}, sequence)]
        if decision.action == "complete":
            audit_ids = self._successful_audit_ids(context.get("evidence", []))
            if audit_ids:
                return [self._reply(message, "task.result", "platform", {"result": self._text(decision.result), "audit_ids": audit_ids}, sequence)]
            if not context.get("completion_corrected"):
                context["completion_corrected"] = True
                context["evidence"] = self._append_evidence(context.get("evidence", []), {"kind": "runtime.rejected", "reason": "successful_tool_evidence_required"})
                return self._worker_decide(message, state, context, sequence + 1)
            return [self._failure(message, "completion_without_tool_evidence", sequence=sequence)]
        return [self._failure(message, "decision_failed", sequence=sequence)]

    def _worker_team_capability_catalog(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, Mapping):
            return []
        catalog: list[dict[str, Any]] = []
        for agent_id, value in raw.items():
            if not isinstance(agent_id, str) or not isinstance(value, Mapping):
                continue
            capabilities = value.get("capabilities")
            if not isinstance(capabilities, list):
                continue
            catalog.append({
                "agent_id": self._text(agent_id, 128),
                "capabilities": self._bounded_text_list(capabilities, 32),
            })
        return catalog

    def _reply(self, source: AgentProtocolMessage, type: str, receiver: str, payload: dict[str, Any], sequence: int) -> AgentProtocolMessage:
        digest = hashlib.sha256(f"{source.idempotency_key}:{type}:{sequence}".encode()).hexdigest()[:20]
        return AgentProtocolMessage(
            version="1.0", message_id=f"ARM-{self.config.agent_id}-{digest}", type=type,
            root_task_id=source.root_task_id, task_id=source.task_id, parent_task_id=source.parent_task_id,
            trace_id=source.trace_id, sender=self.config.agent_id, receiver=receiver,
            timestamp=source.timestamp or datetime.now(timezone.utc).isoformat(),
            idempotency_key=f"runtime:{self.config.agent_id}:{digest}:{sequence}", payload=payload,
        )

    def _failure(self, source: AgentProtocolMessage, code: str, *, sequence: int = 99) -> AgentProtocolMessage:
        return self._reply(source, "task.failed", "platform", {"error_code": self._safe_code(code)}, sequence)

    def _send_responses(self, messages: list[AgentProtocolMessage]) -> bool:
        if not messages:
            return True
        canonical = self._canonical_envelopes(messages)
        if canonical is None:
            self._security(self._state, "outbound_rejected")
            self._save_state()
            return False
        if self._active_pending is not None:
            trial = self._active_pending["trial"]
            source = self._active_pending.get("source")
            pending = {
                "version": _PENDING_VERSION,
                "source": source,
                "messages": [message.model_dump(mode="json") for message in canonical],
                "trial_state": self._bounded_state({
                    **{key: value for key, value in trial.items() if key != "pending_outbox"},
                    "pending_outbox": None,
                }),
            }
            if self._validated_pending_messages(pending) is None:
                self._security(self._state, "outbound_rejected")
                self._save_state()
                return False
            self._state["pending_outbox"] = pending
            self._save_state()
        return self._send_direct(canonical)

    def _persist_protocol_progress(self, state: Mapping[str, Any]) -> None:
        """Commit an acknowledged protocol line without completing its event."""
        durable = deepcopy(dict(state))
        durable["pending_outbox"] = None
        self._state = durable
        self._save_state()

    def _send_direct(self, messages: list[AgentProtocolMessage]) -> bool:
        try:
            for message in messages:
                event_id = self.gateway.send_protocol_message(self.config.room_id, message.type, render_protocol_line(message))
                if not event_id:
                    return False
        except Exception:
            return False
        return True

    @staticmethod
    def _pending_source(event: MatrixEvent, message: AgentProtocolMessage) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "idempotency_key": message.idempotency_key,
            "root_task_id": message.root_task_id,
            "task_id": message.task_id,
            "parent_task_id": message.parent_task_id,
            "trace_id": message.trace_id,
        }

    def _canonical_envelopes(self, messages: list[AgentProtocolMessage]) -> list[AgentProtocolMessage] | None:
        """Create the one safe byte-stable representation used for all sends."""
        if not messages or len(messages) > 8:
            return None
        canonical: list[AgentProtocolMessage] = []
        for message in messages:
            if not self._reasonable_outbound(message):
                return None
            payload = self._safe_value(message.payload)
            if not isinstance(payload, dict):
                return None
            try:
                envelope = AgentProtocolMessage(
                    version=message.version, message_id=message.message_id, type=message.type,
                    root_task_id=message.root_task_id, task_id=message.task_id,
                    parent_task_id=message.parent_task_id, trace_id=message.trace_id,
                    sender=message.sender, receiver=message.receiver,
                    timestamp=message.timestamp, idempotency_key=message.idempotency_key,
                    payload=payload,
                )
            except ValidationError:
                return None
            canonical.append(envelope)
        if sum(len(render_protocol_line(message).encode("utf-8")) for message in canonical) > _MAX_PENDING_ENVELOPE_BYTES:
            return None
        return canonical

    def _reasonable_outbound(self, message: AgentProtocolMessage) -> bool:
        if message.version != "1.0" or message.type not in _OUTBOUND_TYPES or message.sender != self.config.agent_id:
            return False
        if message.receiver not in self.config.trusted_sender_mxids or message.receiver == self.config.agent_id:
            return False
        if message.type != "agent.message" and message.receiver != "platform":
            return False
        values = (message.message_id, message.root_task_id, message.task_id, message.trace_id, message.timestamp, message.idempotency_key)
        if any(not self._safe_protocol_text(value) for value in values):
            return False
        return message.parent_task_id is None or self._safe_protocol_text(message.parent_task_id)

    @staticmethod
    def _safe_protocol_text(value: Any) -> bool:
        return isinstance(value, str) and 0 < len(value) <= 512 and not _SENSITIVE_TEXT.search(value)

    def _validated_pending_messages(self, pending: Any) -> list[AgentProtocolMessage] | None:
        """Fail closed before any outbound send when durable replay data is malformed."""
        if not isinstance(pending, Mapping) or set(pending) != {"version", "source", "messages", "trial_state"} or pending.get("version") != _PENDING_VERSION:
            return None
        source = pending.get("source")
        trial = pending.get("trial_state")
        raw_messages = pending.get("messages")
        if not self._valid_pending_source(source) or not self._complete_state(trial):
            return None
        if not isinstance(raw_messages, list) or not 1 <= len(raw_messages) <= 8:
            return None
        try:
            messages = [AgentProtocolMessage.model_validate(item) for item in raw_messages]
        except (ValidationError, TypeError, ValueError):
            return None
        if sum(len(render_protocol_line(message).encode("utf-8")) for message in messages) > _MAX_PENDING_ENVELOPE_BYTES:
            return None
        for message in messages:
            if not self._reasonable_outbound(message):
                return None
            if (
                message.root_task_id != source["root_task_id"]
                or message.task_id != source["task_id"]
                or message.parent_task_id != source["parent_task_id"]
                or message.trace_id != source["trace_id"]
            ):
                return None
            # Pending messages must already be the redacted canonical form.
            safe_payload = self._safe_value(message.payload)
            if safe_payload != message.payload:
                return None
        try:
            pending_bytes = len(json.dumps(pending, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError):
            return None
        return messages if pending_bytes <= _MAX_STATE_BYTES else None

    def _valid_pending_source(self, source: Any) -> bool:
        if not isinstance(source, Mapping) or set(source) != {"event_id", "idempotency_key", "root_task_id", "task_id", "parent_task_id", "trace_id"}:
            return False
        required = ("event_id", "idempotency_key", "root_task_id", "task_id", "trace_id")
        if any(not self._safe_protocol_text(source.get(name)) for name in required):
            return False
        return source.get("parent_task_id") is None or self._safe_protocol_text(source.get("parent_task_id"))

    @staticmethod
    def _complete_state(value: Any) -> bool:
        if not isinstance(value, Mapping):
            return False
        required = {"cursor", "processed_event_ids", "processed_idempotency_keys", "tasks", "security", "pending_outbox", "agent_id", "mxid", "heartbeat_at", "heartbeat_signature"}
        if set(value) != required:
            return False
        if value.get("cursor") is not None and not isinstance(value.get("cursor"), str):
            return False
        if value.get("pending_outbox") is not None or not isinstance(value.get("tasks"), Mapping):
            return False
        if value.get("heartbeat_at") is not None and not isinstance(value.get("heartbeat_at"), str):
            return False
        if value.get("agent_id") is not None and not isinstance(value.get("agent_id"), str):
            return False
        if value.get("mxid") is not None and not isinstance(value.get("mxid"), str):
            return False
        if value.get("heartbeat_signature") is not None and not isinstance(value.get("heartbeat_signature"), str):
            return False
        for name in ("processed_event_ids", "processed_idempotency_keys"):
            tracked = value.get(name)
            if not isinstance(tracked, list) or len(tracked) > _MAX_TRACKED or any(not isinstance(item, str) or not item for item in tracked):
                return False
        security = value.get("security")
        return isinstance(security, Mapping) and all(
            isinstance(key, str) and isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 9999
            for key, count in security.items()
        )

    def _manager_context(self, *, goal, selected, catalog, child_results, existing_child_task_keys, current_round, limits) -> ManagerContext:
        safe_limits = limits if isinstance(limits, Mapping) else {}
        return ManagerContext(
            user_goal=goal, manager_skill_id="agentteams-manager-coordination", selected_skill_ids=selected,
            team_capability_catalog=[{"agent_id": key, **self._bounded_value(value)} for key, value in catalog.items()],
            child_results=child_results,
            existing_child_task_keys=self._bounded_text_list(existing_child_task_keys, limit=20),
            current_round=current_round,
            max_rounds=self._positive_int(safe_limits.get("rounds"), 3),
            max_children=self._positive_int(safe_limits.get("children"), 5),
            max_nodes=self._positive_int(safe_limits.get("nodes"), 20),
        )

    def _remember_manager(self, state, message, goal, selected, catalog_version) -> None:
        # Catalog entries are a per-message authority snapshot.  Persist only
        # its monotonic version; a later planning round must supply a fresh one.
        state["tasks"][message.task_id] = {
            "kind": "manager", "goal": goal, "selected_skill_ids": selected,
            "team_catalog_version": catalog_version,
        }
        self._trim_tasks(state)

    def _new_worker_context(self, message, selected) -> dict[str, Any]:
        parent_chain = message.payload.get("parent_chain_agent_ids")
        if not isinstance(parent_chain, list) or not parent_chain or any(not isinstance(item, str) or not item for item in parent_chain):
            raise AgentDecisionError("invalid_parent_chain")
        # The platform persistently signs and forwards assignments over Matrix;
        # its authenticated protocol sender is therefore not itself a business
        # ancestor.  A direct parent-agent envelope remains valid for protocol
        # compatibility, while any other sender is rejected before planning.
        if (
            message.sender not in {"platform", *parent_chain}
            or self.config.agent_id in parent_chain
            or len(parent_chain) != len(set(parent_chain))
        ):
            raise AgentDecisionError("invalid_parent_chain")
        catalog, version = self._fresh_team_catalog(message.payload)
        return {
            "kind": "worker", "selected_skill_ids": selected, "completion_corrected": False, "evidence": [],
            "parent_chain_agent_ids": self._bounded_text_list(parent_chain, 8),
            "team_catalog": catalog, "team_catalog_version": version,
            "task": {"title": self._text(message.payload.get("title", "task")), "goal": self._goal(message),
                     "client_task_key": self._text(message.payload.get("client_task_key", "task")),
                     "dependency_results": self._bounded_collection(message.payload.get("dependency_results"))},
        }

    def _safe_subtask(self, subtask: PlannedSubtask | None, catalog: Mapping[str, Any], *, parent_chain: list[str] | None = None) -> dict[str, Any]:
        if subtask is None or not subtask.required_capability or subtask.assignee_agent_id == self.config.agent_id or subtask.assignee_agent_id in set(parent_chain or []):
            raise AgentDecisionError("invalid_model_decision")
        member = catalog.get(subtask.assignee_agent_id)
        capabilities = member.get("capabilities", []) if isinstance(member, Mapping) else []
        if subtask.assignee_agent_id not in self.config.trusted_sender_mxids or subtask.required_capability not in capabilities:
            raise AgentDecisionError("invalid_model_decision")
        return {
            "client_task_key": self._text(subtask.client_task_key, 96), "title": self._text(subtask.title, 256),
            "instruction": self._text(subtask.goal), "assignee_agent_id": subtask.assignee_agent_id,
            "depends_on": self._bounded_text_list(subtask.depends_on, 32), "required": bool(subtask.required),
            "required_capability": self._text(subtask.required_capability, 128), "material": {},
        }

    def _fresh_team_catalog(self, payload: Mapping[str, Any], previous_version: Any = None) -> tuple[dict[str, dict[str, Any]], int]:
        raw = payload.get("team_capability_catalog")
        version = payload.get("team_catalog_version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise AgentDecisionError("invalid_team_catalog")
        if isinstance(previous_version, int) and version <= previous_version:
            raise AgentDecisionError("invalid_team_catalog")
        catalog: dict[str, dict[str, Any]] = {}
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, Mapping) and isinstance(item.get("agent_id"), str):
                    caps = item.get("capabilities", [])
                    if isinstance(caps, list) and all(isinstance(value, str) for value in caps):
                        catalog[item["agent_id"]] = {"capabilities": self._bounded_text_list(caps, 32)}
        if not catalog:
            raise AgentDecisionError("invalid_team_catalog")
        return catalog, version

    def _tool(self, name: Any) -> dict[str, str] | None:
        if not isinstance(name, str):
            return None
        for item in self.config.safe_tool_catalog:
            action = item.get("action", "read")
            if (item.get("name") == name
                    and isinstance(item.get("plugin_id"), str)
                    and item["plugin_id"]
                    and action in {"read", "query", "execute"}):
                return {"plugin_id": item["plugin_id"], "action": action, "required_capability": str(item.get("required_capability", ""))}
        return None

    def _safe_selected(self, values: Any, allowed: set[str]) -> list[str]:
        if not isinstance(values, list) or not values or any(not isinstance(item, str) for item in values):
            raise AgentDecisionError("skill_selection_failed")
        if len(values) != len(set(values)) or not set(values).issubset(allowed):
            raise AgentDecisionError("skill_selection_failed")
        return list(values)

    def _successful_audit_ids(self, evidence: Any) -> list[int]:
        if not isinstance(evidence, list):
            return []
        for item in reversed(evidence):
            value = item.get("value") if isinstance(item, Mapping) and item.get("kind") == "tool.result" else None
            if isinstance(value, Mapping) and value.get("ok") is True:
                ids = value.get("audit_ids")
                if isinstance(ids, list) and ids and all(
                    isinstance(item, int) and not isinstance(item, bool) and 0 < item < 2 ** 63
                    for item in ids
                ):
                    return ids[:16]
        return []

    def _load_state(self) -> dict[str, Any]:
        default = {"cursor": None, "processed_event_ids": [], "processed_idempotency_keys": [], "tasks": {}, "security": {}, "pending_outbox": None, "agent_id": None, "mxid": None, "heartbeat_at": None, "heartbeat_signature": None}
        try:
            require_safe_path(self.config.state_dir, allow_missing=True)
            require_safe_path(self._state_path)
            descriptor = os.open(self._state_path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as handle:
                metadata = os.fstat(handle.fileno())
                path_metadata = os.lstat(self._state_path)
                if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(path_metadata.st_mode) or getattr(path_metadata, "st_file_attributes", 0) & 0x400 or (metadata.st_dev, metadata.st_ino) != (path_metadata.st_dev, path_metadata.st_ino) or metadata.st_size > _MAX_STATE_BYTES:
                    return default
                contents = handle.read(_MAX_STATE_BYTES + 1)
                if len(contents) > _MAX_STATE_BYTES:
                    return default
                final_path_metadata = os.lstat(self._state_path)
                if (stat.S_ISLNK(final_path_metadata.st_mode)
                        or getattr(final_path_metadata, "st_file_attributes", 0) & 0x400
                        or not stat.S_ISREG(final_path_metadata.st_mode)
                        or (metadata.st_dev, metadata.st_ino) != (final_path_metadata.st_dev, final_path_metadata.st_ino)):
                    return default
                raw = json.loads(contents.decode("utf-8"))
        except (OSError, ValueError, TypeError):
            return default
        if not isinstance(raw, dict):
            return default
        for key, fallback in default.items():
            raw.setdefault(key, fallback)
        if not isinstance(raw["processed_event_ids"], list) or not isinstance(raw["processed_idempotency_keys"], list) or not isinstance(raw["tasks"], dict):
            return default
        return raw

    def _save_state(self) -> None:
        require_safe_path(self.config.state_dir, allow_missing=True)
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        require_safe_path(self.config.state_dir)
        safe = self._bounded_state({key: value for key, value in self._state.items() if key != "matrix_token"})
        # Pending envelopes have already been canonicalized and validated.  Do
        # not run them through a second lossy normalizer, or a crash recovery
        # could emit a different protocol line than the first send attempt.
        pending = self._state.get("pending_outbox")
        if pending is not None and self._validated_pending_messages(pending) is not None:
            safe["pending_outbox"] = pending
        elif pending is not None:
            safe["pending_outbox"] = None
            security = safe.setdefault("security", {})
            security["pending_corrupt"] = min(9999, int(security.get("pending_corrupt", 0)) + 1)
        temporary = self.config.state_dir / f".{self.config.agent_id}.{os.getpid()}.{time.time_ns()}.tmp"
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
        temporary_identity = None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                metadata = os.fstat(handle.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("invalid_runtime_config")
                temporary_identity = (metadata.st_dev, metadata.st_ino)
                json.dump(safe, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            require_safe_path(self.config.state_dir)
            require_safe_path(temporary)
            path_metadata = os.lstat(temporary)
            if stat.S_ISLNK(path_metadata.st_mode) or getattr(path_metadata, "st_file_attributes", 0) & 0x400 or not stat.S_ISREG(path_metadata.st_mode) or temporary_identity != (path_metadata.st_dev, path_metadata.st_ino):
                raise ValueError("invalid_runtime_config")
            os.replace(temporary, self._state_path)
            final_metadata = os.lstat(self._state_path)
            if (stat.S_ISLNK(final_metadata.st_mode)
                    or getattr(final_metadata, "st_file_attributes", 0) & 0x400
                    or not stat.S_ISREG(final_metadata.st_mode)
                    or temporary_identity != (final_metadata.st_dev, final_metadata.st_ino)):
                raise ValueError("invalid_runtime_config")
        finally:
            if temporary.exists():
                try:
                    metadata = temporary.stat(follow_symlinks=False)
                    if temporary_identity == (metadata.st_dev, metadata.st_ino):
                        temporary.unlink()
                except OSError:
                    pass

    def _mark_processed(self, state, event_id) -> None:
        state["processed_event_ids"] = self._trim([*state["processed_event_ids"], event_id])

    def _mark_idempotency(self, state, key) -> None:
        state["processed_idempotency_keys"] = self._trim([*state["processed_idempotency_keys"], key])

    def _security(self, state, name) -> None:
        security = state.setdefault("security", {})
        security[name] = min(9999, int(security.get(name, 0)) + 1)

    @staticmethod
    def _trim(values: list[Any]) -> list[Any]:
        return values[-_MAX_TRACKED:]

    def _trim_tasks(self, state) -> None:
        tasks = state["tasks"]
        while len(tasks) > _MAX_TASK_CONTEXTS:
            tasks.pop(next(iter(tasks)))

    def _append_evidence(self, old, item) -> list[dict[str, Any]]:
        values = list(old) if isinstance(old, list) else []
        values.append(self._bounded_value(item))
        while len(values) > _MAX_EVIDENCE_ITEMS or len(json.dumps(values, ensure_ascii=False)) > _MAX_EVIDENCE_BYTES:
            values.pop(0)
        return values

    def _visible_pause(self) -> None:
        self.pause(float(self.config.pause_seconds))

    def _goal(self, message: AgentProtocolMessage, fallback: Any = None) -> str:
        value = message.payload.get("instruction", message.payload.get("goal", fallback if fallback is not None else message.payload.get("title", "task")))
        return self._text(value)

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default

    @staticmethod
    def _text(value: Any, limit: int = _MAX_TEXT) -> str:
        return value.strip()[:limit] if isinstance(value, str) and value.strip() else "task"

    def _bounded_text_list(self, values: Any, limit: int = 16) -> list[str]:
        return [self._text(value) for value in (values if isinstance(values, list) else [])[:limit] if isinstance(value, str) and value.strip()]

    def _bounded_collection(self, values: Any) -> list[dict[str, Any]]:
        return [self._bounded_value(value) for value in (values if isinstance(values, list) else [])[:_MAX_EVIDENCE_ITEMS] if isinstance(value, Mapping)]

    def _bounded_value(self, value: Any, depth: int = 0) -> Any:
        return self._safe_value(value, depth)

    def _safe_value(self, value: Any, depth: int = 0) -> Any:
        # Evidence frequently has one more layer (kind/value/result/audit_ids)
        # than task context.  The state byte budget below is the hard limit;
        # this depth bound merely prevents pathological nesting from reaching it.
        if depth >= 10:
            return "[truncated]"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return "[redacted]" if _SENSITIVE_TEXT.search(value) else value[:_MAX_TEXT]
        if isinstance(value, Mapping):
            return {
                str(key)[:80]: self._safe_value(item, depth + 1)
                for key, item in list(value.items())[:20]
                if not _is_sensitive_key(str(key))
            }
        if isinstance(value, list):
            return [self._safe_value(item, depth + 1) for item in value[:20]]
        return "[unsupported]"

    def _bounded_state(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Return a redacted, finite JSON document before any filesystem write."""
        safe = self._safe_value(value)
        if not isinstance(safe, dict):
            return {"cursor": None, "processed_event_ids": [], "processed_idempotency_keys": [], "tasks": {}, "security": {}}
        def size() -> int:
            return len(json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        tasks = safe.get("tasks")
        while size() > _MAX_STATE_BYTES and isinstance(tasks, dict) and tasks:
            tasks.pop(next(iter(tasks)))
        for key in ("processed_event_ids", "processed_idempotency_keys"):
            values = safe.get(key)
            while size() > _MAX_STATE_BYTES and isinstance(values, list) and values:
                del values[: max(1, len(values) // 2)]
        if size() > _MAX_STATE_BYTES:
            safe["security"] = {"state_truncated": 1}
        return safe

    @staticmethod
    def _safe_code(code: Any) -> str:
        return code if isinstance(code, str) and code in _SAFE_ERROR_CODES else "runtime_error"


def _is_sensitive_key(key: str) -> bool:
    """Match whole configuration/data-field terms, never incidental substrings."""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
    words = {part for part in re.split(r"[^a-z0-9]+", spaced) if part}
    if words.intersection({"token", "secret", "password", "passwd", "pwd", "authorization", "credential", "cookie", "header", "private", "endpoint", "url", "uri", "stack", "traceback", "exception"}) or words == {"key"}:
        return True
    return ("api" in words and "key" in words) or ("access" in words and "key" in words)
