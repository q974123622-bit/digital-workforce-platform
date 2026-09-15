from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeRunEngine:
    def __init__(self, fail_run_id=None, mismatch_run_id=None, fail_exception=None):
        self.fail_run_id = fail_run_id
        self.mismatch_run_id = mismatch_run_id
        self.fail_exception = fail_exception
        self.calls = []

    def _result(self, request, mode):
        from app.services.agentteams_database_benchmark_runner import BenchmarkRunResult
        from app.services.agentteams_database_evaluation import DatabaseBenchmarkMetrics, DiagnosisResult

        self.calls.append((request.run_id, request.scenario_id, mode))
        if request.run_id == self.fail_run_id:
            raise self.fail_exception or RuntimeError("one run failed")
        fingerprint = "wrong" if request.run_id == self.mismatch_run_id else request.fairness_fingerprint
        return BenchmarkRunResult(
            run_id=request.run_id,
            scenario_id=request.scenario_id,
            mode=mode,
            status="completed",
            fairness_fingerprint=fingerprint,
            diagnosis=DiagnosisResult(
                root_cause_id="lock_wait_chain",
                top_candidates=["lock_wait_chain"],
                evidence_ids=[f"db:{request.scenario_id}:locks:abc"],
                conclusion="诊断完成。",
                confidence=0.9,
            ),
            metrics=DatabaseBenchmarkMetrics(
                primary_accuracy=1.0,
                top2_hit=1.0,
                evidence_coverage=1.0,
                evidence_validity=1.0,
                secondary_cause_hit=0.8,
                conflict_resolution_rate=0.75,
                effective_rework_rate=0.5,
                budget_success_rate=0.7,
                end_to_end_ms=100 if mode == "single_agent" else 70,
                total_model_steps=3 if mode == "single_agent" else 6,
                tool_call_count=2 if mode == "single_agent" else 5,
                duplicate_tool_calls=0,
                critical_path_steps=3 if mode == "single_agent" else 2,
                parallel_speedup=None if mode == "single_agent" else 1.4,
                manager_rounds=0 if mode == "single_agent" else 1,
                rework_count=0,
                required_child_count=0 if mode == "single_agent" else 5,
                received_child_result_count=0 if mode == "single_agent" else 5,
                lost_child_results=0,
                success_rate=1.0,
                hard_gate_passed=True,
                hard_gate_failures=[],
            ),
        )

    def run_agentteams(self, request, *, mode="agentteams_full"):
        return self._result(request, mode)


def test_create_experiment_expands_scenario_repeat_mode_matrix_in_stable_order(db_session):
    from app import models
    from app.services.agentteams_database_benchmark import DatabaseBenchmarkService

    service = DatabaseBenchmarkService(project_root=PROJECT_ROOT)
    experiment = service.create_experiment(
        db_session,
        owner_human_no="E10281",
        scenario_ids=["lock_contention", "large_query"],
        repeats=2,
        fairness_config={"max_model_steps": 20, "max_tool_calls": 10, "timeout_ms": 30_000},
    )

    runs = db_session.query(models.BenchmarkRun).filter_by(experiment_id=experiment.id).order_by(models.BenchmarkRun.ordinal).all()
    assert experiment.status == "pending"
    assert len(experiment.fairness_fingerprint) == 64
    assert experiment.fairness_config["model"]
    assert experiment.fairness_config["model_settings"] == {
        "thinking": False,
        "reasoning_effort": "low",
        "structured_output": "json_object",
        "request_timeout_seconds": 90.0,
        "prompt_contract": "database-diagnosis-taxonomy-v1",
    }
    assert [(run.scenario_id, run.repeat_index, run.mode) for run in runs] == [
        ("lock_contention", 1, "agentteams_full"),
        ("lock_contention", 1, "agentteams_serial"),
        ("lock_contention", 1, "agentteams_uncoordinated"),
        ("lock_contention", 2, "agentteams_full"),
        ("lock_contention", 2, "agentteams_serial"),
        ("lock_contention", 2, "agentteams_uncoordinated"),
        ("large_query", 1, "agentteams_full"),
        ("large_query", 1, "agentteams_serial"),
        ("large_query", 1, "agentteams_uncoordinated"),
        ("large_query", 2, "agentteams_full"),
        ("large_query", 2, "agentteams_serial"),
        ("large_query", 2, "agentteams_uncoordinated"),
    ]


