"""Typed, safe DeepSeek decisions for the AgentTeams runtime.

This module only prepares model context and parses untrusted structured output.
It deliberately has no Matrix, database, Tool, or permission side effects.
"""

from __future__ import annotations

import json
import re
from time import sleep
from typing import Any, Callable, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, model_validator

from .agentteams_role_change_scenario import (
    ROLE_CHANGE_FIRST_ROUND_CAPABILITIES,
    ROLE_CHANGE_SECOND_ROUND_CAPABILITIES,
    ROLE_CHANGE_SKILL_ID,
)
from .agentteams_skills import ProjectSkillRegistry, SkillRegistryError
from .llm import LLMProvider, LLMUnavailableError
from ..vendor.agentscope_agentteams.taskflow_core import (
    DagTask,
    TaskflowError,
    validate_dag,
)


class AgentDecisionError(RuntimeError):
    """A model decision remained unsafe after its bounded corrective retry."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _DecisionValidationError(ValueError):
    """A controlled business-rule rejection of an otherwise parsed decision."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _reasoning_content: str = PrivateAttr(default="")

    @property
    def reasoning_content(self) -> str:
        return self._reasoning_content

    def _attach_reasoning(self, value: str) -> None:
        self._reasoning_content = value.strip()[:2048]


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


_ENGLISH_READINESS_TOKEN = re.compile(
    r"(?<![A-Za-z_])(?:not_ready|ready|incomplete)(?![A-Za-z_])",
    re.IGNORECASE,
)


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
    readiness_decision: Literal["ready", "not_ready", "incomplete"]
    completed_items: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


def _validate_final_summary_language(summary: FinalSummary) -> None:
    narrative = [
        summary.summary,
        *summary.completed_items,
        *summary.risks,
        *summary.unknowns,
        *summary.next_actions,
    ]
    if any(text and not _contains_chinese(text) for text in narrative):
        raise _DecisionValidationError("user_facing_text_must_contain_chinese")
    if any(_ENGLISH_READINESS_TOKEN.search(text) for text in narrative if text):
        raise _DecisionValidationError("user_facing_text_must_translate_readiness_status")


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
    subtasks: list[PlannedSubtask] = Field(default_factory=list, max_length=5)
    result: str | None = Field(default=None, min_length=1)
    failure_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def action_payload_is_unambiguous(self) -> "WorkerDecision":
        fields_by_action = {
            "tool": {"tool_name", "tool_arguments"},
            "ask": {"recipient_agent_id", "message"},
            "delegate": {"subtask", "subtasks"},
            "complete": {"result"},
            "fail": {"failure_reason"},
        }
        required_by_action = {
            "tool": {"tool_name"},
            "ask": {"recipient_agent_id", "message"},
            "delegate": set(),
            "complete": {"result"},
            "fail": {"failure_reason"},
        }
        values = {
            "tool_name": self.tool_name,
            "tool_arguments": self.tool_arguments,
            "recipient_agent_id": self.recipient_agent_id,
            "message": self.message,
            "subtask": self.subtask,
            "subtasks": self.subtasks or None,
            "result": self.result,
            "failure_reason": self.failure_reason,
        }
        allowed = fields_by_action[self.action]
        present = {name for name, value in values.items() if value is not None}
        if self.action == "delegate" and ("subtask" in present) == ("subtasks" in present):
            raise ValueError("delegate_requires_exactly_one_subtask_shape")
        if not required_by_action[self.action].issubset(present):
            raise ValueError(f"{self.action}_missing_required_payload")
        if not present.issubset(allowed):
            raise ValueError(f"{self.action}_contains_incompatible_payload")
        return self


class ResultReviewDecision(_StrictModel):
    action: Literal["accept", "reject", "replan", "stop_blocked"]
    feedback: str = Field(min_length=1, max_length=512)
    subtasks: list[PlannedSubtask] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def action_payload_is_unambiguous(self) -> "ResultReviewDecision":
        if self.action == "replan" and not self.subtasks:
            raise ValueError("replan_requires_subtasks")
        if self.action != "replan" and self.subtasks:
            raise ValueError(f"{self.action}_contains_incompatible_subtasks")
        return self


class TeamSelectionCandidate(_StrictModel):
    """One Team visible to the Manager before task creation."""

    team_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    leader_agent_id: str = Field(min_length=1)
    leader_name: str = Field(min_length=1)
    workers: list[dict[str, Any]] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class TeamSelectionContext(_StrictModel):
    user_goal: str = Field(min_length=1)
    team_catalog: list[TeamSelectionCandidate] = Field(min_length=1)
    manager_skill_id: str = "agentteams-manager-coordination"


