"""Owner-scoped API for the local MultiAgentBench-inspired database benchmark."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import SessionLocal, get_db
from ..services.agentteams_database_benchmark import (
    ActiveBenchmarkExperimentError,
    DatabaseBenchmarkService,
)


router = APIRouter(prefix="/agentteams-benchmarks/database", tags=["agentteams-benchmarks"])
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="database-benchmark")


def _service() -> DatabaseBenchmarkService:
    return DatabaseBenchmarkService(project_root=_PROJECT_ROOT)


def _require_actor(db: Session, actor_no: str) -> models.HumanEmployee:
    actor = db.get(models.HumanEmployee, actor_no)
    if actor is None:
        raise HTTPException(status_code=404, detail="员工不存在")
    if actor.status != "active":
        raise HTTPException(status_code=403, detail="员工当前未启用")
    return actor


def _require_experiment(
    db: Session,
    experiment_id: str,
    actor_no: str,
) -> models.BenchmarkExperiment:
    actor = _require_actor(db, actor_no)
    experiment = db.get(models.BenchmarkExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="数据库评测实验不存在")
    if experiment.owner_human_no != actor.employee_no:
        raise HTTPException(status_code=403, detail="无权访问该数据库评测实验")
    return experiment


def _experiment_out(db: Session, experiment: models.BenchmarkExperiment) -> schemas.DatabaseBenchmarkExperimentOut:
    runs = list(db.scalars(
        select(models.BenchmarkRun).where(models.BenchmarkRun.experiment_id == experiment.id)
    ))
    return schemas.DatabaseBenchmarkExperimentOut(
        id=experiment.id,
        benchmark_id=experiment.benchmark_id,
        status=experiment.status,
        scenario_ids=list(experiment.scenario_ids),
        repeats=experiment.repeats,
        fairness_config=dict(experiment.fairness_config),
        fairness_fingerprint=experiment.fairness_fingerprint,
        summary=dict(experiment.summary),
        total_runs=len(runs),
        completed_runs=sum(run.status == "completed" for run in runs),
        failed_runs=sum(run.status == "failed" for run in runs),
        invalid_runs=sum(run.status == "invalid" for run in runs),
        created_at=experiment.created_at,
        started_at=experiment.started_at,
        finished_at=experiment.finished_at,
    )


def _execute_background(experiment_id: str) -> None:
    db = SessionLocal()
    try:
        from ..services.agentteams_database_benchmark_backend import build_deepseek_database_backend
        from ..services.agentteams_database_benchmark_runner import DatabaseBenchmarkRunEngine

        backend = build_deepseek_database_backend(project_root=_PROJECT_ROOT)
        engine = DatabaseBenchmarkRunEngine(db, backend=backend, project_root=_PROJECT_ROOT)
        _service().execute_experiment(db, experiment_id, engine=engine)
    except Exception:
        db.rollback()
        experiment = db.get(models.BenchmarkExperiment, experiment_id)
        if experiment is not None and experiment.status != "completed":
            experiment.status = "failed"
            experiment.summary = {
                "failure_code": "benchmark_runtime_unavailable",
                "provenance": {
                    "benchmark_id": "multiagentbench-database-local-v1",
                    "label": "基于 MultiAgentBench Database 场景改编的本地对照评测",
                },
            }
            db.commit()
    finally:
        db.close()


def enqueue_experiment(experiment_id: str) -> None:
    _EXECUTOR.submit(_execute_background, experiment_id)


@router.post(
    "/experiments",
    response_model=schemas.DatabaseBenchmarkExperimentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_experiment(
    payload: schemas.DatabaseBenchmarkExperimentCreateIn,
    actor_no: str = Query(...),
    db: Session = Depends(get_db),
):
    actor = _require_actor(db, actor_no)
    try:
        experiment = _service().create_experiment(
            db,
            owner_human_no=actor.employee_no,
            scenario_ids=payload.scenario_ids,
            repeats=payload.repeats,
            fairness_config={
                "max_model_steps": payload.max_model_steps,
                "max_tool_calls": payload.max_tool_calls,
                "timeout_ms": payload.timeout_ms,
            },
        )
    except ActiveBenchmarkExperimentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "benchmark_experiment_already_running",
                "experiment_id": exc.experiment_id,
            },
        ) from None
    except ValueError:
        raise HTTPException(status_code=422, detail="数据库评测参数不合法") from None
    response = _experiment_out(db, experiment)
    enqueue_experiment(experiment.id)
    return response


@router.get(
    "/experiments/latest",
    response_model=schemas.DatabaseBenchmarkExperimentOut | None,
)
def get_latest_experiment(
    actor_no: str = Query(...),
    db: Session = Depends(get_db),
):
    actor = _require_actor(db, actor_no)
    experiment = db.scalar(
        select(models.BenchmarkExperiment)
        .where(models.BenchmarkExperiment.owner_human_no == actor.employee_no)
        .order_by(
            models.BenchmarkExperiment.created_at.desc(),
            models.BenchmarkExperiment.id.desc(),
        )
        .limit(1)
    )
    return _experiment_out(db, experiment) if experiment is not None else None


@router.get("/experiments/{experiment_id}", response_model=schemas.DatabaseBenchmarkExperimentOut)
def get_experiment(
    experiment_id: str,
    actor_no: str = Query(...),
    db: Session = Depends(get_db),
):
    return _experiment_out(db, _require_experiment(db, experiment_id, actor_no))


@router.get("/experiments/{experiment_id}/runs", response_model=list[schemas.DatabaseBenchmarkRunOut])
def list_runs(
    experiment_id: str,
    actor_no: str = Query(...),
    db: Session = Depends(get_db),
):
    experiment = _require_experiment(db, experiment_id, actor_no)
    return list(db.scalars(
        select(models.BenchmarkRun)
        .where(models.BenchmarkRun.experiment_id == experiment.id)
        .order_by(models.BenchmarkRun.ordinal)
    ))
