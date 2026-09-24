import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import Security from './Security';
import { AuthProvider } from '../context/AuthContext';

const ADMIN_ACCOUNT = {
  username: 'admin', employee_no: 'SYS-ADMIN', name: '平台管理员', department: '平台运营部',
  employment_type: 'formal', roles: ['agent_admin', 'security_admin', 'platform_admin'],
  must_change_password: false,
};

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

afterEach(() => vi.unstubAllGlobals());

function kb(id: string, name: string, dataLevel = 'L3') {
  return {
    id, name, level: dataLevel, data_level: dataLevel, resource_type: 'knowledge',
    domain: '客户敏感信息', description: '客户身份与风险信息', status: 'active', doc_path: null,
  };
}

function stubFetch(knowledgeBases: unknown[], options: { requests?: unknown[]; plugins?: unknown[] } = {}) {
  const posts: Array<{ url: string; body: Record<string, unknown> | null }> = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/api/v1/auth/me')) return json(ADMIN_ACCOUNT);
    if (init?.method === 'POST') {
      posts.push({ url, body: init.body ? JSON.parse(String(init.body)) : null });
      return json({ id: 1, status: 'pending' });
    }
    if (url.endsWith('/api/v1/knowledge-bases')) return json(knowledgeBases);
    if (url.includes('/api/v1/access-requests')) return json(options.requests ?? []);
    if (url.endsWith('/api/v1/admin/plugins')) return json(options.plugins ?? []);
    return json([]);
  }));
  return posts;
}

/** 访问申请面板依赖登录态判断审批通道，因此必须包在 AuthProvider 内。 */
function renderSecurity() {
  return render(<AuthProvider><Security /></AuthProvider>);
}

async function openAccessTab() {
  fireEvent.click(await screen.findByRole('tab', { name: '访问申请' }));
}

it('没有已登记的 L3 知识库时不提供访问申请入口', async () => {
  const posts = stubFetch([kb('KB-IT-SERVICE', 'IT 服务知识库', 'L2')]);
  renderSecurity();
  await openAccessTab();

  expect(await screen.findByText('当前没有已登记的 L3 知识库，暂无需提交敏感数据访问申请。')).toBeInTheDocument();
  const submit = screen.getByRole('button', { name: '提交访问申请' });
  expect(submit).toBeDisabled();

  fireEvent.click(submit);
  expect(posts).toHaveLength(0);
});

it('按选中的 L3 知识库提交访问申请', async () => {
  const posts = stubFetch([kb('KB-SENSITIVE-A', '客户敏感信息库'), kb('KB-SENSITIVE-B', '纪检敏感信息库')]);
  renderSecurity();
  await openAccessTab();

  // 知识库列表与申请列表并行加载，需等 L3 资源就绪后按钮才可用。
  await waitFor(() => expect(screen.getByRole('button', { name: '提交访问申请' })).toBeEnabled());
  const submit = screen.getByRole('button', { name: '提交访问申请' });

  fireEvent.mouseDown(document.querySelector('.ant-select-selector') as Element);
  fireEvent.click(await screen.findByTitle('纪检敏感信息库（KB-SENSITIVE-B）'));
  fireEvent.click(submit);

  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toMatchObject({ resource_type: 'knowledge', resource_id: 'KB-SENSITIVE-B' });
  expect(String(posts[0].body?.reason)).toContain('纪检敏感信息库');
});

it('插件类申请单显示插件名与类型，批准走管理端通道并提示授权效果', async () => {
  const posts = stubFetch([kb('KB-SENSITIVE-A', '客户敏感信息库')], {
    requests: [{
      id: 7, applicant_no: 'DT-E10281', resource_type: 'plugin', resource_id: 'PLG-FUND',
      reason: '做基金分析需要', status: 'pending', approval_chain: [], decided_by: null,
      decided_at: null, created_at: '2026-09-18T02:00:00',
    }],
    plugins: [{ id: 'PLG-FUND', name: '盈米基金数据', plugin_type: 'mcp', scope: 'shared' }],
  });
  renderSecurity();
  await openAccessTab();

  // 资源列把插件 id 映射成可读名称，类型列区分知识库/插件
  expect(await screen.findByText('盈米基金数据')).toBeInTheDocument();
  expect(screen.getByText('插件')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /批\s*准/ }));
  expect(await screen.findByText('批准后自动为该员工的分身建立插件授权')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '确认批准' }));

  await waitFor(() => expect(posts).toHaveLength(1));
  // 管理员账号没有分身，必须走 /admin/access-requests/{id}/approve
  expect(posts[0].url).toContain('/api/v1/admin/access-requests/7/approve');
  expect(posts[0].body).toMatchObject({ approve: true });
});
