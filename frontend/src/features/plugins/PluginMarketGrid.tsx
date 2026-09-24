import { SearchOutlined } from '@ant-design/icons';
import { Button, Empty, Input, Pagination, Segmented } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import type { McpCredentialAlert, UnifiedPlugin } from '../../api/client';
import PluginMarketCard, { pluginIdOf } from './PluginMarketCard';
import type { PluginVersionTarget } from './PluginMarketCard';
import {
  DATA_LEVEL_OPTIONS,
  MCP_CATEGORY_LABELS,
  PLUGIN_SOURCE_OPTIONS,
  pluginDescription,
} from './pluginMeta';

export type { PluginVersionTarget } from './PluginMarketCard';

type TypeFilter = 'all' | 'skill' | 'mcp';

/** 每页条数：与网格的两列布局配合，一屏放得下。 */
const PAGE_SIZE = 24;

/** 单选筛选里的「不限制」哨兵值：antd Segmented 不接受 undefined 作为选中值。 */
const ANY = '__any__';

interface CategoryChip {
  value: string;
  label: string;
  count: number;
}

function sectionTitle(type: 'skill' | 'mcp' | 'all', count: number): string {
  const noun = type === 'skill' ? '技能' : type === 'mcp' ? '插件' : '资源';
  return `全部${noun}（${count}）`;
}

