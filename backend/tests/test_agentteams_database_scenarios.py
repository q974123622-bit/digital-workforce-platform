from pathlib import Path

import pytest
from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_catalog_keeps_legacy_scenarios_and_loads_three_coordination_scenarios():
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    catalog = DatabaseScenarioCatalog.from_root(PROJECT_ROOT)

    assert catalog.ids() == [
        "bulk_writes",
        "lock_contention",
        "vacuum_pressure",
        "index_anomaly",
        "large_query",
        "compound_lock_index",
        "conflicting_signals_followup",
        "budgeted_incident_triage",
    ]
    scenario = catalog.get("lock_contention")
    assert scenario.expected.root_cause_id == "lock_wait_chain"
    assert set(scenario.available_signals) == {
        "statements",
        "locks",
        "indexes",
        "vacuum",
        "table_stats",
    }

    compound = catalog.get("compound_lock_index")
    assert compound.complexity == "coordination"
    assert compound.tool_budget == 4
    assert compound.expected.secondary_causes == ("invalid_covering_index",)
    assert compound.expected.requires_followup_signals == ("indexes",)

    conflicting = catalog.get("conflicting_signals_followup")
    assert conflicting.complexity == "coordination"
    assert conflicting.tool_budget == 4
    assert conflicting.expected.requires_followup_signals == ("locks",)

    budgeted = catalog.get("budgeted_incident_triage")
    assert budgeted.complexity == "coordination"
    assert budgeted.tool_budget == 3
    assert budgeted.expected.secondary_causes == ("autovacuum_lag",)
    assert budgeted.expected.requires_followup_signals == ("table_stats",)


def test_inspection_returns_bounded_rows_and_stable_evidence_without_expected_labels():
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    catalog = DatabaseScenarioCatalog.from_root(PROJECT_ROOT)

    first = catalog.inspect(
        "lock_contention",
        "locks",
        filters={"relation": "orders"},
        limit=1,
    )
    second = catalog.inspect(
        "lock_contention",
        "locks",
        filters={"relation": "orders"},
        limit=1,
    )

    assert first.evidence_id == second.evidence_id
    assert first.evidence_id.startswith("db:lock_contention:locks:")
    assert first.source == "local_fixture"
    assert len(first.rows) == 1
    serialized = first.model_dump_json()
    assert "root_cause_id" not in serialized
    assert "expected" not in serialized


@pytest.mark.parametrize(
    ("scenario_id", "signal", "filters", "limit"),
    [
        ("../expected", "locks", {}, 10),
        ("unknown", "locks", {}, 10),
        ("lock_contention", "expected", {}, 10),
        ("lock_contention", "locks", {}, 0),
        ("lock_contention", "locks", {}, 101),
        ("lock_contention", "locks", {"unknown": "x"}, 10),
    ],
)
def test_inspection_rejects_unknown_or_unbounded_inputs(scenario_id, signal, filters, limit):
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    catalog = DatabaseScenarioCatalog.from_root(PROJECT_ROOT)

    with pytest.raises(ValueError, match="invalid_database_inspection"):
        catalog.inspect(scenario_id, signal, filters=filters, limit=limit)


@pytest.mark.parametrize(
    "filters",
    [
        {"focus": "slow_customer_search"},
        {"scenario_id": "budgeted_incident_triage"},
        {"database": None},
        {"database": ""},
        {"database": 123},
        {"database": "x" * 129},
    ],
)
def test_database_inspection_filters_strictly_reject_extra_null_empty_wrong_type_and_long_values(filters):
    """Removing extra=forbid or bounded strict strings must break this test."""
    from app.services.agentteams_database_scenarios import DatabaseInspectionFilters

    with pytest.raises(ValidationError):
        DatabaseInspectionFilters.model_validate(filters)


def test_complete_request_distinguishes_empty_filters_from_missing_or_null_and_accepts_length_boundary():
    """Defaulting a missing request field or rejecting the documented boundary must fail."""
    from app.services.agentteams_database_scenarios import DatabaseInspectionRequest

    base = {"scenario_id": "lock_contention", "signal": "locks", "limit": 20}
    with pytest.raises(ValidationError):
        DatabaseInspectionRequest.model_validate(base)
    with pytest.raises(ValidationError):
        DatabaseInspectionRequest.model_validate({**base, "filters": None})

    empty = DatabaseInspectionRequest.model_validate({**base, "filters": {}})
    boundary = DatabaseInspectionRequest.model_validate({
        **base, "filters": {"relation": "x" * 128},
    })
    assert empty.filters.model_dump(exclude_none=True) == {}
    assert len(boundary.filters.relation) == 128


@pytest.mark.parametrize(
    ("signal", "filters"),
    [
        ("statements", {"relation": "orders"}),
        ("locks", {"database": "orders"}),
        ("indexes", {"pid": "8101"}),
        ("vacuum", {"index_name": "idx_orders"}),
        ("table_stats", {"state": "waiting"}),
    ],
)
def test_complete_request_rejects_known_filter_fields_for_the_wrong_signal(signal, filters):
    """A global field allowlist without signal-to-field validation must fail."""
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    catalog = DatabaseScenarioCatalog.from_root(PROJECT_ROOT)

    with pytest.raises(ValueError, match="database_filter_not_allowed_for_signal"):
        catalog.validate_inspection_request(
            "lock_contention", signal, filters=filters, limit=20,
        )


@pytest.mark.parametrize(
    ("signal", "filters"),
    [
        ("statements", {}),
        ("statements", {"database": "orders", "query_id": "lc-1"}),
        ("locks", {"relation": "orders", "state": "waiting", "pid": "8102"}),
        ("indexes", {"table": "customers", "index_name": "customers_email_idx"}),
        ("vacuum", {"table": "orders"}),
        ("table_stats", {"table": "orders"}),
    ],
)
def test_complete_request_accepts_only_signal_compatible_filters_including_allowed_empty(signal, filters):
    """Over-restricting valid filtered or explicitly unfiltered requests must fail."""
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    request = DatabaseScenarioCatalog.from_root(PROJECT_ROOT).validate_inspection_request(
        "lock_contention", signal, filters=filters, limit=20,
    )

    assert request.filters.model_dump(exclude_none=True) == filters


def test_complete_request_rejects_a_signal_not_supported_by_the_selected_scenario():
    """Skipping the scenario-to-signal relation must break this test."""
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    catalog = DatabaseScenarioCatalog.from_root(PROJECT_ROOT)
    scenario = catalog.get("lock_contention")
    restricted = scenario.model_copy(update={"available_signals": ("locks",)})
    catalog._by_id[scenario.id] = restricted

    with pytest.raises(ValueError, match="database_signal_not_available_for_scenario"):
        catalog.validate_inspection_request(
            "lock_contention", "statements", filters={}, limit=20,
        )


def test_catalog_rejects_a_manifest_that_points_outside_its_fixture_root(tmp_path):
    from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog

    fixture_root = tmp_path / "mock-data" / "agentteams-benchmark" / "database"
    fixture_root.mkdir(parents=True)
    (fixture_root / "scenarios.jsonl").write_text(
        '{"id":"escape","title":"x","task":"x","directory":"../outside"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid_database_scenario_catalog"):
        DatabaseScenarioCatalog.from_root(tmp_path)
