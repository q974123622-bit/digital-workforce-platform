import pytest
from fastapi import HTTPException

from app import models
from app.services import identity
from app.services.gateway import invoke_plugin
from app.services.policy import ResourceRef, evaluate
from app.services.agentteams_collaboration import (
    AgentTeamsCollaborationService,
    matrix_user_id_for_runtime,
)
from app.services.agentteams_protocol import AgentProtocolMessage
from app import seed


def _message(run_id, task_id, sender, message_type, key, payload, parent_task_id=None):
    return AgentProtocolMessage(
        version="1.0",
        message_id=f"MSG-{key}",
        type=message_type,
        root_task_id=run_id,
        task_id=task_id,
        parent_task_id=parent_task_id,
        trace_id=run_id,
        sender=sender,
        receiver="platform",
        timestamp="2026-09-11T09:00:00+08:00",
        idempotency_key=f"{run_id}:{key}",
        payload=payload,
    )


def test_team_plugin_grant_is_a_persisted_permission_layer(db_session):
    """Catches collapsing team permissions back into employee-only grants."""
    grant_model = getattr(models, "TeamPluginGrant", None)

    assert grant_model is not None
    grant = grant_model(
        team_id="TEAM-PERSISTENCE-TEST",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
        decision_mode="allow",
        grant_source="test",
    )
    db_session.add(grant)
    db_session.commit()

    persisted = db_session.query(grant_model).filter_by(
        team_id="TEAM-PERSISTENCE-TEST",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
    ).one()
    assert persisted.decision_mode == "allow"


def test_team_and_digital_twin_allows_are_both_required(db_session):
    """Catches collaboration authorizing against the VE worker instead of the selected twin."""
    db_session.add(models.EmployeePluginGrant(
        employee_id="DT-E10281",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
        decision_mode="allow",
        grant_source="test",
    ))
    db_session.commit()

    executor = identity.resolve_identity(db_session, "VE-0002")
    permission_subject = identity.resolve_identity(db_session, "DT-E10281")
    result = evaluate(
        db_session,
        executor,
        ResourceRef(type="workflow", id="agentteams-onboarding-employee", data_level="L1"),
        "execute",
        context={"team_id": "TEAM-ONBOARD"},
        permission_subject=permission_subject,
    )

    assert result.decision == "allow"
    assert "团队授权" in result.reason


def test_team_deny_cannot_be_reopened_by_digital_twin_allow(db_session):
    """Catches a personal allow bypassing an explicit team deny."""
    db_session.add(models.TeamPluginGrant(
        team_id="TEAM-ONBOARD", plugin_id="knowledge-l1", action="read",
        decision_mode="deny", grant_source="test",
    ))
    db_session.commit()

    db_session.add(models.EmployeePluginGrant(
        employee_id="DT-E10281", plugin_id="knowledge-l1", action="read",
        decision_mode="allow", grant_source="test",
    ))
    db_session.commit()

    executor = identity.resolve_identity(db_session, "VE-0001")
    permission_subject = identity.resolve_identity(db_session, "DT-E10281")
    result = evaluate(
        db_session,
        executor,
        ResourceRef(type="knowledge", id="knowledge-l1", data_level="L1"),
        "read",
        context={"team_id": "TEAM-ONBOARD"},
        permission_subject=permission_subject,
    )

    assert result.decision == "deny"
    assert "团队授权" in result.reason


def test_digital_twin_personal_permission_does_not_require_team_membership(db_session):
    """Catches incorrectly treating the human twin as one of the team's VE workers."""
    db_session.add(models.EmployeePluginGrant(
        employee_id="DT-E10281",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
        decision_mode="allow",
        grant_source="test",
    ))
    db_session.commit()

    executor = identity.resolve_identity(db_session, "VE-0002")
    permission_subject = identity.resolve_identity(db_session, "DT-E10281")
    assert db_session.query(models.TeamMember).filter_by(
        team_id="TEAM-ONBOARD", employee_id="DT-E10281"
    ).count() == 0
    result = evaluate(
        db_session,
        executor,
        ResourceRef(type="workflow", id="agentteams-onboarding-employee", data_level="L1"),
        "execute",
        context={"team_id": "TEAM-ONBOARD"},
        permission_subject=permission_subject,
    )

    assert result.decision == "allow"


def test_gateway_authorizes_with_selected_digital_twin_and_audits_both_identities(db_session):
    """Catches Gateway using the VE worker grant as the collaboration personal layer."""
    db_session.add(models.EmployeePluginGrant(
        employee_id="DT-E10281",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
        decision_mode="allow",
        grant_source="test",
    ))
    db_session.commit()

    result = invoke_plugin(
        db_session,
        employee_id="VE-0002",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
        params={"employee_ref": "新员工A"},
        trace_id="THREE-LAYER-TWIN",
        execution_context={"team_id": "TEAM-ONBOARD", "owner_human_no": "E10281"},
    )

    assert result["ok"] is True
    audit = db_session.query(models.AuditEvent).filter_by(trace_id="THREE-LAYER-TWIN").one()
    assert audit.actor == "DT-E10281"
    assert audit.employee_id == "VE-0002"
    assert audit.team_id == "TEAM-ONBOARD"


