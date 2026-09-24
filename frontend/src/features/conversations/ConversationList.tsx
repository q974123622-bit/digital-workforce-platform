import { DeleteOutlined, LoadingOutlined, SearchOutlined, TeamOutlined } from '@ant-design/icons';
import { Avatar, Button, Empty, Input } from 'antd';
import { useMemo, useState } from 'react';
import type { ConversationSearchHit, ConversationSummary, Employee } from '@dwp/shared-schema';
import AgentAvatar from '../../components/ui/AgentAvatar';
import { serverDate } from '../../utils/serverTime';

export interface ContactMeta {
  label: string;
  emoji: string;
  color: string;
  bg: string;
}

const formatTime = (iso: string) => {
  // 服务端时间戳无时区标识，统一按 UTC 解析，否则会比实际时间早一个时区差。
  const date = serverDate(iso) ?? new Date();
  const now = new Date();
  return date.toDateString() === now.toDateString()
    ? date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
};

/**
 * 二次确认走卡片内联，不用 antd Popconfirm。
 * portal 壳（body overflow:hidden + 固定视口）里 antd 浮层定位不可靠，
 * 内联确认不依赖浮层，任何情况下都能看到并点到。
 */
function DeleteEntry({
  confirming,
  label,
  onRequestConfirm,
  onCancel,
  onConfirm,
}: {
  confirming: boolean;
  label: string;
  onRequestConfirm: () => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (confirming) {
    return (
      <span className="flex shrink-0 items-center gap-1.5" onClick={(event) => event.stopPropagation()}>
        <Button size="small" danger type="primary" aria-label="确认删除" onClick={(event) => { event.stopPropagation(); onConfirm(); }}>
          删除
        </Button>
        <Button size="small" aria-label="取消删除" onClick={(event) => { event.stopPropagation(); onCancel(); }}>
          取消
        </Button>
      </span>
    );
  }
  return (
    <span
      role="button"
      tabIndex={0}
      aria-label={label}
      title="删除会话"
      className="shrink-0 cursor-pointer rounded p-1 text-ink-tertiary opacity-50 transition-opacity hover:bg-line-light hover:text-ink group-hover:opacity-100"
      onClick={(event) => {
        event.stopPropagation();
        onRequestConfirm();
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onRequestConfirm();
        }
      }}
    >
      <DeleteOutlined />
    </span>
  );
}

/** 把片段按关键字切成「普通/命中」两段，命中段交给 <mark> 高亮（不用 innerHTML，避免注入）。 */
function splitByKeyword(text: string, keyword: string) {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return [{ text, hit: false }];
  const parts: { text: string; hit: boolean }[] = [];
  const haystack = text.toLowerCase();
  let cursor = 0;
  while (cursor < text.length) {
    const index = haystack.indexOf(needle, cursor);
    if (index < 0) break;
    if (index > cursor) parts.push({ text: text.slice(cursor, index), hit: false });
    parts.push({ text: text.slice(index, index + needle.length), hit: true });
    cursor = index + needle.length;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), hit: false });
  return parts.length ? parts : [{ text, hit: false }];
}

/**
 * 检索结果行：会话名 + 命中片段（关键字高亮）+ 说话人 + 命中处数。
 * 与普通会话行分开实现，避免改动既有列表渲染（本地降级筛选仍复用普通行）。
 */
