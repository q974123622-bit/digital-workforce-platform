from pathlib import Path
from threading import Barrier

from app import models


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ScriptedBackend:
    model_name = "fake-same-model"
    model_settings = {"thinking": True, "temperature": 0.1}

    def __init__(self):
        self.single_calls = 0
        self.expert_roles = []
        self.manager_plan_calls = 0
        self.manager_plan_constraints = []
        self.manager_calls = 0

    def manager_plan(self, *, scenario, initial_assignment_count, reserved_followup_calls):
        self.manager_plan_calls += 1
        self.manager_plan_constraints.append({
            "initial_assignment_count": initial_assignment_count,
            "reserved_followup_calls": reserved_followup_calls,
        })
        return {
            "assignments": [
                {
                    "role": "db_write",
                    "assignee_agent_id": "DB-WRITE-01",
                    "signal": "statements",
                    "instruction": "检查写入相关语句。",
                },
                {
                    "role": "db_lock",
                    "assignee_agent_id": "DB-LOCK-01",
                    "signal": "locks",
                    "instruction": "检查锁等待链。",
                },
                {
                    "role": "db_vacuum",
                    "assignee_agent_id": "DB-VACUUM-01",
                    "signal": "vacuum",
                    "instruction": "检查 Vacuum 状态。",
                },
                {
                    "role": "db_index",
                    "assignee_agent_id": "DB-INDEX-01",
                    "signal": "indexes",
                    "instruction": "检查索引状态。",
                },
                {
                    "role": "db_query",
                    "assignee_agent_id": "DB-QUERY-01",
                    "signal": "table_stats",
                    "instruction": "检查表级统计。",
                },
            ],
            "rationale": "并行覆盖五类互补证据。",
        }

    def single_decide(self, *, scenario, observations, allowed_signals):
        self.single_calls += 1
        if len(observations) == 0:
            return {"action": "tool", "signal": "locks", "filters": {}, "limit": 20}
        if len(observations) == 1:
            return {"action": "tool", "signal": "statements", "filters": {}, "limit": 20}
        return {
            "action": "complete",
            "result": {
                "root_cause_id": "lock_wait_chain",
                "top_candidates": ["lock_wait_chain", "resource_heavy_analytics_query"],
                "evidence_ids": [item["evidence_id"] for item in observations],
                "conclusion": "长事务形成锁等待链。",
                "confidence": 0.94,
            },
        }

    def expert_report(self, *, role, scenario, observation):
        self.expert_roles.append(role)
        return {
            "role": role,
            "finding": f"{role} 已检查 {observation['signal']}",
            "evidence_ids": [observation["evidence_id"]],
        }

    def manager_diagnose(self, *, scenario, reports):
        self.manager_calls += 1
        return {
            "root_cause_id": "lock_wait_chain",
            "top_candidates": ["lock_wait_chain", "resource_heavy_analytics_query"],
            "evidence_ids": [evidence for report in reports for evidence in report["evidence_ids"]],
            "conclusion": "五类信号交叉验证后，根因是锁等待链。",
            "confidence": 0.97,
        }


def test_single_and_agentteams_use_same_fairness_config_and_gateway_audits(db_session):
    from app import models
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    backend = ScriptedBackend()
    engine = DatabaseBenchmarkRunEngine(db_session, backend=backend, project_root=PROJECT_ROOT)
    budget = BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000)
    request = BenchmarkRunRequest(
        run_id="RUN-LOCK-1",
        owner_human_no="E10281",
        scenario_id="lock_contention",
        budget=budget,
        fairness_fingerprint="same-fingerprint",
    )

    single = engine.run_single(request)
    team = engine.run_agentteams(request.model_copy(update={"run_id": "RUN-LOCK-2"}))

    assert single.fairness_fingerprint == team.fairness_fingerprint == "same-fingerprint"
    assert single.metrics.primary_accuracy == 1.0
    assert single.metrics.tool_call_count == 2
    assert single.metrics.total_model_steps == 3
    assert team.metrics.primary_accuracy == 1.0
    assert team.metrics.tool_call_count == 5
    assert team.metrics.total_model_steps == 7
    assert team.metrics.manager_rounds == 2
    assert team.metrics.required_child_count == 5
    assert team.metrics.received_child_result_count == 5
    assert set(backend.expert_roles) == {"db_write", "db_lock", "db_vacuum", "db_index", "db_query"}
    audits = db_session.query(models.AuditEvent).filter(
        models.AuditEvent.trace_id.in_(["RUN-LOCK-1", "RUN-LOCK-2"])
    ).all()
    assert len(audits) == 7
    assert all(audit.plugin_id == "agentteams-database-inspector" for audit in audits)
    assert all(audit.decision == "allow" for audit in audits)


