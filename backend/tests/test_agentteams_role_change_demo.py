"""岗位调整场景必须复用真实 AgentTeams、Gateway 与审计链路。"""

from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

from app import models
from app.services.agentteams_local_scenario import (
    LOCAL_DEMO_INSTRUCTION,
    LOCAL_DEMO_TITLE,
)
from app.services.agentteams_skills import ProjectSkillRegistry
from app.services.agentteams_role_change_scenario import (
    ROLE_CHANGE_FIRST_ROUND_CAPABILITIES,
    ROLE_CHANGE_INSTRUCTION,
    ROLE_CHANGE_SECOND_ROUND_CAPABILITIES,
    ROLE_CHANGE_TITLE,
)
from app.services.gateway import invoke_plugin


ROLE_CHANGE_PLUGINS = {
    "VE-HR-MATERIAL": "agentteams-role-change-hr",
    "VE-IT-ACCOUNT": "agentteams-role-change-it-plan",
    "VE-AUDIT-REVIEW": "agentteams-role-change-audit",
}


@pytest.fixture()
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _invoke(db_session, employee_id: str, plugin_id: str, params: dict):
    return invoke_plugin(
        db_session,
        employee_id=employee_id,
        plugin_id=plugin_id,
        action="execute",
        params=params,
        trace_id=f"ROLE-CHANGE-{employee_id}-{plugin_id}",
    )


def test_local_and_matrix_role_change_rules_share_one_contract():
    """A local-only title or capability set would make the two runtimes drift again."""
    assert LOCAL_DEMO_TITLE == ROLE_CHANGE_TITLE
    assert LOCAL_DEMO_INSTRUCTION == ROLE_CHANGE_INSTRUCTION
    assert ROLE_CHANGE_FIRST_ROUND_CAPABILITIES == {
        "hr.role_change.coordinate",
        "it.role_change.plan.coordinate",
    }
    assert ROLE_CHANGE_SECOND_ROUND_CAPABILITIES == {
        "audit.role_change.coordinate",
        "it.role_change.activate.coordinate",
    }


@pytest.mark.parametrize(
    ("employee_id", "plugin_id", "expected_key", "expected_evidence"),
    [
        ("VE-HR-MATERIAL", "agentteams-role-change-hr", "role_change", "HR-RC-001"),
        ("VE-IT-ACCOUNT", "agentteams-role-change-it-plan", "permission_plan", "IT-RC-001"),
        ("VE-AUDIT-REVIEW", "agentteams-role-change-audit", "review", "AUD-RC-001"),
    ],
)
def test_role_change_read_tools_return_bounded_demo_evidence_via_gateway(
    db_session, employee_id, plugin_id, expected_key, expected_evidence,
):
    result = _invoke(db_session, employee_id, plugin_id, {"employee_ref": "张明"})

    assert result["ok"] is True
    assert result["data"]["source"] == "demo"
    assert result["data"]["employee_ref"] == "张明"
    assert expected_evidence in result["data"]["evidence_ids"]
    assert expected_key in result["data"]
    audit = db_session.get(models.AuditEvent, result["audit_ids"][0])
    assert audit is not None
    assert audit.plugin_id == plugin_id
    assert audit.decision == "allow"


def test_role_change_activation_requires_the_audit_approval_reference(db_session):
    with pytest.raises(HTTPException) as error:
        _invoke(
            db_session,
            "VE-IT-DEVICE",
            "agentteams-role-change-it-activate",
            {"employee_ref": "张明", "approval_ref": "伪造编号"},
        )

    assert error.value.status_code == 400
    denied = db_session.query(models.AuditEvent).order_by(models.AuditEvent.id.desc()).first()
    assert denied.decision == "deny"

    result = _invoke(
        db_session,
        "VE-IT-DEVICE",
        "agentteams-role-change-it-activate",
        {"employee_ref": "张明", "approval_ref": "AUD-RC-001"},
    )
    assert result["data"]["activation"]["status"] == "completed"
    assert result["data"]["approval_ref"] == "AUD-RC-001"


def test_role_change_plugins_are_granted_only_to_the_owning_agents(db_session):
    expected = {
        ("VE-HR-MATERIAL", "agentteams-role-change-hr"),
        ("VE-HR-TRAINING", "agentteams-role-change-hr"),
        ("VE-IT-ACCOUNT", "agentteams-role-change-it-plan"),
        ("VE-IT-DEVICE", "agentteams-role-change-it-plan"),
        ("VE-IT-DEVICE", "agentteams-role-change-it-activate"),
        ("VE-AUDIT-REVIEW", "agentteams-role-change-audit"),
    }
    grants = db_session.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.employee_id.in_({employee for employee, _ in expected}),
        models.EmployeePluginGrant.plugin_id.in_({plugin for _, plugin in expected})
    ).all()
    assert {
        (grant.employee_id, grant.plugin_id)
        for grant in grants
        if grant.action == "execute" and grant.decision_mode == "allow"
    } == expected


def test_role_change_skills_define_manager_second_round_and_serial_approval(repo_root):
    registry = ProjectSkillRegistry.from_root(repo_root)
    scenario, hr, it, audit = registry.load([
        "agentteams-role-change-readiness",
        "hr-role-change-assistant",
        "it-role-change-assistant",
        "audit-role-change-review",
    ])

    assert all(term in scenario.content for term in ("第一轮", "并行", "Manager", "第二轮", "depends_on"))
    assert "HR" in scenario.content and "IT" in scenario.content and "审计" in scenario.content
    assert "HR-RC-001" in hr.content
    assert "AUD-RC-001" in audit.content
    assert "approval_ref" in it.content
    assert "激活" in it.content


def test_matrix_bootstrap_registers_both_scenarios_and_all_role_change_tools():
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from scripts.bootstrap_agentteams_matrix import ROLE_IDENTITIES, TEAM_CATALOG

    assert ROLE_IDENTITIES["manager"]["skills"] == [
        "agentteams-onboarding-readiness",
        "agentteams-role-change-readiness",
    ]
    assert ROLE_IDENTITIES["hr"]["tools"] == []
    assert ROLE_IDENTITIES["it"]["tools"] == []
    assert ROLE_IDENTITIES["audit"]["tools"] == []
    assert {tool["plugin_id"] for tool in ROLE_IDENTITIES["hr_material"]["tools"]} >= {
        "agentteams-role-change-hr",
    }
    assert {tool["plugin_id"] for tool in ROLE_IDENTITIES["it_device"]["tools"]} >= {
        "agentteams-role-change-it-plan",
        "agentteams-role-change-it-activate",
    }
    assert {tool["plugin_id"] for tool in ROLE_IDENTITIES["audit_review"]["tools"]} >= {
        "agentteams-role-change-audit",
    }
    assert "it.role_change.activate.coordinate" in TEAM_CATALOG["VE-0003"]["capabilities"]