def test_create_experiment_additively_syncs_database_benchmark_runtime_resources(db_session):
    from app import models
    from app.services.agentteams_database_benchmark import DatabaseBenchmarkService

    db_session.query(models.TeamMember).filter_by(team_id="TEAM-DB-BENCH").delete()
    db_session.query(models.Team).filter_by(id="TEAM-DB-BENCH").delete()
    db_session.query(models.EmployeePluginGrant).filter_by(
        plugin_id="agentteams-database-inspector",
    ).delete()
    db_session.query(models.Plugin).filter_by(id="agentteams-database-inspector").delete()
    db_session.commit()

    DatabaseBenchmarkService(project_root=PROJECT_ROOT).create_experiment(
        db_session,
        owner_human_no="E10281",
        scenario_ids=["lock_contention"],
        repeats=1,
        fairness_config={"max_model_steps": 20, "max_tool_calls": 10, "timeout_ms": 30_000},
    )

    assert db_session.get(models.Team, "TEAM-DB-BENCH") is not None
    assert db_session.get(models.Plugin, "agentteams-database-inspector") is not None
    members = db_session.query(models.TeamMember).filter_by(team_id="TEAM-DB-BENCH").all()
    assert len(members) == 8
    assert {
        member.employee_id: (member.role, member.reports_to_employee_id)
        for member in members
    } == {
        "VE-0001": ("manager", None),
        "DB-OPS-LEAD": ("leader", "VE-0001"),
        "DB-PERF-LEAD": ("leader", "VE-0001"),
        "DB-WRITE-01": ("worker", "DB-OPS-LEAD"),
        "DB-LOCK-01": ("worker", "DB-OPS-LEAD"),
        "DB-VACUUM-01": ("worker", "DB-OPS-LEAD"),
        "DB-INDEX-01": ("worker", "DB-PERF-LEAD"),
        "DB-QUERY-01": ("worker", "DB-PERF-LEAD"),
    }
    assert db_session.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.employee_id.in_({
            "VE-0001", "DB-WRITE-01", "DB-LOCK-01",
            "DB-VACUUM-01", "DB-INDEX-01", "DB-QUERY-01",
        }),
        models.EmployeePluginGrant.plugin_id == "agentteams-database-inspector",
        models.EmployeePluginGrant.decision_mode == "allow",
    ).count() == 6


def test_new_complex_experiment_expands_only_three_scenarios_to_forty_five_runs(db_session):
    from app import models
    from app.services.agentteams_database_benchmark import DatabaseBenchmarkService

    scenario_ids = [
        "compound_lock_index",
        "conflicting_signals_followup",
        "budgeted_incident_triage",
    ]
    experiment = DatabaseBenchmarkService(project_root=PROJECT_ROOT).create_experiment(
        db_session,
        owner_human_no="E10281",
        scenario_ids=scenario_ids,
        repeats=5,
        fairness_config={"max_model_steps": 20, "max_tool_calls": 10, "timeout_ms": 30_000},
    )
    runs = db_session.query(models.BenchmarkRun).filter_by(experiment_id=experiment.id).all()

    assert len(runs) == 45
    assert {run.scenario_id for run in runs} == set(scenario_ids)
    assert not {"bulk_writes", "lock_contention", "vacuum_pressure", "index_anomaly", "large_query"}.intersection(
        run.scenario_id for run in runs
    )


def test_execution_contains_one_run_failure_and_aggregates_remaining_valid_runs(db_session):
    from app import models
    from app.services.agentteams_database_benchmark import DatabaseBenchmarkService

    service = DatabaseBenchmarkService(project_root=PROJECT_ROOT)
    experiment = service.create_experiment(
        db_session,
        owner_human_no="E10281",
        scenario_ids=["lock_contention"],
        repeats=2,
        fairness_config={"max_model_steps": 20, "max_tool_calls": 10, "timeout_ms": 30_000},
    )
    runs = db_session.query(models.BenchmarkRun).filter_by(experiment_id=experiment.id).order_by(models.BenchmarkRun.ordinal).all()
    engine = FakeRunEngine(fail_run_id=runs[0].id)

    service.execute_experiment(db_session, experiment.id, engine=engine)

    db_session.refresh(experiment)
    rows = db_session.query(models.BenchmarkRun).filter_by(experiment_id=experiment.id).order_by(models.BenchmarkRun.ordinal).all()
    assert experiment.status == "completed"
    assert rows[0].status == "failed"
    assert rows[0].failure_code == "run_execution_failed"
    assert all(row.status == "completed" for row in rows[1:])
    assert experiment.summary["agentteams_full"]["valid_runs"] == 1
    assert experiment.summary["agentteams_full"]["primary_accuracy"] == 1.0
    assert experiment.summary["agentteams_full"]["p50_valid_answer_ms"] == 70
    assert experiment.summary["agentteams_full"]["average_parallel_speedup"] == 1.4
    assert experiment.summary["agentteams_full"]["secondary_cause_hit"] == 0.8
    assert experiment.summary["agentteams_full"]["conflict_resolution_rate"] == 0.75
    assert experiment.summary["agentteams_full"]["effective_rework_rate"] == 0.5
    assert experiment.summary["agentteams_full"]["budget_success_rate"] == 0.7
    assert experiment.summary["agentteams_full"]["p95_end_to_end_ms"] is None
    assert experiment.summary["attribution"] == {
        "parallel_time_reduction": 0.0,
        "coordination_accuracy_gain": 0.0,
        "coordination_evidence_gain": 0.0,
        "full_team_parallel_speedup": 1.4,
    }


