import { useCallback, useMemo, useState } from 'react';
import { Badge, Card, Col, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { AuditEvent, Policy } from '@dwp/shared-schema';
import { api } from '../api/client';
import { DecisionTag } from '../components/tags';
import { EmptyState, ErrorState, LoadingState } from '../components/PageState';
import { useAsyncData } from '../hooks/useAsyncData';

const EFFECT_HEX: Record<string, string> = {
  allow: '#52c41a',
  deny: '#ff4d4f',
  approval: '#faad14',
};

function PoliciesPanel() {
  const fetcher = useCallback(() => api.listPolicies(), []);
  const { data: policies, loading, error, reload } = useAsyncData<Policy[]>(fetcher);

  if (loading) return <LoadingState rows={6} />;
  if (error) return <ErrorState onRetry={reload} />;

  return (
    <div>
      {policies && policies.length > 0 ? (
        <Row gutter={[16, 16]}>
          {policies.map((policy) => (
            <Col xs={24} lg={12} xl={8} key={policy.id}>
              <Card
                className="hover-card"
                style={{ borderLeft: `4px solid ${EFFECT_HEX[policy.effect] ?? '#8c8c8c'}` }}
                styles={{ body: { padding: '16px 20px' } }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <Space size={8} wrap>
                    <Typography.Text strong>{policy.name}</Typography.Text>
                    <DecisionTag value={policy.effect} />
                  </Space>
                  <Space size={12} wrap>
                    <Tag>优先级 {policy.priority}</Tag>
                    <Badge status={policy.enabled ? 'success' : 'default'} text={policy.enabled ? '已启用' : '已停用'} />
                  </Space>
                </div>
                <Typography.Paragraph type="secondary" style={{ margin: '10px 0 0', fontSize: 13 }}>
                  {policy.description}
                </Typography.Paragraph>
              </Card>
            </Col>
          ))}
        </Row>
      ) : (
        <EmptyState description="暂无策略" />
      )}
    </div>
  );
}

function AuditPanel() {
  const [decision, setDecision] = useState<string>();
  const fetcher = useCallback(() => api.listAudit({ decision }), [decision]);
  const { data: events, loading, error, reload } = useAsyncData<AuditEvent[]>(fetcher);

  const list = events ?? [];
  const summary = useMemo(
    () => [
      { key: 'total', label: '审计事件', value: list.length, color: '#1f2733' },
      { key: 'allow', label: '允许', value: list.filter((event) => event.decision === 'allow').length, color: '#52c41a' },
      { key: 'deny', label: '拒绝', value: list.filter((event) => event.decision === 'deny').length, color: '#ff4d4f' },
      {
        key: 'approval',
        label: '待审批',
        value: list.filter((event) => event.decision === 'approval').length,
        color: '#faad14',
      },
    ],
    [list],
  );

  const columns: ColumnsType<AuditEvent> = useMemo(
    () => [
      { title: 'ID', dataIndex: 'id', width: 64, render: (value: number) => <span className="mono">{value}</span> },
      {
        title: '时间',
        dataIndex: 'ts',
        width: 170,
        render: (value: string) => (value ? new Date(value).toLocaleString() : '—'),
      },
      { title: 'Trace', dataIndex: 'trace_id', render: (value: string) => <span className="mono">{value}</span> },
      { title: '主体', dataIndex: 'actor' },
      { title: '插件', dataIndex: 'plugin_id' },
      { title: '动作', dataIndex: 'action' },
      { title: '决策', dataIndex: 'decision', render: (value: string) => <DecisionTag value={value} /> },
      {
        title: '原因',
        dataIndex: 'reason',
        ellipsis: true,
        render: (value: string | null) => value ?? '—',
      },
    ],
    [],
  );

  if (error) return <ErrorState onRetry={reload} />;

  return (
    <div>
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {summary.map((item) => (
          <Col xs={12} lg={6} key={item.key}>
            <Card styles={{ body: { padding: '14px 18px' } }}>
              <Statistic
                title={item.label}
                value={item.value}
                className="tabular-nums"
                valueStyle={{ fontSize: 22, fontWeight: 600, color: item.color }}
              />
            </Card>
          </Col>
        ))}
      </Row>
      <Space style={{ marginBottom: 16 }}>
        <Select
          allowClear
          aria-label="按决策筛选"
          placeholder="按决策筛选"
          style={{ width: 150 }}
          value={decision}
          onChange={setDecision}
          options={[
            { value: 'allow', label: '允许' },
            { value: 'deny', label: '拒绝' },
            { value: 'approval', label: '待审批' },
          ]}
        />
      </Space>
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={list}
        pagination={false}
        scroll={{ x: 960 }}
      />
    </div>
  );
}

export default function Security() {
  return (
    <div>
      <Typography.Title level={3}>安全中心</Typography.Title>
      <Tabs
        items={[
          { key: 'policies', label: '权限策略', children: <PoliciesPanel /> },
          { key: 'audit', label: '审计日志', children: <AuditPanel /> },
        ]}
      />
    </div>
  );
}
