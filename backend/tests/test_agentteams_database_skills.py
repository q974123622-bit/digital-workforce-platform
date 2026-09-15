from pathlib import Path
import json

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATABASE_SKILLS = {
    "agentteams-database-diagnosis",
    "db-write-investigation",
    "db-lock-investigation",
    "db-vacuum-investigation",
    "db-index-investigation",
    "db-query-investigation",
}


def test_database_scenario_and_professional_skills_load_from_project_registry():
    from app.services.agentteams_skills import ProjectSkillRegistry

    registry = ProjectSkillRegistry.from_root(PROJECT_ROOT)
    loaded = registry.load(sorted(DATABASE_SKILLS))

    assert {skill.skill_id for skill in loaded} == DATABASE_SKILLS
    scenario = next(skill for skill in loaded if skill.skill_id == "agentteams-database-diagnosis")
    assert "evidence_id" in scenario.content
    assert "Single Agent" in scenario.content


@pytest.mark.parametrize(
    ("role", "variable"),
    [
        ("db_ops_lead", "DEEPSEEK_API_KEY_MANAGER"),
        ("db_perf_lead", "DEEPSEEK_API_KEY_MANAGER"),
        ("db_write", "DEEPSEEK_API_KEY_DB_WRITE"),
        ("db_lock", "DEEPSEEK_API_KEY_DB_LOCK"),
        ("db_vacuum", "DEEPSEEK_API_KEY_DB_VACUUM"),
        ("db_index", "DEEPSEEK_API_KEY_DB_INDEX"),
        ("db_query", "DEEPSEEK_API_KEY_DB_QUERY"),
    ],
)
def test_each_database_role_resolves_only_its_own_key_environment(role, variable):
    from scripts.run_agentteams_agent import resolve_role_api_key

    assert resolve_role_api_key(role, {variable: "role-only-key"}) == "role-only-key"
    with pytest.raises(ValueError, match="deepseek_key_missing"):
        resolve_role_api_key(role, {"DEEPSEEK_API_KEY": "shared-key"})


def test_database_role_skill_mapping_is_one_professional_skill_per_worker():
    from scripts.run_agentteams_agent import role_skill_ids

    assert role_skill_ids("manager") >= {"agentteams-database-diagnosis"}
    assert role_skill_ids("db_ops_lead") == {"agentteams-database-diagnosis"}
    assert role_skill_ids("db_perf_lead") == {"agentteams-database-diagnosis"}
    assert role_skill_ids("db_write") == {"db-write-investigation"}
    assert role_skill_ids("db_lock") == {"db-lock-investigation"}
    assert role_skill_ids("db_vacuum") == {"db-vacuum-investigation"}
    assert role_skill_ids("db_index") == {"db-index-investigation"}
    assert role_skill_ids("db_query") == {"db-query-investigation"}


def test_database_runtime_role_defaults_to_its_single_professional_skill(tmp_path):
    from app.services.agentteams_agent_runtime import AgentRuntimeConfig

    config = AgentRuntimeConfig(
        agent_id="DB-LOCK-01",
        role="db_lock",
        matrix_mxid="@db-lock:matrix.test",
        matrix_token="matrix-token",
        room_id="!database:matrix.test",
        trusted_sender_mxids={
            "platform": "@platform:matrix.test",
            "DB-LOCK-01": "@db-lock:matrix.test",
        },
        allowed_skill_ids={"db-lock-investigation"},
        state_dir=tmp_path / "state",
    )

    assert config.professional_skill_ids == {"db-lock-investigation"}


