import json
import os
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from . import models
from .database import DATABASE_URL, Base, SessionLocal, engine, ensure_schema_compatibility
from .services import config, knowledge_catalog
from .services.auth import hash_password, verify_password
from .services.external_mcp_adapter import EHOME_PLUGIN_ID, QIEMAN_PLUGIN_ID, XYZQ_PLUGIN_ID

SEED_PATH = Path(__file__).resolve().parents[2] / "mock-data" / "seed.json"

# 历史演示/虚构知识库：9-14 之前的 seed.json 灌入，老库升级后不会自动消失，需按显式黑名单清理。
# 只列虚构库，绝不用「只保留真实库」的白名单——老库里还有内部知识引擎的真实库
# （数据治理/智慧纪检/财务报账/运营管理/立项采购），白名单会误删。
LEGACY_MOCK_KNOWLEDGE_BASE_IDS: tuple[str, ...] = (
    "KB-PUBLIC",
    "KB-ONBOARD",
    "KB-INTERNAL",
    "KB-FINTECH",
    "KB-REG-INTERNAL",
    "KB-REG-EXTERNAL",
    "KB-CUSTOMER-SENSITIVE",
    # 更早版本的演示知识库
    "KB-L1-PUB",
    "KB-L2-HR",
    "KB-L2-IT",
    "KB-L2-FIN",
)

# 历史分级 mock 插件：知识访问已改为 Policy 按 data_level 分级，不再需要这些插件入口。
LEGACY_KNOWLEDGE_PLUGIN_IDS: tuple[str, ...] = ("knowledge-l1", "knowledge-l2", "knowledge-l3")

# 真实知识库的权威文案：仅当存量描述仍带演示字样时才纠正，避免覆盖人工编辑内容。
# 文案与目录同源（app/services/knowledge_catalog.py），避免两处漂移。
REAL_KB_CANONICAL_DESCRIPTIONS: dict[str, str] = {
    entry["id"]: entry["description"] for entry in knowledge_catalog.KB_CATALOG
}

_DEMO_DESCRIPTION_MARKERS: tuple[str, ...] = ("虚构", "演示", "Demo", "VE-0001")


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
        models.McpCredential,
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
        models.ConversationMessage,
        models.Conversation,
        models.Skill,
        models.TaskRun,
        models.AuditEvent,
        models.TeamMember,
        models.Team,
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


