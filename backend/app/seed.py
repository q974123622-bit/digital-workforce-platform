import json
import os
import sys
from pathlib import Path

from sqlalchemy import select

from . import models
from .database import DATABASE_URL, Base, SessionLocal, engine, ensure_schema_compatibility
from .services.auth import hash_password

SEED_PATH = Path(__file__).resolve().parents[2] / "mock-data" / "seed.json"


def _secret_value(env_name: str, file_env_name: str, default_file: str, fallback: str) -> str:
    value = os.getenv(env_name)
    if value:
        return value
    path = Path(os.getenv(file_env_name, default_file))
    if path.is_file():
        candidate = path.read_text(encoding="utf-8").strip()
        if candidate:
            return candidate
    return fallback


def load_seed() -> dict:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def seed_data(db, data: dict) -> None:
    # 幂等：先清空旧数据再灌种子（演示环境专用，SQLite 无外键约束）
    for model in (
        models.MemorySyncJob,
        models.MemoryRecord,
        models.ConversationMemoryState,
        models.McpRuntimeInstance,
        models.PluginBuildJob,
        models.AgentPluginBinding,
        models.PluginReview,
        models.PluginVersion,
        models.AgentExecutionEvent,
        models.AgentExecution,
        models.DelegationRun,
        models.PersonaVersion,
        models.AgentKnowledgeGrant,
        models.AgentRuntime,
        models.AgentProfile,
        models.DirectoryBinding,
        models.AuthSession,
        models.Account,
        models.AgentTeamsEventSeen,
        models.ConversationMessage,
        models.Conversation,
        models.Skill,
        models.TaskRun,
        models.AuditEvent,
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
    for row in data.get("skills", []):
        db.add(models.Skill(**row))
    for conv in data.get("conversations", []):
        db.add(models.Conversation(**{k: v for k, v in conv.items() if k != "messages"}))
        for msg in conv.get("messages", []):
            db.add(models.ConversationMessage(conversation_id=conv["id"], **msg))
    for row in data.get("task_runs", []):
        db.add(models.TaskRun(**row))
    db.commit()
    ensure_mvp_seed(db)


def ensure_mvp_seed(db) -> None:
    """Idempotently add the knowledge-first MVP identities to old and newly seeded databases."""
    employee_specs = {
        "AI-GENERAL": {
            "name": "AI员工平台",
            "department": "综合服务",
            "persona": "你是AI员工平台，是公司中的综合知识服务同事。你负责内规、外规、IT服务和通用办事流程。先理解同事真正要解决的问题，再查找有权限的正式资料；涉及制度必须给出来源，资料不足时明确说明。",
            "domains": ["通用", "内部制度", "外部监管", "IT服务"],
            "responsibilities": ["回答公司内规与外规问题", "查询IT服务流程", "解释通用办事流程"],
        },
        "AI-INVESTMENT": {
            "name": "投资分析AI员工",
            "department": "投资分析",
            "persona": "你是投资分析AI员工，是公司中的投资分析专业同事。你负责证券业务、投行咨询和研究资料分析。结论必须由获授权资料支撑，区分资料事实与分析判断，不提供未经核实的信息。",
            "domains": ["证券业务", "投行咨询", "投资分析"],
            "responsibilities": ["分析证券业务", "检索投行咨询资料", "形成有出处的投资分析"],
        },
    }
    # 当前知识问答 MVP 只保留两名岗位型数字员工；真人分身继续保留。
    # 历史演示员工改为 inactive，以保留审计/历史关联但不再出现在通讯录中。
    active_role_employee_ids = set(employee_specs)
    for existing in db.query(models.DigitalEmployee).all():
        if existing.type != "twin" and existing.employee_no not in active_role_employee_ids:
            existing.status = "inactive"
        elif existing.type == "twin":
            existing.runtime_type = "harness"
            existing.runtime_ref = f"dwp-harness-{existing.employee_no.lower()}"
    for employee_id, spec in employee_specs.items():
        if db.get(models.DigitalEmployee, employee_id) is None:
            db.add(models.DigitalEmployee(
                employee_no=employee_id, name=spec["name"], type="virtual", owner_human_no="E10281",
                department=spec["department"], role_prompt=spec["persona"], runtime_type="harness",
                runtime_ref=f"dwp-harness-{employee_id.lower()}", location="remote", internet="deny",
                max_data_level="L2", allowed_domains=spec["domains"],
            ))
        else:
            employee = db.get(models.DigitalEmployee, employee_id)
            employee.name = spec["name"]
            employee.status = "active"
            employee.runtime_type = "harness"
            employee.runtime_ref = f"dwp-harness-{employee_id.lower()}"
    if db.get(models.KnowledgeBase, "KB-INVESTMENT-BANKING") is None:
        db.add(models.KnowledgeBase(
            id="KB-INVESTMENT-BANKING", name="投行咨询知识库", level="L2", data_level="L2",
            resource_type="knowledge", allowed_employment_type=["formal"], department_scope=["*"],
            domain="投行咨询", description="投行业务、尽调和项目咨询知识（虚构演示）",
            status="active", doc_path="mock-data/kb/investment-banking.md",
        ))
    db.flush()

    for employee in db.query(models.DigitalEmployee).all():
        spec = employee_specs.get(employee.employee_no)
        if db.get(models.AgentProfile, employee.employee_no) is None:
            db.add(models.AgentProfile(
                employee_id=employee.employee_no,
                identity_kind="human_twin" if employee.type == "twin" else "role_employee",
                responsibilities=spec["responsibilities"] if spec else ([employee.role_prompt] if employee.role_prompt else []),
                knowledge_domains=spec["domains"] if spec else list(employee.allowed_domains or []),
                accepts_tasks=["knowledge_question", "document_summary"],
                delegation_policy="bounded_single" if employee.type == "twin" else "none",
                fallback_employee_id="AI-GENERAL" if employee.type == "twin" else None,
            ))
        if db.get(models.AgentRuntime, employee.employee_no) is None:
            db.add(models.AgentRuntime(
                employee_id=employee.employee_no, engine="harness",
                container_name=f"dwp-harness-{employee.employee_no.lower().replace('_', '-')}",
                state="stopped", workspace_ref=f"backend/harness-workspaces/{employee.employee_no}",
            ))

    grant_specs = {
        "AI-GENERAL": [
            "KB-PUBLIC", "KB-ONBOARD", "KB-INTERNAL", "KB-FINTECH",
            "KB-IT-SERVICE", "KB-REG-INTERNAL", "KB-REG-EXTERNAL",
        ],
        "AI-INVESTMENT": ["KB-SECURITIES", "KB-INVESTMENT-BANKING"],
        # 张三的分身不直接持有知识库；遇到知识问题必须委派给岗位数字员工。
        "DT-E10281": [],
        "DT-E20999": ["KB-PUBLIC", "KB-ONBOARD", "KB-REG-EXTERNAL"],
        # Historical inactive demo identities remain callable by compatibility tests,
        # but are not shown in the active directory or provisioned as MVP containers.
        "VE-0001": ["KB-PUBLIC", "KB-ONBOARD"],
    }
    for employee_id, kb_ids in grant_specs.items():
        db.query(models.AgentKnowledgeGrant).filter(
            models.AgentKnowledgeGrant.employee_id == employee_id,
            models.AgentKnowledgeGrant.knowledge_base_id.not_in(kb_ids) if kb_ids else True,
        ).delete(synchronize_session=False)
        for kb_id in kb_ids:
            if db.scalar(select(models.AgentKnowledgeGrant).where(
                models.AgentKnowledgeGrant.employee_id == employee_id,
                models.AgentKnowledgeGrant.knowledge_base_id == kb_id,
            )) is None:
                db.add(models.AgentKnowledgeGrant(employee_id=employee_id, knowledge_base_id=kb_id))
        # 插件级知识许可也按具体库的数据等级重新生成，避免旧授权残留造成 UI 误导。
        db.query(models.EmployeePluginGrant).filter(
            models.EmployeePluginGrant.employee_id == employee_id,
            models.EmployeePluginGrant.plugin_id.in_(["knowledge-l1", "knowledge-l2", "knowledge-l3"]),
        ).delete(synchronize_session=False)
        levels = {
            db.get(models.KnowledgeBase, kb_id).data_level
            for kb_id in kb_ids
            if db.get(models.KnowledgeBase, kb_id) is not None
        }
        for data_level in sorted(levels):
            db.add(models.EmployeePluginGrant(
                employee_id=employee_id, plugin_id=f"knowledge-{data_level.lower()}", action="read",
                decision_mode="allow", grant_source="seed",
            ))

    # 历史 Skill/知识能力统一迁移为已发布 Plugin 1.0.0；保留原 ID 和审计关联。
    for plugin in db.query(models.Plugin).all():
        if plugin.plugin_type not in {"skill", "mcp"}:
            plugin.plugin_type = "mcp"
        if plugin.type == "knowledge":
            plugin.plugin_type = "mcp"
            plugin.mcp_category = "knowledge"
        elif plugin.type in {"workflow", "rpa"}:
            # Insecure no-auth mode exists solely for the legacy automated demo suite.
            # Real deployments require auth and keep these historical capabilities disabled.
            plugin.status = "active" if os.getenv("DWP_REQUIRE_AUTH", "1") == "0" else "disabled"
        elif plugin.type in {"http", "mcp"}:
            plugin.plugin_type = "mcp"
            plugin.mcp_category = plugin.mcp_category or ("cloud_information" if plugin.type == "http" else "internal_system")
        plugin.scope = plugin.scope or "shared"
        if plugin.current_version is None:
            plugin.current_version = "1.0.0"
        if db.scalar(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id == plugin.id,
            models.PluginVersion.version == plugin.current_version,
        )) is None:
            db.add(models.PluginVersion(
                plugin_id=plugin.id, version=plugin.current_version,
                deployment_mode="external", artifact_path="", sha256="seed",
                manifest={"tools": [{"name": "search_knowledge"}]} if plugin.mcp_category == "knowledge" else {"tools": []},
                data_level=plugin.data_level, review_status="approved", publish_status="published",
                submitted_by="migration", reviewed_by="migration", reviewed_at=__import__("datetime").datetime.now(),
                published_at=__import__("datetime").datetime.now(),
            ))
    for skill in db.query(models.Skill).all():
        plugin_id = f"SKILL-{skill.id}"
        if db.get(models.Plugin, plugin_id) is None:
            db.add(models.Plugin(
                id=plugin_id, name=skill.name, type="mcp", plugin_type="skill", scope="personal",
                owner_human_no=skill.owner_human_no, current_version="1.0.0", status="active",
                endpoint_ref="mock://", data_level="L1", description=skill.description,
            ))
            db.add(models.PluginVersion(
                plugin_id=plugin_id, version="1.0.0", deployment_mode="instruction",
                artifact_path="", sha256="migration", manifest={"instruction_summary": skill.content[:500]},
                data_level="L1", review_status="approved", publish_status="published",
                submitted_by=skill.owner_human_no, reviewed_by="migration",
                reviewed_at=__import__("datetime").datetime.now(), published_at=__import__("datetime").datetime.now(),
            ))
            twin_id = f"DT-{skill.owner_human_no}"
            if db.get(models.DigitalEmployee, twin_id):
                db.add(models.AgentPluginBinding(
                    plugin_id=plugin_id, target_agent_id=twin_id, authorized_by="migration",
                    employee_enabled=skill.status == "active", admin_enabled=True,
                ))
    db.flush()
    for grant in db.query(models.EmployeePluginGrant).all():
        plugin = db.get(models.Plugin, grant.plugin_id)
        if plugin and plugin.plugin_type == "mcp" and db.scalar(select(models.AgentPluginBinding).where(
            models.AgentPluginBinding.plugin_id == plugin.id,
            models.AgentPluginBinding.target_agent_id == grant.employee_id,
        )) is None:
            db.add(models.AgentPluginBinding(
                plugin_id=plugin.id, target_agent_id=grant.employee_id, authorized_by="migration",
                employee_enabled=True, admin_enabled=grant.decision_mode == "allow",
                decision_mode=grant.decision_mode,
            ))

    fallback_password = os.getenv("DWP_DEMO_PASSWORD", "Demo@123456")
    employee_password = _secret_value(
        "DWP_EMPLOYEE_INITIAL_PASSWORD", "DWP_EMPLOYEE_INITIAL_PASSWORD_FILE",
        "/run/secrets/employee_initial_password", fallback_password,
    )
    admin_password = _secret_value(
        "DWP_ADMIN_INITIAL_PASSWORD", "DWP_ADMIN_INITIAL_PASSWORD_FILE",
        "/run/secrets/admin_initial_password", fallback_password,
    )
    admin_human = db.get(models.HumanEmployee, "SYS-ADMIN")
    if admin_human is None:
        db.add(models.HumanEmployee(
            employee_no="SYS-ADMIN", name="平台管理员", department="平台管理",
            employment_type="system", status="hidden",
        ))
        db.flush()
    account_specs = {
        "E10281": ("E10281", ["user"], employee_password, "active"),
        "E20999": ("E20999", ["user"], employee_password, "active"),
        "admin": ("SYS-ADMIN", ["agent_admin", "security_admin", "platform_admin"], admin_password, "active"),
    }
    for username, (human_no, roles, password, status) in account_specs.items():
        account = db.scalar(select(models.Account).where(models.Account.username == username))
        if account is None:
            account = models.Account(username=username, password_hash=hash_password(password),
                                     human_employee_no=human_no, must_change_password=True)
            db.add(account)
        account.human_employee_no = human_no; account.roles = roles; account.status = status
    for account in db.query(models.Account).all():
        if account.username not in account_specs:
            account.status = "inactive"
            account.roles = []
    for human in db.query(models.HumanEmployee).filter(models.HumanEmployee.status == "active").all():
        if db.scalar(select(models.DirectoryBinding).where(
            models.DirectoryBinding.provider == "mock",
            models.DirectoryBinding.external_user_id == human.employee_no,
        )) is None:
            db.add(models.DirectoryBinding(
                provider="mock", corp_id="demo-corp", external_user_id=human.employee_no,
                human_employee_no=human.employee_no,
            ))
    db.commit()


def seed_if_empty() -> None:
    # seed/runtime-manager may run before FastAPI lifespan; always upgrade additive
    # SQLite columns before ORM queries so -NoReset can safely reuse an old database.
    Base.metadata.create_all(bind=engine)
    ensure_schema_compatibility()
    db = SessionLocal()
    try:
        if db.query(models.DigitalEmployee).count() == 0:
            seed_data(db, load_seed())
        else:
            ensure_mvp_seed(db)
    finally:
        db.close()


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
