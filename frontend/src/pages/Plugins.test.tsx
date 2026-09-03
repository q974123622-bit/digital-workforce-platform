import { afterEach, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import Plugins from './Plugins';

afterEach(() => vi.unstubAllGlobals());

it('能力中心只把授权、Policy 与 Harness 都就绪的能力标为可用', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.endsWith('/api/v1/agents') ? [{
      employee_id: 'AI-GENERAL', display_name: 'AI员工平台', identity_kind: 'role_employee',
      owner_human_no: 'E10281', department: '综合服务', responsibilities: [], knowledge_domains: [],
      accepts_tasks: ['knowledge_question'], delegation_policy: 'none', fallback_employee_id: null,
      persona_status: 'published', persona_version: 1, runtime_engine: 'harness', runtime_state: 'ready',
      container_name: 'dwp-harness-ai-general', knowledge_base_ids: ['KB-IT-SERVICE'],
    }] : {
      employee_id: 'AI-GENERAL', display_name: 'AI员工平台', identity_kind: 'role_employee',
      runtime_engine: 'harness', runtime_state: 'ready', container_name: 'dwp-harness-ai-general',
      knowledge_mode: 'mock', available_count: 1, attention_count: 1,
      capabilities: [
        { id: 'knowledge:KB-IT-SERVICE', name: 'IT 服务知识库', kind: 'knowledge', source_type: 'knowledge_base', description: 'IT 服务问答', actions: ['read'], status: 'available', decision: 'allow', reason: '已就绪', authorized: true, installed: true, healthy: true, data_level: 'L2', knowledge_base_id: 'KB-IT-SERVICE', target_employee_ids: [], example_prompts: ['VPN 怎么申请？'] },
        { id: 'knowledge:KB-SECURITIES', name: '证券业务知识库', kind: 'knowledge', source_type: 'knowledge_base', description: '证券业务', actions: ['read'], status: 'unauthorized', decision: 'deny', reason: '未授权', authorized: false, installed: true, healthy: false, data_level: 'L2', knowledge_base_id: 'KB-SECURITIES', target_employee_ids: [], example_prompts: [] },
      ],
    };
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }));

  render(<Plugins />);
  expect(await screen.findByText('IT 服务知识库')).toBeInTheDocument();
  expect(screen.getByText('现在可用')).toBeInTheDocument();
  expect(screen.getByText('证券业务知识库')).toBeInTheDocument();
  expect(screen.getAllByText('未授权').length).toBeGreaterThan(0);
  expect(screen.getByText('Mock 知识')).toBeInTheDocument();
});