def test_matrix_tool_execution_rechecks_team_permission_at_gateway(db_session):
    """Catches AgentTeams dropping its persisted team before the gateway recheck."""
    team_grant = db_session.query(models.TeamPluginGrant).filter_by(
        team_id="TEAM-ONBOARD", plugin_id="agentteams-onboarding-employee", action="execute"
    ).one()
    service = AgentTeamsCollaborationService(capability_catalog_source="database")
    run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title="权限复核",
        instruction="核对新员工A入职资料",
        material={},
        owner_human_no="E10281",
    )
    twin_grant = models.EmployeePluginGrant(
        employee_id="DT-E10281",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
        decision_mode="allow",
        grant_source="test",
    )
    db_session.add(twin_grant)
    db_session.flush()
    manager = db_session.get(models.DigitalEmployee, "VE-0001")
    worker = db_session.get(models.DigitalEmployee, "VE-0002")
    service.apply_message(
        db_session,
        matrix_sender=matrix_user_id_for_runtime(manager.runtime_ref),
        message=_message(
            run.id,
            run.id,
            "VE-0001",
            "task.delegate",
            "delegate",
            {"subtasks": [{
                "client_task_key": "hr",
                "title": "HR核查",
                "instruction": "读取新员工A入职资料",
                "assignee_agent_id": "VE-0002",
                "depends_on": [],
                "required": True,
                "required_capability": "agentteams-onboarding-employee",
            }]},
        ),
    )
    node = db_session.query(models.AgentTaskNode).filter_by(
        root_task_id=run.id, client_task_key="hr"
    ).one()
    service.apply_message(
        db_session,
        matrix_sender=matrix_user_id_for_runtime(worker.runtime_ref),
        message=_message(run.id, node.id, "VE-0002", "task.accepted", "accepted", {}, run.id),
    )
    twin_grant.decision_mode = "deny"
    db_session.flush()

    events = service.apply_message(
        db_session,
        matrix_sender=matrix_user_id_for_runtime(worker.runtime_ref),
        message=_message(
            run.id,
            node.id,
            "VE-0002",
            "tool.request",
            "tool",
            {
                "plugin_id": "agentteams-onboarding-employee",
                "action": "execute",
                "params": {"employee_ref": "新员工A"},
            },
            run.id,
        ),
    )

    assert [event.event_type for event in events] == ["tool.denied"]
    audit = db_session.query(models.AuditEvent).filter_by(trace_id=node.trace_id).one()
    assert audit.actor == "DT-E10281"
    assert audit.employee_id == "VE-0002"
    assert audit.team_id == "TEAM-ONBOARD"


def test_seed_installs_team_permissions_for_both_collaboration_scenarios(db_session):
    """Catches a new database starting with the model but no usable team grants."""
    seeded = {
        (grant.team_id, grant.plugin_id, grant.action, grant.decision_mode)
        for grant in db_session.query(models.TeamPluginGrant).all()
    }

    assert {
        ("TEAM-ONBOARD", "agentteams-onboarding-employee", "execute", "allow"),
        ("TEAM-ONBOARD", "agentteams-it-readiness", "execute", "allow"),
        ("TEAM-ONBOARD", "agentteams-onboarding-evidence", "execute", "allow"),
        ("TEAM-ONBOARD", "agentteams-role-change-hr", "execute", "allow"),
        ("TEAM-ONBOARD", "agentteams-role-change-it-plan", "execute", "allow"),
        ("TEAM-ONBOARD", "agentteams-role-change-audit", "execute", "allow"),
        ("TEAM-ONBOARD", "agentteams-role-change-it-activate", "execute", "allow"),
        ("TEAM-ONBOARD", "agentteams-expense-claim", "execute", "allow"),
        ("TEAM-ONBOARD", "agentteams-expense-budget", "execute", "allow"),
        ("TEAM-ONBOARD", "agentteams-expense-audit", "execute", "allow"),
        ("TEAM-ONBOARD", "agentteams-expense-payment", "execute", "allow"),
        ("TEAM-DB-BENCH", "agentteams-database-inspector", "read", "allow"),
    } <= seeded


def test_seed_installs_personal_collaboration_permissions_for_each_digital_twin(db_session):
    """Catches a clean database where teams exist but selected twins cannot use them."""
    seeded = {
        (grant.employee_id, grant.plugin_id, grant.action, grant.decision_mode)
        for grant in db_session.query(models.EmployeePluginGrant).all()
    }

    assert {
        ("DT-E10281", "agentteams-onboarding-employee", "execute", "allow"),
        ("DT-E10281", "agentteams-database-inspector", "read", "allow"),
        ("DT-E20999", "agentteams-onboarding-employee", "execute", "allow"),
        ("DT-E20999", "agentteams-database-inspector", "read", "allow"),
    } <= seeded


