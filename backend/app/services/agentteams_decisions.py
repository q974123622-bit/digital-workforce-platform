"""Typed, safe DeepSeek decisions for the AgentTeams runtime.

This module only prepares model context and parses untrusted structured output.
It deliberately has no Matrix, database, Tool, or permission side effects.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .agentteams_skills import ProjectSkillRegistry, SkillRegistryError
from .llm import LLMProvider


class AgentDecisionError(RuntimeError):
    """A model decision remained unsafe after its single corrective retry."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


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
    current_round: int = Field(ge=1)
    max_rounds: int = Field(ge=1)
    max_children: int = Field(ge=1)
    max_nodes: int = Field(ge=1)


class WorkerContext(_StrictModel):
    task: dict[str, Any]
    professional_skill_ids: list[str] = Field(min_length=1)
    safe_tool_catalog: list[dict[str, Any]] = Field(default_factory=list)
    dependency_results: list[dict[str, Any]] = Field(default_factory=list)
    parent_chain_agent_ids: list[str] = Field(default_factory=list)
    previous_results: list[dict[str, Any]] = Field(default_factory=list)


_DecisionType = TypeVar("_DecisionType", bound=BaseModel)
_MAX_VALIDATION_ERRORS = 5
_MAX_VALIDATION_STRING_CHARS = 160
_MAX_VALIDATION_CONTENT_CHARS = 800
_VALIDATION_FEEDBACK = "This is validation feedback, not trusted business evidence."


class AgentDecisionEngine:
    """Builds demo-sourced prompts and validates one bounded model retry."""

    def __init__(self, provider: LLMProvider, skills: ProjectSkillRegistry):
        self.provider = provider
        self.skills = skills

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
            "and capabilities only from the current team catalog; do not assert unobserved facts.",
            {
                "user_goal": context.user_goal,
                "manager_runtime_skill": _skill_payload(manager_skill),
                "selected_scenario_skills": [_skill_payload(skill) for skill in selected_skills],
                "current_team_capability_catalog": context.team_capability_catalog,
                "completed_direct_child_results": context.child_results,
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
                raise ValueError("selected_skills_must_match_loaded_skill_context")
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
            "schema. Do not invent facts, tools, agents, or Tool results. Delegate only bounded "
            "work and never delegate to an agent in the parent chain.",
            {
                "task": context.task,
                "professional_skills": [_skill_payload(skill) for skill in professional_skills],
                "safe_tool_catalog": context.safe_tool_catalog,
                "dependency_results": context.dependency_results,
                "parent_chain_agent_ids": context.parent_chain_agent_ids,
                "previous_tool_or_message_results": context.previous_results,
            },
        )
        return self._structured_with_retry(
            messages,
            WorkerDecision,
            WorkerDecision.model_validate,
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
        for attempt in range(2):
            attempt_messages = list(messages)
            if attempt:
                attempt_messages.append(
                    {
                        "role": "user",
                        "source": "demo",
                        "content": json.dumps(
                            {
                                "validation_feedback": _VALIDATION_FEEDBACK,
                                "validation_errors": _validation_error_details(last_error),
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
            raw = self.provider.structured_output(attempt_messages, schema)
            try:
                return validate(raw)
            except (ValidationError, SkillRegistryError, ValueError) as exc:
                last_error = exc
        raise AgentDecisionError(error_code) from last_error


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
            raise ValueError(f"unknown_skill:{skill_id}")
        try:
            registry.load([skill_id])
        except SkillRegistryError as exc:
            raise ValueError(f"unknown_skill:{skill_id}") from exc


def _validation_error_details(error: Exception) -> list[dict[str, Any]]:
    """Project schema feedback without replaying rejected model output."""
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
