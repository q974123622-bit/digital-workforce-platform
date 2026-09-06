"""Typed, safe DeepSeek decisions for the AgentTeams runtime.

This module only prepares model context and parses untrusted structured output.
It deliberately has no Matrix, database, Tool, or permission side effects.
"""

from __future__ import annotations

import json
from time import sleep
from typing import Any, Callable, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .agentteams_skills import ProjectSkillRegistry, SkillRegistryError
from .llm import LLMProvider, LLMUnavailableError


class AgentDecisionError(RuntimeError):
    """A model decision remained unsafe after its bounded corrective retry."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _DecisionValidationError(ValueError):
    """A controlled business-rule rejection of an otherwise parsed decision."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillSelection(_StrictModel):
    selected_skill_ids: list[str] = Field(min_length=1)


class PlannedSubtask(_StrictModel):
    client_task_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    required_capability: str = Field(min_length=1)
    assignee_agent_id: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    required: bool = True
    reason: str = Field(min_length=1)


class FinalSummary(_StrictModel):
    summary: str = Field(min_length=1)
    completed_items: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class ManagerDecision(_StrictModel):
    selected_skill_ids: list[str] = Field(min_length=1)
    action: Literal["delegate", "summarize", "fail"]
    subtasks: list[PlannedSubtask] = Field(default_factory=list)
    summary: FinalSummary | None = None
    failure_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def action_payload_is_unambiguous(self) -> "ManagerDecision":
        if self.action == "delegate":
            if not self.subtasks or self.summary is not None or self.failure_reason is not None:
                raise ValueError("delegate_requires_subtasks_only")
        elif self.action == "summarize":
            if self.subtasks or self.summary is None or self.failure_reason is not None:
                raise ValueError("summarize_requires_summary_only")
        elif self.subtasks or self.summary is not None or self.failure_reason is None:
            raise ValueError("fail_requires_failure_reason_only")
        return self


class WorkerDecision(_StrictModel):
    action: Literal["tool", "ask", "delegate", "complete", "fail"]
    tool_name: str | None = Field(default=None, min_length=1)
    tool_arguments: dict[str, Any] | None = None
    recipient_agent_id: str | None = Field(default=None, min_length=1)
    message: str | None = Field(default=None, min_length=1)
    subtask: PlannedSubtask | None = None
    result: str | None = Field(default=None, min_length=1)
    failure_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def action_payload_is_unambiguous(self) -> "WorkerDecision":
        fields_by_action = {
            "tool": {"tool_name", "tool_arguments"},
            "ask": {"recipient_agent_id", "message"},
            "delegate": {"subtask"},
            "complete": {"result"},
            "fail": {"failure_reason"},
        }
        required_by_action = {
            "tool": {"tool_name"},
            "ask": {"recipient_agent_id", "message"},
            "delegate": {"subtask"},
            "complete": {"result"},
            "fail": {"failure_reason"},
        }
        values = {
            "tool_name": self.tool_name,
            "tool_arguments": self.tool_arguments,
            "recipient_agent_id": self.recipient_agent_id,
            "message": self.message,
            "subtask": self.subtask,
            "result": self.result,
            "failure_reason": self.failure_reason,
        }
        allowed = fields_by_action[self.action]
        present = {name for name, value in values.items() if value is not None}
        if not required_by_action[self.action].issubset(present):
            raise ValueError(f"{self.action}_missing_required_payload")
        if not present.issubset(allowed):
            raise ValueError(f"{self.action}_contains_incompatible_payload")
        return self


class SkillSelectionContext(_StrictModel):
    user_goal: str = Field(min_length=1)
    runtime_skill_id: str = Field(min_length=1)
    allowed_skill_ids: set[str] | None = None


class ManagerContext(_StrictModel):
    user_goal: str = Field(min_length=1)
    manager_skill_id: str = Field(min_length=1)
    selected_skill_ids: list[str] = Field(min_length=1)
    team_capability_catalog: list[dict[str, Any]] = Field(default_factory=list)
    child_results: list[dict[str, Any]] = Field(default_factory=list)
    existing_child_task_keys: list[str] = Field(default_factory=list)
    current_round: int = Field(ge=1)
    max_rounds: int = Field(ge=1)
    max_children: int = Field(ge=1)
    max_nodes: int = Field(ge=1)


