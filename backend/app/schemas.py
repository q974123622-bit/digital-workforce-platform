from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    runtime_type: str | None = None  # 缺省按 type：twin→demo（不建容器），virtual/rpa→agentteams
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
    source: str = "builtin"  # builtin | agentteams
    created_at: datetime


class TaskCreateIn(BaseModel):
    request: str


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
    seq: int


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


class ConversationSendIn(BaseModel):
    actor_no: str
    content: str


class AgentExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    trigger_message_seq: int
    trace_id: str
    primary_employee_id: str
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


class AgentExecutionDetailOut(BaseModel):
    execution: AgentExecutionOut
    events: list[AgentExecutionEventOut] = Field(default_factory=list)


class ConversationAddParticipantIn(BaseModel):
    employee_no: str


class WorkplaceHomeOut(BaseModel):
    actor: ActorOut
    twin: EmployeeOut
    available_employees: list[EmployeeOut] = []
    skills: list[SkillOut] = []
    recent_conversations: list[ConversationSummaryOut] = []


class WorkflowEmployeeOut(BaseModel):
    employee_no: str
    name: str
    type: str


class WorkflowOut(BaseModel):
    plugin_id: str
    name: str
    type: str
    data_level: str
    description: str
    steps: list[str] = []
    demo_prompt: str = ""
    authorized_employees: list[WorkflowEmployeeOut] = []
    owner_employee: WorkflowEmployeeOut | None = None


class ClearConversationOut(BaseModel):
    ok: bool


# ---- Knowledge-first MVP contracts ----


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=200)


class AccountOut(BaseModel):
    username: str
    employee_no: str
    name: str
    department: str
    employment_type: str
    roles: list[str] = []
    must_change_password: bool = False


class LoginOut(BaseModel):
    account: AccountOut
    expires_at: datetime


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
    knowledge_base_ids: list[str] = []


class AgentRuntimeOutV1(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: str
    engine: str
    container_name: str
    state: str
    workspace_ref: str
    last_error: str
    last_active_at: datetime | None = None


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