def test_strict_runtime_accepts_manager_leaders_and_five_database_workers(tmp_path):
    from scripts.run_agentteams_agent import load_runtime_config

    role_rows = {
        "manager": ("VE-0001", "agentteams-database-diagnosis"),
        "db_ops_lead": ("DB-OPS-LEAD", "agentteams-database-diagnosis"),
        "db_perf_lead": ("DB-PERF-LEAD", "agentteams-database-diagnosis"),
        "db_write": ("DB-WRITE-01", "db-write-investigation"),
        "db_lock": ("DB-LOCK-01", "db-lock-investigation"),
        "db_vacuum": ("DB-VACUUM-01", "db-vacuum-investigation"),
        "db_index": ("DB-INDEX-01", "db-index-investigation"),
        "db_query": ("DB-QUERY-01", "db-query-investigation"),
    }
    tool_schema = {
        "type": "object",
        "required": ["scenario_id", "signal", "filters", "limit"],
        "properties": {
            "scenario_id": {"type": "string", "enum": ["bulk_writes"]},
            "signal": {"type": "string", "enum": ["statements"]},
            "filters": {"type": "object", "additionalProperties": {"type": "string"}, "maxProperties": 4},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
    }
    agents = {}
    team_catalog = {}
    for role, (agent_id, skill_id) in role_rows.items():
        is_coordinator = role == "manager" or role.endswith("_lead")
        tools = [] if is_coordinator else [{
            "name": "inspect_database_signal",
            "plugin_id": "agentteams-database-inspector",
            "action": "read",
            "required_capability": f"database.{role.removeprefix('db_')}.inspect",
            "input_schema": tool_schema,
        }]
        agents[role] = {
            "agent_id": agent_id,
            "mxid": f"@{role}:matrix.test",
            "token": f"{role}-matrix-token",
            "allowed_skill_ids": [skill_id],
            "safe_tool_catalog": tools,
        }
        team_catalog[agent_id] = {
            "agent_id": agent_id,
            "capabilities": (
                [] if role == "manager"
                else ["db.operations.coordinate"] if role == "db_ops_lead"
                else ["db.performance.coordinate"] if role == "db_perf_lead"
                else [f"database.{role.removeprefix('db_')}.inspect"]
            ),
        }
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({
        "matrix_url": "https://matrix.test",
        "room_id": "!database:matrix.test",
        "team_id": "TEAM-DB-BENCH",
        "platform": {"mxid": "@platform:matrix.test", "token": "platform-matrix-token"},
        "agents": agents,
        "team_catalog": team_catalog,
        "team_relationships": {
            "VE-0001": {"role": "Manager", "reports_to": [], "direct_reports": ["DB-OPS-LEAD", "DB-PERF-LEAD"]},
            "DB-OPS-LEAD": {"role": "数据库运维负责人", "reports_to": ["VE-0001"], "direct_reports": ["DB-WRITE-01", "DB-LOCK-01", "DB-VACUUM-01"]},
            "DB-PERF-LEAD": {"role": "数据库性能负责人", "reports_to": ["VE-0001"], "direct_reports": ["DB-INDEX-01", "DB-QUERY-01"]},
            "DB-WRITE-01": {"role": "写入诊断", "reports_to": ["DB-OPS-LEAD"], "direct_reports": []},
            "DB-LOCK-01": {"role": "锁诊断", "reports_to": ["DB-OPS-LEAD"], "direct_reports": []},
            "DB-VACUUM-01": {"role": "Vacuum诊断", "reports_to": ["DB-OPS-LEAD"], "direct_reports": []},
            "DB-INDEX-01": {"role": "索引诊断", "reports_to": ["DB-PERF-LEAD"], "direct_reports": []},
            "DB-QUERY-01": {"role": "查询诊断", "reports_to": ["DB-PERF-LEAD"], "direct_reports": []},
        },
    }), encoding="utf-8")

    for role, (agent_id, skill_id) in role_rows.items():
        key_name = (
            "DEEPSEEK_API_KEY_MANAGER"
            if role in {"manager", "db_ops_lead", "db_perf_lead"}
            else f"DEEPSEEK_API_KEY_{role.upper()}"
        )
        config, _ = load_runtime_config(
            role,
            path,
            env={
                "DWP_AGENTTEAMS_RUNTIME_CONFIG": str(path),
                key_name: "role-only-key",
            },
        )
        assert config.agent_id == agent_id
        assert config.allowed_skill_ids == {skill_id}
        if role.startswith("db_") and not role.endswith("_lead"):
            assert config.safe_tool_catalog[0]["name"] == "inspect_database_signal"
