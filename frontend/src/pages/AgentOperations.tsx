import { ReloadOutlined } from '@ant-design/icons';
import type { AgentProfile } from '@dwp/shared-schema';
import { Button, Card, Empty, Space, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useState } from 'react';
import { api } from '../api/client';
import { useAsyncData } from '../hooks/useAsyncData';
import { ErrorState, LoadingState } from '../components/PageState';

const { Paragraph, Text, Title } = Typography;

export default function AgentOperations() {
  const fetcher = useCallback(() => api.listAgents(), []);
  const { data, loading, error, reload } = useAsyncData(fetcher);
  const [acting, setActing] = useState('');

  const changeRuntime = async (row: AgentProfile, start: boolean) => {
    setActing(row.employee_id);
    try {
      if (start) await api.startAgentRuntime(row.employee_id);
      else await api.stopAgentRuntime(row.employee_id);
      message.success(start ? 'Harness 运行环境已就绪' : 'Harness 运行环境已停止');
      reload();
    } catch (cause) {
      message.error((cause as Error).message);
    } finally {
      setActing('');
    }
  };

  const columns: ColumnsType<AgentProfile> = [
    {
      title: '数字员工 / 分身', key: 'identity', width: 220,
      render: (_, row) => <div><Text strong>{row.display_name}</Text><div className="text-xs text-[#86909c]">{row.employee_id} · {row.department}</div></div>,
    },
    {
      title: '身份', dataIndex: 'identity_kind', width: 120,
      render: (value) => <Tag color={value === 'human_twin' ? 'blue' : 'default'}>{value === 'human_twin' ? '真人分身' : '岗位同事'}</Tag>,
    },
    {
      title: '职责与知识范围', key: 'scope',
      render: (_, row) => <div><Paragraph className="!mb-1 !text-sm" ellipsis={{ rows: 2 }}>{row.responsibilities.join('；') || '待配置职责'}</Paragraph><Space size={[4, 4]} wrap>{row.knowledge_domains.map((domain) => <Tag key={domain}>{domain}</Tag>)}</Space></div>,
    },
    {
      title: 'Harness', key: 'runtime', width: 180,
      render: (_, row) => <div><Tag color={row.runtime_state === 'ready' ? 'green' : row.runtime_state === 'failed' ? 'red' : 'default'}>{row.runtime_state}</Tag><div className="mt-1 text-xs text-[#86909c]">{row.container_name}</div></div>,
    },
    {
      title: '人设', key: 'persona', width: 110,
      render: (_, row) => <div>v{row.persona_version}<div className="text-xs text-[#86909c]">{row.persona_status}</div></div>,
    },
    {
      title: '操作', key: 'action', width: 150,
      render: (_, row) => <Space><Button size="small" type="primary" loading={acting === row.employee_id} disabled={row.runtime_state === 'ready'} onClick={() => changeRuntime(row, true)}>启动</Button><Button size="small" loading={acting === row.employee_id} disabled={row.runtime_state === 'stopped'} onClick={() => changeRuntime(row, false)}>停止</Button></Space>,
    },
  ];

  if (loading) return <LoadingState rows={8} />;
  if (error) return <ErrorState onRetry={reload} />;
  const rows = data ?? [];
  return (
    <div>
      <div className="mb-4 flex items-start justify-between">
        <div><Title level={4} className="!mb-1">数字员工与运行环境</Title><Text type="secondary">数字分身和岗位型数字员工均使用独立 Harness 实例与持久工作区</Text></div>
        <Button icon={<ReloadOutlined />} onClick={reload}>刷新</Button>
      </div>
      <Card className="border-[#e5e6eb]" styles={{ body: { padding: 0 } }}>
        {rows.length ? <Table rowKey="employee_id" columns={columns} dataSource={rows} pagination={false} scroll={{ x: 980 }} /> : <Empty className="py-12" />}
      </Card>
    </div>
  );
}
