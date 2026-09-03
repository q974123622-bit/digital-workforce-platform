// 数字员工平台 — 前后端统一类型定义
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
  kind: 'knowledge' | 'delegation' | 'instruction';
  source_type: 'knowledge_base' | 'platform_tool' | 'skill';
  description: string;
  actions: string[];
  status: 'available' | 'approval' | 'unauthorized' | 'runtime_unavailable' | 'disabled';
  decision: 'allow' | 'approval' | 'deny';
  reason: string;
  authorized: boolean;
  installed: boolean;
  healthy: boolean;
  data_level: string | null;
  knowledge_base_id: string | null;
  target_employee_ids: string[];
  example_prompts: string[];
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
  seq: number;
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

export interface AgentExecution {
  id: string;
  conversation_id: string;
  trigger_message_seq: number;
  trace_id: string;
  primary_employee_id: string;
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

export interface ChatReply {
  session_id: string;
  trace_id: string;
  message: string;
  tool_cards: {
    plugin_id: string;
    name: string;
    decision: string;
    policy_id: string | null;
    reason: string | null;
  }[];
  policy_denied: {
    plugin_id: string;
    name: string;
    decision: string;
    policy_id: string | null;
    reason: string | null;
  } | null;
}

export interface Account {
  username: string;
  employee_no: string;
  name: string;
  department: string;
  employment_type: 'formal' | 'intern';
  roles: string[];
  must_change_password: boolean;
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
  knowledge_base_ids: string[];
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
