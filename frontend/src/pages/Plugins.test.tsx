import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import Plugins from './Plugins';

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

afterEach(() => vi.unstubAllGlobals());

it('治理页展示审核意见，并提供下架入口', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/api/v1/admin/plugins')) {
      return json([{
        id: 'PLG-X', name: '测试插件', plugin_type: 'mcp', scope: 'shared',
        current_version: '1.0.0', status: 'active', owner_human_no: null,
      }]);
    }
    if (url.endsWith('/api/v1/admin/plugin-submissions')) {
      return json([{
        id: 7, plugin_id: 'PLG-X', name: '测试插件', plugin_type: 'mcp', version: '1.0.0',
        data_level: 'L2', deployment_mode: 'external', review_status: 'rejected',
        publish_status: 'rejected', submitted_by: 'E10281', review_note: '缺少输出模板',
        created_at: '2026-09-18T02:00:00',
      }]);
    }
    if (url.endsWith('/api/v1/admin/agent-plugin-bindings')) return json([]);
    if (url.endsWith('/api/v1/admin/memory/health')) {
      return json({ mode: 'mock', status: 'healthy', memories: 0, pending_jobs: 0, failed_jobs: 0 });
    }
    if (url.endsWith('/api/v1/agents')) return json([]);
    return json({});
  }));

  render(<Plugins />);
  expect(await screen.findByText('缺少输出模板')).toBeInTheDocument();
  // antd Button 会在两个汉字之间插入空格，用正则匹配可读名称
  expect(screen.getByRole('button', { name: /下\s*架/ })).toBeInTheDocument();
});

it('MCP 接入页签展示已登记服务、连通状态与掩码密钥', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/api/v1/admin/mcp/plugins')) {
      return json({
        services: [{
          plugin_id: 'PLG-MCP1', name: '行情数据 MCP', description: '行情查询', scope: 'shared',
          owner_human_no: null, mcp_category: 'fund', data_level: 'L1', status: 'active',
          current_version: '1.0.0', url: 'http://10.0.0.9:9100/mcp', tool_prefix: 'mcp_1a2b3c4d__',
          authenticated: true, auth_header_name: 'x-api-key', secret_hint: '****alue',
          tools_count: 2, latency_ms: 42, checked_at: '2026-09-21T02:00:00',
          server_info: {}, runtime_state: 'running', runtime_health: 'healthy', pending_version_id: null,
        }],
        credential_encryption_available: true,
      });
    }
    if (url.endsWith('/api/v1/admin/plugins') || url.endsWith('/api/v1/admin/plugin-submissions')
      || url.endsWith('/api/v1/admin/agent-plugin-bindings') || url.endsWith('/api/v1/agents')) return json([]);
    if (url.endsWith('/api/v1/admin/memory/health')) {
      return json({ mode: 'mock', status: 'healthy', memories: 0, pending_jobs: 0, failed_jobs: 0 });
    }
    return json({});
  }));

  render(<Plugins />);
  fireEvent.click(await screen.findByRole('tab', { name: 'MCP 接入' }));

  expect(await screen.findByText('行情数据 MCP')).toBeInTheDocument();
  // 统计卡与状态徽标都叫「连通正常」，这里只断言状态徽标
  expect(screen.getByText('连通正常', { selector: '.ant-tag' })).toBeInTheDocument();
  // 只展示掩码，绝不回显明文密钥
  expect(screen.getByText('****alue')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /接入新的 MCP 服务/ })).toBeInTheDocument();
});

it('能力中心只把授权、Policy 与 Harness 都就绪的能力标为可用', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.endsWith('/api/v1/admin/plugins') || url.endsWith('/api/v1/admin/plugin-submissions') || url.endsWith('/api/v1/admin/agent-plugin-bindings')
      ? []
      : url.endsWith('/api/v1/admin/memory/health')
        ? { mode: 'mock', status: 'healthy', memories: 0, pending_jobs: 0, failed_jobs: 0 }
        : url.endsWith('/api/v1/agents') ? [{
      employee_id: 'AI-GENERAL', display_name: 'AI员工平台', identity_kind: 'role_employee',
      owner_human_no: 'E10281', department: '综合服务', responsibilities: [], knowledge_domains: [],
      accepts_tasks: ['knowledge_question'], delegation_policy: 'none', fallback_employee_id: null,
      persona_status: 'published', persona_version: 1, runtime_engine: 'harness', runtime_state: 'ready',
      container_name: 'dwp-harness-ai-general', knowledge_base_ids: ['KB-IT-SERVICE'],
    }] : {
      employee_id: 'AI-GENERAL', display_name: 'AI员工平台', identity_kind: 'role_employee',
      runtime_engine: 'harness', runtime_state: 'ready', container_name: 'dwp-harness-ai-general',
      knowledge_mode: 'internal', available_count: 1, attention_count: 1,
      capabilities: [
        { id: 'knowledge:KB-IT-SERVICE', name: 'IT 服务知识库', kind: 'knowledge', source_type: 'knowledge_base', description: 'IT 服务问答', actions: ['read'], status: 'available', decision: 'allow', reason: '已就绪', authorized: true, installed: true, healthy: true, data_level: 'L2', knowledge_base_id: 'KB-IT-SERVICE', target_employee_ids: [], example_prompts: ['VPN 怎么申请？'] },
        { id: 'knowledge:KB-SECURITIES', name: '证券业务知识库', kind: 'knowledge', source_type: 'knowledge_base', description: '证券业务', actions: ['read'], status: 'unauthorized', decision: 'deny', reason: '未授权', authorized: false, installed: true, healthy: false, data_level: 'L2', knowledge_base_id: 'KB-SECURITIES', target_employee_ids: [], example_prompts: [] },
      ],
    };
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }));

  render(<Plugins />);
  fireEvent.click(await screen.findByText('运行能力核验'));
  expect(await screen.findByText('IT 服务知识库')).toBeInTheDocument();
  expect(screen.getByText('现在可用')).toBeInTheDocument();
  expect(screen.getByText('证券业务知识库')).toBeInTheDocument();
  expect(screen.getAllByText('未授权').length).toBeGreaterThan(0);
  expect(screen.getByText('内部知识引擎')).toBeInTheDocument();
});
