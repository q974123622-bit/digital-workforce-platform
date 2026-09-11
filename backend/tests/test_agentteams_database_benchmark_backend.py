import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeProvider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def structured_output_with_trace(self, messages, schema):
        from app.services.llm import StructuredLLMResponse

        self.calls.append((messages, schema))
        return StructuredLLMResponse(data=self.response, reasoning_content="内部推理摘要")


def test_backend_routes_single_expert_and_manager_calls_without_leaking_expected_label():
    from app.services.agentteams_database_benchmark_backend import DeepSeekDatabaseBenchmarkBackend
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    single = FakeProvider({"action": "tool", "signal": "locks", "filters": {}, "limit": 20})
    lock = FakeProvider({"role": "db_lock", "finding": "存在等待链。", "evidence_ids": ["db:lock_contention:locks:abc"]})
    manager = FakeProvider({
        "root_cause_id": "lock_wait_chain",
        "top_candidates": ["lock_wait_chain"],
        "evidence_ids": ["db:lock_contention:locks:abc"],
        "conclusion": "存在锁等待链。",
        "confidence": 0.9,
    })
    providers = {
        "single_agent": single,
        "manager": manager,
        "db_write": FakeProvider({}),
        "db_lock": lock,
        "db_vacuum": FakeProvider({}),
        "db_index": FakeProvider({}),
        "db_query": FakeProvider({}),
    }
    backend = DeepSeekDatabaseBenchmarkBackend(providers=providers, project_root=PROJECT_ROOT)
    scenario = DatabaseScenarioCatalog.from_root(PROJECT_ROOT).get("lock_contention")

    assert backend.single_decide(scenario=scenario, observations=[], allowed_signals=scenario.available_signals)["action"] == "tool"
    assert backend.expert_report(
        role="db_lock",
        scenario=scenario,
        observation={"signal": "locks", "evidence_id": "db:lock_contention:locks:abc", "rows": []},
    )["role"] == "db_lock"
    assert backend.manager_diagnose(
        scenario=scenario,
        reports=[{"role": "db_lock", "finding": "存在等待链。", "evidence_ids": ["db:lock_contention:locks:abc"]}],
    )["root_cause_id"] == "lock_wait_chain"

    prompts = "\n".join(
        message["content"]
        for provider in (single, lock, manager)
        for messages, _ in provider.calls
        for message in messages
    )
    assert "expected" not in prompts.lower()
    assert "evidence_id" in prompts
    assert all(message["source"] == "demo" for provider in (single, lock, manager) for messages, _ in provider.calls for message in messages)

    expert_messages, expert_schema = lock.calls[0]
    expert_payload = json.loads(expert_messages[1]["content"])
    assert expert_payload["required_role"] == "db_lock"
    assert expert_schema["properties"]["role"]["const"] == "db_lock"

    taxonomy = [
        "autovacuum_lag",
        "invalid_covering_index",
        "lock_wait_chain",
        "resource_heavy_analytics_query",
        "unbatched_bulk_writes",
    ]
    single_messages, single_schema = single.calls[0]
    manager_messages, manager_schema = manager.calls[0]
    assert json.loads(single_messages[1]["content"])["scenario"]["candidate_root_causes"] == taxonomy
    assert json.loads(manager_messages[1]["content"])["scenario"]["candidate_root_causes"] == taxonomy
    assert single_schema["$defs"]["DiagnosisResult"]["properties"]["root_cause_id"]["enum"] == taxonomy
    assert single_schema["$defs"]["DiagnosisResult"]["properties"]["top_candidates"]["items"]["enum"] == taxonomy
    assert manager_schema["properties"]["root_cause_id"]["enum"] == taxonomy
    assert manager_schema["properties"]["top_candidates"]["items"]["enum"] == taxonomy


