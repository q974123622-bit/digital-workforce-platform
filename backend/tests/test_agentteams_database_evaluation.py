from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _expected_and_evidence(scenario_id="lock_contention"):
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    catalog = DatabaseScenarioCatalog.from_root(PROJECT_ROOT)
    expected = catalog.get(scenario_id).expected
    evidence = {
        signal: catalog.inspect(scenario_id, signal, filters={}, limit=20).evidence_id
        for signal in expected.required_signals
    }
    return expected, evidence


def test_correct_diagnosis_with_audited_required_evidence_passes_all_result_gates():
    from app.services.agentteams_database_evaluation import (
        DiagnosisResult,
        ProcessFacts,
        evaluate_diagnosis,
    )

    expected, evidence = _expected_and_evidence()
    result = DiagnosisResult(
        root_cause_id="lock_wait_chain",
        top_candidates=["lock_wait_chain", "slow_query"],
        evidence_ids=list(evidence.values()),
        conclusion="长事务形成锁等待链。",
        confidence=0.94,
    )

    score = evaluate_diagnosis(
        scenario_id="lock_contention",
        expected=expected,
        result=result,
        valid_audit_evidence_ids=set(evidence.values()),
        facts=ProcessFacts(
            end_to_end_ms=1200,
            model_step_durations_ms=[400, 500],
            tool_evidence_ids=list(evidence.values()),
            manager_rounds=2,
            rework_count=0,
            required_child_count=2,
            received_child_result_count=2,
            terminal_status="completed",
        ),
    )

    assert score.primary_accuracy == 1.0
    assert score.top2_hit == 1.0
    assert score.evidence_coverage == 1.0
    assert score.evidence_validity == 1.0
    assert score.hard_gate_passed is True
    assert score.total_model_steps == 2
    assert score.tool_call_count == 2
    assert score.critical_path_steps == 2
    assert score.parallel_speedup == pytest.approx(0.75)


def test_wrong_primary_answer_can_have_top2_credit_without_primary_accuracy():
    from app.services.agentteams_database_evaluation import DiagnosisResult, evaluate_diagnosis

    expected, evidence = _expected_and_evidence()
    score = evaluate_diagnosis(
        scenario_id="lock_contention",
        expected=expected,
        result=DiagnosisResult(
            root_cause_id="slow_query",
            top_candidates=["slow_query", "lock_wait_chain"],
            evidence_ids=list(evidence.values()),
            conclusion="候选中包含锁等待。",
            confidence=0.55,
        ),
        valid_audit_evidence_ids=set(evidence.values()),
    )

    assert score.primary_accuracy == 0.0
    assert score.top2_hit == 1.0
    assert score.hard_gate_passed is True


@pytest.mark.parametrize(
    ("evidence_id", "expected_failure"),
    [
        ("db:other_scenario:locks:forged", "cross_scenario_evidence"),
        ("db:lock_contention:locks:forged", "unaudited_evidence"),
        ("made-up", "invalid_evidence_id"),
    ],
)
def test_forged_or_unaudited_evidence_fails_the_hard_gate(evidence_id, expected_failure):
    from app.services.agentteams_database_evaluation import DiagnosisResult, evaluate_diagnosis

    expected, _ = _expected_and_evidence()
    score = evaluate_diagnosis(
        scenario_id="lock_contention",
        expected=expected,
        result=DiagnosisResult(
            root_cause_id="lock_wait_chain",
            top_candidates=["lock_wait_chain"],
            evidence_ids=[evidence_id],
            conclusion="结论引用了无效证据。",
            confidence=0.8,
        ),
        valid_audit_evidence_ids=set(),
    )

    assert score.primary_accuracy == 0.0
    assert score.hard_gate_passed is False
    assert expected_failure in score.hard_gate_failures


