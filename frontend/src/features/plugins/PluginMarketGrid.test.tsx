import { expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import PluginMarketGrid from './PluginMarketGrid';
import type { UnifiedPlugin } from '../../api/client';

/** 货架条目：默认一条可申请的技能，各用例按需覆写。 */
const item = (over: Partial<UnifiedPlugin> = {}): UnifiedPlugin => ({
  plugin_id: 'SKILL-BASE', id: 'SKILL-BASE', name: '基础技能', plugin_type: 'skill',
  scope: 'shared', mcp_category: undefined, skill_category: null, data_level: 'L1',
  current_version: '1.0.0', version: '1.0.0', description: '基础说明',
  source: 'platform', featured: false, state: 'requestable', request_id: null,
  ...over,
});

const noop = () => undefined;

function renderGrid(market: UnifiedPlugin[], fixedType: 'skill' | 'mcp' = 'skill') {
  return render(
    <PluginMarketGrid
      market={market}
      fixedType={fixedType}
      onEnable={noop}
      onOpenDetail={noop}
      onNewVersion={noop}
      onRequest={noop}
    />,
  );
}

const cards = () => screen.queryAllByLabelText(/^查看插件 /).map((node) => node.getAttribute('aria-label'));

/**
 * 工具条上的筛选一律是内联控件（Segmented / 分类胶囊），没有浮层。
 * 按筛选组的 aria-label 取到组，再按可见文案点其中的一项。
 */
const group = (name: string) => within(screen.getByRole('group', { name }));

function pickSegment(groupName: string, option: string) {
  fireEvent.click(group(groupName).getByText(option));
}

/** 分类胶囊把条数写在同一个按钮里，所以按可访问名做前缀匹配。 */
function pickChip(groupName: string, label: string) {
  fireEvent.click(group(groupName).getByRole('button', { name: new RegExp(label) }));
}

it('精选技能单独成组，其余进「全部技能」', () => {
  renderGrid([
    item({ plugin_id: 'A', name: '基金筛选', featured: true, skill_category: '数据分析' }),
    item({ plugin_id: 'B', name: '办公助手', skill_category: '办公效率' }),
  ]);

  expect(screen.getByText('精选推荐')).toBeInTheDocument();
  expect(screen.getByText(/全部技能/)).toBeInTheDocument();
  expect(cards()).toEqual(['查看插件 基金筛选', '查看插件 办公助手']);
  // 精选卡片带金色标记
  expect(screen.getByText('精选')).toBeInTheDocument();
});

it('按来源筛选只保留对应来源的技能', () => {
  renderGrid([
    item({ plugin_id: 'A', name: '来自 SkillHub', source: 'skillhub' }),
    item({ plugin_id: 'B', name: '平台共享技能', source: 'platform' }),
    item({ plugin_id: 'C', name: '我自己的技能', source: 'personal' }),
  ]);
  expect(cards()).toHaveLength(3);

  pickSegment('按来源筛选', '平台共享');

  expect(cards()).toEqual(['查看插件 平台共享技能']);
});

it('按业务分类筛选，分类胶囊只列货架上真实出现过的分类', () => {
  renderGrid([
    item({ plugin_id: 'A', name: '基金筛选', skill_category: '数据分析' }),
    item({ plugin_id: 'B', name: '办公助手', skill_category: '办公效率' }),
  ]);

  pickChip('按技能分类筛选', '办公效率');

  expect(cards()).toEqual(['查看插件 办公助手']);
  // 未出现在货架上的分类不进入胶囊，避免点了之后一片空白
  expect(group('按技能分类筛选').queryByRole('button', { name: /生活服务/ })).toBeNull();
  // 胶囊上带条数，便于判断值不值得点
  expect(group('按技能分类筛选').getByRole('button', { name: /办公效率\s*1/ })).toBeInTheDocument();
});

it('按数据等级筛选', () => {
  renderGrid([
    item({ plugin_id: 'A', name: '公开技能', data_level: 'L1' }),
    item({ plugin_id: 'B', name: '内部技能', data_level: 'L2' }),
  ]);

  pickSegment('按数据等级筛选', 'L2 内部');

  expect(cards()).toEqual(['查看插件 内部技能']);
});

it('清空筛选把搜索、来源、等级、分类一起复位', () => {
  renderGrid([
    item({ plugin_id: 'A', name: '基金筛选', skill_category: '数据分析', source: 'skillhub' }),
    item({ plugin_id: 'B', name: '办公助手', skill_category: '办公效率', source: 'platform' }),
  ]);

  fireEvent.change(screen.getByLabelText('搜索技能'), { target: { value: '办公' } });
  pickSegment('按来源筛选', '平台共享');
  pickChip('按技能分类筛选', '办公效率');
  expect(cards()).toEqual(['查看插件 办公助手']);

  fireEvent.click(screen.getByRole('button', { name: '清空筛选' }));

  expect(cards()).toEqual(['查看插件 基金筛选', '查看插件 办公助手']);
  expect(screen.getByLabelText('搜索技能')).toHaveValue('');
});

it('超过一页时分页，第二页展示剩余条目', () => {
  const market = Array.from({ length: 30 }, (_, index) => item({
    plugin_id: `SKILL-${index}`, id: `SKILL-${index}`, name: `技能 ${index}`,
  }));
  renderGrid(market);

  expect(cards()).toHaveLength(24);
  expect(screen.getByText('共 30 个技能')).toBeInTheDocument();

  fireEvent.click(screen.getByTitle('2'));

  expect(cards()).toHaveLength(6);
});

it('搜索命中名称、简介与分类', () => {
  renderGrid([
    item({ plugin_id: 'A', name: '基金筛选', description: '按业绩筛选候选基金' }),
    item({ plugin_id: 'B', name: '办公助手', description: '写周报', skill_category: '办公效率' }),
  ]);

  fireEvent.change(screen.getByLabelText('搜索技能'), { target: { value: '周报' } });

  expect(cards()).toEqual(['查看插件 办公助手']);
});

it('筛选无结果时给出可执行的空态，而不是空白页', () => {
  renderGrid([item({ plugin_id: 'A', name: '基金筛选' })]);

  fireEvent.change(screen.getByLabelText('搜索技能'), { target: { value: '不存在的技能' } });

  expect(screen.getByText(/没有匹配的技能/)).toBeInTheDocument();
  expect(cards()).toHaveLength(0);
});

it('MCP 市场按 mcp_category 筛选，胶囊用中文标签', () => {
  renderGrid([
    item({ plugin_id: 'A', name: '基金数据', plugin_type: 'mcp', mcp_category: 'fund' }),
    item({ plugin_id: 'B', name: '其他工具', plugin_type: 'mcp', mcp_category: 'other' }),
  ], 'mcp');

  pickChip('按 MCP 分类筛选', '其他');

  expect(cards()).toEqual(['查看插件 其他工具']);
});

it('MCP 分类用中文标签，且搜索能命中中文分类名', () => {
  renderGrid([
    item({ plugin_id: 'A', name: '兴证e家', plugin_type: 'mcp', mcp_category: 'employee_service' }),
    item({ plugin_id: 'B', name: '基金数据', plugin_type: 'mcp', mcp_category: 'fund' }),
  ], 'mcp');

  // 分类胶囊不暴露英文枚举值
  expect(group('按 MCP 分类筛选').getByRole('button', { name: /员工服务\s*1/ })).toBeInTheDocument();
  expect(group('按 MCP 分类筛选').queryByRole('button', { name: /employee_service/ })).toBeNull();

  fireEvent.change(screen.getByLabelText('搜索插件'), { target: { value: '员工服务' } });

  expect(cards()).toEqual(['查看插件 兴证e家']);
});

it('列表为空时提示联系管理员导入', () => {
  renderGrid([]);
  expect(screen.getByText(/技能市场暂时没有可用技能/)).toBeInTheDocument();
});
