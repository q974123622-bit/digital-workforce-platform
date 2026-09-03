from datetime import datetime

from sqlalchemy import JSON, DateTime, LargeBinary, String
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


class PluginVersion(Base):
    __tablename__ = "plugin_version"

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


class KnowledgeChunk(Base):
    """RAG 索引块：kb_chunk 表（id / kb_id / source_file / title / content / embedding BLOB / dims / created_at）。"""

    __tablename__ = "kb_chunk"

    id: Mapped[int] = mapped_column(primary_key=True)
    kb_id: Mapped[str] = mapped_column(String, index=True)
    source_file: Mapped[str] = mapped_column(String, default="")
    title: Mapped[str] = mapped_column(String, default="")
    content: Mapped[str] = mapped_column(String, default="")
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    dims: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


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
    source: Mapped[str] = mapped_column(String, default="builtin")  # builtin | agentteams
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


class Conversation(Base):
    """职场会话：私聊（direct）与协作群聊（group）统一承载。"""

    __tablename__ = "conversation"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, default="direct")  # direct | group
    title: Mapped[str] = mapped_column(String, default="")
    owner_human_no: Mapped[str] = mapped_column(String, index=True)
    participants: Mapped[list] = mapped_column(JSON, default=list)
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
    seq: Mapped[int] = mapped_column(default=0)


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
    status: Mapped[str] = mapped_column(String, default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    error_summary: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AgentExecution(Base):
    """Durable, user-visible execution state; never stores hidden model reasoning."""

    __tablename__ = "agent_execution"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    trigger_message_seq: Mapped[int] = mapped_column(index=True)
    trace_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    primary_employee_id: Mapped[str] = mapped_column(String, default="")
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


class AgentTeamsEventSeen(Base):
    """已回传过的 AgentTeams 房间事件（持久化去重，避免重启后重放）。"""

    __tablename__ = "agentteams_event_seen"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# ---- Knowledge-first MVP: identity, colleague directory and bounded delegation ----


class Account(Base):
    """Local login account. External directory identities bind to the same human record later."""

    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
