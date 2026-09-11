from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


def test_strict_runtime_accepts_the_bounded_database_inspector_schema():
    from app.services.agentteams_runtime_config import _tool_input_schema

    schema = {
        "type": "object",
        "required": ["scenario_id", "signal", "filters", "limit"],
        "properties": {
            "scenario_id": {"type": "string", "enum": ["bulk_writes", "lock_contention"]},
            "signal": {"type": "string", "enum": ["statements", "locks"]},
            "filters": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "maxProperties": 4,
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
    }

    assert _tool_input_schema(schema) == schema


def test_database_runtime_shape_syncs_seeded_team_tools_and_bindings(db_session, tmp_path):
    from app import models
    from app.seed import sync_strict_runtime_state
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    path = tmp_path / "database-runtime.json"
    path.write_text(json.dumps(_database_runtime_config()), encoding="utf-8")
    runtime = load_strict_runtime_config(path, environ={})

    db_session.query(models.TeamMember).filter_by(team_id="TEAM-DB-BENCH").delete()
    db_session.query(models.Team).filter_by(id="TEAM-DB-BENCH").delete()
    db_session.commit()

    sync_strict_runtime_state(db_session, runtime)

    team = db_session.get(models.Team, "TEAM-DB-BENCH")
    assert team is not None
    assert team.leader_employee_id == "VE-0001"
    assert {
        member.employee_id: member.role
        for member in db_session.query(models.TeamMember).filter_by(team_id="TEAM-DB-BENCH")
    } == {
        "VE-0001": "leader",
        "DB-WRITE-01": "worker",
        "DB-LOCK-01": "worker",
        "DB-VACUUM-01": "worker",
        "DB-INDEX-01": "worker",
        "DB-QUERY-01": "worker",
    }
    assert db_session.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.employee_id.in_({
            "VE-0001", "DB-WRITE-01", "DB-LOCK-01",
            "DB-VACUUM-01", "DB-INDEX-01", "DB-QUERY-01",
        }),
        models.EmployeePluginGrant.plugin_id == "agentteams-database-inspector",
        models.EmployeePluginGrant.decision_mode == "allow",
    ).count() == 6


def test_sync_strict_runtime_creates_team_and_exact_matrix_bindings_from_seed(db_session, tmp_path):
    """A strict startup must be self-contained, without a manual database edit."""
    from app import models
    from app.seed import sync_strict_runtime_bindings
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(_runtime_config()), encoding="utf-8")
    runtime = load_strict_runtime_config(path, environ={})

    for agent_id in (identity.agent_id for identity in runtime.agents.values()):
        db_session.query(models.TeamMember).filter_by(employee_id=agent_id).delete()
        db_session.query(models.DigitalEmployee).filter_by(employee_no=agent_id).delete()
    db_session.query(models.TeamMember).filter_by(team_id=runtime.team_id).delete()
    db_session.query(models.Team).filter_by(id=runtime.team_id).delete()
    db_session.commit()

    sync_strict_runtime_bindings(db_session, runtime)

    team = db_session.get(models.Team, runtime.team_id)
    assert team is not None
    assert team.leader_employee_id == runtime.agents["manager"].agent_id
    members = {
        member.employee_id: member.role
        for member in db_session.query(models.TeamMember).filter_by(team_id=runtime.team_id)
    }
    assert members == {
        runtime.agents["manager"].agent_id: "leader",
        runtime.agents["hr"].agent_id: "worker",
        runtime.agents["it"].agent_id: "worker",
        runtime.agents["audit"].agent_id: "worker",
    }
    for identity in runtime.agents.values():
        employee = db_session.get(models.DigitalEmployee, identity.agent_id)
        assert employee is not None
        assert employee.status == "active"
        assert employee.runtime_type == "agentteams"
        assert employee.runtime_ref == identity.mxid


