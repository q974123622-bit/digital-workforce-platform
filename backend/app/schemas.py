from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class GrantOut(BaseModel):
    plugin_id: str
    name: str = ""
    type: str = ""
    action: str = "read"
    decision_mode: str = "allow"
    data_level: str = ""


class EmployeeCreate(BaseModel):
    name: str
    type: str
    source_human_no: str | None = None
    owner_human_no: str
    department: str = ""
    role_prompt: str = ""
    runtime_type: str | None = None  # 缺省按 type：twin→demo（不建容器），virtual/rpa→容器
    location: str = "remote"
    internet: str = "deny"
    max_data_level: str = "L1"
    allowed_domains: list[str] = []


class EmployeeUpdate(BaseModel):
    name: str | None = None
    department: str | None = None
    role_prompt: str | None = None
    status: str | None = None
    runtime_type: str | None = None
    location: str | None = None
    internet: str | None = None
    max_data_level: str | None = None
    allowed_domains: list[str] | None = None


class EmployeeOut(BaseModel):
    id: str
    employee_no: str
    name: str
    type: str
    employment_type: Literal["formal", "intern"]
    source_human_no: str | None
    owner_human_no: str
    owner_name: str
    department: str
    role_prompt: str
    status: str
    runtime_type: str
    runtime_ref: str | None
    location: str
    internet: str
    max_data_level: str
    allowed_domains: list[str]
    grants: list[GrantOut] = []
    avatar_url: str | None = None


class EmployeeRuntimeOut(BaseModel):
    employee_no: str
    runtime_type: str
    runtime_ref: str | None
    status: str
    worker_phase: str | None = None
    matrix_user_id: str | None = None
    room_id: str | None = None
    detail: str = ""


class PluginBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: Literal["knowledge", "mcp", "workflow", "rpa", "http"]
    endpoint_ref: str = "mock://"
    data_level: Literal["L1", "L2", "L3"] = "L1"
    status: Literal["active", "disabled"] = "active"
    description: str = ""
    runtime_meta: dict = {}


class PluginCreate(PluginBase):
    id: str | None = None


class PluginUpdate(BaseModel):
    name: str | None = None
    type: Literal["knowledge", "mcp", "workflow", "rpa", "http"] | None = None
    endpoint_ref: str | None = None
    data_level: Literal["L1", "L2", "L3"] | None = None
    status: Literal["active", "disabled"] | None = None
    description: str | None = None
    runtime_meta: dict | None = None


class HarnessExecuteIn(BaseModel):
    employee_no: str
    task_prompt: str
    trace_id: str = "HARNESS-0"


class HarnessExecuteOut(BaseModel):
    trace_id: str
    decision: str
    policy_id: str | None = None
    reason: str = ""
    mode: str = "demo"
    ok: bool = False
    result: str = ""


class PluginOut(PluginBase):
    id: str


class CapabilityOut(BaseModel):
    contract_version: str
    id: str
    name: str
    source_type: Literal["skill", "plugin"]
    kind: str
    description: str
    status: str
    executable: bool
    actions: list[str]
    input_schema: dict
    executor: dict
    owner_human_no: str | None = None
    ready: bool = True
    issues: list[str] = []


class PolicyBase(BaseModel):
    name: str
    effect: str = "allow"
    description: str = ""
    enabled: bool = True
    priority: int = 0


class PolicyCreate(PolicyBase):
    id: str | None = None


class PolicyUpdate(BaseModel):
    name: str | None = None
    effect: str | None = None
    description: str | None = None
    enabled: bool | None = None
    priority: int | None = None


class PolicyOut(PolicyBase):
    id: str


class AuditCreate(BaseModel):
    trace_id: str
    actor: str = ""
    employee_id: str | None = None
    team_id: str | None = None
    plugin_id: str | None = None
    knowledge_base_id: str | None = None
    action: str = ""
    decision: str = "deny"
    reason: str | None = None
    result_summary: str | None = None


