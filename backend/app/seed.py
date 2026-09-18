import json
import sys
from copy import deepcopy
from pathlib import Path

from sqlalchemy import select

from . import models
from .database import DATABASE_URL, Base, SessionLocal, engine
from .services.agentteams_database_scenarios import (
    DatabaseScenarioCatalog,
    database_inspection_input_schema,
)
from .services.agentteams_hierarchy import TeamHierarchy, TeamHierarchyError

SEED_PATH = Path(__file__).resolve().parents[2] / "mock-data" / "seed.json"
_STRICT_DEMO_ROLE_PLUGINS = {
    "manager": ("VE-0001", ()),
    "hr": ("VE-0002", ()),
    "it": ("VE-0003", ()),
    "audit": ("VE-0005", ()),
    "hr_material": ("VE-HR-MATERIAL", ("agentteams-onboarding-materials", "agentteams-role-change-hr")),
    "hr_training": ("VE-HR-TRAINING", ("agentteams-onboarding-training", "agentteams-role-change-hr")),
    "it_account": ("VE-IT-ACCOUNT", ("agentteams-it-accounts", "agentteams-role-change-it-plan")),
    "it_device": ("VE-IT-DEVICE", ("agentteams-it-devices", "agentteams-role-change-it-plan", "agentteams-role-change-it-activate")),
    "audit_review": ("VE-AUDIT-REVIEW", ("agentteams-onboarding-evidence", "agentteams-role-change-audit", "agentteams-expense-audit")),
    "finance": ("VE-FIN-LEAD", ()),
    "finance_invoice": ("VE-FIN-INVOICE", ("agentteams-expense-claim",)),
    "finance_budget": ("VE-FIN-BUDGET", ("agentteams-expense-budget",)),
    "finance_payment": ("VE-FIN-PAYMENT", ("agentteams-expense-payment",)),
}
_STRICT_DATABASE_ROLE_PLUGINS = {
    "manager": ("VE-0001", ("agentteams-database-inspector",)),
    "db_ops_lead": ("DB-OPS-LEAD", ()),
    "db_perf_lead": ("DB-PERF-LEAD", ()),
    "db_write": ("DB-WRITE-01", ("agentteams-database-inspector",)),
    "db_lock": ("DB-LOCK-01", ("agentteams-database-inspector",)),
    "db_vacuum": ("DB-VACUUM-01", ("agentteams-database-inspector",)),
    "db_index": ("DB-INDEX-01", ("agentteams-database-inspector",)),
    "db_query": ("DB-QUERY-01", ("agentteams-database-inspector",)),
}
_STRICT_ROLE_CATALOGS = {
    frozenset(_STRICT_DEMO_ROLE_PLUGINS): _STRICT_DEMO_ROLE_PLUGINS,
    frozenset(_STRICT_DATABASE_ROLE_PLUGINS): _STRICT_DATABASE_ROLE_PLUGINS,
}
_DATABASE_BENCHMARK_EMPLOYEE_IDS = {
    "DB-OPS-LEAD", "DB-PERF-LEAD",
    "DB-WRITE-01", "DB-LOCK-01", "DB-VACUUM-01", "DB-INDEX-01", "DB-QUERY-01",
}
_DATABASE_BENCHMARK_PRINCIPAL_IDS = {
    "VE-0001", "DB-WRITE-01", "DB-LOCK-01", "DB-VACUUM-01", "DB-INDEX-01", "DB-QUERY-01",
}
_DATABASE_BENCHMARK_PLUGIN_ID = "agentteams-database-inspector"
_DATABASE_BENCHMARK_TEAM_ID = "TEAM-DB-BENCH"
_COLLABORATION_TEAM_IDS = {"TEAM-ONBOARD", _DATABASE_BENCHMARK_TEAM_ID}
_COLLABORATION_TWIN_IDS = {"DT-E10281", "DT-E20999"}


