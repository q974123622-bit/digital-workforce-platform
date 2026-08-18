import type { AuditEvent, Employee, KnowledgeBase, Plugin, Policy, Team, TeamDetail } from '@dwp/shared-schema';

const BASE = '/api/v1';

type RequestOptions = RequestInit & { absolute?: boolean };

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const url = init?.absolute ? path : `${BASE}${path}`;
  const { absolute: _absolute, ...fetchInit } = init ?? {};
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(fetchInit.headers ?? {}) },
    ...fetchInit,
  });
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
  listPolicies: () => request<Policy[]>('/policies'),
  listAudit: (params?: { decision?: string }) => request<AuditEvent[]>(`/audit${qs(params)}`),
  listTeams: () => request<Team[]>('/teams'),
  getTeam: (teamId: string) => request<TeamDetail>(`/teams/${encodeURIComponent(teamId)}`),
  listKnowledgeBases: () => request<KnowledgeBase[]>('/knowledge-bases'),
};
