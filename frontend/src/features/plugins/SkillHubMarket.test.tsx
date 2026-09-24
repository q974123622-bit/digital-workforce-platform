import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import SkillHubMarket from './SkillHubMarket';

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

const skill = (over: Record<string, unknown> = {}) => ({
  slug: 'docx', display_name: '文档处理', summary: '生成、解析与转换 docx 文档',
  version: '0.0.2', source: 'skillhub', category: 'AI大赛-财富管理赛道',
  downloads: 12, stars: 3, tags: ['文档'], download_disabled: false,
  imported: false, imported_version: null, imported_plugin_id: null, ...over,
});

function stubFetch(onPost?: (url: string, body: Record<string, unknown> | null) => void) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === 'POST') {
      onPost?.(url, init.body ? JSON.parse(String(init.body)) : null);
      if (url.endsWith('/admin/skillhub/import')) {
        // 批量导入改成后台批次：接口立即返回 batch_id，进度另查。
        return json({ batch_id: 'SHB-1', total: 1, status: 'running' });
      }
      return json({ refreshed: true });
    }
    // 注意要排在 /imports 列表之前，否则会被列表分支先吃掉。
    if (url.includes('/admin/skillhub/imports/')) {
      return json({
        batch_id: 'SHB-1', status: 'completed', total: 1, done: 2,
        results: [
          { id: 1, slug: 'xlsx', version: '0.0.2', status: 'imported', message: '导入成功', plugin_id: 'SKILL-1', created_at: '2026-09-22T02:00:00', updated_at: '2026-09-22T02:00:00' },
          { id: 2, slug: 'pdf', version: '0.0.1', status: 'failed', message: '技能包内未找到 SKILL.md', created_at: '2026-09-22T02:00:00', updated_at: '2026-09-22T02:00:00' },
        ],
        summary: { imported: 1, skipped: 0, failed: 1 },
      });
    }
    if (url.includes('/admin/skillhub/overview')) {
      return json({
        enabled: true, base_url: 'http://10.49.70.2', message: '',
        categories: [
          { category: 'AI大赛-财富管理赛道', desc: '数字化人才大赛' },
          { category: '办公效率', desc: '' },
        ],
      });
    }
    if (url.includes('/admin/skillhub/skills?')) {
      return json({
        items: [
          skill(),
          skill({ slug: 'xlsx', display_name: '已导入技能', imported: true, imported_version: '0.0.2' }),
        ],
        total: 129, page: 1, page_size: 24, imported_total: 1,
      });
    }
    if (url.includes('/admin/skillhub/imports')) return json({ items: [] });
    return json({});
  }));
}

afterEach(() => vi.unstubAllGlobals());

it('浏览 SkillHub 目录：已导入的卡片不可勾选，全选只选未导入项', async () => {
  stubFetch();
  render(<SkillHubMarket />);

  expect(await screen.findByText('文档处理')).toBeInTheDocument();
  expect(screen.getByText('已导入')).toBeInTheDocument();
  expect(screen.getByLabelText('选择技能 已导入技能')).toBeDisabled();
  expect(screen.getByText(/目录共 129 个/)).toBeInTheDocument();

  fireEvent.click(screen.getByLabelText('全选本页'));
  // 工具条与底部操作条都会显示已选数量
  expect(await screen.findAllByText(/已选 1 项/)).toHaveLength(2);
});

it('批量导入走后台批次：提交后轮询进度并逐条回显结果', async () => {
  const posts: Array<{ url: string; body: Record<string, unknown> | null }> = [];
  stubFetch((url, body) => posts.push({ url, body }));
  const fetchMock = globalThis.fetch as unknown as { mock: { calls: unknown[][] } };
  const calledUrls = () => fetchMock.mock.calls.map(([input]) => String(input));
  render(<SkillHubMarket />);
  await screen.findByText('文档处理');

  fireEvent.click(screen.getByLabelText('选择技能 文档处理'));
  fireEvent.click(await screen.findByRole('button', { name: /批量导入/ }));

  await waitFor(() => expect(posts.some((row) => row.url.includes('/admin/skillhub/import'))).toBe(true));
  const call = posts.find((row) => row.url.includes('/admin/skillhub/import'))!;
  expect(call.body).toMatchObject({ slugs: ['docx'], generate_intro: true });
  // 提交之后必须去查批次进度，而不是等一个同步响应
  await waitFor(() => expect(
    calledUrls().some((url) => url.includes('/admin/skillhub/imports/SHB-1')),
  ).toBe(true));

  expect(await screen.findByText('本次导入结果（2）')).toBeInTheDocument();
  // 「导入成功」既出现在结果列表的状态标签，也出现在该条的说明文案里
  expect(screen.getAllByText('导入成功').length).toBeGreaterThan(0);
  expect(screen.getByText('技能包内未找到 SKILL.md')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /重\s*试/ })).toBeInTheDocument();
});

it('按分类筛选把 category 传给后端', async () => {
  const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    urls.push(url);
    if (url.includes('/admin/skillhub/overview')) {
      return json({ enabled: true, base_url: 'http://x', categories: [{ category: '办公效率', desc: '' }] });
    }
    if (url.includes('/admin/skillhub/skills?')) {
      return json({ items: [skill()], total: 1, page: 1, page_size: 24, imported_total: 0 });
    }
    return json({ items: [] });
  }));
  render(<SkillHubMarket />);
  await screen.findByText('文档处理');

  fireEvent.click(screen.getByRole('button', { name: /办公效率/ }));

  await waitFor(() => expect(urls.some((url) => url.includes('category='))).toBe(true));
});

it('未接入 SkillHub 时给出明确原因而不是空白页', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes('/admin/skillhub/overview')) {
      return json({ enabled: false, base_url: '', categories: [], message: 'SkillHub 未接入：请配置 DWP_SKILLHUB_BASE_URL' });
    }
    return json({});
  }));
  render(<SkillHubMarket />);

  expect(await screen.findByText('SkillHub 未接入')).toBeInTheDocument();
  expect(screen.getByText(/DWP_SKILLHUB_BASE_URL/)).toBeInTheDocument();
});