function SearchHitRow({
  hit,
  twin,
  employees,
  selected,
  confirming,
  keyword,
  onActivate,
  onOpenProfile,
  onRequestConfirm,
  onCancelConfirm,
  onConfirmDelete,
}: {
  hit: ConversationSearchHit;
  twin: Employee | null;
  employees: Employee[];
  selected: boolean;
  confirming: boolean;
  keyword: string;
  onActivate: () => void;
  onOpenProfile: (employeeNo: string) => void;
  onRequestConfirm: () => void;
  onCancelConfirm: () => void;
  onConfirmDelete: () => void;
}) {
  const conv = hit.conversation;
  const isGroup = conv.kind === 'group';
  const participant = conv.participants[0];
  const employee = employees.find((item) => item.employee_no === participant?.employee_no)
    ?? (twin?.employee_no === participant?.employee_no ? twin : null);
  const title = isGroup ? `${conv.title || '协作空间'}（${conv.participants.length}）` : participant?.name ?? '';
  const speaker = hit.match_role === 'assistant' ? (hit.match_speaker || 'AI员工') : '你';
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onActivate}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onActivate();
        }
      }}
      className={`conversation-row group mb-0.5 flex w-full cursor-pointer items-start gap-3 rounded-container px-2.5 py-2.5 text-left transition-colors ${
        selected ? 'active bg-brand-soft' : 'hover:bg-surface-subtle'
      }`}
    >
      {isGroup ? (
        <Avatar size={36} style={{ background: 'var(--dwp-surface-subtle)', color: 'var(--dwp-text-secondary)' }}>
          <TeamOutlined />
        </Avatar>
      ) : (
        <span
          role="button"
          tabIndex={0}
          className="avatar-button"
          aria-label="查看人物资料"
          onClick={(event) => { event.stopPropagation(); if (participant) onOpenProfile(participant.employee_no); }}
          onKeyDown={(event) => { if (participant && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); event.stopPropagation(); onOpenProfile(participant.employee_no); } }}
        >
          <AgentAvatar employee={employee} name={participant?.name} size={36} />
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-sm font-medium text-ink">{title}</span>
          {!confirming && <span className="shrink-0 text-xs text-ink-tertiary">{formatTime(conv.updated_at)}</span>}
        </div>
        {hit.matched_in === 'name' ? (
          <div className="truncate text-xs text-ink-secondary">{conv.last_message || '暂无消息'}</div>
        ) : (
          <>
            <div className="conversation-snippet mt-0.5 text-xs text-ink-secondary">
              <span className="text-ink-tertiary">{speaker}：</span>
              {splitByKeyword(hit.snippet, keyword).map((part, index) => (
                part.hit
                  ? <mark key={index}>{part.text}</mark>
                  : <span key={index}>{part.text}</span>
              ))}
            </div>
            <div className="mt-0.5 text-[11px] text-ink-tertiary">
              {hit.match_count > 1 ? `共 ${hit.match_count} 处提到` : '1 处提到'}
            </div>
          </>
        )}
      </div>
      <DeleteEntry
        confirming={confirming}
        label="删除会话"
        onRequestConfirm={onRequestConfirm}
        onCancel={onCancelConfirm}
        onConfirm={onConfirmDelete}
      />
    </div>
  );
}