def load_seed() -> dict:
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    scenario_ids = DatabaseScenarioCatalog.from_root(
        Path(__file__).resolve().parents[2]
    ).ids()
    for plugin in data.get("plugins", []):
        if isinstance(plugin, dict) and plugin.get("id") == _DATABASE_BENCHMARK_PLUGIN_ID:
            runtime_meta = plugin.get("runtime_meta")
            if not isinstance(runtime_meta, dict):
                raise ValueError("invalid_database_benchmark_seed")
            runtime_meta["input_schema"] = database_inspection_input_schema(scenario_ids)
    return data


def seed_data(db, data: dict) -> None:
    # 幂等：先清空旧数据再灌种子（演示环境专用，SQLite 无外键约束）
    for model in (
        models.BenchmarkRun,
        models.BenchmarkExperiment,
        models.AgentTeamsEventSeen,
        models.ConversationMessage,
        models.Conversation,
        models.Skill,
        models.TaskRun,
        models.AuditEvent,
        models.MemoryEntry,
        models.TeamMember,
        models.Team,
        models.KnowledgeChunk,
        models.KnowledgeBase,
        models.AccessRequest,
        models.ChatMessage,
        models.ChatSession,
        models.TeamPluginGrant,
        models.EmployeePluginGrant,
        models.Policy,
        models.Plugin,
        models.DigitalEmployee,
        models.HumanEmployee,
    ):
        db.query(model).delete()
    db.commit()

    for row in data.get("human_employees", []):
        db.add(models.HumanEmployee(**row))
    for row in data.get("digital_employees", []):
        db.add(models.DigitalEmployee(**row))
    for row in data.get("plugins", []):
        db.add(models.Plugin(**row))
    for row in data.get("employee_plugin_grants", []):
        db.add(models.EmployeePluginGrant(**row))
    for row in data.get("team_plugin_grants", []):
        db.add(models.TeamPluginGrant(**row))
    for row in data.get("policies", []):
        db.add(models.Policy(**row))
    for row in data.get("audit_events", []):
        db.add(models.AuditEvent(**row))
    for team in data.get("teams", []):
        members = team.get("members", [])
        db.add(models.Team(**{k: v for k, v in team.items() if k != "members"}))
        for m in members:
            db.add(models.TeamMember(team_id=team["id"], **m))
    for row in data.get("knowledge_bases", []):
        db.add(models.KnowledgeBase(**row))
    for row in data.get("personal_memories", []):
        db.add(models.MemoryEntry(**row))
    for row in data.get("skills", []):
        db.add(models.Skill(**row))
    for conv in data.get("conversations", []):
        db.add(models.Conversation(**{k: v for k, v in conv.items() if k != "messages"}))
        for msg in conv.get("messages", []):
            db.add(models.ConversationMessage(conversation_id=conv["id"], **msg))
    for row in data.get("task_runs", []):
        db.add(models.TaskRun(**row))
    db.commit()
    sync_collaboration_twin_plugin_grants(db)


def seed_if_empty() -> None:
    db = SessionLocal()
    try:
        if db.query(models.DigitalEmployee).count() == 0 or db.query(models.Skill).count() == 0:
            seed_data(db, load_seed())
    finally:
        db.close()


def sync_team_plugin_grants(db, *, commit: bool = True) -> None:
    """Install or refresh only version-controlled team grants, without resetting data."""
    rows = [row for row in load_seed().get("team_plugin_grants", []) if isinstance(row, dict)]
    for row in rows:
        if db.get(models.Team, row["team_id"]) is None or db.get(models.Plugin, row["plugin_id"]) is None:
            continue
        grant = db.query(models.TeamPluginGrant).filter_by(
            team_id=row["team_id"],
            plugin_id=row["plugin_id"],
            action=row["action"],
        ).one_or_none()
        if grant is None:
            db.add(models.TeamPluginGrant(**deepcopy(row)))
        elif grant.grant_source in {"", "seed"}:
            grant.decision_mode = row["decision_mode"]
            grant.grant_source = row.get("grant_source", "seed")
    if commit:
        db.commit()


