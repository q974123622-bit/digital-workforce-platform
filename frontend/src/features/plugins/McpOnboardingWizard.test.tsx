import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import McpOnboardingWizard from './McpOnboardingWizard';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const PROBE = {
  url: 'http://10.0.0.9:9100/mcp',
  protocol_version: '2024-11-05',
  server_info: { name: 'demo-mcp', version: '1.2.0' },
  capabilities: { tools: { listChanged: true } },
  tools: [
    {
      name: 'Echo',
      description: '回显输入文本',
      parameters: [{ name: 'text', type: 'string', description: '待回显文本' }],
      required: ['text'],
      input_schema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] },
    },
    { name: 'ServerTime', description: '返回服务端时间', parameters: [], required: [] },
  ],
  tools_count: 2,
  tools_truncated: false,
  latency_ms: 42,
  authenticated: true,
  auth_header_name: 'x-api-key',
};

const DRAFT = {
  name: '行情查询服务',
  description: '面向投资场景的行情查询能力。',
  tools: [
    { name: 'Echo', description: '把输入原样返回，用于连通性验证' },
    { name: 'ServerTime', description: '获取服务端当前时间' },
  ],
  mcp_category: 'fund',
  data_level: 'L2',
  generated: true,
  degraded_reason: null,
};

const CREATED = {
  plugin_id: 'PLG-MCP1',
  name: '行情查询服务',
  plugin_type: 'mcp',
  scope: 'shared',
  status: 'active',
  current_version: '1.0.0',
  version: '1.0.0',
  review_status: 'approved',
  publish_status: 'published',
  mcp_category: 'fund',
  data_level: 'L2',
  url: PROBE.url,
  tool_prefix: 'mcp_1a2b3c4d__',
  authenticated: true,
  auth_header_name: 'x-api-key',
  secret_hint: '****alue',
  tools_count: 2,
  owner_human_no: null,
};

afterEach(() => vi.unstubAllGlobals());

it('四步向导：探测 → 生成简介（可自定义提示词重生成）→ 提交发布', async () => {
  const drafts: Array<Record<string, unknown>> = [];
  const creates: Array<Record<string, unknown>> = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/api/v1/admin/mcp/probe')) return json(PROBE);
    if (url.endsWith('/api/v1/admin/mcp/draft')) {
      drafts.push(JSON.parse(String(init?.body)));
      return json(DRAFT);
    }
    if (url.endsWith('/api/v1/admin/mcp/plugins')) {
      creates.push(JSON.parse(String(init?.body)));
      return json(CREATED, 201);
    }
    return json({});
  }));
  const onDone = vi.fn();

  render(<McpOnboardingWizard mode="admin" onDone={onDone} />);

  fireEvent.change(screen.getByPlaceholderText(/10\.56\.35\.240/), {
    target: { value: 'http://10.0.0.9:9100/mcp' },
  });
  fireEvent.click(screen.getByRole('button', { name: /测试连接并解析/ }));

  // 步骤二：解析结果（服务信息 + 工具清单 + 耗时）
  expect(await screen.findByText('Echo')).toBeInTheDocument();
  expect(screen.getByText('ServerTime')).toBeInTheDocument();
  expect(screen.getByText('42 ms')).toBeInTheDocument();
  expect(screen.getByText('demo-mcp · v1.2.0')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /生成中文简介/ }));
  // 步骤三：AI 草稿填入可编辑区
  const nameInput = await screen.findByLabelText('插件中文名');
  await waitFor(() => expect(nameInput).toHaveValue('行情查询服务'));
  expect(screen.getByLabelText('插件简介')).toHaveValue('面向投资场景的行情查询能力。');
  expect(screen.getByLabelText('工具说明 Echo')).toHaveValue('把输入原样返回，用于连通性验证');

  // 不满意时用自定义提示词重新生成一版
  fireEvent.change(screen.getByLabelText('自定义生成提示词'), { target: { value: '更简短一些' } });
  fireEvent.click(screen.getByRole('button', { name: /重新生成/ }));
  await waitFor(() => expect(drafts).toHaveLength(2));
  expect(drafts[1].prompt).toBe('更简短一些');
  expect(drafts[0].prompt).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
  expect(await screen.findByText('提交后立即发布')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /确认接入并发布/ }));
  await waitFor(() => expect(creates).toHaveLength(1));
  // 工具说明以人工确认后的版本落库，并带回入参 Schema
  expect(creates[0]).toMatchObject({
    url: PROBE.url,
    name: '行情查询服务',
    scope: 'shared',
    mcp_category: 'fund',
    data_level: 'L2',
  });
  expect((creates[0].tools as Array<Record<string, unknown>>)[0]).toMatchObject({
    name: 'Echo',
    description: '把输入原样返回，用于连通性验证',
    input_schema: PROBE.tools[0].input_schema,
  });
  expect(await screen.findByText('MCP 服务已接入并发布')).toBeInTheDocument();
});

it('探测失败时原位给出可读错误，不进入下一步', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json(
    { error: { code: 'HTTP_502', message: '连接 MCP 服务失败：无法连接 External MCP 服务', detail: null } },
    502,
  )));

  render(<McpOnboardingWizard mode="admin" onDone={vi.fn()} />);
  fireEvent.change(screen.getByPlaceholderText(/10\.56\.35\.240/), {
    target: { value: 'http://10.0.0.9:9100/mcp' },
  });
  fireEvent.click(screen.getByRole('button', { name: /测试连接并解析/ }));

  expect(await screen.findByText('测试连接失败')).toBeInTheDocument();
  expect(screen.getByText(/无法连接 External MCP 服务/)).toBeInTheDocument();
  // 仍停留在步骤一，可修改后重试
  expect(screen.getByRole('button', { name: /测试连接并解析/ })).toBeInTheDocument();
});

it('员工端范围锁定为个人，提示等待管理员审核', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/api/v1/my/mcp/probe')) return json(PROBE);
    if (url.endsWith('/api/v1/my/mcp/draft')) return json(DRAFT);
    return json(CREATED, 201);
  }));

  render(<McpOnboardingWizard mode="employee" onDone={vi.fn()} />);
  fireEvent.change(screen.getByPlaceholderText(/10\.56\.35\.240/), {
    target: { value: 'http://10.0.0.9:9100/mcp' },
  });
  fireEvent.click(screen.getByRole('button', { name: /测试连接并解析/ }));
  await screen.findByText('Echo');
  fireEvent.click(screen.getByRole('button', { name: /生成中文简介/ }));
  await screen.findByLabelText('插件中文名');
  fireEvent.click(screen.getByRole('button', { name: /下一步/ }));
  expect(await screen.findByText('提交后等待管理员审核')).toBeInTheDocument();
  expect(screen.getByText('个人（仅自己分身）')).toBeInTheDocument();
});
