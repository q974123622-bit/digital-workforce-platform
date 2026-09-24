import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import PluginCenter from './PluginCenter';

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

/** 市场货架条目：默认「可申请」，各用例按需覆写状态。 */
const marketItem = (over: Record<string, unknown> = {}) => ({
  plugin_id: 'PLG-FUND', id: 'PLG-FUND', name: '盈米基金数据', plugin_type: 'mcp',
  scope: 'shared', mcp_category: 'fund', data_level: 'L2', current_version: '1.0.0',
  version: '1.0.0', description: '提供基金资料与业务数据查询能力。',
  source: 'platform', state: 'requestable', request_id: null, decision_mode: null, ...over,
});

/** 知识库货架条目：默认「可申请」。 */
const knowledgeItem = (over: Record<string, unknown> = {}) => ({
  id: 'KB-WEALTH', name: '财富管理知识库', domain: '财富管理', data_level: 'L2',
  description: '覆盖财富管理条线的产品与合规材料。', status: 'active',
  state: 'requestable', request_id: null, occupant: null, occupant_name: null,
  active_source: 'rest', source_available: true, ...over,
});

function stubFetch(
  onPost?: (url: string, body: Record<string, unknown> | null) => void,
  marketOverride?: Array<Record<string, unknown>>,
  credentialAlerts: Array<Record<string, unknown>> = [],
) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === 'POST') {
      onPost?.(url, init.body ? JSON.parse(String(init.body)) : null);
      return json({ id: 11, status: 'pending' });
    }
    if (url.endsWith('/api/v1/my/plugins')) {
      return json({
        effective: [{
          plugin_id: 'PLG-ACTIVE', id: 'PLG-ACTIVE', name: '盈米基金 MCP', plugin_type: 'mcp',
          scope: 'shared', mcp_category: 'fund', version: '1.2.0', description: '基金资料查询',
        }],
        available: [],
        market: marketOverride ?? [
          marketItem(),
          marketItem({
            plugin_id: 'SKILL-QM', id: 'SKILL-QM', name: '基金筛选', plugin_type: 'skill',
            mcp_category: null, source: 'skillhub', state: 'enabled',
          }),
          marketItem({ plugin_id: 'PLG-PENDING', id: 'PLG-PENDING', name: '待审插件', state: 'pending', request_id: 12 }),
        ],
        market_counts: { mcp: 2, skill: 1 },
      });
    }
    if (url.endsWith('/api/v1/my/mcp/credential-alerts')) {
      return json({
        warn_days: 7,
        alerts: credentialAlerts,
        expired_count: credentialAlerts.filter((item) => item.state === 'expired').length,
        expiring_count: credentialAlerts.filter((item) => item.state === 'expiring').length,
      });
    }
    if (url.endsWith('/api/v1/my/knowledge-bases')) {
      return json({
        items: [
          knowledgeItem(),
          knowledgeItem({
            id: 'KB-SECURITIES', name: '证券业务知识库', domain: '证券', state: 'occupied',
            occupant: 'AI-INVESTMENT', occupant_name: '投资顾问', active_source: 'verag',
          }),
        ],
      });
    }
    if (url.endsWith('/api/v1/my/plugins/submissions')) {
      return json([{
        id: 3, plugin_id: 'SKILL-X', name: '周报模板', plugin_type: 'skill', version: '1.0.0',
        data_level: 'L2', deployment_mode: 'instruction', review_status: 'rejected',
        publish_status: 'rejected', submitted_by: 'E10281', review_note: '缺少输出模板',
        created_at: '2026-09-18T02:00:00',
      }]);
    }
    return json({});
  }));
}

afterEach(() => vi.unstubAllGlobals());

it('MCP 市场按六态渲染操作，未授权工具可填写理由提交申请', async () => {
  const posts: Array<{ url: string; body: Record<string, unknown> | null }> = [];
  stubFetch((url, body) => posts.push({ url, body }));

  render(<MemoryRouter><PluginCenter /></MemoryRouter>);

  // 默认页签是「MCP 市场」
  expect(await screen.findByRole('tab', { name: /MCP 市场/ })).toBeInTheDocument();
  expect(await screen.findByText('盈米基金数据')).toBeInTheDocument();
  // 申请中 → 禁用，并显示申请单号
  expect(screen.getByRole('button', { name: /申请中/ })).toBeDisabled();
  expect(screen.getByText('申请单 ARQ-12 · 等待管理员审批')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /申请使用/ }));
  const reason = await screen.findByLabelText('使用理由');
  fireEvent.change(reason, { target: { value: '做基金分析' } });
  fireEvent.click(screen.getByRole('button', { name: '提交申请' }));

  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toContain('/api/v1/my/plugins/PLG-FUND/request');
  expect(posts[0].body).toMatchObject({ reason: '做基金分析' });
});

it('技能市场只出技能，并标注来源 SkillHub', async () => {
  stubFetch();
  render(<MemoryRouter><PluginCenter /></MemoryRouter>);
  await screen.findByText('盈米基金数据');

  fireEvent.click(screen.getByRole('tab', { name: /技能市场/ }));

  expect(await screen.findByText('基金筛选')).toBeInTheDocument();
  expect(screen.getByText('来源 SkillHub')).toBeInTheDocument();
  // MCP 工具不再出现在技能市场（面板已隐藏，role 查询按可访问性过滤）
  expect(screen.queryByRole('button', { name: '查看插件 盈米基金数据' })).toBeNull();
});