def sync_collaboration_twin_plugin_grants(db, *, commit: bool = True) -> None:
    """Mirror current collaboration-team allows to the two approved twins."""
    twins = list(db.scalars(
        select(models.DigitalEmployee).where(
            models.DigitalEmployee.employee_no.in_(_COLLABORATION_TWIN_IDS),
            models.DigitalEmployee.type == "twin",
            models.DigitalEmployee.status == "active",
        )
    ))
    team_allows = list(db.scalars(
        select(models.TeamPluginGrant).where(
            models.TeamPluginGrant.team_id.in_(_COLLABORATION_TEAM_IDS),
            models.TeamPluginGrant.decision_mode == "allow",
        )
    ))
    allowed_pairs = sorted({(grant.plugin_id, grant.action) for grant in team_allows})
    for twin in twins:
        for plugin_id, action in allowed_pairs:
            matches = db.query(models.EmployeePluginGrant).filter_by(
                employee_id=twin.employee_no,
                plugin_id=plugin_id,
                action=action,
            ).all()
            if any(grant.grant_source != "seed" for grant in matches):
                continue
            seeded = next((grant for grant in matches if grant.grant_source == "seed"), None)
            if seeded is None:
                db.add(models.EmployeePluginGrant(
                    employee_id=twin.employee_no,
                    plugin_id=plugin_id,
                    action=action,
                    decision_mode="allow",
                    grant_source="seed",
                ))
            else:
                seeded.decision_mode = "allow"
    if commit:
        db.commit()


