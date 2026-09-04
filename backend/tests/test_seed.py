"""Seed data regressions for governed AgentTeams demo tools."""

from app import models
from app.seed import load_seed


def test_agentteams_onboarding_demo_seed_registers_domain_workers_and_plugins(db_session):
    data = load_seed()
    plugin_ids = {row["id"] for row in data["plugins"]}
    assert {
        "agentteams-onboarding-employee",
        "agentteams-it-readiness",
        "agentteams-onboarding-evidence",
    } <= plugin_ids

    audit_worker = db_session.get(models.DigitalEmployee, "VE-0005")
    assert audit_worker is not None
    assert audit_worker.runtime_ref == "audit-worker"
    members = {
        member.employee_id
        for member in db_session.query(models.TeamMember).filter_by(team_id="TEAM-ONBOARD")
    }
    assert "VE-0005" in members


def test_agentteams_onboarding_demo_seed_grants_only_the_new_domain_plugins(db_session):
    expected = {
        "VE-0002": "agentteams-onboarding-employee",
        "VE-0003": "agentteams-it-readiness",
        "VE-0005": "agentteams-onboarding-evidence",
    }
    grants = db_session.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.plugin_id.in_(set(expected.values()))
    ).all()

    assert {(grant.employee_id, grant.plugin_id, grant.action, grant.decision_mode) for grant in grants} == {
        (employee_id, plugin_id, "execute", "allow")
        for employee_id, plugin_id in expected.items()
    }
