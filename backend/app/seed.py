import json
import sys
from pathlib import Path

from . import models
from .database import DATABASE_URL, Base, SessionLocal, engine

SEED_PATH = Path(__file__).resolve().parents[2] / "mock-data" / "seed.json"


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


def sync_strict_runtime_bindings(db, runtime) -> None:
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
    db.commit()


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
