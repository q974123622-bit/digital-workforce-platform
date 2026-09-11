import { DatabaseOutlined } from '@ant-design/icons';
import { Alert, Card, Table, Tag, Typography } from 'antd';
import { useCallback } from 'react';
import { api } from '../api/client';
import { useAsyncData } from '../hooks/useAsyncData';
import { ErrorState, LoadingState } from '../components/PageState';

const { Title, Text } = Typography;

const STATUS_COLOR: Record<string, string> = {
  active: 'green',
  connected: 'green',
  disconnected: 'red',
  syncing: 'blue',
};

export default function Knowledge() {
  const fetcher = useCallback(() => api.listKnowledgeBases(), []);
  const { data, loading, error, reload } = useAsyncData(fetcher);

  if (loading) return <LoadingState rows={6} />;
  if (error) return <ErrorState onRetry={reload} />;

  const bases = data ?? [];

  return (
    <div className="space-y-4">
      <div>
        <Title level={2} className="!mb-1 !text-[26px]">知识资源</Title>
        <Text type="secondary">查看知识库资源目录、数据等级和登记状态</Text>
      </div>
      <Alert
        showIcon
        type="info"
        message="插件授权与知识库授权相互独立"
        description="知识库 MCP 决定数字员工是否具备检索工具，具体知识库授权决定可以查询哪些资源。当前接口未返回探测时间、内部映射或运行模式时，界面不会推断为连接成功。"
      />
      <Card>
        <Table
          rowKey="id"
          pagination={false}
          dataSource={bases}
          columns={[
            {
              title: '知识库',
              dataIndex: 'name',
              render: (name: string, row) => (
                <div className="flex items-center gap-2">
                  <span className="grid h-8 w-8 place-items-center rounded-control bg-brand-soft text-brand">
                    <DatabaseOutlined />
                  </span>
                  <div>
                    <div className="font-medium text-ink">{name}</div>
                    <div className="text-xs text-ink-secondary">{row.domain}</div>
                  </div>
                </div>
              ),
            },
            { title: '等级', dataIndex: 'data_level', width: 100, render: (v: string) => <Tag>{v ?? '未标注'}</Tag> },
            { title: '资源状态', dataIndex: 'status', width: 120, render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{v === 'active' ? '已登记' : v === 'connected' ? '已连接' : v === 'disconnected' ? '连接异常' : v === 'syncing' ? '同步中' : '尚未检查'}</Tag> },
            { title: '说明', dataIndex: 'description', ellipsis: true },
          ]}
        />
      </Card>
    </div>
  );
}
