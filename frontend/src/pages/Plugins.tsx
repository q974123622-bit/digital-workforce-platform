import { useEffect, useMemo, useState } from 'react';
import { Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Plugin } from '@dwp/shared-schema';
import { api } from '../api/client';

const typeLabel: Record<string, string> = {
  knowledge: '知识库',
  mcp: 'MCP',
  workflow: 'Workflow',
  rpa: 'RPA',
  http: 'HTTP API',
};

export default function Plugins() {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .listPlugins()
      .then(setPlugins)
      .finally(() => setLoading(false));
  }, []);

  const columns: ColumnsType<Plugin> = useMemo(
    () => [
      { title: 'ID', dataIndex: 'id' },
      { title: '名称', dataIndex: 'name' },
      {
        title: '类型',
        dataIndex: 'type',
        render: (value: string) => <Tag color="geekblue">{typeLabel[value] ?? value}</Tag>,
      },
      { title: '数据等级', dataIndex: 'data_level' },
      { title: '接入方式', dataIndex: 'endpoint_ref' },
      { title: '状态', dataIndex: 'status' },
      { title: '描述', dataIndex: 'description' },
    ],
    [],
  );

  return (
    <div>
      <Typography.Title level={3}>插件中心</Typography.Title>
      <Typography.Paragraph type="secondary">插件统一登记，权限与调用在后续 Sprint 通过 Plugin Gateway 生效。</Typography.Paragraph>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={plugins} pagination={false} />
    </div>
  );
}
