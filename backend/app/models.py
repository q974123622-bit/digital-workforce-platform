from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class HumanEmployee(Base):
    __tablename__ = "human_employee"

    employee_no: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    department: Mapped[str] = mapped_column(String, default="")
    employment_type: Mapped[str] = mapped_column(String)  # formal | intern
    status: Mapped[str] = mapped_column(String, default="active")


class DigitalEmployee(Base):
    __tablename__ = "digital_employee"

    employee_no: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)  # twin | virtual | rpa
    source_human_no: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_human_no: Mapped[str] = mapped_column(String)
    department: Mapped[str] = mapped_column(String, default="")
    role_prompt: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="active")
    runtime_type: Mapped[str] = mapped_column(String, default="demo")
    runtime_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str] = mapped_column(String, default="remote")
    internet: Mapped[str] = mapped_column(String, default="deny")
    max_data_level: Mapped[str] = mapped_column(String, default="L1")
    allowed_domains: Mapped[list] = mapped_column(JSON, default=list)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
class Plugin(Base):
    __tablename__ = "plugin"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)  # knowledge | mcp | workflow | rpa | http
    endpoint_ref: Mapped[str] = mapped_column(String, default="mock://")
    data_level: Mapped[str] = mapped_column(String, default="L1")
    status: Mapped[str] = mapped_column(String, default="active")
    description: Mapped[str] = mapped_column(String, default="")
    runtime_meta: Mapped[dict] = mapped_column(JSON, default=dict)  # mcpServer/tool 等运行时元数据
    # 统一插件模型。type 暂时保留用于兼容旧接口，新增代码只使用 plugin_type。
    plugin_type: Mapped[str] = mapped_column(String, default="mcp")  # skill | mcp
    scope: Mapped[str] = mapped_column(String, default="shared")  # personal | shared
    owner_human_no: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    mcp_category: Mapped[str | None] = mapped_column(String, nullable=True)
    current_version: Mapped[str | None] = mapped_column(String, nullable=True)
    # 技能业务分类（19 类词表之一，来自 tags 映射或管理员人工标注）。
    skill_category: Mapped[str | None] = mapped_column(String, nullable=True)
    # 运营推荐位：为真时在员工货架置顶并打「精选」标。
    featured: Mapped[bool] = mapped_column(default=False)