def test_strict_tool_sync_repairs_only_referenced_demo_plugins_and_grants(db_session, tmp_path):
    """A populated strict database must be repaired additively without resetting evidence."""
    from app import models
    from app.seed import sync_strict_runtime_demo_tools
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(_runtime_config()), encoding="utf-8")
    runtime = load_strict_runtime_config(path, environ={})
    demo_plugin_ids = {
        "agentteams-onboarding-employee",
        "agentteams-it-readiness",
        "agentteams-onboarding-evidence",
        "agentteams-role-change-hr",
        "agentteams-role-change-it-plan",
        "agentteams-role-change-it-activate",
        "agentteams-role-change-audit",
    }
    db_session.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.plugin_id.in_(demo_plugin_ids)
    ).delete(synchronize_session=False)
    db_session.query(models.Plugin).filter(
        models.Plugin.id.in_(demo_plugin_ids)
    ).delete(synchronize_session=False)
    db_session.add(models.Plugin(
        id="unrelated-preserved-plugin",
        name="Unrelated",
        type="mcp",
        endpoint_ref="mock://unrelated",
        data_level="L1",
        status="active",
        description="must survive",
        runtime_meta={},
    ))
    db_session.add(models.EmployeePluginGrant(
        employee_id="VE-0001",
        plugin_id="unrelated-preserved-plugin",
        action="read",
        decision_mode="allow",
        grant_source="manual",
    ))
    db_session.add(models.TaskRun(
        id="unrelated-preserved-task",
        team_id="TEAM-ONBOARD",
        trace_id="trace-preserved",
        request="preserve me",
        status="done",
        subtasks=[],
        summary="preserved",
        source="agentteams",
    ))
    db_session.add(models.AuditEvent(
        trace_id="trace-preserved",
        actor="VE-0001",
        action="unrelated.read",
        decision="allow",
        result_summary="preserved",
    ))
    db_session.commit()

    sync_strict_runtime_demo_tools(db_session, runtime)
    sync_strict_runtime_demo_tools(db_session, runtime)

    plugins = {
        plugin.id: plugin
        for plugin in db_session.query(models.Plugin).filter(models.Plugin.id.in_(demo_plugin_ids))
    }
    assert set(plugins) == demo_plugin_ids
    assert all(
        plugins[plugin_id].runtime_meta["input_schema"] == _employee_ref_schema()
        for plugin_id in {
            "agentteams-onboarding-employee", "agentteams-it-readiness", "agentteams-onboarding-evidence",
        }
    )
    assert all(
        plugins[plugin_id].runtime_meta["input_schema"] == _role_change_schema()
        for plugin_id in {
            "agentteams-role-change-hr", "agentteams-role-change-it-plan", "agentteams-role-change-audit",
        }
    )
    assert plugins["agentteams-role-change-it-activate"].runtime_meta["input_schema"] == _role_change_activation_schema()
    grants = db_session.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.plugin_id.in_(demo_plugin_ids)
    ).all()
    assert {
        (grant.employee_id, grant.plugin_id, grant.action, grant.decision_mode)
        for grant in grants
    } == {
        ("VE-0002", "agentteams-onboarding-employee", "execute", "allow"),
        ("VE-0003", "agentteams-it-readiness", "execute", "allow"),
        ("VE-0005", "agentteams-onboarding-evidence", "execute", "allow"),
        ("VE-0002", "agentteams-role-change-hr", "execute", "allow"),
        ("VE-0003", "agentteams-role-change-it-plan", "execute", "allow"),
        ("VE-0003", "agentteams-role-change-it-activate", "execute", "allow"),
        ("VE-0005", "agentteams-role-change-audit", "execute", "allow"),
    }
    assert db_session.get(models.Plugin, "unrelated-preserved-plugin") is not None
    assert db_session.query(models.EmployeePluginGrant).filter_by(
        plugin_id="unrelated-preserved-plugin"
    ).count() == 1
    assert db_session.get(models.TaskRun, "unrelated-preserved-task") is not None
    assert db_session.query(models.AuditEvent).filter_by(trace_id="trace-preserved").count() == 1


def test_strict_tool_sync_fails_closed_for_a_runtime_plugin_absent_from_demo_seed(db_session, tmp_path):
    from app import models
    from app.seed import sync_strict_runtime_demo_tools
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    value = _runtime_config()
    value["agents"]["hr"]["safe_tool_catalog"][0]["plugin_id"] = "invented-plugin"
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    runtime = load_strict_runtime_config(path, environ={})

    with pytest.raises(ValueError, match="invalid_strict_runtime"):
        sync_strict_runtime_demo_tools(db_session, runtime)

    assert db_session.get(models.Plugin, "invented-plugin") is None


