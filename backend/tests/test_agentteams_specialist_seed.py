"""Seed and Tool regressions for the four onboarding specialists."""

from types import SimpleNamespace

from app import models, seed
from app.services.adapters import run_adapter
from app.services.agentteams_runtime_config import (
    demo_employee_ref_input_schema,
    expense_payment_input_schema,
    expense_ref_input_schema,
    role_change_activation_input_schema,
    role_change_employee_ref_input_schema,
)
from app.services.agentteams_hierarchy import TeamHierarchy


SPECIALIST_TO_PLUGIN = {
    "VE-HR-MATERIAL": "agentteams-onboarding-materials",
    "VE-HR-TRAINING": "agentteams-onboarding-training",
    "VE-IT-ACCOUNT": "agentteams-it-accounts",
    "VE-IT-DEVICE": "agentteams-it-devices",
}


def _persisted_hierarchy(db_session, team_id: str) -> TeamHierarchy:
    team = db_session.get(models.Team, team_id)
    members = db_session.query(models.TeamMember).filter_by(team_id=team_id).all()
    parent_by_member = {
        member.employee_id: member.reports_to_employee_id for member in members
    }
    relationships = {
        member.employee_id: {
            "role": member.role,
            "reports_to": [member.reports_to_employee_id] if member.reports_to_employee_id else [],
            "direct_reports": sorted(
                employee_id
                for employee_id, parent_id in parent_by_member.items()
                if parent_id == member.employee_id
            ),
        }
        for member in members
    }
    return TeamHierarchy.from_relationships(
        manager_id=team.leader_employee_id,
        member_ids=set(parent_by_member),
        relationships=relationships,
    )


def test_managed_teams_seed_real_manager_leader_worker_hierarchies(db_session):
    onboarding = _persisted_hierarchy(db_session, "TEAM-ONBOARD")
    assert onboarding.direct_reports_of("VE-0001") == (
        "VE-0002", "VE-0003", "VE-0005", "VE-FIN-LEAD",
    )
    assert onboarding.direct_reports_of("VE-0002") == (
        "VE-HR-MATERIAL", "VE-HR-TRAINING",
    )
    assert onboarding.direct_reports_of("VE-0003") == (
        "VE-IT-ACCOUNT", "VE-IT-DEVICE",
    )
    assert onboarding.direct_reports_of("VE-0005") == ("VE-AUDIT-REVIEW",)
    assert onboarding.direct_reports_of("VE-FIN-LEAD") == (
        "VE-FIN-BUDGET", "VE-FIN-INVOICE", "VE-FIN-PAYMENT",
    )

    database = _persisted_hierarchy(db_session, "TEAM-DB-BENCH")
    assert database.direct_reports_of("VE-0001") == (
        "DB-OPS-LEAD", "DB-PERF-LEAD",
    )
    assert database.direct_reports_of("DB-OPS-LEAD") == (
        "DB-LOCK-01", "DB-VACUUM-01", "DB-WRITE-01",
    )
    assert database.direct_reports_of("DB-PERF-LEAD") == (
        "DB-INDEX-01", "DB-QUERY-01",
    )


def _tool(plugin_id: str, *, schema: dict | None = None) -> dict:
    return {
        "name": f"test.{plugin_id}",
        "plugin_id": plugin_id,
        "action": "execute",
        "required_capability": "test.capability",
        "input_schema": schema or demo_employee_ref_input_schema(),
    }


