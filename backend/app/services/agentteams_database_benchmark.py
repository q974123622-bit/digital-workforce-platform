"""Persistent experiment matrix and aggregation for the database benchmark."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..seed import sync_database_benchmark_resources
from . import config
from .agentteams_database_benchmark_backend import (
    DATABASE_BENCHMARK_PROMPT_CONTRACT,
    DATABASE_BENCHMARK_REQUEST_TIMEOUT_SECONDS,
)
from .agentteams_database_benchmark_runner import BenchmarkBudget, BenchmarkRunRequest
from .agentteams_database_scenarios import DatabaseScenarioCatalog
from .llm import LLMUnavailableError


_BENCHMARK_ID = "multiagentbench-database-local-v1"
_MODES = ("agentteams_full", "agentteams_serial", "agentteams_uncoordinated")
_TERMINAL_RUN_STATUSES = {"completed", "failed", "invalid"}
_SAFE_LLM_FAILURE_CODES = {
    "network_error": "llm_network_error",
    "rate_limited": "llm_rate_limited",
    "client_error": "llm_client_error",
    "server_error": "llm_server_error",
    "http_error": "llm_http_error",
    "invalid_response_json": "llm_invalid_response",
    "invalid_structured_json": "llm_invalid_structured_output",
    "llm_unavailable": "llm_unavailable",
}


class ActiveBenchmarkExperimentError(RuntimeError):
    def __init__(self, experiment_id: str) -> None:
        super().__init__("benchmark_experiment_already_running")
        self.experiment_id = experiment_id


def _safe_run_failure_code(exc: Exception) -> str:
    if isinstance(exc, LLMUnavailableError):
        return _SAFE_LLM_FAILURE_CODES.get(exc.code, "llm_unavailable")
    return "run_execution_failed"


class DatabaseBenchmarkService:
    def __init__(self, *, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self.catalog = DatabaseScenarioCatalog.from_root(self.project_root)

    def create_experiment(
        self,
        db: Session,
        *,
        owner_human_no: str,
        scenario_ids: list[str],
        repeats: int,
        fairness_config: dict[str, Any],
    ) -> models.BenchmarkExperiment:
        if (
            not owner_human_no
            or isinstance(repeats, bool)
            or not isinstance(repeats, int)
            or not 1 <= repeats <= 10
            or not scenario_ids
            or len(scenario_ids) != len(set(scenario_ids))
            or any(scenario_id not in self.catalog.ids() for scenario_id in scenario_ids)
        ):
            raise ValueError("invalid_benchmark_experiment")
        try:
            budget = BenchmarkBudget.model_validate(fairness_config)
        except Exception:
            raise ValueError("invalid_benchmark_experiment") from None
        active = db.scalar(
            select(models.BenchmarkExperiment)
            .where(
                models.BenchmarkExperiment.owner_human_no == owner_human_no,
                models.BenchmarkExperiment.status.in_(("pending", "running")),
            )
            .order_by(
                models.BenchmarkExperiment.created_at.desc(),
                models.BenchmarkExperiment.id.desc(),
            )
            .limit(1)
        )
        if active is not None:
            raise ActiveBenchmarkExperimentError(active.id)
        normalized_config = {
            **budget.model_dump(mode="json"),
            "model": config.agentteams_worker_model(),
            "model_settings": {
                "thinking": False,
                "reasoning_effort": "low",
                "structured_output": "json_object",
                "request_timeout_seconds": DATABASE_BENCHMARK_REQUEST_TIMEOUT_SECONDS,
                "prompt_contract": DATABASE_BENCHMARK_PROMPT_CONTRACT,
            },
            "memory_enabled": False,
            "cache_enabled": False,
            "dataset": _BENCHMARK_ID,
        }
        encoded = json.dumps(
            normalized_config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = sha256(encoded).hexdigest()
        sync_database_benchmark_resources(db, commit=False)
        experiment = models.BenchmarkExperiment(
            id=f"DBE-{uuid4().hex}",
            owner_human_no=owner_human_no,
            benchmark_id=_BENCHMARK_ID,
            status="pending",
            scenario_ids=list(scenario_ids),
            repeats=repeats,
            fairness_config=normalized_config,
            fairness_fingerprint=fingerprint,
            summary={},
        )
        db.add(experiment)
        ordinal = 0
        for scenario_id in scenario_ids:
            for repeat_index in range(1, repeats + 1):
                for mode in _MODES:
                    ordinal += 1
                    db.add(models.BenchmarkRun(
                        id=f"DBR-{uuid4().hex}",
                        experiment_id=experiment.id,
                        ordinal=ordinal,
                        scenario_id=scenario_id,
                        repeat_index=repeat_index,
                        mode=mode,
                        status="pending",
                        fairness_fingerprint=fingerprint,
                        metrics={},
                        diagnosis={},
                    ))
        db.commit()
        db.refresh(experiment)
        return experiment

    def execute_experiment(self, db: Session, experiment_id: str, *, engine: Any) -> models.BenchmarkExperiment:
        experiment = db.get(models.BenchmarkExperiment, experiment_id)
        if experiment is None:
            raise ValueError("benchmark_experiment_not_found")
        if experiment.status == "completed":
            return experiment
        experiment.status = "running"
        experiment.started_at = experiment.started_at or datetime.now()
        db.commit()
        runs = list(db.scalars(
            select(models.BenchmarkRun)
            .where(models.BenchmarkRun.experiment_id == experiment.id)
            .order_by(models.BenchmarkRun.ordinal)
        ))
        budget = BenchmarkBudget.model_validate({
            key: experiment.fairness_config[key]
            for key in ("max_model_steps", "max_tool_calls", "timeout_ms")
        })
        for run in runs:
            if run.status in _TERMINAL_RUN_STATUSES:
                continue
            run.status = "running"
            run.started_at = run.started_at or datetime.now()
            run.failure_code = ""
            db.commit()
            try:
                request = BenchmarkRunRequest(
                    run_id=run.id,
                    scenario_id=run.scenario_id,
                    budget=budget,
                    fairness_fingerprint=experiment.fairness_fingerprint,
                    owner_human_no=experiment.owner_human_no,
                )
                result = engine.run_agentteams(request, mode=run.mode)
                if result.fairness_fingerprint != experiment.fairness_fingerprint:
                    run.status = "invalid"
                    run.failure_code = "fairness_fingerprint_mismatch"
                    run.metrics = {}
                    run.diagnosis = {}
                else:
                    run.status = result.status
                    run.task_run_id = result.task_run_id
                    run.metrics = result.metrics.model_dump(mode="json")
                    run.diagnosis = (
                        result.diagnosis.model_dump(mode="json")
                        if result.diagnosis is not None else {}
                    )
                    failures = result.metrics.hard_gate_failures
                    run.failure_code = failures[0] if failures else ""
            except Exception as exc:
                run.status = "failed"
                run.failure_code = _safe_run_failure_code(exc)
                run.metrics = {}
                run.diagnosis = {}
            run.finished_at = datetime.now()
            db.commit()
        experiment.summary = self._aggregate(runs)
        experiment.status = "completed"
        experiment.finished_at = datetime.now()
        db.commit()
        db.refresh(experiment)
        return experiment

    @staticmethod
    def _aggregate(runs: list[models.BenchmarkRun]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for mode in _MODES:
            mode_runs = [
                run for run in runs
                if run.mode == mode and run.status == "completed" and run.metrics
            ]
            timings = sorted(
                int(run.metrics.get("end_to_end_ms", 0)) for run in mode_runs
            )
            valid_answer_timings = sorted(
                int(run.metrics.get("end_to_end_ms", 0))
                for run in mode_runs
                if float(run.metrics.get("primary_accuracy", 0)) == 1.0
            )
            summary[mode] = {
                "total_runs": sum(1 for run in runs if run.mode == mode),
                "valid_runs": len(mode_runs),
                "failed_or_invalid_runs": sum(
                    1 for run in runs if run.mode == mode and run.status in {"failed", "invalid"}
                ),
                "primary_accuracy": _mean(mode_runs, "primary_accuracy"),
                "top2_hit": _mean(mode_runs, "top2_hit"),
                "evidence_coverage": _mean(mode_runs, "evidence_coverage"),
                "evidence_validity": _mean(mode_runs, "evidence_validity"),
                "secondary_cause_hit": _mean(mode_runs, "secondary_cause_hit"),
                "conflict_resolution_rate": _mean(mode_runs, "conflict_resolution_rate"),
                "effective_rework_rate": _mean(mode_runs, "effective_rework_rate"),
                "budget_success_rate": _mean(mode_runs, "budget_success_rate"),
                "success_rate": _mean(mode_runs, "success_rate"),
                "average_model_steps": _mean(mode_runs, "total_model_steps"),
                "average_critical_path_steps": _mean(mode_runs, "critical_path_steps"),
                "average_tool_calls": _mean(mode_runs, "tool_call_count"),
                "average_duplicate_tool_calls": _mean(mode_runs, "duplicate_tool_calls"),
                "average_rework_count": _mean(mode_runs, "rework_count"),
                "average_parallel_speedup": _mean(mode_runs, "parallel_speedup"),
                "p50_end_to_end_ms": int(median(timings)) if timings else None,
                "p50_valid_answer_ms": (
                    int(median(valid_answer_timings)) if valid_answer_timings else None
                ),
                "p95_end_to_end_ms": (
                    _percentile_nearest_rank(timings, 0.95)
                    if len(timings) >= 20 else None
                ),
            }
        full = summary["agentteams_full"]
        serial = summary["agentteams_serial"]
        uncoordinated = summary["agentteams_uncoordinated"]
        full_valid_p50 = full["p50_valid_answer_ms"]
        serial_valid_p50 = serial["p50_valid_answer_ms"]
        summary["attribution"] = {
            "parallel_time_reduction": (
                round(1 - full_valid_p50 / serial_valid_p50, 6)
                if isinstance(full_valid_p50, int)
                and isinstance(serial_valid_p50, int)
                and serial_valid_p50 > 0
                else None
            ),
            "coordination_accuracy_gain": _difference(
                full["primary_accuracy"], uncoordinated["primary_accuracy"],
            ),
            "coordination_evidence_gain": _difference(
                full["evidence_coverage"], uncoordinated["evidence_coverage"],
            ),
            "full_team_parallel_speedup": full["average_parallel_speedup"],
        }
        summary["provenance"] = {
            "benchmark_id": _BENCHMARK_ID,
            "label": "基于 MultiAgentBench 里程碑思想的 AgentTeam 协作机制消融评测",
        }
        return summary


def _mean(runs: list[models.BenchmarkRun], field: str) -> float | None:
    values = [float(run.metrics[field]) for run in runs if field in run.metrics]
    return round(sum(values) / len(values), 6) if values else None


def _difference(left: Any, right: Any) -> float | None:
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    return round(float(left) - float(right), 6)


def _percentile_nearest_rank(values: list[int], quantile: float) -> int | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, int((len(values) * quantile + 0.999999)) - 1))
    return values[index]