def test_strict_runtime_sync_rolls_back_tools_when_binding_phase_fails(
    db_session, tmp_path, monkeypatch,
):
    from app import models, seed
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(_runtime_config()), encoding="utf-8")
    runtime = load_strict_runtime_config(path, environ={})
    demo_plugin_ids = {
        "agentteams-onboarding-employee",
        "agentteams-it-readiness",
        "agentteams-onboarding-evidence",
        "agentteams-role-change-hr",
        "agentteams-role-change-it-plan",
        "agentteams-role-change-it-activate",
        "agentteams-role-change-audit",
    }
    db_session.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.plugin_id.in_(demo_plugin_ids)
    ).delete(synchronize_session=False)
    db_session.query(models.Plugin).filter(
        models.Plugin.id.in_(demo_plugin_ids)
    ).delete(synchronize_session=False)
    db_session.query(models.TeamMember).filter_by(employee_id="VE-0001").delete(
        synchronize_session=False
    )
    db_session.query(models.DigitalEmployee).filter_by(employee_no="VE-0001").delete(
        synchronize_session=False
    )
    db_session.commit()
    incomplete_seed = seed.load_seed()
    incomplete_seed["digital_employees"] = [
        row for row in incomplete_seed["digital_employees"]
        if row.get("employee_no") != "VE-0001"
    ]
    monkeypatch.setattr(seed, "load_seed", lambda: incomplete_seed)

    with pytest.raises(ValueError, match="missing_strict_seed_employee"):
        seed.sync_strict_runtime_state(db_session, runtime)

    db_session.expire_all()
    assert db_session.query(models.Plugin).filter(models.Plugin.id.in_(demo_plugin_ids)).count() == 0
    assert db_session.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.plugin_id.in_(demo_plugin_ids)
    ).count() == 0


def test_strict_enabled_lifespan_syncs_bindings_before_starting_runner(db_session, tmp_path, monkeypatch):
    """The real startup path, rather than an operator, performs the sync."""
    from app import main, models

    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(_runtime_config()), encoding="utf-8")
    for agent_id in ("VE-0001", "VE-0002", "VE-0003", "VE-0005"):
        db_session.query(models.TeamMember).filter_by(employee_id=agent_id).delete()
        db_session.query(models.DigitalEmployee).filter_by(employee_no=agent_id).delete()
    db_session.query(models.TeamMember).filter_by(team_id="TEAM-ONBOARD").delete()
    db_session.query(models.Team).filter_by(id="TEAM-ONBOARD").delete()
    demo_plugin_ids = {
        "agentteams-onboarding-employee",
        "agentteams-it-readiness",
        "agentteams-onboarding-evidence",
        "agentteams-role-change-hr",
        "agentteams-role-change-it-plan",
        "agentteams-role-change-it-activate",
        "agentteams-role-change-audit",
    }
    db_session.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.plugin_id.in_(demo_plugin_ids)
    ).delete(synchronize_session=False)
    db_session.query(models.Plugin).filter(
        models.Plugin.id.in_(demo_plugin_ids)
    ).delete(synchronize_session=False)
    db_session.commit()

    class NoopRunner:
        def join(self, timeout=None):
            return None

    def start_after_sync(_stop):
        db_session.expire_all()
        assert {
            plugin.id
            for plugin in db_session.query(models.Plugin).filter(models.Plugin.id.in_(demo_plugin_ids))
        } == demo_plugin_ids
        assert db_session.query(models.EmployeePluginGrant).filter(
            models.EmployeePluginGrant.employee_id.in_(("VE-0001", "VE-0002", "VE-0003", "VE-0005")),
            models.EmployeePluginGrant.plugin_id.in_(demo_plugin_ids),
            ).count() == 7
        return NoopRunner()

    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(path))
    monkeypatch.setattr(main, "start_agentteams_runner", start_after_sync)

    with TestClient(main.app):
        db_session.expire_all()
        team = db_session.get(models.Team, "TEAM-ONBOARD")
        assert team is not None
        assert team.leader_employee_id == "VE-0001"
        assert {
            employee.employee_no: employee.runtime_ref
            for employee in db_session.query(models.DigitalEmployee).filter(
                models.DigitalEmployee.employee_no.in_(("VE-0001", "VE-0002", "VE-0003", "VE-0005"))
            )
        } == {
            "VE-0001": "@manager:test",
            "VE-0002": "@hr:test",
            "VE-0003": "@it:test",
            "VE-0005": "@audit:test",
        }