class PluginVersion(Base):
    __tablename__ = "plugin_version"
    # 同一插件内版本号必须唯一：否则 publish/rollback 无法定位版本，
    # 且 ``submit_zip`` 的任意 version 会造成同名版本互相覆盖。
    # 索引名与 database.ensure_schema_compatibility 中创建的一致，避免重复建索引。
    __table_args__ = (Index("uq_plugin_version", "plugin_id", "version", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[str] = mapped_column(String)
    deployment_mode: Mapped[str] = mapped_column(String, default="instruction")
    artifact_path: Mapped[str] = mapped_column(String, default="")
    sha256: Mapped[str] = mapped_column(String, default="")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    data_level: Mapped[str] = mapped_column(String, default="L1")
    review_status: Mapped[str] = mapped_column(String, default="pending")
    publish_status: Mapped[str] = mapped_column(String, default="draft")
    submitted_by: Mapped[str] = mapped_column(String)
    reviewed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    review_note: Mapped[str] = mapped_column(String, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PluginReview(Base):
    __tablename__ = "plugin_review"

    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_version_id: Mapped[int] = mapped_column(index=True)
    decision: Mapped[str] = mapped_column(String)
    reviewer: Mapped[str] = mapped_column(String)
    note: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AgentPluginBinding(Base):
    __tablename__ = "agent_plugin_binding"

    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String, index=True)
    target_agent_id: Mapped[str] = mapped_column(String, index=True)
    pinned_version: Mapped[str | None] = mapped_column(String, nullable=True)
    authorized_by: Mapped[str] = mapped_column(String, default="")
    employee_enabled: Mapped[bool] = mapped_column(default=True)
    admin_enabled: Mapped[bool] = mapped_column(default=True)
    decision_mode: Mapped[str] = mapped_column(String, default="allow")
    priority: Mapped[int] = mapped_column(default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PluginBuildJob(Base):
    __tablename__ = "plugin_build_job"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    plugin_version_id: Mapped[int] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    runtime: Mapped[str] = mapped_column(String, default="")
    attempts: Mapped[int] = mapped_column(default=0)
    error_summary: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class McpRuntimeInstance(Base):
    __tablename__ = "mcp_runtime_instance"

    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_version_id: Mapped[int] = mapped_column(index=True)
    container_name: Mapped[str] = mapped_column(String, default="")
    state: Mapped[str] = mapped_column(String, default="mock")
    health: Mapped[str] = mapped_column(String, default="unknown")
    last_error: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class McpCredential(Base):
    """自助接入 MCP 的鉴权凭据（只存密文与掩码，明文永不落库）。

    ``plugin_id`` 与 ``plugin.id`` 一一对应；非敏感运行信息（地址、头名、超时、
    工具前缀、探测结果）放在 ``Plugin.runtime_meta``，本表只承载密钥。
    """

    __tablename__ = "mcp_credential"

    plugin_id: Mapped[str] = mapped_column(String, primary_key=True)
    header_name: Mapped[str] = mapped_column(String, default="x-api-key")
    secret_ciphertext: Mapped[str] = mapped_column(String, default="")
    secret_hint: Mapped[str] = mapped_column(String, default="")
    updated_by: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class McpUserCredential(Base):
    """按人存储的 MCP 用户级凭据（如兴证e家的 ``X-MCP-JWT``）。

    ``McpCredential`` 是「一插件一密钥」的应用级凭据；用户级凭据按人签发、会过期，
    因此单独建表，主键 ``(plugin_id, human_no)``。明文永不落库，接口只回掩码。
    """

    __tablename__ = "mcp_user_credential"

    plugin_id: Mapped[str] = mapped_column(String, primary_key=True)
    human_no: Mapped[str] = mapped_column(String, primary_key=True)
    header_name: Mapped[str] = mapped_column(String, default="X-MCP-JWT")
    secret_ciphertext: Mapped[str] = mapped_column(String, default="")
    secret_hint: Mapped[str] = mapped_column(String, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class EmployeePluginGrant(Base):
    __tablename__ = "employee_plugin_grant"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[str] = mapped_column(String, index=True)
    plugin_id: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String, default="read")
    decision_mode: Mapped[str] = mapped_column(String, default="allow")  # allow | deny | approval
    grant_source: Mapped[str] = mapped_column(String, default="")  # seed | whitelist | manual


class Policy(Base):
    __tablename__ = "policy"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    effect: Mapped[str] = mapped_column(String)  # allow | deny | approval
    description: Mapped[str] = mapped_column(String, default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    priority: Mapped[int] = mapped_column(default=0)


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[str] = mapped_column(String, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    actor: Mapped[str] = mapped_column(String, default="")
    employee_id: Mapped[str | None] = mapped_column(String, nullable=True)
    team_id: Mapped[str | None] = mapped_column(String, nullable=True)
    plugin_id: Mapped[str | None] = mapped_column(String, nullable=True)
    knowledge_base_id: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, default="")
    decision: Mapped[str] = mapped_column(String, default="deny")
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(String, nullable=True)


class Team(Base):
    __tablename__ = "team"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    leader_employee_id: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="")


class TeamMember(Base):
    __tablename__ = "team_member"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(String, index=True)
    employee_id: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="worker")


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String, default="L1")
    data_level: Mapped[str] = mapped_column(String, default="L1")
    resource_type: Mapped[str] = mapped_column(String, default="knowledge")
    allowed_employment_type: Mapped[list] = mapped_column(JSON, default=list)
    department_scope: Mapped[list] = mapped_column(JSON, default=list)
    domain: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="active")
    doc_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # 双后端映射：{"rest": 751, "verag": 623}（远端库在某一环境不存在时对应键缺失）。
    # 远端库 ID 落库而不再放环境变量，才能表达「一个平台库挂两条通道」。
    engine_map: Mapped[dict] = mapped_column(JSON, default=dict)


class KnowledgeSourceHealth(Base):
    """知识源/MCP 工具的最近一次可达性探测结果。

    ``source`` 取值：``rest`` / ``verag`` / ``mcp:<plugin_id>``；
    ``state`` 取值：``available`` / ``unavailable`` / ``unknown``。
    该表是「不可达即摘除工具、目录保留但置灰」的唯一判据，定时探测与
    管理端手动重测都写这里。
    """

    __tablename__ = "knowledge_source_health"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String, unique=True, index=True)
    state: Mapped[str] = mapped_column(String, default="unknown")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ChatSession(Base):
    __tablename__ = "chat_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    employee_id: Mapped[str] = mapped_column(String)
    trace_id: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    role: Mapped[str] = mapped_column(String)  # user | assistant | tool
    content: Mapped[str] = mapped_column(String, default="")
    tool_cards: Mapped[list] = mapped_column(JSON, default=list)


