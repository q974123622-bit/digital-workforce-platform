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
  source_human_no: string | null;
  owner_human_no: string;
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

export interface Plugin {
  id: string;
  name: string;
  type: string;
  endpoint_ref: string;
  data_level: string;
  status: string;
  description: string;
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
  trigger_message_seq: number | null;
  trace_id: string;
  request: string;
  status: string;
    subtasks: TaskSubtask[];
    summary: string;
    source?: 'builtin' | 'agentteams';
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