def _runtime_config() -> dict:
    return {
        "matrix_url": "http://matrix.test",
        "room_id": "!team:test",
        "team_id": "TEAM-ONBOARD",
        "platform": {"mxid": "@platform:test", "token": "platform-token"},
        "agents": {
            "manager": {"agent_id": "VE-0001", "mxid": "@manager:test", "token": "manager-token", "allowed_skill_ids": ["agentteams-onboarding-readiness", "agentteams-role-change-readiness"], "safe_tool_catalog": []},
            "hr": {"agent_id": "VE-0002", "mxid": "@hr:test", "token": "hr-token", "allowed_skill_ids": ["hr-onboarding-assistant", "hr-role-change-assistant"], "safe_tool_catalog": [
                {"name": "agentteams.hr.lookup_onboarding", "plugin_id": "agentteams-onboarding-employee", "action": "execute", "required_capability": "hr.onboarding.read", "input_schema": _employee_ref_schema()},
                {"name": "agentteams.hr.lookup_role_change", "plugin_id": "agentteams-role-change-hr", "action": "execute", "required_capability": "hr.role_change.read", "input_schema": _role_change_schema()},
            ]},
            "it": {"agent_id": "VE-0003", "mxid": "@it:test", "token": "it-token", "allowed_skill_ids": ["it-service-assistant", "it-role-change-assistant"], "safe_tool_catalog": [
                {"name": "agentteams.it.lookup_readiness", "plugin_id": "agentteams-it-readiness", "action": "execute", "required_capability": "it.readiness.read", "input_schema": _employee_ref_schema()},
                {"name": "agentteams.it.lookup_permission_plan", "plugin_id": "agentteams-role-change-it-plan", "action": "execute", "required_capability": "it.permission.plan", "input_schema": _role_change_schema()},
                {"name": "agentteams.it.activate_role_change", "plugin_id": "agentteams-role-change-it-activate", "action": "execute", "required_capability": "it.permission.activate", "input_schema": _role_change_activation_schema()},
            ]},
            "audit": {"agent_id": "VE-0005", "mxid": "@audit:test", "token": "audit-token", "allowed_skill_ids": ["audit-evidence-review", "audit-role-change-review"], "safe_tool_catalog": [
                {"name": "agentteams.audit.lookup_evidence", "plugin_id": "agentteams-onboarding-evidence", "action": "execute", "required_capability": "audit.evidence.read", "input_schema": _employee_ref_schema()},
                {"name": "agentteams.audit.review_role_change", "plugin_id": "agentteams-role-change-audit", "action": "execute", "required_capability": "audit.permission.review", "input_schema": _role_change_schema()},
            ]},
        },
        "team_catalog": {},
    }


def _database_runtime_config() -> dict:
    from app.seed import load_seed

    database_plugin = next(
        row for row in load_seed()["plugins"]
        if row["id"] == "agentteams-database-inspector"
    )
    schema = database_plugin["runtime_meta"]["input_schema"]
    roles = {
        "manager": ("VE-0001", ["agentteams-database-diagnosis"]),
        "db_write": ("DB-WRITE-01", ["db-write-investigation"]),
        "db_lock": ("DB-LOCK-01", ["db-lock-investigation"]),
        "db_vacuum": ("DB-VACUUM-01", ["db-vacuum-investigation"]),
        "db_index": ("DB-INDEX-01", ["db-index-investigation"]),
        "db_query": ("DB-QUERY-01", ["db-query-investigation"]),
    }
    agents = {}
    for role, (agent_id, skills) in roles.items():
        tools = [{
            "name": f"agentteams.{role}.inspect_database_signal",
            "plugin_id": "agentteams-database-inspector",
            "action": "read",
            "required_capability": "agentteams-database-inspector",
            "input_schema": schema,
        }]
        agents[role] = {
            "agent_id": agent_id,
            "mxid": f"@{role}:test",
            "token": f"{role}-token",
            "allowed_skill_ids": skills,
            "safe_tool_catalog": tools,
        }
    return {
        "matrix_url": "http://matrix.test",
        "room_id": "!database:test",
        "team_id": "TEAM-DB-BENCH",
        "platform": {"mxid": "@platform:test", "token": "platform-token"},
        "agents": agents,
        "team_catalog": {},
    }


def _employee_ref_schema() -> dict:
    return {
        "type": "object",
        "required": ["employee_ref"],
        "properties": {
            "employee_ref": {"type": "string", "const": "新员工A", "maxLength": 16},
        },
        "additionalProperties": False,
    }


