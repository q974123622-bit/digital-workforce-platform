import json
import os
import sys
from pathlib import Path

from sqlalchemy import select

from . import models
from .database import DATABASE_URL, Base, SessionLocal, engine, ensure_schema_compatibility
from .services import config
from .services.auth import hash_password
from .services.external_mcp_adapter import QIEMAN_PLUGIN_ID, XYZQ_PLUGIN_ID

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
        "AI-RESEARCH": {
            "name": "研究分析AI员工",
            "department": "研究分析",
            "persona": "你是研究分析AI员工，是公司中的专业研究同事。你负责证券、投行与行业研究资料的整理分析。必须先理解同事真正要研究的问题，再检索获授权的研报与行业资料；结论区分资料事实与个人分析判断，注明资料出处，不提供未经核实或推测的信息。",
            "domains": ["研究分析", "证券研究", "投行研究", "行业资料"],
            "responsibilities": ["检索与分析研究资料", "梳理证券与投行研究观点", "输出有出处的分析结论"],
        },
        "AI-FUND": {
            "name": "基金分析AI员工",
            "department": "基金研究",
            "persona": "你是基金分析AI员工，是公司中的基金数据专业同事。你负责基金产品数据查询与专业分析，可调用盈米基金数据工具获取基金代码、业绩、归因、配置等数据。结论必须由工具返回的真实数据支撑，区分数据事实与分析判断，注明数据来源，不提供未经核实或推测的信息。",
            "domains": ["基金分析", "基金数据", "资产配置"],
            "responsibilities": ["查询基金产品数据", "分析基金业绩与归因", "输出有出处的基金分析结论"],
        },
        "AI-XYZQ": {
            "name": "兴业证券智能投顾助手",
            "department": "证券投顾",
            "persona": "你是兴业证券智能投顾助手，是公司中的证券投资咨询专业同事。你负责证券投资类咨询，可调用兴业证券智能投顾工具回答金融知识科普、股票/基金/理财咨询、投资规则解读、市场资讯参考、开户及业务办理指引等问题。结论必须由工具返回的真实数据支撑，注明数据来源，不提供未经核实或推测的信息。",
            "domains": ["证券投顾", "证券投资", "市场资讯"],
            "responsibilities": ["回答证券投资类咨询", "解读投资规则与市场资讯", "提供开户及业务办理指引"],
        },
    }
    # 当前知识问答 MVP 只保留三名岗位型数字员工；真人分身继续保留。
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
            # 内规（742）与外规（748）已从内部引擎下线，不再授予，避免检索报错。
            "KB-IT-SERVICE",
        ],
        "AI-INVESTMENT": ["KB-SECURITIES", "KB-INVESTMENT-BANKING"],
        "AI-RESEARCH": ["KB-SECURITIES", "KB-INVESTMENT-BANKING"],
        "AI-FUND": ["KB-SECURITIES"],
        "AI-XYZQ": ["KB-SECURITIES"],
        # 张三的分身不直接持有知识库；遇到知识问题必须委派给岗位数字员工。
        "DT-E10281": [],
        "DT-E20999": [],
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
        levels = {
            db.get(models.KnowledgeBase, kb_id).data_level
            for kb_id in kb_ids
            if db.get(models.KnowledgeBase, kb_id) is not None
        }
        expected_plugin_ids = {
            f"knowledge-{data_level.lower()}" for data_level in levels
        }
        db.query(models.AgentPluginBinding).filter(
            models.AgentPluginBinding.target_agent_id == employee_id,
            models.AgentPluginBinding.plugin_id.in_(["knowledge-l1", "knowledge-l2", "knowledge-l3"]),
            models.AgentPluginBinding.plugin_id.not_in(expected_plugin_ids) if expected_plugin_ids else True,
        ).delete(synchronize_session=False)
        db.query(models.EmployeePluginGrant).filter(
            models.EmployeePluginGrant.employee_id == employee_id,
            models.EmployeePluginGrant.plugin_id.in_(["knowledge-l1", "knowledge-l2", "knowledge-l3"]),
        ).delete(synchronize_session=False)
        for data_level in sorted(levels):
            db.add(models.EmployeePluginGrant(
                employee_id=employee_id, plugin_id=f"knowledge-{data_level.lower()}", action="read",
                decision_mode="allow", grant_source="seed",
            ))

    # 盈米 External MCP 插件：配置了 DWP_QIEMAN_MCP_URL 才注册并授权。
    # 工具由 ExternalMcpClient 动态发现（listChanged=true），Manifest 不静态声明 Schema。
    if config.qieman_mcp_configured():
        if db.get(models.Plugin, QIEMAN_PLUGIN_ID) is None:
            db.add(models.Plugin(
                id=QIEMAN_PLUGIN_ID, name="盈米基金数据 MCP", type="mcp",
                plugin_type="mcp", endpoint_ref="external://qieman", data_level="L2",
                status="active", description="盈米 AI 开放平台基金数据工具（External MCP）",
                runtime_meta={"mcp_url_ref": config.QIEMAN_MCP_URL},
                scope="shared", mcp_category="cloud_information", current_version="1.0.0",
            ))
            db.add(models.PluginVersion(
                plugin_id=QIEMAN_PLUGIN_ID, version="1.0.0",
                deployment_mode="external", artifact_path="", sha256="seed",
                manifest={"tools": [], "external_mcp": True},
                data_level="L2", review_status="approved", publish_status="published",
                submitted_by="seed", reviewed_by="seed",
                reviewed_at=__import__("datetime").datetime.now(),
                published_at=__import__("datetime").datetime.now(),
            ))
        db.flush()
        for employee_id in ("AI-INVESTMENT", "AI-RESEARCH", "AI-FUND"):
            if db.get(models.DigitalEmployee, employee_id) is None:
                continue
            if db.scalar(select(models.EmployeePluginGrant).where(
                models.EmployeePluginGrant.employee_id == employee_id,
                models.EmployeePluginGrant.plugin_id == QIEMAN_PLUGIN_ID,
            )) is None:
                db.add(models.EmployeePluginGrant(
                    employee_id=employee_id, plugin_id=QIEMAN_PLUGIN_ID, action="execute",
                    decision_mode="allow", grant_source="seed",
                ))
            if db.scalar(select(models.AgentPluginBinding).where(
                models.AgentPluginBinding.target_agent_id == employee_id,
                models.AgentPluginBinding.plugin_id == QIEMAN_PLUGIN_ID,
            )) is None:
                db.add(models.AgentPluginBinding(
                    plugin_id=QIEMAN_PLUGIN_ID, target_agent_id=employee_id,
                    authorized_by="seed", employee_enabled=True, admin_enabled=True,
                    decision_mode="allow",
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
        plugin = db.get(models.Plugin, plugin_id)
        if plugin is None:
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
        else:
            # 幂等修复历史遗留：plugin_type 对齐为 skill（旧库可能存成默认 "mcp"）
            plugin.name = skill.name
            plugin.description = skill.description
            plugin.plugin_type = "skill"
            plugin.status = "active"
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
    # 企业微信接入：按 DWP_WECOM_USER_BINDINGS（staff ID -> 员工工号）幂等初始化绑定。
    # bindings 是「企微账号 -> 平台真人」的全局映射，与具体机器人无关（多 bot 共用一份）。
    # 有任一机器人配置即同步；corp_id 仅存首个 bot_id 作标识，不做去重条件。
    wecom_bots = config.wecom_bots()
    if wecom_bots:
        wecom_corp_id = wecom_bots[0].bot_id
        for wecom_user_id, employee_no in config.wecom_user_bindings().items():
            if db.get(models.HumanEmployee, employee_no) is None:
                continue
            existing = db.scalar(select(models.DirectoryBinding).where(
                models.DirectoryBinding.provider == "wecom",
                models.DirectoryBinding.external_user_id == wecom_user_id,
            ))
            if existing is None:
                db.add(models.DirectoryBinding(
                    provider="wecom", corp_id=wecom_corp_id,
                    external_user_id=wecom_user_id, human_employee_no=employee_no,
                ))
            elif existing.human_employee_no != employee_no:
                # 重新绑定：更新真人映射并保持 active，避免陈旧指向。
                existing.human_employee_no = employee_no
                existing.status = "active"
    ensure_qieman_skills(db)
    ensure_xyzq_mcp(db)
    db.commit()


# 盈米 Skill 的中文显示名（load_skill 与 available_skills 用中文名，DSH 更易关联场景）
QIEMAN_SKILL_NAMES = {
    "fund-screener": "基金筛选",
    "portfolio-doctor": "组合诊断",
    "market-morning-brief": "市场早报",
    "fund-analyst": "基金分析",
    "advisor-content-studio": "投顾内容",
    "wealth-family-advisor": "财富规划",
    "wealth-goalcalc": "目标测算",
    "wealth-goalmatch": "目标匹配",
    "wealth-healthcheck": "财务体检",
    "wealth-report": "财富报告",
    "design-data-visualization": "数据可视化",
}


def ensure_qieman_skills(db) -> None:
    """接入盈米金融 Skill：读取 mock-data/qieman-skills.json，创建 Skill/插件并绑定 AI-FUND。

    盈米 Skill 是场景化 SOP（意图识别 + 工具编排 + 输出规范），通过 load_skill 显式加载。
    skill_id 使用 QM- 前缀区分平台演示 Skill（SK-），插件绑定到 AI-FUND（岗位员工）。
    """
    path = Path(__file__).resolve().parents[2] / "mock-data" / "qieman-skills.json"
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    now = __import__("datetime").datetime.now()
    for item in data:
        skill_id = f"QM-{item['id']}"
        plugin_id = f"SKILL-{skill_id}"
        display_name = QIEMAN_SKILL_NAMES.get(item["id"], item["name"])
        # Skill 记录（幂等：已存在则更新 name/content/description）
        skill = db.get(models.Skill, skill_id)
        if skill is None:
            db.add(models.Skill(
                id=skill_id, owner_human_no="SYS-ADMIN", name=display_name,
                description=item["description"], content=item["content"], status="active",
            ))
        else:
            skill.name = display_name
            skill.description = item["description"]
            skill.content = item["content"]
            skill.status = "active"
        # Plugin 记录（幂等：已存在则更新 name/description）
        # scope 用 shared：skill 是团队共享能力，若用 personal + SYS-ADMIN owner
        # 会被 effective_plugins 按 owner 过滤掉，导致 available_skills 为空。
        plugin = db.get(models.Plugin, plugin_id)
        if plugin is None:
            db.add(models.Plugin(
                id=plugin_id, name=display_name, type="mcp", plugin_type="skill",
                scope="shared", owner_human_no="SYS-ADMIN", status="active",
                description=item["description"], current_version="1.0.0",
            ))
            db.add(models.PluginVersion(
                plugin_id=plugin_id, version="1.0.0", deployment_mode="instruction",
                artifact_path="", sha256="qieman",
                manifest={"instruction_summary": item["description"]},
                data_level="L2", review_status="approved", publish_status="published",
                submitted_by="qieman", reviewed_by="qieman", reviewed_at=now, published_at=now,
            ))
        else:
            plugin.name = display_name
            plugin.description = item["description"]
            plugin.plugin_type = "skill"
            plugin.scope = "shared"
            plugin.status = "active"
        if db.scalar(select(models.AgentPluginBinding).where(
            models.AgentPluginBinding.plugin_id == plugin_id,
            models.AgentPluginBinding.target_agent_id == "AI-FUND",
        )) is None:
            db.add(models.AgentPluginBinding(
                plugin_id=plugin_id, target_agent_id="AI-FUND",
                authorized_by="qieman", employee_enabled=True, admin_enabled=True,
                decision_mode="allow",
            ))


def ensure_xyzq_mcp(db) -> None:
    """接入兴业证券 External MCP：注册 xyzq-mcp-server 插件并授权 AI-XYZQ（与盈米隔离）。

    工具由 ExternalMcpClient 动态发现（listChanged=true），Manifest 不静态声明 Schema。
    匿名可调（无 api_key），响应为 SSE（text/event-stream）。
    """
    if not config.xyzq_mcp_configured():
        return
    if db.get(models.Plugin, XYZQ_PLUGIN_ID) is None:
        db.add(models.Plugin(
            id=XYZQ_PLUGIN_ID, name="兴业证券智能投顾 MCP", type="mcp",
            plugin_type="mcp", endpoint_ref="external://xyzq", data_level="L2",
            status="active", description="兴业证券智能投顾助手（External MCP，chat 工具，匿名）",
            runtime_meta={"mcp_url_ref": config.XYZQ_MCP_URL},
            scope="shared", mcp_category="cloud_information", current_version="1.0.0",
        ))
        db.add(models.PluginVersion(
            plugin_id=XYZQ_PLUGIN_ID, version="1.0.0",
            deployment_mode="external", artifact_path="", sha256="seed",
            manifest={"tools": [], "external_mcp": True},
            data_level="L2", review_status="approved", publish_status="published",
            submitted_by="seed", reviewed_by="seed",
            reviewed_at=__import__("datetime").datetime.now(),
            published_at=__import__("datetime").datetime.now(),
        ))
    if db.scalar(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == XYZQ_PLUGIN_ID,
        models.AgentPluginBinding.target_agent_id == "AI-XYZQ",
    )) is None:
        db.add(models.AgentPluginBinding(
            plugin_id=XYZQ_PLUGIN_ID, target_agent_id="AI-XYZQ",
            authorized_by="seed", employee_enabled=True, admin_enabled=True,
            decision_mode="allow",
        ))


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