def test_manager_planning_exports_only_public_scenario_and_bounded_team_catalog():
    from app.services.agentteams_database_benchmark_backend import DeepSeekDatabaseBenchmarkBackend
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    manager = FakeProvider({
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
    })
    providers = {
        "single_agent": FakeProvider({}),
        "manager": manager,
        "db_write": FakeProvider({}),
        "db_lock": FakeProvider({}),
        "db_vacuum": FakeProvider({}),
        "db_index": FakeProvider({}),
        "db_query": FakeProvider({}),
    }
    backend = DeepSeekDatabaseBenchmarkBackend(providers=providers, project_root=PROJECT_ROOT)
    scenario = DatabaseScenarioCatalog.from_root(PROJECT_ROOT).get("lock_contention")

    assert len(backend.manager_plan(
        scenario=scenario,
        initial_assignment_count=5,
        reserved_followup_calls=0,
    )["assignments"]) == 5

    messages, schema = manager.calls[0]
    exported = json.loads(messages[1]["content"])
    assert exported["scenario"] == {
        "id": "lock_contention",
        "title": scenario.title,
        "task": scenario.task,
        "available_signals": list(scenario.available_signals),
    }
    assert exported["team_catalog"] == [
        {"role": "db_write", "agent_id": "DB-WRITE-01", "allowed_signals": ["statements", "table_stats"], "allowed_filters_by_signal": {"statements": ["database", "query_id"], "table_stats": ["table"]}},
        {"role": "db_lock", "agent_id": "DB-LOCK-01", "allowed_signals": ["locks"], "allowed_filters_by_signal": {"locks": ["pid", "relation", "state"]}},
        {"role": "db_vacuum", "agent_id": "DB-VACUUM-01", "allowed_signals": ["vacuum", "table_stats"], "allowed_filters_by_signal": {"vacuum": ["table"], "table_stats": ["table"]}},
        {"role": "db_index", "agent_id": "DB-INDEX-01", "allowed_signals": ["indexes", "statements"], "allowed_filters_by_signal": {"indexes": ["index_name", "table"], "statements": ["database", "query_id"]}},
        {"role": "db_query", "agent_id": "DB-QUERY-01", "allowed_signals": ["statements", "table_stats"], "allowed_filters_by_signal": {"statements": ["database", "query_id"], "table_stats": ["table"]}},
    ]
    assert exported["planning_constraints"] == {
        "initial_assignment_count": 5,
        "reserved_followup_calls": 0,
    }
    assert schema["properties"]["assignments"]["minItems"] == 5
    assert schema["properties"]["assignments"]["maxItems"] == 5
    filters_schema = schema["$defs"]["DatabaseInspectionFilters"]
    assert filters_schema["additionalProperties"] is False
    assert set(filters_schema["properties"]) == {
        "relation", "query_id", "table", "database", "state", "index_name", "pid",
    }
    assert "focus" not in filters_schema["properties"]
    assert exported["inspection_contract"]["signals"]["statements"] == {
        "filter_fields": ["database", "query_id"],
        "allows_empty_filters": True,
    }
    serialized = json.dumps(exported, ensure_ascii=False).lower()
    assert "lock_wait_chain" not in serialized
    assert "expected" not in serialized
    assert "directory" not in serialized
    assert "api_key" not in serialized
    assert "sk-" not in serialized


def test_manager_review_receives_public_reports_and_remaining_budget_without_hidden_labels():
    from app.services.agentteams_database_benchmark_backend import DeepSeekDatabaseBenchmarkBackend
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    manager = FakeProvider({"action": "complete", "rationale": "证据已经充分。"})
    providers = {
        "single_agent": FakeProvider({}), "manager": manager,
        "db_write": FakeProvider({}), "db_lock": FakeProvider({}),
        "db_vacuum": FakeProvider({}), "db_index": FakeProvider({}), "db_query": FakeProvider({}),
    }
    backend = DeepSeekDatabaseBenchmarkBackend(providers=providers, project_root=PROJECT_ROOT)
    scenario = DatabaseScenarioCatalog.from_root(PROJECT_ROOT).get("compound_lock_index")

    decision = backend.manager_review(
        scenario=scenario,
        reports=[{"role": "db_lock", "finding": "存在锁等待。", "evidence_ids": ["db:x:locks:a"]}],
        observed_signals=["locks"],
        remaining_tool_calls=1,
    )

    assert decision["action"] == "complete"
    payload = json.loads(manager.calls[0][0][1]["content"])
    assert payload["remaining_tool_calls"] == 1
    assert payload["observed_signals"] == ["locks"]
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "secondary_causes" not in serialized
    assert "requires_followup_signals" not in serialized
    assert "expected" not in serialized
    _, schema = manager.calls[0]
    assert schema["$defs"]["DatabaseInspectionFilters"]["additionalProperties"] is False
    assert payload["inspection_contract"]["signals"]["locks"]["filter_fields"] == [
        "pid", "relation", "state",
    ]