def sync_database_benchmark_resources(db, *, commit: bool = True) -> None:
    """Additively install the checked-in benchmark identities, Tool and team.

    Existing application data is left in place. Only IDs reserved by this
    benchmark are synchronized to the version-controlled seed contract.
    """
    data = load_seed()
    employee_rows = {
        row["employee_no"]: row
        for row in data.get("digital_employees", [])
        if isinstance(row, dict) and row.get("employee_no") in _DATABASE_BENCHMARK_EMPLOYEE_IDS
    }
    plugin_rows = [
        row for row in data.get("plugins", [])
        if isinstance(row, dict) and row.get("id") == _DATABASE_BENCHMARK_PLUGIN_ID
    ]
    team_rows = [
        row for row in data.get("teams", [])
        if isinstance(row, dict) and row.get("id") == _DATABASE_BENCHMARK_TEAM_ID
    ]
    grant_rows = [
        row for row in data.get("employee_plugin_grants", [])
        if isinstance(row, dict)
        and row.get("plugin_id") == _DATABASE_BENCHMARK_PLUGIN_ID
        and row.get("employee_id") in _DATABASE_BENCHMARK_PRINCIPAL_IDS
    ]
    team_grant_rows = [
        row for row in data.get("team_plugin_grants", [])
        if isinstance(row, dict)
        and row.get("team_id") == _DATABASE_BENCHMARK_TEAM_ID
        and row.get("plugin_id") == _DATABASE_BENCHMARK_PLUGIN_ID
    ]
    if (
        set(employee_rows) != _DATABASE_BENCHMARK_EMPLOYEE_IDS
        or len(plugin_rows) != 1
        or len(team_rows) != 1
        or len(grant_rows) != len(_DATABASE_BENCHMARK_PRINCIPAL_IDS)
        or len(team_grant_rows) != 1
    ):
        raise ValueError("invalid_database_benchmark_seed")

    for employee_id, row in employee_rows.items():
        employee = db.get(models.DigitalEmployee, employee_id)
        if employee is None:
            db.add(models.DigitalEmployee(**deepcopy(row)))
        else:
            for field, value in row.items():
                if field != "employee_no":
                    setattr(employee, field, deepcopy(value))

    plugin_row = plugin_rows[0]
    plugin = db.get(models.Plugin, _DATABASE_BENCHMARK_PLUGIN_ID)
    if plugin is None:
        db.add(models.Plugin(**deepcopy(plugin_row)))
    else:
        for field, value in plugin_row.items():
            if field != "id":
                setattr(plugin, field, deepcopy(value))

    for row in grant_rows:
        matches = db.query(models.EmployeePluginGrant).filter_by(
            employee_id=row["employee_id"],
            plugin_id=_DATABASE_BENCHMARK_PLUGIN_ID,
        ).all()
        if len(matches) > 1:
            raise ValueError("invalid_database_benchmark_seed")
        if matches:
            grant = matches[0]
            grant.action = row["action"]
            grant.decision_mode = row["decision_mode"]
            grant.grant_source = row.get("grant_source", "seed")
        else:
            db.add(models.EmployeePluginGrant(**deepcopy(row)))

    team_row = deepcopy(team_rows[0])
    member_rows = team_row.pop("members")
    team = db.get(models.Team, _DATABASE_BENCHMARK_TEAM_ID)
    if team is None:
        db.add(models.Team(**team_row))
    else:
        team.name = team_row["name"]
        team.leader_employee_id = team_row["leader_employee_id"]
        team.description = team_row["description"]
    for row in member_rows:
        matches = db.query(models.TeamMember).filter_by(
            team_id=_DATABASE_BENCHMARK_TEAM_ID,
            employee_id=row["employee_id"],
        ).all()
        if len(matches) > 1:
            raise ValueError("invalid_database_benchmark_seed")
        if matches:
            matches[0].role = row["role"]
            matches[0].reports_to_employee_id = row.get("reports_to_employee_id")
            matches[0].capabilities = deepcopy(row.get("capabilities", []))
        else:
            db.add(models.TeamMember(team_id=_DATABASE_BENCHMARK_TEAM_ID, **row))
    team_grant_row = team_grant_rows[0]
    team_grant = db.query(models.TeamPluginGrant).filter_by(
        team_id=_DATABASE_BENCHMARK_TEAM_ID,
        plugin_id=_DATABASE_BENCHMARK_PLUGIN_ID,
        action=team_grant_row["action"],
    ).one_or_none()
    if team_grant is None:
        db.add(models.TeamPluginGrant(**deepcopy(team_grant_row)))
    elif team_grant.grant_source in {"", "seed"}:
        team_grant.decision_mode = team_grant_row["decision_mode"]
        team_grant.grant_source = team_grant_row.get("grant_source", "seed")
    db.flush()
    sync_collaboration_twin_plugin_grants(db, commit=False)
    if commit:
        db.commit()


def sync_strict_runtime_demo_tools(db, runtime, *, commit: bool = True) -> None:
    """Additively synchronize only the strict demo Tools named by the runtime.

    The checked-in seed remains the plugin/grant authority.  Runtime entries
    must match it exactly before any database row is changed, so an invented
    plugin, mismatched employee, or drifted input contract fails closed.
    """
    plugin_rows, grant_rows = _strict_runtime_demo_seed_records(runtime)

    existing_plugins = {
        plugin_id: db.get(models.Plugin, plugin_id)
        for plugin_id in plugin_rows
    }
    existing_grants = {}
    for key in grant_rows:
        employee_id, plugin_id = key
        matches = db.query(models.EmployeePluginGrant).filter_by(
            employee_id=employee_id,
            plugin_id=plugin_id,
        ).all()
        if len(matches) > 1:
            raise ValueError("invalid_strict_runtime")
        existing_grants[key] = matches[0] if matches else None

    for plugin_id, row in plugin_rows.items():
        plugin = existing_plugins[plugin_id]
        if plugin is None:
            db.add(models.Plugin(**deepcopy(row)))
            continue
        for field in (
            "name", "type", "endpoint_ref", "data_level", "status", "description", "runtime_meta",
        ):
            setattr(plugin, field, deepcopy(row[field]))

    for key, row in grant_rows.items():
        grant = existing_grants[key]
        if grant is None:
            db.add(models.EmployeePluginGrant(**deepcopy(row)))
            continue
        grant.action = row["action"]
        grant.decision_mode = row["decision_mode"]
    if commit:
        db.commit()