def _role_change_schema() -> dict:
    return {
        "type": "object",
        "required": ["employee_ref"],
        "properties": {
            "employee_ref": {"type": "string", "const": "张明", "maxLength": 16},
        },
        "additionalProperties": False,
    }


def _role_change_activation_schema() -> dict:
    return {
        "type": "object",
        "required": ["employee_ref", "approval_ref"],
        "properties": {
            "employee_ref": {"type": "string", "const": "张明", "maxLength": 16},
            "approval_ref": {"type": "string", "const": "AUD-RC-001", "maxLength": 32},
        },
        "additionalProperties": False,
    }


def test_runtime_config_has_five_unique_identities_and_a_secret_free_public_view(tmp_path):
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(_runtime_config()), encoding="utf-8")

    runtime = load_strict_runtime_config(path, environ={})

    assert runtime.platform.mxid == "@platform:test"
    assert set(runtime.agents) == {"manager", "hr", "it", "audit"}
    public = runtime.public_view()
    assert "token" not in json.dumps(public)
    assert public["platform_mxid"] == "@platform:test"


@pytest.mark.parametrize("mutate", [
    lambda value: value["agents"]["hr"].update(token="manager-token"),
    lambda value: value["agents"]["hr"].update(mxid="@manager:test"),
    lambda value: value["agents"]["audit"].pop("token"),
])
def test_runtime_config_fails_closed_for_duplicate_or_missing_credentials(tmp_path, mutate):
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    value = _runtime_config()
    mutate(value)
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid_runtime_config"):
        load_strict_runtime_config(path, environ={})


def test_tool_catalog_defaults_missing_action_and_rejects_explicit_invalid_actions(tmp_path):
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    value = _runtime_config()
    value["agents"]["hr"]["safe_tool_catalog"] = [{
        "name": "lookup",
        "plugin_id": "hr-employee-mcp",
        "input_schema": _employee_ref_schema(),
    }]
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert load_strict_runtime_config(path, environ={}).agents["hr"].safe_tool_catalog[0]["action"] == "read"

    for action in ("", "delete"):
        value["agents"]["hr"]["safe_tool_catalog"][0]["action"] = action
        path.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError, match="invalid_runtime_config"):
            load_strict_runtime_config(path, environ={})


@pytest.mark.parametrize(
    "input_schema",
    [
        None,
        {**_employee_ref_schema(), "additionalProperties": True},
        {
            "type": "object",
            "required": ["api_token"],
            "properties": {"api_token": {"type": "string", "const": "x", "maxLength": 16}},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "required": ["employee_ref"],
            "properties": {"employee_ref": {"type": "object", "properties": {}}},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "required": [["employee_ref"]],
            "properties": {"employee_ref": {"type": "string", "const": "新员工A", "maxLength": 16}},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "required": ["employee_ref"],
            "properties": {"employee_ref": {"type": "string", "const": "x" * 5000, "maxLength": 5000}},
            "additionalProperties": False,
        },
    ],
    ids=["missing", "open-object", "sensitive-name", "nested-object", "non-string-required", "oversized"],
)
def test_runtime_config_rejects_unsafe_or_malformed_tool_input_schema(tmp_path, input_schema):
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    value = _runtime_config()
    tool = value["agents"]["hr"]["safe_tool_catalog"][0]
    if input_schema is None:
        tool.pop("input_schema")
    else:
        tool["input_schema"] = input_schema
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid_runtime_config"):
        load_strict_runtime_config(path, environ={})


@pytest.mark.parametrize(
    "sensitive_const",
    [
        "token=synthetic-value",
        "secret: synthetic-value",
        "password = synthetic-value",
        "api-key: synthetic-value",
        "sk-abcdefghijklmnopqrstuvwxyz0123456789",
    ],
    ids=["token", "secret", "password", "api-key-assignment", "api-key-shape"],
)
def test_runtime_config_rejects_sensitive_tool_schema_constant_content(tmp_path, sensitive_const):
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    value = _runtime_config()
    field_schema = value["agents"]["hr"]["safe_tool_catalog"][0]["input_schema"][
        "properties"
    ]["employee_ref"]
    field_schema["const"] = sensitive_const
    field_schema["maxLength"] = 128
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid_runtime_config"):
        load_strict_runtime_config(path, environ={})
