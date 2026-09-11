import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AuditOutlined,
  DatabaseOutlined,
  ExclamationCircleOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { Card, Empty, List, Space, Statistic, Tag, Typography } from 'antd';
import type { AuditEvent } from '@dwp/shared-schema';
import { api } from '../api/client';
import { DecisionTag } from '../components/tags';
import { ErrorState, LoadingState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';

const formatTime = (ts?: string | null) => (ts ? new Date(ts).toLocaleString() : '—');

interface DashboardData {
  pendingPlugins: number;
  unavailableAgents: number;
  knowledgeIssues: number;
  memoryBacklog: number;
  audit: AuditEvent[];
}

export default function Dashboard() {
  const navigate = useNavigate();
  const fetcher = useCallback(async (): Promise<DashboardData> => {
    const [submissions, agents, knowledgeBases, memory, audit] = await Promise.all([
      api.listPluginSubmissions(),
      api.listAgents(),
      api.listKnowledgeBases(),
      api.memoryHealth(),
      api.listAudit(),
    ]);
    return {
      pendingPlugins: submissions.filter((row) => row.review_status === 'pending').length,
      unavailableAgents: agents.filter((row) => !['ready', 'busy'].includes(row.runtime_state)).length,
      knowledgeIssues: knowledgeBases.filter((row) => row.status !== 'active' && row.status !== 'connected').length,
      memoryBacklog: (memory.pending_jobs ?? 0) + (memory.failed_jobs ?? 0),
      audit,
    };
  }, []);
  const { data, loading, error, reload } = useAsyncData<DashboardData>(fetcher);

  if (loading) return <LoadingState rows={8} />;
  if (error) return <ErrorState onRetry={reload} />;

  const todos = [
    { key: '/plugins', label: '待审核插件', value: data?.pendingPlugins ?? 0, icon: <DatabaseOutlined />, color: 'var(--dwp-warning)', bg: 'var(--dwp-warning-soft)' },
    { key: '/agents', label: '不可用数字员工', value: data?.unavailableAgents ?? 0, icon: <RobotOutlined />, color: 'var(--dwp-danger)', bg: 'var(--dwp-danger-soft)' },
    { key: '/knowledge', label: '知识连接异常', value: data?.knowledgeIssues ?? 0, icon: <ExclamationCircleOutlined />, color: 'var(--dwp-warning)', bg: 'var(--dwp-warning-soft)' },
    { key: '/settings', label: '记忆同步积压', value: data?.memoryBacklog ?? 0, icon: <SafetyCertificateOutlined />, color: 'var(--dwp-brand)', bg: 'var(--dwp-brand-soft)' },
  ];

  return (
    <div className="space-y-5">
      <div className="page-heading">
        <div>
        <Typography.Title level={2} className="!mb-1 !text-[26px]">运行概览</Typography.Title>
        <Typography.Text type="secondary">待办优先：先处理需要人工介入的异常与审核</Typography.Text>
        </div>
        <Tag color="blue">MVP 运行环境</Tag>
      </div>

      <section className="admin-metrics grid gap-px overflow-hidden rounded-container border border-line bg-line sm:grid-cols-2 xl:grid-cols-4">
        {todos.map((todo) => (
          <button
            key={todo.key}
            type="button"
            onClick={() => navigate(todo.key)}
            className="cursor-pointer border-0 bg-surface p-5 text-left hover:bg-brand-soft"
          >
            <div className="flex items-center gap-3">
              <span className="grid h-11 w-11 place-items-center rounded-control text-xl" style={{ background: todo.bg, color: todo.color }}>
                {todo.icon}
              </span>
              <Statistic title={todo.label} value={todo.value} valueStyle={{ fontSize: 24, fontWeight: 600, color: todo.color }} />
            </div>
          </button>
        ))}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="最近审计动态"
          extra={<Typography.Link onClick={() => navigate('/security')} style={{ fontSize: 13 }}>查看全部</Typography.Link>}
        >
          {!data?.audit.length ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无审计事件" />
          ) : (
            <List
              size="small"
              dataSource={data.audit.slice(0, 5)}
              renderItem={(item) => (
                <List.Item style={{ padding: '10px 0' }}>
                  <List.Item.Meta
                    title={
                      <Space size={8}>
                        <AuditOutlined style={{ color: 'var(--dwp-text-secondary)' }} />
                        <span>{item.actor} · {item.action}{item.plugin_id ? ` · ${item.plugin_id}` : ''}</span>
                      </Space>
                    }
                    description={<span style={{ fontSize: 13 }}>{item.reason ?? item.result_summary ?? '—'}</span>}
                  />
                  <div style={{ textAlign: 'right', marginLeft: 16 }}>
                    <DecisionTag value={item.decision} />
                    <div style={{ fontSize: 12, color: 'var(--dwp-text-secondary)', marginTop: 4 }}>{formatTime(item.ts)}</div>
                  </div>
                </List.Item>
              )}
            />
          )}
        </Card>
        <Card title="当前待办">
          <div className="space-y-2">
            {todos.map((todo) => (
              <div key={todo.key} className="flex items-center justify-between rounded-control bg-surface-subtle px-3 py-2.5">
                <Space size={8}>
                  <span style={{ color: todo.color }}>{todo.icon}</span>
                  <span className="text-sm text-ink">处理：{todo.label}</span>
                </Space>
                <Tag color={todo.value > 0 ? 'warning' : 'default'}>{todo.value > 0 ? `${todo.value} 项待处理` : '暂无'}</Tag>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