class AuditOut(AuditCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime


class TeamMemberOut(BaseModel):
    employee_id: str
    role: str


class TeamOut(BaseModel):
    id: str
    name: str
    leader_employee_id: str
    description: str = ""
    members: list[TeamMemberOut] = []


class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    level: str
    data_level: str = "L1"
    resource_type: str = "knowledge"
    allowed_employment_type: list[str] = []
    department_scope: list[str] = []
    domain: str
    description: str
    status: str
    doc_path: str | None
    # 双后端映射与可达性（由 routers/knowledge.py 组装，不直接来自 ORM）。
    sources: dict[str, int] = {}
    active_source: str | None = None
    available: bool = True
    unavailable_reason: str = ""


class KnowledgeSourceStatusOut(BaseModel):
    """知识源/MCP 工具的连通状态（管理端「数据源状态」区）。"""

    source: str
    label: str
    environment: str
    environment_label: str
    kind: str = ""
    configured: bool = False
    state: str = "unknown"
    available: bool = False
    latency_ms: int = 0
    last_error: str = ""
    detail: dict = {}
    checked_at: datetime | None = None


class KnowledgeSourceProbeIn(BaseModel):
    """手动重测入参：不传 source 表示按 scope 重测一批。"""

    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    scope: Literal["knowledge", "all"] = "knowledge"


class KnowledgeGrantItemOut(BaseModel):
    """独占分配矩阵中的一行：某个 AI 员工当前持有的知识库集合。"""

    employee_id: str
    employee_name: str = ""
    employee_type: str = ""
    knowledge_base_ids: list[str] = []


class KnowledgeGrantMatrixOut(BaseModel):
    employees: list[KnowledgeGrantItemOut] = []
    knowledge_bases: list[KnowledgeBaseOut] = []
    # {knowledge_base_id: employee_id}，用于前端把「已被他人占用」的库置灰。
    occupancy: dict[str, str] = {}


class KnowledgeGrantReplaceIn(BaseModel):
    """整体替换某个 AI 员工的知识库授权集合（保证不出现交叉中间态）。"""

    model_config = ConfigDict(extra="forbid")

    knowledge_base_ids: list[str] = []


class AccessRequestCreate(BaseModel):
    resource_type: Literal["knowledge", "plugin"]
    resource_id: str = Field(min_length=1)
    reason: str = Field(default="", max_length=500)


class AccessRequestApproveIn(BaseModel):
    approve: bool
    actor_no: str


class AccessRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    applicant_no: str
    resource_type: str
    resource_id: str
    reason: str
    status: str
    approval_chain: list = []
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime


# ---- Sprint 2：Core Control Plane 内部接口（契约 API_CONTRACT.md §6） ----


class SubjectRef(BaseModel):
    type: str | None = None
    id: str | None = None
    employee_no: str
    employment_type: str | None = None


class ResourceRefIn(BaseModel):
    type: str
    id: str
    data_level: str | None = None


class PolicyEvaluateIn(BaseModel):
    subject: SubjectRef
    resource: ResourceRefIn
    action: str
    context: dict | None = None


class PolicyEvaluateOut(BaseModel):
    decision: str
    policy_id: str | None = None
    reason: str


class GatewayInvokeIn(BaseModel):
    employee_id: str
    plugin_id: str
    action: str
    params: dict = {}
    trace_id: str = ""


class GatewayInvokeOut(BaseModel):
    ok: bool
    data: dict | None = None
    decision: str
    audit_ids: list[int] = []
    policy_id: str | None = None
    execution_mode: str = "pending"
    runtime_mode: str = "pending"
    runtime_context_id: str = ""
    runtime_summary: str = ""
    tool_name: str = ""
    tool_type: str = ""


# ---- Sprint 3：Enterprise Resource & Security Layer ----


class KnowledgeSearchIn(BaseModel):
    employee_id: str
    knowledge_base_id: str
    query: str
    trace_id: str = ""


class KnowledgeSearchOut(BaseModel):
    ok: bool
    data: dict | None = None
    decision: str
    audit_ids: list[int] = []
    policy_id: str | None = None


class SandboxRunIn(BaseModel):
    employee_id: str
    task_id: str = ""
    command: str = ""
    mount_dir: str = ""
    network: str = "none"
    execution_location: str = "remote"  # remote | local（兼容扩展字段，默认 remote）


class SandboxRunOut(BaseModel):
    mode: str
    status: str
    logs: list[str] = []


# ---- Sprint 4：Chat ----


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str
    role: str
    content: str
    tool_cards: list = []


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    trace_id: str
    message: str
    tool_cards: list = []
    policy_denied: dict | None = None


# ---- Sprint 5：Team Task（契约 §3.6 TaskRunDto）----


class SubtaskOut(BaseModel):
    worker_id: str
    worker_no: str
    summary: str
    plugin_ids: list[str] = []
    status: str = "pending"  # pending | running | completed | approval | denied | failed
    result: str | None = None
    approval: dict | None = None
    collaboration_status: str = "planned"
    collaboration_messages: list[str] = []
    execution_mode: str = "pending"
    runtime_mode: str = "pending"
    runtime_context_id: str = ""
    runtime_summary: str = ""
    tool_name: str = ""
    tool_type: str = ""


class TaskRunOut(BaseModel):
    id: str
    team_id: str
    conversation_id: str | None = None
    trigger_message_seq: int | None = None
    trace_id: str
    request: str
    status: str = "pending"  # parsing | running | approval | completed | denied | failed
    subtasks: list[SubtaskOut] = []
    summary: str = ""
    source: str = "builtin"
    created_at: datetime


class TaskApproveIn(BaseModel):
    approve: bool
    actor_no: str


# ---- Sprint 6：员工工作台 ----


class WorkspacePluginOut(BaseModel):
    plugin_id: str
    name: str
    type: str
    action: str
    decision_mode: str
    data_level: str


class WorkspaceKbOut(BaseModel):
    knowledge_base_id: str
    name: str
    data_level: str
    description: str
    accessible: bool
    decision: str
    # 数据源可达性：accessible 表示「有权限」，available 表示「数据源现在真的能用」。
    available: bool = True
    active_source: str | None = None


class WorkspaceSecurityOut(BaseModel):
    location: str
    internet: str
    max_data_level: str
    allowed_domains: list[str]


class WorkspaceOut(BaseModel):
    employee: EmployeeOut
    role_prompt: str
    plugins: list[WorkspacePluginOut] = []
    knowledge_bases: list[WorkspaceKbOut] = []
    security: WorkspaceSecurityOut


# ---- Sprint 7：个人工作中心（职场）----


class SkillCreate(BaseModel):
    actor_no: str
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    content: str = Field(default="", max_length=20_000)


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    content: str | None = Field(default=None, max_length=20_000)
    status: Literal["active", "disabled"] | None = None


class SkillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_human_no: str
    name: str
    description: str
    content: str
    status: str
    created_at: datetime


class ActorOut(BaseModel):
    employee_no: str
    name: str
    department: str
    employment_type: str


class ConversationParticipantOut(BaseModel):
    employee_no: str
    name: str = ""
    role: str = "member"  # organizer | member
    employee_type: str = ""


class ConversationCreate(BaseModel):
    actor_no: str
    kind: str = "direct"  # direct | group
    title: str = ""
    participant_employee_nos: list[str] = []


class ConversationMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: str
    participant_no: str
    participant_name: str
    role: str
    content: str
    tool_cards: list = []
    attachment_ids: list[str] = []
    seq: int
    # 非空表示这条回复已被同轮的新回答取代，界面折叠显示。
    superseded_at: datetime | None = None


class ConversationOut(BaseModel):
    id: str
    kind: str
    title: str
    owner_human_no: str
    participants: list[ConversationParticipantOut] = []
    messages: list[ConversationMessageOut] = []
    tasks: list[TaskRunOut] = []
    updated_at: datetime


class ConversationSummaryOut(BaseModel):
    id: str
    kind: str
    title: str
    owner_human_no: str
    participants: list[ConversationParticipantOut] = []
    last_message: str = ""
    updated_at: datetime


class ConversationSearchHitOut(BaseModel):
    """会话检索结果：会话摘要 + 命中元信息（供侧栏片段展示与点击定位）。"""

    conversation: ConversationSummaryOut
    matched_in: str = "content"  # name | content
    match_count: int = 0
    match_seq: int | None = None
    match_role: str = ""  # user | assistant
    match_speaker: str = ""  # assistant 时的说话人展示名；user 为空
    snippet: str = ""


class ConversationSendIn(BaseModel):
    actor_no: str
    content: str
    attachment_ids: list[str] = []


class ConversationMessagePatchIn(BaseModel):
    """就地编辑最新一轮提问（不重跑）。"""

    actor_no: str
    content: str


class ConversationMessageRunIn(BaseModel):
    """就地编辑最新一轮提问并重跑；content 为空表示沿用原内容重跑。"""

    actor_no: str
    content: str | None = None


class ConversationAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attachment_id: str = Field(validation_alias="id")
    conversation_id: str
    filename: str
    file_type: str
    mime_type: str
    size: int
    status: str
    processing_hint: str | None = None
    error_code: str = ""
    page_count: int | None = None
    sheet_count: int | None = None
    chunk_count: int = 0
    message_seq: int | None = None
    pack_id: str | None = None
    business_no: str | None = None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def preview_kind(self) -> str:
        """可预览类型：``"image"`` / ``"pdf"`` / ``"none"``（全链路唯一判定来源）。

        惰性 import 服务层，避免 ``schemas`` 与 ``services`` 的循环依赖。
        """
        from .services.attachment_preview import preview_kind

        return preview_kind(self.file_type)


class AgentExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    trigger_message_seq: int
    trace_id: str
    primary_employee_id: str
    conversation_generation: int = 0
    retry_of_execution_id: str | None = None
    status: str
    stage: str
    error_code: str = ""
    error_message: str = ""
    retryable: bool = False
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class ConversationRunOut(BaseModel):
    execution_id: str
    trigger_message_seq: int
    conversation: ConversationOut


class AgentExecutionEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_seq: int
    event_type: str
    actor_employee_id: str
    stage: str
    status: str
    title: str
    detail: str
    knowledge_base_id: str | None = None
    target_agent_id: str | None = None
    hit_count: int | None = None
    payload: dict = Field(default_factory=dict)
    created_at: datetime


class ExecutionArtifactOut(BaseModel):
    """AI员工本轮在工作区写下的产物（核对表等），前端据此渲染预览/下载卡片。"""

    model_config = ConfigDict(from_attributes=True)

    artifact_id: str = Field(validation_alias="id")
    execution_id: str
    # 工作区内的相对路径（output/核对表.md），与模型答复里的路径对得上
    name: str
    filename: str
    # 可预览类型：markdown | html | csv | text —— 前端只读这个字段
    kind: str
    mime_type: str
    size: int
    created_at: datetime


class AgentExecutionDetailOut(BaseModel):
    execution: AgentExecutionOut
    events: list[AgentExecutionEventOut] = Field(default_factory=list)
    # 本轮产物随执行一起返回：前端按 execution 归属渲染，不需要额外请求，也不会挂错消息
    artifacts: list[ExecutionArtifactOut] = Field(default_factory=list)


class ToolInputFieldOut(BaseModel):
    """待补全字段：只含字段名、说明与掩码，绝不含明文凭据。"""

    key: str
    label: str = ""
    kind: str = "text"  # secret | text
    required: bool = True
    hint: str = ""
    source: str = ""
    masked: str = ""
    reason: str = ""


class ToolInputRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    execution_id: str
    trace_id: str = ""
    plugin_id: str = ""
    tool_name: str = ""
    fields: list[ToolInputFieldOut] = Field(default_factory=list)
    reason: str = ""
    status: str = "pending"
    submitted_values: dict = Field(default_factory=dict)
    resolved_at: datetime | None = None
    resolved_by: str = ""
    created_at: datetime


class ToolInputSubmitIn(BaseModel):
    actor_no: str
    request_id: str
    values: dict[str, str] = Field(default_factory=dict)


class ToolInputIgnoreIn(BaseModel):
    actor_no: str


class ConversationAddParticipantIn(BaseModel):
    employee_no: str


class WorkplaceHomeOut(BaseModel):
    actor: ActorOut
    twin: EmployeeOut
    available_employees: list[EmployeeOut] = []
    skills: list[SkillOut] = []
    recent_conversations: list[ConversationSummaryOut] = []


class ClearConversationOut(BaseModel):
    ok: bool


# ---- Knowledge-first MVP contracts ----


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=200)


