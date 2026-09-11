"""Deterministic, audit-backed scoring for the local database benchmark."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agentteams_database_scenarios import ExpectedDiagnosis, SIGNAL_NAMES


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiagnosisResult(_StrictModel):
    root_cause_id: str = Field(min_length=1, max_length=128)
    top_candidates: list[str] = Field(min_length=1, max_length=5)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    conclusion: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def candidates_and_evidence_are_unique(self) -> "DiagnosisResult":
        if len(self.top_candidates) != len(set(self.top_candidates)):
            raise ValueError("duplicate_top_candidate")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("duplicate_result_evidence")
        return self


class ProcessFacts(_StrictModel):
    end_to_end_ms: int = Field(default=0, ge=0)
    model_step_durations_ms: list[int] = Field(default_factory=list, max_length=200)
    tool_evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    followup_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    tool_budget: int | None = Field(default=None, ge=1, le=200)
    critical_path_steps: int | None = Field(default=None, ge=0)
    manager_rounds: int = Field(default=0, ge=0)
    rework_count: int = Field(default=0, ge=0)
    required_child_count: int = Field(default=0, ge=0)
    received_child_result_count: int = Field(default=0, ge=0)
    terminal_status: str = "completed"
    failure_codes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def durations_are_non_negative(self) -> "ProcessFacts":
        if any(isinstance(value, bool) or value < 0 for value in self.model_step_durations_ms):
            raise ValueError("invalid_model_step_duration")
        return self


class DatabaseBenchmarkMetrics(_StrictModel):
    primary_accuracy: float
    top2_hit: float
    evidence_coverage: float
    evidence_validity: float
    secondary_cause_hit: float = 0.0
    conflict_resolution_rate: float = 0.0
    effective_rework_rate: float = 0.0
    budget_success_rate: float = 0.0
    end_to_end_ms: int
    total_model_steps: int
    tool_call_count: int
    duplicate_tool_calls: int
    critical_path_steps: int
    parallel_speedup: float | None
    manager_rounds: int
    rework_count: int
    required_child_count: int
    received_child_result_count: int
    lost_child_results: int
    success_rate: float
    hard_gate_passed: bool
    hard_gate_failures: list[str]


def _evidence_parts(evidence_id: str) -> tuple[str, str] | None:
    if not isinstance(evidence_id, str):
        return None
    parts = evidence_id.split(":")
    if len(parts) != 4 or parts[0] != "db" or not parts[1] or parts[2] not in SIGNAL_NAMES or not parts[3]:
        return None
    return parts[1], parts[2]


def evaluate_diagnosis(
    *,
    scenario_id: str,
    expected: ExpectedDiagnosis,
    result: DiagnosisResult,
    valid_audit_evidence_ids: set[str],
    facts: ProcessFacts | None = None,
) -> DatabaseBenchmarkMetrics:
    process = facts or ProcessFacts()
    failures: list[str] = []
    result_signals: set[str] = set()
    valid_count = 0
    for evidence_id in result.evidence_ids:
        parts = _evidence_parts(evidence_id)
        if parts is None:
            failures.append("invalid_evidence_id")
            continue
        evidence_scenario, signal = parts
        if evidence_scenario != scenario_id:
            failures.append("cross_scenario_evidence")
            continue
        result_signals.add(signal)
        if evidence_id not in valid_audit_evidence_ids:
            failures.append("unaudited_evidence")
        else:
            valid_count += 1

    lost_child_results = max(
        0, process.required_child_count - process.received_child_result_count,
    )
    if lost_child_results:
        failures.append("lost_child_result")
    if process.terminal_status != "completed":
        failures.append("invalid_terminal_status")
    failures.extend(code for code in process.failure_codes if code)
    failures = list(dict.fromkeys(failures))
    accepted_answers = {expected.root_cause_id, *expected.aliases}
    primary_correct = result.root_cause_id in accepted_answers
    top2_correct = any(candidate in accepted_answers for candidate in result.top_candidates[:2])
    required_signals = set(expected.required_signals)
    evidence_coverage = (
        len(required_signals.intersection(result_signals)) / len(required_signals)
        if required_signals else 1.0
    )
    evidence_validity = valid_count / len(result.evidence_ids) if result.evidence_ids else 0.0

    secondary_cause_hit = (
        1.0
        if not expected.secondary_causes
        or set(expected.secondary_causes).issubset(set(result.top_candidates))
        else 0.0
    )
    required_followup = set(expected.requires_followup_signals)
    initial_call_count = max(0, len(process.tool_evidence_ids) - len(process.followup_evidence_ids))
    initial_evidence_ids = set(process.tool_evidence_ids[:initial_call_count])
    unique_valid_followups = {
        evidence_id
        for evidence_id in process.followup_evidence_ids
        if evidence_id in valid_audit_evidence_ids and evidence_id not in initial_evidence_ids
    }
    conflict_resolution_rate = (
        1.0
        if not required_followup or (primary_correct and bool(unique_valid_followups))
        else 0.0
    )
    effective_rework_rate = (
        min(1.0, len(unique_valid_followups) / process.rework_count)
        if process.rework_count
        else (0.0 if required_followup else 1.0)
    )
    is_complex = bool(expected.secondary_causes or expected.requires_followup_signals)
    if process.tool_budget is not None and len(process.tool_evidence_ids) > process.tool_budget:
        failures.append("tool_budget_exceeded")
    failures = list(dict.fromkeys(failures))
    hard_gate_passed = not failures
    quality_passed = (
        (not is_complex or evidence_coverage >= 1.0)
        and (not expected.secondary_causes or secondary_cause_hit >= 1.0)
        and (not required_followup or conflict_resolution_rate >= 1.0)
        and (not required_followup or effective_rework_rate >= 1.0)
    )

    tool_counts = Counter(process.tool_evidence_ids)
    duplicate_tool_calls = sum(count - 1 for count in tool_counts.values() if count > 1)
    total_model_steps = len(process.model_step_durations_ms)
    critical_path_steps = (
        process.critical_path_steps
        if process.critical_path_steps is not None
        else total_model_steps
    )
    serial_model_ms = sum(process.model_step_durations_ms)
    parallel_speedup = (
        serial_model_ms / process.end_to_end_ms
        if serial_model_ms > 0 and process.end_to_end_ms > 0
        else None
    )
    primary_answer_passed = hard_gate_passed and primary_correct
    success = primary_answer_passed and quality_passed
    budget_success = success and (
        process.tool_budget is None
        or len(process.tool_evidence_ids) <= process.tool_budget
    )
    return DatabaseBenchmarkMetrics(
        primary_accuracy=1.0 if primary_answer_passed else 0.0,
        top2_hit=1.0 if hard_gate_passed and top2_correct else 0.0,
        evidence_coverage=evidence_coverage,
        evidence_validity=evidence_validity,
        secondary_cause_hit=secondary_cause_hit,
        conflict_resolution_rate=conflict_resolution_rate,
        effective_rework_rate=effective_rework_rate,
        budget_success_rate=1.0 if budget_success else 0.0,
        end_to_end_ms=process.end_to_end_ms,
        total_model_steps=total_model_steps,
        tool_call_count=len(process.tool_evidence_ids),
        duplicate_tool_calls=duplicate_tool_calls,
        critical_path_steps=critical_path_steps,
        parallel_speedup=parallel_speedup,
        manager_rounds=process.manager_rounds,
        rework_count=process.rework_count,
        required_child_count=process.required_child_count,
        received_child_result_count=process.received_child_result_count,
        lost_child_results=lost_child_results,
        success_rate=1.0 if success else 0.0,
        hard_gate_passed=hard_gate_passed,
        hard_gate_failures=failures,
    )
