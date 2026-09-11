"""Run allowlisted database scenarios created from the generic collaboration UI.

Only the checked-in scenario identified by ``scenario_id`` reaches the model
backend.  User-authored titles, instructions, and material remain persisted for
display and are deliberately never forwarded to the external provider.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import func, select

from .. import models
from ..database import SessionLocal
from .agentteams_database_benchmark_backend import build_deepseek_database_backend
from .agentteams_database_benchmark_runner import BenchmarkBudget, BenchmarkRunRequest, DatabaseBenchmarkRunEngine
from .agentteams_collaboration import CollaborationConflict
from .llm import LLMUnavailableError


DATABASE_TEAM_ID = "TEAM-DB-BENCH"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="database-scenario")


def enqueue_database_collaboration_task(task_id: str, scenario_id: str) -> None:
    _EXECUTOR.submit(_execute_allowlisted_scenario, task_id, scenario_id)


def _execute_allowlisted_scenario(task_id: str, scenario_id: str) -> None:
    db = SessionLocal()
    try:
        run = db.get(models.TaskRun, task_id)
        root = db.get(models.AgentTaskNode, task_id)
        if run is None or root is None or run.team_id != DATABASE_TEAM_ID or root.status == "cancelled":
            return
        engine = DatabaseBenchmarkRunEngine(
            db,
            backend=build_deepseek_database_backend(project_root=PROJECT_ROOT),
            project_root=PROJECT_ROOT,
        )
        result = engine.run_agentteams(
            BenchmarkRunRequest(
                run_id=task_id,
                scenario_id=scenario_id,
                budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=300_000),
                fairness_fingerprint="generic-database-scenario-v1",
                owner_human_no=run.owner_human_no,
            ),
            existing_task_run_id=task_id,
        )
        if result.status not in {"completed", "invalid"}:
            failures = result.metrics.hard_gate_failures
            _mark_failed(db, task_id, failures[0] if failures else "database_scenario_failed")
        db.commit()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, LLMUnavailableError):
            code = f"llm_{exc.code}"
        elif isinstance(exc, CollaborationConflict):
            code = str(exc) or "database_scenario_protocol_conflict"
        elif isinstance(exc, RuntimeError) and str(exc) in {
            "database_benchmark_runtime_unbound",
            "database_collaboration_task_mismatch",
            "database_inspection_not_audited",
        }:
            code = str(exc)
        else:
            code = "database_scenario_runtime_unavailable"
        _mark_failed(db, task_id, code)
        db.commit()
    finally:
        db.close()


def _mark_failed(db, task_id: str, code: str) -> None:
    run = db.get(models.TaskRun, task_id)
    root = db.get(models.AgentTaskNode, task_id)
    if run is None or root is None or root.status == "cancelled":
        return
    run.status = "failed"
    run.summary = "数据库协作诊断执行失败"
    root.status = "failed"
    root.error_code = code
    root.error_message = "数据库协作诊断执行失败"
    next_seq = db.scalar(select(func.max(models.AgentTaskEvent.event_seq)).where(models.AgentTaskEvent.root_task_id == task_id)) or 0
    db.add(models.AgentTaskEvent(
        root_task_id=task_id,
        task_id=task_id,
        event_seq=next_seq + 1,
        event_type="task.failed",
        actor_agent_id=root.assignee_agent_id,
        target_agent_id=None,
        summary="database scenario failed",
        detail={"error_code": code},
    ))