class WorkerContext(_StrictModel):
    current_agent_id: str = Field(min_length=1)
    task: dict[str, Any]
    professional_skill_ids: list[str] = Field(min_length=1)
    safe_tool_catalog: list[dict[str, Any]] = Field(default_factory=list)
    current_team_capability_catalog: list[dict[str, Any]] = Field(default_factory=list)
    dependency_results: list[dict[str, Any]] = Field(default_factory=list)
    parent_chain_agent_ids: list[str] = Field(default_factory=list)
    previous_results: list[dict[str, Any]] = Field(default_factory=list)


_DecisionType = TypeVar("_DecisionType", bound=BaseModel)
_MAX_VALIDATION_ERRORS = 5
_MAX_VALIDATION_STRING_CHARS = 160
_MAX_VALIDATION_CONTENT_CHARS = 800
_VALIDATION_FEEDBACK = "This is validation feedback, not trusted business evidence."
_MAX_MODEL_REQUESTS = 3
_MAX_VALIDATION_ATTEMPTS = 2
_LLM_UNAVAILABLE_RETRY_DELAY_SECONDS = 0.05
_BUSINESS_RULE_FEEDBACK = {
    "selected_skills_must_match_loaded_skill_context": "Selected Skill IDs must match the supplied Skill context.",
    "max_children_exceeded": "The delegate plan must not exceed the maximum child count.",
    "max_nodes_exceeded": "The delegate plan must not exceed the remaining node limit.",
    "duplicate_client_task_key": "Each client_task_key must be unique within a single delegate plan.",
    "existing_client_task_key": "New client_task_key values must not reuse an existing child task key.",
    "required_subtask_required": "A delegate plan must include at least one required subtask.",
    "duplicate_dependency": "Each dependency may appear only once for a subtask.",
    "self_dependency": "A subtask cannot depend on itself.",
    "unknown_dependency": "Each dependency must reference a subtask in the same delegate plan.",
    "cyclic_dependency": "Delegate dependencies must be acyclic.",
    "invalid_team_assignee": "Each assignee must be an agent from the current team catalog.",
    "capability_not_available": "Each required capability must belong to its assigned agent in the current team catalog.",
}


