import { DeleteOutlined, EditOutlined, PushpinOutlined } from '@ant-design/icons';
import { Button, Empty, List, Popconfirm, Tag } from 'antd';
import type { MemoryItem } from '../../api/client';

const TYPE_LABEL: Record<string, string> = {
  preference: '偏好',
  fact: '事实',
  workflow: '工作流',
  decision: '决策',
  summary: '摘要',
};

const SYNC_LABEL: Record<string, string> = {
  synced: '已同步',
  pending: '同步中',
  failed: '同步失败',
};

const formatDate = (iso?: string) => (iso ? new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—');

export default function MemoryList({
  memories,
  onEdit,
  onDelete,
  onRetain,
}: {
  memories: MemoryItem[];
  onEdit: (memory: MemoryItem) => void;
  onDelete: (memory: MemoryItem) => void;
  onRetain: (memory: MemoryItem) => void;
}) {
  if (memories.length === 0) {
    return <Empty description="暂无长期记忆；最近五轮仍作为短期上下文使用" />;
  }
  return (
    <List
      dataSource={memories}
      renderItem={(item) => (
        <List.Item className="memory-row"
          actions={[
            <Button key="edit" type="link" icon={<EditOutlined />} onClick={() => onEdit(item)}>编辑</Button>,
            !item.retained && (
              <Button key="retain" type="link" icon={<PushpinOutlined />} onClick={() => onRetain(item)}>长期保留</Button>
            ),
            <Popconfirm key="delete" title="删除这条记忆？" onConfirm={() => onDelete(item)}>
              <Button danger type="link" icon={<DeleteOutlined />}>删除</Button>
            </Popconfirm>,
          ]}
        >
          <List.Item.Meta
            title={
              <span className="flex flex-wrap items-center gap-1.5">
                <Tag>{TYPE_LABEL[item.memory_type] ?? item.memory_type}</Tag>
                <Tag color={item.source === 'explicit' ? 'blue' : 'default'}>
                  {item.source === 'explicit' ? '显式记忆' : '自动摘要'}
                </Tag>
                <Tag color={item.sync_status === 'failed' ? 'red' : item.sync_status === 'pending' ? 'orange' : 'green'}>
                  {SYNC_LABEL[item.sync_status] ?? item.sync_status}
                </Tag>
                {item.retained && <Tag color="gold">长期保留</Tag>}
              </span>
            }
            description={
              <span>
                <span className="block text-[15px] leading-6 text-ink">{item.content}</span>
                <span className="mt-1 block text-xs text-ink-secondary">
                  来源时间：{formatDate(item.created_at)}
                  {item.expires_at ? ` · 保留至 ${formatDate(item.expires_at)}` : ' · 长期保留'}
                </span>
              </span>
            }
          />
        </List.Item>
      )}
    />
  );
}