def test_single_agent_actual_request_uses_the_same_strict_database_filter_contract():
    """Leaving Single Agent on an open dict schema must break this test."""
    from app.services.agentteams_database_benchmark_backend import DeepSeekDatabaseBenchmarkBackend
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    single = FakeProvider({"action": "tool", "signal": "statements", "filters": {}, "limit": 20})
    providers = {
        "single_agent": single, "manager": FakeProvider({}),
        "db_write": FakeProvider({}), "db_lock": FakeProvider({}),
        "db_vacuum": FakeProvider({}), "db_index": FakeProvider({}), "db_query": FakeProvider({}),
    }
    backend = DeepSeekDatabaseBenchmarkBackend(providers=providers, project_root=PROJECT_ROOT)
    scenario = DatabaseScenarioCatalog.from_root(PROJECT_ROOT).get("budgeted_incident_triage")

    backend.single_decide(
        scenario=scenario, observations=[], allowed_signals=scenario.available_signals,
    )

    messages, schema = single.calls[0]
    payload = json.loads(messages[1]["content"])
    assert schema["$defs"]["DatabaseInspectionFilters"]["additionalProperties"] is False
    assert payload["inspection_contract"]["signals"] == {
        "statements": {"filter_fields": ["database", "query_id"], "allows_empty_filters": True},
        "locks": {"filter_fields": ["pid", "relation", "state"], "allows_empty_filters": True},
        "indexes": {"filter_fields": ["index_name", "table"], "allows_empty_filters": True},
        "vacuum": {"filter_fields": ["table"], "allows_empty_filters": True},
        "table_stats": {"filter_fields": ["table"], "allows_empty_filters": True},
    }


def test_factory_requires_six_role_specific_keys_and_never_uses_shared_fallback():
    from app.services.agentteams_database_benchmark_backend import build_deepseek_database_backend

    required = {
        "DEEPSEEK_API_KEY_MANAGER": "manager-key",
        "DEEPSEEK_API_KEY_DB_WRITE": "write-key",
        "DEEPSEEK_API_KEY_DB_LOCK": "lock-key",
        "DEEPSEEK_API_KEY_DB_VACUUM": "vacuum-key",
        "DEEPSEEK_API_KEY_DB_INDEX": "index-key",
        "DEEPSEEK_API_KEY_DB_QUERY": "query-key",
    }
    seen = []

    class Provider:
        def __init__(self, *, api_key, model, thinking, reasoning_effort, request_timeout):
            seen.append((api_key, model, thinking, reasoning_effort, request_timeout))

    backend = build_deepseek_database_backend(
        project_root=PROJECT_ROOT,
        environ=required,
        provider_factory=Provider,
    )

    assert backend.model_name
    assert {item[0] for item in seen} == set(required.values())
    assert all(item[2] is False for item in seen)
    assert all(item[3] == "low" for item in seen)
    assert backend.model_settings == {
        "thinking": False,
        "reasoning_effort": "low",
        "structured_output": "json_object",
        "request_timeout_seconds": 90.0,
        "prompt_contract": "database-diagnosis-taxonomy-v1",
    }
    assert all(item[4] == 90.0 for item in seen)

    with pytest.raises(ValueError, match="database_benchmark_key_missing"):
        build_deepseek_database_backend(
            project_root=PROJECT_ROOT,
            environ={"DEEPSEEK_API_KEY": "shared-key"},
            provider_factory=Provider,
        )
