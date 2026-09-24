// AI员工平台 — 前后端统一类型定义
// 与 backend/app/schemas.py 及 docs/API_CONTRACT.md 保持一致。

export type EmployeeType = "twin" | "virtual" | "rpa";

export interface HumanEmployee {
  employee_no: string;
  name: string;
  department: string;
  employment_type: "formal" | "intern";
  status: string;
}

export interface Grant {
  plugin_id: string;
  name: string;
  type: string;
  action: string;
  decision_mode: "allow" | "deny" | "approval";
  data_level: string;
}

export interface Employee {
  id: string;
  employee_no: string;
  name: string;
  type: EmployeeType;
  employment_type: "formal" | "intern";
  source_human_no: string | null;
  owner_human_no: string;
  owner_name?: string;
  department: string;
  role_prompt: string;
  status: string;
  runtime_type: string;
  runtime_ref: string | null;
  location: string;
  internet: string;
  max_data_level: string;
  allowed_domains: string[];
  grants: Grant[];
  avatar_url?: string | null;
}

export interface AccessRequest {
  id: number;
  applicant_no: string;
  resource_type: "knowledge" | "plugin";
  resource_id: string;
  reason: string;
  status: "pending" | "granted" | "rejected";
  approval_chain: { actor_no: string; decision: string }[];
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface Plugin {
  id: string;
  name: string;
  type: string;
  endpoint_ref: string;
  data_level: string;
  status: string;
  description: string;
  runtime_meta: Record<string, unknown>;
}

export interface Capability {
  contract_version: string;
  id: string;
  name: string;
  source_type: 'skill' | 'plugin';
  kind: string;
  description: string;
  status: 'active' | 'disabled';
  executable: boolean;
  actions: string[];
  input_schema: Record<string, unknown>;
  executor: { primary?: string; adapter_ref?: string; fallback?: string; [key: string]: unknown };
  owner_human_no: string | null;
  ready: boolean;
  issues: string[];
}

export interface EffectiveCapabilityItem {
  id: string;
  name: string;
  kind: 'knowledge' | 'delegation' | 'instruction' | 'plugin' | 'skill';
  source_type: 'knowledge_base' | 'platform_tool' | 'skill';
  description: string;
  actions: string[];
  status: 'available' | 'approval' | 'unauthorized' | 'runtime_unavailable' | 'source_unavailable' | 'disabled';
  decision: 'allow' | 'approval' | 'deny';
  reason: string;
  authorized: boolean;
  installed: boolean;
  healthy: boolean;
  data_level: string | null;
  knowledge_base_id: string | null;
  target_employee_ids: string[];
  example_prompts: string[];
  /** 技能投放态（只有技能类能力项有值）：真源是 harness 工作区文件系统。 */
  deploy_state?: 'deployed' | 'stale' | 'missing' | 'failed' | 'removed' | null;
  /** 投放态的一行说明（含失败原因）。 */
  deploy_detail?: string;
  /** 最近一次投放时间（后端 naive UTC，展示要走 formatServerTime）。 */
  deployed_at?: string | null;
}

export interface EffectiveCapabilities {
  employee_id: string;
  display_name: string;
  identity_kind: string;
  runtime_engine: string;
  runtime_state: string;
  container_name: string;
  knowledge_mode: 'mock' | 'internal';
  capabilities: EffectiveCapabilityItem[];
  available_count: number;
  attention_count: number;
}

export interface Policy {
  id: string;
  name: string;
  effect: "allow" | "deny" | "approval";
  description: string;
  enabled: boolean;
  priority: number;
}

export interface AuditEvent {
  id: number;
  trace_id: string;
  ts: string;
  actor: string;
  employee_id: string | null;
  team_id: string | null;
  plugin_id: string | null;
  knowledge_base_id?: string | null;
  action: string;
  decision: string;
  reason: string | null;
  result_summary: string | null;
}

export interface TeamMember {
  employee_id: string;
  role: string;
}

export interface Team {
  id: string;
  name: string;
  leader_employee_id: string;
  description: string;
}

export interface TeamDetail extends Team {
  members: TeamMember[];
}

export interface KnowledgeBase {
  id: string;
  name: string;
  level: string;
  data_level?: string;
  resource_type?: string;
  allowed_employment_type?: string[];
  department_scope?: string[];
  domain: string;
  description: string;
  status: string;
  doc_path: string | null;
  /** 双后端映射：{ rest: 751, verag: 623 }（远端库不在该环境时缺键） */
  sources?: Record<string, number>;
  /** 当前生效通道：rest（测试）/ verag（生产）/ null（都不可达） */
  active_source?: string | null;
  /** 数据源是否可达；false 时目录保留但界面置灰 */
  available?: boolean;
  unavailable_reason?: string;
}

/** 知识源连通状态（管理端「数据源状态」区） */
export interface KnowledgeSourceStatus {
  source: string;
  label: string;
  environment: string;
  environment_label: string;
  kind?: string;
  configured: boolean;
  state: "available" | "unavailable" | "unknown" | string;
  available: boolean;
  latency_ms: number;
  last_error: string;
  detail: Record<string, unknown>;
  checked_at: string | null;
}

/** 独占分配矩阵中的一行 */
export interface KnowledgeGrantItem {
  employee_id: string;
  employee_name: string;
  employee_type: string;
  knowledge_base_ids: string[];
}

export interface KnowledgeGrantMatrix {
  employees: KnowledgeGrantItem[];
  knowledge_bases: KnowledgeBase[];
  /** { knowledge_base_id: employee_id }，用于把「已被他人占用」的库置灰 */
  occupancy: Record<string, string>;
}

export interface ChatSession {
  session_id: string;
  employee_id: string;
  trace_id: string;
  created_at: string;
}

export interface ChatMessage {
  session_id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_cards: unknown[];
}

export interface TaskRun {
  id: string;
  team_id: string;
  conversation_id: string | null;
  trigger_message_seq: number | null;
  trace_id: string;
  request: string;
  status: string;
  subtasks: TaskSubtask[];
  summary: string;
  source: 'builtin' | 'agentteams';
  created_at: string;
}

export interface TaskSubtask {
  worker_id: string;
  worker_no: string;
  summary: string;
  plugin_ids: string[];
  status: "pending" | "running" | "completed" | "approval" | "denied" | "failed";
  result: string | null;
  approval: { policy_id?: string; reason?: string } | null;
  collaboration_status?: 'planned' | 'collaborating' | 'acknowledged' | 'reported' | 'unavailable';
  collaboration_messages?: string[];
  execution_mode?: 'pending' | 'demo_adapter' | 'knowledge_adapter' | 'harness' | 'failed';
  runtime_mode?: 'pending' | 'demo_adapter' | 'knowledge_adapter' | 'harness' | 'failed';
  runtime_context_id?: string;
  runtime_summary?: string;
  tool_name?: string;
  tool_type?: string;
}

export interface WorkspacePlugin {
  plugin_id: string;
  name: string;
  type: string;
  action: string;
  decision_mode: "allow" | "deny" | "approval";
  data_level: string;
}

export interface WorkspaceKb {
  knowledge_base_id: string;
  name: string;
  data_level: string;
  description: string;
  accessible: boolean;
  decision: string;
  /** 数据源可达性：accessible 是「有权限」，available 是「现在真的能用」 */
  available?: boolean;
  active_source?: string | null;
}

export interface WorkspaceSecurity {
  location: string;
  internet: string;
  max_data_level: string;
  allowed_domains: string[];
}

export interface Workspace {
  employee: Employee;
  role_prompt: string;
  plugins: WorkspacePlugin[];
  knowledge_bases: WorkspaceKb[];
  security: WorkspaceSecurity;
}

export interface Skill {
  id: string;
  owner_human_no: string;
  name: string;
  description: string;
  content: string;
  status: 'active' | 'disabled';
  created_at: string;
}

export interface WorkplaceActor {
  employee_no: string;
  name: string;
  department: string;
  employment_type: 'formal' | 'intern';
}

export interface ConversationParticipant {
  employee_no: string;
  name: string;
  role: 'organizer' | 'member';
  employee_type: string;
}

export interface ConversationMessage {
  id: number;
  conversation_id: string;
  participant_no: string;
  participant_name: string;
  role: 'user' | 'assistant';
  content: string;
  tool_cards: unknown[];
  attachment_ids?: string[];
  seq: number;
  /** 非空表示该回复已被同一轮的新回答取代，界面折叠为「上一版回复」。 */
  superseded_at?: string | null;
}

export interface ConversationAttachment {
  attachment_id: string;
  conversation_id: string;
  filename: string;
  file_type: string;
  mime_type: string;
  size: number;
  status: 'pending' | 'processing' | 'ready' | 'failed' | 'ocr_required' | 'conversion_required' | 'deleted';
  processing_hint: string | null;
  error_code: string;
  page_count: number | null;
  sheet_count: number | null;
  chunk_count: number;
  message_seq: number | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  kind: 'direct' | 'group';
  title: string;
  owner_human_no: string;
  participants: ConversationParticipant[];
  messages: ConversationMessage[];
  tasks: TaskRun[];
  updated_at: string;
}

export interface ConversationSummary {
  id: string;
  kind: 'direct' | 'group';
  title: string;
  owner_human_no: string;
  participants: ConversationParticipant[];
  last_message: string;
  updated_at: string;
}

/** 会话检索结果：会话摘要 + 命中元信息（侧栏片段展示与点击定位用）。 */
export interface ConversationSearchHit {
  conversation: ConversationSummary;
  /** name = 命中会话名/成员名；content = 命中历史消息正文。 */
  matched_in: 'name' | 'content';
  /** 该会话命中的消息条数。 */
  match_count: number;
  /** 命中的最近一条消息 seq，供前端滚动定位；仅名称命中时为 null。 */
  match_seq: number | null;
  match_role: 'user' | 'assistant' | '';
  /** assistant 时的说话人展示名；user 为空。 */
  match_speaker: string;
  /** 已压平 Markdown 的命中片段（带前后省略号）。 */
  snippet: string;
}

export interface AgentExecution {
  id: string;
  conversation_id: string;
  trigger_message_seq: number;
  trace_id: string;
  primary_employee_id: string;
  conversation_generation?: number;
  retry_of_execution_id?: string | null;
  status: 'queued' | 'running' | 'streaming' | 'waiting_approval' | 'completed' | 'failed' | 'cancelled';
  stage: string;
  error_code: string;
  error_message: string;
  retryable: boolean;
  started_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface ConversationRunReply {
  execution_id: string;
  trigger_message_seq: number;
  conversation: Conversation;
}

export interface AgentExecutionEvent {
  event_seq: number;
  event_type: string;
  actor_employee_id: string;
  stage: string;
  status: string;
  title: string;
  detail: string;
  knowledge_base_id: string | null;
  target_agent_id: string | null;
  hit_count: number | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface AgentExecutionDetail {
  execution: AgentExecution;
  events: AgentExecutionEvent[];
}

export interface WorkplaceHome {
  actor: WorkplaceActor;
  twin: Employee;
  available_employees: Employee[];
  skills: Skill[];
  recent_conversations: ConversationSummary[];
}

export interface WorkflowEmployee {
  employee_no: string;
  name: string;
  type: string;
}

export interface Workflow {
  plugin_id: string;
  name: string;
  type: string;
  data_level: string;
  description: string;
  steps: string[];
  demo_prompt: string;
  authorized_employees: WorkflowEmployee[];
  owner_employee: WorkflowEmployee | null;
}

export interface Citation {
  citation_id: string;
  document_title: string;
  /** 命中文档标题（仅元数据，最多 5 条）；旧数据可能为空。 */
  document_titles?: string[];
  knowledge_base_name: string;
  source: string;
  version: string | null;
  preview_available: boolean;
  /** 非空表示该来源由被委派的岗位AI员工检索（分身自己查的为空）。 */
  agent_employee_id?: string | null;
  agent_employee_name?: string | null;
  hit_count?: number;
}

export interface ToolCard {
  plugin_id: string;
  name: string;
  decision: string;
  source: string | null;
  policy_id: string | null;
  reason: string | null;
  citation: Citation | null;
  agent_employee_id?: string | null;
  agent_employee_name?: string | null;
  knowledge_base_id?: string | null;
  hit_count?: number;
}

export interface ChatReply {
  session_id: string;
  trace_id: string;
  message: string;
  tool_cards: ToolCard[];
  policy_denied: ToolCard | null;
}

export interface Account {
  username: string;
  employee_no: string;
  name: string;
  department: string;
  employment_type: 'formal' | 'intern';
  roles: string[];
  must_change_password: boolean;
  /** 登录人头像（复用本人分身的演示头像）；没有分身时为 null，前端回退姓名首字。 */
  avatar_url?: string | null;
}

export interface LoginReply {
  account: Account;
  expires_at: string;
}

export interface AgentProfile {
  employee_id: string;
  display_name: string;
  identity_kind: 'human_twin' | 'role_employee';
  owner_human_no: string;
  department: string;
  responsibilities: string[];
  knowledge_domains: string[];
  accepts_tasks: string[];
  delegation_policy: 'bounded_single' | 'none';
  fallback_employee_id: string | null;
  persona_status: string;
  persona_version: number;
  runtime_engine: 'harness';
  runtime_state: string;
  container_name: string;
  /** Harness 容器实际使用的底座镜像（含 tag），取自容器标签；查不到时回落部署声明值。 */
  runtime_image: string;
  /** dsh 框架版本（L1），按该AI员工的 runtime 目录名还原，灰度切换后逐行不同。 */
  runtime_version: string;
  /** Profile 版本（L2 插件开关 / 模型路由层）。 */
  runtime_profile_rev: string;
  knowledge_base_ids: string[];
}

/** 运维页顶部「运行环境」汇总里的一个镜像。 */
export interface RuntimeImage {
  role: string;
  /** 完整镜像名（含 tag），如 dwp-backend:0.4.0-offline。 */
  name: string;
  /** 补充说明，如 Harness 底座的「dsh 0.1.5-rc.1 · profile v1-20260910」。 */
  note: string;
}

export interface RuntimeInfo {
  /** 平台交付版本 tag（DWP_IMAGE_TAG）。 */
  delivery_tag: string;
  images: RuntimeImage[];
}

export interface DirectoryUser {
  provider: string;
  external_user_id: string;
  employee_no: string;
  name: string;
  department: string;
  employment_type: string;
  status: string;
  default_twin_id: string | null;
}
