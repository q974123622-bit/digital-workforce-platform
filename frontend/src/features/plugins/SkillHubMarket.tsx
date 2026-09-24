import { CloudDownloadOutlined, HistoryOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { Alert, Button, Checkbox, Empty, Input, Pagination, Progress, Select, Spin, Tag, Typography, message } from 'antd';
import { useCallback, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { SkillHubImportRecord } from '../../api/client';
import { useAsyncData } from '../../hooks/useAsyncData';
import { ErrorState, LoadingState } from '../../components/PageState';
import SkillHubImportRecords from './SkillHubImportRecords';
import SkillHubSkillCard from './SkillHubSkillCard';

const { Text } = Typography;
const PAGE_SIZE = 24;
/** 与后端 `MAX_BATCH_SLUGS` 对齐：一次批量导入的条数上限。 */
const MAX_BATCH = 200;

const STATUS_META: Record<string, { label: string; color: string }> = {
  imported: { label: '导入成功', color: 'green' },
  skipped: { label: '已跳过', color: 'default' },
  failed: { label: '失败', color: 'red' },
  pending: { label: '处理中', color: 'processing' },
};

/**
 * 管理端「技能市场」：浏览内网 SkillHub 全部技能并把它们导入平台货架。
 *
 * 已导入的卡片置为不可勾选（幂等由后端按 slug+version 兜底）；导入是**后台批次**，
 * 提交后轮询进度条，结果逐条回显、失败项可直接重试 —— 一百多个技能全量灌库时
 * 不需要靠猜，也不会撞上网关的 300 秒读超时。
 */
export default function SkillHubMarket() {
  const [keywordDraft, setKeywordDraft] = useState('');
  const [keyword, setKeyword] = useState('');
  const [category, setCategory] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [importing, setImporting] = useState(false);
  const [batchProgress, setBatchProgress] = useState<{ done: number; total: number } | null>(null);
  const [results, setResults] = useState<SkillHubImportRecord[]>([]);
  const [recordsOpen, setRecordsOpen] = useState(false);

  const overviewFetcher = useCallback(() => api.getSkillHubOverview(), []);
  const overview = useAsyncData(overviewFetcher);
  const listFetcher = useCallback(
    () => api.listSkillHubSkills({ page, pageSize: PAGE_SIZE, keyword, category }),
    [page, keyword, category],
  );
  const list = useAsyncData(listFetcher);

  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const importedTotal = list.data?.imported_total ?? 0;
  const categories = overview.data?.categories ?? [];
  const enabled = overview.data?.enabled ?? false;

  const selectable = useMemo(() => items.filter((item) => !item.imported).map((item) => item.slug), [items]);
  const allSelected = selectable.length > 0 && selectable.every((slug) => selected.includes(slug));

  const toggle = (slug: string, next: boolean) => {
    setSelected((current) => (next
      ? [...current, slug]
      : current.filter((item) => item !== slug)));
  };

  /** 把全量目录里还没导入的 slug 收集齐（服务端每页上限 100，131 条约两页）。 */
  const collectPendingSlugs = async (): Promise<string[]> => {
    const slugs: string[] = [];
    for (let current = 1; current <= 20; current += 1) {
      const reply = await api.listSkillHubSkills({ page: current, pageSize: 100 });
      reply.items.forEach((item) => { if (!item.imported) slugs.push(item.slug); });
      if (current * 100 >= reply.total) break;
    }
    return slugs;
  };

  const runImport = async (slugs: string[]) => {
    const batch = slugs.slice(0, MAX_BATCH);
    if (!batch.length) {
      message.info('请先勾选要导入的技能');
      return;
    }
    setImporting(true);
    setBatchProgress({ done: 0, total: batch.length });
    try {
      // 导入改成了后台批次：接口立即返回 batch_id，进度靠轮询。
      // 后端串行下载 + 转换 + 按需生成简介，131 条要几分钟，同步做必然被网关 300s 掐断。
      const started = await api.importSkillHubSkills(batch);
      let snapshot = await api.getSkillHubImportBatch(started.batch_id);
      setResults(snapshot.results);
      setBatchProgress({ done: snapshot.done, total: snapshot.total || batch.length });
      for (let tick = 0; tick < 400 && snapshot.status === 'running'; tick += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        snapshot = await api.getSkillHubImportBatch(started.batch_id);
        setResults(snapshot.results);
        setBatchProgress({ done: snapshot.done, total: snapshot.total || batch.length });
      }
      setSelected([]);
      list.reload();
      if (snapshot.status === 'running') {
        message.warning('导入仍在后台进行，可稍后在「导入记录」查看结果');
      } else {
        message.success(
          `导入完成：成功 ${snapshot.summary.imported}、跳过 ${snapshot.summary.skipped}、失败 ${snapshot.summary.failed}`,
        );
      }
    } catch (err) {
      message.error(`导入失败：${(err as Error).message}`);
    } finally {
      setImporting(false);
      setBatchProgress(null);
    }
  };

  const importAllPending = async () => {
    setImporting(true);
    try {
      const slugs = await collectPendingSlugs();
      if (!slugs.length) {
        message.info('目录里的技能都已导入');
        setImporting(false);
        return;
      }
      await runImport(slugs);
    } catch (err) {
      message.error(`读取目录失败：${(err as Error).message}`);
      setImporting(false);
    }
  };

  if (overview.loading) return <LoadingState rows={6} />;
  if (overview.error) return <ErrorState onRetry={overview.reload} />;

  if (!enabled) {
    return (
      <div>
        <div className="mb-4">
          <Typography.Title level={2} className="!mb-1 !text-[26px]">技能市场</Typography.Title>
          <Typography.Text type="secondary">从内网 SkillHub 挑选技能并导入平台货架</Typography.Text>
        </div>
        <Alert
          showIcon
          type="warning"
          message="SkillHub 未接入"
          description={overview.data?.message || '请配置 DWP_SKILLHUB_BASE_URL 后重试。'}
        />
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <Typography.Title level={2} className="!mb-1 !text-[26px]">技能市场</Typography.Title>
          <Typography.Text type="secondary">
            浏览内网 SkillHub 的技能并导入平台货架；导入后所有员工可见，员工申请、管理员审批后授权到指定 AI员工
          </Typography.Text>
        </div>
        <div className="flex items-center gap-2">
          <Button icon={<HistoryOutlined />} onClick={() => setRecordsOpen(true)}>导入记录</Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => { void api.refreshSkillHubCache().then(() => { list.reload(); message.success('已刷新目录'); }); }}
          >
            刷新目录
          </Button>
          <Button
            type="primary"
            icon={<CloudDownloadOutlined />}
            loading={importing}
            onClick={() => void importAllPending()}
          >
            一键导入全部
          </Button>
        </div>
      </div>

      <Alert
        className="mb-4"
        showIcon
        type="info"
        message="导入即发布，全可见 + 申请审批"
        description={`技能包（SKILL.md 与 scripts 脚本）会完整入库并投放到运行容器，员工要用需先申请。当前已导入 ${importedTotal} 个，目录共 ${total} 个。`}
      />

      <div className="flex flex-col gap-4 xl:flex-row">
        <aside className="skillhub-category-rail hidden shrink-0 xl:block xl:w-[200px]">
          <div className="sticky top-2 space-y-1 rounded-container bg-surface p-2">
            <button
              type="button"
              className={`w-full rounded-control px-3 py-2 text-left text-xs ${category ? 'text-ink-secondary hover:bg-surface-subtle' : 'bg-[var(--dwp-brand-soft)] text-[var(--dwp-brand)]'}`}
              onClick={() => { setCategory(undefined); setPage(1); }}
            >
              全部技能（{total}）
            </button>
            {categories.map((row) => (
              <button
                key={row.category}
                type="button"
                className={`w-full rounded-control px-3 py-2 text-left text-xs ${category === row.category ? 'bg-[var(--dwp-brand-soft)] text-[var(--dwp-brand)]' : 'text-ink-secondary hover:bg-surface-subtle'}`}
                onClick={() => { setCategory(row.category); setPage(1); }}
              >
                {row.category}
              </button>
            ))}
          </div>
        </aside>

        <div className="min-w-0 flex-1">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <Input
              allowClear
              aria-label="搜索 SkillHub 技能"
              prefix={<SearchOutlined className="text-ink-tertiary" />}
              placeholder="搜索技能名称或用途，回车确认"
              value={keywordDraft}
              onChange={(event) => setKeywordDraft(event.target.value)}
              onPressEnter={() => { setKeyword(keywordDraft.trim()); setPage(1); }}
              className="w-full sm:w-[260px]"
            />
            <Select
              allowClear
              className="xl:hidden"
              aria-label="按分类筛选"
              placeholder="分类"
              style={{ minWidth: 180 }}
              value={category}
              onChange={(next) => { setCategory(next); setPage(1); }}
              options={categories.map((row) => ({ value: row.category, label: row.category }))}
            />
            <Checkbox
              checked={allSelected}
              indeterminate={!allSelected && selectable.some((slug) => selected.includes(slug))}
              disabled={selectable.length === 0}
              onChange={(event) => setSelected(event.target.checked ? selectable : [])}
            >
              全选本页
            </Checkbox>
            <span className="text-xs text-ink-tertiary">
              已选 {selected.length} 项 · 本页 {items.length} 个
            </span>
          </div>

          {list.loading && <div className="py-10 text-center"><Spin /></div>}
          {list.error && <ErrorState onRetry={list.reload} />}
          {!list.loading && !list.error && (
            <>
              <div className="plugin-catalog-grid grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
                {items.map((skill) => (
                  <SkillHubSkillCard
                    key={skill.slug}
                    skill={skill}
                    checked={selected.includes(skill.slug)}
                    onToggle={toggle}
                  />
                ))}
                {items.length === 0 && (
                  <Empty className="md:col-span-2 2xl:col-span-3" description="没有匹配的技能，换个关键字或分类试试" />
                )}
              </div>
              <div className="mt-4 flex justify-end">
                <Pagination
                  current={page}
                  pageSize={PAGE_SIZE}
                  total={total}
                  showSizeChanger={false}
                  onChange={setPage}
                  hideOnSinglePage={total <= PAGE_SIZE}
                />
              </div>
            </>
          )}
        </div>
      </div>

      {selected.length > 0 && (
        <div className="skillhub-action-bar sticky bottom-0 mt-4 flex flex-wrap items-center gap-3 rounded-container border border-line bg-surface px-4 py-3">
          <Text strong>已选 {selected.length} 项</Text>
          <Button size="small" type="link" className="!px-0" onClick={() => setSelected([])}>清空选择</Button>
          <div className="flex-1" />
          <Button
            type="primary"
            icon={<CloudDownloadOutlined />}
            loading={importing}
            onClick={() => void runImport(selected)}
          >
            批量导入
          </Button>
        </div>
      )}

      {importing && (
        <div className="mt-3">
          <Progress
            percent={batchProgress && batchProgress.total
              ? Math.round((batchProgress.done / batchProgress.total) * 100)
              : 0}
            status="active"
            showInfo={false}
          />
          <div className="mt-1 text-xs text-ink-tertiary">
            后台串行导入中{batchProgress ? `（已完成 ${batchProgress.done}/${batchProgress.total}）` : ''}
            ，可以切到别的页签，结果会记在「导入记录」里
          </div>
        </div>
      )}

      {results.length > 0 && (
        <div className="mt-4 rounded-container border border-line bg-surface p-4">
          <div className="mb-2 flex items-center justify-between">
            <Text strong>本次导入结果（{results.length}）</Text>
            <Button size="small" type="link" className="!px-0" onClick={() => setResults([])}>收起</Button>
          </div>
          <div className="space-y-2">
            {results.map((row) => {
              const meta = STATUS_META[row.status] ?? STATUS_META.pending;
              return (
                <div key={row.id} className="flex flex-wrap items-center gap-3 rounded-control bg-surface-subtle px-3 py-2">
                  <Tag color={meta.color}>{meta.label}</Tag>
                  <span className="text-xs text-ink">{row.slug}{row.version ? `@${row.version}` : ''}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-ink-secondary">{row.message}</span>
                  {row.status === 'failed' && (
                    <Button size="small" onClick={() => void runImport([row.slug])}>重试</Button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <SkillHubImportRecords
        open={recordsOpen}
        onClose={() => setRecordsOpen(false)}
        onRetry={(slug) => void runImport([slug])}
      />
    </div>
  );
}
