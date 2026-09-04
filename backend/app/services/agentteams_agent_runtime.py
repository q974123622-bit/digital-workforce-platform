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
import json
import os
from pathlib import Path
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


Role = Literal["manager", "hr", "it", "audit"]
_ROLE_AGENT_IDS = {"manager": "VE-0001", "hr": "VE-0002", "it": "VE-0003", "audit": "VE-0005"}
_ROLE_PROFESSIONAL_SKILLS = {
    "hr": {"hr-onboarding-assistant"},
    "it": {"it-service-assistant"},
    "audit": {"audit-evidence-review"},
}
_SAFE_ERROR_CODES = {
    "skill_selection_failed", "planning_failed", "decision_failed", "summary_failed",
    "completion_without_tool_evidence", "invalid_model_decision", "runtime_error",
}
_MAX_TRACKED = 256
_MAX_TASK_CONTEXTS = 32
_MAX_EVIDENCE_ITEMS = 8
_MAX_EVIDENCE_BYTES = 4096
_MAX_TEXT = 512


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
        if self.role not in _ROLE_AGENT_IDS or self.agent_id != _ROLE_AGENT_IDS[self.role]:
            raise ValueError("invalid_runtime_identity")
        if not self.matrix_mxid.startswith("@") or not self.matrix_token or not self.room_id:
            raise ValueError("invalid_runtime_config")
        if self.trusted_sender_mxids.get(self.agent_id) != self.matrix_mxid:
            raise ValueError("invalid_runtime_config")
        if not self.trusted_sender_mxids.get("platform"):
            raise ValueError("invalid_runtime_config")
        if not self.allowed_skill_ids:
            raise ValueError("invalid_runtime_config")
        object.__setattr__(self, "state_dir", Path(self.state_dir))
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

    def run_once(self) -> int:
        """Handle one ordered Matrix sync batch and return handled protocol count.

        The persisted Matrix cursor moves only once every event in the batch has
        completed safely. Individual event IDs are persisted immediately after a
        successful response, so a later send failure cannot replay them.
        """
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
        handled = 0
        for event in events:
            if not isinstance(event.event_id, str) or not event.event_id:
                continue
            if event.event_id in self._state["processed_event_ids"]:
                continue
            trial = deepcopy(self._state)
            outcome = self._process_event(event, trial)
            if outcome is None:
                # A network failure left this event wholly uncommitted.
                return handled
            if outcome:
                handled += outcome
            self._mark_processed(trial, event.event_id)
            self._state = trial
            self._save_state()
        # Only a fully-consumed batch advances the opaque Matrix token.
        self._state["cursor"] = next_cursor
        self._save_state()
        return handled

    def run_forever(self, stop_event: Any) -> None:
        while not stop_event.is_set():
            self.run_once()
            stop_event.wait(1.0)

    def close(self) -> None:
        self.gateway.close()

    def _process_event(self, event: MatrixEvent, state: dict[str, Any]) -> int | bool | None:
        if event.sender == self.config.matrix_mxid:
            return False
        try:
            messages = parse_protocol_lines(event.body)
        except (json.JSONDecodeError, ValidationError, ValueError):
            self._security(state, "parse_error")
            return False
        count = 0
        for message in messages:
            if message.receiver != self.config.agent_id:
                continue
            expected_mxid = self.config.trusted_sender_mxids.get(message.sender)
            if expected_mxid != event.sender:
                self._security(state, "spoofed_sender")
                continue
            if message.idempotency_key in state["processed_idempotency_keys"]:
                continue
            try:
                responses = self._handle_message(message, event, state)
            except AgentDecisionError as exc:
                responses = [self._failure(message, self._safe_code(exc.code))]
            except Exception:
                responses = [self._failure(message, "runtime_error")]
            if not self._send_responses(responses):
                return None
            self._mark_idempotency(state, message.idempotency_key)
            count += 1
        return count

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
        catalog = self._team_catalog(message.payload)
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
            goal=goal, selected=selected, catalog=catalog, child_results=[], current_round=1, limits={}
        ))
        self._remember_manager(state, message, goal, selected, catalog)
        return [skill_message, *self._manager_decision(message, decision, catalog, 1)]

    def _manager_review(self, message: AgentProtocolMessage, state: dict[str, Any]) -> list[AgentProtocolMessage]:
        stored = state["tasks"].get(message.task_id, {})
        selected = self._safe_selected(stored.get("selected_skill_ids", []), self.config.scenario_skill_ids)
        if not selected:
            raise AgentDecisionError("planning_failed")
        catalog = stored.get("team_catalog", {})
        if not isinstance(catalog, dict):
            catalog = {}
        current_round = self._positive_int(message.payload.get("current_round"), 1)
        self._visible_pause()
        decision = self.decisions.plan(self._manager_context(
            goal=self._goal(message, stored.get("goal")), selected=selected, catalog=catalog,
            child_results=self._bounded_collection(message.payload.get("child_results")),
            current_round=current_round, limits=message.payload.get("remaining_limits", {}),
        ))
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
        evidence = {"kind": evidence_kind, "value": self._bounded_value(message.payload)}
        context["evidence"] = self._append_evidence(context.get("evidence", []), evidence)
        self._visible_pause()
        return self._worker_decide(message, state, context, 0)

    def _worker_decide(self, message: AgentProtocolMessage, state: dict[str, Any], context: dict[str, Any], sequence: int) -> list[AgentProtocolMessage]:
        decision = self.decisions.decide_work(WorkerContext(
            task=dict(context["task"]), professional_skill_ids=["employee-collaboration", *context["selected_skill_ids"]],
            safe_tool_catalog=[self._bounded_value(item) for item in self.config.safe_tool_catalog],
            dependency_results=self._bounded_collection(context["task"].get("dependency_results")),
            parent_chain_agent_ids=self._bounded_text_list(context["parent_chain_agent_ids"]),
            previous_results=self._bounded_collection(context.get("evidence", [])),
        ))
        if decision.action == "tool":
            tool = self._tool(decision.tool_name)
            if tool is None:
                raise AgentDecisionError("decision_failed")
            return [self._reply(message, "tool.request", "platform", {
                "plugin_id": tool["plugin_id"], "action": tool["action"],
                "params": self._bounded_value(decision.tool_arguments or {}),
            }, sequence)]
        if decision.action == "ask":
            recipient = decision.recipient_agent_id
            if recipient not in self.config.trusted_sender_mxids or recipient in {"platform", self.config.agent_id}:
                raise AgentDecisionError("decision_failed")
            return [self._reply(message, "agent.message", recipient, {"kind": "peer.ask", "text": self._text(decision.message)}, sequence)]
        if decision.action == "delegate":
            subtask = self._safe_subtask(decision.subtask, self.config.team_catalog, parent_chain=context["parent_chain_agent_ids"])
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
        try:
            for message in messages:
                event_id = self.gateway.send_protocol_message(self.config.room_id, message.type, render_protocol_line(message))
                if not event_id:
                    return False
        except Exception:
            return False
        return True

    def _manager_context(self, *, goal, selected, catalog, child_results, current_round, limits) -> ManagerContext:
        safe_limits = limits if isinstance(limits, Mapping) else {}
        return ManagerContext(
            user_goal=goal, manager_skill_id="agentteams-manager-coordination", selected_skill_ids=selected,
            team_capability_catalog=[{"agent_id": key, **self._bounded_value(value)} for key, value in catalog.items()],
            child_results=child_results, current_round=current_round,
            max_rounds=self._positive_int(safe_limits.get("rounds"), 3),
            max_children=self._positive_int(safe_limits.get("children"), 5),
            max_nodes=self._positive_int(safe_limits.get("nodes"), 20),
        )

    def _remember_manager(self, state, message, goal, selected, catalog) -> None:
        state["tasks"][message.task_id] = {"kind": "manager", "goal": goal, "selected_skill_ids": selected, "team_catalog": catalog}
        self._trim_tasks(state)

    def _new_worker_context(self, message, selected) -> dict[str, Any]:
        return {
            "kind": "worker", "selected_skill_ids": selected, "completion_corrected": False, "evidence": [],
            "parent_chain_agent_ids": [message.sender] if message.sender != "platform" else [],
            "task": {"title": self._text(message.payload.get("title", "task")), "goal": self._goal(message),
                     "client_task_key": self._text(message.payload.get("client_task_key", "task")),
                     "dependency_results": self._bounded_collection(message.payload.get("dependency_results"))},
        }

    def _safe_subtask(self, subtask: PlannedSubtask | None, catalog: Mapping[str, Any], *, parent_chain: list[str] | None = None) -> dict[str, Any]:
        if subtask is None or not subtask.required_capability or subtask.assignee_agent_id in set(parent_chain or []):
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

    def _team_catalog(self, payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        raw = payload.get("team_capability_catalog")
        catalog: dict[str, dict[str, Any]] = {}
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, Mapping) and isinstance(item.get("agent_id"), str):
                    caps = item.get("capabilities", [])
                    if isinstance(caps, list) and all(isinstance(value, str) for value in caps):
                        catalog[item["agent_id"]] = {"capabilities": self._bounded_text_list(caps, 32)}
        return catalog or {key: self._bounded_value(value) for key, value in self.config.team_catalog.items()}

    def _tool(self, name: Any) -> dict[str, str] | None:
        if not isinstance(name, str):
            return None
        for item in self.config.safe_tool_catalog:
            if item.get("name") == name and isinstance(item.get("plugin_id"), str):
                return {"plugin_id": item["plugin_id"], "action": str(item.get("action", "read"))}
        return None

    def _safe_selected(self, values: Any, allowed: set[str]) -> list[str]:
        if not isinstance(values, list) or not values or any(not isinstance(item, str) for item in values):
            raise AgentDecisionError("skill_selection_failed")
        if len(values) != len(set(values)) or not set(values).issubset(allowed):
            raise AgentDecisionError("skill_selection_failed")
        return list(values)

    def _successful_audit_ids(self, evidence: Any) -> list[str]:
        if not isinstance(evidence, list):
            return []
        for item in reversed(evidence):
            value = item.get("value") if isinstance(item, Mapping) and item.get("kind") == "tool.result" else None
            if isinstance(value, Mapping) and value.get("ok") is True:
                ids = value.get("audit_ids")
                if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids):
                    return self._bounded_text_list(ids, 16)
        return []

    def _load_state(self) -> dict[str, Any]:
        default = {"cursor": None, "processed_event_ids": [], "processed_idempotency_keys": [], "tasks": {}, "security": {}}
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
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
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        safe = self._bounded_value({key: value for key, value in self._state.items() if key != "matrix_token"})
        temporary = self._state_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(safe, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._state_path)

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
        if depth >= 4:
            return "[truncated]"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value[:_MAX_TEXT]
        if isinstance(value, Mapping):
            return {str(key)[:80]: self._bounded_value(item, depth + 1) for key, item in list(value.items())[:20] if "token" not in str(key).lower() and "secret" not in str(key).lower()}
        if isinstance(value, list):
            return [self._bounded_value(item, depth + 1) for item in value[:20]]
        return "[unsupported]"

    @staticmethod
    def _safe_code(code: Any) -> str:
        return code if isinstance(code, str) and code in _SAFE_ERROR_CODES else "runtime_error"
