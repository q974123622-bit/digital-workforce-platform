from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    employment_type: Literal["formal", "intern"]  # formal | intern（twin 取真人，virtual/rpa 取 Owner）
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


class AgentCollaborationTaskCreateIn(BaseModel):
    team_id: str | None = None
    leader_agent_id: str | None = None
    title: str
    instruction: str
    material: dict = Field(default_factory=dict)
    thinking_enabled: bool = False


class AgentTaskNodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    root_task_id: str
    parent_task_id: str | None
    client_task_key: str
    title: str
    coordinator_agent_id: str
    assignee_agent_id: str
    node_kind: str
    revision_of_task_id: str | None
    status: str
    progress: int
    depth: int
    required: bool
    output_payload: dict
    error_code: str
    error_message: str


class AgentTaskEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    root_task_id: str
    task_id: str
    event_seq: int
    event_type: str
    actor_agent_id: str
    target_agent_id: str | None
    summary: str
    detail: dict
    created_at: datetime


class AgentMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    root_task_id: str
    task_id: str
    parent_task_id: str | None
    message_type: str
    sender_agent_id: str
    receiver_agent_id: str
    body: str
    payload: dict
    matrix_room_id: str
    matrix_event_id: str | None
    direction: str
    delivery_status: str
    created_at: datetime


class AgentTaskGraphOut(BaseModel):
    current_round: int = 0
    selected_skill_ids: list[str] = Field(default_factory=list)
    nodes: list[AgentTaskNodeOut] = Field(default_factory=list)
    parent_edges: list[dict] = Field(default_factory=list)
    dependency_edges: list[dict] = Field(default_factory=list)
    communication_edges: list[dict] = Field(default_factory=list)


class AgentCollaborationTaskOut(BaseModel):
    id: str
    team_id: str
    trace_id: str
    title: str
    instruction: str
    status: str
    source: Literal["agentteams"]
    summary: str
    nodes: list[AgentTaskNodeOut] = Field(default_factory=list)
    latest_event_id: int
    created_at: datetime


class AgentWorkerHealthOut(BaseModel):
    agent_id: str
    matrix_user_id: str
    status: str
    last_heartbeat_at: str | None = None


class AgentCollaborationHealthOut(BaseModel):
    enabled: bool
    ready_for_demo: bool
    reason: str
    strict_ready: bool = False
    configured_team_id: str = ""
    mode: Literal["local_demo", "agentteams_strict"]
    transport: Literal["local", "matrix"]
    matrix: str
    room: str
    manager: str
    platform_team: str
    workers: list[AgentWorkerHealthOut] = Field(default_factory=list)


class AgentFinalResultOut(BaseModel):
    task_id: str
    status: str
    summary: str
    node_results: list[AgentTaskNodeOut] = Field(default_factory=list)


class AgentEvaluationDimensionOut(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=40)
    score: int = Field(ge=0)
    max_score: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def score_not_above_maximum(self):
        if self.score > self.max_score:
            raise ValueError("score_exceeds_maximum")
        return self


class AgentTaskEvaluationOut(BaseModel):
    version: str
    evaluated_at: str
    rule_score: int = Field(ge=0, le=60)
    rule_max_score: Literal[60]
    rule_dimensions: list[AgentEvaluationDimensionOut] = Field(default_factory=list)
    rule_deductions: list[str] = Field(default_factory=list)
    model_status: Literal["completed", "unavailable"]
    model_score: int | None = Field(default=None, ge=0, le=40)
    model_max_score: Literal[40]
    model_dimensions: list[AgentEvaluationDimensionOut] = Field(default_factory=list)
    model_strengths: list[str] = Field(default_factory=list)
    model_deductions: list[str] = Field(default_factory=list)
    model_verdict: str = ""
    total_score: int | None = Field(default=None, ge=0, le=100)
    total_max_score: Literal[100]

    @model_validator(mode="after")
    def scores_match_status_and_total(self):
        if self.model_status == "completed":
            if self.model_score is None or self.total_score != self.rule_score + self.model_score:
                raise ValueError("invalid_completed_evaluation_total")
        elif self.model_score is not None or self.total_score is not None:
            raise ValueError("unavailable_model_must_not_have_total")
        return self


class DatabaseBenchmarkExperimentCreateIn(BaseModel):
    scenario_ids: list[str] = Field(min_length=1, max_length=5)
    repeats: int = Field(default=5, ge=1, le=10)
    max_model_steps: int = Field(default=20, ge=1, le=200)
    max_tool_calls: int = Field(default=10, ge=1, le=200)
    timeout_ms: int = Field(default=30_000, ge=100, le=3_600_000)


class DatabaseBenchmarkExperimentOut(BaseModel):
    id: str
    benchmark_id: str
    status: str
    scenario_ids: list[str]
    repeats: int
    fairness_config: dict
    fairness_fingerprint: str
    summary: dict
    total_runs: int
    completed_runs: int
    failed_runs: int
    invalid_runs: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class DatabaseBenchmarkRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    experiment_id: str
    ordinal: int
    scenario_id: str
    repeat_index: int
    mode: Literal[
        "single_agent", "agentteams", "agentteams_full",
        "agentteams_serial", "agentteams_uncoordinated",
    ]
    status: str
    task_run_id: str | None
    fairness_fingerprint: str
    metrics: dict
    diagnosis: dict
    failure_code: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
