import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Dashboard from './Dashboard';
import type { AgentProfile, AuditEvent, KnowledgeBase } from '@dwp/shared-schema';
import type { PluginSubmission } from '../api/client';

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

const submissions: PluginSubmission[] = [
  { id: 1, name: '报销制度速答', plugin_type: 'skill', data_level: 'L1', deployment_mode: 'instruction', review_status: 'pending', publish_status: 'unpublished', submitted_by: 'E10281', review_note: '', created_at: '2026-08-19T08:00:00' },
];

const agents: AgentProfile[] = [
  {
    employee_id: 'VE-0001', display_name: '新员工入职助手', identity_kind: 'role_employee', owner_human_no: 'E10021',
    department: '人力资源部', responsibilities: [], knowledge_domains: [], accepts_tasks: [],
    delegation_policy: 'bounded_single', fallback_employee_id: null, persona_status: 'active', persona_version: 1,
    runtime_engine: 'harness', runtime_state: 'ready', container_name: 'harness-ve-0001', knowledge_base_ids: [],
    runtime_image: 'dwp-dsh:rc6', runtime_version: '0.1.5-rc.1', runtime_profile_rev: 'v1-20260910',
  },
  {
    employee_id: 'VE-0002', display_name: 'IT 助理', identity_kind: 'role_employee', owner_human_no: 'E10021',
    department: 'IT 部', responsibilities: [], knowledge_domains: [], accepts_tasks: [],
    delegation_policy: 'none', fallback_employee_id: null, persona_status: 'active', persona_version: 1,
    runtime_engine: 'harness', runtime_state: 'crashed', container_name: 'harness-ve-0002', knowledge_base_ids: [],
    runtime_image: 'dwp-dsh:rc6', runtime_version: '0.1.5-rc.1', runtime_profile_rev: 'v1-20260910',
  },
];

const knowledgeBases: KnowledgeBase[] = [
  { id: 'KB-1', name: '公开制度知识库', level: 'L1', domain: '制度', description: '', status: 'active', doc_path: null, available: true },
  { id: 'KB-2', name: '内部流程知识库', level: 'L2', domain: '流程', description: '', status: 'active', doc_path: null, available: false, unavailable_reason: '数据源不可达' },
];

const audit: AuditEvent[] = [
  {
    id: 1, trace_id: 'T-DEMO-001', ts: new Date().toISOString(), actor: 'DT-E10281', employee_id: 'DT-E10281',
    team_id: null, plugin_id: 'knowledge-l2', action: 'read', decision: 'allow', reason: 'POLICY-001 已授权', result_summary: null,
  },
];

const mockApi = () =>
  vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/admin/plugin-submissions')) return Promise.resolve(json(submissions));
    if (url.includes('/agents')) return Promise.resolve(json(agents));
    if (url.includes('/knowledge-bases')) return Promise.resolve(json(knowledgeBases));
    if (url.includes('/admin/memory/health')) return Promise.resolve(json({ mode: 'mock', status: 'healthy', memories: 10, pending_jobs: 2, failed_jobs: 1 }));
    if (url.includes('/audit')) return Promise.resolve(json(audit));
    return Promise.resolve(json({ status: 'ok' }));
  });

const renderDashboard = () =>
  render(
    <MemoryRouter initialEntries={['/']}>
      <Dashboard />
    </MemoryRouter>,
  );

describe('Dashboard', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockApi());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('渲染四类待办与最近审计动态', async () => {
    renderDashboard();

    expect(await screen.findByText('待审核插件')).toBeInTheDocument();
    expect(screen.getByText('不可用AI员工')).toBeInTheDocument();
    expect(screen.getByText('知识源异常')).toBeInTheDocument();
    expect(screen.getByText('记忆同步积压')).toBeInTheDocument();
    expect(await screen.findByText('最近审计动态')).toBeInTheDocument();
    expect(screen.getByText(/已授权/)).toBeInTheDocument();
  });

  it('待办数字正确反映异常与积压', async () => {
    renderDashboard();

    // 待审核插件 1、不可用员工 1（crashed）、知识异常 1（disconnected）、记忆积压 3（2+1）
    expect(await screen.findByText('待审核插件')).toBeInTheDocument();
    const statisticValues = screen.getAllByText('1');
    expect(statisticValues.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('加载失败时展示错误态并支持重试', async () => {
    let fail = true;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        if (fail) return Promise.reject(new Error('network down'));
        return mockApi()(input);
      }),
    );

    renderDashboard();
    expect(await screen.findByText('数据加载失败')).toBeInTheDocument();

    fail = false;
    fireEvent.click(screen.getByRole('button', { name: /重新加载/ }));
    expect(await screen.findByText('待审核插件')).toBeInTheDocument();
  });
});