def _strict_onboarding_runtime():
    role_change_schema = role_change_employee_ref_input_schema()
    activation_schema = role_change_activation_input_schema()
    roles = {
        "manager": ("VE-0001", []),
        "hr": (
            "VE-0002",
            [],
        ),
        "it": (
            "VE-0003",
            [],
        ),
        "audit": (
            "VE-0005",
            [],
        ),
        "hr_material": (
            "VE-HR-MATERIAL",
            [_tool("agentteams-onboarding-materials"), _tool("agentteams-role-change-hr", schema=role_change_schema)],
        ),
        "hr_training": (
            "VE-HR-TRAINING",
            [_tool("agentteams-onboarding-training"), _tool("agentteams-role-change-hr", schema=role_change_schema)],
        ),
        "it_account": (
            "VE-IT-ACCOUNT",
            [_tool("agentteams-it-accounts"), _tool("agentteams-role-change-it-plan", schema=role_change_schema)],
        ),
        "it_device": (
            "VE-IT-DEVICE",
            [
                _tool("agentteams-it-devices"),
                _tool("agentteams-role-change-it-plan", schema=role_change_schema),
                _tool("agentteams-role-change-it-activate", schema=activation_schema),
            ],
        ),
        "audit_review": (
            "VE-AUDIT-REVIEW",
            [
                _tool("agentteams-onboarding-evidence"),
                _tool("agentteams-role-change-audit", schema=role_change_schema),
                _tool("agentteams-expense-audit", schema=expense_ref_input_schema()),
            ],
        ),
        "finance": ("VE-FIN-LEAD", []),
        "finance_invoice": (
            "VE-FIN-INVOICE",
            [_tool("agentteams-expense-claim", schema=expense_ref_input_schema())],
        ),
        "finance_budget": (
            "VE-FIN-BUDGET",
            [_tool("agentteams-expense-budget", schema=expense_ref_input_schema())],
        ),
        "finance_payment": (
            "VE-FIN-PAYMENT",
            [_tool("agentteams-expense-payment", schema=expense_payment_input_schema())],
        ),
    }
    agents = {
        role: SimpleNamespace(
            agent_id=agent_id,
            mxid=f"@{role}:test",
            safe_tool_catalog=tools,
        )
        for role, (agent_id, tools) in roles.items()
    }
    relationships = {
        "VE-0001": {"role": "Manager", "reports_to": [], "direct_reports": ["VE-0002", "VE-0003", "VE-0005", "VE-FIN-LEAD"]},
        "VE-0002": {"role": "HR负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-HR-MATERIAL", "VE-HR-TRAINING"]},
        "VE-0003": {"role": "IT负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-IT-ACCOUNT", "VE-IT-DEVICE"]},
        "VE-0005": {"role": "审计负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-AUDIT-REVIEW"]},
        "VE-HR-MATERIAL": {"role": "HR材料专员", "reports_to": ["VE-0002"], "direct_reports": []},
        "VE-HR-TRAINING": {"role": "HR培训专员", "reports_to": ["VE-0002"], "direct_reports": []},
        "VE-IT-ACCOUNT": {"role": "IT账号专员", "reports_to": ["VE-0003"], "direct_reports": []},
        "VE-IT-DEVICE": {"role": "IT设备专员", "reports_to": ["VE-0003"], "direct_reports": []},
        "VE-AUDIT-REVIEW": {"role": "审计专员", "reports_to": ["VE-0005"], "direct_reports": []},
        "VE-FIN-LEAD": {"role": "财务负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-FIN-INVOICE", "VE-FIN-BUDGET", "VE-FIN-PAYMENT"]},
        "VE-FIN-INVOICE": {"role": "发票核验专员", "reports_to": ["VE-FIN-LEAD"], "direct_reports": []},
        "VE-FIN-BUDGET": {"role": "预算核验专员", "reports_to": ["VE-FIN-LEAD"], "direct_reports": []},
        "VE-FIN-PAYMENT": {"role": "付款制单专员", "reports_to": ["VE-FIN-LEAD"], "direct_reports": []},
    }
    return SimpleNamespace(team_id="TEAM-ONBOARD", agents=agents, team_relationships=relationships)


def test_onboarding_specialists_have_isolated_seeded_tools_and_permissions(db_session):
    employees = {
        employee.employee_no: employee
        for employee in db_session.query(models.DigitalEmployee).filter(
            models.DigitalEmployee.employee_no.in_(SPECIALIST_TO_PLUGIN)
        )
    }
    assert set(employees) == set(SPECIALIST_TO_PLUGIN)
    assert {employee.department for employee in employees.values()} == {
        "人力资源部", "IT 服务部",
    }
    assert all(employee.runtime_type == "agentteams" for employee in employees.values())

    members = {
        member.employee_id
        for member in db_session.query(models.TeamMember).filter_by(team_id="TEAM-ONBOARD")
    }
    assert set(SPECIALIST_TO_PLUGIN) <= members

    personal_allows = {
        (grant.employee_id, grant.plugin_id)
        for grant in db_session.query(models.EmployeePluginGrant).filter_by(
            action="execute", decision_mode="allow"
        )
    }
    assert {
        (employee_id, plugin_id)
        for employee_id, plugin_id in SPECIALIST_TO_PLUGIN.items()
    } <= personal_allows
    assert ("VE-0002", "agentteams-onboarding-employee") in personal_allows
    assert ("VE-0003", "agentteams-it-readiness") in personal_allows

    team_allows = {
        grant.plugin_id
        for grant in db_session.query(models.TeamPluginGrant).filter_by(
            team_id="TEAM-ONBOARD", action="execute", decision_mode="allow"
        )
    }
    assert set(SPECIALIST_TO_PLUGIN.values()) <= team_allows
    assert {"agentteams-onboarding-employee", "agentteams-it-readiness"} <= team_allows

    zhang_san_allows = {
        grant.plugin_id
        for grant in db_session.query(models.EmployeePluginGrant).filter_by(
            employee_id="DT-E10281", action="execute", decision_mode="allow"
        )
    }
    assert set(SPECIALIST_TO_PLUGIN.values()) <= zhang_san_allows


