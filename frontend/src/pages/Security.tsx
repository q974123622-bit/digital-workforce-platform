import { useEffect, useMemo, useState } from 'react';
import { Select, Space, Table, Tag, Tabs, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { AuditEvent, Policy } from '@dwp/shared-schema';
import { api } from '../api/client';

function PoliciesTable() {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .listPolicies()
      .then(setPolicies)
      .finally(() => setLoading(false));
  }, []);

  const columns: ColumnsType<Policy> = useMemo(
    () => [
      { title: 'ID', dataIndex: 'id' },
      { title: '名称', dataIndex: 'name' },
      {
        title: '效果',
        dataIndex: 'effect',
        render: (value: string) => (
          <Tag color={value === 'allow' ? 'green' : value === 'deny' ? 'red' : 'orange'}>{value}</Tag>
        ),
      },
      { title: '优先级', dataIndex: 'priority' },
      { title: '启用', dataIndex: 'enabled', render: (value: boolean) => (value ? '是' : '否') },
      { title: '说明', dataIndex: 'description' },
    ],
    [],
  );

  return <Table rowKey="id" loading={loading} columns={columns} dataSource={policies} pagination={false} />;
}

function AuditTable() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [decision, setDecision] = useState<string>();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .listAudit({ decision })
      .then(setEvents)
      .finally(() => setLoading(false));
  }, [decision]);

  const columns: ColumnsType<AuditEvent> = useMemo(
    () => [
      { title: 'ID', dataIndex: 'id' },
      { title: 'Trace', dataIndex: 'trace_id' },
      { title: '时间', dataIndex: 'ts', render: (value: string) => new Date(value).toLocaleString() },
      { title: '主体', dataIndex: 'actor' },
      { title: '插件', dataIndex: 'plugin_id' },
      { title: '动作', dataIndex: 'action' },
      {
        title: '决策',
        dataIndex: 'decision',
        render: (value: string) => (
          <Tag color={value === 'allow' ? 'green' : value === 'deny' ? 'red' : 'orange'}>{value}</Tag>
        ),
      },
      { title: '原因', dataIndex: 'reason' },
    ],
    [],
  );

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="按决策筛选"
          style={{ width: 160 }}
          value={decision}
          onChange={setDecision}
          options={[
            { value: 'allow', label: 'allow' },
            { value: 'deny', label: 'deny' },
            { value: 'approval', label: 'approval' },
          ]}
        />
      </Space>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={events} pagination={false} />
    </div>
  );
}

export default function Security() {
  return (
    <div>
      <Typography.Title level={3}>安全中心</Typography.Title>
      <Tabs
        items={[
          { key: 'policies', label: '权限策略', children: <PoliciesTable /> },
          { key: 'audit', label: '审计日志', children: <AuditTable /> },
        ]}
      />
    </div>
  );
}
