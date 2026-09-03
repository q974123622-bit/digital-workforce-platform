import { DeleteOutlined, PushpinOutlined } from '@ant-design/icons';
import { Button, Card, Empty, List, Select, Space, Tag, Typography } from 'antd';
import { useCallback, useState } from 'react';
import { api } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { useAsyncData } from '../hooks/useAsyncData';
import { ErrorState, LoadingState } from '../components/PageState';

export default function MemoryPage() {
  const { account } = useAuth();
  const agents = [`DT-${account?.employee_no}`, 'AI-GENERAL', 'AI-INVESTMENT'];
  const [agentId, setAgentId] = useState(agents[0]);
  const fetcher = useCallback(() => api.listMyMemories(agentId), [agentId]);
  const { data, loading, error, reload } = useAsyncData(fetcher);
  return <div className="space-y-4"><div><Typography.Title level={3} className="!mb-1">我的记忆</Typography.Title><Typography.Text type="secondary">长期记忆按“当前账号 × 数字员工”隔离；完整聊天记录仍在原会话中</Typography.Text></div>
    <Card><Space><Typography.Text strong>查看对象</Typography.Text><Select value={agentId} onChange={setAgentId} className="w-64" options={agents.map((value) => ({ value, label: value.startsWith('DT-') ? '我的数字分身' : value === 'AI-GENERAL' ? 'AI员工平台' : '投资分析AI员工' }))} /></Space></Card>
    {loading ? <LoadingState rows={5} /> : error ? <ErrorState onRetry={reload} /> : <Card>{data?.length ? <List dataSource={data} renderItem={(item) => <List.Item actions={[!item.retained && <Button type="link" icon={<PushpinOutlined />} onClick={() => api.retainMyMemory(agentId, item.id).then(reload)}>长期保留</Button>, <Button danger type="link" icon={<DeleteOutlined />} onClick={() => api.deleteMyMemory(agentId, item.id).then(reload)}>删除</Button>]}><List.Item.Meta title={<Space><Tag>{item.memory_type}</Tag><Tag color={item.source === 'explicit' ? 'blue' : 'default'}>{item.source === 'explicit' ? '显式记忆' : '自动摘要'}</Tag><Tag color={item.sync_status === 'failed' ? 'red' : 'green'}>{item.sync_status}</Tag></Space>} description={item.content} /></List.Item>} /> : <Empty description="暂无长期记忆；最近五轮仍作为短期上下文使用" />}</Card>}
  </div>;
}
