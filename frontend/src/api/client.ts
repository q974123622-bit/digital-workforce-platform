import type { AuditEvent, ChatMessage, Employee, KnowledgeBase, Plugin, Policy, Team, TeamDetail } from '@dwp/shared-schema';
import {
  mockAudit,
  mockEmployees,
  mockKnowledgeBases,
  mockPlugins,
  mockPolicies,
  mockTeamDetail,
  mockTeams,
} from './mock';

const BASE = '/api/v1';

// 后端未启动时，根据请求路径返回对应 Mock 数据（TODO: 后端跑起来后删除）
function resolveMock(path: string): unknown {
  const p = path.split('?')[0]; // 去掉查询参数
  if (p === '/employees') return mockEmployees;
  if (p.startsWith('/employees/')) {
    const parts = p.split('/').filter(Boolean); // ['employees', '<no>', ...]
    if (parts.length === 2) {
      const no = decodeURIComponent(parts[1]);
      return mockEmployees.find((e) => e.employee_no === no);
    }
    return undefined; // 如 /employees/{no}/chat → 交给 Chat.tsx 自己的 Mock
  }
  if (p === '/plugins') return mockPlugins;
  if (p === '/policies') return mockPolicies;
  if (p.startsWith('/audit')) return mockAudit;
  if (p === '/teams') return mockTeams;
  if (p.startsWith('/teams/')) return mockTeamDetail;
  if (p === '/knowledge-bases') return mockKnowledgeBases;
  return undefined;
}

// 聊天响应（对应 API_CONTRACT.md §3.5，后端 Sprint 4 已实现，整段 JSON 返回）
export interface ChatResponse {
  session_id: string;
  trace_id: string;
  message: string;
  tool_cards: { plugin_id: string; name: string; decision: string }[];
  policy_denied?: { policy_id: string; reason: string; plugin_id: string } | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      ...init,
    });
  } catch {
    // 后端未启动：回退到前端 Mock
    const mock = resolveMock(path);
    if (mock !== undefined) return mock as T;
    throw new Error('后端未启动，且该接口无 Mock 数据');
  }
  if (!res.ok) {
    // 后端未启动时 Vite 代理会返回 500，这里同样回退到 Mock
    const mock = resolveMock(path);
    if (mock !== undefined) return mock as T;
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
  health: () => request<{ status: string }>('/health', { headers: {} }),
  listEmployees: (params?: { type?: string }) => request<Employee[]>(`/employees${qs(params)}`),
  getEmployee: (employeeNo: string) => request<Employee>(`/employees/${encodeURIComponent(employeeNo)}`),
  listPlugins: () => request<Plugin[]>('/plugins'),
  listPolicies: () => request<Policy[]>('/policies'),
  listAudit: (params?: { decision?: string }) => request<AuditEvent[]>(`/audit${qs(params)}`),
  listTeams: () => request<Team[]>('/teams'),
  getTeam: (teamId: string) => request<TeamDetail>(`/teams/${encodeURIComponent(teamId)}`),
  listKnowledgeBases: () => request<KnowledgeBase[]>('/knowledge-bases'),
  chat: (employeeNo: string, body: { message: string; session_id?: string }) =>
    request<ChatResponse>(`/employees/${encodeURIComponent(employeeNo)}/chat`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  getChatMessages: (sessionId: string) =>
    request<ChatMessage[]>(`/chat/sessions/${encodeURIComponent(sessionId)}/messages`),
};
