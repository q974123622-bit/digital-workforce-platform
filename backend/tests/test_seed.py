"""Seed data regressions for governed AgentTeams demo tools."""

from app import models
from copy import deepcopy
from pathlib import Path

from app.seed import load_seed, sync_database_benchmark_resources


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_agentteams_onboarding_demo_seed_registers_domain_workers_and_plugins(db_session):
    data = load_seed()
    plugin_ids = {row["id"] for row in data["plugins"]}
    assert {
        "agentteams-onboarding-employee",
        "agentteams-it-readiness",
        "agentteams-onboarding-evidence",
    } <= plugin_ids

    audit_worker = db_session.get(models.DigitalEmployee, "VE-AUDIT-REVIEW")
    assert audit_worker is not None
    assert audit_worker.runtime_ref == "audit-review-worker"
    members = {
        member.employee_id
        for member in db_session.query(models.TeamMember).filter_by(team_id="TEAM-ONBOARD")
    }
    assert "VE-0005" in members


def test_agentteams_onboarding_demo_seed_grants_only_the_new_domain_plugins(db_session):
    expected = {
        "VE-HR-MATERIAL": "agentteams-onboarding-materials",
        "VE-HR-TRAINING": "agentteams-onboarding-training",
        "VE-IT-ACCOUNT": "agentteams-it-accounts",
        "VE-IT-DEVICE": "agentteams-it-devices",
        "VE-AUDIT-REVIEW": "agentteams-onboarding-evidence",
    }
    grants = db_session.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.employee_id.in_(set(expected)),
        models.EmployeePluginGrant.plugin_id.in_(set(expected.values())),
    ).all()

    assert {(grant.employee_id, grant.plugin_id, grant.action, grant.decision_mode) for grant in grants} == {
        (employee_id, plugin_id, "execute", "allow")
        for employee_id, plugin_id in expected.items()
    }


def test_agentteams_onboarding_plugins_publish_strict_employee_ref_schema():
    data = load_seed()
    expected_endpoints = {
        "agentteams-onboarding-employee": "mock://agentteams/onboarding-employee",
        "agentteams-it-readiness": "mock://agentteams/it-readiness",
        "agentteams-onboarding-evidence": "mock://agentteams/onboarding-evidence",
    }
    plugins = {plugin["id"]: plugin for plugin in data["plugins"]}

    for plugin_id, endpoint in expected_endpoints.items():
        schema = plugins[plugin_id]["runtime_meta"]["input_schema"]
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["employee_ref"]
        assert schema["properties"]["employee_ref"] == {
            "type": "string", "const": "新员工A", "maxLength": 16,
        }
        assert plugins[plugin_id]["endpoint_ref"] == endpoint


def test_database_benchmark_seed_schema_matches_the_generated_runtime_contract():
    """A stale hand-maintained seed schema must break this test."""
    from app.services.agentteams_database_scenarios import (
        DatabaseScenarioCatalog,
        database_inspection_input_schema,
    )

    data = load_seed()
    plugin = next(
        row for row in data["plugins"] if row["id"] == "agentteams-database-inspector"
    )
    expected = database_inspection_input_schema(
        DatabaseScenarioCatalog.from_root(PROJECT_ROOT).ids()
    )

    assert plugin["runtime_meta"]["input_schema"] == expected


def test_database_benchmark_sync_upgrades_existing_plugin_idempotently_without_touching_onboarding(db_session):
    """A create-only sync or broad resource reset must break this test."""
    from app.services.agentteams_database_scenarios import (
        DatabaseScenarioCatalog,
        database_inspection_input_schema,
    )

    database_plugin = db_session.get(models.Plugin, "agentteams-database-inspector")
    onboarding_plugin = db_session.get(models.Plugin, "agentteams-onboarding-employee")
    onboarding_before = deepcopy(onboarding_plugin.runtime_meta)
    database_plugin.runtime_meta = {
        **database_plugin.runtime_meta,
        "input_schema": {"type": "object", "additionalProperties": True},
    }
    db_session.flush()

    sync_database_benchmark_resources(db_session, commit=False)
    first = deepcopy(database_plugin.runtime_meta)
    sync_database_benchmark_resources(db_session, commit=False)
    second = deepcopy(database_plugin.runtime_meta)

    assert first == second
    assert first["input_schema"] == database_inspection_input_schema(
        DatabaseScenarioCatalog.from_root(PROJECT_ROOT).ids()
    )
    assert onboarding_plugin.runtime_meta == onboarding_before