def _strict_runtime_demo_seed_records(runtime) -> tuple[dict[str, dict], dict[tuple[str, str], dict]]:
    role_plugins = _STRICT_ROLE_CATALOGS.get(frozenset(runtime.agents))
    if role_plugins is None:
        raise ValueError("invalid_strict_runtime")

    seed = load_seed()
    seed_plugins: dict[str, list[dict]] = {}
    for row in seed.get("plugins", []):
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            seed_plugins.setdefault(row["id"], []).append(row)
    seed_grants = [row for row in seed.get("employee_plugin_grants", []) if isinstance(row, dict)]

    selected_plugins: dict[str, dict] = {}
    selected_grants: dict[tuple[str, str], dict] = {}
    for role, (expected_employee_id, expected_plugin_ids) in role_plugins.items():
        identity = runtime.agents[role]
        if identity.agent_id != expected_employee_id:
            raise ValueError("invalid_strict_runtime")
        tools = list(identity.safe_tool_catalog)
        if tuple(tool.get("plugin_id") for tool in tools) != expected_plugin_ids:
            raise ValueError("invalid_strict_runtime")
        for tool in tools:
            plugin_id = tool["plugin_id"]
            candidates = seed_plugins.get(plugin_id, [])
            if len(candidates) != 1:
                raise ValueError("invalid_strict_runtime")
            plugin_row = candidates[0]
            runtime_meta = plugin_row.get("runtime_meta")
            if (
                not isinstance(runtime_meta, dict)
                or runtime_meta.get("input_schema") != tool.get("input_schema")
                or tool.get("action") not in runtime_meta.get("actions", [])
            ):
                raise ValueError("invalid_strict_runtime")
            matching_grants = [
                row for row in seed_grants
                if row.get("employee_id") == identity.agent_id
                and row.get("plugin_id") == plugin_id
            ]
            if (
                len(matching_grants) != 1
                or matching_grants[0].get("action") != tool.get("action")
                or matching_grants[0].get("decision_mode") != "allow"
            ):
                raise ValueError("invalid_strict_runtime")
            selected_plugins[plugin_id] = plugin_row
            selected_grants[(identity.agent_id, plugin_id)] = matching_grants[0]
    return selected_plugins, selected_grants