class TeamSelectionDecision(_StrictModel):
    action: Literal["delegate", "no_match"]
    team_id: str | None = Field(default=None, min_length=1)
    leader_agent_id: str | None = Field(default=None, min_length=1)
    reason: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def action_payload_is_unambiguous(self) -> "TeamSelectionDecision":
        if self.action == "delegate":
            if self.team_id is None or self.leader_agent_id is None:
                raise ValueError("delegate_requires_team_and_leader")
        elif self.team_id is not None or self.leader_agent_id is not None:
            raise ValueError("no_match_must_not_select_team")
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
    remaining_rounds: int = Field(ge=0)
    max_children: int = Field(ge=1)
    max_nodes: int = Field(ge=1)
    team_relationship_graph: dict[str, Any] = Field(default_factory=dict)


class WorkerContext(_StrictModel):
    current_agent_id: str = Field(min_length=1)
    task: dict[str, Any]
    professional_skill_ids: list[str] = Field(min_length=1)
    safe_tool_catalog: list[dict[str, Any]] = Field(default_factory=list)
    current_team_capability_catalog: list[dict[str, Any]] = Field(default_factory=list)
    dependency_results: list[dict[str, Any]] = Field(default_factory=list)
    parent_chain_agent_ids: list[str] = Field(default_factory=list)
    previous_results: list[dict[str, Any]] = Field(default_factory=list)
    team_relationship_graph: dict[str, Any] = Field(default_factory=dict)


class ResultReviewContext(_StrictModel):
    reviewer_agent_id: str = Field(min_length=1)
    task: dict[str, Any]
    submitted_result: dict[str, Any]
    result_status: Literal[
        "SUCCESS",
        "SUCCESS_WITH_NOTES",
        "REVISION_NEEDED",
        "BLOCKED",
        "INTERRUPTED",
    ] = "SUCCESS"
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    current_dag: list[dict[str, Any]] = Field(default_factory=list)
    team_capability_catalog: list[dict[str, Any]] = Field(default_factory=list)
    team_relationship_graph: dict[str, Any] = Field(default_factory=dict)
    remaining_recovery_attempts: int = Field(default=0, ge=0)