def test_expense_workers_have_only_their_seeded_business_tools(db_session):
    expected = {
        "VE-FIN-INVOICE": "agentteams-expense-claim",
        "VE-FIN-BUDGET": "agentteams-expense-budget",
        "VE-FIN-PAYMENT": "agentteams-expense-payment",
        "VE-AUDIT-REVIEW": "agentteams-expense-audit",
    }
    personal_allows = {
        (grant.employee_id, grant.plugin_id)
        for grant in db_session.query(models.EmployeePluginGrant).filter(
            models.EmployeePluginGrant.employee_id.in_(expected),
            models.EmployeePluginGrant.action == "execute",
            models.EmployeePluginGrant.decision_mode == "allow",
        )
    }

    assert {(employee_id, plugin_id) for employee_id, plugin_id in expected.items()} <= personal_allows
    assert db_session.get(models.Team, "TEAM-ONBOARD").name == "AgentTeams 业务协作团队"


def test_onboarding_specialist_adapters_return_only_their_domain_evidence(db_session):
    expected = {
        "agentteams-onboarding-materials": ("materials", {"HR-MAT-A-001", "HR-MAT-A-002"}),
        "agentteams-onboarding-training": ("training", {"HR-TRN-A-001", "HR-TRN-A-002"}),
        "agentteams-it-accounts": ("accounts", {"IT-ACC-A-001", "IT-ACC-A-002"}),
        "agentteams-it-devices": ("devices", {"IT-DEV-A-001", "IT-DEV-A-002"}),
    }

    results = {}
    for plugin_id, (domain_key, evidence_ids) in expected.items():
        plugin = db_session.get(models.Plugin, plugin_id)
        assert plugin is not None
        result = run_adapter(plugin, {"employee_ref": "新员工A"})
        assert set(result) == {
            "source", "employee_ref", "readiness", "evidence_ids", domain_key,
        }
        assert set(result["evidence_ids"]) == evidence_ids
        assert {item["evidence_id"] for item in result[domain_key]} == evidence_ids
        results[plugin_id] = result

    assert len({tuple(result["evidence_ids"]) for result in results.values()}) == 4


def test_strict_onboarding_sync_repairs_existing_database_idempotently(db_session):
    runtime = _strict_onboarding_runtime()
    specialist_ids = set(SPECIALIST_TO_PLUGIN)
    specialist_plugin_ids = set(SPECIALIST_TO_PLUGIN.values())

    db_session.query(models.TeamMember).filter(
        models.TeamMember.team_id == "TEAM-ONBOARD",
        models.TeamMember.employee_id.in_(specialist_ids),
    ).delete(synchronize_session=False)
    db_session.query(models.DigitalEmployee).filter(
        models.DigitalEmployee.employee_no.in_(specialist_ids)
    ).delete(synchronize_session=False)
    db_session.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.plugin_id.in_(specialist_plugin_ids)
    ).delete(synchronize_session=False)
    db_session.query(models.TeamPluginGrant).filter(
        models.TeamPluginGrant.team_id == "TEAM-ONBOARD",
        models.TeamPluginGrant.plugin_id.in_(specialist_plugin_ids),
    ).delete(synchronize_session=False)
    db_session.query(models.Plugin).filter(
        models.Plugin.id.in_(specialist_plugin_ids)
    ).delete(synchronize_session=False)
    db_session.commit()

    seed.sync_strict_runtime_state(db_session, runtime)
    first_counts = (
        db_session.query(models.DigitalEmployee).count(),
        db_session.query(models.TeamMember).count(),
        db_session.query(models.Plugin).count(),
        db_session.query(models.EmployeePluginGrant).count(),
        db_session.query(models.TeamPluginGrant).count(),
    )
    seed.sync_strict_runtime_state(db_session, runtime)

    assert (
        db_session.query(models.DigitalEmployee).count(),
        db_session.query(models.TeamMember).count(),
        db_session.query(models.Plugin).count(),
        db_session.query(models.EmployeePluginGrant).count(),
        db_session.query(models.TeamPluginGrant).count(),
    ) == first_counts
    assert {
        employee.employee_no
        for employee in db_session.query(models.DigitalEmployee).filter(
            models.DigitalEmployee.employee_no.in_(specialist_ids)
        )
    } == specialist_ids
    assert {
        grant.plugin_id
        for grant in db_session.query(models.TeamPluginGrant).filter_by(
            team_id="TEAM-ONBOARD", action="execute", decision_mode="allow"
        )
    } >= specialist_plugin_ids
    assert {
        grant.plugin_id
        for grant in db_session.query(models.EmployeePluginGrant).filter_by(
            employee_id="DT-E10281", action="execute", decision_mode="allow"
        )
    } >= specialist_plugin_ids
