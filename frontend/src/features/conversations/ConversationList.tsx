import { EditOutlined, SearchOutlined, TeamOutlined } from '@ant-design/icons';
import { Avatar, Button, Empty, Input, Tag } from 'antd';
import { useMemo } from 'react';
import type { ConversationSummary, Employee } from '@dwp/shared-schema';

export interface ContactMeta {
  label: string;
  emoji: string;
  color: string;
  bg: string;
}

const formatTime = (iso: string) => {
  const date = new Date(iso);
  const now = new Date();
  return date.toDateString() === now.toDateString()
    ? date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
};

export default function ConversationList({
  conversations,
  twin,
  selectedId,
  keyword,
  onKeywordChange,
  onSelect,
  onOpenTwin,
  onNewChat,
  metaOf,
}: {
  conversations: ConversationSummary[];
  twin: Employee | null;
  selectedId: string | null;
  keyword: string;
  onKeywordChange: (value: string) => void;
  onSelect: (id: string) => void;
  onOpenTwin: () => void;
  onNewChat: () => void;
  metaOf: (employeeNo: string) => ContactMeta;
}) {
  const twinConv = useMemo(
    () =>
      twin
        ? conversations.find(
            (conv) => conv.kind === 'direct' && conv.participants.some((p) => p.employee_no === twin.employee_no),
          )
        : undefined,
    [conversations, twin],
  );
  const visibleConvs = useMemo(() => {
    const others = conversations.filter((conv) => conv.id !== twinConv?.id);
    if (!keyword) return others;
    return others.filter((conv) => {
      const name = conv.kind === 'group' ? conv.title : conv.participants[0]?.name ?? '';
      return name.includes(keyword) || conv.last_message.includes(keyword);
    });
  }, [conversations, twinConv, keyword]);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-line-light px-3 pb-3 pt-4">
        <div className="mb-3 flex items-center justify-between px-1">
          <div>
            <div className="text-base font-semibold text-ink">消息</div>
            <div className="text-[11px] text-ink-tertiary">企业知识与协作</div>
          </div>
          <Button type="text" shape="circle" icon={<EditOutlined />} aria-label="发起会话" onClick={onNewChat} />
        </div>
        <div className="flex items-center gap-2">
          <Input
            allowClear
            prefix={<SearchOutlined className="text-ink-tertiary" />}
            placeholder="搜索会话"
            value={keyword}
            onChange={(e) => onKeywordChange(e.target.value)}
          />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {!keyword && twin && (
          <button
            type="button"
            onClick={() => twinConv ? onSelect(twinConv.id) : onOpenTwin()}
            className={`conversation-row mb-1 flex w-full cursor-pointer items-center gap-3 rounded-container px-2.5 py-2.5 text-left transition-colors ${selectedId === twinConv?.id ? 'active bg-brand-soft' : 'hover:bg-surface-subtle'}`}
          >
            <Avatar size={36} style={{ background: 'var(--dwp-brand-soft)', color: 'var(--dwp-brand)' }}>
              {twin.name.slice(0, 1)}
            </Avatar>
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate text-sm font-medium text-ink">{twin.name}</span>
                {twinConv && <span className="shrink-0 text-xs text-ink-tertiary">{formatTime(twinConv.updated_at)}</span>}
              </div>
              <div className="truncate text-xs text-ink-secondary">
                {twinConv ? twinConv.last_message : '开始和我的分身聊聊吧'}
              </div>
            </div>
            <Tag color="blue" className="!m-0">我的分身</Tag>
          </button>
        )}
        {visibleConvs.length === 0 && (
          <Empty style={{ marginTop: 48 }} description={keyword ? '没有找到相关会话' : '还没有会话，点 + 发起一个吧'} />
        )}
        {visibleConvs.map((conv) => {
          const isGroup = conv.kind === 'group';
          const participant = conv.participants[0];
          const meta = isGroup ? null : metaOf(participant?.employee_no ?? '');
          return (
            <button
              key={conv.id}
              type="button"
              onClick={() => onSelect(conv.id)}
              className={`conversation-row mb-0.5 flex w-full cursor-pointer items-center gap-3 rounded-container px-2.5 py-2.5 text-left transition-colors ${
                selectedId === conv.id ? 'active bg-brand-soft' : 'hover:bg-surface-subtle'
              }`}
            >
              {isGroup ? (
                <Avatar size={36} style={{ background: 'var(--dwp-surface-subtle)', color: 'var(--dwp-text-secondary)' }}>
                  <TeamOutlined />
                </Avatar>
              ) : (
                <Avatar size={36} style={{ background: meta?.bg, color: meta?.color }}>
                  {meta?.emoji}
                </Avatar>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-sm font-medium text-ink">
                    {isGroup ? `${conv.title || '协作空间'}（${conv.participants.length}）` : participant?.name ?? ''}
                  </span>
                  <span className="shrink-0 text-xs text-ink-tertiary">{formatTime(conv.updated_at)}</span>
                </div>
                <div className="truncate text-xs text-ink-secondary">{conv.last_message || '暂无消息'}</div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
