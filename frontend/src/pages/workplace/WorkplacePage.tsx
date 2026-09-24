import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { ReactNode } from 'react';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CommentOutlined,
  DeleteOutlined,
  DownOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  LeftOutlined,
  RobotOutlined,
  StopOutlined,
  TeamOutlined,
  UpOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import {
  Alert,
  Avatar,
  Button,
  Checkbox,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd';
import type {
  Conversation,
  ConversationAttachment,
  ConversationSearchHit,
  AgentExecution,
  AgentExecutionDetail,
  ConversationSummary,
  EffectiveCapabilities,
  Employee,
  Skill,
  TaskRun,
  WorkplaceHome,
} from '@dwp/shared-schema';
import { api } from '../../api/client';
import type { ToolInputRequest } from '../../api/client';
import { ErrorState, LoadingState } from '../../components/PageState';
import { useCurrentUser } from '../../context/CurrentUserContext';
import { useAsyncData } from '../../hooks/useAsyncData';
import ConversationList from '../../features/conversations/ConversationList';
import type { ContactMeta } from '../../features/conversations/ConversationList';
import Composer from '../../features/conversations/Composer';
import MessageList from '../../features/conversations/MessageList';
import BrandMark from '../../components/ui/BrandMark';
import ExecutionSummary from '../../features/executions/ExecutionSummary';
import ExecutionDetails from '../../features/executions/ExecutionDetails';
import type { ExecutionStep } from '../../features/executions/ExecutionDetails';
import InputRequestCard from '../../features/executions/InputRequestCard';
import CitationPreview from '../../features/knowledge/CitationPreview';
import type { KnowledgeSourceFallback } from '../../features/knowledge/CitationList';
import ColleagueList from '../../features/agents/ColleagueList';
import AgentAvatar from '../../components/ui/AgentAvatar';
import { EMPLOYEE_TYPE_LABEL } from '../../components/tags';
import { formatServerTime, parseServerTime } from '../../utils/serverTime';

const { Text, Paragraph } = Typography;

const GROUP_META: Record<string, ContactMeta> = {
  twin: { label: EMPLOYEE_TYPE_LABEL.twin, emoji: '分', color: 'var(--dwp-brand)', bg: 'var(--dwp-brand-soft)' },
  virtual: { label: EMPLOYEE_TYPE_LABEL.virtual, emoji: '员', color: 'var(--dwp-brand)', bg: 'var(--dwp-brand-soft)' },
  rpa: { label: EMPLOYEE_TYPE_LABEL.rpa, emoji: '执', color: 'var(--dwp-warning)', bg: 'var(--dwp-warning-soft)' },
  group: { label: EMPLOYEE_TYPE_LABEL.group, emoji: '协', color: 'var(--dwp-text-secondary)', bg: 'var(--dwp-surface-subtle)' },
};

const TASK_STATUS: Record<string, { label: string; color: string; icon: ReactNode }> = {
  pending: { label: '待执行', color: 'default', icon: <ClockCircleOutlined /> },
  running: { label: '执行中', color: 'processing', icon: <ExperimentOutlined /> },
  approval: { label: '待审批', color: 'warning', icon: <ClockCircleOutlined /> },
  completed: { label: '已完成', color: 'success', icon: <CheckCircleOutlined /> },
  denied: { label: '已拒绝', color: 'error', icon: <StopOutlined /> },
  failed: { label: '失败', color: 'error', icon: <StopOutlined /> },
};

interface LiveExecution {
  id: string;
  triggerSeq: number;
  employeeId: string;
  status: AgentExecution['status'];
  /** 开始/结束时间一律用服务端时间戳解析后的 epoch，避免与服务端事件时间混算时差。 */
  startedAt: number;
  finishedAt?: number;
  expanded: boolean;
  steps: ExecutionStep[];
  answers: Record<string, string>;
  error?: string;
  retryable?: boolean;
  resumeAfter?: string;
}

const executionIsActive = (status: AgentExecution['status']) =>
  ['queued', 'running', 'streaming', 'waiting_approval'].includes(status);

function restoreExecution(detail: AgentExecutionDetail): LiveExecution {
  const { execution: run, events } = detail;
  const active = executionIsActive(run.status);
  const startedAt = parseServerTime(run.started_at) ?? Date.now();
  const stepEvents = events.filter(
    (event) => !['answer_chunk', 'answer_done', 'error'].includes(event.event_type) && event.title,
  );
  // 每步耗时在渲染层按相邻步骤的开始时间差计算，这里只保留开始时间，
  // 这样「进行中」的最后一步可以实时增长，而不是等下一个事件才跳一次。
  const steps: ExecutionStep[] = stepEvents.map((event) => ({
    id: String(event.event_seq),
    title: event.title,
    detail: event.detail,
    status: event.status,
    startedAt: parseServerTime(event.created_at) ?? startedAt,
    knowledgeBaseId: event.knowledge_base_id ?? undefined,
    knowledgeBaseName: typeof event.payload.knowledge_base_name === 'string' ? event.payload.knowledge_base_name : undefined,
    actorEmployeeId: event.actor_employee_id || undefined,
    targetAgentId: event.target_agent_id ?? undefined,
    hitCount: event.hit_count ?? undefined,
  }));
  const answers: Record<string, string> = {};
  if (active) {
    events.filter((event) => event.event_type === 'answer_chunk').forEach((event) => {
      const text = typeof event.payload.text === 'string' ? event.payload.text : '';
      answers[event.actor_employee_id] = (answers[event.actor_employee_id] ?? '') + text;
    });
  }
  const failure = [...events].reverse().find((event) => event.event_type === 'error');
  const lastEvent = events.length ? events[events.length - 1] : undefined;
  return {
    id: run.id,
    triggerSeq: run.trigger_message_seq,
    employeeId: run.primary_employee_id,
    status: run.status,
    startedAt,
    finishedAt: parseServerTime(run.completed_at),
    // 默认折叠：摘要副标题已能体现当前进行到哪一步，完整轨迹按需展开。
    expanded: run.status === 'failed',
    steps,
    answers,
    error: failure?.detail || run.error_message || undefined,
    // 已停止的执行也允许重试：原消息虽在会话里，但输入框内容已清空，给个一键重跑更省事。
    retryable: active ? run.retryable : run.status === 'cancelled',
    resumeAfter: active && lastEvent ? String(lastEvent.event_seq) : undefined,
  };
}

function TaskCard({ task, workerName, metaOf, onApprove, acting, onOpenProfile }: {
  task: TaskRun;
  workerName: (employeeNo: string) => string;
  metaOf: (employeeNo: string) => ContactMeta;
  onApprove: (task: TaskRun, approve: boolean) => void;
  acting: boolean;
  onOpenProfile?: (employeeNo: string) => void;
}) {
  const [expanded, setExpanded] = useState(task.status !== 'completed');
  const meta = TASK_STATUS[task.status] ?? TASK_STATUS.pending;
  return (
    <div className="mt-2 rounded-container border border-line bg-surface p-3">
      <div className="flex items-center gap-2">
        <Avatar size={30} style={{ background: 'var(--dwp-brand-soft)', color: 'var(--dwp-brand)' }} icon={<RobotOutlined />} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-ink">{task.request}</div>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {task.id} · 内置协作 · 全程留痕
          </Text>
        </div>
        <Space size={4}>
          <Tag icon={meta.icon} color={meta.color}>{meta.label}</Tag>
          <Button
            type="text" size="small"
            aria-label={expanded ? '收起任务详情' : '展开任务详情'}
            icon={expanded ? <UpOutlined /> : <DownOutlined />}
            onClick={() => setExpanded((v) => !v)}
          />
        </Space>
      </div>
      {expanded && (
        <div className="mt-2 space-y-2">
          {task.subtasks.map((sub, index) => {
            const subMeta = TASK_STATUS[sub.status] ?? TASK_STATUS.pending;
            const workerMeta = metaOf(sub.worker_id);
            return (
              <div key={`${sub.worker_no}-${index}`} className="rounded-control bg-surface-subtle p-2.5">
                <Space size={8}>
                  <span role="button" tabIndex={0} className="avatar-button" aria-label={`查看${workerName(sub.worker_id)}的资料`} onClick={() => onOpenProfile?.(sub.worker_id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onOpenProfile?.(sub.worker_id); }}>
                    <Avatar size={22} style={{ background: workerMeta.bg, color: workerMeta.color, fontSize: 11 }}>{workerMeta.emoji}</Avatar>
                  </span>
                  <Text strong>{workerName(sub.worker_id)}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>{sub.summary}</Text>
                  <Tag icon={subMeta.icon} color={subMeta.color}>{subMeta.label}</Tag>
                </Space>
                {sub.result && <Paragraph type="secondary" style={{ margin: '6px 0 0', fontSize: 12 }}>{sub.result}</Paragraph>}
              </div>
            );
          })}
          {task.status === 'approval' && (
            <Space>
              <Text strong>等待审批：</Text>
              <Button type="primary" size="small" loading={acting} onClick={() => onApprove(task, true)}>批准</Button>
              <Button danger size="small" loading={acting} onClick={() => onApprove(task, false)}>拒绝</Button>
            </Space>
          )}
          {task.status === 'completed' && task.summary && (
            <div className="rounded-control bg-brand-soft p-2.5">
              <Text strong>Leader 汇总</Text>
              <Paragraph style={{ margin: '6px 0 0' }}>{task.summary}</Paragraph>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function WorkplacePage() {
  const { actor } = useCurrentUser();
  const actorNo = actor.employee_no;
  const [searchParams, setSearchParams] = useSearchParams();

  const homeFetcher = useCallback(() => api.getWorkplace(actorNo), [actorNo]);
  const { data: home, loading, error, reload } = useAsyncData<WorkplaceHome>(homeFetcher);

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const refreshConversations = useCallback(() => {
    return api.listConversations(actorNo).then(setConversations);
  }, [actorNo]);

  useEffect(() => { void refreshConversations().catch(() => undefined); }, [refreshConversations]);

  const [keyword, setKeyword] = useState('');
  // 会话检索（内容全文）：唯一权威在后端，前端只做防抖调用与渲染。
  const [searchHits, setSearchHits] = useState<ConversationSearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string>();
  // 命中定位：nonce 保证重复点击同一条命中仍能重新滚动（只比 seq 不会触发 effect 重跑）。
  const [focusMessage, setFocusMessage] = useState<{ seq: number; nonce: number } | null>(null);
  // 检索请求序号：丢弃过期响应，避免慢响应覆盖新结果。
  const searchSeqRef = useRef(0);
  const [selectedId, setSelectedId] = useState<string | null>(() => searchParams.get('conversation'));
  const [sideView, setSideView] = useState<'messages' | 'directory'>(() => searchParams.get('view') === 'directory' ? 'directory' : 'messages');
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [attachments, setAttachments] = useState<ConversationAttachment[]>([]);
  const [attachmentOpenId, setAttachmentOpenId] = useState<string>();
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [pendingAfterSeq, setPendingAfterSeq] = useState<number | null>(null);
  const pendingReply = pendingAfterSeq !== null;
  const [chatError, setChatError] = useState<string>();
  const [acting, setActing] = useState(false);
  const [liveExecution, setLiveExecution] = useState<LiveExecution | null>(null);
  const [savedExecutions, setSavedExecutions] = useState<LiveExecution[]>([]);
  const executionEventIds = useRef(new Set<string>());
  const connectedExecutionId = useRef<string>();
  const sseFailures = useRef(0);
  const [connectionIssue, setConnectionIssue] = useState<string>();
  const [reconnectNonce, setReconnectNonce] = useState(0);
  const [historyMayHaveMore, setHistoryMayHaveMore] = useState(false);
  // 就地编辑最新一轮提问：编辑框与原位内联确认同理，不能用 antd 浮层。
  const [editingSeq, setEditingSeq] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState('');
  const [editSaving, setEditSaving] = useState(false);
  // 同一轮被重跑多次时，更早的执行折叠进「查看上一次执行」。
  const [olderExecutionSeqs, setOlderExecutionSeqs] = useState<number[]>([]);

  const [newChatOpen, setNewChatOpen] = useState(false);
  const [selectedNos, setSelectedNos] = useState<string[]>([]);
  // 移动端：会话列表 / 聊天区一次只显示一个。
  const [mobileView, setMobileView] = useState<'list' | 'chat'>('list');

  const [profileOpen, setProfileOpen] = useState(() => Boolean(searchParams.get('profile')));
  const [citationOpen, setCitationOpen] = useState(false);
  const [capabilityEmployeeNo, setCapabilityEmployeeNo] = useState<string | undefined>(() => searchParams.get('profile') ?? undefined);
  const [effectiveCapabilities, setEffectiveCapabilities] = useState<EffectiveCapabilities>();
  const [capabilityLoading, setCapabilityLoading] = useState(false);
  const [capabilityError, setCapabilityError] = useState<string>();
  const [skillModalOpen, setSkillModalOpen] = useState(false);
  const [skillForm] = Form.useForm();

  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const [memberNos, setMemberNos] = useState<string[]>([]);
  // 清空二次确认走内联按钮：portal 壳里 antd 浮层不可靠（见 ConversationList 注释）。
  const [clearConfirming, setClearConfirming] = useState(false);
  // 技能删除同样走内联确认（Popconfirm 在这个壳里点不到确认按钮）。
  const [skillConfirmingId, setSkillConfirmingId] = useState<string | null>(null);

  // MCP 工具缺参/缺鉴权 → 执行轨迹里的「信息补全」气泡。
  // 数据来自后端 tool_input_request（按会话拉全量，前端按 execution_id 分组渲染）。
  const [inputRequests, setInputRequests] = useState<ToolInputRequest[]>([]);
  const [inputSubmittingId, setInputSubmittingId] = useState<string | null>(null);
  const [inputErrors, setInputErrors] = useState<Record<string, string>>({});
  const [ignoredInputIds, setIgnoredInputIds] = useState<string[]>([]);

  useEffect(() => {
    const nextConversation = searchParams.get('conversation');
    const nextView = searchParams.get('view') === 'directory' ? 'directory' : 'messages';
    const nextProfile = searchParams.get('profile');
    setSelectedId((current) => current === nextConversation ? current : nextConversation);
    setSideView(nextView);
    setCapabilityEmployeeNo(nextProfile ?? undefined);
    setProfileOpen(Boolean(nextProfile));
  }, [searchParams]);

  useEffect(() => {
    setSelectedId(searchParams.get('conversation')); setSelected(null); setInput(''); setPendingAfterSeq(null);
    setLiveExecution(null); setSavedExecutions([]);
  // actorNo 变化代表真实登录身份变化，此时才重置会话状态。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actorNo]);

  useEffect(() => {
    setEditingSeq(null); setEditDraft(''); setOlderExecutionSeqs([]);
    if (!selectedId) { setSelected(null); return; }
    let active = true;
    api.getConversation(selectedId).then((conv) => { if (active) setSelected(conv); }).catch(() => { if (active) setSelected(null); });
    return () => { active = false; };
  }, [selectedId]);

  const refreshAttachments = useCallback(async () => {
    if (!selectedId) { setAttachments([]); return; }
    const rows = await api.listConversationAttachments(selectedId);
    setAttachments(rows);
  }, [selectedId]);

  useEffect(() => {
    void refreshAttachments().catch(() => setAttachments([]));
  }, [refreshAttachments]);

  /** 拉取本会话的「待补全」记录；切换会话、收到 input_required 事件、续跑发起后都要刷。 */
  const refreshInputRequests = useCallback(async () => {
    if (!selectedId) { setInputRequests([]); return; }
    const rows = await api.listConversationInputRequests(selectedId);
    setInputRequests(rows);
  }, [selectedId]);

  useEffect(() => {
    setInputErrors({});
    setIgnoredInputIds([]);
    void refreshInputRequests().catch(() => setInputRequests([]));
  }, [refreshInputRequests]);

  const attachmentProcessing = attachments.some((item) => ['pending', 'processing'].includes(item.status));
  useEffect(() => {
    if (!attachmentProcessing) return;
    const timer = window.setInterval(() => void refreshAttachments().catch(() => undefined), 1000);
    return () => window.clearInterval(timer);
  }, [attachmentProcessing, refreshAttachments]);

  // 页面重开恢复历史执行轨迹与进行中任务。
  useEffect(() => {
    if (!selectedId) { setLiveExecution(null); setSavedExecutions([]); return; }
    let cancelled = false;
    api.getConversationRunHistory(selectedId).then((details) => {
      if (cancelled) return;
      const restored = details.map(restoreExecution);
      const activeRun = [...restored].reverse().find((run) => executionIsActive(run.status)) ?? null;
      connectedExecutionId.current = activeRun?.id;
      executionEventIds.current.clear();
      setLiveExecution(activeRun);
      setSavedExecutions(restored.filter((run) => !executionIsActive(run.status)));
      setHistoryMayHaveMore(details.length === 20);
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId || !liveExecution || ['completed', 'failed', 'cancelled'].includes(liveExecution.status)) return;
    if (connectedExecutionId.current !== liveExecution.id) {
      connectedExecutionId.current = liveExecution.id;
      executionEventIds.current.clear();
    }
    const executionId = liveExecution.id;
    const source = new EventSource(
      api.conversationRunEventUrl(selectedId, liveExecution.id, liveExecution.resumeAfter),
      { withCredentials: true },
    );
    source.onopen = () => { sseFailures.current = 0; setConnectionIssue(undefined); };
    const progress = (event: MessageEvent) => {
      if (event.lastEventId && executionEventIds.current.has(event.lastEventId)) return;
      if (event.lastEventId) executionEventIds.current.add(event.lastEventId);
      const data = JSON.parse(event.data) as Record<string, unknown>;
      // 缺参/缺鉴权：后端已落「待补全」记录，立刻拉一次以便就地弹出补全气泡。
      if (data.event_type === 'input_required') {
        void refreshInputRequests().catch(() => undefined);
      }
      setLiveExecution((current) => {
        if (!current || current.id !== executionId) return current;
        const at = parseServerTime(typeof data.created_at === 'string' ? data.created_at : null) ?? Date.now();
        const step: ExecutionStep = {
          id: event.lastEventId || `${Date.now()}-${current.steps.length}`,
          title: String(data.title ?? '正在执行'),
          detail: String(data.detail ?? ''),
          status: String(data.status ?? 'running'),
          startedAt: at,
          knowledgeBaseId: data.knowledge_base_id ? String(data.knowledge_base_id) : undefined,
          knowledgeBaseName: typeof data.knowledge_base_name === 'string' ? data.knowledge_base_name : undefined,
          actorEmployeeId: data.employee_id ? String(data.employee_id) : undefined,
          targetAgentId: data.target_agent_id ? String(data.target_agent_id) : undefined,
          hitCount: typeof data.hit_count === 'number' ? data.hit_count : undefined,
        };
        // 不强制展开：用户手动收起后，后续事件不应再把卡片弹开。
        // 也不把状态硬写成 running：迟到的 progress 事件（如已停止后仍在飞的工具回调）
        // 一旦把终态翻回运行中，卡片会永远转圈、输入框会一直锁死。
        const nextStatus = String(data.status ?? '') as AgentExecution['status'];
        return {
          ...current,
          status: executionIsActive(nextStatus) ? nextStatus : current.status,
          steps: [...current.steps, step],
          resumeAfter: event.lastEventId || current.resumeAfter,
        };
      });
    };
    const delta = (event: MessageEvent) => {
      if (event.lastEventId && executionEventIds.current.has(event.lastEventId)) return;
      if (event.lastEventId) executionEventIds.current.add(event.lastEventId);
      const data = JSON.parse(event.data) as { employee_id?: string; delta?: string };
      setLiveExecution((current) => {
        if (!current || current.id !== executionId) return current;
        const employeeId = data.employee_id || current.employeeId;
        return { ...current, status: 'streaming', resumeAfter: event.lastEventId || current.resumeAfter, answers: { ...current.answers, [employeeId]: (current.answers[employeeId] ?? '') + (data.delta ?? '') } };
      });
    };
    const done = async () => {
      source.close();
      // 保留用户手动展开的选择，不在完成瞬间强行收起。
      setLiveExecution((current) => current?.id === executionId ? { ...current, status: 'completed', finishedAt: Date.now() } : current);
      try {
        const conversation = await api.getConversation(selectedId);
        setSelected(conversation);
        setLiveExecution((current) => current?.id === executionId ? { ...current, answers: {} } : current);
        setConnectionIssue(undefined);
      } catch {
        setConnectionIssue('答复已生成，但正式消息同步失败；可重新加载会话');
      }
      void refreshConversations().catch(() => undefined);
    };
    // 服务端确认已停止：以服务端时间为准，收敛本地「点了停止但请求还没回来」的中间态。
    const stopped = (event: MessageEvent) => {
      if (event.lastEventId && executionEventIds.current.has(event.lastEventId)) return;
      if (event.lastEventId) executionEventIds.current.add(event.lastEventId);
      source.close();
      let at: number | undefined;
      try {
        const data = JSON.parse(event.data) as Record<string, unknown>;
        at = parseServerTime(typeof data.created_at === 'string' ? data.created_at : null);
      } catch { at = undefined; }
      setLiveExecution((current) => current?.id === executionId
        ? { ...current, status: 'cancelled', finishedAt: at ?? current.finishedAt, retryable: true }
        : current);
      void refreshConversations().catch(() => undefined);
    };
    const failed = (event: Event) => {
      if (event instanceof MessageEvent && event.data) {
        source.close();
        const data = JSON.parse(event.data) as { message?: string; retryable?: boolean };
        setLiveExecution((current) => current?.id === executionId ? { ...current, status: 'failed', expanded: true, finishedAt: Date.now(), error: data.message, retryable: data.retryable } : current);
        return;
      }
      sseFailures.current += 1;
      void api.me().catch(() => undefined);
      if (sseFailures.current >= 3) {
        source.close();
        setConnectionIssue('执行进度连接已中断');
      }
    };
    source.addEventListener('progress', progress);
    source.addEventListener('answer_delta', delta);
    source.addEventListener('answer_done', done);
    source.addEventListener('cancelled', stopped);
    source.addEventListener('error', failed);
    return () => source.close();
  }, [liveExecution?.id, reconnectNonce, refreshConversations, refreshInputRequests, selectedId]);

  useEffect(() => {
    if (!liveExecution || executionIsActive(liveExecution.status)) return;
    setSavedExecutions((current) => [...current.filter((run) => run.id !== liveExecution.id), liveExecution]);
    // 本轮结束后补全气泡的「提交」按钮才可用，此时把最新状态同步一次（含已过期/已提交）。
    void refreshInputRequests().catch(() => undefined);
  }, [liveExecution, refreshInputRequests]);

  const streamedLength = liveExecution ? Object.values(liveExecution.answers).reduce((t, a) => t + a.length, 0) : 0;
  // 有执行在跑：输入区右侧按钮由「发送」换成「停止」（输入框仍可继续打字）。
  const executionRunning = Boolean(liveExecution && executionIsActive(liveExecution.status));

  // 「最新一条用户消息」始终可编辑/可重跑：已有回复也不拦。重跑会把旧回复软作废
  // （superseded_at），模型上下文只保留新答案，界面把旧的折叠成「上一版回复」。
  // 仍在执行中时不给入口，避免和正在跑的这轮抢同一 seq（后端同会话串行，否则 409）。
  // 放在这里（早于下方 early return）是为了让 regenerateMessage 能用 useCallback 声明。
  const lastUserMessage = selected ? selected.messages.filter((m) => m.role === 'user').slice(-1)[0] : undefined;
  const editableSeq = selected && lastUserMessage && !executionRunning ? lastUserMessage.seq : null;

  /**
   * 「重新生成」：与「编辑后重跑」共用同一条后端链路（rerunConversationMessage），
   * 区别只是提问原文不变、触发源从编辑框保存换成气泡上的按钮。
   * 旧回复由后端软作废（superseded_at）并折叠成「上一版回复」，不会追加重复的用户消息。
   */
  const regenerateMessage = useCallback(async () => {
    if (!selectedId || !lastUserMessage || executionRunning) return;
    setSending(true); setChatError(undefined); setFocusMessage(null);
    try {
      const run = await api.rerunConversationMessage(selectedId, lastUserMessage.seq, actorNo, lastUserMessage.content);
      setSelected(run.conversation);
      executionEventIds.current.clear();
      connectedExecutionId.current = run.execution_id;
      const primary = run.conversation.participants[0]?.employee_no ?? '';
      setLiveExecution({ id: run.execution_id, triggerSeq: run.trigger_message_seq, employeeId: primary, status: 'queued', startedAt: Date.now(), expanded: false, steps: [], answers: {} });
      await refreshConversations();
    } catch (err) {
      message.error(`重新生成失败：${(err as Error).message}`);
      setChatError((err as Error).message);
    } finally { setSending(false); }
  }, [selectedId, lastUserMessage, executionRunning, actorNo, refreshConversations]);

  /**
   * 提交缺失信息并自动续跑：后端加密保存凭据（按人）后以「原提问 + 补充说明」开跑新一轮，
   * 前端切到新的 live execution 并继续消费 SSE，用户不需要再发一句话。
   * 上一轮仍在执行时后端回 409（retryable），这里原位提示、气泡保持可重试。
   */
  const submitInputRequest = useCallback(async (request: ToolInputRequest, values: Record<string, string>) => {
    if (!selectedId || !request.execution_id) return;
    setInputSubmittingId(request.id);
    setInputErrors((current) => {
      const next = { ...current };
      delete next[request.id];
      return next;
    });
    try {
      const run = await api.submitRunInput(selectedId, request.execution_id, actorNo, request.id, values);
      setSelected(run.conversation);
      executionEventIds.current.clear();
      connectedExecutionId.current = run.execution_id;
      const primary = run.conversation.participants[0]?.employee_no ?? '';
      setLiveExecution({
        id: run.execution_id, triggerSeq: run.trigger_message_seq, employeeId: primary,
        status: 'queued', startedAt: Date.now(), expanded: false, steps: [], answers: {},
      });
      await refreshInputRequests().catch(() => undefined);
      await refreshConversations().catch(() => undefined);
    } catch (err) {
      setInputErrors((current) => ({ ...current, [request.id]: (err as Error).message }));
    } finally {
      setInputSubmittingId(null);
    }
  }, [selectedId, actorNo, refreshConversations, refreshInputRequests]);

  /**
   * 忽略某条补全请求：后端标记 cancelled（刷新后不再重复弹出），前端同时本地收起。
   * 本地先收起是为了点击立刻有反馈；后端失败也不回滚——用户已经表达了「不看这条」的意图。
   */
  const ignoreInputRequest = useCallback((requestId: string) => {
    setIgnoredInputIds((current) => current.includes(requestId) ? current : [...current, requestId]);
    const target = inputRequests.find((item) => item.id === requestId);
    if (!selectedId || !target?.execution_id) return;
    void api.ignoreRunInput(selectedId, target.execution_id, requestId, actorNo)
      .then(() => refreshInputRequests())
      .catch(() => undefined);
  }, [selectedId, actorNo, inputRequests, refreshInputRequests]);

  /**
   * 会话检索：输入 250ms 防抖后调后端 /conversations/search（覆盖全部历史消息正文 + 会话名 + 成员名）。
   * 失败时置 searchError，由 ConversationList 退回本地「会话名 + 最近一条消息」筛选。
   */
  useEffect(() => {
    const text = keyword.trim();
    if (!text) {
      searchSeqRef.current += 1;
      setSearchHits([]);
      setSearching(false);
      setSearchError(undefined);
      return;
    }
    setSearching(true);
    const timer = window.setTimeout(() => {
      const seq = ++searchSeqRef.current;
      api.searchConversations(actorNo, text)
        .then((rows) => {
          if (seq !== searchSeqRef.current) return;
          setSearchHits(rows);
          setSearchError(undefined);
        })
        .catch((err: Error) => {
          if (seq !== searchSeqRef.current) return;
          setSearchHits([]);
          setSearchError(err.message);
        })
        .finally(() => {
          if (seq === searchSeqRef.current) setSearching(false);
        });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [keyword, actorNo]);

  const hasPollableTask = selected?.tasks.some((task) => ['pending', 'parsing', 'running'].includes(task.status));
  useEffect(() => {
    if ((!hasPollableTask && !pendingReply) || !selectedId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const poll = async () => {
      try {
        const conv = await api.getConversation(selectedId);
        if (cancelled) return;
        failures = 0;
        setConnectionIssue(undefined);
        setSelected(conv);
        const hasMatchingReply = pendingAfterSeq != null && conv.messages.some((m) => m.role === 'assistant' && m.seq > pendingAfterSeq);
        const matchingTask = pendingAfterSeq == null ? undefined : conv.tasks.find((task) => task.trigger_message_seq === pendingAfterSeq);
        const responseAppeared = hasMatchingReply || matchingTask != null;
        if (responseAppeared) setPendingAfterSeq(null);
        await refreshConversations();
        const taskStillRunning = conv.tasks.some((task) => ['pending', 'parsing', 'running'].includes(task.status));
        const replyStillPending = pendingAfterSeq != null && !responseAppeared;
        if (!cancelled && (taskStillRunning || replyStillPending)) timer = setTimeout(poll, 2500);
      } catch {
        failures += 1;
        if (cancelled) return;
        if (failures >= 5) {
          setConnectionIssue('会话状态连接已中断');
          return;
        }
        const delays = [2500, 5000, 10000, 20000, 30000];
        timer = setTimeout(poll, delays[failures - 1]);
      }
    };
    timer = setTimeout(poll, 2500);
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [hasPollableTask, pendingReply, pendingAfterSeq, reconnectNonce, selectedId, refreshConversations]);

  const twin = home?.twin ?? null;
  const employees = home?.available_employees ?? [];
  const skills = home?.skills ?? [];

  useEffect(() => {
    if (!profileOpen || !capabilityEmployeeNo) return;
    let cancelled = false;
    setCapabilityLoading(true); setCapabilityError(undefined);
    api.getEffectiveCapabilities(capabilityEmployeeNo)
      .then((result) => { if (!cancelled) setEffectiveCapabilities(result); })
      .catch((cause: Error) => { if (!cancelled) setCapabilityError(cause.message); })
      .finally(() => { if (!cancelled) setCapabilityLoading(false); });
    return () => { cancelled = true; };
  }, [capabilityEmployeeNo, profileOpen]);

  const metaOf = useCallback((employeeNo: string): ContactMeta => {
    if (twin && employeeNo === twin.employee_no) return GROUP_META.twin;
    const emp = employees.find((e) => e.employee_no === employeeNo);
    if (emp?.type === 'rpa') return GROUP_META.rpa;
    if (emp?.type === 'virtual') return GROUP_META.virtual;
    return { label: employeeNo, emoji: '同', color: 'var(--dwp-text-secondary)', bg: 'var(--dwp-surface-subtle)' };
  }, [twin, employees]);

  const updateLocationState = useCallback((changes: Record<string, string | null>) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      Object.entries(changes).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const openProfile = useCallback((employeeNo: string) => {
    setCapabilityEmployeeNo(employeeNo);
    setProfileOpen(true);
    updateLocationState({ profile: employeeNo });
  }, [updateLocationState]);

  const selectConversation = useCallback((id: string) => {
    setSelectedId(id);
    setSideView('messages');
    setMobileView('chat');
    setClearConfirming(false);
    // 切换会话即取消上一次的命中定位（否则新会话会被滚到不存在的 seq）。
    setFocusMessage(null);
    updateLocationState({ conversation: id, view: 'messages' });
  }, [updateLocationState]);

  /** 点击检索结果：打开会话，并把命中的那条消息交给消息列表滚动定位 + 高亮。 */
  const openSearchHit = useCallback((hit: ConversationSearchHit) => {
    selectConversation(hit.conversation.id);
    setFocusMessage(hit.match_seq === null ? null : { seq: hit.match_seq, nonce: Date.now() });
  }, [selectConversation]);

  const openDirect = async (employeeNo: string) => {
    let existing = conversations.find((conv) => conv.kind === 'direct' && conv.participants.some((p) => p.employee_no === employeeNo));
    try {
      // 首页与会话列表并行加载时，用户可能比列表请求更快点击分身。
      // 创建前再核对一次服务端列表，避免重复会话和偶发空白页。
      if (!existing) {
        const latest = await api.listConversations(actorNo);
        setConversations(latest);
        existing = latest.find((conv) => conv.kind === 'direct' && conv.participants.some((p) => p.employee_no === employeeNo));
      }
      if (existing) { selectConversation(existing.id); return; }
      const conv = await api.createConversation({ actor_no: actorNo, kind: 'direct', participant_employee_nos: [employeeNo] });
      await refreshConversations();
      selectConversation(conv.id);
    } catch (err) { message.error(`发起会话失败：${(err as Error).message}`); }
  };

  const send = async (overrideText?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || sending || (liveExecution && executionIsActive(liveExecution.status)) || !selectedId) return;
    const pendingAttachments = attachments.filter((item) => item.message_seq === null && item.status !== 'deleted');
    if (pendingAttachments.some((item) => item.status !== 'ready')) {
      message.warning('请等待附件解析完成，或移除暂不可用的附件');
      return;
    }
    setInput('');
    setSending(true);
    setChatError(undefined);
    // 发新消息后视口应回到最新消息，取消检索命中定位。
    setFocusMessage(null);
    const attachmentIds = pendingAttachments.map((item) => item.attachment_id);
    setSelected((prev) => {
      if (!prev) return prev;
      const lastSeq = prev.messages.reduce((max, m) => Math.max(max, m.seq), 0);
      return { ...prev, messages: [...prev.messages, { id: -Date.now(), conversation_id: prev.id, participant_no: actorNo, participant_name: actor.name, role: 'user', content: text, tool_cards: [], attachment_ids: attachmentIds, seq: lastSeq + 1 }] };
    });
    try {
      const run = await api.startConversationRun(selectedId, actorNo, text, attachmentIds);
      setSelected(run.conversation);
      setPendingAfterSeq(null);
      executionEventIds.current.clear();
      connectedExecutionId.current = run.execution_id;
      const primary = run.conversation.participants[0]?.employee_no ?? '';
      setLiveExecution({ id: run.execution_id, triggerSeq: run.trigger_message_seq, employeeId: primary, status: 'queued', startedAt: Date.now(), expanded: false, steps: [], answers: {} });
      await refreshConversations();
      await refreshAttachments();
    } catch (err) {
      setInput(text);
      setChatError((err as Error).message);
      setSelected((prev) => prev ? { ...prev, messages: prev.messages.filter((m) => m.id >= 0) } : prev);
    } finally { setSending(false); }
  };

  const uploadFiles = async (files: File[]) => {
    if (!selectedId) { message.warning('请先打开一个会话'); return; }
    const queued = attachments.filter((item) => item.message_seq === null && item.status !== 'deleted').length;
    if (queued + files.length > 5) { message.warning('单次消息最多添加 5 个文件'); return; }
    for (const file of files) {
      try {
        const row = await api.uploadConversationAttachment(selectedId, file);
        setAttachments((current) => [...current.filter((item) => item.attachment_id !== row.attachment_id), row]);
      } catch (err) {
        message.error(`${file.name} 上传失败：${(err as Error).message}`);
      }
    }
    await refreshAttachments().catch(() => undefined);
  };

  const removeAttachment = async (attachment: ConversationAttachment) => {
    if (!selectedId) return;
    try {
      await api.deleteConversationAttachment(selectedId, attachment.attachment_id);
      await refreshAttachments();
    } catch (err) { message.error(`移除失败：${(err as Error).message}`); }
  };

  const retryAttachment = async (attachment: ConversationAttachment) => {
    if (!selectedId) return;
    try {
      const row = await api.retryConversationAttachment(selectedId, attachment.attachment_id);
      setAttachments((current) => current.map((item) => item.attachment_id === row.attachment_id ? row : item));
    } catch (err) { message.error(`重试失败：${(err as Error).message}`); }
  };

  const retryExecution = async (executionId: string) => {
    if (!selectedId || sending || (liveExecution && executionIsActive(liveExecution.status))) return;
    setSending(true); setChatError(undefined);
    try {
      const run = await api.retryConversationRun(selectedId, executionId);
      setSelected(run.conversation);
      executionEventIds.current.clear();
      connectedExecutionId.current = run.execution_id;
      const primary = run.conversation.participants[0]?.employee_no ?? '';
      setLiveExecution({ id: run.execution_id, triggerSeq: run.trigger_message_seq, employeeId: primary, status: 'queued', startedAt: Date.now(), expanded: false, steps: [], answers: {} });
      await refreshConversations();
    } catch (err) {
      setChatError((err as Error).message);
    } finally { setSending(false); }
  };

  /**
   * 停止本次执行。dsh 是一次性 CLI 调用、无法保存/恢复进程内状态，所以只能「停止」
   * （终态 cancelled，不可续跑），不是「暂停」；已产生的轨迹与半截答复都会保留。
   */
  const stopExecution = async () => {
    if (!selectedId || !liveExecution || !executionIsActive(liveExecution.status) || stopping) return;
    const executionId = liveExecution.id;
    setStopping(true);
    try {
      await api.cancelConversationRun(selectedId, executionId);
      setLiveExecution((current) => current?.id === executionId
        ? { ...current, status: 'cancelled', finishedAt: Date.now(), retryable: true }
        : current);
    } catch (err) {
      message.error(`停止失败：${(err as Error).message}`);
    } finally { setStopping(false); }
  };

  /** 就地编辑最新一轮提问：保存后重跑，不追加新的用户消息。 */
  const startEditMessage = (message: { seq: number; content: string }) => {
    setEditingSeq(message.seq);
    setEditDraft(message.content);
  };

  const cancelEditMessage = () => {
    setEditingSeq(null);
    setEditDraft('');
  };

  const submitEditMessage = async () => {
    if (!selectedId || editingSeq === null || editSaving) return;
    const content = editDraft.trim();
    if (!content) return;
    setEditSaving(true); setChatError(undefined); setFocusMessage(null);
    try {
      const run = await api.rerunConversationMessage(selectedId, editingSeq, actorNo, content);
      setSelected(run.conversation);
      setEditingSeq(null); setEditDraft('');
      executionEventIds.current.clear();
      connectedExecutionId.current = run.execution_id;
      const primary = run.conversation.participants[0]?.employee_no ?? '';
      setLiveExecution({ id: run.execution_id, triggerSeq: run.trigger_message_seq, employeeId: primary, status: 'queued', startedAt: Date.now(), expanded: false, steps: [], answers: {} });
      await refreshConversations();
    } catch (err) {
      setChatError((err as Error).message);
    } finally { setEditSaving(false); }
  };

  const loadOlderExecutions = async () => {
    if (!selectedId || savedExecutions.length === 0) return;
    try {
      const older = await api.getOlderConversationRunHistory(selectedId, savedExecutions[0].id);
      const restored = older.map(restoreExecution).filter((run) => !executionIsActive(run.status));
      setSavedExecutions((current) => [...restored, ...current.filter((run) => !restored.some((old) => old.id === run.id))]);
      setHistoryMayHaveMore(older.length === 20);
    } catch (err) { message.error(`加载历史失败：${(err as Error).message}`); }
  };

  const workerName = (employeeNo: string) => {
    const participant = selected?.participants.find((p) => p.employee_no === employeeNo);
    if (participant) return participant.name;
    if (twin && employeeNo === twin.employee_no) return twin.name;
    return employees.find((e) => e.employee_no === employeeNo)?.name ?? employeeNo;
  };

  const handleApprove = async (task: TaskRun, approve: boolean) => {
    if (!selectedId || acting) return;
    setActing(true);
    try { await api.approveTask(task.id, approve, actorNo); const conv = await api.getConversation(selectedId); setSelected(conv); await refreshConversations(); }
    catch (err) { message.error(`审批失败：${(err as Error).message}`); }
    finally { setActing(false); }
  };

  /** 清空/删除后复位聊天区状态，避免残留执行轨迹、断线提示与「加载更早记录」入口。 */
  const resetChatState = useCallback(() => {
    setPendingAfterSeq(null);
    setLiveExecution(null);
    setSavedExecutions([]);
    setAttachments([]);
    setAttachmentOpenId(undefined);
    setHistoryMayHaveMore(false);
    setConnectionIssue(undefined);
    setStopping(false);
    setEditingSeq(null);
    setEditDraft('');
    setEditSaving(false);
    setOlderExecutionSeqs([]);
    sseFailures.current = 0;
    executionEventIds.current.clear();
    connectedExecutionId.current = undefined;
  }, []);

  const handleClear = async () => {
    if (!selectedId) return;
    try {
      await api.clearConversation(selectedId, actorNo);
      resetChatState();
      const conv = await api.getConversation(selectedId); setSelected(conv); await refreshConversations();
    } catch (err) { message.error(`清空失败：${(err as Error).message}`); }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteConversation(id, actorNo);
      if (id === selectedId) {
        // 必须显式置空 selectedId，否则 showList 为 false 会渲染空白聊天区。
        setSelectedId(null);
        setSelected(null);
        setMobileView('list');
        resetChatState();
        updateLocationState({ conversation: null, view: 'messages' });
      }
      await refreshConversations();
      message.success('会话已删除');
    } catch (err) { message.error(`删除失败：${(err as Error).message}`); }
  };

  const submitNewChat = async () => {
    if (selectedNos.length === 0) { message.warning('请至少选择 1 位AI员工'); return; }
    try {
      const conv = await api.createConversation({ actor_no: actorNo, kind: 'direct', title: undefined, participant_employee_nos: selectedNos });
      setNewChatOpen(false); setSelectedNos([]);
      await refreshConversations(); selectConversation(conv.id);
    } catch (err) { message.error(`创建会话失败：${(err as Error).message}`); }
  };

  const submitSkill = async () => {
    const values = await skillForm.validateFields();
    try {
      await api.createSkill({ actor_no: actorNo, name: values.name, description: values.description ?? '', content: values.content ?? '' });
      setSkillModalOpen(false); skillForm.resetFields(); reload(); message.success('个人工作方法已保存');
    } catch (err) { message.error(`上传失败：${(err as Error).message}`); }
  };

  const toggleSkill = async (skill: Skill, enabled: boolean) => {
    try { await api.updateSkill(skill.id, actorNo, { status: enabled ? 'active' : 'disabled' }); reload(); }
    catch (err) { message.error(`更新失败：${(err as Error).message}`); }
  };

  const removeSkill = async (skill: Skill) => {
    try { await api.deleteSkill(skill.id, actorNo); reload(); }
    catch (err) { message.error(`删除失败：${(err as Error).message}`); }
  };

  const submitAddMembers = async () => {
    if (!selectedId) return;
    try {
      let current = selected;
      for (const no of memberNos) current = await api.addConversationParticipant(selectedId, no);
      setSelected(current); setAddMemberOpen(false); setMemberNos([]); await refreshConversations();
    } catch (err) { message.error(`添加成员失败：${(err as Error).message}`); }
  };

  if (loading) return <LoadingState rows={8} />;
  if (error || !home || !home.actor || !home.twin || !Array.isArray(home.available_employees)) {
    return <ErrorState onRetry={reload} />;
  }

  const chatParticipants = selected?.participants ?? [];
  const isGroup = selected?.kind === 'group';
  const taskGroups = new Map<number, TaskRun[]>();
  const unattachedTasks: TaskRun[] = [];
  for (const task of selected?.tasks ?? []) {
    if (task.trigger_message_seq != null && selected?.messages.some((m) => m.seq === task.trigger_message_seq)) {
      const list = taskGroups.get(task.trigger_message_seq) ?? [];
      list.push(task); taskGroups.set(task.trigger_message_seq, list);
    } else { unattachedTasks.push(task); }
  }
  const selectedTitle = selected && (selected.kind === 'group' ? selected.title || '协作空间' : selected.participants[0]?.name ?? '');
  // 标题栏类型徽章：口径与会话列表分段一致，都读 participant.employee_type（群聊固定「协作空间」）。
  const headerType = selected?.kind === 'group' ? 'group' : (selected?.participants[0]?.employee_type || 'virtual');
  const headerMeta = GROUP_META[headerType] ?? GROUP_META.virtual;
  const addableMembers = employees.filter((emp) => !chatParticipants.some((p) => p.employee_no === emp.employee_no));
  const capabilityEmployee = [twin, ...employees].find((e) => e?.employee_no === capabilityEmployeeNo) ?? null;
  /**
   * 本轮执行事件里的知识检索来源（含委派检索）：工具卡片缺失时用它兜底。
   * 事件表天然不过滤执行人，所以「分身无知识权限 → 委派岗位员工检索」这条链路
   * 也能显示来源；卡片存在时由 CitationList 优先取卡片（信息更全）。
   */
  const knowledgeFallbackOf = (msg: { seq: number; participant_no: string }): KnowledgeSourceFallback[] => {
    const executions = [
      ...(liveExecution?.triggerSeq === msg.seq ? [liveExecution] : []),
      ...savedExecutions.filter((run) => run.triggerSeq === msg.seq),
    ];
    const out: KnowledgeSourceFallback[] = [];
    const seen = new Set<string>();
    for (const run of executions) {
      for (const step of run.steps) {
        if (!step.knowledgeBaseId || seen.has(step.knowledgeBaseId)) continue;
        seen.add(step.knowledgeBaseId);
        // 仅当检索人不是本条回复的作者时才标注「由 XX 检索」。
        const delegated = step.actorEmployeeId && step.actorEmployeeId !== msg.participant_no
          ? step.actorEmployeeId
          : undefined;
        out.push({
          knowledgeBaseId: step.knowledgeBaseId,
          knowledgeBaseName: step.knowledgeBaseName,
          hitCount: step.hitCount,
          agentEmployeeId: delegated,
          agentEmployeeName: delegated ? workerName(delegated) : undefined,
        });
      }
    }
    return out;
  };

  // 「来源与依据」抽屉取最近一条未作废的回复：卡片为空时同样吃执行事件兜底。
  const lastAssistantMessage = selected
    ? [...selected.messages].reverse().find((m) => m.role === 'assistant' && !m.superseded_at)
    : undefined;
  const lastAssistantCards = lastAssistantMessage?.tool_cards ?? [];
  const lastAssistantFallback = lastAssistantMessage ? knowledgeFallbackOf(lastAssistantMessage) : [];

  const renderExecutionCard = (run: LiveExecution) => {
    // 补全气泡**不放进可折叠的轨迹里**：它是「需要你动手」的东西，藏进折叠区用户就看不到；
    // 提交后还要原地留一行「已补充 · 已继续执行」，折叠会把它一起藏掉。
    const runInputRequests = inputRequests.filter((request) => request.execution_id === run.id);
    const runActive = executionIsActive(run.status);
    return (
      <div className="mt-1.5">
        <ExecutionSummary
          run={run}
          employeeName={workerName(run.employeeId)}
          expanded={run.expanded}
          onToggle={() => {
            if (liveExecution?.id === run.id) setLiveExecution((c) => c ? { ...c, expanded: !c.expanded } : c);
            else setSavedExecutions((c) => c.map((r) => r.id === run.id ? { ...r, expanded: !r.expanded } : r));
          }}
          onRetry={() => void retryExecution(run.id)}
        />
        {runInputRequests.map((request) => (
          <InputRequestCard
            key={request.id}
            request={request}
            running={runActive}
            submitting={inputSubmittingId === request.id}
            error={inputErrors[request.id]}
            ignored={ignoredInputIds.includes(request.id)}
            onSubmit={(values) => void submitInputRequest(request, values)}
            onIgnore={ignoreInputRequest}
          />
        ))}
        {run.expanded && (
          <ExecutionDetails
            run={run}
            steps={run.steps}
            employeeName={workerName}
            onRetry={() => void retryExecution(run.id)}
          />
        )}
        {/* 流式答复不依赖展开状态：折叠时也要能实时看到正在生成的文字。 */}
        {Object.entries(run.answers).map(([id, answer]) => answer ? (
          <div key={id} className="mt-1.5 rounded-control bg-surface-subtle p-2.5 text-sm text-ink">{workerName(id)}：{answer}</div>
        ) : null)}
      </div>
    );
  };

  const renderMessageExtra = (msg: { seq: number }) => {
    // 同一轮可能挂多条执行（改完重跑 / 停止后重跑），必须全部收齐：
    // 只渲染最新一条，更早的折叠起来。否则旧代码的 `.find` 只会取到最早那条，
    // 而 /runs/history 是旧→新排序，新卡片不显示，看起来像「点了重跑没反应」。
    const executions = [
      ...(liveExecution?.triggerSeq === msg.seq ? [liveExecution] : []),
      ...savedExecutions.filter((run) => run.triggerSeq === msg.seq),
    ].sort((a, b) => a.startedAt - b.startedAt);
    const latestExecution = executions[executions.length - 1];
    const olderExecutions = executions.slice(0, -1);
    const olderOpen = olderExecutionSeqs.includes(msg.seq);
    const tasks = taskGroups.get(msg.seq) ?? [];
    return (
      <div className="ml-12 mt-1.5 max-w-[760px]">
        {tasks.map((task) => (
          <TaskCard key={task.id} task={task} workerName={workerName} metaOf={metaOf} onApprove={handleApprove} acting={acting} onOpenProfile={openProfile} />
        ))}
        {olderExecutions.length > 0 && (
          <div className="mt-1.5">
            <Button
              type="link"
              size="small"
              className="!px-0 text-xs"
              icon={olderOpen ? <UpOutlined /> : <DownOutlined />}
              onClick={() => setOlderExecutionSeqs((current) => current.includes(msg.seq)
                ? current.filter((seq) => seq !== msg.seq)
                : [...current, msg.seq])}
            >
              {olderOpen ? '收起上一次执行' : `查看上一次执行（${olderExecutions.length}）`}
            </Button>
            {olderOpen && olderExecutions.map((run) => <div key={run.id}>{renderExecutionCard(run)}</div>)}
          </div>
        )}
        {latestExecution && renderExecutionCard(latestExecution)}
      </div>
    );
  };

  const showList = mobileView === 'list' || selectedId === null;
  const handleSelectConversation = selectConversation;

  return (
    <div className="flex h-full">
      {/* 会话列表 288px（移动端一次只显示一个） */}
      <aside className={`w-full shrink-0 flex-col border-r border-line bg-surface md:w-[304px] ${showList ? 'flex' : 'hidden'} md:flex`}>
        <div className="workspace-section-switch" role="tablist" aria-label="协作内容">
          <button type="button" role="tab" aria-selected={sideView === 'messages'} className={sideView === 'messages' ? 'active' : ''} onClick={() => { setSideView('messages'); updateLocationState({ view: 'messages' }); }}>会话</button>
          <button type="button" role="tab" aria-selected={sideView === 'directory'} className={sideView === 'directory' ? 'active' : ''} onClick={() => { setSideView('directory'); updateLocationState({ view: 'directory' }); }}>同事</button>
        </div>
        {sideView === 'messages' ? (
          <ConversationList
            conversations={conversations}
            twin={twin}
            employees={employees}
            selectedId={selectedId}
            keyword={keyword}
            hits={searchHits}
            searching={searching}
            searchError={searchError}
            onKeywordChange={setKeyword}
            onSelect={handleSelectConversation}
            onSelectHit={openSearchHit}
            onOpenTwin={() => { setMobileView('chat'); void openDirect(twin!.employee_no); }}
            onOpenProfile={openProfile}
            onOpenDirectory={() => { setSideView('directory'); updateLocationState({ view: 'directory' }); }}
            onDelete={(id) => void handleDelete(id)}
          />
        ) : (
          <ColleagueList twin={twin} employees={employees} onOpenChat={(no) => void openDirect(no)} onOpenProfile={openProfile} />
        )}
      </aside>

      {/* 聊天区 */}
      <main className={`chat-workspace min-w-0 flex-1 ${showList ? 'hidden' : 'flex'} md:flex flex-col bg-page`}>
        {selected ? (
          <>
            <div className="flex h-16 shrink-0 items-center gap-3 border-b border-line bg-surface px-4 sm:px-5">
              <Button type="text" className="md:hidden" icon={<LeftOutlined />} onClick={() => setMobileView('list')} aria-label="返回会话列表" />
              {isGroup ? (
                <Avatar size={36} style={{ background: 'var(--dwp-surface-subtle)', color: 'var(--dwp-text-secondary)' }}><TeamOutlined /></Avatar>
              ) : (
                <button type="button" className="avatar-button" aria-label={`查看${selectedTitle}的资料`} onClick={() => openProfile(selected.participants[0]?.employee_no ?? '')}>
                  <AgentAvatar employee={[twin, ...employees].find((item) => item?.employee_no === selected.participants[0]?.employee_no)} name={selectedTitle ?? undefined} size={36} />
                </button>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="truncate text-base font-semibold text-ink">{selectedTitle}</span>
                  <span className={`type-badge type-badge--header type-badge--${headerType}`}>{headerMeta.label}</span>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-ink-secondary">
                  {!isGroup && <span className="inline-block h-1.5 w-1.5 rounded-full bg-success" />}
                  <span>{isGroup ? `${selected.participants.length} 人 · ${selected.participants.map((p) => p.name).join('、')}` : '当前可用'}</span>
                </div>
              </div>
              <Tooltip title="来源与依据">
                <Button type="text" icon={<FileSearchOutlined />} onClick={() => setCitationOpen(true)} aria-label="来源与依据" />
              </Tooltip>
              {!isGroup && (
                <Button type="text" aria-label="资料" icon={<CommentOutlined />} onClick={() => openProfile(selected.participants[0]?.employee_no ?? '')}>
                  资料
                </Button>
              )}
              {isGroup && addableMembers.length > 0 && <Button size="small" onClick={() => setAddMemberOpen(true)}>添加成员</Button>}
              {clearConfirming ? (
                <Space size={6}>
                  <Button size="small" danger type="primary" aria-label="确认清空" onClick={() => { setClearConfirming(false); void handleClear(); }}>
                    确认清空
                  </Button>
                  <Button size="small" aria-label="取消清空" onClick={() => setClearConfirming(false)}>取消</Button>
                </Space>
              ) : (
                <Button size="small" danger onClick={() => setClearConfirming(true)}>清空</Button>
              )}
            </div>

            {connectionIssue && (
              <div className="flex items-center justify-between gap-3 border-b border-warning/30 bg-warning-soft px-4 py-2 text-xs text-ink-secondary">
                <span>{connectionIssue}</span>
                <Button size="small" onClick={() => { setConnectionIssue(undefined); sseFailures.current = 0; setReconnectNonce((value) => value + 1); }}>重新连接</Button>
              </div>
            )}
            {historyMayHaveMore && (
              <div className="border-b border-line bg-surface px-4 py-1.5 text-center">
                <Button type="link" size="small" onClick={() => void loadOlderExecutions()}>加载更早执行记录</Button>
              </div>
            )}

            <MessageList
              conversation={selected}
              streamingLength={streamedLength}
              focusMessage={focusMessage}
              renderMessageExtra={renderMessageExtra}
              employeeOf={(employeeNo) => [twin, ...employees].find((item) => item?.employee_no === employeeNo) ?? null}
              onOpenProfile={openProfile}
              attachmentOf={(attachmentId) => attachments.find((item) => item.attachment_id === attachmentId)}
              onOpenAttachment={setAttachmentOpenId}
              knowledgeFallbackOf={knowledgeFallbackOf}
              editableSeq={editableSeq}
              editingSeq={editingSeq}
              editDraft={editDraft}
              editSaving={editSaving}
              onStartEdit={startEditMessage}
              onEditDraftChange={setEditDraft}
              onSubmitEdit={() => void submitEditMessage()}
              onCancelEdit={cancelEditMessage}
              canRegenerate={editableSeq !== null}
              onRegenerate={() => void regenerateMessage()}
              emptyState={
                <div className="mx-auto flex h-full max-w-[520px] flex-col items-center justify-center px-6 text-center">
                  <div className="grid h-16 w-16 place-items-center rounded-[18px] border border-line bg-surface text-2xl text-brand shadow-sm">{isGroup ? <TeamOutlined /> : <RobotOutlined />}</div>
                  <Typography.Title level={4} className="!mb-1 !mt-4">和 {selectedTitle} 开始协作</Typography.Title>
                  <Paragraph type="secondary" className="!mb-0">直接描述问题或工作目标。AI员工会按权限查阅知识，并保留可核验的执行记录。</Paragraph>
                </div>
              }
            />

            <div className="shrink-0 border-t border-line bg-surface px-4 pb-3 pt-2.5 sm:px-6">
              <Composer
                value={input}
                onChange={setInput}
                onSend={() => void send()}
                sending={sending}
                active={executionRunning}
                stopping={stopping}
                onStop={() => void stopExecution()}
                disabled={executionRunning}
                placeholder={isGroup ? '描述一个任务，我来拆解安排给同事们…' : '发消息…'}
                attachments={attachments.filter((item) => item.message_seq === null && item.status !== 'deleted')}
                onFiles={(files) => void uploadFiles(files)}
                onRemoveAttachment={(item) => void removeAttachment(item)}
                onRetryAttachment={(item) => void retryAttachment(item)}
              />
              {chatError && <div className="mt-1.5 text-xs text-danger">发送失败：{chatError}，消息已保留在输入框</div>}
            </div>
          </>
        ) : (
          <div className="mx-auto flex h-full max-w-[640px] flex-col items-center justify-center px-6 text-center">
            <BrandMark size={68} />
            <Typography.Title level={3} style={{ margin: '18px 0 4px' }}>你好，{actor.name}</Typography.Title>
            <Paragraph type="secondary" className="max-w-[460px]">从一段对话开始今天的工作。你可以询问公司制度、IT 服务或投资研究相关问题。</Paragraph>
            <Space>
              {twin && <Button type="primary" icon={<RobotOutlined />} onClick={() => void openDirect(twin.employee_no)}>和我的分身聊聊</Button>}
              <Button icon={<CommentOutlined />} onClick={() => setNewChatOpen(true)}>查找AI员工</Button>
            </Space>
          </div>
        )}
      </main>

      {/* 发起会话弹窗 */}
      <Modal
        title="发起会话"
        open={newChatOpen}
        onCancel={() => { setNewChatOpen(false); setSelectedNos([]); }}
        onOk={() => void submitNewChat()}
        okText="创建"
      >
        <Paragraph type="secondary">首版一次只与一位AI员工对话；你的分身会在需要时自主向专业AI员工求助。</Paragraph>
        <div className="max-h-80 overflow-y-auto">
          {([twin, ...employees].filter((e): e is Employee => e !== null)).map((emp) => {
            const checked = selectedNos.includes(emp.employee_no);
            return (
              <div key={emp.employee_no} className="flex cursor-pointer items-center gap-3 rounded-control px-2 py-2 hover:bg-surface-subtle" onClick={() => setSelectedNos([emp.employee_no])}>
                <span className="avatar-button" role="button" tabIndex={0} onClick={(event) => { event.stopPropagation(); openProfile(emp.employee_no); }}><AgentAvatar employee={emp} size={34} /></span>
                <div className="min-w-0 flex-1">
                  <div className="text-sm text-ink">{emp.name}</div>
                  <div className="text-xs text-ink-secondary">{emp.department}</div>
                </div>
                <Checkbox checked={checked} />
              </div>
            );
          })}
        </div>
      </Modal>

      {/* 添加群成员弹窗 */}
      <Modal
        title="添加成员"
        open={addMemberOpen}
        onCancel={() => { setAddMemberOpen(false); setMemberNos([]); }}
        onOk={() => void submitAddMembers()}
        okText="添加"
        okButtonProps={{ disabled: memberNos.length === 0 }}
      >
        {addableMembers.length === 0 ? <Empty description="没有可添加的AI员工" /> : addableMembers.map((emp) => {
          return (
            <div key={emp.employee_no} className="flex cursor-pointer items-center gap-3 rounded-control px-2 py-2 hover:bg-surface-subtle" onClick={() => setMemberNos((prev) => prev.includes(emp.employee_no) ? prev.filter((n) => n !== emp.employee_no) : [...prev, emp.employee_no])}>
              <span className="avatar-button" role="button" tabIndex={0} onClick={(event) => { event.stopPropagation(); openProfile(emp.employee_no); }}><AgentAvatar employee={emp} size={34} /></span>
              <div className="min-w-0 flex-1">
                <div className="text-sm text-ink">{emp.name}</div>
                <div className="text-xs text-ink-secondary">{emp.role_prompt || emp.department}</div>
              </div>
              <Checkbox checked={memberNos.includes(emp.employee_no)} />
            </div>
          );
        })}
      </Modal>

      {/* 同事资料抽屉 */}
      <Drawer
        title="同事资料"
        open={profileOpen}
        onClose={() => { setProfileOpen(false); setCapabilityEmployeeNo(undefined); setSkillConfirmingId(null); updateLocationState({ profile: null }); }}
        width={440}
        extra={capabilityEmployee?.type === 'twin' ? (
          <Button type="primary" size="small" icon={<UploadOutlined />} onClick={() => setSkillModalOpen(true)}>添加工作方法</Button>
        ) : null}
      >
        {capabilityEmployee && (
          <>
            <div className="flex items-center gap-3">
              <AgentAvatar employee={capabilityEmployee} size={64} />
              <div>
                <div className="text-lg font-semibold text-ink">{capabilityEmployee.name}</div>
                <Text type="secondary" style={{ fontSize: 12 }}>{capabilityEmployee.department} · {metaOf(capabilityEmployee.employee_no).label}</Text>
              </div>
            </div>
            <Paragraph className="mt-3 rounded-control bg-surface-subtle p-3">{capabilityEmployee.role_prompt || '（尚未配置职责与擅长方向）'}</Paragraph>
            <Button className="mb-1" type="primary" icon={<CommentOutlined />} onClick={() => { setProfileOpen(false); updateLocationState({ profile: null }); void openDirect(capabilityEmployee.employee_no); }}>发消息</Button>
            {capabilityLoading && <LoadingState rows={4} />}
            {capabilityError && <Alert type="error" showIcon message="能力状态加载失败" description={capabilityError} />}
            {effectiveCapabilities && (
              <>
                <Space size={[6, 6]} wrap className="mt-3">
                  <Tag color={['ready', 'busy'].includes(effectiveCapabilities.runtime_state) ? 'green' : 'red'}>
                    Harness {['ready', 'busy'].includes(effectiveCapabilities.runtime_state) ? '已就绪' : effectiveCapabilities.runtime_state}
                  </Tag>
                  <Tag color={effectiveCapabilities.knowledge_mode === 'internal' ? 'blue' : 'gold'}>
                    {effectiveCapabilities.knowledge_mode === 'internal' ? '内部知识引擎' : 'Mock 知识'}
                  </Tag>
                  <Tag color="blue">{effectiveCapabilities.available_count} 项可用</Tag>
                </Space>
                <div className="mt-3 space-y-2">
                  {effectiveCapabilities.capabilities.filter((item) => item.authorized).length === 0 && <Empty description="暂未授权能力" />}
                  {effectiveCapabilities.capabilities.filter((item) => item.authorized).map((item) => (
                    <div key={item.id} className="flex items-start justify-between gap-3 rounded-control bg-surface-subtle p-3">
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-ink">{item.name}</div>
                        <div className="text-xs text-ink-secondary">{item.description}</div>
                        {item.example_prompts[0] && <div className="text-xs text-ink-tertiary">示例：{item.example_prompts[0]}</div>}
                      </div>
                      <Tag color={item.status === 'available' ? 'success' : item.status === 'approval' ? 'warning' : 'error'}>
                        {item.status === 'available' ? '可用' : item.status === 'approval' ? '需审批' : '环境未就绪'}
                      </Tag>
                    </div>
                  ))}
                </div>
              </>
            )}
            {capabilityEmployee.type === 'twin' && (
              <div className="mt-4">
                <div className="mb-2 text-sm font-medium text-ink">个人工作方法（{skills.length}）</div>
                {skills.length === 0 && <Empty description="还没有个人工作方法" />}
                {skills.map((skill) => (
                  <div key={skill.id} className="mb-2 flex items-center gap-3 rounded-control bg-surface-subtle p-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-ink">{skill.name}</div>
                      <div className="truncate text-xs text-ink-secondary">{skill.description || skill.content.slice(0, 60)}</div>
                    </div>
                    <Switch size="small" checked={skill.status === 'active'} onChange={(checked) => void toggleSkill(skill, checked)} aria-label={`切换技能 ${skill.name}`} />
                    {skillConfirmingId === skill.id ? (
                      <Space size={4}>
                        <Button size="small" danger type="primary" aria-label={`确认删除技能 ${skill.name}`} onClick={() => { setSkillConfirmingId(null); void removeSkill(skill); }}>删除</Button>
                        <Button size="small" aria-label={`取消删除技能 ${skill.name}`} onClick={() => setSkillConfirmingId(null)}>取消</Button>
                      </Space>
                    ) : (
                      <Button type="text" size="small" danger icon={<DeleteOutlined />} aria-label={`删除技能 ${skill.name}`} onClick={() => setSkillConfirmingId(skill.id)} />
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </Drawer>

      {/* 引用预览侧栏 */}
      <CitationPreview
        cards={lastAssistantCards}
        fallback={lastAssistantFallback}
        open={citationOpen}
        onClose={() => setCitationOpen(false)}
      />

      <Drawer
        title="文件详情"
        open={Boolean(attachmentOpenId)}
        onClose={() => setAttachmentOpenId(undefined)}
        width={420}
      >
        {(() => {
          const item = attachments.find((row) => row.attachment_id === attachmentOpenId);
          if (!item) return <Empty description="文件已删除或不再属于当前会话" />;
          return (
            <div className="space-y-4">
              <div className="rounded-container border border-line bg-surface-subtle p-4">
                <div className="break-all text-base font-semibold text-ink">{item.filename}</div>
                <div className="mt-1 text-xs text-ink-secondary">{item.file_type.toUpperCase()} · {(item.size / 1024).toFixed(1)} KiB</div>
              </div>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div><div className="text-xs text-ink-tertiary">处理状态</div><div className="mt-1 text-ink">{item.status === 'ready' ? '解析完成' : item.processing_hint || item.status}</div></div>
                <div><div className="text-xs text-ink-tertiary">内容规模</div><div className="mt-1 text-ink">{item.page_count != null ? `${item.page_count} 页` : item.sheet_count != null ? `${item.sheet_count} 个工作表` : `${item.chunk_count} 个片段`}</div></div>
                <div><div className="text-xs text-ink-tertiary">会话关联</div><div className="mt-1 text-ink">{item.message_seq == null ? '待发送' : `第 ${item.message_seq} 条消息`}</div></div>
                <div><div className="text-xs text-ink-tertiary">上传时间</div><div className="mt-1 text-ink">{formatServerTime(item.created_at)}</div></div>
              </div>
              <Alert type="info" showIcon message="按需读取" description="AI员工只会在回答当前问题时按需搜索和读取相关片段，文件全文不会一次性塞入模型上下文。" />
            </div>
          );
        })()}
      </Drawer>

      {/* 添加个人工作方法弹窗 */}
      <Modal
        title="添加个人工作方法"
        open={skillModalOpen}
        onCancel={() => { setSkillModalOpen(false); skillForm.resetFields(); }}
        onOk={() => void submitSkill()}
        okText="上传"
        width={520}
      >
        <Form form={skillForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如：报销制度速答" maxLength={40} />
          </Form.Item>
          <Form.Item name="description" label="一句话说明">
            <Input placeholder="我的分身会用这句话向别人介绍这项技能" maxLength={80} />
          </Form.Item>
          <Form.Item name="content" label="工作方法内容" rules={[{ required: true, message: '请填写或导入内容' }]}>
            <Input.TextArea rows={7} placeholder="粘贴 Markdown / 纯文本内容" />
          </Form.Item>
          <Upload.Dragger
            accept=".md,.txt,.markdown"
            maxCount={1}
            beforeUpload={(file) => {
              const reader = new FileReader();
              reader.onload = () => skillForm.setFieldValue('content', String(reader.result ?? ''));
              reader.readAsText(file);
              return false;
            }}
            showUploadList={false}
          >
            <p className="ant-upload-drag-icon"><UploadOutlined /></p>
            <p className="ant-upload-text">点击或拖拽 .md / .txt 文件导入内容</p>
          </Upload.Dragger>
        </Form>
      </Modal>
    </div>
  );
}
