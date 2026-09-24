import { ReloadOutlined, RobotOutlined, SearchOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Input, Select, Space, Switch, Table, Tag, Tooltip, Typography, message } from 'antd';
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { UnifiedPlugin } from '../../api/client';
import { api } from '../../api/client';
import { useAsyncData } from '../../hooks/useAsyncData';
import { ErrorState, LoadingState } from '../../components/PageState';
import {
  DATA_LEVEL_OPTIONS,
  PLUGIN_SOURCE_OPTIONS,
  SKILL_CATEGORY_OPTIONS,
  pluginSourceMeta,
} from './pluginMeta';

const STATUS_META: Record<string, { label: string; color: string }> = {
  active: { label: '上架中', color: 'success' },
  withdrawn: { label: '已下架', color: 'default' },
  draft: { label: '草稿', color: 'warning' },
};

/**
 * 技能治理台：导入之后的持续运营入口。
 *
 * SkillHub 只给 tags、不给分类（`category` 全为 null），约 35 个技能连 tags 都没有，
 * 所以「分类自建」必须落在人身上 —— 这张表就是那个落点：改分类、改数据等级、
 * 置推荐位、下架与恢复，以及对英文/超长简介按条重生成中文简介。
 */
export default function SkillGovernance() {
  const fetcher = useCallback(() => api.listAdminPlugins(), []);
  const { data, loading, error, reload } = useAsyncData(fetcher);
  const [rows, setRows] = useState<UnifiedPlugin[]>([]);
  const [keyword, setKeyword] = useState('');
  const [source, setSource] = useState<string | undefined>(undefined);
  const [category, setCategory] = useState<string | undefined>(undefined);
  const [onlyUncategorized, setOnlyUncategorized] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [introId, setIntroId] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  useEffect(() => {
    if (data) setRows(data.filter((row) => row.plugin_type === 'skill'));
  }, [data]);

  const categoryOptions = useMemo(() => {
    const seen = new Set<string>();
    rows.forEach((row) => { if (row.skill_category) seen.add(row.skill_category); });
    return [
      ...SKILL_CATEGORY_OPTIONS.filter((option) => seen.has(option.value)),
      ...SKILL_CATEGORY_OPTIONS.filter((option) => !seen.has(option.value)),
    ];
  }, [rows]);

  const filtered = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    return rows.filter((row) => {
      if (source && (row.source ?? 'platform') !== source) return false;
      if (category && row.skill_category !== category) return false;
      if (onlyUncategorized && row.skill_category) return false;
      if (!needle) return true;
      return `${row.name} ${row.description ?? ''}`.toLowerCase().includes(needle);
    });
  }, [rows, keyword, source, category, onlyUncategorized]);

  const patch = async (row: UnifiedPlugin, payload: Parameters<typeof api.updatePluginGovernance>[1]) => {
    const pluginId = String(row.id);
    setSavingId(pluginId);
    try {
      const updated = await api.updatePluginGovernance(pluginId, payload);
      setRows((current) => current.map((item) => (String(item.id) === pluginId
        ? { ...item, ...updated }
        : item)));
      message.success('已保存');
    } catch (err) {
      message.error(`保存失败：${(err as Error).message}`);
    } finally {
      setSavingId(null);
    }
  };

  const regenerate = async (row: UnifiedPlugin) => {
    const pluginId = String(row.id);
    setIntroId(pluginId);
    try {
      const result = await api.regeneratePluginIntro(pluginId);
      setRows((current) => current.map((item) => (String(item.id) === pluginId
        ? { ...item, description: result.description, skill_category: result.skill_category }
        : item)));
      if (result.generated) message.success('已生成中文简介');
      else message.warning(`已降级为原文首句：${result.degraded_reason ?? '模型不可用'}`);
    } catch (err) {
      message.error(`生成失败：${(err as Error).message}`);
    } finally {
      setIntroId(null);
    }
  };

  if (loading) return <LoadingState rows={7} />;
  if (error) return <ErrorState onRetry={reload} />;

  const uncategorized = rows.filter((row) => !row.skill_category).length;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <Typography.Title level={2} className="!mb-1 !text-[26px]">技能治理</Typography.Title>
          <Typography.Text type="secondary">
            给导入的技能标业务分类、定数据等级、置推荐位，或下架不再维护的技能
          </Typography.Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={reload}>刷新</Button>
      </div>

      <Alert
        className="mb-4"
        type="info"
        showIcon
        message={`共 ${rows.length} 个技能，其中 ${uncategorized} 个还没有业务分类`}
        description="SkillHub 不返回业务分类，分类来自 tags 自动归类 + 这里的人工标注。数据等级改的是授权判定实际读取的字段；下架会停用全部授权，恢复上架不会自动恢复授权。"
      />

      <Card>
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <Input
            allowClear
            aria-label="搜索技能"
            prefix={<SearchOutlined className="text-ink-tertiary" />}
            placeholder="搜索技能名称或简介"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            className="w-full sm:w-[240px]"
          />
          <Select
            allowClear
            aria-label="按来源筛选"
            placeholder="来源"
            style={{ minWidth: 140 }}
            value={source}
            onChange={setSource}
            options={PLUGIN_SOURCE_OPTIONS}
          />
          <Select
            allowClear
            showSearch
            aria-label="按分类筛选"
            placeholder="业务分类"
            style={{ minWidth: 180 }}
            value={category}
            onChange={setCategory}
            options={categoryOptions}
          />
          <Switch
            size="small"
            checked={onlyUncategorized}
            onChange={setOnlyUncategorized}
            aria-label="只看未分类"
          />
          <span className="text-xs text-ink-tertiary">只看未分类</span>
          <div className="flex-1" />
          <span className="text-xs text-ink-tertiary">共 {filtered.length} 个</span>
        </div>

        <Table
          rowKey={(row) => String(row.id)}
          dataSource={filtered}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          size="small"
          scroll={{ x: 1180 }}
          columns={[
            {
              title: '技能', dataIndex: 'name', width: 200,
              render: (value: string, row: UnifiedPlugin) => (
                <div>
                  <div className="text-ink">{value}</div>
                  <div className="text-xs text-ink-tertiary">
                    {row.featured ? '精选 · ' : ''}{row.current_version ? `v${row.current_version}` : '尚未发布'}
                  </div>
                </div>
              ),
            },
            {
              title: '来源', dataIndex: 'source', width: 120,
              render: (value: string) => <Tag color={pluginSourceMeta(value).color}>{pluginSourceMeta(value).label}</Tag>,
            },
            {
              title: '业务分类', dataIndex: 'skill_category', width: 200,
              render: (value: string | null, row: UnifiedPlugin) => (
                <Select
                  size="small" className="w-full" allowClear showSearch
                  placeholder="未分类"
                  value={value ?? undefined}
                  loading={savingId === String(row.id)}
                  onChange={(next) => void patch(row, { skill_category: next ?? '' })}
                  options={categoryOptions}
                />
              ),
            },
            {
              title: '数据等级', dataIndex: 'data_level', width: 120,
              render: (value: string, row: UnifiedPlugin) => (
                <Select
                  size="small" className="w-full"
                  value={value}
                  onChange={(next) => void patch(row, { data_level: next })}
                  options={DATA_LEVEL_OPTIONS}
                />
              ),
            },
            {
              title: '推荐位', dataIndex: 'featured', width: 90,
              render: (value: boolean, row: UnifiedPlugin) => (
                <Tooltip title="置顶到员工技能市场的「精选推荐」">
                  <Switch
                    size="small" checked={Boolean(value)}
                    loading={savingId === String(row.id)}
                    onChange={(checked) => void patch(row, { featured: checked })}
                    aria-label={`切换推荐位 ${row.name}`}
                  />
                </Tooltip>
              ),
            },
            {
              title: '状态', dataIndex: 'status', width: 100,
              render: (value: string) => {
                const meta = STATUS_META[value] ?? { label: value, color: 'default' };
                return <Tag color={meta.color}>{meta.label}</Tag>;
              },
            },
            {
              title: '简介', dataIndex: 'description',
              render: (value: string, row: UnifiedPlugin) => (
                <div className="max-w-[360px]">
                  <Typography.Paragraph type="secondary" className="!mb-0 !text-xs" ellipsis={{ rows: 2 }}>
                    {value || '暂无简介'}
                  </Typography.Paragraph>
                  {row.intro?.generated === false && (
                    <span className="text-xs text-ink-tertiary">原文透传（未生成）</span>
                  )}
                </div>
              ),
            },
            {
              title: '操作', width: 190, fixed: 'right',
              render: (_: unknown, row: UnifiedPlugin) => {
                const pluginId = String(row.id);
                const withdrawn = row.status === 'withdrawn';
                return (
                  <Space size={4}>
                    <Button
                      size="small"
                      icon={<RobotOutlined />}
                      loading={introId === pluginId}
                      onClick={() => void regenerate(row)}
                    >
                      重生成简介
                    </Button>
                    {withdrawn ? (
                      <Button size="small" type="primary" onClick={() => void patch(row, { status: 'active' })}>
                        恢复上架
                      </Button>
                    ) : confirmingId === pluginId ? (
                      <>
                        <Button
                          size="small" danger type="primary"
                          onClick={() => { setConfirmingId(null); void patch(row, { status: 'withdrawn' }); }}
                        >
                          确认下架
                        </Button>
                        <Button size="small" onClick={() => setConfirmingId(null)}>取消</Button>
                      </>
                    ) : (
                      <Button size="small" danger onClick={() => setConfirmingId(pluginId)}>下架</Button>
                    )}
                  </Space>
                );
              },
            },
          ]}
        />
      </Card>
    </div>
  );
}
