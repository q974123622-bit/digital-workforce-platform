import type {
  AuditEvent,
  Capability,
  ChatMessage,
  ChatReply,
  Conversation,
  ConversationSummary,
  Employee,
  KnowledgeBase,
  Plugin,
  Policy,
  Skill,
  TaskRun,
  Team,
  TeamDetail,
  Workflow,
  Workspace,
  WorkplaceHome,
} from '@dwp/shared-schema';

const BASE = '/api/v1';

type RequestOptions = RequestInit & { absolute?: boolean };

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const url = init?.absolute ? path : `${BASE}${path}`;
  const { absolute: _absolute, ...fetchInit } = init ?? {};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  let res: Response;
  try {
    res = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...(fetchInit.headers ?? {}) },
      ...fetchInit,
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timer);
    if (controller.signal.aborted) {
      throw new Error('请求超时，请稍后重试');
    }
    throw err;
  }
  clearTimeout(timer);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      message = body?.error?.message ?? body?.detail ?? message;
    } catch {
      // 保留默认错误信息
    }
    throw new Error(message);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function qs(params?: Record<string, string | undefined>): string {
  if (!params) return '';
  const parts = Object.entries(params).filter(([, v]) => v !== undefined && v !== '');
  return parts.length ? `?${parts.map(([k, v]) => `${k}=${encodeURIComponent(v!)}`).join('&')}` : '';
}

export const api = {
  health: () => request<{ status: string }>('/health', { absolute: true }),
  listEmployees: (params?: { type?: string }) => request<Employee[]>(`/employees${qs(params)}`),
  getEmployee: (employeeNo: string) => request<Employee>(`/employees/${encodeURIComponent(employeeNo)}`),
  listPlugins: () => request<Plugin[]>('/plugins'),
  listCapabilities: (actorNo: string) =>
    request<Capability[]>(`/capabilities?actor_no=${encodeURIComponent(actorNo)}`),
  listPolicies: () => request<Policy[]>('/policies'),
  listAudit: (params?: { decision?: string }) => request<AuditEvent[]>(`/audit${qs(params)}`),
  listTeams: () => request<Team[]>('/teams'),
  getTeam: (teamId: string) => request<TeamDetail>(`/teams/${encodeURIComponent(teamId)}`),
  createTask: (teamId: string, requestText: string) =>
    request<TaskRun>(`/teams/${encodeURIComponent(teamId)}/tasks`, {
      method: 'POST',
      body: JSON.stringify({ request: requestText }),
    }),
  getTask: (teamId: string, taskId: string) =>
    request<TaskRun>(`/teams/${encodeURIComponent(teamId)}/tasks/${encodeURIComponent(taskId)}`),
  approveTask: (taskId: string, approve: boolean, actorNo: string) =>
    request<TaskRun>(`/tasks/${encodeURIComponent(taskId)}/approve`, {
      method: 'POST',
      body: JSON.stringify({ approve, actor_no: actorNo }),
    }),
  listKnowledgeBases: () => request<KnowledgeBase[]>('/knowledge-bases'),
  getWorkspace: (employeeNo: string) => request<Workspace>(`/employees/${encodeURIComponent(employeeNo)}/workspace`),
  chat: (employeeNo: string, message: string, sessionId?: string) =>
    request<ChatReply>(`/employees/${encodeURIComponent(employeeNo)}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message, session_id: sessionId ?? null }),
    }),
  listMessages: (sessionId: string) => request<ChatMessage[]>(`/chat/sessions/${encodeURIComponent(sessionId)}/messages`),
  getWorkplace: (actorNo: string) => request<WorkplaceHome>(`/workplace?actor_no=${encodeURIComponent(actorNo)}`),
  listSkills: (actorNo: string) => request<Skill[]>(`/skills?actor_no=${encodeURIComponent(actorNo)}`),
  createSkill: (payload: { actor_no: string; name: string; description?: string; content?: string }) =>
    request<Skill>('/skills', { method: 'POST', body: JSON.stringify(payload) }),
  updateSkill: (
    skillId: string,
    actorNo: string,
    patch: Partial<Pick<Skill, 'name' | 'description' | 'content' | 'status'>>,
  ) => request<Skill>(`/skills/${encodeURIComponent(skillId)}?actor_no=${encodeURIComponent(actorNo)}`, {
    method: 'PUT',
    body: JSON.stringify(patch),
  }),
  deleteSkill: (skillId: string, actorNo: string) =>
    request<void>(`/skills/${encodeURIComponent(skillId)}?actor_no=${encodeURIComponent(actorNo)}`, { method: 'DELETE' }),
  listConversations: (actorNo: string) =>
    request<ConversationSummary[]>(`/conversations?actor_no=${encodeURIComponent(actorNo)}`),
  createConversation: (payload: {
    actor_no: string;
    kind: 'direct' | 'group';
    title?: string;
    participant_employee_nos: string[];
  }) => request<Conversation>('/conversations', { method: 'POST', body: JSON.stringify(payload) }),
  getConversation: (conversationId: string) =>
    request<Conversation>(`/conversations/${encodeURIComponent(conversationId)}`),
  sendConversationMessage: (conversationId: string, actorNo: string, content: string) =>
    request<Conversation>(`/conversations/${encodeURIComponent(conversationId)}/messages`, {
      method: 'POST',
      body: JSON.stringify({ actor_no: actorNo, content }),
    }),
  addConversationParticipant: (conversationId: string, employeeNo: string) =>
    request<Conversation>(`/conversations/${encodeURIComponent(conversationId)}/participants`, {
      method: 'POST',
      body: JSON.stringify({ employee_no: employeeNo }),
    }),
  clearConversation: (conversationId: string, actorNo: string) =>
    request<{ ok: boolean }>(
      `/conversations/${encodeURIComponent(conversationId)}?actor_no=${encodeURIComponent(actorNo)}`,
      { method: 'DELETE' },
    ),
  listWorkflows: () => request<Workflow[]>('/workflows'),
};