class KeyLoginIn(BaseModel):
    access_key: str = Field(min_length=12, max_length=200)


class AccountOut(BaseModel):
    username: str
    employee_no: str
    name: str
    department: str
    employment_type: str
    roles: list[str] = []
    must_change_password: bool = False
    # 登录人头像：复用本人数字分身的演示头像；没有分身（如管理端账号）时为 None，
    # 前端回退姓名首字。``human_employee`` 不存头像，见 ``routers/auth._human_avatar_url``。
    avatar_url: str | None = None


class LoginOut(BaseModel):
    account: AccountOut
    expires_at: datetime


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=8, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class DirectoryUserOut(BaseModel):
    provider: str = "mock"
    external_user_id: str
    employee_no: str
    name: str
    department: str
    employment_type: str
    status: str
    default_twin_id: str | None = None


class AgentProfileOut(BaseModel):
    employee_id: str
    display_name: str
    identity_kind: str
    owner_human_no: str
    department: str
    responsibilities: list[str] = []
    knowledge_domains: list[str] = []
    accepts_tasks: list[str] = []
    delegation_policy: str
    fallback_employee_id: str | None = None
    persona_status: str
    persona_version: int
    runtime_engine: str
    runtime_state: str
    container_name: str
    runtime_image: str = ""
    runtime_version: str = ""
    runtime_profile_rev: str = ""
    knowledge_base_ids: list[str] = []


