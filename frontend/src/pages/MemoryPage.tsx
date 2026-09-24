import { Card, Select, Space, Typography, message } from 'antd';
import { useCallback, useState } from 'react';
import { api } from '../api/client';
import type { MemoryItem } from '../api/client';
import { useAuth } from '../context/AuthContext';
import { useAsyncData } from '../hooks/useAsyncData';
import { ErrorState, LoadingState } from '../components/PageState';
import MemoryList from '../features/memory/MemoryList';
import MemoryEditor from '../features/memory/MemoryEditor';

export default function MemoryPage() {
  const { account } = useAuth();
  const agents = [`DT-${account?.employee_no}`, 'AI-GENERAL', 'AI-INVESTMENT'];
  const [agentId, setAgentId] = useState(agents[0]);
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const fetcher = useCallback(() => api.listMyMemories(agentId), [agentId]);
  const { data, loading, error, reload } = useAsyncData(fetcher);

  const remove = async (memory: MemoryItem) => {
    try { await api.deleteMyMemory(agentId, memory.id); reload(); }
    catch (err) { message.error(`删除失败：${(err as Error).message}`); }
  };
  const retain = async (memory: MemoryItem) => {
    try { await api.retainMyMemory(agentId, memory.id); reload(); }
    catch (err) { message.error(`操作失败：${(err as Error).message}`); }
  };

  return (
    <div className="page-shell space-y-5">
      <div className="page-heading">
        <div>
          <Typography.Title level={2} className="!mb-1 !text-[26px]">我的记忆</Typography.Title>
          <Typography.Text type="secondary">由你掌控AI员工记住的偏好、事实与工作约定</Typography.Text>
        </div>
        <div className="rounded-full bg-brand-soft px-3 py-1.5 text-xs font-medium text-brand">最近 5 轮自动作为短期上下文</div>
      </div>
      <Card className="selector-card">
        <Space>
          <Typography.Text strong>查看对象</Typography.Text>
          <Select
            value={agentId}
            onChange={setAgentId}
            className="w-64"
            options={agents.map((value) => ({
              value,
              label: value.startsWith('DT-') ? '我的分身' : value === 'AI-GENERAL' ? 'AI员工平台' : '投资分析AI员工',
            }))}
          />
        </Space>
      </Card>
      {loading ? <LoadingState rows={5} /> : error ? <ErrorState onRetry={reload} /> : (
        <Card className="content-card" styles={{ body: { padding: '8px 20px' } }}>
          <MemoryList
            memories={data ?? []}
            onEdit={setEditing}
            onDelete={(memory) => void remove(memory)}
            onRetain={(memory) => void retain(memory)}
          />
        </Card>
      )}
      <MemoryEditor
        agentId={agentId}
        memory={editing}
        open={editing !== null}
        onClose={() => setEditing(null)}
        onSaved={reload}
      />
    </div>
  );
}
