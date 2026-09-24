import { DatabaseOutlined, ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Table, Tag, Typography, message } from 'antd';
import { useCallback, useState } from 'react';
import type { KnowledgeBase, KnowledgeSourceStatus } from '@dwp/shared-schema';
import { api } from '../api/client';
import { ErrorState, LoadingState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';
import { formatServerTime } from '../utils/serverTime';

const { Title, Text } = Typography;

interface KnowledgeData {
  bases: KnowledgeBase[];
  sources: KnowledgeSourceStatus[];
}

/** 通道中文名：测试环境走 REST，生产环境走 VeRAG MCP。 */
const SOURCE_LABEL: Record<string, string> = {
  rest: '测试 REST',
  verag: '生产 VeRAG',
};

const STATUS_COLOR: Record<string, string> = {
  active: 'green',
  connected: 'green',
  syncing: 'blue',
};

function sourceTone(state: string): { label: string; color: string; grey: boolean } {
  if (state === 'available') return { label: '连通正常', color: 'green', grey: false };
  if (state === 'unavailable') return { label: '不可用', color: 'default', grey: true };
  return { label: '尚未检测', color: 'default', grey: true };
}

function SourceCard({ row }: { row: KnowledgeSourceStatus }) {
  const tone = sourceTone(row.state);
  const detail = row.detail ?? {};
  const visibleBases = detail.visible_knowledge_bases;
  const toolsCount = detail.tools_count;

  return (
    <Card size="small" className={tone.grey ? 'opacity-80' : undefined}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span
              className={`grid h-8 w-8 place-items-center rounded-control ${
                tone.grey ? 'bg-surface-subtle text-ink-tertiary' : 'bg-brand-soft text-brand'
              }`}
            >
              <DatabaseOutlined />
            </span>
            <div>
              <div className="font-medium text-ink">{row.label}</div>
              <div className="text-xs text-ink-tertiary">{row.environment_label}</div>
            </div>
          </div>
        </div>
        <Tag color={tone.color}>{tone.label}</Tag>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-secondary">
        <span>最近检测：{formatServerTime(row.checked_at)}</span>
        <span>耗时：{row.latency_ms ? `${row.latency_ms} ms` : '—'}</span>
        {typeof visibleBases === 'number' && <span>可见知识库：{visibleBases} 个</span>}
        {typeof toolsCount === 'number' && <span>工具数：{toolsCount}</span>}
        {!row.configured && <span>该通道未配置</span>}
      </div>
      {row.state === 'unavailable' && row.last_error && (
        <div className="mt-2 truncate text-xs text-ink-tertiary" title={row.last_error}>
          原因：{row.last_error}
        </div>
      )}
    </Card>
  );
}

export default function Knowledge() {
  const [probing, setProbing] = useState(false);
  const fetcher = useCallback(async (): Promise<KnowledgeData> => {
    const [bases, sources] = await Promise.all([
      api.listKnowledgeBases(),
      // 数据源状态是管理端接口：取不到时降级为空列表，不影响知识库目录展示。
      api.listKnowledgeSources().catch(() => [] as KnowledgeSourceStatus[]),
    ]);
    return { bases, sources };
  }, []);
  const { data, loading, error, reload } = useAsyncData<KnowledgeData>(fetcher);

  const reprobe = useCallback(async () => {
    setProbing(true);
    try {
      await api.probeKnowledgeSources({ scope: 'knowledge' });
      message.success('数据源重测完成');
    } catch (err) {
      message.error(err instanceof Error ? err.message : '重测失败，请稍后重试');
    } finally {
      setProbing(false);
      reload();
    }
  }, [reload]);

  if (loading) return <LoadingState rows={6} />;
  if (error) return <ErrorState onRetry={reload} />;

  const bases = data?.bases ?? [];
  const sources = data?.sources ?? [];
  const unavailableCount = bases.filter((row) => row.available === false).length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Title level={2} className="!mb-1 !text-[26px]">知识资源</Title>
          <Text type="secondary">
            双通道并存：测试环境走内部知识引擎 REST，生产环境走 VeRAG MCP；一个知识库两种环境共用同一份授权
          </Text>
        </div>
        <Button icon={<ReloadOutlined />} loading={probing} onClick={reprobe}>
          重新检测
        </Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {sources.map((row) => (
          <SourceCard key={row.source} row={row} />
        ))}
        {sources.length === 0 && (
          <Card size="small">
            <Text type="secondary">暂未取到数据源状态，可点击「重新检测」触发一次探测。</Text>
          </Card>
        )}
      </div>

      <Alert
        showIcon
        type="info"
        message="不可用只影响AI员工检索，知识库目录仍保留"
        description={
          unavailableCount > 0
            ? `当前有 ${unavailableCount} 个知识库的数据源不可达：AI员工侧会暂时摘除这些库，数据源恢复后自动重新可用。`
            : '数据源不可达时，AI员工侧会暂时摘除对应知识库，管理端目录保留并置灰，恢复后自动重新可用。'
        }
      />

      <Card>
        <Table
          rowKey="id"
          pagination={false}
          dataSource={bases}
          onRow={(row) => ({
            title: row.available === false ? row.unavailable_reason : undefined,
            style: row.available === false ? { background: 'var(--dwp-surface-subtle)' } : undefined,
          })}
          columns={[
            {
              title: '知识库',
              dataIndex: 'name',
              render: (name: string, row) => {
                const grey = row.available === false;
                return (
                  <div className="flex items-center gap-2">
                    <span
                      className={`grid h-8 w-8 place-items-center rounded-control ${
                        grey ? 'bg-surface-subtle text-ink-tertiary' : 'bg-brand-soft text-brand'
                      }`}
                    >
                      <DatabaseOutlined />
                    </span>
                    <div>
                      <div className={grey ? 'font-medium text-ink-tertiary' : 'font-medium text-ink'}>
                        {name}
                      </div>
                      <div className="text-xs text-ink-tertiary">{row.domain}</div>
                    </div>
                  </div>
                );
              },
            },
            {
              title: '等级',
              dataIndex: 'data_level',
              width: 90,
              render: (v: string) => <Tag>{v ?? '未标注'}</Tag>,
            },
            {
              title: '数据源',
              dataIndex: 'sources',
              width: 200,
              render: (_: unknown, row: KnowledgeBase) => {
                const mapping = row.sources ?? {};
                const keys = Object.keys(mapping);
                if (keys.length === 0) return <Text type="secondary">按环境配置</Text>;
                return (
                  <div className="flex flex-wrap gap-1">
                    {keys.map((key) => {
                      const active = row.active_source === key;
                      const label = SOURCE_LABEL[key] ?? key;
                      return (
                        <Tag
                          key={key}
                          color={active ? 'blue' : 'default'}
                          title={`远端库 ID：${mapping[key]}${active ? '（当前生效）' : ''}`}
                        >
                          {label}
                          {active ? ' · 生效' : ''}
                        </Tag>
                      );
                    })}
                  </div>
                );
              },
            },
            {
              title: '状态',
              dataIndex: 'available',
              width: 110,
              render: (available: boolean | undefined, row: KnowledgeBase) => {
                if (available === false) {
                  return (
                    <Tag color="default" title={row.unavailable_reason}>
                      不可用
                    </Tag>
                  );
                }
                const status = row.status;
                return (
                  <Tag color={STATUS_COLOR[status] ?? 'green'}>
                    {status === 'active' ? '已登记' : status === 'syncing' ? '同步中' : '可用'}
                  </Tag>
                );
              },
            },
            { title: '说明', dataIndex: 'description', ellipsis: true },
          ]}
        />
      </Card>
    </div>
  );
}