class RuntimeImageOut(BaseModel):
    """顶部「运行环境」汇总里的一个镜像。"""

    role: str
    name: str
    note: str = ""


class RuntimeInfoOut(BaseModel):
    """平台自身的镜像与交付版本，用于运维页顶部一行汇总。"""

    delivery_tag: str = ""
    images: list[RuntimeImageOut] = []


class AgentRuntimeOutV1(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: str
    engine: str
    container_name: str
    state: str
    workspace_ref: str
    last_error: str
    last_active_at: datetime | None = None


class EffectiveCapabilityItemOut(BaseModel):
    id: str
    name: str
    kind: Literal["knowledge", "delegation", "instruction", "plugin", "skill"]
    source_type: Literal["knowledge_base", "platform_tool", "skill", "plugin"]
    description: str = ""
    actions: list[str] = []
    status: Literal[
        "available", "approval", "unauthorized",
        "runtime_unavailable", "source_unavailable", "disabled",
    ]
    decision: Literal["allow", "approval", "deny"]
    reason: str = ""
    authorized: bool = False
    installed: bool = False
    healthy: bool = False
    data_level: str | None = None
    knowledge_base_id: str | None = None
    target_employee_ids: list[str] = []
    example_prompts: list[str] = []
    # 技能投放态（只有技能类能力项有值）：真源是 harness 工作区文件系统，
    # 与 ``status`` 分开成独立字段，避免再扩张 status 的 Literal 破既有契约。
    deploy_state: Literal["deployed", "stale", "missing", "failed", "removed"] | None = None
    deploy_detail: str = ""
    deployed_at: str | None = None


class EffectiveCapabilitiesOut(BaseModel):
    employee_id: str
    display_name: str
    identity_kind: str
    runtime_engine: str
    runtime_state: str
    container_name: str
    knowledge_mode: Literal["mock", "internal"]
    capabilities: list[EffectiveCapabilityItemOut] = []
    available_count: int = 0
    attention_count: int = 0


class PersonaDraftIn(BaseModel):
    source_refs: list[str] = []
    responsibilities: list[str] = []
    project_context: str = Field(default="", max_length=20_000)


class PersonaVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: str
    version: int
    status: str
    content: str
    source_refs: list[str] = []
    reviewed_by: str | None = None
    created_at: datetime


class WeComMockIn(BaseModel):
    corp_id: str = "demo-corp"
    wecom_user_id: str
    content: str = Field(min_length=1, max_length=10_000)
    target_agent_id: str | None = None


class WeComRouteOut(BaseModel):
    employee_no: str
    human_name: str
    target_agent_id: str
    target_agent_name: str
    content: str


# ---- 管理端技能市场（内网 SkillHub）----

class SkillHubSkillOut(BaseModel):
    slug: str
    display_name: str
    summary: str = ""
    version: str = ""
    source: str = ""
    category: str = ""
    downloads: int = 0
    stars: int = 0
    updated_at: int | None = None
    namespace: str = ""
    author: str = ""
    department: str = ""
    tags: list[str] = []
    # tags 命中「推荐/精选/热门」即视为运营推荐位（导入后员工货架置顶）。
    featured: bool = False
    download_disabled: bool = False
    imported: bool = False
    imported_version: str | None = None
    imported_plugin_id: str | None = None


class SkillHubCategoryOut(BaseModel):
    category: str
    desc: str = ""


class SkillHubOverviewOut(BaseModel):
    enabled: bool
    base_url: str = ""
    categories: list[SkillHubCategoryOut] = []
    message: str = ""


class SkillHubSkillPageOut(BaseModel):
    items: list[SkillHubSkillOut] = []
    total: int = 0
    page: int = 1
    page_size: int = 24
    imported_total: int = 0


class SkillHubImportRecordOut(BaseModel):
    id: int
    batch_id: str | None = None
    slug: str
    version: str = ""
    source: str = ""
    display_name: str = ""
    category: str = ""
    plugin_id: str | None = None
    sha256: str = ""
    status: str
    message: str = ""
    file_count: int = 0
    instruction_chars: int = 0
    imported_by: str = ""
    created_at: datetime
    updated_at: datetime


class SkillHubImportSummaryOut(BaseModel):
    imported: int = 0
    skipped: int = 0
    failed: int = 0


class SkillHubImportBatchOut(BaseModel):
    results: list[SkillHubImportRecordOut] = []
    summary: SkillHubImportSummaryOut


class SkillHubImportBatchStartOut(BaseModel):
    """批量导入已受理：返回批次号，进度另查 ``/imports/{batch_id}``。"""

    batch_id: str
    total: int = 0
    status: str = "running"


class SkillHubImportProgressOut(SkillHubImportBatchOut):
    batch_id: str
    # running | completed | unknown（进程重启后批次登记丢失）
    status: str = "running"
    total: int = 0
    done: int = 0


class SkillHubImportListOut(BaseModel):
    items: list[SkillHubImportRecordOut] = []


class SkillHubRefreshOut(BaseModel):
    refreshed: bool


# ---- 员工侧知识库市场 ----

class KnowledgeRequestIn(BaseModel):
    reason: str = Field(default="", max_length=500)


class KnowledgeMarketItemOut(BaseModel):
    id: str
    name: str
    domain: str = ""
    data_level: str = "L1"
    description: str = ""
    status: str = "active"
    # enabled | pending | occupied | requestable
    state: str
    request_id: int | None = None
    occupant: str | None = None
    occupant_name: str | None = None
    active_source: str | None = None
    source_available: bool = False


class KnowledgeMarketOut(BaseModel):
    items: list[KnowledgeMarketItemOut] = []
