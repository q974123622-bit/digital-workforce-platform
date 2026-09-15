"""Measured Single Agent and multi-agent execution for database benchmark runs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.orm import Session

from .. import models
from .agentteams_collaboration import (
    AgentTeamsCollaborationService,
    matrix_user_id_for_runtime,
)
from .agentteams_protocol import AgentProtocolMessage
from .agentteams_database_evaluation import (
    DatabaseBenchmarkMetrics,
    DiagnosisResult,
    ProcessFacts,
    evaluate_diagnosis,
)
from .agentteams_database_scenarios import (
    DatabaseInspectionFilters,
    DatabaseScenario,
    DatabaseScenarioCatalog,
    SIGNAL_FILTER_FIELDS,
    SignalName,
    validate_database_signal_filters,
)
from .gateway import invoke_plugin


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BenchmarkBudget(_FrozenModel):
    max_model_steps: int = Field(ge=1, le=200)
    max_tool_calls: int = Field(ge=1, le=200)
    timeout_ms: int = Field(ge=100, le=3_600_000)


class BenchmarkRunRequest(_FrozenModel):
    run_id: str = Field(min_length=1, max_length=128)
    scenario_id: str = Field(min_length=1, max_length=64)
    budget: BenchmarkBudget
    fairness_fingerprint: str = Field(min_length=1, max_length=128)
    owner_human_no: str = Field(default="", max_length=64)


class BenchmarkRunResult(_FrozenModel):
    run_id: str
    scenario_id: str
    mode: Literal[
        "single_agent",
        "agentteams",
        "agentteams_full",
        "agentteams_serial",
        "agentteams_uncoordinated",
    ]
    status: Literal["completed", "failed", "invalid"]
    fairness_fingerprint: str
    task_run_id: str | None = None
    diagnosis: DiagnosisResult | None = None
    metrics: DatabaseBenchmarkMetrics


class SingleAgentDecision(_FrozenModel):
    action: Literal["tool", "complete"]
    signal: SignalName | None = None
    filters: DatabaseInspectionFilters | None = None
    limit: int | None = Field(default=None, ge=1, le=100)
    result: DiagnosisResult | None = None

    @model_validator(mode="after")
    def payload_matches_action(self) -> "SingleAgentDecision":
        if self.action == "tool":
            if self.signal is None or self.result is not None:
                raise ValueError("tool_payload_required")
            validate_database_signal_filters(
                self.signal, self.filters or DatabaseInspectionFilters(),
            )
        elif self.result is None or any(value is not None for value in (self.signal, self.filters, self.limit)):
            raise ValueError("complete_payload_required")
        return self


class ExpertReport(_FrozenModel):
    role: str = Field(min_length=1, max_length=64)
    finding: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class LeaderReport(_FrozenModel):
    leader_agent_id: str = Field(min_length=1, max_length=64)
    finding: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


DatabaseExpertRole = Literal["db_write", "db_lock", "db_vacuum", "db_index", "db_query"]
AgentTeamsBenchmarkMode = Literal[
    "agentteams_full", "agentteams_serial", "agentteams_uncoordinated",
]

_TEAM_CAPABILITIES: tuple[tuple[DatabaseExpertRole, str, tuple[SignalName, ...]], ...] = (
    ("db_write", "DB-WRITE-01", ("statements", "table_stats")),
    ("db_lock", "DB-LOCK-01", ("locks",)),
    ("db_vacuum", "DB-VACUUM-01", ("vacuum", "table_stats")),
    ("db_index", "DB-INDEX-01", ("indexes", "statements")),
    ("db_query", "DB-QUERY-01", ("statements", "table_stats")),
)
_DATABASE_LEADERS = {
    "db_write": ("DB-OPS-LEAD", "db.operations.coordinate"),
    "db_lock": ("DB-OPS-LEAD", "db.operations.coordinate"),
    "db_vacuum": ("DB-OPS-LEAD", "db.operations.coordinate"),
    "db_index": ("DB-PERF-LEAD", "db.performance.coordinate"),
    "db_query": ("DB-PERF-LEAD", "db.performance.coordinate"),
}


def manager_team_catalog() -> list[dict[str, Any]]:
    """Return the bounded, non-secret catalog that Manager may plan against."""
    return [
        {
            "role": role,
            "agent_id": employee_id,
            "allowed_signals": list(allowed_signals),
            "allowed_filters_by_signal": {
                signal: sorted(SIGNAL_FILTER_FIELDS[signal]) for signal in allowed_signals
            },
        }
        for role, employee_id, allowed_signals in _TEAM_CAPABILITIES
    ]


class ManagerAssignment(_FrozenModel):
    role: DatabaseExpertRole
    assignee_agent_id: str = Field(min_length=1, max_length=64)
    signal: SignalName
    filters: DatabaseInspectionFilters = Field(default_factory=DatabaseInspectionFilters)
    instruction: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def filters_match_signal(self) -> "ManagerAssignment":
        validate_database_signal_filters(self.signal, self.filters)
        return self


class ManagerPlan(_FrozenModel):
    assignments: list[ManagerAssignment] = Field(min_length=1, max_length=5)
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def assignments_match_authorized_team(self) -> "ManagerPlan":
        expected = {
            role: (employee_id, set(allowed_signals))
            for role, employee_id, allowed_signals in _TEAM_CAPABILITIES
        }
        if len({item.role for item in self.assignments}) != len(self.assignments):
            raise ValueError("manager_plan_must_not_repeat_database_role")
        for item in self.assignments:
            employee_id, allowed_signals = expected[item.role]
            if item.assignee_agent_id != employee_id or item.signal not in allowed_signals:
                raise ValueError("manager_plan_assignment_not_authorized")
        return self


class ManagerReview(_FrozenModel):
    action: Literal["follow_up", "complete"]
    assignment: ManagerAssignment | None = None
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def payload_matches_action(self) -> "ManagerReview":
        if self.action == "follow_up" and self.assignment is None:
            raise ValueError("follow_up_assignment_required")
        if self.action == "complete" and self.assignment is not None:
            raise ValueError("complete_must_not_assign_follow_up")
        if self.assignment is not None:
            ManagerPlan(assignments=[self.assignment], rationale=self.rationale)
        return self


class DatabaseBenchmarkBackend(Protocol):
    model_name: str
    model_settings: dict[str, Any]

    def single_decide(self, *, scenario: DatabaseScenario, observations: list[dict], allowed_signals: tuple[str, ...]) -> dict: ...

    def manager_plan(
        self,
        *,
        scenario: DatabaseScenario,
        initial_assignment_count: int,
        reserved_followup_calls: int,
    ) -> dict: ...

    def manager_review(
        self,
        *,
        scenario: DatabaseScenario,
        reports: list[dict],
        observed_signals: list[SignalName],
        remaining_tool_calls: int,
    ) -> dict: ...

    def expert_report(self, *, role: str, scenario: DatabaseScenario, observation: dict) -> dict: ...

    def leader_summarize(
        self,
        *,
        leader_agent_id: str,
        scenario: DatabaseScenario,
        reports: list[dict],
    ) -> dict: ...

    def manager_diagnose(self, *, scenario: DatabaseScenario, reports: list[dict]) -> dict: ...

    def uncoordinated_diagnose(self, *, scenario: DatabaseScenario, reports: list[dict]) -> dict: ...


class DatabaseBenchmarkRunEngine:
    def __init__(
        self,
        db: Session,
        *,
        backend: DatabaseBenchmarkBackend,
        project_root: Path,
    ) -> None:
        self.db = db
        self.backend = backend
        self.catalog = DatabaseScenarioCatalog.from_root(project_root)
        self.root_cause_taxonomy = set(self.catalog.root_cause_taxonomy())

    def _uses_canonical_root_cause_labels(self, result: DiagnosisResult) -> bool:
        return (
            result.root_cause_id in self.root_cause_taxonomy
            and all(candidate in self.root_cause_taxonomy for candidate in result.top_candidates)
        )

    def run_single(self, request: BenchmarkRunRequest) -> BenchmarkRunResult:
        scenario = self.catalog.get(request.scenario_id)
        started = perf_counter()
        observations: list[dict] = []
        model_durations: list[int] = []
        evidence_ids: list[str] = []
        valid_audit_evidence_ids: set[str] = set()
        while True:
            elapsed_ms = self._elapsed_ms(started)
            if elapsed_ms >= request.budget.timeout_ms:
                return self._failed(request, "single_agent", "timeout", elapsed_ms, model_durations, evidence_ids)
            if len(model_durations) >= request.budget.max_model_steps:
                return self._failed(request, "single_agent", "model_step_budget_exhausted", elapsed_ms, model_durations, evidence_ids)
            decision_started = perf_counter()
            raw_decision = self.backend.single_decide(
                scenario=scenario,
                observations=list(observations),
                allowed_signals=scenario.available_signals,
            )
            model_durations.append(self._elapsed_ms(decision_started))
            try:
                decision = SingleAgentDecision.model_validate(raw_decision)
            except ValidationError:
                code = (
                    "single_agent_delegation_forbidden"
                    if isinstance(raw_decision, dict) and raw_decision.get("action") == "delegate"
                    else "invalid_single_agent_decision"
                )
                return self._failed(request, "single_agent", code, self._elapsed_ms(started), model_durations, evidence_ids, invalid=True)
            if decision.action == "complete":
                if not self._uses_canonical_root_cause_labels(decision.result):
                    return self._failed(
                        request,
                        "single_agent",
                        "invalid_single_agent_decision",
                        self._elapsed_ms(started),
                        model_durations,
                        evidence_ids,
                        invalid=True,
                    )
                facts = ProcessFacts(
                    end_to_end_ms=self._elapsed_ms(started),
                    model_step_durations_ms=model_durations,
                    tool_evidence_ids=evidence_ids,
                    terminal_status="completed",
                )
                metrics = evaluate_diagnosis(
                    scenario_id=scenario.id,
                    expected=scenario.expected,
                    result=decision.result,
                    valid_audit_evidence_ids=valid_audit_evidence_ids,
                    facts=facts,
                )
                return BenchmarkRunResult(
                    run_id=request.run_id,
                    scenario_id=scenario.id,
                    mode="single_agent",
                    status="completed" if metrics.hard_gate_passed else "invalid",
                    fairness_fingerprint=request.fairness_fingerprint,
                    diagnosis=decision.result,
                    metrics=metrics,
                )
            if len(evidence_ids) >= request.budget.max_tool_calls:
                return self._failed(
                    request, "single_agent", "tool_call_budget_exhausted",
                    self._elapsed_ms(started), model_durations, evidence_ids,
                )
            observation = self._inspect(
                employee_id="VE-0001",
                request=request,
                signal=decision.signal,
                filters=(
                    decision.filters.model_dump(exclude_none=True)
                    if decision.filters is not None else {}
                ),
                limit=decision.limit or 20,
            )
            observations.append(observation)
            evidence_ids.append(observation["evidence_id"])
            valid_audit_evidence_ids.add(observation["evidence_id"])

    def run_agentteams(
        self,
        request: BenchmarkRunRequest,
        *,
        existing_task_run_id: str | None = None,
        mode: AgentTeamsBenchmarkMode = "agentteams_full",
    ) -> BenchmarkRunResult:
        return self._run_agentteams_hierarchical(
            request,
            existing_task_run_id=existing_task_run_id,
            mode=mode,
        )

    def _run_agentteams_flat_legacy(
        self,
        request: BenchmarkRunRequest,
        *,
        existing_task_run_id: str | None = None,
        mode: AgentTeamsBenchmarkMode = "agentteams_full",
    ) -> BenchmarkRunResult:
        scenario = self.catalog.get(request.scenario_id)
        started = perf_counter()
        model_durations: list[int] = []
        evidence_ids: list[str] = []
        followup_evidence_ids: list[str] = []
        valid_audit_evidence_ids: set[str] = set()
        scenario_tool_budget = min(request.budget.max_tool_calls, scenario.tool_budget)
        initial_assignment_count = (
            len(_TEAM_CAPABILITIES)
            if scenario.complexity == "legacy"
            else scenario_tool_budget - (1 if scenario.expected.requires_followup_signals else 0)
        )
        if initial_assignment_count < 1:
            return self._failed(request, mode, "tool_call_budget_exhausted", 0, [], [])
        required_model_steps = initial_assignment_count + (
            1 if mode == "agentteams_uncoordinated"
            else 2 if scenario.complexity == "legacy"
            else 3 + (1 if scenario.expected.requires_followup_signals else 0)
        )
        if request.budget.max_model_steps < required_model_steps:
            return self._failed(request, mode, "model_step_budget_exhausted", 0, [], [])

        collaboration = AgentTeamsCollaborationService(
            max_children=len(_TEAM_CAPABILITIES),
            capability_catalog_source="database",
        )
        if existing_task_run_id is None:
            task_run = collaboration.create_task(
                self.db,
                team_id="TEAM-DB-BENCH",
                leader_agent_id="VE-0001",
                title=f"数据库异常诊断：{scenario.title}",
                instruction=scenario.task,
                material={
                    "scenario_id": scenario.id,
                    "available_signals": list(scenario.available_signals),
                    "benchmark_run_id": request.run_id,
                },
                owner_human_no=request.owner_human_no,
            )
            task_run.trace_id = request.run_id
        else:
            task_run = self.db.get(models.TaskRun, existing_task_run_id)
            if (
                task_run is None
                or task_run.team_id != "TEAM-DB-BENCH"
                or task_run.owner_human_no != request.owner_human_no
            ):
                raise RuntimeError("database_collaboration_task_mismatch")
        root = self.db.get(models.AgentTaskNode, task_run.id)
        if root is None:
            raise RuntimeError("database_benchmark_root_missing")
        root.trace_id = task_run.trace_id
        self.db.flush()
        self.db.commit()

        manager_id = "VE-0001"
        if mode == "agentteams_uncoordinated":
            plan = self._fixed_uncoordinated_plan(limit=scenario_tool_budget)
        else:
            planning_started = perf_counter()
            raw_plan = self.backend.manager_plan(
                scenario=scenario,
                initial_assignment_count=initial_assignment_count,
                reserved_followup_calls=(
                    1 if scenario.expected.requires_followup_signals else 0
                ),
            )
            model_durations.append(self._elapsed_ms(planning_started))
            try:
                plan = ManagerPlan.model_validate(raw_plan)
            except ValidationError as error:
                self._fail_collaboration_node(
                    collaboration=collaboration,
                    request=request,
                    task_run_id=task_run.id,
                    node=root,
                    code="invalid_manager_plan",
                    validation=self._validation_metadata("manager_plan", error),
                )
                return self._failed(
                    request, mode, "invalid_manager_plan",
                    self._elapsed_ms(started), model_durations, evidence_ids,
                    invalid=True, task_run_id=task_run.id,
                )

        if len(plan.assignments) != (
            scenario_tool_budget if mode == "agentteams_uncoordinated" else initial_assignment_count
        ):
            self._fail_collaboration_node(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=root,
                code="invalid_manager_plan",
            )
            return self._failed(
                request, mode, "invalid_manager_plan",
                self._elapsed_ms(started), model_durations, evidence_ids,
                invalid=True, task_run_id=task_run.id,
            )

        if not isinstance(plan, ManagerPlan):
            self._fail_collaboration_node(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=root,
                code="invalid_manager_plan",
            )
            return self._failed(request, mode, "invalid_manager_plan", self._elapsed_ms(started), model_durations, evidence_ids, invalid=True, task_run_id=task_run.id)

        try:
            for assignment in plan.assignments:
                self.catalog.validate_inspection_request(
                    scenario.id,
                    assignment.signal,
                    filters=assignment.filters.model_dump(exclude_none=True),
                    limit=20,
                )
        except ValueError:
            self._fail_collaboration_node(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=root,
                code="invalid_manager_plan",
            )
            return self._failed(
                request, mode, "invalid_manager_plan",
                self._elapsed_ms(started), model_durations, evidence_ids,
                invalid=True, task_run_id=task_run.id,
            )

        self._record_decision_basis(
            collaboration=collaboration,
            request=request,
            task_run_id=task_run.id,
            node=root,
            stage="planning",
            action="delegate",
            text=plan.rationale,
            suffix="manager-plan-basis",
        )

        delegate = self._protocol_message(
            request=request,
            message_type="task.delegate",
            root_task_id=task_run.id,
            task_id=task_run.id,
            parent_task_id=None,
            sender=manager_id,
            receiver=manager_id,
            suffix="delegate-round-1",
            payload={
                "selected_skill_ids": ["agentteams-database-diagnosis"],
                "planning_rationale": plan.rationale,
                "subtasks": [
                    {
                        "client_task_key": assignment.role,
                        "title": f"{assignment.role} 专项调查",
                        "instruction": assignment.instruction,
                        "assignee_agent_id": assignment.assignee_agent_id,
                        "depends_on": [],
                        "material": {
                            "scenario_id": scenario.id,
                            "signal": assignment.signal,
                            "filters": assignment.filters.model_dump(exclude_none=True),
                        },
                        "required": True,
                        "required_capability": "agentteams-database-inspector",
                    }
                    for assignment in plan.assignments
                ],
            },
        )
        collaboration.apply_message(
            self.db,
            matrix_sender=self._matrix_sender(manager_id),
            message=delegate,
        )

        children = {
            node.client_task_key: node
            for node in self.db.query(models.AgentTaskNode).filter_by(
                root_task_id=task_run.id,
            )
            if node.parent_task_id == task_run.id
        }
        expert_inputs: list[tuple[str, models.AgentTaskNode, dict]] = []
        for assignment in plan.assignments:
            role = assignment.role
            employee_id = assignment.assignee_agent_id
            signal = assignment.signal
            if self._elapsed_ms(started) >= request.budget.timeout_ms:
                return self._failed(request, mode, "timeout", self._elapsed_ms(started), model_durations, evidence_ids)
            child = children.get(role)
            if child is None or child.assignee_agent_id != employee_id:
                return self._failed(
                    request, mode, "missing_child_task",
                    self._elapsed_ms(started), model_durations, evidence_ids, invalid=True,
                )
            sender_mxid = self._matrix_sender(employee_id)
            collaboration.apply_message(
                self.db,
                matrix_sender=sender_mxid,
                message=self._protocol_message(
                    request=request,
                    message_type="task.accepted",
                    root_task_id=task_run.id,
                    task_id=child.id,
                    parent_task_id=task_run.id,
                    sender=employee_id,
                    receiver=manager_id,
                    suffix=f"{role}-accepted",
                    payload={},
                ),
            )
            collaboration.apply_message(
                self.db,
                matrix_sender=sender_mxid,
                message=self._protocol_message(
                    request=request,
                    message_type="task.progress",
                    root_task_id=task_run.id,
                    task_id=child.id,
                    parent_task_id=task_run.id,
                    sender=employee_id,
                    receiver=manager_id,
                    suffix=f"{role}-progress",
                    payload={"progress": 20},
                ),
            )
            collaboration.apply_message(
                self.db,
                matrix_sender=sender_mxid,
                message=self._protocol_message(
                    request=request,
                    message_type="tool.request",
                    root_task_id=task_run.id,
                    task_id=child.id,
                    parent_task_id=task_run.id,
                    sender=employee_id,
                    receiver="platform",
                    suffix=f"{role}-tool",
                    payload={
                        "plugin_id": "agentteams-database-inspector",
                        "action": "read",
                        "params": {
                            "scenario_id": scenario.id,
                            "signal": signal,
                            "filters": assignment.filters.model_dump(exclude_none=True),
                            "limit": 20,
                        },
                    },
                ),
            )
            self.db.flush()
            tool_result = (
                self.db.query(models.AgentMessage)
                .filter_by(
                    root_task_id=task_run.id,
                    task_id=child.id,
                    message_type="tool.result",
                    direction="outbound",
                )
                .order_by(models.AgentMessage.created_at.desc(), models.AgentMessage.id.desc())
                .first()
            )
            if tool_result is None or tool_result.payload.get("ok") is not True:
                return self._failed(
                    request, mode, "database_inspection_failed",
                    self._elapsed_ms(started), model_durations, evidence_ids,
                )
            observation = dict(tool_result.payload.get("data") or {})
            if not observation.get("evidence_id"):
                return self._failed(
                    request, mode, "database_inspection_not_audited",
                    self._elapsed_ms(started), model_durations, evidence_ids, invalid=True,
                )
            evidence_ids.append(observation["evidence_id"])
            valid_audit_evidence_ids.add(observation["evidence_id"])
            expert_inputs.append((role, child, observation))

        self.db.commit()

        def run_expert(expert_input: tuple[str, models.AgentTaskNode, dict]) -> tuple[dict, int]:
            role, _child, observation = expert_input
            step_started = perf_counter()
            raw_report = self.backend.expert_report(
                role=role,
                scenario=scenario,
                observation=observation,
            )
            return raw_report, self._elapsed_ms(step_started)

        # Tool reads stay serial because they share this SQLAlchemy session. The
        # independent expert model steps run concurrently, matching the measured
        # two-stage critical path: experts, then Manager.
        if mode == "agentteams_serial":
            expert_outputs = [run_expert(expert_input) for expert_input in expert_inputs]
        else:
            with ThreadPoolExecutor(max_workers=len(expert_inputs)) as executor:
                expert_outputs = list(executor.map(run_expert, expert_inputs))

        reports: list[dict] = []
        for (role, child, observation), (raw_report, duration_ms) in zip(expert_inputs, expert_outputs, strict=True):
            model_durations.append(duration_ms)
            try:
                report = ExpertReport.model_validate(raw_report)
            except ValidationError:
                self._fail_collaboration_node(
                    collaboration=collaboration,
                    request=request,
                    task_run_id=task_run.id,
                    node=child,
                    code="invalid_child_result",
                )
                return self._failed(
                    request, mode, "invalid_child_result",
                    self._elapsed_ms(started), model_durations, evidence_ids,
                    invalid=True, task_run_id=task_run.id,
                )
            if report.role != role or report.evidence_ids != [observation["evidence_id"]]:
                self._fail_collaboration_node(
                    collaboration=collaboration,
                    request=request,
                    task_run_id=task_run.id,
                    node=child,
                    code="invalid_child_result",
                )
                return self._failed(
                    request, mode, "invalid_child_result",
                    self._elapsed_ms(started), model_durations, evidence_ids,
                    invalid=True, task_run_id=task_run.id,
                )
            report_payload = report.model_dump(mode="json")
            reports.append(report_payload)
            self._record_decision_basis(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=child,
                stage="worker_analysis",
                action="complete",
                text=report.finding,
                suffix=f"{role}-decision-basis",
            )
            self._submit_and_accept_result(
                collaboration=collaboration,
                request=request,
                node=child,
                coordinator_agent_id=manager_id,
                suffix=f"{role}-result",
                payload={**report_payload, "summary": report.finding},
            )

        expected_child_results = len(plan.assignments)
        summary_request = (
            self.db.query(models.AgentMessage)
            .filter_by(
                root_task_id=task_run.id,
                task_id=task_run.id,
                message_type="task.summary",
                direction="outbound",
            )
            .order_by(models.AgentMessage.created_at.desc(), models.AgentMessage.id.desc())
            .first()
        )
        if summary_request is None or len(summary_request.payload.get("child_results", [])) != expected_child_results:
            return self._failed(
                request, mode, "lost_child_result",
                self._elapsed_ms(started), model_durations, evidence_ids, invalid=True,
            )

        manager_rounds = 1 if mode == "agentteams_uncoordinated" else 2
        rework_count = 0
        if scenario.complexity == "coordination" and mode != "agentteams_uncoordinated":
            self.db.commit()
            review_started = perf_counter()
            raw_review = self.backend.manager_review(
                scenario=scenario,
                reports=reports,
                observed_signals=[assignment.signal for assignment in plan.assignments],
                remaining_tool_calls=scenario_tool_budget - len(evidence_ids),
            )
            model_durations.append(self._elapsed_ms(review_started))
            manager_rounds = 3
            try:
                review = ManagerReview.model_validate(raw_review)
            except ValidationError as error:
                self._fail_collaboration_node(
                    collaboration=collaboration,
                    request=request,
                    task_run_id=task_run.id,
                    node=root,
                    code="invalid_manager_review",
                    validation=self._validation_metadata("manager_review", error),
                )
                return self._failed(
                    request, mode, "invalid_manager_review",
                    self._elapsed_ms(started), model_durations, evidence_ids,
                    invalid=True, task_run_id=task_run.id,
                )
            if review.assignment is not None:
                try:
                    self.catalog.validate_inspection_request(
                        scenario.id,
                        review.assignment.signal,
                        filters=review.assignment.filters.model_dump(exclude_none=True),
                        limit=20,
                    )
                except ValueError:
                    self._fail_collaboration_node(
                        collaboration=collaboration,
                        request=request,
                        task_run_id=task_run.id,
                        node=root,
                        code="invalid_manager_review",
                    )
                    return self._failed(
                        request, mode, "invalid_manager_review",
                        self._elapsed_ms(started), model_durations, evidence_ids,
                        invalid=True, task_run_id=task_run.id,
                    )
            self._record_decision_basis(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=root,
                stage="review",
                action=review.action,
                text=review.rationale,
                suffix="manager-review-basis",
                iteration=2,
            )
            if review.action == "follow_up":
                if len(evidence_ids) >= scenario_tool_budget:
                    return self._failed(
                        request, mode, "tool_call_budget_exhausted",
                        self._elapsed_ms(started), model_durations, evidence_ids,
                        task_run_id=task_run.id,
                    )
                assignment = review.assignment
                if assignment is None:
                    raise RuntimeError("validated_follow_up_assignment_missing")
                followup_key = f"{assignment.role}_followup"
                collaboration.apply_message(
                    self.db,
                    matrix_sender=self._matrix_sender(manager_id),
                    message=self._protocol_message(
                        request=request,
                        message_type="task.delegate",
                        root_task_id=task_run.id,
                        task_id=task_run.id,
                        parent_task_id=None,
                        sender=manager_id,
                        receiver=manager_id,
                        suffix="delegate-round-2",
                        payload={
                            "selected_skill_ids": ["agentteams-database-diagnosis"],
                            "planning_rationale": review.rationale,
                            "subtasks": [{
                                "client_task_key": followup_key,
                                "title": f"{assignment.role} 二次复核",
                                "instruction": assignment.instruction,
                                "assignee_agent_id": assignment.assignee_agent_id,
                                "depends_on": [],
                                "material": {
                                    "scenario_id": scenario.id,
                                    "signal": assignment.signal,
                                    "filters": assignment.filters.model_dump(exclude_none=True),
                                    "round": 2,
                                },
                                "required": True,
                                "required_capability": "agentteams-database-inspector",
                            }],
                        },
                    ),
                )
                followup = (
                    self.db.query(models.AgentTaskNode)
                    .filter_by(root_task_id=task_run.id, parent_task_id=task_run.id, client_task_key=followup_key)
                    .one()
                )
                sender_mxid = self._matrix_sender(assignment.assignee_agent_id)
                for message_type, suffix, payload in (
                    ("task.accepted", f"{followup_key}-accepted", {}),
                    ("task.progress", f"{followup_key}-progress", {"progress": 50}),
                    ("tool.request", f"{followup_key}-tool", {
                        "plugin_id": "agentteams-database-inspector",
                        "action": "read",
                        "params": {
                            "scenario_id": scenario.id,
                            "signal": assignment.signal,
                            "filters": assignment.filters.model_dump(exclude_none=True),
                            "limit": 20,
                        },
                    }),
                ):
                    collaboration.apply_message(
                        self.db,
                        matrix_sender=sender_mxid,
                        message=self._protocol_message(
                            request=request,
                            message_type=message_type,
                            root_task_id=task_run.id,
                            task_id=followup.id,
                            parent_task_id=task_run.id,
                            sender=assignment.assignee_agent_id,
                            receiver="platform" if message_type == "tool.request" else manager_id,
                            suffix=suffix,
                            payload=payload,
                        ),
                    )
                self.db.flush()
                followup_tool_result = (
                    self.db.query(models.AgentMessage)
                    .filter_by(
                        root_task_id=task_run.id,
                        task_id=followup.id,
                        message_type="tool.result",
                        direction="outbound",
                    )
                    .order_by(models.AgentMessage.created_at.desc(), models.AgentMessage.id.desc())
                    .first()
                )
                if followup_tool_result is None or followup_tool_result.payload.get("ok") is not True:
                    return self._failed(
                        request, mode, "database_inspection_failed",
                        self._elapsed_ms(started), model_durations, evidence_ids,
                        task_run_id=task_run.id,
                    )
                observation = dict(followup_tool_result.payload.get("data") or {})
                evidence_id = observation.get("evidence_id")
                if not evidence_id:
                    return self._failed(
                        request, mode, "database_inspection_not_audited",
                        self._elapsed_ms(started), model_durations, evidence_ids,
                        invalid=True, task_run_id=task_run.id,
                    )
                evidence_ids.append(evidence_id)
                followup_evidence_ids.append(evidence_id)
                valid_audit_evidence_ids.add(evidence_id)
                self.db.commit()
                expert_started = perf_counter()
                raw_report = self.backend.expert_report(
                    role=assignment.role,
                    scenario=scenario,
                    observation=observation,
                )
                model_durations.append(self._elapsed_ms(expert_started))
                try:
                    report = ExpertReport.model_validate(raw_report)
                except ValidationError:
                    return self._failed(
                        request, mode, "invalid_child_result",
                        self._elapsed_ms(started), model_durations, evidence_ids,
                        invalid=True, task_run_id=task_run.id,
                    )
                if report.role != assignment.role or report.evidence_ids != [evidence_id]:
                    return self._failed(
                        request, mode, "invalid_child_result",
                        self._elapsed_ms(started), model_durations, evidence_ids,
                        invalid=True, task_run_id=task_run.id,
                    )
                report_payload = report.model_dump(mode="json")
                reports.append(report_payload)
                rework_count = 1
                self._record_decision_basis(
                    collaboration=collaboration,
                    request=request,
                    task_run_id=task_run.id,
                    node=followup,
                    stage="worker_followup",
                    action="complete",
                    text=report.finding,
                    suffix=f"{followup_key}-decision-basis",
                    iteration=2,
                )
                self._submit_and_accept_result(
                    collaboration=collaboration,
                    request=request,
                    node=followup,
                    coordinator_agent_id=manager_id,
                    suffix=f"{followup_key}-result",
                    payload={**report_payload, "summary": report.finding},
                )
                expected_child_results += 1
                summary_request = (
                    self.db.query(models.AgentMessage)
                    .filter_by(
                        root_task_id=task_run.id,
                        task_id=task_run.id,
                        message_type="task.summary",
                        direction="outbound",
                    )
                    .order_by(models.AgentMessage.created_at.desc(), models.AgentMessage.id.desc())
                    .first()
                )
                if summary_request is None or len(summary_request.payload.get("child_results", [])) != expected_child_results:
                    return self._failed(
                        request, mode, "lost_child_result",
                        self._elapsed_ms(started), model_durations, evidence_ids,
                        invalid=True, task_run_id=task_run.id,
                    )
        self.db.commit()
        manager_started = perf_counter()
        diagnose = (
            getattr(self.backend, "uncoordinated_diagnose", self.backend.manager_diagnose)
            if mode == "agentteams_uncoordinated"
            else self.backend.manager_diagnose
        )
        raw_diagnosis = diagnose(scenario=scenario, reports=reports)
        model_durations.append(self._elapsed_ms(manager_started))
        try:
            diagnosis = DiagnosisResult.model_validate(raw_diagnosis)
        except ValidationError:
            self._fail_collaboration_node(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=root,
                code="invalid_manager_result",
            )
            return self._failed(
                request, mode, "invalid_manager_result",
                self._elapsed_ms(started), model_durations, evidence_ids,
                invalid=True, task_run_id=task_run.id,
            )
        if not self._uses_canonical_root_cause_labels(diagnosis):
            self._fail_collaboration_node(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=root,
                code="invalid_manager_result",
            )
            return self._failed(
                request, mode, "invalid_manager_result",
                self._elapsed_ms(started), model_durations, evidence_ids,
                invalid=True, task_run_id=task_run.id,
            )
        self._record_decision_basis(
            collaboration=collaboration,
            request=request,
            task_run_id=task_run.id,
            node=root,
            stage="summary",
            action="complete",
            text=diagnosis.conclusion,
            suffix="manager-summary-basis",
            iteration=2,
        )
        collaboration.apply_message(
            self.db,
            matrix_sender=self._matrix_sender(manager_id),
            message=self._protocol_message(
                request=request,
                message_type="task.summary",
                root_task_id=task_run.id,
                task_id=task_run.id,
                parent_task_id=None,
                sender=manager_id,
                receiver="platform",
                suffix="manager-summary",
                payload={
                    **diagnosis.model_dump(mode="json"),
                    "summary": diagnosis.conclusion,
                },
            ),
        )
        self.db.flush()
        facts = ProcessFacts(
            end_to_end_ms=self._elapsed_ms(started),
            model_step_durations_ms=model_durations,
            tool_evidence_ids=evidence_ids,
            followup_evidence_ids=followup_evidence_ids,
            tool_budget=scenario_tool_budget,
            critical_path_steps=(
                (
                    len(model_durations)
                    if mode == "agentteams_serial"
                    else 5 if followup_evidence_ids
                    else 4
                )
                if scenario.complexity == "coordination" and mode != "agentteams_uncoordinated"
                else 7 if mode == "agentteams_serial"
                else 2 if mode == "agentteams_uncoordinated"
                else 3
            ),
            manager_rounds=manager_rounds,
            rework_count=rework_count,
            required_child_count=expected_child_results,
            received_child_result_count=len(reports),
            terminal_status="completed",
        )
        metrics = evaluate_diagnosis(
            scenario_id=scenario.id,
            expected=scenario.expected,
            result=diagnosis,
            valid_audit_evidence_ids=valid_audit_evidence_ids,
            facts=facts,
        )
        return BenchmarkRunResult(
            run_id=request.run_id,
            scenario_id=scenario.id,
            mode=mode,
            status="completed" if metrics.hard_gate_passed else "invalid",
            fairness_fingerprint=request.fairness_fingerprint,
            task_run_id=task_run.id,
            diagnosis=diagnosis,
            metrics=metrics,
        )

    def _run_agentteams_hierarchical(
        self,
        request: BenchmarkRunRequest,
        *,
        existing_task_run_id: str | None,
        mode: AgentTeamsBenchmarkMode,
    ) -> BenchmarkRunResult:
        """Run the benchmark through the same strict three-level task graph as live teams."""
        scenario = self.catalog.get(request.scenario_id)
        started = perf_counter()
        model_durations: list[int] = []
        evidence_ids: list[str] = []
        followup_evidence_ids: list[str] = []
        valid_audit_evidence_ids: set[str] = set()
        scenario_tool_budget = min(request.budget.max_tool_calls, scenario.tool_budget)
        initial_assignment_count = (
            len(_TEAM_CAPABILITIES)
            if scenario.complexity == "legacy"
            else scenario_tool_budget - (1 if scenario.expected.requires_followup_signals else 0)
        )
        if initial_assignment_count < 1:
            return self._failed(request, mode, "tool_call_budget_exhausted", 0, [], [])

        collaboration = AgentTeamsCollaborationService(
            max_children=len(_TEAM_CAPABILITIES),
            capability_catalog_source="database",
        )
        if existing_task_run_id is None:
            task_run = collaboration.create_task(
                self.db,
                team_id="TEAM-DB-BENCH",
                leader_agent_id="VE-0001",
                title=f"数据库异常诊断：{scenario.title}",
                instruction=scenario.task,
                material={
                    "scenario_id": scenario.id,
                    "available_signals": list(scenario.available_signals),
                    "benchmark_run_id": request.run_id,
                },
                owner_human_no=request.owner_human_no,
            )
            task_run.trace_id = request.run_id
        else:
            task_run = self.db.get(models.TaskRun, existing_task_run_id)
            if (
                task_run is None
                or task_run.team_id != "TEAM-DB-BENCH"
                or task_run.owner_human_no != request.owner_human_no
            ):
                raise RuntimeError("database_collaboration_task_mismatch")
        root = self.db.get(models.AgentTaskNode, task_run.id)
        if root is None:
            raise RuntimeError("database_benchmark_root_missing")
        root.trace_id = task_run.trace_id
        self.db.flush()
        self.db.commit()

        manager_id = "VE-0001"
        if mode == "agentteams_uncoordinated":
            plan = self._fixed_uncoordinated_plan(limit=scenario_tool_budget)
        else:
            planning_started = perf_counter()
            raw_plan = self.backend.manager_plan(
                scenario=scenario,
                initial_assignment_count=initial_assignment_count,
                reserved_followup_calls=(1 if scenario.expected.requires_followup_signals else 0),
            )
            model_durations.append(self._elapsed_ms(planning_started))
            try:
                plan = ManagerPlan.model_validate(raw_plan)
            except ValidationError as error:
                self._fail_collaboration_node(
                    collaboration=collaboration,
                    request=request,
                    task_run_id=task_run.id,
                    node=root,
                    code="invalid_manager_plan",
                    validation=self._validation_metadata("manager_plan", error),
                )
                return self._failed(
                    request, mode, "invalid_manager_plan", self._elapsed_ms(started),
                    model_durations, evidence_ids, invalid=True, task_run_id=task_run.id,
                )

        expected_assignment_count = (
            scenario_tool_budget if mode == "agentteams_uncoordinated" else initial_assignment_count
        )
        if len(plan.assignments) != expected_assignment_count:
            self._fail_collaboration_node(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=root,
                code="invalid_manager_plan",
            )
            return self._failed(
                request, mode, "invalid_manager_plan", self._elapsed_ms(started),
                model_durations, evidence_ids, invalid=True, task_run_id=task_run.id,
            )
        try:
            for assignment in plan.assignments:
                self.catalog.validate_inspection_request(
                    scenario.id,
                    assignment.signal,
                    filters=assignment.filters.model_dump(exclude_none=True),
                    limit=20,
                )
        except ValueError:
            self._fail_collaboration_node(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=root,
                code="invalid_manager_plan",
            )
            return self._failed(
                request, mode, "invalid_manager_plan", self._elapsed_ms(started),
                model_durations, evidence_ids, invalid=True, task_run_id=task_run.id,
            )

        initial_leader_count = len({_DATABASE_LEADERS[item.role][0] for item in plan.assignments})
        required_model_steps = len(plan.assignments) + initial_leader_count + 1
        if mode != "agentteams_uncoordinated":
            required_model_steps += 1
            if scenario.complexity == "coordination":
                required_model_steps += 1
                if scenario.expected.requires_followup_signals:
                    required_model_steps += 2
        if request.budget.max_model_steps < required_model_steps:
            self._fail_collaboration_node(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=root,
                code="model_step_budget_exhausted",
            )
            return self._failed(
                request, mode, "model_step_budget_exhausted", self._elapsed_ms(started),
                model_durations, evidence_ids, task_run_id=task_run.id,
            )

        self._record_decision_basis(
            collaboration=collaboration,
            request=request,
            task_run_id=task_run.id,
            node=root,
            stage="planning",
            action="delegate",
            text=plan.rationale,
            suffix="manager-plan-basis",
        )

        expected_leader_results = 0

        def execute_round(
            assignments: list[ManagerAssignment],
            *,
            round_number: int,
            rationale: str,
        ) -> tuple[list[dict], str | None, bool]:
            nonlocal expected_leader_results
            groups: dict[str, dict[str, Any]] = {}
            for assignment in assignments:
                leader_id, coordination_capability = _DATABASE_LEADERS[assignment.role]
                group = groups.setdefault(
                    leader_id,
                    {"capability": coordination_capability, "assignments": []},
                )
                group["assignments"].append(assignment)

            manager_subtasks = []
            for leader_id, group in groups.items():
                manager_subtasks.append({
                    "client_task_key": f"round-{round_number}-{leader_id.lower()}",
                    "title": f"数据库领域调查（{leader_id}）",
                    "instruction": "组织直属数据库 Worker 完成指定检查并汇总证据。",
                    "assignee_agent_id": leader_id,
                    "depends_on": [],
                    "material": {
                        "scenario_id": scenario.id,
                        "round": round_number,
                        "assignments": [
                            {
                                "role": item.role,
                                "signal": item.signal,
                                "filters": item.filters.model_dump(exclude_none=True),
                                "instruction": item.instruction,
                            }
                            for item in group["assignments"]
                        ],
                    },
                    "required": True,
                    "required_capability": group["capability"],
                })
            collaboration.apply_message(
                self.db,
                matrix_sender=self._matrix_sender(manager_id),
                message=self._protocol_message(
                    request=request,
                    message_type="task.delegate",
                    root_task_id=task_run.id,
                    task_id=task_run.id,
                    parent_task_id=None,
                    sender=manager_id,
                    receiver=manager_id,
                    suffix=f"delegate-round-{round_number}",
                    payload={
                        "selected_skill_ids": ["agentteams-database-diagnosis"],
                        "planning_rationale": rationale,
                        "subtasks": manager_subtasks,
                    },
                ),
            )
            leader_nodes = {
                node.client_task_key: node
                for node in self.db.query(models.AgentTaskNode).filter_by(
                    root_task_id=task_run.id,
                    parent_task_id=task_run.id,
                )
            }
            round_leaders: dict[str, models.AgentTaskNode] = {}
            worker_inputs: list[tuple[ManagerAssignment, str, models.AgentTaskNode]] = []
            for leader_id, group in groups.items():
                leader_key = f"round-{round_number}-{leader_id.lower()}"
                leader_node = leader_nodes.get(leader_key)
                if leader_node is None or leader_node.assignee_agent_id != leader_id:
                    return [], "missing_leader_task", True
                round_leaders[leader_id] = leader_node
                leader_sender = self._matrix_sender(leader_id)
                for message_type, suffix, payload in (
                    ("task.accepted", f"{leader_key}-accepted", {}),
                    ("task.progress", f"{leader_key}-progress", {"progress": 10}),
                ):
                    collaboration.apply_message(
                        self.db,
                        matrix_sender=leader_sender,
                        message=self._protocol_message(
                            request=request,
                            message_type=message_type,
                            root_task_id=task_run.id,
                            task_id=leader_node.id,
                            parent_task_id=task_run.id,
                            sender=leader_id,
                            receiver=manager_id,
                            suffix=suffix,
                            payload=payload,
                        ),
                    )
                self._record_decision_basis(
                    collaboration=collaboration,
                    request=request,
                    task_run_id=task_run.id,
                    node=leader_node,
                    stage="leader_delegation",
                    action="delegate",
                    text=f"按领域边界将 {len(group['assignments'])} 项检查派给直属专业 Worker。",
                    suffix=f"{leader_key}-delegation-basis",
                    iteration=round_number,
                )
                collaboration.apply_message(
                    self.db,
                    matrix_sender=leader_sender,
                    message=self._protocol_message(
                        request=request,
                        message_type="task.delegate",
                        root_task_id=task_run.id,
                        task_id=leader_node.id,
                        parent_task_id=task_run.id,
                        sender=leader_id,
                        receiver=leader_id,
                        suffix=f"{leader_key}-delegate-workers",
                        payload={
                            "planning_rationale": "Leader 根据本领域任务选择直属 Worker。",
                            "subtasks": [
                                {
                                    "client_task_key": f"round-{round_number}-{item.role}",
                                    "title": f"{item.role} 专项调查",
                                    "instruction": item.instruction,
                                    "assignee_agent_id": item.assignee_agent_id,
                                    "depends_on": [],
                                    "material": {
                                        "scenario_id": scenario.id,
                                        "signal": item.signal,
                                        "filters": item.filters.model_dump(exclude_none=True),
                                        "round": round_number,
                                    },
                                    "required": True,
                                    "required_capability": "agentteams-database-inspector",
                                }
                                for item in group["assignments"]
                            ],
                        },
                    ),
                )
                workers = {
                    node.client_task_key: node
                    for node in self.db.query(models.AgentTaskNode).filter_by(
                        root_task_id=task_run.id,
                        parent_task_id=leader_node.id,
                    )
                }
                for item in group["assignments"]:
                    worker = workers.get(f"round-{round_number}-{item.role}")
                    if worker is None or worker.assignee_agent_id != item.assignee_agent_id:
                        return [], "missing_child_task", True
                    worker_inputs.append((item, leader_id, worker))

            expert_inputs: list[tuple[ManagerAssignment, str, models.AgentTaskNode, dict]] = []
            for assignment, leader_id, worker in worker_inputs:
                if self._elapsed_ms(started) >= request.budget.timeout_ms:
                    return [], "timeout", False
                sender_mxid = self._matrix_sender(assignment.assignee_agent_id)
                for message_type, suffix, payload in (
                    ("task.accepted", f"{worker.client_task_key}-accepted", {}),
                    ("task.progress", f"{worker.client_task_key}-progress", {"progress": 20}),
                    ("tool.request", f"{worker.client_task_key}-tool", {
                        "plugin_id": "agentteams-database-inspector",
                        "action": "read",
                        "params": {
                            "scenario_id": scenario.id,
                            "signal": assignment.signal,
                            "filters": assignment.filters.model_dump(exclude_none=True),
                            "limit": 20,
                        },
                    }),
                ):
                    collaboration.apply_message(
                        self.db,
                        matrix_sender=sender_mxid,
                        message=self._protocol_message(
                            request=request,
                            message_type=message_type,
                            root_task_id=task_run.id,
                            task_id=worker.id,
                            parent_task_id=worker.parent_task_id,
                            sender=assignment.assignee_agent_id,
                            receiver="platform" if message_type == "tool.request" else leader_id,
                            suffix=suffix,
                            payload=payload,
                        ),
                    )
                self.db.flush()
                tool_result = (
                    self.db.query(models.AgentMessage)
                    .filter_by(
                        root_task_id=task_run.id,
                        task_id=worker.id,
                        message_type="tool.result",
                        direction="outbound",
                    )
                    .order_by(models.AgentMessage.created_at.desc(), models.AgentMessage.id.desc())
                    .first()
                )
                if tool_result is None or tool_result.payload.get("ok") is not True:
                    return [], "database_inspection_failed", False
                observation = dict(tool_result.payload.get("data") or {})
                evidence_id = observation.get("evidence_id")
                if not evidence_id:
                    return [], "database_inspection_not_audited", True
                evidence_ids.append(evidence_id)
                valid_audit_evidence_ids.add(evidence_id)
                if round_number > 1:
                    followup_evidence_ids.append(evidence_id)
                expert_inputs.append((assignment, leader_id, worker, observation))
            self.db.commit()

            def run_expert(item: tuple[ManagerAssignment, str, models.AgentTaskNode, dict]) -> tuple[dict, int]:
                assignment, _leader_id, _worker, observation = item
                step_started = perf_counter()
                raw = self.backend.expert_report(
                    role=assignment.role,
                    scenario=scenario,
                    observation=observation,
                )
                return raw, self._elapsed_ms(step_started)

            if mode == "agentteams_serial":
                expert_outputs = [run_expert(item) for item in expert_inputs]
            else:
                with ThreadPoolExecutor(max_workers=len(expert_inputs)) as executor:
                    expert_outputs = list(executor.map(run_expert, expert_inputs))

            reports_by_leader: dict[str, list[dict]] = {leader_id: [] for leader_id in groups}
            for expert_input, (raw_report, duration_ms) in zip(
                expert_inputs, expert_outputs, strict=True,
            ):
                assignment, leader_id, worker, observation = expert_input
                model_durations.append(duration_ms)
                try:
                    report = ExpertReport.model_validate(raw_report)
                except ValidationError:
                    self._fail_collaboration_node(
                        collaboration=collaboration,
                        request=request,
                        task_run_id=task_run.id,
                        node=worker,
                        code="invalid_child_result",
                    )
                    return [], "invalid_child_result", True
                if report.role != assignment.role or report.evidence_ids != [observation["evidence_id"]]:
                    self._fail_collaboration_node(
                        collaboration=collaboration,
                        request=request,
                        task_run_id=task_run.id,
                        node=worker,
                        code="invalid_child_result",
                    )
                    return [], "invalid_child_result", True
                payload = report.model_dump(mode="json")
                reports_by_leader[leader_id].append(payload)
                self._record_decision_basis(
                    collaboration=collaboration,
                    request=request,
                    task_run_id=task_run.id,
                    node=worker,
                    stage="worker_analysis" if round_number == 1 else "worker_followup",
                    action="complete",
                    text=report.finding,
                    suffix=f"{worker.client_task_key}-decision-basis",
                    iteration=round_number,
                )
                self._submit_and_accept_result(
                    collaboration=collaboration,
                    request=request,
                    node=worker,
                    coordinator_agent_id=leader_id,
                    suffix=f"{worker.client_task_key}-result",
                    payload={**payload, "summary": report.finding},
                )

            def run_leader(item: tuple[str, list[dict]]) -> tuple[dict, int]:
                leader_id, worker_reports = item
                step_started = perf_counter()
                raw = self.backend.leader_summarize(
                    leader_agent_id=leader_id,
                    scenario=scenario,
                    reports=worker_reports,
                )
                return raw, self._elapsed_ms(step_started)

            leader_inputs = list(reports_by_leader.items())
            if mode == "agentteams_serial":
                leader_outputs = [run_leader(item) for item in leader_inputs]
            else:
                with ThreadPoolExecutor(max_workers=len(leader_inputs)) as executor:
                    leader_outputs = list(executor.map(run_leader, leader_inputs))

            round_reports: list[dict] = []
            for (leader_id, worker_reports), (raw_report, duration_ms) in zip(
                leader_inputs, leader_outputs, strict=True,
            ):
                model_durations.append(duration_ms)
                leader_node = round_leaders[leader_id]
                try:
                    leader_report = LeaderReport.model_validate(raw_report)
                except ValidationError:
                    self._fail_collaboration_node(
                        collaboration=collaboration,
                        request=request,
                        task_run_id=task_run.id,
                        node=leader_node,
                        code="invalid_leader_result",
                    )
                    return [], "invalid_leader_result", True
                expected_evidence = list(dict.fromkeys(
                    evidence_id
                    for worker_report in worker_reports
                    for evidence_id in worker_report["evidence_ids"]
                ))
                if (
                    leader_report.leader_agent_id != leader_id
                    or set(leader_report.evidence_ids) != set(expected_evidence)
                    or len(leader_report.evidence_ids) != len(expected_evidence)
                ):
                    self._fail_collaboration_node(
                        collaboration=collaboration,
                        request=request,
                        task_run_id=task_run.id,
                        node=leader_node,
                        code="invalid_leader_result",
                    )
                    return [], "invalid_leader_result", True
                payload = leader_report.model_dump(mode="json")
                round_reports.append(payload)
                self._record_decision_basis(
                    collaboration=collaboration,
                    request=request,
                    task_run_id=task_run.id,
                    node=leader_node,
                    stage="leader_summary",
                    action="complete",
                    text=leader_report.finding,
                    suffix=f"{leader_node.client_task_key}-summary-basis",
                    iteration=round_number,
                )
                self._submit_and_accept_result(
                    collaboration=collaboration,
                    request=request,
                    node=leader_node,
                    coordinator_agent_id=manager_id,
                    suffix=f"{leader_node.client_task_key}-result",
                    payload={**payload, "summary": leader_report.finding},
                )
            expected_leader_results += len(round_reports)
            summary_request = (
                self.db.query(models.AgentMessage)
                .filter_by(
                    root_task_id=task_run.id,
                    task_id=task_run.id,
                    message_type="task.summary",
                    direction="outbound",
                )
                .order_by(models.AgentMessage.created_at.desc(), models.AgentMessage.id.desc())
                .first()
            )
            if (
                summary_request is None
                or len(summary_request.payload.get("child_results", [])) != expected_leader_results
            ):
                return [], "lost_child_result", True
            self.db.commit()
            return round_reports, None, False

        reports, round_error, round_invalid = execute_round(
            plan.assignments,
            round_number=1,
            rationale=plan.rationale,
        )
        if round_error is not None:
            return self._failed(
                request, mode, round_error, self._elapsed_ms(started), model_durations,
                evidence_ids, invalid=round_invalid, task_run_id=task_run.id,
            )

        manager_rounds = 1 if mode == "agentteams_uncoordinated" else 2
        rework_count = 0
        if scenario.complexity == "coordination" and mode != "agentteams_uncoordinated":
            review_started = perf_counter()
            raw_review = self.backend.manager_review(
                scenario=scenario,
                reports=reports,
                observed_signals=[assignment.signal for assignment in plan.assignments],
                remaining_tool_calls=scenario_tool_budget - len(evidence_ids),
            )
            model_durations.append(self._elapsed_ms(review_started))
            manager_rounds = 3
            try:
                review = ManagerReview.model_validate(raw_review)
            except ValidationError as error:
                self._fail_collaboration_node(
                    collaboration=collaboration,
                    request=request,
                    task_run_id=task_run.id,
                    node=root,
                    code="invalid_manager_review",
                    validation=self._validation_metadata("manager_review", error),
                )
                return self._failed(
                    request, mode, "invalid_manager_review", self._elapsed_ms(started),
                    model_durations, evidence_ids, invalid=True, task_run_id=task_run.id,
                )
            if review.assignment is not None:
                try:
                    self.catalog.validate_inspection_request(
                        scenario.id,
                        review.assignment.signal,
                        filters=review.assignment.filters.model_dump(exclude_none=True),
                        limit=20,
                    )
                except ValueError:
                    return self._failed(
                        request, mode, "invalid_manager_review", self._elapsed_ms(started),
                        model_durations, evidence_ids, invalid=True, task_run_id=task_run.id,
                    )
            self._record_decision_basis(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=root,
                stage="review",
                action=review.action,
                text=review.rationale,
                suffix="manager-review-basis",
                iteration=2,
            )
            if review.action == "follow_up":
                if len(evidence_ids) >= scenario_tool_budget:
                    return self._failed(
                        request, mode, "tool_call_budget_exhausted", self._elapsed_ms(started),
                        model_durations, evidence_ids, task_run_id=task_run.id,
                    )
                assignment = review.assignment
                if assignment is None:
                    raise RuntimeError("validated_follow_up_assignment_missing")
                followup_reports, round_error, round_invalid = execute_round(
                    [assignment],
                    round_number=2,
                    rationale=review.rationale,
                )
                if round_error is not None:
                    return self._failed(
                        request, mode, round_error, self._elapsed_ms(started), model_durations,
                        evidence_ids, invalid=round_invalid, task_run_id=task_run.id,
                    )
                reports.extend(followup_reports)
                rework_count = 1

        self.db.commit()
        manager_started = perf_counter()
        diagnose = (
            getattr(self.backend, "uncoordinated_diagnose", self.backend.manager_diagnose)
            if mode == "agentteams_uncoordinated"
            else self.backend.manager_diagnose
        )
        raw_diagnosis = diagnose(scenario=scenario, reports=reports)
        model_durations.append(self._elapsed_ms(manager_started))
        try:
            diagnosis = DiagnosisResult.model_validate(raw_diagnosis)
        except ValidationError:
            self._fail_collaboration_node(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=root,
                code="invalid_manager_result",
            )
            return self._failed(
                request, mode, "invalid_manager_result", self._elapsed_ms(started),
                model_durations, evidence_ids, invalid=True, task_run_id=task_run.id,
            )
        if not self._uses_canonical_root_cause_labels(diagnosis):
            self._fail_collaboration_node(
                collaboration=collaboration,
                request=request,
                task_run_id=task_run.id,
                node=root,
                code="invalid_manager_result",
            )
            return self._failed(
                request, mode, "invalid_manager_result", self._elapsed_ms(started),
                model_durations, evidence_ids, invalid=True, task_run_id=task_run.id,
            )
        self._record_decision_basis(
            collaboration=collaboration,
            request=request,
            task_run_id=task_run.id,
            node=root,
            stage="summary",
            action="complete",
            text=diagnosis.conclusion,
            suffix="manager-summary-basis",
            iteration=2,
        )
        collaboration.apply_message(
            self.db,
            matrix_sender=self._matrix_sender(manager_id),
            message=self._protocol_message(
                request=request,
                message_type="task.summary",
                root_task_id=task_run.id,
                task_id=task_run.id,
                parent_task_id=None,
                sender=manager_id,
                receiver="platform",
                suffix="manager-summary",
                payload={**diagnosis.model_dump(mode="json"), "summary": diagnosis.conclusion},
            ),
        )
        self.db.flush()
        if mode == "agentteams_serial":
            critical_path_steps = len(model_durations)
        elif mode == "agentteams_uncoordinated":
            critical_path_steps = 3
        elif scenario.complexity == "coordination":
            critical_path_steps = 7 if followup_evidence_ids else 5
        else:
            critical_path_steps = 4
        facts = ProcessFacts(
            end_to_end_ms=self._elapsed_ms(started),
            model_step_durations_ms=model_durations,
            tool_evidence_ids=evidence_ids,
            followup_evidence_ids=followup_evidence_ids,
            tool_budget=scenario_tool_budget,
            critical_path_steps=critical_path_steps,
            manager_rounds=manager_rounds,
            rework_count=rework_count,
            required_child_count=expected_leader_results,
            received_child_result_count=len(reports),
            terminal_status="completed",
        )
        metrics = evaluate_diagnosis(
            scenario_id=scenario.id,
            expected=scenario.expected,
            result=diagnosis,
            valid_audit_evidence_ids=valid_audit_evidence_ids,
            facts=facts,
        )
        return BenchmarkRunResult(
            run_id=request.run_id,
            scenario_id=scenario.id,
            mode=mode,
            status="completed" if metrics.hard_gate_passed else "invalid",
            fairness_fingerprint=request.fairness_fingerprint,
            task_run_id=task_run.id,
            diagnosis=diagnosis,
            metrics=metrics,
        )

    @staticmethod
    def _fixed_uncoordinated_plan(*, limit: int = 5) -> ManagerPlan:
        signals: dict[DatabaseExpertRole, SignalName] = {
            "db_write": "statements",
            "db_lock": "locks",
            "db_vacuum": "vacuum",
            "db_index": "indexes",
            "db_query": "table_stats",
        }
        return ManagerPlan(
            assignments=[
                ManagerAssignment(
                    role=role,
                    assignee_agent_id=employee_id,
                    signal=signals[role],
                    instruction=f"按固定模板检查 {signals[role]} 信号。",
                )
                for role, employee_id, _allowed_signals in _TEAM_CAPABILITIES[:limit]
            ],
            rationale="无协调消融组：按固定模板分发任务，不进行 Manager 动态规划。",
        )

    def _record_decision_basis(
        self,
        *,
        collaboration: AgentTeamsCollaborationService,
        request: BenchmarkRunRequest,
        task_run_id: str,
        node: models.AgentTaskNode,
        stage: str,
        action: str,
        text: str,
        suffix: str,
        iteration: int = 1,
    ) -> None:
        """Persist a concise evidence-based decision summary for the clickable UI."""
        normalized = text.strip()[:2048]
        if not normalized:
            return
        collaboration.apply_message(
            self.db,
            matrix_sender=self._matrix_sender(node.assignee_agent_id),
            message=self._protocol_message(
                request=request,
                message_type="agent.message",
                root_task_id=task_run_id,
                task_id=node.id,
                parent_task_id=node.parent_task_id,
                sender=node.assignee_agent_id,
                receiver=node.coordinator_agent_id,
                suffix=suffix,
                payload={
                    "kind": "decision.trace",
                    "stage": stage,
                    "iteration": iteration,
                    "action": action,
                    "reasoning_segments": [
                        normalized[index:index + 480]
                        for index in range(0, len(normalized), 480)
                    ],
                },
            ),
        )

    def _matrix_sender(self, employee_id: str) -> str:
        employee = self.db.get(models.DigitalEmployee, employee_id)
        sender = matrix_user_id_for_runtime(employee.runtime_ref if employee is not None else None)
        if sender is None:
            raise RuntimeError("database_benchmark_runtime_unbound")
        return sender

    def _fail_collaboration_node(
        self,
        *,
        collaboration: AgentTeamsCollaborationService,
        request: BenchmarkRunRequest,
        task_run_id: str,
        node: models.AgentTaskNode,
        code: str,
        validation: dict[str, str] | None = None,
    ) -> None:
        collaboration.apply_message(
            self.db,
            matrix_sender=self._matrix_sender(node.assignee_agent_id),
            message=self._protocol_message(
                request=request,
                message_type="task.failed",
                root_task_id=task_run_id,
                task_id=node.id,
                parent_task_id=node.parent_task_id,
                sender=node.assignee_agent_id,
                receiver=node.coordinator_agent_id,
                suffix=f"{node.client_task_key}-failed-{code}",
                payload={
                    "error_code": code,
                    "error_message": "invalid structured output",
                    **({"validation": validation} if validation else {}),
                },
            ),
        )
        # The benchmark backend has already established that this structured
        # result cannot be recovered. Mirror the live Leader result gate by
        # explicitly stopping the failed Worker graph instead of relying on
        # the pre-AgentScope behavior where Worker failure killed ancestors.
        if (
            node.node_kind == "worker_task"
            and node.coordinator_agent_id != node.assignee_agent_id
        ):
            collaboration.apply_message(
                self.db,
                matrix_sender=self._matrix_sender(node.coordinator_agent_id),
                message=self._protocol_message(
                    request=request,
                    message_type="task.blocked",
                    root_task_id=task_run_id,
                    task_id=node.id,
                    parent_task_id=node.parent_task_id,
                    sender=node.coordinator_agent_id,
                    receiver="platform",
                    suffix=f"{node.client_task_key}-blocked-{code}",
                    payload={
                        "feedback": "结构化结果无效，当前评测运行无法安全恢复。",
                        "reason_code": code,
                    },
                ),
            )
        self.db.flush()

    @staticmethod
    def _validation_metadata(stage: str, error: ValidationError) -> dict[str, str]:
        first = error.errors()[0]
        return {
            "stage": stage,
            "field": ".".join(str(item) for item in first.get("loc", ())) or "request",
            "reason": str(first.get("type") or "validation_error"),
        }

    @staticmethod
    def _protocol_message(
        *,
        request: BenchmarkRunRequest,
        message_type: str,
        root_task_id: str,
        task_id: str,
        parent_task_id: str | None,
        sender: str,
        receiver: str,
        suffix: str,
        payload: dict[str, Any],
    ) -> AgentProtocolMessage:
        message_id = f"DBM-{uuid4().hex}"
        return AgentProtocolMessage(
            version="1.0",
            message_id=message_id,
            type=message_type,
            root_task_id=root_task_id,
            task_id=task_id,
            parent_task_id=parent_task_id,
            trace_id=request.run_id,
            sender=sender,
            receiver=receiver,
            timestamp=datetime.now(timezone.utc).isoformat(),
            idempotency_key=f"{root_task_id}:{suffix}",
            payload=payload,
        )

    def _submit_and_accept_result(
        self,
        *,
        collaboration: AgentTeamsCollaborationService,
        request: BenchmarkRunRequest,
        node: models.AgentTaskNode,
        coordinator_agent_id: str,
        suffix: str,
        payload: dict[str, Any],
    ) -> None:
        """Exercise the same worker submission and coordinator review gate as live runs."""
        collaboration.apply_message(
            self.db,
            matrix_sender=self._matrix_sender(node.assignee_agent_id),
            message=self._protocol_message(
                request=request,
                message_type="task.result",
                root_task_id=node.root_task_id,
                task_id=node.id,
                parent_task_id=node.parent_task_id,
                sender=node.assignee_agent_id,
                receiver=coordinator_agent_id,
                suffix=suffix,
                payload=payload,
            ),
        )
        collaboration.apply_message(
            self.db,
            matrix_sender=self._matrix_sender(coordinator_agent_id),
            message=self._protocol_message(
                request=request,
                message_type="task.result.accepted",
                root_task_id=node.root_task_id,
                task_id=node.id,
                parent_task_id=node.parent_task_id,
                sender=coordinator_agent_id,
                receiver=node.assignee_agent_id,
                suffix=f"{suffix}-accepted",
                payload={"feedback": "benchmark result passed coordinator review"},
            ),
        )

    def _inspect(
        self,
        *,
        employee_id: str,
        request: BenchmarkRunRequest,
        signal: SignalName,
        filters: dict[str, str],
        limit: int,
    ) -> dict:
        response = invoke_plugin(
            self.db,
            employee_id=employee_id,
            plugin_id="agentteams-database-inspector",
            action="read",
            params={
                "scenario_id": request.scenario_id,
                "signal": signal,
                "filters": filters,
                "limit": limit,
            },
            trace_id=request.run_id,
        )
        if response.get("ok") is not True or not response.get("audit_ids"):
            raise RuntimeError("database_inspection_not_audited")
        return dict(response["data"])

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, int(round((perf_counter() - started) * 1000)))

    @staticmethod
    def _failed(
        request: BenchmarkRunRequest,
        mode: Literal[
            "single_agent", "agentteams", "agentteams_full",
            "agentteams_serial", "agentteams_uncoordinated",
        ],
        code: str,
        elapsed_ms: int,
        model_durations: list[int],
        evidence_ids: list[str],
        *,
        invalid: bool = False,
        task_run_id: str | None = None,
    ) -> BenchmarkRunResult:
        metrics = DatabaseBenchmarkMetrics(
            primary_accuracy=0.0,
            top2_hit=0.0,
            evidence_coverage=0.0,
            evidence_validity=0.0,
            end_to_end_ms=elapsed_ms,
            total_model_steps=len(model_durations),
            tool_call_count=len(evidence_ids),
            duplicate_tool_calls=len(evidence_ids) - len(set(evidence_ids)),
            critical_path_steps=len(model_durations),
            parallel_speedup=None,
            manager_rounds=0,
            rework_count=0,
            required_child_count=0,
            received_child_result_count=0,
            lost_child_results=0,
            success_rate=0.0,
            hard_gate_passed=False,
            hard_gate_failures=[code],
        )
        return BenchmarkRunResult(
            run_id=request.run_id,
            scenario_id=request.scenario_id,
            mode=mode,
            status="invalid" if invalid else "failed",
            fairness_fingerprint=request.fairness_fingerprint,
            task_run_id=task_run_id,
            metrics=metrics,
        )
