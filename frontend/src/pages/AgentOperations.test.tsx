import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import AgentOperations from './AgentOperations';
import type { AgentProfile, RuntimeInfo } from '@dwp/shared-schema';

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

const agents: AgentProfile[] = [
  {
    employee_id: 'AI-GENERAL', display_name: 'AI员工平台', identity_kind: 'role_employee', owner_human_no: 'E10021',
    department: '综合服务', responsibilities: [], knowledge_domains: [], accepts_tasks: [],
    delegation_policy: 'none', fallback_employee_id: null, persona_status: 'published', persona_version: 1,
    runtime_engine: 'harness', runtime_state: 'ready', container_name: 'dwp-harness-ai-general',
    runtime_image: 'dwp-dsh:rc6', runtime_version: '0.1.5-rc.1', runtime_profile_rev: 'v1-20260910',
    knowledge_base_ids: ['KB-IT-SERVICE'],
  },
];

const runtimeInfo: RuntimeInfo = {
  delivery_tag: '0.4.0-offline',
  images: [
    { role: '后端服务', name: 'dwp-backend:0.4.0-offline', note: '' },
    { role: '前端控制台', name: 'dwp-frontend:0.4.0-offline', note: '' },
    { role: 'Harness 底座', name: 'dwp-dsh:rc6', note: 'dsh 0.1.5-rc.1' },
  ],
};

describe('AgentOperations', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/agents/runtime-info')) return Promise.resolve(json(runtimeInfo));
        if (url.includes('/agents')) return Promise.resolve(json(agents));
        return Promise.resolve(json({ status: 'ok' }));
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('Harness 列同时显示容器名、底座镜像与 dsh 版本', async () => {
    render(<AgentOperations />);

    expect(await screen.findByText('dwp-harness-ai-general')).toBeInTheDocument();
    expect(screen.getByText('dwp-dsh:rc6 · dsh 0.1.5-rc.1')).toBeInTheDocument();
  });

  it('顶部汇总交付版本与三个平台镜像', async () => {
    render(<AgentOperations />);

    expect(await screen.findByText('0.4.0-offline')).toBeInTheDocument();
    expect(screen.getByText('dwp-backend:0.4.0-offline')).toBeInTheDocument();
    expect(screen.getByText('dwp-frontend:0.4.0-offline')).toBeInTheDocument();
    expect(screen.getByText('· dsh 0.1.5-rc.1')).toBeInTheDocument();
  });
});
