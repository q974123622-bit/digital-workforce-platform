import { Button, Input, Space } from 'antd';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { Conversation, ConversationAttachment, ConversationMessage, Employee } from '@dwp/shared-schema';
import { EditOutlined, FileTextOutlined, HistoryOutlined } from '@ant-design/icons';
import MarkdownText from '../../components/MarkdownText';
import CitationList from '../knowledge/CitationList';
import type { KnowledgeSourceFallback } from '../knowledge/CitationList';
import AgentAvatar from '../../components/ui/AgentAvatar';
import { EMPLOYEE_TYPE_LABEL } from '../../components/tags';
import { copyText } from '../../utils/clipboard';
import { attachmentThumbnailUrl, previewKindOf } from './attachmentPreview';

/**
 * 消息列表：单独滚动、自动滚动仅在贴近底部时跟随，上滚后显示「回到底部」。
 * 长答案限制阅读宽度约 760px。
 */
export default function MessageList({
  conversation,
  streamingLength,
  focusMessage = null,
  renderMessageExtra,
  emptyState,
  employeeOf,
  onOpenProfile,
  attachmentOf,
  onOpenAttachment,
  onPreviewAttachment,
  knowledgeFallbackOf,
  editableSeq = null,
  editingSeq = null,
  editDraft = '',
  editSaving = false,
  onStartEdit,
  onEditDraftChange,
  onSubmitEdit,
  onCancelEdit,
  canRegenerate = false,
  onRegenerate,
}: {
  conversation: Conversation;
  streamingLength: number;
  /**
   * 检索命中定位：把 seq 对应的消息滚到视口中间并短暂高亮。
   * nonce 让「重复点击同一条命中」也能重新定位（只比 seq 不会触发 effect 重跑）。
   */
  focusMessage?: { seq: number; nonce: number } | null;
  renderMessageExtra: (msg: ConversationMessage) => ReactNode;
  emptyState: ReactNode;
  employeeOf: (employeeNo: string) => Employee | null;
  onOpenProfile: (employeeNo: string) => void;
  attachmentOf: (attachmentId: string) => ConversationAttachment | undefined;
  onOpenAttachment: (attachmentId: string) => void;
  /**
   * 点击图片/PDF 缩略图时打开预览灯箱。未传时回落到 `onOpenAttachment`，
   * 保证既有调用方与测试零改动。
   */
  onPreviewAttachment?: (attachmentId: string) => void;
  /**
   * 本轮执行事件里的知识检索来源（含委派）：工具卡片缺失时兜底，
   * 保证「只要本轮查过知识库就一定有来源展示」。
   */
  knowledgeFallbackOf?: (msg: ConversationMessage) => KnowledgeSourceFallback[];
  /** 唯一允许就地编辑的那条用户消息（最新一轮提问），null 表示不可编辑。 */
  editableSeq?: number | null;
  editingSeq?: number | null;
  editDraft?: string;
  editSaving?: boolean;
  onStartEdit?: (msg: ConversationMessage) => void;
  onEditDraftChange?: (value: string) => void;
  onSubmitEdit?: () => void;
  onCancelEdit?: () => void;
  /**
   * 是否允许「重新生成」。由父级用既有口径计算：最新一条用户消息 && 当前没有执行在跑。
   * 运行中会与正在跑的这轮抢同一 seq（后端同会话串行，否则 409），所以为 false 时不挂按钮。
   */
  canRegenerate?: boolean;
  /** 点击「重新生成」时上报的意图；父级用最新一条用户消息的 seq + content 调 rerunConversationMessage。 */
  onRegenerate?: (msg: ConversationMessage) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // 命中定位用的行节点表：seq -> 行 DOM（消息被清空时同步移除）。
  const rowRefs = useRef(new Map<number, HTMLDivElement>());
  const [focusedSeq, setFocusedSeq] = useState<number | null>(null);
  const [scrolledUp, setScrolledUp] = useState(false);
  // 被重跑取代的旧回复默认折叠：展开状态放在组件内本地 state，
  // portal 壳里不用 antd 浮层（定位不可靠）。
  const [expandedSuperseded, setExpandedSuperseded] = useState<number[]>([]);
  // 「已复制」原地回显：只可能同时存在一条，用消息 id 记录当前回显项。
  const [copiedMsgId, setCopiedMsgId] = useState<number | null>(null);
  const copyTimer = useRef<number | null>(null);
  // 缩略图加载失败（文件缺失/损坏）的附件：退回原来的「图标 + 文件名」卡片，不整块崩。
  const [failedThumbs, setFailedThumbs] = useState<string[]>([]);

  useEffect(() => () => {
    if (copyTimer.current !== null) window.clearTimeout(copyTimer.current);
  }, []);

  const handleCopy = (msg: ConversationMessage) => {
    void copyText(msg.content).then((ok) => {
      if (!ok) return;
      setCopiedMsgId(msg.id);
      if (copyTimer.current !== null) window.clearTimeout(copyTimer.current);
      copyTimer.current = window.setTimeout(() => setCopiedMsgId(null), 1500);
    });
  };

  // 「重新生成」只对最新一轮有意义（后端只允许重跑最新一轮，旧回复会被软作废）。
  // 取最后一条未作废的AI员工回复：否则历史回复上都会冒出「重新生成」按钮。
  const lastAssistantId = [...conversation.messages]
    .reverse()
    .find((item) => item.role === 'assistant' && !item.superseded_at)?.id ?? null;

  // 检索命中定位：滚到目标消息并短暂高亮。
  // 依赖 messages.length 是因为命中时会话可能还在加载（首次渲染拿不到行节点）。
  useEffect(() => {
    if (!focusMessage) return;
    const node = rowRefs.current.get(focusMessage.seq);
    if (!node) return;
    const frame = requestAnimationFrame(() => {
      // jsdom 未实现 scrollIntoView，测试环境下跳过滚动即可。
      if (typeof node.scrollIntoView === 'function') node.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
    setFocusedSeq(focusMessage.seq);
    const timer = window.setTimeout(() => {
      setFocusedSeq((current) => (current === focusMessage.seq ? null : current));
    }, 2400);
    return () => { cancelAnimationFrame(frame); window.clearTimeout(timer); };
  }, [focusMessage, conversation.id, conversation.messages.length]);

  useEffect(() => {
    // 命中定位期间不自动滚到底部，否则刚滚到的命中消息会被立刻拉走。
    if (scrolledUp || focusMessage) return;
    const frame = requestAnimationFrame(() => {
      const node = scrollRef.current;
      if (node) node.scrollTop = node.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [conversation.messages.length, streamingLength, scrolledUp, focusMessage]);

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
          // 被同轮新回答取代的旧回复：默认只留一行入口，点开才看内容。
          const superseded = !mine && Boolean(msg.superseded_at);
          const supersededOpen = superseded && expandedSuperseded.includes(msg.id);
          // 说话人类型：头像与徽章共用同一次查找。类型缺失（如历史消息里的已删员工）时不渲染徽章。
          const speakerEmployee = mine ? null : employeeOf(msg.participant_no);
          const speakerType = speakerEmployee?.type ?? '';
          return (
            <div
              key={msg.id}
              ref={(node) => {
                if (node) rowRefs.current.set(msg.seq, node);
                else rowRefs.current.delete(msg.seq);
              }}
              className={`message-row mx-auto mb-5 w-full max-w-[960px]${focusedSeq === msg.seq ? ' message-row--focus' : ''}`}
            >
              <div className={`flex gap-3 ${mine ? 'justify-end' : 'justify-start'}`}>
                {!mine && (
                  <button type="button" className="avatar-button mt-0.5 shrink-0" aria-label={`查看${msg.participant_name}的资料`} onClick={() => onOpenProfile(msg.participant_no)}>
                    <AgentAvatar employee={speakerEmployee} name={msg.participant_name} size={36} />
                  </button>
                )}
                <div className={`max-w-[760px] ${mine ? 'items-end' : 'items-start'}`}>
                  {!mine && (
                    <div className="mb-1 flex items-center gap-1.5">
                      <span className="text-xs font-medium text-ink-secondary">{msg.participant_name}</span>
                      {speakerType && (
                        <span className={`type-badge type-badge--${speakerType}`}>
                          {EMPLOYEE_TYPE_LABEL[speakerType] ?? speakerType}
                        </span>
                      )}
                    </div>
                  )}
                  {(msg.attachment_ids ?? []).length > 0 && (
                    <div className={`mb-2 flex flex-wrap gap-2 ${mine ? 'justify-end' : 'justify-start'}`}>
                      {(msg.attachment_ids ?? []).map((attachmentId) => {
                        const attachment = attachmentOf(attachmentId);
                        const thumbable = Boolean(attachment)
                          && previewKindOf(attachment) !== 'none'
                          && !failedThumbs.includes(attachmentId);
                        if (thumbable && attachment) {
                          return (
                            <button
                              key={attachmentId}
                              type="button"
                              title={attachment.filename}
                              aria-label={`预览 ${attachment.filename}`}
                              className="h-[72px] w-[72px] shrink-0 overflow-hidden rounded-control border border-line bg-surface-subtle transition-colors hover:border-brand"
                              onClick={() => (onPreviewAttachment ?? onOpenAttachment)(attachmentId)}
                            >
                              <img
                                src={attachmentThumbnailUrl(conversation.id, attachmentId)}
                                alt={attachment.filename}
                                loading="lazy"
                                className="h-full w-full object-cover"
                                onError={() => setFailedThumbs((current) => (
                                  current.includes(attachmentId) ? current : [...current, attachmentId]
                                ))}
                              />
                            </button>
                          );
                        }
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
                  {mine && editableSeq === msg.seq && editingSeq === msg.seq ? (
                    /* 编辑框走原位内联，不用 Modal/Popover：portal 壳里浮层定位不可靠。 */
                    <div className="w-full rounded-bubble border border-line bg-surface px-3 py-2.5">
                      <Input.TextArea
                        autoFocus
                        value={editDraft}
                        autoSize={{ minRows: 2, maxRows: 10 }}
                        onChange={(event) => onEditDraftChange?.(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Escape') { event.preventDefault(); onCancelEdit?.(); return; }
                          if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); onSubmitEdit?.(); }
                        }}
                      />
                      <div className="mt-2 flex items-center justify-between gap-2">
                        <span className="text-xs text-ink-tertiary">保存后会重新执行本轮任务</span>
                        <Space size={6}>
                          <Button
                            size="small"
                            type="primary"
                            loading={editSaving}
                            disabled={!editDraft.trim()}
                            onClick={() => onSubmitEdit?.()}
                          >
                            保存并重跑
                          </Button>
                          <Button size="small" disabled={editSaving} onClick={() => onCancelEdit?.()}>取消</Button>
                        </Space>
                      </div>
                    </div>
                  ) : superseded ? (
                    <div className="w-full">
                      <button
                        type="button"
                        className="flex items-center gap-1.5 rounded-control border border-dashed border-line bg-surface-subtle px-3 py-1.5 text-xs text-ink-tertiary transition-colors hover:text-ink"
                        onClick={() => setExpandedSuperseded((current) => current.includes(msg.id)
                          ? current.filter((id) => id !== msg.id)
                          : [...current, msg.id])}
                      >
                        <HistoryOutlined />
                        {supersededOpen ? '收起上一版回复' : '上一版回复（已被新回答取代）'}
                      </button>
                      {supersededOpen && (
                        <div className="md-body mt-1.5 rounded-bubble border border-dashed border-line bg-surface px-4 py-3 text-[15px] leading-[1.75] text-ink-secondary">
                          <MarkdownText text={msg.content} />
                        </div>
                      )}
                    </div>
                  ) : (
                    <div
                      className={`message-bubble rounded-bubble px-4 py-3 text-[15px] leading-[1.75] ${
                        mine
                          ? 'bg-brand text-[var(--dwp-user-bubble-text)]'
                          : 'border border-line-light bg-surface text-ink'
                      }`}
                    >
                      <MarkdownText text={msg.content} />
                    </div>
                  )}
                  {mine && editableSeq === msg.seq && editingSeq !== msg.seq && (
                    <div className="mt-1 flex justify-end">
                      <Button
                        type="text"
                        size="small"
                        className="!h-6 !px-1.5 text-xs text-ink-tertiary"
                        icon={<EditOutlined />}
                        onClick={() => onStartEdit?.(msg)}
                      >
                        编辑
                      </Button>
                    </div>
                  )}
                  {/* 操作条：显隐由 CSS `:hover`/`:focus-within` 驱动（见 product.css），
                      不用 React state，避免流式输出期间每行多一次 render。 */}
                  {!mine && !superseded && (
                    <div className="message-actions">
                      <Button type="text" size="small" onClick={() => handleCopy(msg)}>
                        {copiedMsgId === msg.id ? '已复制' : '复制'}
                      </Button>
                      {canRegenerate && msg.id === lastAssistantId && (
                        <Button type="text" size="small" onClick={() => onRegenerate?.(msg)}>重新生成</Button>
                      )}
                    </div>
                  )}
                  {!mine && (!superseded || supersededOpen) && (
                    <CitationList cards={msg.tool_cards} fallback={knowledgeFallbackOf?.(msg) ?? []} />
                  )}
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
