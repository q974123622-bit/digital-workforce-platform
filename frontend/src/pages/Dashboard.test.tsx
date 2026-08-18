import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Dashboard from './Dashboard';
import type { AuditEvent, Employee, Plugin, Team } from '@dwp/shared-schema';

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

const employees: Employee[] = [
  {
    id: '1',
    employee_no: 'DT-E10281',
    name: '张三的数字分身',
    type: 'twin',
    source_human_no: 'E10281',
    owner_human_no: 'E10281',
    department: '架构部',
    role_prompt: '',
    status: 'active',
    runtime_type: 'demo',
    runtime_ref: null,
    location: 'remote',
    internet: 'deny',
    max_data_level: 'L2',
    allowed_domains: [],
    grants: [],
  },
  {
    id: '2',
    employee_no: 'VE-0001',
    name: '新员工入职助手',
    type: 'virtual',
    source_human_no: null,
    owner_human_no: 'E10021',
    department: '人力资源部',
    role_prompt: '',
    status: 'active',
    runtime_type: 'harness',
    runtime_ref: null,
    location: 'remote',
    internet: 'deny',
    max_data_level: 'L2',
    allowed_domains: [],
    grants: [],
  },
  {
    id: '3',
    employee_no: 'RPA-0001',
    name: '报表机器人',
    type: 'rpa',
    source_human_no: null,
    owner_human_no: 'E20999',
    department: '财务部',
    role_prompt: '',
    status: 'active',
    runtime_type: 'demo',
    runtime_ref: null,
    location: 'remote',
    internet: 'allow',
    max_data_level: 'L3',
    allowed_domains: [],
    grants: [],
  },
];

const plugins: Plugin[] = [
  {
    id: 'knowledge-l1',
    name: '公开知识库',
    type: 'knowledge',
    endpoint_ref: 'mock://kb/l1',
    data_level: 'L1',
    status: 'active',
    description: '公开知识检索',
  },
];

const teams: Team[] = [
  { id: 'TEAM-ONBOARD', name: '新员工入职团队', leader_employee_id: 'VE-0001', description: '入职准备' },
];

const audit: AuditEvent[] = [
  {
    id: 1,
    trace_id: 'T-DEMO-001',
    ts: new Date().toISOString(),
    actor: 'DT-E10281',
    employee_id: 'DT-E10281',
    team_id: null,
    plugin_id: 'knowledge-l2',
    action: 'read',
    decision: 'allow',
    reason: 'POLICY-001 已授权',
    result_summary: null,
  },
];

const mockApi = () =>
  vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/api/v1/employees')) return Promise.resolve(json(employees));
    if (url.includes('/api/v1/plugins')) return Promise.resolve(json(plugins));
    if (url.includes('/api/v1/teams')) return Promise.resolve(json(teams));
    if (url.includes('/api/v1/audit')) return Promise.resolve(json(audit));
    return Promise.resolve(json({ status: 'ok' }));
  });

const renderDashboard = () =>
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
      </Routes>
    </MemoryRouter>,
  );

describe('Dashboard', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockApi());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('渲染统计卡片、能力入口与最近审计动态', async () => {
    renderDashboard();

    expect(await screen.findByText('数字员工总数')).toBeInTheDocument();
    expect(screen.getAllByText('数字分身').length).toBeGreaterThan(0);
    expect(screen.getAllByText('虚拟员工').length).toBeGreaterThan(0);
    expect(screen.getAllByText('RPA').length).toBeGreaterThan(0);
    expect(screen.getByText('插件中心')).toBeInTheDocument();
    expect(await screen.findByText('最近审计动态')).toBeInTheDocument();
    expect(screen.getByText(/已授权/)).toBeInTheDocument();
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
    expect(await screen.findByText('数字员工总数')).toBeInTheDocument();
  });
});
