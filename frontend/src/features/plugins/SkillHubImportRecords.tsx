import { ReloadOutlined } from '@ant-design/icons';
import { Button, Drawer, Empty, Segmented, Spin, Table, Tag, Typography } from 'antd';
import { useCallback, useState } from 'react';
import { api } from '../../api/client';
import type { SkillHubImportRecord } from '../../api/client';
import { useAsyncData } from '../../hooks/useAsyncData';
import { ErrorState } from '../../components/PageState';
import { formatServerTime } from '../../utils/serverTime';

const STATUS_META: Record<string, { label: string; color: string }> = {
  imported: { label: '导入成功', color: 'green' },
  skipped: { label: '已跳过', color: 'default' },
  failed: { label: '失败', color: 'red' },
  pending: { label: '处理中', color: 'processing' },
};

type StatusFilter = 'all' | 'imported' | 'skipped' | 'failed';

/**
 * 导入记录抽屉：谁在什么时候导入了哪个版本、成功还是失败、失败原因是什么。
 *
 * 只读展示 + 失败项一键重试；抽屉打开时才拉数据（`destroyOnClose` 语义由外层控制）。
 */
export default function SkillHubImportRecords({
  open, onClose, onRetry,
}: {
  open: boolean;
  onClose: () => void;
  onRetry: (slug: string) => void;
}) {
  const [status, setStatus] = useState<StatusFilter>('all');
  const fetcher = useCallback(
    () => api.listSkillHubImports(status === 'all' ? undefined : status),
    [status],
  );
  const { data, loading, error, reload } = useAsyncData(fetcher);
  const rows = data?.items ?? [];

  return (
    <Drawer
      title="SkillHub 导入记录"
      open={open}
      onClose={onClose}
      width={760}
      extra={<Button icon={<ReloadOutlined />} onClick={reload}>刷新</Button>}
    >
      <div className="mb-3 flex items-center gap-3">
        <Segmented
          value={status}
          onChange={(value) => setStatus(value as StatusFilter)}
          options={[
            { label: '全部', value: 'all' },
            { label: '成功', value: 'imported' },
            { label: '跳过', value: 'skipped' },
            { label: '失败', value: 'failed' },
          ]}
        />
        <span className="text-xs text-ink-tertiary">共 {rows.length} 条</span>
      </div>
      {loading && <div className="py-8 text-center"><Spin /></div>}
      {error && <ErrorState onRetry={reload} />}
      {!loading && !error && (
        <Table
          rowKey="id"
          size="small"
          dataSource={rows}
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          locale={{ emptyText: <Empty description="还没有导入记录" /> }}
          columns={[
            {
              title: '技能', dataIndex: 'slug',
              render: (value: string, row: SkillHubImportRecord) => (
                <div>
                  <div className="text-ink">{row.display_name || value}</div>
                  <div className="text-xs text-ink-tertiary">{value}{row.version ? `@${row.version}` : ''}</div>
                </div>
              ),
            },
            {
              title: '分类', dataIndex: 'category', width: 150,
              render: (value: string) => value || '—',
            },
            {
              title: '状态', dataIndex: 'status', width: 110,
              render: (value: string) => {
                const meta = STATUS_META[value] ?? STATUS_META.pending;
                return <Tag color={meta.color}>{meta.label}</Tag>;
              },
            },
            {
              title: '文件数', dataIndex: 'file_count', width: 80,
              render: (value: number) => value || '—',
            },
            {
              title: '导入时间', dataIndex: 'updated_at', width: 170,
              render: (value: string) => formatServerTime(value),
            },
            {
              title: '说明', dataIndex: 'message',
              render: (value: string, row: SkillHubImportRecord) => (
                <div className="flex items-center gap-2">
                  <Typography.Text type="secondary" className="!text-xs">{value || '—'}</Typography.Text>
                  {row.status === 'failed' && (
                    <Button size="small" type="link" className="!px-0" onClick={() => onRetry(row.slug)}>重试</Button>
                  )}
                </div>
              ),
            },
          ]}
        />
      )}
    </Drawer>
  );
}