export default function ConversationList({
  conversations,
  twin,
  employees,
  selectedId,
  keyword,
  hits,
  searching,
  searchError,
  onKeywordChange,
  onSelect,
  onSelectHit,
  onOpenTwin,
  onOpenProfile,
  onOpenDirectory,
  onDelete,
}: {
  conversations: ConversationSummary[];
  twin: Employee | null;
  employees: Employee[];
  selectedId: string | null;
  keyword: string;
  /** 后端内容检索结果（含会话名/成员名命中），按会话聚合。 */
  hits: ConversationSearchHit[];
  searching: boolean;
  /** 内容检索接口不可用时的提示；非空则回退到本地「会话名 + 最近一条消息」筛选。 */
  searchError?: string;
  onKeywordChange: (value: string) => void;
  onSelect: (id: string) => void;
  onSelectHit: (hit: ConversationSearchHit) => void;
  onOpenTwin: () => void;
  onOpenProfile: (employeeNo: string) => void;
  /** 检索无结果时去「同事」按姓名找AI员工。 */
  onOpenDirectory: () => void;
  onDelete: (id: string) => void;
}) {
  // 同一时刻只允许一张卡片处于「确认删除」状态；点击卡片其它位置即取消。
  const [confirmId, setConfirmId] = useState<string | null>(null);

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

  // 分段口径：群聊 → 协作空间；分身直聊 → 我的分身；其余直聊 → AI员工。
  // 判定读 participant.employee_type（后端按 DigitalEmployee.type 回填），不依赖 twin prop 是否已加载。
  const grouped = useMemo(() => {
    const twinConvs: ConversationSummary[] = [];
    const employeeConvs: ConversationSummary[] = [];
    const groupConvs: ConversationSummary[] = [];
    for (const conv of visibleConvs) {
      if (conv.kind === 'group') groupConvs.push(conv);
      else if (conv.participants[0]?.employee_type === 'twin') twinConvs.push(conv);
      else employeeConvs.push(conv);
    }
    return { twinConvs, employeeConvs, groupConvs };
  }, [visibleConvs]);

  const activate = (id: string) => {
    if (confirmId === id) {
      setConfirmId(null);
      return;
    }
    setConfirmId(null);
    onSelect(id);
  };

  // 内容检索不可用时退回本地「会话名 + 最近一条消息」筛选（可见结果不空白）。
  const fallbackMode = Boolean(keyword) && Boolean(searchError);
  // 检索命中态走独立的平铺列表（按命中聚合），分段只作用于普通列表。
  const listMode = !keyword || fallbackMode;
  // 分身段首行是「置顶的分身入口」，无搜索词时才出现，计数要与实际展示一致。
  const twinRowCount = grouped.twinConvs.length + (listMode && !keyword && twin ? 1 : 0);
  const hasTwinSection = listMode && twinRowCount > 0;
  const hasAnyRow = hasTwinSection || grouped.employeeConvs.length > 0 || grouped.groupConvs.length > 0;

  const renderRow = (conv: ConversationSummary) => {
    const isGroup = conv.kind === 'group';
    const participant = conv.participants[0];
    const employee = employees.find((item) => item.employee_no === participant?.employee_no) ?? (twin?.employee_no === participant?.employee_no ? twin : null);
    return (
      <div
        key={conv.id}
        role="button"
        tabIndex={0}
        onClick={() => activate(conv.id)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            activate(conv.id);
          }
        }}
        className={`conversation-row group mb-0.5 flex w-full cursor-pointer items-center gap-3 rounded-container px-2.5 py-2.5 text-left transition-colors ${
          selectedId === conv.id ? 'active bg-brand-soft' : 'hover:bg-surface-subtle'
        }`}
      >
        {isGroup ? (
          <Avatar size={36} style={{ background: 'var(--dwp-surface-subtle)', color: 'var(--dwp-text-secondary)' }}>
            <TeamOutlined />
          </Avatar>
        ) : (
          <span role="button" tabIndex={0} className="avatar-button" aria-label="查看人物资料" onClick={(event) => { event.stopPropagation(); if (participant) onOpenProfile(participant.employee_no); }} onKeyDown={(event) => { if (participant && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); event.stopPropagation(); onOpenProfile(participant.employee_no); } }}>
            <AgentAvatar employee={employee} name={participant?.name} size={36} />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate text-sm font-medium text-ink">
              {isGroup ? `${conv.title || '协作空间'}（${conv.participants.length}）` : participant?.name ?? ''}
            </span>
            {confirmId !== conv.id && <span className="shrink-0 text-xs text-ink-tertiary">{formatTime(conv.updated_at)}</span>}
          </div>
          <div className="truncate text-xs text-ink-secondary">
            {confirmId === conv.id ? '确认删除该会话？消息、任务与执行记录将永久删除' : (conv.last_message || '暂无消息')}
          </div>
        </div>
        <DeleteEntry
          confirming={confirmId === conv.id}
          label="删除会话"
          onRequestConfirm={() => setConfirmId(conv.id)}
          onCancel={() => setConfirmId(null)}
          onConfirm={() => { setConfirmId(null); onDelete(conv.id); }}
        />
      </div>
    );
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-line-light px-3 pb-3 pt-3">
        <Input
          allowClear
          prefix={<SearchOutlined className="text-ink-tertiary" />}
          suffix={searching ? <LoadingOutlined className="text-ink-tertiary" /> : undefined}
          placeholder="搜索会话"
          value={keyword}
          onChange={(e) => onKeywordChange(e.target.value)}
        />
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {listMode && fallbackMode && (
          <div className="mb-2 rounded-control border border-line-light bg-warning-soft px-2.5 py-2 text-xs text-ink-secondary">
            内容检索暂不可用，已按会话名筛选
          </div>
        )}
        {listMode && !hasAnyRow && (
          <Empty style={{ marginTop: 48 }} description={keyword ? '没有找到相关会话' : '还没有会话'} />
        )}
        {hasTwinSection && (
          <section className="list-section list-section--twin" aria-label="我的分身">
            <div className="list-section-head" role="heading" aria-level={3}>
              <span>我的分身</span>
              <b>{twinRowCount}</b>
            </div>
            <div className="list-section-body">
              {!keyword && twin && (
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => { if (twinConv) activate(twinConv.id); else onOpenTwin(); }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      if (twinConv) {
                        activate(twinConv.id);
                      } else {
                        onOpenTwin();
                      }
                    }
                  }}
                  className={`conversation-row group mb-0.5 flex w-full cursor-pointer items-center gap-3 rounded-container px-2.5 py-2.5 text-left transition-colors ${selectedId === twinConv?.id ? 'active bg-brand-soft' : 'hover:bg-surface-subtle'}`}
                >
                  <span role="button" tabIndex={0} className="avatar-button" aria-label="查看人物资料" onClick={(event) => { event.stopPropagation(); onOpenProfile(twin.employee_no); }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); onOpenProfile(twin.employee_no); } }}>
                    <AgentAvatar employee={twin} size={36} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-sm font-medium text-ink">{twin.name}</span>
                      {twinConv && <span className="shrink-0 text-xs text-ink-tertiary">{formatTime(twinConv.updated_at)}</span>}
                    </div>
                    <div className="truncate text-xs text-ink-secondary">
                      {twinConv ? twinConv.last_message : '开始和我的分身聊聊吧'}
                    </div>
                  </div>
                  {twinConv && (
                    <DeleteEntry
                      confirming={confirmId === twinConv.id}
                      label="删除分身会话"
                      onRequestConfirm={() => setConfirmId(twinConv.id)}
                      onCancel={() => setConfirmId(null)}
                      onConfirm={() => { setConfirmId(null); onDelete(twinConv.id); }}
                    />
                  )}
                </div>
              )}
              {grouped.twinConvs.map(renderRow)}
            </div>
          </section>
        )}
        {listMode && grouped.employeeConvs.length > 0 && (
          <section className="list-section list-section--employees" aria-label="AI员工">
            <div className="list-section-head" role="heading" aria-level={3}>
              <span>AI员工</span>
              <b>{grouped.employeeConvs.length}</b>
            </div>
            <div className="list-section-body">{grouped.employeeConvs.map(renderRow)}</div>
          </section>
        )}
        {listMode && grouped.groupConvs.length > 0 && (
          <section className="list-section list-section--groups" aria-label="协作空间">
            <div className="list-section-head" role="heading" aria-level={3}>
              <span>协作空间</span>
              <b>{grouped.groupConvs.length}</b>
            </div>
            <div className="list-section-body">{grouped.groupConvs.map(renderRow)}</div>
          </section>
        )}
        {keyword && !fallbackMode && hits.length === 0 && !searching && (
          <>
            <Empty style={{ marginTop: 48 }} description="没有找到相关会话" />
            <div className="px-2 text-center">
              <Button type="link" size="small" onClick={onOpenDirectory}>
                去「同事」按姓名找AI员工
              </Button>
            </div>
          </>
        )}
        {keyword && !fallbackMode && hits.map((hit) => (
          <SearchHitRow
            key={hit.conversation.id}
            hit={hit}
            twin={twin}
            employees={employees}
            selected={selectedId === hit.conversation.id}
            confirming={confirmId === hit.conversation.id}
            keyword={keyword}
            onActivate={() => {
              if (confirmId === hit.conversation.id) {
                setConfirmId(null);
                return;
              }
              setConfirmId(null);
              onSelectHit(hit);
            }}
            onOpenProfile={onOpenProfile}
            onRequestConfirm={() => setConfirmId(hit.conversation.id)}
            onCancelConfirm={() => setConfirmId(null)}
            onConfirmDelete={() => { setConfirmId(null); onDelete(hit.conversation.id); }}
          />
        ))}
      </div>
    </div>
  );
}