def sync_strict_runtime_bindings(db, runtime, *, commit: bool = True) -> None:
    """Synchronize the fixed strict demo Team from the validated runtime file.

    The Matrix bootstrap is intentionally credential-only.  This narrowly
    bridges its public identity facts into the platform's durable Team and
    digital-employee records, so a clean strict startup never requires an
    operator to edit the database by hand.
    """
    templates = {
        row["employee_no"]: row
        for row in load_seed().get("digital_employees", [])
        if isinstance(row, dict) and isinstance(row.get("employee_no"), str)
    }
    role_plugins = _STRICT_ROLE_CATALOGS.get(frozenset(runtime.agents))
    if role_plugins is None:
        raise ValueError("invalid_strict_runtime")
    agent_ids = {identity.agent_id for identity in runtime.agents.values()}
    if set(runtime.agents) != set(role_plugins) or None in agent_ids:
        raise ValueError("invalid_strict_runtime")
    try:
        hierarchy = TeamHierarchy.from_relationships(
            manager_id=runtime.agents["manager"].agent_id,
            member_ids=agent_ids,
            relationships=runtime.team_relationships,
        )
    except TeamHierarchyError as exc:
        raise ValueError("invalid_strict_runtime") from exc

    team = db.get(models.Team, runtime.team_id)
    manager_id = runtime.agents["manager"].agent_id
    if team is None:
        team = models.Team(
            id=runtime.team_id,
            name=(
                "AgentTeams 数据库诊断评测团队"
                if frozenset(runtime.agents) == frozenset(_STRICT_DATABASE_ROLE_PLUGINS)
                else "AgentTeams 业务协作团队"
            ),
            leader_employee_id=manager_id,
            description="本地 Matrix 严格运行时的受控协作团队",
        )
        db.add(team)
    else:
        team.leader_employee_id = manager_id
        if frozenset(runtime.agents) == frozenset(_STRICT_DEMO_ROLE_PLUGINS):
            team.name = "AgentTeams 业务协作团队"

    db.query(models.TeamMember).filter(
        models.TeamMember.team_id == runtime.team_id,
        ~models.TeamMember.employee_id.in_(agent_ids),
    ).delete(synchronize_session=False)

    for role in role_plugins:
        identity = runtime.agents[role]
        member_role = hierarchy.level_of(identity.agent_id)
        parent_ids = runtime.team_relationships[identity.agent_id]["reports_to"]
        reports_to = parent_ids[0] if parent_ids else None
        raw_catalog_entry = getattr(runtime, "team_catalog", {}).get(identity.agent_id, {})
        structural_capabilities = (
            list(raw_catalog_entry.get("capabilities", []))
            if isinstance(raw_catalog_entry, dict)
            else []
        )
        employee = db.get(models.DigitalEmployee, identity.agent_id)
        if employee is None:
            template = templates.get(identity.agent_id)
            if template is None:
                raise ValueError("missing_strict_seed_employee")
            employee = models.DigitalEmployee(**template)
            db.add(employee)
        employee.status = "active"
        employee.runtime_type = "agentteams"
        employee.runtime_ref = identity.mxid

        member = db.query(models.TeamMember).filter_by(
            team_id=runtime.team_id, employee_id=identity.agent_id
        ).first()
        if member is None:
            db.add(models.TeamMember(
                team_id=runtime.team_id,
                employee_id=identity.agent_id,
                role=member_role,
                reports_to_employee_id=reports_to,
                capabilities=structural_capabilities,
            ))
        else:
            member.role = member_role
            member.reports_to_employee_id = reports_to
            member.capabilities = structural_capabilities
    if commit:
        db.commit()


def sync_strict_runtime_state(db, runtime) -> None:
    """Atomically synchronize strict Tools, identities, and all permission layers."""
    try:
        sync_strict_runtime_demo_tools(db, runtime, commit=False)
        sync_strict_runtime_bindings(db, runtime, commit=False)
        if frozenset(runtime.agents) == frozenset(_STRICT_DEMO_ROLE_PLUGINS):
            twin = db.get(models.DigitalEmployee, "DT-E10281")
            if twin is None:
                twin_template = next(
                    (
                        row for row in load_seed().get("digital_employees", [])
                        if isinstance(row, dict) and row.get("employee_no") == "DT-E10281"
                    ),
                    None,
                )
                if twin_template is None:
                    raise ValueError("missing_strict_seed_employee")
                db.add(models.DigitalEmployee(**deepcopy(twin_template)))
        db.flush()
        sync_team_plugin_grants(db, commit=False)
        db.flush()
        sync_collaboration_twin_plugin_grants(db, commit=False)
        db.commit()
    except Exception:
        db.rollback()
        raise


def main() -> None:
    if "--reset" in sys.argv and DATABASE_URL.startswith("sqlite"):
        db_file = Path(DATABASE_URL.replace("sqlite:///", ""))
        if db_file.exists():
            db_file.unlink()
    Base.metadata.create_all(bind=engine)
    seed_if_empty()
    # RAG 索引：seed 重建时顺带重建 kb_chunk（离线无 Key 时使用本地演示向量）
    from .services import kb_index

    kb_index.rebuild_index()
    print("seed ok")


if __name__ == "__main__":
    main()