class TaskRun(Base):
    __tablename__ = "task_run"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    team_id: Mapped[str] = mapped_column(String)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    trigger_message_seq: Mapped[int | None] = mapped_column(nullable=True)
    trace_id: Mapped[str] = mapped_column(String, default="")
    request: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="pending")
    subtasks: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(String, default="")
    source: Mapped[str] = mapped_column(String, default="builtin")  # builtin
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AccessRequest(Base):
    """L3 敏感资源读取白名单申请：pending -> granted/rejected。"""

    __tablename__ = "access_request"

    id: Mapped[int] = mapped_column(primary_key=True)
    applicant_no: Mapped[str] = mapped_column(String, index=True)
    resource_type: Mapped[str] = mapped_column(String)  # knowledge | plugin
    resource_id: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="pending")
    approval_chain: Mapped[list] = mapped_column(JSON, default=list)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# ---- Sprint 7：个人工作中心（职场）----


class Skill(Base):
    """员工上传给数字分身的技能（文本/Markdown，注入分身人设）。"""

    __tablename__ = "skill"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_human_no: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="")
    content: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="active")  # active | disabled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SkillHubImport(Base):
    """SkillHub 技能导入记录：幂等判据与导入结果留痕。

    同一个 ``(slug, version, sha256)`` 只导入一次；``status`` 为
    ``imported`` / ``skipped`` / ``failed``，失败原因写入 ``message`` 供管理端展示。
    """

    __tablename__ = "skillhub_import"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String, index=True)
    # 批量导入批次号：同一次批量导入的所有记录共享，供前端轮询进度。
    batch_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    version: Mapped[str] = mapped_column(String, default="")
    source: Mapped[str] = mapped_column(String, default="")
    display_name: Mapped[str] = mapped_column(String, default="")
    category: Mapped[str] = mapped_column(String, default="")
    plugin_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    sha256: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="pending")
    message: Mapped[str] = mapped_column(String, default="")
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    instruction_chars: Mapped[int] = mapped_column(Integer, default=0)
    imported_by: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Conversation(Base):
    """职场会话：私聊（direct）与协作群聊（group）统一承载。"""

    __tablename__ = "conversation"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, default="direct")  # direct | group
    title: Mapped[str] = mapped_column(String, default="")
    owner_human_no: Mapped[str] = mapped_column(String, index=True)
    participants: Mapped[list] = mapped_column(JSON, default=list)
    generation: Mapped[int] = mapped_column(default=0)
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ConversationMessage(Base):
    """职场会话消息：user 为员工本人，assistant 为数字成员回复。"""

    __tablename__ = "conversation_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    participant_no: Mapped[str] = mapped_column(String, default="")
    participant_name: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default="user")  # user | assistant
    content: Mapped[str] = mapped_column(String, default="")
    tool_cards: Mapped[list] = mapped_column(JSON, default=list)
    attachment_ids: Mapped[list] = mapped_column(JSON, default=list)
    seq: Mapped[int] = mapped_column(default=0)
    generation: Mapped[int] = mapped_column(default=0, index=True)
    # 就地重跑后，被新回答取代的旧回复不删除，只标记作废：模型上下文不再读取它，
    # 界面折叠成「上一版回复」供回看。
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ConversationAttachment(Base):
    __tablename__ = "conversation_attachment"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    owner_human_no: Mapped[str] = mapped_column(String, index=True)
    generation: Mapped[int] = mapped_column(default=0, index=True)
    message_seq: Mapped[int | None] = mapped_column(nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String)
    file_type: Mapped[str] = mapped_column(String)
    mime_type: Mapped[str] = mapped_column(String, default="application/octet-stream")
    size: Mapped[int] = mapped_column(default=0)
    sha256: Mapped[str] = mapped_column(String)
    storage_path: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    processing_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str] = mapped_column(String, default="")
    page_count: Mapped[int | None] = mapped_column(nullable=True)
    sheet_count: Mapped[int | None] = mapped_column(nullable=True)
    chunk_count: Mapped[int] = mapped_column(default=0)
    # 材料包：同一 zip 上传的附件共享 pack_id，business_no 是包内业务号（物化目录名）
    pack_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    business_no: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    deleted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AttachmentChunk(Base):
    __tablename__ = "attachment_chunk"

    id: Mapped[int] = mapped_column(primary_key=True)
    attachment_id: Mapped[str] = mapped_column(String, index=True)
    chunk_no: Mapped[int] = mapped_column(default=0)
    content: Mapped[str] = mapped_column(String, default="")
    location: Mapped[str] = mapped_column(String, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class AttachmentProcessingJob(Base):
    __tablename__ = "attachment_processing_job"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    attachment_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    error_summary: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ConversationMemoryState(Base):
    __tablename__ = "conversation_memory_state"

    conversation_id: Mapped[str] = mapped_column(String, primary_key=True)
    compacted_through_seq: Mapped[int] = mapped_column(default=0)
    rolling_summary: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="idle")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MemoryRecord(Base):
    __tablename__ = "memory_record"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, default="default", index=True)
    requester_human_no: Mapped[str] = mapped_column(String, index=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    memory_type: Mapped[str] = mapped_column(String, default="summary")
    content: Mapped[str] = mapped_column(String, default="")
    source: Mapped[str] = mapped_column(String, default="automatic")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retained: Mapped[bool] = mapped_column(default=False)
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_status: Mapped[str] = mapped_column(String, default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MemorySyncJob(Base):
    __tablename__ = "memory_sync_job"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    memory_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String, default="upsert")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    error_summary: Mapped[str] = mapped_column(String, default="")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AgentExecution(Base):
    """Durable, user-visible execution state; never stores hidden model reasoning."""

    __tablename__ = "agent_execution"
    __table_args__ = (
        Index(
            "uq_agent_execution_active_conversation",
            "conversation_id",
            unique=True,
            sqlite_where=text("status IN ('queued','running','streaming','waiting_approval')"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    trigger_message_seq: Mapped[int] = mapped_column(index=True)
    trace_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    primary_employee_id: Mapped[str] = mapped_column(String, default="")
    conversation_generation: Mapped[int] = mapped_column(default=0)
    retry_of_execution_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, default="queued")
    stage: Mapped[str] = mapped_column(String, default="queued")
    error_code: Mapped[str] = mapped_column(String, default="")
    error_message: Mapped[str] = mapped_column(String, default="")
    retryable: Mapped[bool] = mapped_column(default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentExecutionEvent(Base):
    """Sanitized progress event and replayable answer chunk for SSE clients."""

    __tablename__ = "agent_execution_event"
    __table_args__ = (
        Index(
            "uq_agent_execution_event_sequence",
            "execution_id",
            "event_seq",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[str] = mapped_column(String, index=True)
    event_seq: Mapped[int] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String)
    actor_employee_id: Mapped[str] = mapped_column(String, default="")
    stage: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="running")
    title: Mapped[str] = mapped_column(String, default="")
    detail: Mapped[str] = mapped_column(String, default="")
    knowledge_base_id: Mapped[str | None] = mapped_column(String, nullable=True)
    target_agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    hit_count: Mapped[int | None] = mapped_column(nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ExecutionArtifact(Base):
    """AI员工在 /workspace 里产出的文件（核对表、导出数据等）的快照。

    为什么要快照而不是按需读工作区：工作区是按**员工**共享的
    （``harness/<EMP>/workspace`` 绑定到容器 ``/workspace``），同一个 AI 员工跑第二次
    审计会把 ``output/核对表.md`` 覆盖掉。若按需实时读，用户在旧会话里点「预览」
    看到的会是新一轮的内容 —— 对审计场景这是结论错位，比看不到更糟。
    所以每轮执行结束时把产物复制到 ``DWP_RUN_OUTPUT_ROOT`` 下，之后只读快照。
    """

    __tablename__ = "execution_artifact"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    execution_id: Mapped[str] = mapped_column(String, index=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    employee_id: Mapped[str] = mapped_column(String, default="")
    # 工作区内的相对路径（output/核对表.md）——展示时保留，便于对上模型的答复
    name: Mapped[str] = mapped_column(String)
    # 展示与下载用的文件名（核对表.md）
    filename: Mapped[str] = mapped_column(String)
    suffix: Mapped[str] = mapped_column(String, default="")
    mime_type: Mapped[str] = mapped_column(String, default="application/octet-stream")
    # 可预览类型：markdown | html | csv | text —— 前端只读这个字段，不按后缀到处判断
    kind: Mapped[str] = mapped_column(String, default="text")
    size: Mapped[int] = mapped_column(default=0)
    sha256: Mapped[str] = mapped_column(String, default="")
    storage_path: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ToolInputRequest(Base):
    """MCP 工具缺参/缺鉴权时的「待补全」记录（气泡补全的事实来源）。

    与执行状态机刻意解耦：执行仍按原样跑完（模型会说明「需要补充什么」），补全状态
    独立存在本表，避免改动 ``AgentExecution`` 的活跃态唯一索引与 SSE 语义。
    ``submitted_values`` 只记掩码与非敏感值，敏感值一律不落库。
    """

    __tablename__ = "tool_input_request"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    execution_id: Mapped[str] = mapped_column(String, index=True)
    trace_id: Mapped[str] = mapped_column(String, default="")
    plugin_id: Mapped[str] = mapped_column(String, default="")
    tool_name: Mapped[str] = mapped_column(String, default="")
    fields: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="pending")  # pending | submitted | expired | cancelled
    submitted_values: Mapped[dict] = mapped_column(JSON, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# ---- Knowledge-first MVP: identity, colleague directory and bounded delegation ----


class Account(Base):
    """Local login account. External directory identities bind to the same human record later."""

    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    login_key_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    human_employee_no: Mapped[str] = mapped_column(String, index=True)
    roles: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="active")
    must_change_password: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AuthSession(Base):
    __tablename__ = "auth_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    account_id: Mapped[int] = mapped_column(index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DirectoryBinding(Base):
    """Stable identity mapping used by the mock directory and the future WeCom adapter."""

    __tablename__ = "directory_binding"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String, default="mock")
    corp_id: Mapped[str] = mapped_column(String, default="demo-corp")
    external_user_id: Mapped[str] = mapped_column(String, index=True)
    human_employee_no: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="active")


class AgentProfile(Base):
    """Business identity of an AI colleague; runtime concerns deliberately live elsewhere."""

    __tablename__ = "agent_profile"

    employee_id: Mapped[str] = mapped_column(String, primary_key=True)
    identity_kind: Mapped[str] = mapped_column(String)  # human_twin | role_employee
    responsibilities: Mapped[list] = mapped_column(JSON, default=list)
    knowledge_domains: Mapped[list] = mapped_column(JSON, default=list)
    accepts_tasks: Mapped[list] = mapped_column(JSON, default=list)
    delegation_policy: Mapped[str] = mapped_column(String, default="none")  # bounded_single | none
    fallback_employee_id: Mapped[str | None] = mapped_column(String, nullable=True)
    persona_status: Mapped[str] = mapped_column(String, default="published")
    persona_version: Mapped[int] = mapped_column(default=1)


class AgentRuntime(Base):
    __tablename__ = "agent_runtime"

    employee_id: Mapped[str] = mapped_column(String, primary_key=True)
    engine: Mapped[str] = mapped_column(String, default="harness")
    container_name: Mapped[str] = mapped_column(String, unique=True)
    state: Mapped[str] = mapped_column(String, default="stopped")
    workspace_ref: Mapped[str] = mapped_column(String, default="")
    last_error: Mapped[str] = mapped_column(String, default="")
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentKnowledgeGrant(Base):
    __tablename__ = "agent_knowledge_grant"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[str] = mapped_column(String, index=True)
    knowledge_base_id: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String, default="read")
    status: Mapped[str] = mapped_column(String, default="active")


class PersonaVersion(Base):
    __tablename__ = "persona_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String, default="draft")  # draft | published | superseded
    content: Mapped[str] = mapped_column(String, default="")
    source_refs: Mapped[list] = mapped_column(JSON, default=list)
    reviewed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DelegationRun(Base):
    __tablename__ = "delegation_run"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String, index=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    requester_human_no: Mapped[str] = mapped_column(String, index=True)
    sender_employee_id: Mapped[str] = mapped_column(String)
    recipient_employee_id: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String)  # answer_self | delegate | clarify | refuse
    goal: Mapped[str] = mapped_column(String, default="")
    reason: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="planned")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    # 失败原因。没有它时委派失败只能看到 status=failed + evidence=[]，无法区分
    # 「目标员工正忙」「目标无权访问该知识库」「容器异常」等情况。
    error: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
