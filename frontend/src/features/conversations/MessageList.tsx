import { Button } from 'antd';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { Conversation, ConversationAttachment, ConversationMessage, Employee } from '@dwp/shared-schema';
import { FileTextOutlined } from '@ant-design/icons';
import MarkdownText from '../../components/MarkdownText';
import CitationList from '../knowledge/CitationList';
import AgentAvatar from '../../components/ui/AgentAvatar';

/**
 * 消息列表：单独滚动、自动滚动仅在贴近底部时跟随，上滚后显示「回到底部」。
 * 长答案限制阅读宽度约 760px。
 */
export default function MessageList({
  conversation,
  streamingLength,
  renderMessageExtra,
  emptyState,
  employeeOf,
  onOpenProfile,
  attachmentOf,
  onOpenAttachment,
}: {
  conversation: Conversation;
  streamingLength: number;
  renderMessageExtra: (msg: ConversationMessage) => ReactNode;
  emptyState: ReactNode;
  employeeOf: (employeeNo: string) => Employee | null;
  onOpenProfile: (employeeNo: string) => void;
  attachmentOf: (attachmentId: string) => ConversationAttachment | undefined;
  onOpenAttachment: (attachmentId: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrolledUp, setScrolledUp] = useState(false);

  useEffect(() => {
    if (scrolledUp) return;
    const frame = requestAnimationFrame(() => {
      const node = scrollRef.current;
      if (node) node.scrollTop = node.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [conversation.messages.length, streamingLength, scrolledUp]);

  const scrollToBottom = () => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
    setScrolledUp(false);
  };

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={scrollRef}
        className="message-canvas h-full overflow-y-auto px-4 py-5 sm:px-6"
        onScroll={(e) => {
          const node = e.currentTarget;
          setScrolledUp(node.scrollHeight - node.scrollTop - node.clientHeight > 80);
        }}
      >
        {conversation.messages.length === 0 && emptyState}
        {conversation.messages.map((msg) => {
          const mine = msg.role === 'user';
          return (
            <div key={msg.id} className="message-row mx-auto mb-5 w-full max-w-[960px]">
              <div className={`flex gap-3 ${mine ? 'justify-end' : 'justify-start'}`}>
                {!mine && (
                  <button type="button" className="avatar-button mt-0.5 shrink-0" aria-label={`查看${msg.participant_name}的资料`} onClick={() => onOpenProfile(msg.participant_no)}>
                    <AgentAvatar employee={employeeOf(msg.participant_no)} name={msg.participant_name} size={36} />
                  </button>
                )}
                <div className={`max-w-[760px] ${mine ? 'items-end' : 'items-start'}`}>
                  {!mine && <div className="mb-1 text-xs font-medium text-ink-secondary">{msg.participant_name}</div>}
                  {(msg.attachment_ids ?? []).length > 0 && (
                    <div className={`mb-2 flex flex-wrap gap-2 ${mine ? 'justify-end' : 'justify-start'}`}>
                      {(msg.attachment_ids ?? []).map((attachmentId) => {
                        const attachment = attachmentOf(attachmentId);
                        return (
                          <button
                            key={attachmentId}
                            type="button"
                            className="flex max-w-[300px] items-center gap-2 rounded-control border border-line bg-surface px-3 py-2 text-left transition-colors hover:border-brand"
                            onClick={() => onOpenAttachment(attachmentId)}
                          >
                            <FileTextOutlined className="shrink-0 text-brand" />
                            <span className="min-w-0">
                              <span className="block truncate text-xs font-medium text-ink">{attachment?.filename ?? '文件已删除'}</span>
                              <span className="block text-[10px] text-ink-tertiary">{attachment ? attachment.file_type.toUpperCase() : attachmentId}</span>
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                  <div
                    className={`message-bubble rounded-bubble px-4 py-3 text-[15px] leading-[1.75] ${
                      mine
                        ? 'bg-brand text-[var(--dwp-user-bubble-text)]'
                        : 'border border-line-light bg-surface text-ink'
                    }`}
                  >
                    <MarkdownText text={msg.content} />
                  </div>
                  {!mine && msg.tool_cards.length > 0 && <CitationList cards={msg.tool_cards} />}
                </div>
              </div>
              <div className="mx-auto w-full max-w-[960px]">{renderMessageExtra(msg)}</div>
            </div>
          );
        })}
      </div>
      {scrolledUp && (
        <Button className="absolute bottom-3 left-1/2 -translate-x-1/2" size="small" onClick={scrollToBottom}>
          回到底部
        </Button>
      )}
    </div>
  );
}