def test_process_metrics_count_duplicate_calls_and_lost_child_results():
    from app.services.agentteams_database_evaluation import (
        DiagnosisResult,
        ProcessFacts,
        evaluate_diagnosis,
    )

    expected, evidence = _expected_and_evidence()
    evidence_ids = list(evidence.values())
    score = evaluate_diagnosis(
        scenario_id="lock_contention",
        expected=expected,
        result=DiagnosisResult(
            root_cause_id="lock_wait_chain",
            top_candidates=["lock_wait_chain"],
            evidence_ids=evidence_ids,
            conclusion="锁等待链成立。",
            confidence=0.9,
        ),
        valid_audit_evidence_ids=set(evidence_ids),
        facts=ProcessFacts(
            end_to_end_ms=900,
            model_step_durations_ms=[200, 300, 400],
            tool_evidence_ids=[evidence_ids[0], evidence_ids[0], evidence_ids[1]],
            manager_rounds=3,
            rework_count=1,
            required_child_count=3,
            received_child_result_count=2,
            terminal_status="completed",
        ),
    )

    assert score.tool_call_count == 3
    assert score.duplicate_tool_calls == 1
    assert score.lost_child_results == 1
    assert score.hard_gate_passed is False
    assert "lost_child_result" in score.hard_gate_failures
    assert score.primary_accuracy == 0.0


def test_illegal_terminal_status_and_explicit_budget_failure_are_hard_gates():
    from app.services.agentteams_database_evaluation import (
        DiagnosisResult,
        ProcessFacts,
        evaluate_diagnosis,
    )

    expected, evidence = _expected_and_evidence()
    evidence_ids = list(evidence.values())
    score = evaluate_diagnosis(
        scenario_id="lock_contention",
        expected=expected,
        result=DiagnosisResult(
            root_cause_id="lock_wait_chain",
            top_candidates=["lock_wait_chain"],
            evidence_ids=evidence_ids,
            conclusion="锁等待链成立。",
            confidence=0.9,
        ),
        valid_audit_evidence_ids=set(evidence_ids),
        facts=ProcessFacts(
            terminal_status="running",
            failure_codes=["model_step_budget_exhausted"],
        ),
    )

    assert score.hard_gate_passed is False
    assert set(score.hard_gate_failures) >= {
        "invalid_terminal_status",
        "model_step_budget_exhausted",
    }


def test_complex_diagnosis_requires_secondary_cause_full_evidence_and_effective_followup():
    from app.services.agentteams_database_evaluation import (
        DiagnosisResult,
        ProcessFacts,
        evaluate_diagnosis,
    )
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    catalog = DatabaseScenarioCatalog.from_root(PROJECT_ROOT)
    scenario = catalog.get("compound_lock_index")
    evidence = {
        signal: catalog.inspect(scenario.id, signal, filters={}, limit=20).evidence_id
        for signal in ("locks", "statements", "indexes", "vacuum")
    }
    score = evaluate_diagnosis(
        scenario_id=scenario.id,
        expected=scenario.expected,
        result=DiagnosisResult(
            root_cause_id="lock_wait_chain",
            top_candidates=["lock_wait_chain", "invalid_covering_index"],
            evidence_ids=[evidence["locks"], evidence["statements"], evidence["indexes"]],
            conclusion="锁等待是主因，无效索引是伴随问题。",
            confidence=0.91,
        ),
        valid_audit_evidence_ids=set(evidence.values()),
        facts=ProcessFacts(
            model_step_durations_ms=[10] * 8,
            tool_evidence_ids=[evidence["locks"], evidence["statements"], evidence["vacuum"], evidence["indexes"]],
            followup_evidence_ids=[evidence["indexes"]],
            tool_budget=4,
            rework_count=1,
        ),
    )

    assert score.primary_accuracy == 1.0
    assert score.secondary_cause_hit == 1.0
    assert score.conflict_resolution_rate == 1.0
    assert score.effective_rework_rate == 1.0
    assert score.budget_success_rate == 1.0
    assert score.success_rate == 1.0
    assert score.hard_gate_passed is True