_DecisionType = TypeVar("_DecisionType", bound=BaseModel)
_MAX_VALIDATION_ERRORS = 5
_MAX_VALIDATION_STRING_CHARS = 160
_MAX_VALIDATION_CONTENT_CHARS = 800
_VALIDATION_FEEDBACK = "This is validation feedback, not trusted business evidence."
_MAX_MODEL_REQUESTS = 3
_MAX_VALIDATION_ATTEMPTS = 2
_LLM_UNAVAILABLE_RETRY_DELAY_SECONDS = 0.05
_ONBOARDING_SKILL_ID = "agentteams-onboarding-readiness"
_ONBOARDING_DEPARTMENT_CAPABILITIES = {
    "hr.onboarding.coordinate",
    "it.onboarding.coordinate",
}
_ONBOARDING_AUDIT_CAPABILITY = "audit.onboarding.coordinate"
_ONBOARDING_FOLLOW_UP_CAPABILITIES = _ONBOARDING_DEPARTMENT_CAPABILITIES
_LEADER_CHILD_CAPABILITIES = {
    "hr.onboarding.coordinate": {"hr.materials.read", "hr.training.read"},
    "it.onboarding.coordinate": {"it.accounts.read", "it.devices.read"},
    "audit.onboarding.coordinate": {"audit.evidence.read"},
    "hr.role_change.coordinate": {"hr.role_change.read"},
    "it.role_change.plan.coordinate": {"it.permission.plan"},
    "audit.role_change.coordinate": {"audit.permission.review"},
    "it.role_change.activate.coordinate": {"it.permission.activate"},
    "db.operations.coordinate": {
        "database.write.inspect", "database.lock.inspect", "database.vacuum.inspect",
    },
    "db.performance.coordinate": {
        "database.index.inspect", "database.query.inspect",
    },
}
_BUSINESS_RULE_FEEDBACK = {
    "selected_skills_must_match_loaded_skill_context": "Selected Skill IDs must match the supplied Skill context.",
    "max_children_exceeded": "The delegate plan must not exceed the maximum child count.",
    "max_nodes_exceeded": "The delegate plan must not exceed the remaining node limit.",
    "duplicate_client_task_key": "Each client_task_key must be unique within a single delegate plan.",
    "existing_client_task_key": "New client_task_key values must not reuse an existing child task key.",
    "existing_recovery_task_key":
        "Replacement client_task_key values must be new and must not reuse a key from the current department DAG.",
    "required_subtask_required": "A delegate plan must include at least one required subtask.",
    "duplicate_dependency": "Each dependency may appear only once for a subtask.",
    "self_dependency": "A subtask cannot depend on itself.",
    "unknown_dependency": "Each dependency must reference a subtask in the same delegate plan.",
    "cyclic_dependency": "Delegate dependencies must be acyclic.",
    "invalid_team_assignee": "Each assignee must be an agent from the current team catalog.",
    "capability_not_available": "Each required capability must belong to its assigned agent in the current team catalog.",
    "manager_must_delegate_to_leader": "A Manager may delegate only to direct Team Leaders.",
    "leader_must_delegate_to_worker": "A Team Leader may delegate only to its own direct Workers.",
    "worker_cannot_delegate": "A Worker must execute or complete its task and cannot create child tasks.",
    "user_facing_text_must_contain_chinese":
        "Rewrite every user-facing narrative field in Simplified Chinese.",
    "user_facing_text_must_translate_readiness_status":
        "Translate ready, not_ready, and incomplete into Chinese inside narrative fields.",
    "role_change_first_round_required":
        "For this role-change scenario, first delegate HR role evidence and IT permission planning in one dependency-free batch.",
    "role_change_follow_up_required":
        "After the first evidence round, delegate audit review and IT activation in one batch, with IT activation depending on audit review.",
    "role_change_final_summary_required":
        "After the approved activation round, return the final successful Chinese summary.",
    "role_change_final_has_unsubstantiated_gaps":
        "All fixed Demo evidence is complete; return empty risks and unknowns instead of inventing gaps.",
    "tool_outside_assigned_capability":
        "Use only a Tool whose required capability matches the capability assigned to this task.",
    "role_change_it_plan_must_return_to_manager":
        "After inspecting the role-change permission plan, return the finding to Manager; do not delegate audit from this first-round task.",
    "delegate_target_not_direct_report":
        "Delegate only to an Agent listed in direct_reports in the supplied platform relationship graph.",
    "invalid_team_relationship_graph":
        "The platform relationship graph must describe the current receiving Agent.",
    "onboarding_department_round_required":
        "For hierarchical onboarding, first delegate one parallel HR coordination task and one parallel IT coordination task.",
    "onboarding_audit_round_required":
        "After both department summaries return, delegate one independent audit evidence review as the second Manager round.",
    "onboarding_targeted_follow_up_required":
        "After the audit returns and round budget remains, delegate one required targeted follow-up to the HR or IT department lead for an observed evidence gap.",
    "onboarding_final_summary_required":
        "After the targeted follow-up round returns, produce the final evidence-based summary.",
    "department_lead_initial_batch_required":
        "A department lead must delegate one parallel required batch covering every direct report and its advertised capability.",
    "department_lead_summary_required":
        "After direct reports return, complete the department task from their evidence instead of delegating again.",
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

    def select_team(self, context: TeamSelectionContext) -> TeamSelectionDecision:
        """Match a goal to a Team and address only its registered Leader.

        The rule follows AgentScope AgentTeams: check matching Teams first and
        delegate to the Team Leader instead of reaching into the Team roster.
        """
        manager_skill = self.skills.load([context.manager_skill_id])[0]
        messages = _messages(
            "You are the existing AgentTeams Manager. Match the user goal against the supplied "
            "Team catalog using Team name, description, capabilities, Team Leader and Worker roster. "
            "Prefer a Team for complex or multi-skill work. If one Team matches, return action "
            "delegate with exactly that team_id and its registered leader_agent_id. Never delegate "
            "to a Team Worker. If no Team matches, return action no_match and omit both IDs. Return "
            "only JSON matching the supplied schema, do not invent IDs, and write reason in concise "
            "Simplified Chinese.",
            {
                "user_goal": context.user_goal,
                "manager_runtime_skill": _skill_payload(manager_skill),
                "team_catalog": [item.model_dump() for item in context.team_catalog],
            },
        )

        def validate(raw: dict[str, Any]) -> TeamSelectionDecision:
            decision = TeamSelectionDecision.model_validate(raw)
            if not _contains_chinese(decision.reason):
                raise _DecisionValidationError("team_selection_reason_must_contain_chinese")
            if decision.action == "no_match":
                return decision
            candidates = {item.team_id: item for item in context.team_catalog}
            candidate = candidates.get(str(decision.team_id))
            if candidate is None:
                raise _DecisionValidationError("unknown_team")
            if decision.leader_agent_id != candidate.leader_agent_id:
                raise _DecisionValidationError("invalid_team_leader")
            return decision

        return self._structured_with_retry(
            messages,
            TeamSelectionDecision,
            validate,
            error_code="team_selection_failed",
        )

    def plan(self, context: ManagerContext) -> ManagerDecision:
        manager_skill = self.skills.load([context.manager_skill_id])[0]
        selected_skills = self.skills.load(context.selected_skill_ids)
        scenario_execution_contract: dict[str, Any] = {}
        if ROLE_CHANGE_SKILL_ID in context.selected_skill_ids:
            if not context.child_results:
                scenario_execution_contract = {
                    "required_action": "delegate",
                    "parallel_capabilities": sorted(ROLE_CHANGE_FIRST_ROUND_CAPABILITIES),
                }
            elif context.current_round == 1:
                scenario_execution_contract = {
                    "required_action": "delegate",
                    "serial_capabilities": sorted(ROLE_CHANGE_SECOND_ROUND_CAPABILITIES),
                    "dependency": "it.permission.activate depends_on audit.permission.review",
                }
            else:
                scenario_execution_contract = {
                    "required_action": "summarize",
                    "required_readiness_decision": "ready",
                    "evidence_precedence": "latest_completed_activation",
                    "required_empty_fields": ["risks", "unknowns"],
                    "narrative_language": "Simplified Chinese",
                    "translate_status_words": {
                        "ready": "已就绪",
                        "approved": "已批准",
                        "completed": "已完成",
                        "pending_audit_review": "待审计复核",
                    },
                }
        elif _ONBOARDING_SKILL_ID in context.selected_skill_ids and context.team_relationship_graph:
            if not context.child_results:
                scenario_execution_contract = {
                    "required_action": "delegate",
                    "parallel_capabilities": sorted(_ONBOARDING_DEPARTMENT_CAPABILITIES),
                    "delegation_level": "department_leads",
                }
            elif context.current_round == 1:
                scenario_execution_contract = {
                    "required_action": "delegate",
                    "parallel_capabilities": [_ONBOARDING_AUDIT_CAPABILITY],
                    "delegation_level": "manager_follow_up",
                }
            elif context.current_round == 2 and context.remaining_rounds > 0:
                scenario_execution_contract = {
                    "required_action": "delegate",
                    "allowed_capabilities": sorted(_ONBOARDING_FOLLOW_UP_CAPABILITIES),
                    "minimum_required_subtasks": 1,
                    "delegation_level": "targeted_evidence_reconciliation",
                    "instruction": "Choose only department leads needed to resolve gaps or contradictions observed in returned evidence.",
                }
            else:
                scenario_execution_contract = {
                    "required_action": "summarize",
                    "evidence_scope": "department_summaries_and_audit_review",
                }
        messages = _messages(
            "You are an AgentTeams manager. Make a bounded, evidence-oriented decision for "
            "the current round. Return only JSON matching the supplied schema. Choose assignees "
            "and capabilities only from the current team catalog. Within one delegate plan, every "
            "client_task_key must be unique; depends_on may reference only keys in that same batch "
            "and must be acyclic. Each assignee and required capability must exactly match the "
            "current team catalog. New client_task_key values must not reuse an existing child task key; "
            "do not assert unobserved facts. Return selected_skill_ids with exactly the selected scenario "
            "Skill IDs supplied below. Do not add, drop, or replace IDs; their order is not significant. "
            "If action is summarize, write every user-facing narrative field in Simplified Chinese while "
            "keeping Agent IDs, evidence IDs, and technical abbreviations unchanged. "
            "Do not write ready, not_ready, or incomplete inside narrative fields; express those statuses in Chinese. "
            "When required evidence is confirmed missing, a summarize action must use readiness_decision "
            "not_ready or incomplete.",
            {
                "user_goal": context.user_goal,
                "manager_runtime_skill": _skill_payload(manager_skill),
                "selected_scenario_skills": [_skill_payload(skill) for skill in selected_skills],
                "required_selected_skill_ids": context.selected_skill_ids,
                "current_team_capability_catalog": context.team_capability_catalog,
                "team_relationship_graph": context.team_relationship_graph,
                "completed_direct_child_results": context.child_results,
                "existing_child_task_keys": context.existing_child_task_keys,
                "current_round": context.current_round,
                "scenario_execution_contract": scenario_execution_contract,
                "limits": {
                    "remaining_rounds": context.remaining_rounds,
                    "max_children": context.max_children,
                    "max_nodes": context.max_nodes,
                },
            },
        )

        def validate(raw: dict[str, Any]) -> ManagerDecision:
            decision = ManagerDecision.model_validate(raw)
            if decision.summary is not None:
                _validate_final_summary_language(decision.summary)
            if set(decision.selected_skill_ids) != set(context.selected_skill_ids):
                raise _DecisionValidationError("selected_skills_must_match_loaded_skill_context")
            if decision.action == "delegate":
                _validate_manager_delegate_plan(context, decision.subtasks)
            _validate_role_change_manager_decision(context, decision)
            _validate_onboarding_manager_decision(context, decision)
            return decision

        return self._structured_with_retry(
            messages,
            ManagerDecision,
            validate,
            error_code="planning_failed",
        )

    def decide_work(self, context: WorkerContext) -> WorkerDecision:
        professional_skills = self.skills.load(context.professional_skill_ids)
        department_execution_contract: dict[str, Any] = {}
        assigned_leader_capability = context.task.get("required_capability")
        if context.team_relationship_graph and assigned_leader_capability in _LEADER_CHILD_CAPABILITIES:
            if context.previous_results:
                department_execution_contract = {
                    "required_action": "complete",
                    "evidence_scope": "completed_direct_reports",
                }
            else:
                department_execution_contract = {
                    "required_action": "delegate",
                    "parallel_direct_reports": _eligible_direct_reports(
                        context.team_relationship_graph,
                        _LEADER_CHILD_CAPABILITIES[assigned_leader_capability],
                    ),
                }
        messages = _messages(
            "You are an AgentTeams worker. Select exactly one action permitted by the supplied "
            "schema. Action payload contract: all other action-specific fields must be omitted. "
            "tool: action, tool_name, and optional tool_arguments; for tool, omit recipient_agent_id, "
            "message, subtask, subtasks, result, and failure_reason. ask: action, recipient_agent_id, and message; "
            "omit tool_name, tool_arguments, subtask, subtasks, result, and failure_reason. delegate: action and "
            "exactly one of subtask or subtasks; use subtasks for one parallel department batch; omit tool_name, "
            "tool_arguments, recipient_agent_id, message, result, and "
            "failure_reason. complete: action and result; omit tool_name, tool_arguments, "
            "recipient_agent_id, message, subtask, subtasks, and failure_reason. fail: action and failure_reason; "
            "omit tool_name, tool_arguments, recipient_agent_id, message, subtask, subtasks, and result. "
            "Do not invent facts, tools, agents, or Tool results. Delegate only bounded "
            "work and never delegate to yourself or an agent in the parent chain. When a team relationship "
            "graph is supplied, delegate only to direct_reports from that graph. Choose Ask and "
            "Delegate recipients by capability from the supplied current-team catalog. Write task "
            "results as concise Chinese prose; do not use key=value syntax because it resembles "
            "secret configuration and will be safely redacted.",
            {
                "current_agent_id": context.current_agent_id,
                "task": context.task,
                "professional_skills": [_skill_payload(skill) for skill in professional_skills],
                "safe_tool_catalog": context.safe_tool_catalog,
                "current_team_capability_catalog": context.current_team_capability_catalog,
                "dependency_results": context.dependency_results,
                "parent_chain_agent_ids": context.parent_chain_agent_ids,
                "team_relationship_graph": context.team_relationship_graph,
                "department_execution_contract": department_execution_contract,
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

    def review_result(self, context: ResultReviewContext) -> ResultReviewDecision:
        messages = _messages(
            "You are the Team Leader result gate. Follow the AgentScope AgentTeams Leader loop: "
            "inspect the subordinate result and evidence, then accept an effective result, replan "
            "the complete bounded replacement DAG, or stop_blocked when recovery is impossible. "
            "The legacy reject action is allowed only for a submitted effective result that should "
            "be revised by the same Worker. Never accept BLOCKED, INTERRUPTED, or REVISION_NEEDED. "
            "For replan, return every replacement subtask and its internal dependencies; assign only "
            "to a direct report with the required capability. Every replacement client_task_key must "
            "be new and must not reuse a key from current_department_dag. Return only "
            "JSON matching the supplied schema. Write feedback in concise Simplified Chinese and "
            "do not invent facts, evidence, URLs, credentials, or hidden system state.",
            {
                "reviewer_agent_id": context.reviewer_agent_id,
                "task": context.task,
                "result_status": context.result_status,
                "submitted_result": context.submitted_result,
                "authorized_tool_evidence": context.evidence,
                "current_department_dag": context.current_dag,
                "current_team_capability_catalog": context.team_capability_catalog,
                "team_relationship_graph": context.team_relationship_graph,
                "remaining_recovery_attempts": context.remaining_recovery_attempts,
            },
        )

        def validate(raw: dict[str, Any]) -> ResultReviewDecision:
            decision = ResultReviewDecision.model_validate(raw)
            if not decision.feedback.strip() or not _contains_chinese(decision.feedback):
                raise _DecisionValidationError("review_feedback_must_contain_chinese")
            _validate_result_review_decision(context, decision)
            return decision

        return self._structured_with_retry(
            messages,
            ResultReviewDecision,
            validate,
            error_code="decision_failed",
        )

    def summarize(self, context: ManagerContext) -> FinalSummary:
        manager_skill = self.skills.load([context.manager_skill_id])[0]
        selected_skills = self.skills.load(context.selected_skill_ids)
        messages = _messages(
            "You are an AgentTeams manager. Produce a final evidence-based summary only from the "
            "supplied direct-child results. Preserve unknowns and return only JSON matching the schema. "
            "Write every user-facing narrative field in Simplified Chinese. Keep Agent IDs, evidence IDs, "
            "and technical abbreviations such as MFA unchanged. "
            "Do not write ready, not_ready, or incomplete inside narrative fields; express those statuses in Chinese. "
            "Return readiness_decision as ready, not_ready, or incomplete. When required evidence is "
            "confirmed missing, readiness_decision must be not_ready or incomplete.",
            {
                "user_goal": context.user_goal,
                "manager_runtime_skill": _skill_payload(manager_skill),
                "selected_scenario_skills": [_skill_payload(skill) for skill in selected_skills],
                "completed_direct_child_results": context.child_results,
                "current_round": context.current_round,
                "limits": {
                    "remaining_rounds": context.remaining_rounds,
                    "max_children": context.max_children,
                    "max_nodes": context.max_nodes,
                },
            },
        )
        def validate(raw: dict[str, Any]) -> FinalSummary:
            summary = FinalSummary.model_validate(raw)
            _validate_final_summary_language(summary)
            return summary

        return self._structured_with_retry(
            messages,
            FinalSummary,
            validate,
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
                response = self.provider.structured_output_with_trace(attempt_messages, schema)
                raw = response.data
            except LLMUnavailableError:
                if request_number + 1 >= _MAX_MODEL_REQUESTS:
                    raise
                self._pause(_LLM_UNAVAILABLE_RETRY_DELAY_SECONDS)
                continue
            try:
                decision = validate(raw)
                decision._attach_reasoning(response.reasoning_content)
                return decision
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

    if context.team_relationship_graph:
        identity = context.team_relationship_graph.get("self")
        if (
            not isinstance(identity, dict)
            or identity.get("level") != "manager"
        ):
            raise _DecisionValidationError("invalid_team_relationship_graph")
        direct_reports = {
            item.get("agent_id")
            for item in context.team_relationship_graph.get("direct_reports", [])
            if isinstance(item, dict) and isinstance(item.get("agent_id"), str)
        }
        if any(subtask.assignee_agent_id not in direct_reports for subtask in subtasks):
            raise _DecisionValidationError("manager_must_delegate_to_leader")

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


def _validate_result_review_decision(
    context: ResultReviewContext,
    decision: ResultReviewDecision,
) -> None:
    """Validate the complete Leader result-gate decision before any task mutation."""
    effective_statuses = {"SUCCESS", "SUCCESS_WITH_NOTES"}
    if decision.action == "accept":
        if context.result_status not in effective_statuses:
            raise _DecisionValidationError("non_effective_result_cannot_be_accepted")
        return
    if decision.action == "reject":
        if context.result_status not in effective_statuses:
            raise _DecisionValidationError("interrupted_result_requires_replan_or_blocked")
        return
    if decision.action == "stop_blocked":
        return
    if context.remaining_recovery_attempts < 1:
        raise _DecisionValidationError("recovery_attempts_exhausted")

    try:
        validate_dag([
            DagTask(
                task_id=item.client_task_key,
                title=item.title,
                assigned_to=item.assignee_agent_id,
                depends_on=list(item.depends_on),
            )
            for item in decision.subtasks
        ])
    except TaskflowError as exc:
        raise _DecisionValidationError("invalid_recovery_dag") from exc
    current_keys = {
        raw.get("client_task_key")
        for raw in context.current_dag
        if isinstance(raw, dict) and isinstance(raw.get("client_task_key"), str)
    }
    if any(item.client_task_key in current_keys for item in decision.subtasks):
        raise _DecisionValidationError("existing_recovery_task_key")
    if not any(item.required for item in decision.subtasks):
        raise _DecisionValidationError("required_subtask_required")

    capabilities_by_agent: dict[str, set[str]] = {}
    for raw in context.team_capability_catalog:
        agent_id = raw.get("agent_id") if isinstance(raw, dict) else None
        capabilities = raw.get("capabilities") if isinstance(raw, dict) else None
        if isinstance(agent_id, str) and isinstance(capabilities, list):
            capabilities_by_agent.setdefault(agent_id, set()).update(
                capability for capability in capabilities if isinstance(capability, str)
            )

    direct_reports: set[str] | None = None
    if context.team_relationship_graph:
        identity = context.team_relationship_graph.get("self")
        if (
            not isinstance(identity, dict)
            or identity.get("agent_id") != context.reviewer_agent_id
            or identity.get("level") not in {"leader", "manager"}
        ):
            raise _DecisionValidationError("invalid_team_relationship_graph")
        direct_reports = {
            item.get("agent_id")
            for item in context.team_relationship_graph.get("direct_reports", [])
            if isinstance(item, dict) and isinstance(item.get("agent_id"), str)
        }

    for item in decision.subtasks:
        if direct_reports is not None and item.assignee_agent_id not in direct_reports:
            raise _DecisionValidationError("leader_must_delegate_to_worker")
        permitted = capabilities_by_agent.get(item.assignee_agent_id)
        if permitted is None:
            raise _DecisionValidationError("delegate_assignee_not_available")
        if item.required_capability not in permitted:
            raise _DecisionValidationError("delegate_capability_not_available")


def _validate_role_change_manager_decision(
    context: ManagerContext,
    decision: ManagerDecision,
) -> None:
    """Keep the fixed role-change demo evidence-led without replacing model planning."""
    if ROLE_CHANGE_SKILL_ID not in context.selected_skill_ids:
        return

    by_capability = {
        subtask.required_capability: subtask
        for subtask in decision.subtasks
    }
    if not context.child_results:
        expected = ROLE_CHANGE_FIRST_ROUND_CAPABILITIES
        if (
            decision.action != "delegate"
            or set(by_capability) != expected
            or any(by_capability[item].depends_on for item in expected)
        ):
            raise _DecisionValidationError("role_change_first_round_required")
        return

    if context.current_round == 1:
        expected = ROLE_CHANGE_SECOND_ROUND_CAPABILITIES
        audit = by_capability.get("audit.role_change.coordinate")
        activation = by_capability.get("it.role_change.activate.coordinate")
        if (
            decision.action != "delegate"
            or set(by_capability) != expected
            or audit is None
            or activation is None
            or audit.depends_on
            or activation.depends_on != [audit.client_task_key]
        ):
            raise _DecisionValidationError("role_change_follow_up_required")
        return

    if (
        decision.action != "summarize"
        or decision.summary is None
        or decision.summary.readiness_decision != "ready"
    ):
        raise _DecisionValidationError("role_change_final_summary_required")
    if decision.summary.risks or decision.summary.unknowns:
        raise _DecisionValidationError("role_change_final_has_unsubstantiated_gaps")


def _validate_onboarding_manager_decision(
    context: ManagerContext,
    decision: ManagerDecision,
) -> None:
    """Apply the hierarchical onboarding rounds only to the expanded graph."""
    if (
        _ONBOARDING_SKILL_ID not in context.selected_skill_ids
        or not context.team_relationship_graph
    ):
        return
    by_capability = {
        subtask.required_capability: subtask
        for subtask in decision.subtasks
    }
    if not context.child_results:
        if (
            decision.action != "delegate"
            or set(by_capability) != _ONBOARDING_DEPARTMENT_CAPABILITIES
            or any(subtask.depends_on for subtask in decision.subtasks)
            or any(not subtask.required for subtask in decision.subtasks)
        ):
            raise _DecisionValidationError("onboarding_department_round_required")
        return
    if context.current_round == 1:
        audit = by_capability.get(_ONBOARDING_AUDIT_CAPABILITY)
        if (
            decision.action != "delegate"
            or set(by_capability) != {_ONBOARDING_AUDIT_CAPABILITY}
            or audit is None
            or audit.depends_on
            or not audit.required
        ):
            raise _DecisionValidationError("onboarding_audit_round_required")
        return
    if context.current_round == 2 and context.remaining_rounds > 0:
        capabilities = set(by_capability)
        if (
            decision.action != "delegate"
            or not capabilities
            or not capabilities.issubset(_ONBOARDING_FOLLOW_UP_CAPABILITIES)
            or len(capabilities) != len(decision.subtasks)
            or any(subtask.depends_on for subtask in decision.subtasks)
            or any(not subtask.required for subtask in decision.subtasks)
        ):
            raise _DecisionValidationError("onboarding_targeted_follow_up_required")
        return
    if decision.action != "summarize" or decision.summary is None:
        raise _DecisionValidationError("onboarding_final_summary_required")


def _eligible_direct_reports(
    relationship_graph: dict[str, Any], allowed_capabilities: set[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in relationship_graph.get("direct_reports", []):
        if not isinstance(raw, dict):
            continue
        capabilities = raw.get("capabilities")
        if not isinstance(capabilities, list):
            continue
        selected = [
            capability for capability in capabilities
            if isinstance(capability, str) and capability in allowed_capabilities
        ]
        if selected:
            result.append({**raw, "capabilities": selected})
    return result


def _validate_worker_decision(context: WorkerContext, decision: WorkerDecision) -> None:
    """Reject invented worker targets before the runtime performs an action."""
    assigned_leader_capability = context.task.get("required_capability")
    is_team_leader_task = (
        assigned_leader_capability in _LEADER_CHILD_CAPABILITIES
        and bool(context.team_relationship_graph)
    )
    if is_team_leader_task:
        if context.previous_results and decision.action != "complete":
            raise _DecisionValidationError("department_lead_summary_required")
        if not context.previous_results and decision.action != "delegate":
            raise _DecisionValidationError("department_lead_initial_batch_required")
    tools_by_name = {
        item.get("name"): item
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
        tool = tools_by_name.get(decision.tool_name)
        if tool is None:
            raise _DecisionValidationError("tool_not_available")
        assigned_capability = context.task.get("required_capability")
        if (
            isinstance(assigned_capability, str)
            and assigned_capability
            and tool.get("required_capability") != assigned_capability
        ):
            raise _DecisionValidationError("tool_outside_assigned_capability")
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
        subtasks = decision.subtasks or ([decision.subtask] if decision.subtask is not None else [])
        if not subtasks:
            raise _DecisionValidationError("delegate_missing_subtask")
        direct_reports = {
            item.get("agent_id")
            for item in context.team_relationship_graph.get("direct_reports", [])
            if isinstance(item, dict) and isinstance(item.get("agent_id"), str)
        }
        if context.team_relationship_graph:
            identity = context.team_relationship_graph.get("self")
            if (
                not isinstance(identity, dict)
                or identity.get("agent_id") != context.current_agent_id
                or identity.get("level") not in {"leader", "worker"}
            ):
                raise _DecisionValidationError("invalid_team_relationship_graph")
            if identity.get("level") == "worker":
                raise _DecisionValidationError("worker_cannot_delegate")
        keys = {subtask.client_task_key for subtask in subtasks}
        if len(keys) != len(subtasks):
            raise _DecisionValidationError("duplicate_client_task_key")
        for subtask in subtasks:
            if (
                subtask.assignee_agent_id == context.current_agent_id
                or subtask.assignee_agent_id in context.parent_chain_agent_ids
            ):
                raise _DecisionValidationError("delegate_target_in_parent_chain")
            if context.team_relationship_graph and subtask.assignee_agent_id not in direct_reports:
                raise _DecisionValidationError("leader_must_delegate_to_worker")
            permitted = capabilities_by_agent.get(subtask.assignee_agent_id)
            if permitted is None:
                raise _DecisionValidationError("delegate_assignee_not_available")
            if subtask.required_capability not in permitted:
                raise _DecisionValidationError("delegate_capability_not_available")
        if assigned_leader_capability in _LEADER_CHILD_CAPABILITIES:
            allowed_child_capabilities = _LEADER_CHILD_CAPABILITIES[assigned_leader_capability]
            expected = {
                (item.get("agent_id"), capability)
                for item in context.team_relationship_graph.get("direct_reports", [])
                if isinstance(item, dict)
                for capability in item.get("capabilities", [])
                if (
                    isinstance(item.get("agent_id"), str)
                    and isinstance(capability, str)
                    and capability in allowed_child_capabilities
                )
            }
            actual = {
                (subtask.assignee_agent_id, subtask.required_capability)
                for subtask in subtasks
            }
            if (
                context.previous_results
                or actual != expected
                or any(subtask.depends_on for subtask in subtasks)
                or any(not subtask.required for subtask in subtasks)
            ):
                raise _DecisionValidationError(
                    "department_lead_summary_required"
                    if context.previous_results
                    else "department_lead_initial_batch_required"
                )
        return


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
