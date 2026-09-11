import pytest
from fastapi import HTTPException
from pathlib import Path

from app import models
from app.services.adapters import AdapterInputError, run_adapter
from app.services.agentteams_database_scenarios import DatabaseScenarioCatalog
from app.services.gateway import invoke_plugin


PROJECT_ROOT = Path(__file__).resolve().parents[2]


VALID_PARAMS = {
    "scenario_id": "bulk_writes",
    "signal": "statements",
    "filters": {},
    "limit": 20,
}


def test_database_signal_tool_runs_through_gateway_and_creates_allow_audit(db_session):
    result = invoke_plugin(
        db_session,
        employee_id="DB-WRITE-01",
        plugin_id="agentteams-database-inspector",
        action="read",
        params=VALID_PARAMS,
        trace_id="BENCH-RUN-1",
    )

    assert result["ok"] is True
    assert result["data"]["source"] == "local_fixture"
    assert result["data"]["scenario_id"] == "bulk_writes"
    assert result["data"]["signal"] == "statements"
    assert result["data"]["evidence_id"].startswith("db:bulk_writes:statements:")
    assert result["audit_ids"]
    audit = db_session.get(models.AuditEvent, result["audit_ids"][0])
    assert audit.trace_id == "BENCH-RUN-1"
    assert audit.plugin_id == "agentteams-database-inspector"
    assert audit.decision == "allow"


def test_database_signal_tool_is_denied_for_an_ungranted_employee(db_session):
    with pytest.raises(HTTPException) as error:
        invoke_plugin(
            db_session,
            employee_id="VE-0002",
            plugin_id="agentteams-database-inspector",
            action="read",
            params=VALID_PARAMS,
            trace_id="BENCH-DENY-1",
        )

    assert error.value.status_code == 403
    audit = db_session.get(models.AuditEvent, error.value.detail["audit_id"])
    assert audit.decision == "deny"


def test_database_worker_cannot_read_a_signal_outside_its_server_side_scope(db_session):
    with pytest.raises(HTTPException) as error:
        invoke_plugin(
            db_session,
            employee_id="DB-WRITE-01",
            plugin_id="agentteams-database-inspector",
            action="read",
            params={**VALID_PARAMS, "signal": "locks"},
            trace_id="BENCH-DENY-SIGNAL",
        )

    assert error.value.status_code == 403
    audit = db_session.get(models.AuditEvent, error.value.detail["audit_id"])
    assert audit.decision == "deny"
    assert audit.reason == "database_signal_not_authorized"


@pytest.mark.parametrize(
    "params",
    [
        {**VALID_PARAMS, "signal": "expected"},
        {**VALID_PARAMS, "scenario_id": "../lock_contention"},
        {**VALID_PARAMS, "limit": 0},
        {**VALID_PARAMS, "limit": 101},
        {**VALID_PARAMS, "filters": {"path": "expected.json"}},
        {**VALID_PARAMS, "extra": "not-allowed"},
    ],
)
def test_database_signal_tool_rejects_unbounded_input_before_adapter(db_session, params):
    before = db_session.query(models.AuditEvent).count()

    with pytest.raises(HTTPException) as error:
        invoke_plugin(
            db_session,
            employee_id="DB-WRITE-01",
            plugin_id="agentteams-database-inspector",
            action="read",
            params=params,
            trace_id="BENCH-BAD-INPUT",
        )

    assert error.value.status_code == 400
    assert db_session.query(models.AuditEvent).count() == before + 1


def test_direct_database_adapter_call_keeps_strict_validation(db_session):
    plugin = db_session.get(models.Plugin, "agentteams-database-inspector")

    with pytest.raises(AdapterInputError, match="invalid_database_inspection"):
        run_adapter(plugin, {**VALID_PARAMS, "signal": "expected"})


def test_catalog_adapter_and_gateway_consistently_reject_a_known_filter_for_the_wrong_signal(db_session):
    """Duplicated per-layer rules that disagree on cross-signal filters must fail."""
    params = {**VALID_PARAMS, "filters": {"relation": "orders"}}
    catalog = DatabaseScenarioCatalog.from_root(PROJECT_ROOT)
    plugin = db_session.get(models.Plugin, "agentteams-database-inspector")

    with pytest.raises(ValueError, match="database_filter_not_allowed_for_signal"):
        catalog.validate_inspection_request(
            params["scenario_id"], params["signal"],
            filters=params["filters"], limit=params["limit"],
        )
    with pytest.raises(AdapterInputError, match="invalid_database_inspection"):
        run_adapter(plugin, params)
    with pytest.raises(HTTPException) as gateway_error:
        invoke_plugin(
            db_session,
            employee_id="DB-WRITE-01",
            plugin_id="agentteams-database-inspector",
            action="read",
            params=params,
            trace_id="BENCH-CROSS-SIGNAL-FILTER",
        )
    assert gateway_error.value.status_code == 400


def test_gateway_accepts_a_new_scenario_with_valid_empty_filters_and_audited_evidence(db_session):
    """A fix that blocks valid unfiltered requests or new scenarios must fail."""
    result = invoke_plugin(
        db_session,
        employee_id="DB-QUERY-01",
        plugin_id="agentteams-database-inspector",
        action="read",
        params={
            "scenario_id": "budgeted_incident_triage",
            "signal": "statements",
            "filters": {},
            "limit": 20,
        },
        trace_id="BENCH-NEW-SCENARIO-VALID",
    )

    assert result["ok"] is True
    assert result["data"]["evidence_id"].startswith(
        "db:budgeted_incident_triage:statements:"
    )
    assert db_session.get(models.AuditEvent, result["audit_ids"][0]).decision == "allow"