def test_complex_quality_miss_scores_zero_without_invalidating_the_run():
    from app.services.agentteams_database_evaluation import (
        DiagnosisResult,
        ProcessFacts,
        evaluate_diagnosis,
    )
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    catalog = DatabaseScenarioCatalog.from_root(PROJECT_ROOT)
    scenario = catalog.get("compound_lock_index")
    evidence = {
        signal: catalog.inspect(scenario.id, signal, filters={}, limit=20).evidence_id
        for signal in ("locks", "statements", "indexes")
    }
    score = evaluate_diagnosis(
        scenario_id=scenario.id,
        expected=scenario.expected,
        result=DiagnosisResult(
            root_cause_id="lock_wait_chain",
            top_candidates=["lock_wait_chain"],
            evidence_ids=list(evidence.values()),
            conclusion="只报告了锁等待。",
            confidence=0.8,
        ),
        valid_audit_evidence_ids=set(evidence.values()),
        facts=ProcessFacts(
            tool_evidence_ids=list(evidence.values()),
            followup_evidence_ids=[],
            tool_budget=4,
            rework_count=1,
        ),
    )

    assert score.secondary_cause_hit == 0.0
    assert score.conflict_resolution_rate == 0.0
    assert score.effective_rework_rate == 0.0
    assert score.success_rate == 0.0
    assert score.primary_accuracy == 1.0
    assert score.hard_gate_passed is True
    assert score.hard_gate_failures == []


def test_complex_diagnosis_accepts_a_new_audited_followup_signal_that_resolves_the_conflict():
    from app.services.agentteams_database_evaluation import (
        DiagnosisResult,
        ProcessFacts,
        evaluate_diagnosis,
    )
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    catalog = DatabaseScenarioCatalog.from_root(PROJECT_ROOT)
    scenario = catalog.get("compound_lock_index")
    evidence = {
        signal: catalog.inspect(scenario.id, signal, filters={}, limit=20).evidence_id
        for signal in ("locks", "statements", "indexes", "table_stats")
    }
    score = evaluate_diagnosis(
        scenario_id=scenario.id,
        expected=scenario.expected,
        result=DiagnosisResult(
            root_cause_id="lock_wait_chain",
            top_candidates=["lock_wait_chain", "invalid_covering_index"],
            evidence_ids=list(evidence.values()),
            conclusion="复核表统计后，锁等待仍是主因，无效索引是伴随问题。",
            confidence=0.91,
        ),
        valid_audit_evidence_ids=set(evidence.values()),
        facts=ProcessFacts(
            tool_evidence_ids=list(evidence.values()),
            followup_evidence_ids=[evidence["table_stats"]],
            tool_budget=4,
            rework_count=1,
        ),
    )

    assert score.conflict_resolution_rate == 1.0
    assert score.effective_rework_rate == 1.0
    assert score.success_rate == 1.0
    assert score.hard_gate_passed is True


def test_complex_diagnosis_fails_when_tool_budget_is_exceeded():
    from app.services.agentteams_database_evaluation import DiagnosisResult, ProcessFacts, evaluate_diagnosis
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    catalog = DatabaseScenarioCatalog.from_root(PROJECT_ROOT)
    scenario = catalog.get("budgeted_incident_triage")
    evidence = {
        signal: catalog.inspect(scenario.id, signal, filters={}, limit=20).evidence_id
        for signal in ("indexes", "vacuum", "table_stats", "statements")
    }
    score = evaluate_diagnosis(
        scenario_id=scenario.id,
        expected=scenario.expected,
        result=DiagnosisResult(
            root_cause_id="invalid_covering_index",
            top_candidates=["invalid_covering_index", "autovacuum_lag"],
            evidence_ids=[evidence["indexes"], evidence["vacuum"], evidence["table_stats"]],
            conclusion="索引失效为主因，清理滞后为伴随问题。",
            confidence=0.9,
        ),
        valid_audit_evidence_ids=set(evidence.values()),
        facts=ProcessFacts(tool_evidence_ids=list(evidence.values()), tool_budget=3),
    )

    assert score.budget_success_rate == 0.0
    assert score.hard_gate_passed is False
    assert "tool_budget_exceeded" in score.hard_gate_failures