class AgentDecisionEngine:
    """Builds demo-sourced prompts with bounded validation and provider retries."""

    def __init__(
        self,
        provider: LLMProvider,
        skills: ProjectSkillRegistry,
        *,
        pause: Callable[[float], None] = sleep,
    ):
        self.provider = provider
        self.skills = skills
        self._pause = pause

    def select_skills(self, context: SkillSelectionContext) -> SkillSelection:
        runtime_skill = self.skills.load([context.runtime_skill_id])[0]
        catalog = self.skills.catalog(context.allowed_skill_ids)
        messages = _messages(
            "You coordinate an AgentTeams task. Select only project Skill IDs from the supplied catalog. "
            "Return a JSON object matching the supplied schema and do not invent IDs.",
            {
                "user_goal": context.user_goal,
                "runtime_skill": _skill_payload(runtime_skill),
                "skill_catalog": catalog,
            },
        )

        def validate(raw: dict[str, Any]) -> SkillSelection:
            selection = SkillSelection.model_validate(raw)
            _require_known_and_allowed(
                selection.selected_skill_ids,
                allowed_ids=context.allowed_skill_ids,
                registry=self.skills,
            )
            return selection

        return self._structured_with_retry(
            messages,
            SkillSelection,
            validate,
            error_code="skill_selection_failed",
        )

    def plan(self, context: ManagerContext) -> ManagerDecision:
        manager_skill = self.skills.load([context.manager_skill_id])[0]
        selected_skills = self.skills.load(context.selected_skill_ids)
        messages = _messages(
            "You are an AgentTeams manager. Make a bounded, evidence-oriented decision for "
            "the current round. Return only JSON matching the supplied schema. Choose assignees "
            "and capabilities only from the current team catalog. Within one delegate plan, every "
            "client_task_key must be unique; depends_on may reference only keys in that same batch "
            "and must be acyclic. Each assignee and required capability must exactly match the "
            "current team catalog. New client_task_key values must not reuse an existing child task key; "
            "do not assert unobserved facts.",
            {
                "user_goal": context.user_goal,
                "manager_runtime_skill": _skill_payload(manager_skill),
                "selected_scenario_skills": [_skill_payload(skill) for skill in selected_skills],
                "current_team_capability_catalog": context.team_capability_catalog,
                "completed_direct_child_results": context.child_results,
                "existing_child_task_keys": context.existing_child_task_keys,
                "current_round": context.current_round,
                "limits": {
                    "max_rounds": context.max_rounds,
                    "max_children": context.max_children,
                    "max_nodes": context.max_nodes,
                },
            },
        )

        def validate(raw: dict[str, Any]) -> ManagerDecision:
            decision = ManagerDecision.model_validate(raw)
            if set(decision.selected_skill_ids) != set(context.selected_skill_ids):
                raise _DecisionValidationError("selected_skills_must_match_loaded_skill_context")
            if decision.action == "delegate":
                _validate_manager_delegate_plan(context, decision.subtasks)
            return decision

        return self._structured_with_retry(
            messages,
            ManagerDecision,
            validate,
            error_code="planning_failed",
        )

    def decide_work(self, context: WorkerContext) -> WorkerDecision:
        professional_skills = self.skills.load(context.professional_skill_ids)
        messages = _messages(
            "You are an AgentTeams worker. Select exactly one action permitted by the supplied "
            "schema. Action payload contract: all other action-specific fields must be omitted. "
            "tool: action, tool_name, and optional tool_arguments; for tool, omit recipient_agent_id, "
            "message, subtask, result, and failure_reason. ask: action, recipient_agent_id, and message; "
            "omit tool_name, tool_arguments, subtask, result, and failure_reason. delegate: action and "
            "subtask; omit tool_name, tool_arguments, recipient_agent_id, message, result, and "
            "failure_reason. complete: action and result; omit tool_name, tool_arguments, "
            "recipient_agent_id, message, subtask, and failure_reason. fail: action and failure_reason; "
            "omit tool_name, tool_arguments, recipient_agent_id, message, subtask, and result. "
            "Do not invent facts, tools, agents, or Tool results. Delegate only bounded "
            "work and never delegate to yourself or an agent in the parent chain. Choose Ask and "
            "Delegate recipients by capability from the supplied current-team catalog.",
            {
                "current_agent_id": context.current_agent_id,
                "task": context.task,
                "professional_skills": [_skill_payload(skill) for skill in professional_skills],
                "safe_tool_catalog": context.safe_tool_catalog,
                "current_team_capability_catalog": context.current_team_capability_catalog,
                "dependency_results": context.dependency_results,
                "parent_chain_agent_ids": context.parent_chain_agent_ids,
                "previous_tool_or_message_results": context.previous_results,
            },
        )

        def validate(raw: dict[str, Any]) -> WorkerDecision:
            decision = WorkerDecision.model_validate(raw)
            _validate_worker_decision(context, decision)
            return decision

        return self._structured_with_retry(
            messages,
            WorkerDecision,
            validate,
            error_code="decision_failed",
        )

    def summarize(self, context: ManagerContext) -> FinalSummary:
        manager_skill = self.skills.load([context.manager_skill_id])[0]
        selected_skills = self.skills.load(context.selected_skill_ids)
        messages = _messages(
            "You are an AgentTeams manager. Produce a final evidence-based summary only from the "
            "supplied direct-child results. Preserve unknowns and return only JSON matching the schema.",
            {
                "user_goal": context.user_goal,
                "manager_runtime_skill": _skill_payload(manager_skill),
                "selected_scenario_skills": [_skill_payload(skill) for skill in selected_skills],
                "completed_direct_child_results": context.child_results,
                "current_round": context.current_round,
                "limits": {
                    "max_rounds": context.max_rounds,
                    "max_children": context.max_children,
                    "max_nodes": context.max_nodes,
                },
            },
        )
        return self._structured_with_retry(
            messages,
            FinalSummary,
            FinalSummary.model_validate,
            error_code="summary_failed",
        )

    def _structured_with_retry(
        self,
        messages: list[dict[str, str]],
        schema_model: type[_DecisionType],
        validate: Callable[[dict[str, Any]], _DecisionType],
        *,
        error_code: str,
    ) -> _DecisionType:
        schema = schema_model.model_json_schema()
        schema_message = {
            "role": "system",
            "source": "demo",
            "content": json.dumps(
                {"response_schema": schema},
                ensure_ascii=False,
                sort_keys=True,
            ),
        }
        last_validation_error: Exception | None = None
        validation_attempts = 0
        for request_number in range(_MAX_MODEL_REQUESTS):
            attempt_messages = [*messages, schema_message]
            if last_validation_error is not None:
                attempt_messages.append(
                    {
                        "role": "user",
                        "source": "demo",
                        "content": json.dumps(
                            {
                                "validation_feedback": _VALIDATION_FEEDBACK,
                                "validation_errors": _validation_error_details(last_validation_error),
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
            try:
                raw = self.provider.structured_output(attempt_messages, schema)
            except LLMUnavailableError:
                if request_number + 1 >= _MAX_MODEL_REQUESTS:
                    raise
                self._pause(_LLM_UNAVAILABLE_RETRY_DELAY_SECONDS)
                continue
            try:
                return validate(raw)
            except (ValidationError, _DecisionValidationError) as exc:
                last_validation_error = exc
                validation_attempts += 1
                if request_number + 1 >= _MAX_MODEL_REQUESTS:
                    break
                if validation_attempts >= _MAX_VALIDATION_ATTEMPTS:
                    break
        raise AgentDecisionError(error_code) from last_validation_error


def _messages(instruction: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "source": "demo", "content": instruction},
        {
            "role": "user",
            "source": "demo",
            "content": json.dumps(payload, ensure_ascii=False, default=list),
        },
    ]


def _skill_payload(skill: Any) -> dict[str, str]:
    return {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "description": skill.description,
        "whenToUse": skill.when_to_use,
        "content": skill.content,
    }


def _require_known_and_allowed(
    skill_ids: list[str],
    *,
    allowed_ids: set[str] | None,
    registry: ProjectSkillRegistry,
) -> None:
    for skill_id in skill_ids:
        if allowed_ids is not None and skill_id not in allowed_ids:
            raise _DecisionValidationError(f"unknown_skill:{skill_id}")
        try:
            registry.load([skill_id])
        except SkillRegistryError as exc:
            raise _DecisionValidationError(f"unknown_skill:{skill_id}") from exc


def _validate_manager_delegate_plan(context: ManagerContext, subtasks: list[PlannedSubtask]) -> None:
    """Reject protocol-invalid plans before the manager can emit task.delegate."""
    if len(subtasks) > context.max_children:
        raise _DecisionValidationError("max_children_exceeded")
    if len(subtasks) > context.max_nodes:
        raise _DecisionValidationError("max_nodes_exceeded")

    by_key = {subtask.client_task_key: subtask for subtask in subtasks}
    if len(by_key) != len(subtasks):
        raise _DecisionValidationError("duplicate_client_task_key")
    if set(by_key).intersection(context.existing_child_task_keys):
        raise _DecisionValidationError("existing_client_task_key")
    if not any(subtask.required for subtask in subtasks):
        raise _DecisionValidationError("required_subtask_required")
    for subtask in subtasks:
        dependencies = set(subtask.depends_on)
        if len(dependencies) != len(subtask.depends_on):
            raise _DecisionValidationError("duplicate_dependency")
        if subtask.client_task_key in dependencies:
            raise _DecisionValidationError("self_dependency")
        if not dependencies.issubset(by_key):
            raise _DecisionValidationError("unknown_dependency")

    capabilities_by_agent: dict[str, set[str]] = {}
    for entry in context.team_capability_catalog:
        agent_id = entry.get("agent_id")
        capabilities = entry.get("capabilities")
        if not isinstance(agent_id, str) or not isinstance(capabilities, list):
            continue
        capability_ids = {item for item in capabilities if isinstance(item, str)}
        capabilities_by_agent.setdefault(agent_id, set()).update(capability_ids)
    for subtask in subtasks:
        permitted = capabilities_by_agent.get(subtask.assignee_agent_id)
        if permitted is None:
            raise _DecisionValidationError("invalid_team_assignee")
        if subtask.required_capability not in permitted:
            raise _DecisionValidationError("capability_not_available")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise _DecisionValidationError("cyclic_dependency")
        if key in visited:
            return
        visiting.add(key)
        for dependency in by_key[key].depends_on:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in by_key:
        visit(key)


def _validate_worker_decision(context: WorkerContext, decision: WorkerDecision) -> None:
    """Reject invented worker targets before the runtime performs an action."""
    tool_names = {
        item.get("name")
        for item in context.safe_tool_catalog
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    capabilities_by_agent: dict[str, set[str]] = {}
    for entry in context.current_team_capability_catalog:
        agent_id = entry.get("agent_id")
        capabilities = entry.get("capabilities")
        if not isinstance(agent_id, str) or not isinstance(capabilities, list):
            continue
        capabilities_by_agent.setdefault(agent_id, set()).update(
            item for item in capabilities if isinstance(item, str)
        )

    if decision.action == "tool":
        if decision.tool_name not in tool_names:
            raise _DecisionValidationError("tool_not_available")
        return
    if decision.action == "ask":
        known_recipients = set(capabilities_by_agent).union(context.parent_chain_agent_ids)
        if (
            decision.recipient_agent_id == context.current_agent_id
            or decision.recipient_agent_id not in known_recipients
        ):
            raise _DecisionValidationError("ask_recipient_not_available")
        return
    if decision.action == "delegate":
        subtask = decision.subtask
        if subtask is None:
            raise _DecisionValidationError("delegate_missing_subtask")
        if (
            subtask.assignee_agent_id == context.current_agent_id
            or subtask.assignee_agent_id in context.parent_chain_agent_ids
        ):
            raise _DecisionValidationError("delegate_target_in_parent_chain")
        permitted = capabilities_by_agent.get(subtask.assignee_agent_id)
        if permitted is None:
            raise _DecisionValidationError("delegate_assignee_not_available")
        if subtask.required_capability not in permitted:
            raise _DecisionValidationError("delegate_capability_not_available")


def _validation_error_details(error: Exception) -> list[dict[str, Any]]:
    """Project schema feedback without replaying rejected model output."""
    if isinstance(error, _DecisionValidationError):
        message = _BUSINESS_RULE_FEEDBACK.get(str(error))
        if message is not None:
            return [{"loc": [], "type": "business_rule", "msg": message}]
        return [_generic_validation_error()]
    if not isinstance(error, ValidationError):
        return [_generic_validation_error()]

    projected: list[dict[str, Any]] = []
    for record in error.errors(include_url=False)[:_MAX_VALIDATION_ERRORS]:
        candidate = {
            "loc": _safe_error_location(record.get("loc")),
            "type": _bounded_text(record.get("type"), fallback="validation_error"),
            "msg": _schema_message(record.get("type")),
        }
        serialized = json.dumps([*projected, candidate], ensure_ascii=False)
        if len(serialized) > _MAX_VALIDATION_CONTENT_CHARS:
            break
        projected.append(candidate)
    return projected or [_generic_validation_error()]


def _generic_validation_error() -> dict[str, Any]:
    return {
        "loc": [],
        "type": "validation_error",
        "msg": "The prior decision did not satisfy the required schema.",
    }


def _safe_error_location(location: Any) -> list[str | int]:
    """Preserve only structural path shape; field names can be model-controlled."""
    if not isinstance(location, (list, tuple)):
        return []
    safe: list[str | int] = []
    for part in location[:8]:
        if isinstance(part, int) and 0 <= part <= 9_999:
            safe.append(part)
        else:
            safe.append("field")
    return safe


def _bounded_text(value: Any, *, fallback: str) -> str:
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 - model validation feedback must stay safe
        return fallback
    return text[:_MAX_VALIDATION_STRING_CHARS] or fallback


def _schema_message(error_type: Any) -> str:
    messages = {
        "missing": "A required decision field is missing.",
        "extra_forbidden": "An unexpected decision field is not permitted.",
        "literal_error": "The action must use an allowed value.",
    }
    return _bounded_text(
        messages.get(error_type, "A decision value does not satisfy the required schema."),
        fallback="The prior decision did not satisfy the required schema.",
    )
