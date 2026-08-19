import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import App from './App';
import { CurrentUserProvider } from './context/CurrentUserContext';
import type { Employee, WorkplaceHome } from '@dwp/shared-schema';

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

const twin: Employee = {
  id: 'DT-E10281',
  employee_no: 'DT-E10281',
  name: '张三的数字分身',
  type: 'twin',
  source_human_no: 'E10281',
  owner_human_no: 'E10281',
  department: '架构部',
  role_prompt: '我是张三的数字分身。',
  status: 'active',
  runtime_type: 'demo',
  runtime_ref: null,
  location: 'remote',
  internet: 'deny',
  max_data_level: 'L2',
  allowed_domains: [],
  grants: [],
};

const workplace: WorkplaceHome = {
  actor: { employee_no: 'E10281', name: '张三', department: '架构部', employment_type: 'formal' },
  twin,
  available_employees: [],
  skills: [],
  recent_conversations: [],
};

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const routes: Record<string, unknown> = {
        '/api/v1/workplace?actor_no=E10281': workplace,
        '/api/v1/conversations?actor_no=E10281': [],
        '/health': { status: 'ok' },
      };
      const route = Object.entries(routes).find(([key]) => url.endsWith(key));
      return json(route ? route[1] : []);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('App', () => {
  it('默认进入我的职场', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <CurrentUserProvider>
          <App />
        </CurrentUserProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText('数字员工平台')).toBeInTheDocument();
    expect(screen.getAllByText('我的职场').length).toBeGreaterThan(0);
    expect(await screen.findByText('早上好，张三')).toBeInTheDocument();
  });

  it('管理后台数据总览可访问', async () => {
    render(
      <MemoryRouter initialEntries={['/admin']}>
        <CurrentUserProvider>
          <App />
        </CurrentUserProvider>
      </MemoryRouter>,
    );
    expect(screen.getAllByText('数据总览').length).toBeGreaterThan(0);
    expect(await screen.findByText('数字员工总数')).toBeInTheDocument();
  });
});