class AblationBackend(ScriptedBackend):
    def __init__(self):
        super().__init__()
        self.uncoordinated_calls = 0

    def uncoordinated_diagnose(self, *, scenario, reports):
        self.uncoordinated_calls += 1
        return super().manager_diagnose(scenario=scenario, reports=reports)


class ComplexCoordinationBackend(ScriptedBackend):
    def __init__(self):
        super().__init__()
        self.review_calls = 0

    def manager_plan(self, *, scenario, initial_assignment_count, reserved_followup_calls):
        self.manager_plan_calls += 1
        self.manager_plan_constraints.append({
            "initial_assignment_count": initial_assignment_count,
            "reserved_followup_calls": reserved_followup_calls,
        })
        if scenario.id == "budgeted_incident_triage":
            assignments = [
                ("db_index", "DB-INDEX-01", "indexes"),
                ("db_vacuum", "DB-VACUUM-01", "vacuum"),
            ]
        else:
            assignments = [
                ("db_write", "DB-WRITE-01", "statements"),
                ("db_lock", "DB-LOCK-01", "locks"),
                ("db_vacuum", "DB-VACUUM-01", "vacuum" if scenario.id == "compound_lock_index" else "table_stats"),
            ]
        return {
            "assignments": [
                {"role": role, "assignee_agent_id": agent, "signal": signal, "instruction": f"检查 {signal}。"}
                for role, agent, signal in assignments
            ],
            "rationale": "在预算内先调查三个区分度最高的方向。",
        }

    def manager_review(self, *, scenario, reports, observed_signals, remaining_tool_calls):
        self.review_calls += 1
        if not scenario.expected.requires_followup_signals:
            return {"action": "complete", "rationale": "现有证据已经满足预算内诊断。"}
        signal = scenario.expected.requires_followup_signals[0]
        role = "db_index" if signal == "indexes" else "db_query" if signal == "table_stats" else "db_lock"
        agent = "DB-INDEX-01" if role == "db_index" else "DB-QUERY-01" if role == "db_query" else "DB-LOCK-01"
        return {
            "action": "follow_up",
            "assignment": {
                "role": role,
                "assignee_agent_id": agent,
                "signal": signal,
                "instruction": f"复核 {signal} 以解决冲突。",
            },
            "rationale": "首轮报告存在需要定向复核的冲突。",
        }

    def manager_diagnose(self, *, scenario, reports):
        self.manager_calls += 1
        secondary = list(scenario.expected.secondary_causes)
        return {
            "root_cause_id": scenario.expected.root_cause_id,
            "top_candidates": [scenario.expected.root_cause_id, *secondary],
            "evidence_ids": list(dict.fromkeys(
                evidence for report in reports for evidence in report["evidence_ids"]
            )),
            "conclusion": "综合首轮和复核证据完成诊断。",
            "confidence": 0.95,
        }