def test_manager_catalog_exposes_only_team_twin_and_agent_capability_intersection(db_session):
    """Catches Manager seeing a team Tool that the selected twin cannot use."""
    db_session.add(models.EmployeePluginGrant(
        employee_id="DT-E10281",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
        decision_mode="allow",
        grant_source="test",
    ))
    db_session.add(models.EmployeePluginGrant(
        employee_id="DT-E10281",
        plugin_id="agentteams-it-readiness",
        action="execute",
        decision_mode="deny",
        grant_source="manual",
    ))
    db_session.commit()
    catalog = AgentTeamsCollaborationService(
        capability_catalog_source="database"
    )._team_capability_catalog(db_session, "TEAM-ONBOARD", owner_human_no="E10281")
    by_employee = {item["agent_id"]: set(item["capabilities"]) for item in catalog}

    assert "agentteams-onboarding-employee" in by_employee["VE-0002"]
    assert "agentteams-it-readiness" not in by_employee["VE-0003"]
    assert "expense-claim" not in by_employee["VE-0002"]
    assert "agentteams-database-inspector" not in by_employee["VE-0001"]


def test_team_permission_sync_repairs_existing_database_without_reset(db_session):
    """Catches upgrades that define seed grants but never install them into an existing DB."""
    synchronizer = getattr(seed, "sync_team_plugin_grants", None)
    assert synchronizer is not None
    removed = db_session.query(models.TeamPluginGrant).filter_by(
        team_id="TEAM-ONBOARD", plugin_id="agentteams-onboarding-employee", action="execute"
    ).one()
    db_session.delete(removed)
    db_session.commit()

    synchronizer(db_session)
    first_count = db_session.query(models.TeamPluginGrant).count()
    synchronizer(db_session)

    repaired = db_session.query(models.TeamPluginGrant).filter_by(
        team_id="TEAM-ONBOARD", plugin_id="agentteams-onboarding-employee", action="execute"
    ).one()
    assert repaired.decision_mode == "allow"
    assert db_session.query(models.TeamPluginGrant).count() == first_count


def test_twin_permission_sync_repairs_existing_database_without_reset(db_session):
    """Catches an existing database never receiving the new digital-twin layer."""
    synchronizer = getattr(seed, "sync_collaboration_twin_plugin_grants", None)
    assert synchronizer is not None
    removed = db_session.query(models.EmployeePluginGrant).filter_by(
        employee_id="DT-E10281",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
    ).one()
    db_session.delete(removed)
    db_session.commit()

    synchronizer(db_session)
    first_count = db_session.query(models.EmployeePluginGrant).count()
    synchronizer(db_session)

    repaired = db_session.query(models.EmployeePluginGrant).filter_by(
        employee_id="DT-E10281",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
    ).one()
    assert repaired.decision_mode == "allow"
    assert repaired.grant_source == "seed"
    assert db_session.query(models.EmployeePluginGrant).count() == first_count


def test_twin_permission_sync_preserves_manual_deny(db_session):
    """Catches automatic synchronization overwriting a deliberate personal restriction."""
    seeded = db_session.query(models.EmployeePluginGrant).filter_by(
        employee_id="DT-E10281",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
        grant_source="seed",
    ).one()
    db_session.delete(seeded)
    db_session.add(models.EmployeePluginGrant(
        employee_id="DT-E10281",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
        decision_mode="deny",
        grant_source="manual",
    ))
    db_session.commit()

    seed.sync_collaboration_twin_plugin_grants(db_session)

    grants = db_session.query(models.EmployeePluginGrant).filter_by(
        employee_id="DT-E10281",
        plugin_id="agentteams-onboarding-employee",
        action="execute",
    ).all()
    assert [(grant.decision_mode, grant.grant_source) for grant in grants] == [
        ("deny", "manual")
    ]


def test_database_resource_sync_includes_its_team_permission(db_session):
    """Catches benchmark repair restoring workers and Tool but omitting the team layer."""
    grant = db_session.query(models.TeamPluginGrant).filter_by(
        team_id="TEAM-DB-BENCH", plugin_id="agentteams-database-inspector", action="read"
    ).one()
    twin_grant = db_session.query(models.EmployeePluginGrant).filter_by(
        employee_id="DT-E10281",
        plugin_id="agentteams-database-inspector",
        action="read",
        grant_source="seed",
    ).one()
    db_session.delete(grant)
    db_session.delete(twin_grant)
    db_session.commit()

    seed.sync_database_benchmark_resources(db_session)

    repaired = db_session.query(models.TeamPluginGrant).filter_by(
        team_id="TEAM-DB-BENCH", plugin_id="agentteams-database-inspector", action="read"
    ).one()
    assert repaired.decision_mode == "allow"
    repaired_twin = db_session.query(models.EmployeePluginGrant).filter_by(
        employee_id="DT-E10281",
        plugin_id="agentteams-database-inspector",
        action="read",
        grant_source="seed",
    ).one()
    assert repaired_twin.decision_mode == "allow"