it('技能市场按来源筛选，精选技能单独成组并标出业务分类', async () => {
  stubFetch(undefined, [
    marketItem({
      plugin_id: 'SKILL-QM', id: 'SKILL-QM', name: '基金筛选', plugin_type: 'skill',
      mcp_category: null, source: 'skillhub', skill_category: '数据分析', featured: true,
      description: '按业绩与风险偏好筛出候选基金。',
    }),
    marketItem({
      plugin_id: 'SKILL-PLAT', id: 'SKILL-PLAT', name: '平台周报模板', plugin_type: 'skill',
      mcp_category: null, source: 'platform', skill_category: '办公效率',
      description: '一键生成周报。',
    }),
  ]);
  render(<MemoryRouter><PluginCenter /></MemoryRouter>);
  // 这份 stub 只给了技能，默认的 MCP 市场是空的，所以等页签而不是等 MCP 卡片。
  await screen.findByRole('tab', { name: /技能市场/ });
  fireEvent.click(screen.getByRole('tab', { name: /技能市场/ }));

  expect(await screen.findByText('精选推荐')).toBeInTheDocument();
  // 分类既出现在筛选胶囊上，也出现在卡片标签上
  const categoryGroup = within(screen.getByRole('group', { name: '按技能分类筛选' }));
  expect(categoryGroup.getByRole('button', { name: /数据分析/ })).toBeInTheDocument();

  // 来源筛选是内联分段器（没有浮层）：切到「平台共享」后只剩平台技能，精选分组也随之消失
  const sourceGroup = within(screen.getByRole('group', { name: '按来源筛选' }));
  fireEvent.click(sourceGroup.getByText('平台共享'));

  expect(await screen.findByText('平台周报模板')).toBeInTheDocument();
  expect(screen.queryByText('基金筛选')).toBeNull();
  expect(screen.queryByText('精选推荐')).toBeNull();
});

it('令牌即将过期时页头汇总告警，并在对应服务卡片行内标注', async () => {
  stubFetch(undefined, undefined, [{
    plugin_id: 'PLG-FUND', name: '盈米基金数据', mcp_category: 'fund',
    state: 'expiring', days_left: 3, expires_at: '2026-09-27T02:00:00',
  }]);
  render(<MemoryRouter><PluginCenter /></MemoryRouter>);

  expect(await screen.findByText(/1 个 MCP 服务的访问令牌需要更新/)).toBeInTheDocument();
  expect(screen.getByText(/请在服务里重新提供你的访问令牌/)).toBeInTheDocument();
  // 行内标签：精确文案避免与页头告警里的句子混淆
  expect(screen.getByText('令牌 3 天后过期')).toBeInTheDocument();
});

it('令牌已过期时行内标注「已过期」，未过期的服务不出现标签', async () => {
  stubFetch(undefined, undefined, [{
    plugin_id: 'PLG-FUND', name: '盈米基金数据', mcp_category: 'fund',
    state: 'expired', days_left: 0, expires_at: '2026-09-20T02:00:00',
  }]);
  render(<MemoryRouter><PluginCenter /></MemoryRouter>);

  expect(await screen.findByText(/访问令牌需要更新/)).toBeInTheDocument();
  expect(screen.getByText('令牌已过期')).toBeInTheDocument();
  expect(screen.queryByText(/天后过期$/)).toBeNull();
});

it('没有即将过期的令牌时不出现告警', async () => {
  stubFetch();
  render(<MemoryRouter><PluginCenter /></MemoryRouter>);
  await screen.findByText('盈米基金数据');
  expect(screen.queryByText(/访问令牌需要更新/)).toBeNull();
});

it('知识库市场展示占用方并禁用申请入口', async () => {
  stubFetch();
  render(<MemoryRouter><PluginCenter /></MemoryRouter>);
  await screen.findByText('盈米基金数据');

  fireEvent.click(screen.getByRole('tab', { name: /知识库市场/ }));

  expect(await screen.findByText('财富管理知识库')).toBeInTheDocument();
  expect(screen.getByText('证券业务知识库')).toBeInTheDocument();
  expect(screen.getByText('已被 投资顾问 占用')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /已被占用/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: /申请使用/ })).toBeEnabled();
});

it('知识库申请提交后回写状态，搜索与已启用/我的提交页签保留原有入口', async () => {
  const posts: Array<{ url: string; body: Record<string, unknown> | null }> = [];
  stubFetch((url, body) => posts.push({ url, body }));
  render(<MemoryRouter><PluginCenter /></MemoryRouter>);
  await screen.findByText('盈米基金数据');

  fireEvent.click(screen.getByRole('tab', { name: /知识库市场/ }));
  fireEvent.click(await screen.findByRole('button', { name: /申请使用/ }));
  const reason = await screen.findByLabelText('用途说明');
  fireEvent.change(reason, { target: { value: '做季度产品分析' } });
  fireEvent.click(screen.getByRole('button', { name: '提交申请' }));

  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toContain('/api/v1/my/knowledge-bases/KB-WEALTH/request');
  expect(posts[0].body).toMatchObject({ reason: '做季度产品分析' });

  fireEvent.click(screen.getByRole('tab', { name: /已启用/ }));
  expect(await screen.findByRole('button', { name: /停\s*用/ })).toBeInTheDocument();

  fireEvent.click(screen.getByRole('tab', { name: /我的提交/ }));
  expect(await screen.findByText(/缺少输出模板/)).toBeInTheDocument();
  expect(screen.getByText('重新提交')).toBeInTheDocument();
});