def test_complex_full_team_performs_one_effective_followup_within_scenario_budget(db_session):
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    backend = ComplexCoordinationBackend()
    result = DatabaseBenchmarkRunEngine(
        db_session, backend=backend, project_root=PROJECT_ROOT,
    ).run_agentteams(BenchmarkRunRequest(
        run_id="RUN-COMPLEX-FOLLOWUP",
        owner_human_no="E10281",
        scenario_id="compound_lock_index",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "completed"
    assert backend.review_calls == 1
    assert result.metrics.tool_call_count == 4
    assert result.metrics.rework_count == 1
    assert result.metrics.manager_rounds == 3
    assert result.metrics.conflict_resolution_rate == 1.0
    assert result.metrics.secondary_cause_hit == 1.0
    assert result.metrics.budget_success_rate == 1.0


def test_complex_uncoordinated_team_cannot_replace_missing_coordination_with_extra_calls(db_session):
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    backend = ComplexCoordinationBackend()
    result = DatabaseBenchmarkRunEngine(
        db_session, backend=backend, project_root=PROJECT_ROOT,
    ).run_agentteams(
        BenchmarkRunRequest(
                run_id="RUN-COMPLEX-NO-COORDINATION",
                owner_human_no="E10281",
            scenario_id="compound_lock_index",
            budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
            fairness_fingerprint="same",
        ),
        mode="agentteams_uncoordinated",
    )

    assert result.status == "completed"
    assert backend.review_calls == 0
    assert result.metrics.tool_call_count <= 4
    assert result.metrics.conflict_resolution_rate == 0.0
    assert result.metrics.success_rate == 0.0
    assert result.metrics.hard_gate_failures == []


class UnsafeFollowupFiltersBackend(ComplexCoordinationBackend):
    def manager_review(self, *, scenario, reports, observed_signals, remaining_tool_calls):
        review = super().manager_review(
            scenario=scenario,
            reports=reports,
            observed_signals=observed_signals,
            remaining_tool_calls=remaining_tool_calls,
        )
        review["assignment"]["filters"] = {
            "signal": review["assignment"]["signal"],
            "table": "customers,orders",
        }
        return review


def test_followup_rejects_model_generated_filters_before_creating_a_followup_task(db_session):
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    result = DatabaseBenchmarkRunEngine(
        db_session, backend=UnsafeFollowupFiltersBackend(), project_root=PROJECT_ROOT,
    ).run_agentteams(BenchmarkRunRequest(
        run_id="RUN-COMPLEX-SAFE-FOLLOWUP",
        owner_human_no="E10281",
        scenario_id="compound_lock_index",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "invalid"
    assert result.metrics.hard_gate_failures == ["invalid_manager_review"]
    children = db_session.query(models.AgentTaskNode).filter_by(
        root_task_id=result.task_run_id, parent_task_id=result.task_run_id,
    ).all()
    assert all(not child.client_task_key.endswith("_followup") for child in children)


class InvalidLastAssignmentBackend(ComplexCoordinationBackend):
    def manager_plan(self, *, scenario, initial_assignment_count, reserved_followup_calls):
        plan = super().manager_plan(
            scenario=scenario,
            initial_assignment_count=initial_assignment_count,
            reserved_followup_calls=reserved_followup_calls,
        )
        plan["assignments"][-1]["filters"] = {"focus": "slow_customer_search"}
        return plan


def test_entire_manager_plan_is_validated_before_any_child_is_created_or_delegated(db_session):
    """Incremental assignment validation that leaves partial children must break this test."""
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    result = DatabaseBenchmarkRunEngine(
        db_session, backend=InvalidLastAssignmentBackend(), project_root=PROJECT_ROOT,
    ).run_agentteams(BenchmarkRunRequest(
        run_id="RUN-INVALID-LAST-ASSIGNMENT",
        owner_human_no="E10281",
        scenario_id="compound_lock_index",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "invalid"
    assert result.metrics.hard_gate_failures == ["invalid_manager_plan"]
    assert db_session.query(models.AgentTaskNode).filter_by(
        root_task_id=result.task_run_id, parent_task_id=result.task_run_id,
    ).count() == 0
    assert db_session.query(models.AgentMessage).filter_by(
        root_task_id=result.task_run_id, message_type="task.delegate",
    ).count() == 0
    failed = db_session.query(models.AgentMessage).filter_by(
        root_task_id=result.task_run_id,
        task_id=result.task_run_id,
        message_type="task.failed",
    ).one()
    assert failed.payload["payload"]["validation"] == {
        "stage": "manager_plan",
        "field": "assignments.2.filters.focus",
        "reason": "extra_forbidden",
    }


class TransactionProbeBackend(ComplexCoordinationBackend):
    def __init__(self, db_session):
        super().__init__()
        self.db_session = db_session
        self.transaction_states = []

    def _record(self, stage):
        self.transaction_states.append((stage, self.db_session.in_transaction()))

    def manager_plan(self, *, scenario, initial_assignment_count, reserved_followup_calls):
        self._record("manager_plan")
        return super().manager_plan(
            scenario=scenario,
            initial_assignment_count=initial_assignment_count,
            reserved_followup_calls=reserved_followup_calls,
        )

    def expert_report(self, *, role, scenario, observation):
        self._record("expert_report")
        return super().expert_report(role=role, scenario=scenario, observation=observation)

    def manager_review(self, *, scenario, reports, observed_signals, remaining_tool_calls):
        self._record("manager_review")
        return super().manager_review(
            scenario=scenario,
            reports=reports,
            observed_signals=observed_signals,
            remaining_tool_calls=remaining_tool_calls,
        )

    def manager_diagnose(self, *, scenario, reports):
        self._record("manager_diagnose")
        return super().manager_diagnose(scenario=scenario, reports=reports)


def test_agentteam_releases_sqlite_write_transactions_before_model_calls(db_session):
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    backend = TransactionProbeBackend(db_session)
    result = DatabaseBenchmarkRunEngine(
        db_session, backend=backend, project_root=PROJECT_ROOT,
    ).run_agentteams(BenchmarkRunRequest(
        run_id="RUN-COMPLEX-TRANSACTION-CHECKPOINTS",
        owner_human_no="E10281",
        scenario_id="compound_lock_index",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "completed"
    assert backend.transaction_states
    assert all(active is False for _, active in backend.transaction_states), backend.transaction_states


def test_complex_scenario_budget_overrides_a_larger_experiment_budget(db_session):
    from app.services.agentteams_database_benchmark_runner import BenchmarkBudget, BenchmarkRunRequest, DatabaseBenchmarkRunEngine

    backend = ComplexCoordinationBackend()
    result = DatabaseBenchmarkRunEngine(
        db_session, backend=backend, project_root=PROJECT_ROOT,
    ).run_agentteams(BenchmarkRunRequest(
        run_id="RUN-COMPLEX-BUDGET",
        owner_human_no="E10281",
        scenario_id="budgeted_incident_triage",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "completed"
    assert result.metrics.tool_call_count == 3
    assert result.metrics.rework_count == 1
    assert result.metrics.budget_success_rate == 1.0
    assert backend.manager_plan_constraints == [{
        "initial_assignment_count": 2,
        "reserved_followup_calls": 1,
    }]


def test_agentteam_ablation_modes_change_only_coordination_and_scheduling(db_session):
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    budget = BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000)
    serial_backend = AblationBackend()
    serial = DatabaseBenchmarkRunEngine(
        db_session, backend=serial_backend, project_root=PROJECT_ROOT,
    ).run_agentteams(
        BenchmarkRunRequest(
                run_id="RUN-ABLATION-SERIAL",
                owner_human_no="E10281",
            scenario_id="lock_contention",
            budget=budget,
            fairness_fingerprint="same",
        ),
        mode="agentteams_serial",
    )
    uncoordinated_backend = AblationBackend()
    uncoordinated = DatabaseBenchmarkRunEngine(
        db_session, backend=uncoordinated_backend, project_root=PROJECT_ROOT,
    ).run_agentteams(
        BenchmarkRunRequest(
                run_id="RUN-ABLATION-UNCOORDINATED",
                owner_human_no="E10281",
            scenario_id="lock_contention",
            budget=budget,
            fairness_fingerprint="same",
        ),
        mode="agentteams_uncoordinated",
    )

    assert serial.mode == "agentteams_serial"
    assert serial.metrics.critical_path_steps == 7
    assert serial.metrics.manager_rounds == 2
    assert serial_backend.manager_plan_calls == 1
    assert serial_backend.uncoordinated_calls == 0
    assert uncoordinated.mode == "agentteams_uncoordinated"
    assert uncoordinated.metrics.critical_path_steps == 2
    assert uncoordinated.metrics.manager_rounds == 1
    assert uncoordinated.metrics.total_model_steps == 6
    assert uncoordinated_backend.manager_plan_calls == 0
    assert uncoordinated_backend.uncoordinated_calls == 1


def test_agentteams_can_execute_an_existing_generic_database_task(db_session):
    from app import models
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    run = AgentTeamsCollaborationService(
        max_children=5,
        capability_catalog_source="database",
    ).create_task(
        db_session,
        team_id="TEAM-DB-BENCH",
        leader_agent_id="VE-0001",
        title="锁等待诊断",
        instruction="请由数据库团队协作诊断。",
        material={"scenario_id": "lock_contention"},
    )
    run.owner_human_no = "E10281"
    db_session.flush()
    before = db_session.query(models.TaskRun).count()

    result = DatabaseBenchmarkRunEngine(
        db_session,
        backend=ScriptedBackend(),
        project_root=PROJECT_ROOT,
    ).run_agentteams(
        BenchmarkRunRequest(
            run_id=run.id,
            scenario_id="lock_contention",
            budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
            fairness_fingerprint="generic",
            owner_human_no="E10281",
        ),
        existing_task_run_id=run.id,
    )

    assert result.status == "completed"
    assert result.task_run_id == run.id
    assert db_session.query(models.TaskRun).count() == before
    assert db_session.get(models.AgentTaskNode, run.id).status == "completed"
    traces = [
        message.payload.get("payload", message.payload)
        for message in db_session.query(models.AgentMessage).filter_by(
            root_task_id=run.id,
            message_type="agent.message",
        )
        if message.payload.get("payload", message.payload).get("kind") == "decision.trace"
    ]
    assert len(traces) == 7
    assert all(trace["reasoning_segments"] for trace in traces)


class DelegatingSingleBackend(ScriptedBackend):
    def single_decide(self, **_kwargs):
        return {"action": "delegate", "recipient": "db_lock"}


def test_single_agent_fails_closed_when_backend_attempts_delegation(db_session):
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    engine = DatabaseBenchmarkRunEngine(
        db_session,
        backend=DelegatingSingleBackend(),
        project_root=PROJECT_ROOT,
    )
    result = engine.run_single(BenchmarkRunRequest(
        run_id="RUN-SINGLE-DELEGATE",
        scenario_id="lock_contention",
        budget=BenchmarkBudget(max_model_steps=3, max_tool_calls=3, timeout_ms=10_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "invalid"
    assert "single_agent_delegation_forbidden" in result.metrics.hard_gate_failures
    assert result.metrics.primary_accuracy == 0.0


class EndlessToolBackend(ScriptedBackend):
    def single_decide(self, **_kwargs):
        return {"action": "tool", "signal": "locks", "filters": {}, "limit": 20}


def test_single_agent_budget_exhaustion_is_measured_not_self_reported(db_session):
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    result = DatabaseBenchmarkRunEngine(
        db_session,
        backend=EndlessToolBackend(),
        project_root=PROJECT_ROOT,
    ).run_single(BenchmarkRunRequest(
        run_id="RUN-BUDGET",
        scenario_id="lock_contention",
        budget=BenchmarkBudget(max_model_steps=5, max_tool_calls=2, timeout_ms=10_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "failed"
    assert result.metrics.tool_call_count == 2
    assert "tool_call_budget_exhausted" in result.metrics.hard_gate_failures


class ConcurrentExpertBackend(ScriptedBackend):
    def __init__(self):
        super().__init__()
        self.barrier = Barrier(5, timeout=2)

    def expert_report(self, *, role, scenario, observation):
        self.barrier.wait()
        return super().expert_report(role=role, scenario=scenario, observation=observation)


def test_agentteams_expert_model_steps_reach_a_shared_concurrency_barrier(db_session):
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    backend = ConcurrentExpertBackend()
    result = DatabaseBenchmarkRunEngine(
        db_session,
        backend=backend,
        project_root=PROJECT_ROOT,
    ).run_agentteams(BenchmarkRunRequest(
        run_id="RUN-CONCURRENT",
        owner_human_no="E10281",
        scenario_id="lock_contention",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "completed"
    assert len(backend.expert_roles) == 5
    assert result.metrics.critical_path_steps == 3


def test_agentteams_run_is_materialized_by_existing_collaboration_state_machine(db_session):
    from app import models
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    result = DatabaseBenchmarkRunEngine(
        db_session,
        backend=ScriptedBackend(),
        project_root=PROJECT_ROOT,
    ).run_agentteams(BenchmarkRunRequest(
        run_id="RUN-COLLABORATION",
        owner_human_no="E10281",
        scenario_id="lock_contention",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "completed"
    assert result.task_run_id
    task_run = db_session.get(models.TaskRun, result.task_run_id)
    assert task_run is not None
    assert task_run.source == "agentteams"
    assert task_run.status == "completed"
    nodes = db_session.query(models.AgentTaskNode).filter_by(
        root_task_id=result.task_run_id,
    ).all()
    assert len(nodes) == 6
    assert {node.assignee_agent_id for node in nodes if node.depth == 1} == {
        "DB-WRITE-01", "DB-LOCK-01", "DB-VACUUM-01", "DB-INDEX-01", "DB-QUERY-01",
    }
    child_signals = {
        node.client_task_key: node.input_payload["signal"]
        for node in nodes
        if node.depth == 1
    }
    assert child_signals == {
        "db_write": "statements",
        "db_lock": "locks",
        "db_vacuum": "vacuum",
        "db_index": "indexes",
        "db_query": "table_stats",
    }
    event_types = {
        event.event_type
        for event in db_session.query(models.AgentTaskEvent).filter_by(
            root_task_id=result.task_run_id,
        )
    }
    assert {
        "task.delegate",
        "task.assigned",
        "tool.result",
        "task.result.submitted",
        "task.result.accepted",
        "task.completed",
        "task.summary",
    } <= event_types


def test_database_benchmark_uses_its_seeded_team_catalog_in_global_strict_mode(db_session, monkeypatch):
    from types import SimpleNamespace

    from app.services import agentteams_collaboration
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    monkeypatch.setattr(agentteams_collaboration.config, "agentteams_mode", lambda: "agentteams_strict")
    monkeypatch.setattr(
        agentteams_collaboration,
        "load_strict_runtime_config",
        lambda: SimpleNamespace(team_id="TEAM-OTHER"),
    )

    result = DatabaseBenchmarkRunEngine(
        db_session,
        backend=ScriptedBackend(),
        project_root=PROJECT_ROOT,
    ).run_agentteams(BenchmarkRunRequest(
        run_id="RUN-STRICT-DATABASE-BENCHMARK",
        owner_human_no="E10281",
        scenario_id="lock_contention",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "completed"
    assert result.task_run_id


class InvalidExpertBackend(ScriptedBackend):
    def expert_report(self, *, role, scenario, observation):
        if role == "db_index":
            return {"role": role, "finding": "missing evidence", "evidence_ids": []}
        return super().expert_report(role=role, scenario=scenario, observation=observation)


def test_invalid_expert_output_fails_closed_and_leaves_a_terminal_task_tree(db_session):
    from app import models
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    result = DatabaseBenchmarkRunEngine(
        db_session,
        backend=InvalidExpertBackend(),
        project_root=PROJECT_ROOT,
    ).run_agentteams(BenchmarkRunRequest(
        run_id="RUN-INVALID-EXPERT",
        owner_human_no="E10281",
        scenario_id="lock_contention",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "invalid"
    assert result.task_run_id
    assert "invalid_child_result" in result.metrics.hard_gate_failures
    task_run = db_session.get(models.TaskRun, result.task_run_id)
    assert task_run is not None
    assert task_run.status == "failed"


class InvalidManagerBackend(ScriptedBackend):
    def manager_diagnose(self, *, scenario, reports):
        return {"root_cause_id": "lock_wait_chain"}


def test_invalid_manager_output_fails_closed_and_leaves_a_terminal_task_tree(db_session):
    from app import models
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    result = DatabaseBenchmarkRunEngine(
        db_session,
        backend=InvalidManagerBackend(),
        project_root=PROJECT_ROOT,
    ).run_agentteams(BenchmarkRunRequest(
        run_id="RUN-INVALID-MANAGER",
        owner_human_no="E10281",
        scenario_id="lock_contention",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "invalid"
    assert "invalid_manager_result" in result.metrics.hard_gate_failures
    assert db_session.get(models.TaskRun, result.task_run_id).status == "failed"


class FreeformSingleLabelBackend(ScriptedBackend):
    def single_decide(self, *, scenario, observations, allowed_signals):
        if not observations:
            return super().single_decide(
                scenario=scenario,
                observations=observations,
                allowed_signals=allowed_signals,
            )
        return {
            "action": "complete",
            "result": {
                "root_cause_id": "orders_lock_chain_custom_name",
                "top_candidates": ["orders_lock_chain_custom_name"],
                "evidence_ids": [item["evidence_id"] for item in observations],
                "conclusion": "使用了未注册的自由命名标签。",
                "confidence": 0.9,
            },
        }


def test_single_agent_freeform_root_cause_label_fails_closed(db_session):
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    result = DatabaseBenchmarkRunEngine(
        db_session,
        backend=FreeformSingleLabelBackend(),
        project_root=PROJECT_ROOT,
    ).run_single(BenchmarkRunRequest(
        run_id="RUN-FREEFORM-SINGLE-LABEL",
        scenario_id="lock_contention",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "invalid"
    assert "invalid_single_agent_decision" in result.metrics.hard_gate_failures


class FreeformManagerLabelBackend(ScriptedBackend):
    def manager_diagnose(self, *, scenario, reports):
        result = super().manager_diagnose(scenario=scenario, reports=reports)
        result["root_cause_id"] = "orders_lock_chain_custom_name"
        result["top_candidates"] = ["orders_lock_chain_custom_name"]
        return result


def test_manager_freeform_root_cause_label_fails_closed(db_session):
    from app import models
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    result = DatabaseBenchmarkRunEngine(
        db_session,
        backend=FreeformManagerLabelBackend(),
        project_root=PROJECT_ROOT,
    ).run_agentteams(BenchmarkRunRequest(
        run_id="RUN-FREEFORM-MANAGER-LABEL",
        owner_human_no="E10281",
        scenario_id="lock_contention",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "invalid"
    assert "invalid_manager_result" in result.metrics.hard_gate_failures
    assert db_session.get(models.TaskRun, result.task_run_id).status == "failed"


class InvalidManagerPlanBackend(ScriptedBackend):
    def manager_plan(self, *, scenario, initial_assignment_count, reserved_followup_calls):
        plan = super().manager_plan(
            scenario=scenario,
            initial_assignment_count=initial_assignment_count,
            reserved_followup_calls=reserved_followup_calls,
        )
        plan["assignments"][1]["signal"] = "indexes"
        return plan


def test_invalid_manager_plan_fails_closed_before_any_worker_tool_call(db_session):
    from app import models
    from app.services.agentteams_database_benchmark_runner import (
        BenchmarkBudget,
        BenchmarkRunRequest,
        DatabaseBenchmarkRunEngine,
    )

    backend = InvalidManagerPlanBackend()
    result = DatabaseBenchmarkRunEngine(
        db_session,
        backend=backend,
        project_root=PROJECT_ROOT,
    ).run_agentteams(BenchmarkRunRequest(
        run_id="RUN-INVALID-MANAGER-PLAN",
        owner_human_no="E10281",
        scenario_id="lock_contention",
        budget=BenchmarkBudget(max_model_steps=20, max_tool_calls=10, timeout_ms=30_000),
        fairness_fingerprint="same",
    ))

    assert result.status == "invalid"
    assert "invalid_manager_plan" in result.metrics.hard_gate_failures
    assert result.metrics.tool_call_count == 0
    assert backend.expert_roles == []
    assert db_session.get(models.TaskRun, result.task_run_id).status == "failed"