/** 统计某一维度在货架上的真实分布，只列出出现过的值，避免选了之后一片空白。 */
function tally(values: Array<string | null | undefined>): Map<string, number> {
  const counts = new Map<string, number>();
  for (const value of values) {
    if (!value) continue;
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return counts;
}

/**
 * 插件市场货架：工具条（搜索 + 来源 + 等级 + 分类）+ 精选分组 + 分页。
 *
 * 筛选项一律用**内联控件**（Segmented 与分类胶囊），不用 antd 的浮层下拉：
 * 分类动辄十几项，胶囊一行铺开比藏在下拉里好找，也省掉一层浮层。
 *
 * 筛选与分页都在客户端完成 —— 全量货架本来就在内存里，一百多条也就是一次 O(n) 过滤，
 * 比新增服务端分页参数简单，也不扩大接口面。精选分组靠后端已经排好序（精选前置），
 * 这里只做「同页内分区」展示，不改变顺序。
 *
 * `fixedType` 用于拆市后的单一市场（MCP 市场 / 技能市场）：此时不再展示类型分段器。
 */
export default function PluginMarketGrid({
  market, fixedType, credentialAlerts, onEnable, onOpenDetail, onNewVersion, onRequest,
}: {
  market: UnifiedPlugin[];
  fixedType?: 'skill' | 'mcp';
  /** 本人用户级 MCP 令牌的到期提醒，按 plugin_id 索引（货架行内标出）。 */
  credentialAlerts?: Record<string, McpCredentialAlert>;
  onEnable: (pluginId: string, enabled: boolean) => void;
  onOpenDetail: (pluginId: string) => void;
  onNewVersion: (target: PluginVersionTarget) => void;
  onRequest: (item: UnifiedPlugin) => void;
}) {
  const [keyword, setKeyword] = useState('');
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all');
  const [category, setCategory] = useState<string>(ANY);
  const [source, setSource] = useState<string>(ANY);
  const [dataLevel, setDataLevel] = useState<string>(ANY);
  const [page, setPage] = useState(1);
  const effectiveType: TypeFilter = fixedType ?? typeFilter;

  // 分类胶囊：技能看 skill_category，MCP 看 mcp_category（转中文标签）。
  const categoryChips = useMemo<CategoryChip[]>(() => {
    const scoped = market.filter((item) => (fixedType ? item.plugin_type === fixedType : item.plugin_type === effectiveType));
    if (effectiveType === 'skill') {
      const counts = tally(scoped.filter((item) => item.plugin_type === 'skill').map((item) => item.skill_category));
      return Array.from(counts, ([value, count]) => ({ value, label: value, count }))
        .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, 'zh-Hans-CN'));
    }
    if (effectiveType === 'mcp') {
      const counts = tally(scoped.filter((item) => item.plugin_type === 'mcp').map((item) => item.mcp_category));
      return Array.from(counts, ([value, count]) => ({
        value, label: MCP_CATEGORY_LABELS[value] ?? value, count,
      })).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, 'zh-Hans-CN'));
    }
    return [];
  }, [market, fixedType, effectiveType]);

  // 换市场/换类型后原来的分类可能已经不存在，落回「全部分类」。
  useEffect(() => {
    if (category !== ANY && !categoryChips.some((chip) => chip.value === category)) setCategory(ANY);
  }, [category, categoryChips]);

  const filtered = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    return market.filter((item) => {
      if (fixedType && item.plugin_type !== fixedType) return false;
      if (!fixedType && typeFilter !== 'all' && item.plugin_type !== typeFilter) return false;
      if (category !== ANY) {
        const own = item.plugin_type === 'skill' ? item.skill_category : item.mcp_category;
        if (own !== category) return false;
      }
      if (source !== ANY && (item.source ?? 'platform') !== source) return false;
      if (dataLevel !== ANY && item.data_level !== dataLevel) return false;
      if (!needle) return true;
      // 分类两种形态都进索引：技能分类是中文原值，MCP 分类是英文枚举 + 中文标签，
      // 这样「员工服务」和「employee_service」都能搜到。
      const haystack = [
        item.name,
        pluginDescription(item),
        item.skill_category ?? '',
        item.mcp_category ?? '',
        item.mcp_category ? MCP_CATEGORY_LABELS[item.mcp_category] ?? '' : '',
      ].join(' ');
      return haystack.toLowerCase().includes(needle);
    });
  }, [market, keyword, typeFilter, category, source, dataLevel, fixedType]);

  // 任一筛选条件变化都要回到第一页，否则会停在一个已经空掉的页码上。
  useEffect(() => {
    setPage(1);
  }, [keyword, typeFilter, category, source, dataLevel, fixedType]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const paged = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const featured = paged.filter((item) => item.featured);
  const rest = paged.filter((item) => !item.featured);
  const hasFilter = Boolean(keyword.trim())
    || typeFilter !== 'all' || category !== ANY || source !== ANY || dataLevel !== ANY;

  const renderGrid = (items: UnifiedPlugin[]) => (
    <div className="plugin-catalog-grid grid gap-3 md:grid-cols-2">
      {items.map((item) => (
        <PluginMarketCard
          key={pluginIdOf(item)}
          item={item}
          credential={credentialAlerts?.[pluginIdOf(item)]}
          onEnable={onEnable}
          onOpenDetail={onOpenDetail}
          onNewVersion={onNewVersion}
          onRequest={onRequest}
        />
      ))}
    </div>
  );

  const noun = effectiveType === 'skill' ? '技能' : effectiveType === 'mcp' ? '插件' : '资源';

  return (
    <div className="pb-6">
      <div className="plugin-market-toolbar mb-4">
        <div className="flex flex-wrap items-center gap-3">
          <Input
            allowClear
            aria-label={effectiveType === 'skill' ? '搜索技能' : '搜索插件'}
            prefix={<SearchOutlined className="text-ink-tertiary" />}
            placeholder={effectiveType === 'skill' ? '搜索技能名称、用途或分类' : '搜索插件名称、说明或分类'}
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            className="w-full sm:w-[240px]"
          />
          {!fixedType && (
            <Segmented
              aria-label="按类型筛选"
              value={typeFilter}
              onChange={(value) => setTypeFilter(value as TypeFilter)}
              options={[{ label: '全部', value: 'all' }, { label: 'Skill', value: 'skill' }, { label: 'MCP', value: 'mcp' }]}
            />
          )}
          <div role="group" aria-label="按来源筛选" className="flex items-center">
            <Segmented
              value={source}
              onChange={(value) => setSource(String(value))}
              options={[{ label: '全部来源', value: ANY }, ...PLUGIN_SOURCE_OPTIONS]}
            />
          </div>
          <div role="group" aria-label="按数据等级筛选" className="flex items-center">
            <Segmented
              value={dataLevel}
              onChange={(value) => setDataLevel(String(value))}
              options={[{ label: '全部等级', value: ANY }, ...DATA_LEVEL_OPTIONS]}
            />
          </div>
          <span className="text-xs text-ink-tertiary">
            共 {filtered.length} 个{noun}
            {hasFilter && (
              <Button
                type="link" size="small" className="!ml-1 !px-1 !text-xs"
                onClick={() => { setKeyword(''); setTypeFilter('all'); setCategory(ANY); setSource(ANY); setDataLevel(ANY); }}
              >
                清空筛选
              </Button>
            )}
          </span>
        </div>

        {categoryChips.length > 0 && (
          <div role="group" aria-label={effectiveType === 'skill' ? '按技能分类筛选' : '按 MCP 分类筛选'} className="plugin-filter-chips">
            <button
              type="button"
              className="plugin-filter-chip"
              aria-pressed={category === ANY}
              onClick={() => setCategory(ANY)}
            >
              全部分类
            </button>
            {categoryChips.map((chip) => (
              <button
                key={chip.value}
                type="button"
                className="plugin-filter-chip"
                aria-pressed={category === chip.value}
                onClick={() => setCategory(category === chip.value ? ANY : chip.value)}
              >
                {chip.label}
                <span className="plugin-filter-chip-count">{chip.count}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {featured.length > 0 && (
        <section className="mb-5">
          <h3 className="mb-2 text-sm font-medium text-ink-secondary">
            精选推荐 <span className="text-xs text-ink-tertiary">运营置顶</span>
          </h3>
          {renderGrid(featured)}
        </section>
      )}

      {rest.length > 0 && (
        <section>
          {featured.length > 0 && (
            <h3 className="mb-2 text-sm font-medium text-ink-secondary">{sectionTitle(effectiveType, filtered.length)}</h3>
          )}
          {renderGrid(rest)}
        </section>
      )}

      {filtered.length === 0 && (
        <Empty
          description={market.length === 0
            ? (effectiveType === 'skill' ? '技能市场暂时没有可用技能，可联系管理员从 SkillHub 导入' : '暂时没有可用的插件')
            : `没有匹配的${noun}，试试其它关键字或清掉筛选条件`}
        />
      )}

      {filtered.length > PAGE_SIZE && (
        <div className="mt-5 flex justify-end">
          <Pagination
            current={currentPage}
            pageSize={PAGE_SIZE}
            total={filtered.length}
            showSizeChanger={false}
            onChange={setPage}
          />
        </div>
      )}
    </div>
  );
}