def purge_legacy_mock_knowledge_bases(db) -> dict[str, int]:
    """幂等清理历史 mock 知识库及其授权/访问申请，并纠正真实库描述里的演示字样。

    老库（9-14 之前灌入）里的虚构知识库不会被 ensure_mvp_seed 删除，因此这里按显式黑名单清理；
    新库为空跑。第二次启动影响行数为 0。审计事件保留（历史留痕）。
    """
    mock_kb_ids = list(LEGACY_MOCK_KNOWLEDGE_BASE_IDS)
    removed_grants = db.query(models.AgentKnowledgeGrant).filter(
        models.AgentKnowledgeGrant.knowledge_base_id.in_(mock_kb_ids),
    ).delete(synchronize_session=False)
    removed_requests = db.query(models.AccessRequest).filter(
        models.AccessRequest.resource_type == "knowledge",
        models.AccessRequest.resource_id.in_(mock_kb_ids),
    ).delete(synchronize_session=False)
    removed_kbs = db.query(models.KnowledgeBase).filter(
        models.KnowledgeBase.id.in_(mock_kb_ids),
    ).delete(synchronize_session=False)

    # 历史 mock 知识库插件：先删派生记录（版本评审/构建任务/MCP 实例），再删版本与插件本体。
    version_ids = list(db.scalars(select(models.PluginVersion.id).where(
        models.PluginVersion.plugin_id.in_(LEGACY_KNOWLEDGE_PLUGIN_IDS),
    )).all())
    if version_ids:
        for model in (models.PluginReview, models.PluginBuildJob, models.McpRuntimeInstance):
            db.query(model).filter(model.plugin_version_id.in_(version_ids)).delete(synchronize_session=False)
        db.query(models.PluginVersion).filter(
            models.PluginVersion.id.in_(version_ids),
        ).delete(synchronize_session=False)
    db.query(models.AgentPluginBinding).filter(
        models.AgentPluginBinding.plugin_id.in_(LEGACY_KNOWLEDGE_PLUGIN_IDS),
    ).delete(synchronize_session=False)
    # 自助接入 MCP 的鉴权凭据随插件本体一起清理，避免留下无主密钥。
    db.query(models.McpCredential).filter(
        models.McpCredential.plugin_id.in_(LEGACY_KNOWLEDGE_PLUGIN_IDS),
    ).delete(synchronize_session=False)
    db.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.plugin_id.in_(LEGACY_KNOWLEDGE_PLUGIN_IDS),
    ).delete(synchronize_session=False)
    removed_plugins = db.query(models.Plugin).filter(
        models.Plugin.id.in_(LEGACY_KNOWLEDGE_PLUGIN_IDS),
    ).delete(synchronize_session=False)

    corrected_descriptions = 0
    for kb_id, canonical in REAL_KB_CANONICAL_DESCRIPTIONS.items():
        kb = db.get(models.KnowledgeBase, kb_id)
        if kb is None or (kb.description or "") == canonical:
            continue
        if any(marker in (kb.description or "") for marker in _DEMO_DESCRIPTION_MARKERS):
            kb.description = canonical
            corrected_descriptions += 1

    return {
        "knowledge_bases": removed_kbs,
        "knowledge_grants": removed_grants,
        "access_requests": removed_requests,
        "knowledge_plugins": removed_plugins,
        "corrected_descriptions": corrected_descriptions,
    }


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
        "AI-AUDIT": {
            "name": "AI-审核",
            "department": "运营支持",
            "persona": "你是AI-审核，负责机构开户业务的材料比对审核。你的工作是逐字段核对系统录入信息与客户提交材料，把一致、不一致、待确认项分开列出来，并给出每一条结论的材料来源与位置。你只提示线索，不做通过或拒绝的判定；所有结论都要标注「机器提示，以人工审核为准」。看不清、读不到、印章遮挡的内容如实说看不清，不要猜。客户材料的证件号、账号等信息不得写入日志或外发。",
            "domains": ["运营审核", "开户材料", "机构业务"],
            "responsibilities": ["清点并核对开户材料", "逐字段比对系统录入与客户材料", "输出带来源与位置的审核线索"],
        },
    }
    avatar_urls = {
        "DT-E10281": "/avatars/dt-e10281.webp",
        "DT-E20999": "/avatars/dt-e20999.webp",
        "AI-GENERAL": "/avatars/ai-general.webp",
        "AI-INVESTMENT": "/avatars/ai-investment.webp",
        "AI-RESEARCH": "/avatars/ai-research.webp",
        "AI-FUND": "/avatars/ai-fund.webp",
        "AI-XYZQ": "/avatars/ai-xyzq.webp",
        "AI-AUDIT": "/avatars/ai-audit.webp",
    }
    # 知识问答 MVP 保留岗位型AI员工 + 真人分身；历史 VE/RPA 演示员工已从种子数据移除。
    active_role_employee_ids = set(employee_specs)
    for existing in db.query(models.DigitalEmployee).all():
        if existing.employee_no in avatar_urls:
            existing.avatar_url = avatar_urls[existing.employee_no]
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
                avatar_url=avatar_urls.get(employee_id),
            ))
        else:
            employee = db.get(models.DigitalEmployee, employee_id)
            employee.name = spec["name"]
            employee.status = "active"
            employee.runtime_type = "harness"
            employee.runtime_ref = f"dwp-harness-{employee_id.lower()}"
    # 知识库目录收敛：每个业务主题一个语义化英文 ID，行上带双后端映射
    # （测试环境 REST / 生产环境 VeRAG MCP），授权只配一次、换环境不改。
    # 幂等 upsert：已存在的库只更新名称/等级/映射/文案，不删除（历史授权留痕）。
    knowledge_catalog.assert_exclusive()
    for entry in knowledge_catalog.KB_CATALOG:
        engine_map = knowledge_catalog.engine_map_of(entry)
        kb = db.get(models.KnowledgeBase, entry["id"])
        if kb is None:
            db.add(models.KnowledgeBase(
                id=entry["id"], name=entry["name"], level=entry["data_level"],
                data_level=entry["data_level"], resource_type="knowledge",
                allowed_employment_type=["formal"], department_scope=["*"],
                domain=entry["domain"], description=entry["description"],
                status="active", engine_map=engine_map,
            ))
            continue
        kb.name = entry["name"]
        kb.level = entry["data_level"]
        kb.data_level = entry["data_level"]
        kb.domain = entry["domain"]
        kb.status = "active"
        if engine_map:
            kb.engine_map = engine_map
        if kb.description and any(marker in kb.description for marker in _DEMO_DESCRIPTION_MARKERS):
            kb.description = entry["description"]
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

    # 独占分配：任意两个 AI 员工的知识库集合两两不相交（见 knowledge_catalog.GRANT_SPECS）。
    # 每个员工的授权按「整体替换」收敛：先删掉不在目标集合里的旧授权，再补新增。
    # 存量库升级时会留下旧口径的交叉授权（例如 AI-FUND/AI-XYZQ 曾共用 KB-SECURITIES），
    # 这里必须显式删除，否则「两两不相交」在升级库上不成立。
    grant_specs = knowledge_catalog.grant_specs()
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

    # 历史 knowledge-l1/l2/l3 mock 插件已删除，清理其残留的绑定与授权记录。
    # 知识访问现在由 Policy（data_level 分级）+ AgentKnowledgeGrant 控制，不再依赖分级插件。
    db.query(models.AgentPluginBinding).filter(
        models.AgentPluginBinding.plugin_id.in_(["knowledge-l1", "knowledge-l2", "knowledge-l3"]),
    ).delete(synchronize_session=False)
    db.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.plugin_id.in_(["knowledge-l1", "knowledge-l2", "knowledge-l3"]),
    ).delete(synchronize_session=False)

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
                id=plugin_id, name=skill.name, type="skill", plugin_type="skill", scope="personal",
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
    login_keys = {
        "E10281": _secret_value("DWP_LOGIN_KEY_E10281", "DWP_LOGIN_KEY_E10281_FILE", "/run/secrets/login_key_e10281", "").strip(),
        "E20999": _secret_value("DWP_LOGIN_KEY_E20999", "DWP_LOGIN_KEY_E20999_FILE", "/run/secrets/login_key_e20999", "").strip(),
        "admin": _secret_value("DWP_LOGIN_KEY_ADMIN", "DWP_LOGIN_KEY_ADMIN_FILE", "/run/secrets/login_key_admin", "").strip(),
    }
    if os.getenv("DWP_REQUIRE_AUTH", "1") == "1":
        missing = [name for name, value in login_keys.items() if len(value) < 12]
        if missing:
            raise RuntimeError("访问密钥未配置或长度不足 12 个字符：" + ", ".join(missing))
        if len(set(login_keys.values())) != len(login_keys):
            raise RuntimeError("三个账号的访问密钥不能重复")
    for username, (human_no, roles, password, status) in account_specs.items():
        account = db.scalar(select(models.Account).where(models.Account.username == username))
        if account is None:
            account = models.Account(username=username, password_hash=hash_password(password),
                                     human_employee_no=human_no, must_change_password=False)
            db.add(account)
        access_key = login_keys.get(username, "")
        if access_key and not verify_password(access_key, account.login_key_hash or ""):
            account.login_key_hash = hash_password(access_key)
        account.human_employee_no = human_no; account.roles = roles; account.status = status
        account.must_change_password = False
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
    ensure_ehome_mcp(db)
    converge_plugin_governance(db)
    purge_result = purge_legacy_mock_knowledge_bases(db)
    if any(purge_result.values()):
        # 一次性升级清理，直接输出到 stdout，便于在 docker logs 确认生效。
        print(f"[seed] 已清理历史 mock 知识库：{purge_result}", flush=True)
    db.commit()


