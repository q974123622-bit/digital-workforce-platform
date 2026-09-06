import json
import sys
from copy import deepcopy
from pathlib import Path

from . import models
from .database import DATABASE_URL, Base, SessionLocal, engine

SEED_PATH = Path(__file__).resolve().parents[2] / "mock-data" / "seed.json"
_STRICT_DEMO_ROLE_PLUGINS = {
    "manager": ("VE-0001", ()),
    "hr": ("VE-0002", ("agentteams-onboarding-employee",)),
    "it": ("VE-0003", ("agentteams-it-readiness",)),
    "audit": ("VE-0005", ("agentteams-onboarding-evidence",)),
}


def load_seed() -> dict:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def seed_data(db, data: dict) -> None:
    # 幂等：先清空旧数据再灌种子（演示环境专用，SQLite 无外键约束）
    for model in (
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


def seed_if_empty() -> None:
    db = SessionLocal()
    try:
        if db.query(models.DigitalEmployee).count() == 0 or db.query(models.Skill).count() == 0:
            seed_data(db, load_seed())
    finally:
        db.close()


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
    if set(runtime.agents) != set(_STRICT_DEMO_ROLE_PLUGINS):
        raise ValueError("invalid_strict_runtime")

    seed = load_seed()
    seed_plugins: dict[str, list[dict]] = {}
    for row in seed.get("plugins", []):
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            seed_plugins.setdefault(row["id"], []).append(row)
    seed_grants = [row for row in seed.get("employee_plugin_grants", []) if isinstance(row, dict)]

    selected_plugins: dict[str, dict] = {}
    selected_grants: dict[tuple[str, str], dict] = {}
    for role, (expected_employee_id, expected_plugin_ids) in _STRICT_DEMO_ROLE_PLUGINS.items():
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
    expected_roles = {
        "manager": "leader",
        "hr": "worker",
        "it": "worker",
        "audit": "worker",
    }
    agent_ids = {identity.agent_id for identity in runtime.agents.values()}
    if set(runtime.agents) != set(expected_roles) or None in agent_ids:
        raise ValueError("invalid_strict_runtime")

    team = db.get(models.Team, runtime.team_id)
    manager_id = runtime.agents["manager"].agent_id
    if team is None:
        team = models.Team(
            id=runtime.team_id,
            name="AgentTeams 入职协作团队",
            leader_employee_id=manager_id,
            description="本地 Matrix 严格演示的受控协作团队",
        )
        db.add(team)
    else:
        team.leader_employee_id = manager_id

    for role, member_role in expected_roles.items():
        identity = runtime.agents[role]
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
            ))
        else:
            member.role = member_role
    if commit:
        db.commit()


def sync_strict_runtime_state(db, runtime) -> None:
    """Atomically synchronize strict demo Tools, grants, employees, and Team bindings."""
    try:
        sync_strict_runtime_demo_tools(db, runtime, commit=False)
        sync_strict_runtime_bindings(db, runtime, commit=False)
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