def test_execution_persists_safe_llm_failure_code_without_exception_text(db_session):
    from app import models
    from app.services.agentteams_database_benchmark import DatabaseBenchmarkService
    from app.services.llm import LLMUnavailableError

    service = DatabaseBenchmarkService(project_root=PROJECT_ROOT)
    experiment = service.create_experiment(
        db_session,
        owner_human_no="E10281",
        scenario_ids=["lock_contention"],
        repeats=1,
        fairness_config={"max_model_steps": 20, "max_tool_calls": 10, "timeout_ms": 30_000},
    )
    rows = db_session.query(models.BenchmarkRun).filter_by(
        experiment_id=experiment.id,
    ).order_by(models.BenchmarkRun.ordinal).all()
    secret_text = "provider detail must not persist"
    engine = FakeRunEngine(
        fail_run_id=rows[1].id,
        fail_exception=LLMUnavailableError(secret_text, code="network_error"),
    )

    service.execute_experiment(db_session, experiment.id, engine=engine)

    assert rows[1].status == "failed"
    assert rows[1].failure_code == "llm_network_error"
    assert secret_text not in rows[1].failure_code
    assert secret_text not in str(rows[1].metrics)
    assert secret_text not in str(rows[1].diagnosis)


def test_execution_resumes_pending_and_running_rows_but_not_terminal_rows(db_session):
    from app import models
    from app.services.agentteams_database_benchmark import DatabaseBenchmarkService

    service = DatabaseBenchmarkService(project_root=PROJECT_ROOT)
    experiment = service.create_experiment(
        db_session,
        owner_human_no="E10281",
        scenario_ids=["lock_contention"],
        repeats=2,
        fairness_config={"max_model_steps": 20, "max_tool_calls": 10, "timeout_ms": 30_000},
    )
    rows = db_session.query(models.BenchmarkRun).filter_by(experiment_id=experiment.id).order_by(models.BenchmarkRun.ordinal).all()
    rows[0].status = "completed"
    rows[0].metrics = {"primary_accuracy": 1.0, "end_to_end_ms": 100}
    rows[1].status = "running"
    db_session.commit()
    engine = FakeRunEngine()

    service.execute_experiment(db_session, experiment.id, engine=engine)

    assert rows[0].id not in {run_id for run_id, _, _ in engine.calls}
    assert {row.id for row in rows[1:]} == {run_id for run_id, _, _ in engine.calls}


def test_fairness_fingerprint_mismatch_marks_only_that_run_invalid(db_session):
    from app import models
    from app.services.agentteams_database_benchmark import DatabaseBenchmarkService

    service = DatabaseBenchmarkService(project_root=PROJECT_ROOT)
    experiment = service.create_experiment(
        db_session,
        owner_human_no="E10281",
        scenario_ids=["lock_contention"],
        repeats=1,
        fairness_config={"max_model_steps": 20, "max_tool_calls": 10, "timeout_ms": 30_000},
    )
    rows = db_session.query(models.BenchmarkRun).filter_by(experiment_id=experiment.id).order_by(models.BenchmarkRun.ordinal).all()

    service.execute_experiment(db_session, experiment.id, engine=FakeRunEngine(mismatch_run_id=rows[1].id))

    assert rows[0].status == "completed"
    assert rows[1].status == "invalid"
    assert rows[1].failure_code == "fairness_fingerprint_mismatch"
