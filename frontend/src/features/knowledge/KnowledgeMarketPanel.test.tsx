import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import KnowledgeMarketPanel from './KnowledgeMarketPanel';
import type { KnowledgeMarketItem } from '../../api/client';

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

const item = (over: Partial<KnowledgeMarketItem> = {}): KnowledgeMarketItem => ({
  id: 'KB-WEALTH', name: '财富管理知识库', domain: '财富管理', data_level: 'L2',
  description: '覆盖财富管理条线的产品与合规材料。', status: 'active',
  state: 'requestable', request_id: null, occupant: null, occupant_name: null,
  active_source: 'rest', source_available: true, ...over,
});

const items: KnowledgeMarketItem[] = [
  item({ id: 'KB-IT-SERVICE', name: 'IT 服务知识库', state: 'enabled' }),
  item(),
  item({
    id: 'KB-SECURITIES', name: '证券业务知识库', state: 'occupied',
    occupant: 'AI-INVESTMENT', occupant_name: '投资顾问',
  }),
  item({ id: 'KB-HR', name: '人力资源知识库', state: 'pending', request_id: 21 }),
  item({ id: 'KB-AUDIT', name: '审计知识库', state: 'requestable', active_source: null, source_available: false }),
];

afterEach(() => vi.unstubAllGlobals());

it('四态各有唯一操作，被占用的库禁用并说明占用方', () => {
  render(<KnowledgeMarketPanel items={items} onChanged={() => {}} />);

  expect(screen.getByRole('button', { name: /已授权/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: /申请中/ })).toBeDisabled();
  const occupied = screen.getByRole('button', { name: /已被占用/ });
  expect(occupied).toBeDisabled();
  expect(occupied).toHaveAttribute('title', '已被 投资顾问 占用，需管理员改派');
  expect(screen.getAllByRole('button', { name: /申请使用/ })).toHaveLength(2);
  expect(screen.getByText('已被 投资顾问 占用')).toBeInTheDocument();
  expect(screen.getByText('申请单 ARQ-21 · 等待管理员审批')).toBeInTheDocument();
});

it('数据源不可达的知识库照常展示但标注不可用', () => {
  render(<KnowledgeMarketPanel items={items} onChanged={() => {}} />);
  expect(screen.getByText('审计知识库')).toBeInTheDocument();
  expect(screen.getByText('数据源不可达')).toBeInTheDocument();
});

it('提交申请后回调刷新，并带上用途说明', async () => {
  const posts: Array<{ url: string; body: Record<string, unknown> | null }> = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    posts.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null });
    return json({ id: 31, status: 'pending' });
  }));
  const onChanged = vi.fn();

  render(<KnowledgeMarketPanel items={items} onChanged={onChanged} />);
  const requestButtons = screen.getAllByRole('button', { name: /申请使用/ });
  fireEvent.click(requestButtons[0]);

  const reason = await screen.findByLabelText('用途说明');
  fireEvent.change(reason, { target: { value: '做季度产品分析' } });
  fireEvent.click(screen.getByRole('button', { name: '提交申请' }));

  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toContain('/api/v1/my/knowledge-bases/KB-WEALTH/request');
  expect(posts[0].body).toMatchObject({ reason: '做季度产品分析' });
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
});

it('状态分段器只收敛当前货架', () => {
  render(<KnowledgeMarketPanel items={items} onChanged={() => {}} />);

  // 卡片上的状态标签文案与分段器选项同名，按 title 精确命中分段器
  fireEvent.click(screen.getByTitle('可申请'));

  expect(screen.getByText('财富管理知识库')).toBeInTheDocument();
  expect(screen.getByText('审计知识库')).toBeInTheDocument();
  expect(screen.queryByText('证券业务知识库')).toBeNull();
  expect(screen.queryByText('IT 服务知识库')).toBeNull();
});
