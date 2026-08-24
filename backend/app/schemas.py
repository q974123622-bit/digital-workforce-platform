from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
    runtime_type: str = "demo"
    runtime_ref: str | None = None
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
    runtime_ref: str | None = None
    location: str | None = None
    internet: str | None = None
    max_data_level: str | None = None
    allowed_domains: list[str] | None = None


class EmployeeOut(BaseModel):
    id: str
    employee_no: str
    name: str
    type: str
    employment_type: str  # formal | intern（twin 取真人，virtual/rpa 取 Owner）
    source_human_no: str | None
    owner_human_no: str
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


class PluginBase(BaseModel):
    name: str
    type: str
    endpoint_ref: str = "mock://"
    data_level: str = "L1"
    status: str = "active"
    description: str = ""


class PluginCreate(PluginBase):
    id: str | None = None


class PluginUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    endpoint_ref: str | None = None
    data_level: str | None = None
    status: str | None = None
    description: str | None = None


class PluginOut(PluginBase):
    id: str


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


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str
    employee_id: str
    title: str
    created_at: datetime
# ---- P20：Access Request API（L3 敏感资源白名单申请/审批） ----


class AccessRequestCreate(BaseModel):
    resource_type: Literal["knowledge", "plugin", "data"]
    resource_id: str
    reason: str = ""


class AccessRequestApproveIn(BaseModel):
    approve: bool
    actor_no: str


class AccessRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    applicant_no: str
    resource_type: str
    resource_id: str
    reason: str = ""
    status: str = "pending"
    approval_chain: list = []
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime


# ---- P23：Memory（记忆插件后端核心） ----


class MemoryCreate(BaseModel):
    subject_type: str  # human | twin | virtual | team
    subject_no: str  # E10281 / DT-E10281 / VE-0001 / TEAM-ONBOARD
    kind: str = "fact"  # conversation|decision|fact|attachment|summary|profile|basic_info
    content: str
    content_type: str = "text"  # text | structured
    related_subject_no: str | None = None
    trace_id: str | None = None
    file_ref: str | None = None
    visibility: str = "personal"  # public | personal | shared | confidential
    data_level: str = "L1"  # L1 | L2 | L3
    lifecycle: str = "active"  # active | summarized


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_type: str
    subject_no: str
    kind: str
    content: str
    content_type: str
    related_subject_no: str | None
    trace_id: str | None
    file_ref: str | None
    visibility: str
    data_level: str
    lifecycle: str
    created_at: datetime
    updated_at: datetime


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    trace_id: str
    message: str
    tool_cards: list = []
    policy_denied: dict | None = None


# ---- 记忆插件（Memory Entry）----




# ---- Sprint 5：Team Task（契约 §3.6 TaskRunDto）----


class SubtaskOut(BaseModel):
    worker_id: str
    worker_no: str
    summary: str
    plugin_ids: list[str] = []
    status: str = "pending"  # pending | running | completed | approval | denied | failed
    result: str | None = None
    approval: dict | None = None


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
    name: str
    description: str = ""
    content: str = ""


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    content: str | None = None
    status: str | None = None  # active | disabled


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


class ClearConversationOut(BaseModel):
    ok: bool