# 盈米 Skill 的中文显示名（前置路由用中文名，DSH 更易关联场景）
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

    盈米 Skill 是场景化 SOP（意图识别 + 工具编排 + 输出规范），通过前置路由注入任务上下文。
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
        # 会被 effective_plugins 按 owner 过滤掉，导致前置路由无法命中。
        plugin = db.get(models.Plugin, plugin_id)
        if plugin is None:
            db.add(models.Plugin(
                id=plugin_id, name=display_name, type="skill", plugin_type="skill",
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


def ensure_ehome_mcp(db) -> None:
    """接入兴证e家员工服务 External MCP：注册插件并授权 AI-XYZQ 与两个数字分身。

    双鉴权头：应用级 ``X-MCP-API-KEY``（运维预置）+ 用户级 ``X-MCP-JWT``（按人签发、
    会过期，由员工在会话内的「信息补全」气泡里粘贴一次、加密存到本人名下后复用）。
    工具由 ExternalMcpClient 动态发现，Manifest 不静态声明 Schema。

    ``runtime_meta`` 刻意**只放 ``mcp_url_ref``**：内置适配器已按 ``config`` 构造，
    若这里同时写 ``url``/``tool_prefix``，``mcp_registry.external_plugins`` 会再构造一个
    重复的动态适配器（与兴业证券现状保持一致）。
    """
    if not config.ehome_mcp_configured():
        return
    if db.get(models.Plugin, EHOME_PLUGIN_ID) is None:
        db.add(models.Plugin(
            id=EHOME_PLUGIN_ID, name="兴证e家员工服务 MCP", type="mcp",
            plugin_type="mcp", endpoint_ref="external://xyzq-ehome", data_level="L2",
            status="active",
            description=(
                "兴证e家员工服务（会议室、餐卡、考勤休假、通讯录、资讯、OA 送审等查询类工具，"
                "External MCP，按人鉴权）"
            ),
            runtime_meta={"mcp_url_ref": config.EHOME_MCP_URL},
            scope="shared", mcp_category="employee_service", current_version="1.0.0",
        ))
        db.add(models.PluginVersion(
            plugin_id=EHOME_PLUGIN_ID, version="1.0.0",
            deployment_mode="external", artifact_path="", sha256="seed",
            manifest={"tools": [], "external_mcp": True, "user_credential": True},
            data_level="L2", review_status="approved", publish_status="published",
            submitted_by="seed", reviewed_by="seed",
            reviewed_at=datetime.now(), published_at=datetime.now(),
        ))
    for target in ("AI-XYZQ", "DT-E10281", "DT-E20999"):
        if db.scalar(select(models.AgentPluginBinding).where(
            models.AgentPluginBinding.plugin_id == EHOME_PLUGIN_ID,
            models.AgentPluginBinding.target_agent_id == target,
        )) is None:
            db.add(models.AgentPluginBinding(
                plugin_id=EHOME_PLUGIN_ID, target_agent_id=target,
                authorized_by="seed", employee_enabled=True, admin_enabled=True,
                decision_mode="allow",
            ))


def converge_plugin_governance(db) -> dict[str, int]:
    """幂等收敛插件治理历史数据（每次启动执行一次）。

    技能可用性的唯一口径已收敛为「binding → ``Plugin(plugin_type='skill')`` → 已发布
    ``PluginVersion.manifest['instruction']``」。老库里的技能有三处不合规：

    1. 插件 ``type`` 仍是历史值 ``mcp``（新代码按 ``plugin_type`` 判定，但契约/前端仍读 ``type``）；
    2. 版本 manifest 只写了 ``instruction_summary``（摘要），没有 ``instruction``（正文）
       —— 直接切换口径会让盈米 10 个技能集体从提示词里消失；
    3. 部分 ``Skill`` 行缺少对应插件/版本/绑定。

    这里只做条件更新与缺失补齐，**绝不删除任何 plugin 行**；重复执行影响行数为 0。
    输出仅含计数，不含正文或凭据。
    """
    stats = {
        "type_fixed": 0, "instruction_backfilled": 0,
        "plugin_created": 0, "version_created": 0, "binding_created": 0,
    }
    now = datetime.now()
    # 本函数前序步骤（如 ensure_qieman_skills）可能刚 add 了 Skill/Plugin 行，
    # 会话是 autoflush=False，不显式 flush 的话下面的查询看不到它们。
    db.flush()

    for skill in db.query(models.Skill).all():
        plugin_id = f"SKILL-{skill.id}"
        plugin = db.get(models.Plugin, plugin_id)
        if plugin is None:
            plugin = models.Plugin(
                id=plugin_id, name=skill.name, type="skill", plugin_type="skill",
                scope="personal", owner_human_no=skill.owner_human_no, status="active",
                description=skill.description, current_version="1.0.0",
                endpoint_ref="mock://", data_level="L1",
            )
            db.add(plugin)
            db.flush()
            stats["plugin_created"] += 1
        else:
            if plugin.plugin_type != "skill":
                plugin.plugin_type = "skill"
                stats["type_fixed"] += 1
            if plugin.type != "skill":
                plugin.type = "skill"
                stats["type_fixed"] += 1
            if not plugin.current_version:
                plugin.current_version = "1.0.0"

        version_no = plugin.current_version or "1.0.0"
        version = db.scalar(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id == plugin_id,
            models.PluginVersion.version == version_no,
        ))
        if version is None:
            db.add(models.PluginVersion(
                plugin_id=plugin_id, version=version_no, deployment_mode="instruction",
                artifact_path="", sha256="convergence",
                manifest={
                    "instruction_summary": skill.description[:500],
                    "instruction": skill.content[:8000],
                },
                data_level=plugin.data_level or "L1", review_status="approved",
                publish_status="published", submitted_by=skill.owner_human_no,
                reviewed_by="convergence", reviewed_at=now, published_at=now,
            ))
            stats["version_created"] += 1
        elif not str((version.manifest or {}).get("instruction") or "").strip():
            manifest = dict(version.manifest or {})
            manifest["instruction"] = skill.content[:8000]
            manifest.setdefault("instruction_summary", skill.description[:500])
            version.manifest = manifest
            stats["instruction_backfilled"] += 1

        twin_id = f"DT-{skill.owner_human_no}" if skill.owner_human_no else ""
        if twin_id and db.get(models.DigitalEmployee, twin_id) is not None:
            binding = db.scalar(select(models.AgentPluginBinding).where(
                models.AgentPluginBinding.plugin_id == plugin_id,
                models.AgentPluginBinding.target_agent_id == twin_id,
            ))
            if binding is None:
                db.add(models.AgentPluginBinding(
                    plugin_id=plugin_id, target_agent_id=twin_id, authorized_by="convergence",
                    admin_enabled=True, employee_enabled=skill.status == "active",
                    decision_mode="allow",
                ))
                stats["binding_created"] += 1

    if any(stats.values()):
        print(f"[seed] 插件治理收敛：{stats}", flush=True)
    return stats


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
    print("seed ok")


if __name__ == "__main__":
    main()
