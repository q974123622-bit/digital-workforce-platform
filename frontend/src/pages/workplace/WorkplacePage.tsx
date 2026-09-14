import { useCallback, useEffect, useRef, useState } from 'react';
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
  Popconfirm,
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
import CitationPreview from '../../features/knowledge/CitationPreview';

const { Text, Paragraph } = Typography;

const GROUP_META: Record<string, ContactMeta> = {
  twin: { label: '我的分身', emoji: '分', color: 'var(--dwp-brand)', bg: 'var(--dwp-brand-soft)' },
  virtual: { label: '数字员工', emoji: '员', color: 'var(--dwp-brand)', bg: 'var(--dwp-brand-soft)' },
  rpa: { label: '自动化小助手', emoji: '执', color: 'var(--dwp-warning)', bg: 'var(--dwp-warning-soft)' },
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
  startedAt: number;
  lastStepAt: number;
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
  const startedAt = new Date(run.started_at).getTime();
  const stepEvents = events.filter(
    (event) => !['answer_chunk', 'answer_done', 'error'].includes(event.event_type) && event.title,
  );
  let prevAt = startedAt;
  const steps: ExecutionStep[] = stepEvents.map((event) => {
    const at = new Date(event.created_at).getTime();
    const durationMs = Math.max(0, at - prevAt);
    prevAt = at;
    return {
      id: String(event.event_seq),
      title: event.title,
      detail: event.detail,
      status: event.status,
      durationMs,
      knowledgeBaseId: event.knowledge_base_id ?? undefined,
      targetAgentId: event.target_agent_id ?? undefined,
      hitCount: event.hit_count ?? undefined,
    };
  });
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
    lastStepAt: prevAt,
    finishedAt: run.completed_at ? new Date(run.completed_at).getTime() : undefined,
    expanded: active || run.status === 'failed',
    steps,
    answers,
    error: failure?.detail || run.error_message || undefined,
    retryable: active ? run.retryable : false,
    resumeAfter: active && lastEvent ? String(lastEvent.event_seq) : undefined,
  };
}

function TaskCard({ task, workerName, metaOf, onApprove, acting }: {
  task: TaskRun;
  workerName: (employeeNo: string) => string;
  metaOf: (employeeNo: string) => ContactMeta;
  onApprove: (task: TaskRun, approve: boolean) => void;
  acting: boolean;
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
                  <Avatar size={22} style={{ background: workerMeta.bg, color: workerMeta.color, fontSize: 11 }}>
                    {workerMeta.emoji}
                  </Avatar>
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

  const homeFetcher = useCallback(() => api.getWorkplace(actorNo), [actorNo]);
  const { data: home, loading, error, reload } = useAsyncData<WorkplaceHome>(homeFetcher);

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const refreshConversations = useCallback(() => {
    return api.listConversations(actorNo).then(setConversations);
  }, [actorNo]);

  useEffect(() => { void refreshConversations().catch(() => undefined); }, [refreshConversations]);

  const [keyword, setKeyword] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
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

  const [newChatOpen, setNewChatOpen] = useState(false);
  const [selectedNos, setSelectedNos] = useState<string[]>([]);
  // 移动端：会话列表 / 聊天区一次只显示一个。
  const [mobileView, setMobileView] = useState<'list' | 'chat'>('list');

  const [profileOpen, setProfileOpen] = useState(false);
  const [citationOpen, setCitationOpen] = useState(false);
  const [capabilityEmployeeNo, setCapabilityEmployeeNo] = useState<string>();
  const [effectiveCapabilities, setEffectiveCapabilities] = useState<EffectiveCapabilities>();
  const [capabilityLoading, setCapabilityLoading] = useState(false);
  const [capabilityError, setCapabilityError] = useState<string>();
  const [skillModalOpen, setSkillModalOpen] = useState(false);
  const [skillForm] = Form.useForm();

  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const [memberNos, setMemberNos] = useState<string[]>([]);

  useEffect(() => {
    setSelectedId(null); setSelected(null); setInput(''); setPendingAfterSeq(null);
    setLiveExecution(null); setSavedExecutions([]);
  }, [actorNo]);

  useEffect(() => {
    if (!selectedId) { setSelected(null); return; }
    let active = true;
    api.getConversation(selectedId).then((conv) => { if (active) setSelected(conv); }).catch(() => { if (active) setSelected(null); });
    return () => { active = false; };
  }, [selectedId]);

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
      setLiveExecution((current) => {
        if (!current || current.id !== executionId) return current;
        const at = data.created_at ? new Date(String(data.created_at)).getTime() : Date.now();
        const step: ExecutionStep = {
          id: event.lastEventId || `${Date.now()}-${current.steps.length}`,
          title: String(data.title ?? '正在执行'),
          detail: String(data.detail ?? ''),
          status: String(data.status ?? 'running'),
          durationMs: Math.max(0, at - current.lastStepAt),
          knowledgeBaseId: data.knowledge_base_id ? String(data.knowledge_base_id) : undefined,
          targetAgentId: data.target_agent_id ? String(data.target_agent_id) : undefined,
          hitCount: typeof data.hit_count === 'number' ? data.hit_count : undefined,
        };
        return { ...current, status: 'running', expanded: true, lastStepAt: at, steps: [...current.steps, step], resumeAfter: event.lastEventId || current.resumeAfter };
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
      setLiveExecution((current) => current?.id === executionId ? { ...current, status: 'completed', expanded: false, finishedAt: Date.now() } : current);
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
    source.addEventListener('error', failed);
    return () => source.close();
  }, [liveExecution?.id, reconnectNonce, refreshConversations, selectedId]);

  useEffect(() => {
    if (!liveExecution || executionIsActive(liveExecution.status)) return;
    setSavedExecutions((current) => [...current.filter((run) => run.id !== liveExecution.id), liveExecution]);
  }, [liveExecution]);

  const streamedLength = liveExecution ? Object.values(liveExecution.answers).reduce((t, a) => t + a.length, 0) : 0;

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
      if (existing) { setSelectedId(existing.id); return; }
      const conv = await api.createConversation({ actor_no: actorNo, kind: 'direct', participant_employee_nos: [employeeNo] });
      await refreshConversations();
      setSelectedId(conv.id);
    } catch (err) { message.error(`发起会话失败：${(err as Error).message}`); }
  };

  const send = async (overrideText?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || sending || (liveExecution && executionIsActive(liveExecution.status)) || !selectedId) return;
    setInput('');
    setSending(true);
    setChatError(undefined);
    setSelected((prev) => {
      if (!prev) return prev;
      const lastSeq = prev.messages.reduce((max, m) => Math.max(max, m.seq), 0);
      return { ...prev, messages: [...prev.messages, { id: -Date.now(), conversation_id: prev.id, participant_no: actorNo, participant_name: actor.name, role: 'user', content: text, tool_cards: [], seq: lastSeq + 1 }] };
    });
    try {
      const run = await api.startConversationRun(selectedId, actorNo, text);
      setSelected(run.conversation);
      setPendingAfterSeq(null);
      executionEventIds.current.clear();
      connectedExecutionId.current = run.execution_id;
      const primary = run.conversation.participants[0]?.employee_no ?? '';
      setLiveExecution({ id: run.execution_id, triggerSeq: run.trigger_message_seq, employeeId: primary, status: 'queued', startedAt: Date.now(), lastStepAt: Date.now(), expanded: true, steps: [], answers: {} });
      await refreshConversations();
    } catch (err) {
      setInput(text);
      setChatError((err as Error).message);
      setSelected((prev) => prev ? { ...prev, messages: prev.messages.filter((m) => m.id >= 0) } : prev);
    } finally { setSending(false); }
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
      setLiveExecution({ id: run.execution_id, triggerSeq: run.trigger_message_seq, employeeId: primary, status: 'queued', startedAt: Date.now(), lastStepAt: Date.now(), expanded: true, steps: [], answers: {} });
      await refreshConversations();
    } catch (err) {
      setChatError((err as Error).message);
    } finally { setSending(false); }
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

  const handleClear = async () => {
    if (!selectedId) return;
    try {
      await api.clearConversation(selectedId, actorNo);
      setPendingAfterSeq(null); setLiveExecution(null); setSavedExecutions([]);
      const conv = await api.getConversation(selectedId); setSelected(conv); await refreshConversations();
    } catch (err) { message.error(`清空失败：${(err as Error).message}`); }
  };

  const submitNewChat = async () => {
    if (selectedNos.length === 0) { message.warning('请至少选择 1 位数字员工'); return; }
    try {
      const conv = await api.createConversation({ actor_no: actorNo, kind: 'direct', title: undefined, participant_employee_nos: selectedNos });
      setNewChatOpen(false); setSelectedNos([]);
      await refreshConversations(); setSelectedId(conv.id);
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
  const selectedMeta = selected ? metaOf(selected.participants[0]?.employee_no ?? '') : GROUP_META.twin;
  const addableMembers = employees.filter((emp) => !chatParticipants.some((p) => p.employee_no === emp.employee_no));
  const capabilityEmployee = [twin, ...employees].find((e) => e?.employee_no === capabilityEmployeeNo) ?? null;
  const lastAssistantCards = selected ? (selected.messages.filter((m) => m.role === 'assistant' && m.tool_cards.length > 0).slice(-1)[0]?.tool_cards ?? []) : [];

  const renderMessageExtra = (msg: { seq: number }) => {
    const messageExecution = (liveExecution?.triggerSeq === msg.seq ? liveExecution : undefined) ?? savedExecutions.find((run) => run.triggerSeq === msg.seq);
    const tasks = taskGroups.get(msg.seq) ?? [];
    return (
      <div className="ml-12 mt-1.5 max-w-[760px]">
        {tasks.map((task) => (
          <TaskCard key={task.id} task={task} workerName={workerName} metaOf={metaOf} onApprove={handleApprove} acting={acting} />
        ))}
        {messageExecution && (
          <div className="mt-1.5">
            <ExecutionSummary
              run={messageExecution}
              employeeName={workerName(messageExecution.employeeId)}
              expanded={messageExecution.expanded}
              onToggle={() => {
                if (liveExecution?.id === messageExecution.id) setLiveExecution((c) => c ? { ...c, expanded: !c.expanded } : c);
                else setSavedExecutions((c) => c.map((r) => r.id === messageExecution.id ? { ...r, expanded: !r.expanded } : r));
              }}
              onRetry={() => void retryExecution(messageExecution.id)}
            />
            {messageExecution.expanded && (
              <ExecutionDetails
                run={messageExecution}
                steps={messageExecution.steps}
                employeeName={workerName}
                onRetry={() => void retryExecution(messageExecution.id)}
              />
            )}
            {messageExecution.expanded && Object.entries(messageExecution.answers).map(([id, answer]) => answer ? (
              <div key={id} className="mt-1.5 rounded-control bg-surface-subtle p-2.5 text-sm text-ink">{workerName(id)}：{answer}</div>
            ) : null)}
          </div>
        )}
      </div>
    );
  };

  const showList = mobileView === 'list' || selectedId === null;
  const handleSelectConversation = (id: string) => {
    setSelectedId(id);
    setMobileView('chat');
  };

  return (
    <div className="flex h-full">
      {/* 会话列表 288px（移动端一次只显示一个） */}
      <aside className={`w-full shrink-0 border-r border-line bg-surface md:w-[304px] ${showList ? 'flex' : 'hidden'} md:flex`}>
        <ConversationList
          conversations={conversations}
          twin={twin}
          selectedId={selectedId}
          keyword={keyword}
          onKeywordChange={setKeyword}
          onSelect={handleSelectConversation}
          onOpenTwin={() => { setMobileView('chat'); void openDirect(twin!.employee_no); }}
          onNewChat={() => setNewChatOpen(true)}
          metaOf={metaOf}
        />
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
                <Avatar size={36} style={{ background: selectedMeta.bg, color: selectedMeta.color }}>{selectedMeta.emoji}</Avatar>
              )}
              <div className="min-w-0 flex-1">
                <div className="truncate text-base font-semibold text-ink">{selectedTitle}</div>
                <div className="flex items-center gap-1.5 text-xs text-ink-secondary">
                  {!isGroup && <span className="inline-block h-1.5 w-1.5 rounded-full bg-success" />}
                  <span>{isGroup ? `${selected.participants.length} 人 · ${selected.participants.map((p) => p.name).join('、')}` : `${selectedMeta.label} · 当前可用`}</span>
                </div>
              </div>
              <Tooltip title="来源与依据">
                <Button type="text" icon={<FileSearchOutlined />} onClick={() => setCitationOpen(true)} aria-label="来源与依据" />
              </Tooltip>
              {!isGroup && (
                <Button type="text" aria-label="资料" icon={<CommentOutlined />} onClick={() => { setCapabilityEmployeeNo(selected.participants[0]?.employee_no); setProfileOpen(true); }}>
                  资料
                </Button>
              )}
              {isGroup && addableMembers.length > 0 && <Button size="small" onClick={() => setAddMemberOpen(true)}>添加成员</Button>}
              <Popconfirm title="清空本会话？将删除本会话的消息与任务。" onConfirm={() => void handleClear()}>
                <Button size="small" danger>清空</Button>
              </Popconfirm>
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
              renderMessageExtra={renderMessageExtra}
              emptyState={
                <div className="mx-auto flex h-full max-w-[520px] flex-col items-center justify-center px-6 text-center">
                  <div className="grid h-16 w-16 place-items-center rounded-[18px] border border-line bg-surface text-2xl text-brand shadow-sm">{isGroup ? <TeamOutlined /> : <RobotOutlined />}</div>
                  <Typography.Title level={4} className="!mb-1 !mt-4">和 {selectedTitle} 开始协作</Typography.Title>
                  <Paragraph type="secondary" className="!mb-0">直接描述问题或工作目标。数字员工会按权限查阅知识，并保留可核验的执行记录。</Paragraph>
                </div>
              }
            />

            <div className="shrink-0 border-t border-line bg-surface px-4 pb-3 pt-2.5 sm:px-6">
              <Composer
                value={input}
                onChange={setInput}
                onSend={() => void send()}
                sending={sending}
                disabled={Boolean(liveExecution && executionIsActive(liveExecution.status))}
                placeholder={isGroup ? '描述一个任务，我来拆解安排给同事们…' : '发消息…'}
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
              <Button icon={<CommentOutlined />} onClick={() => setNewChatOpen(true)}>查找数字员工</Button>
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
        <Paragraph type="secondary">首版一次只与一位数字员工对话；你的数字分身会在需要时自主向专业数字员工求助。</Paragraph>
        <div className="max-h-80 overflow-y-auto">
          {([twin, ...employees].filter((e): e is Employee => e !== null)).map((emp) => {
            const meta = metaOf(emp.employee_no);
            const checked = selectedNos.includes(emp.employee_no);
            return (
              <div key={emp.employee_no} className="flex cursor-pointer items-center gap-3 rounded-control px-2 py-2 hover:bg-surface-subtle" onClick={() => setSelectedNos([emp.employee_no])}>
                <Avatar size={34} style={{ background: meta.bg, color: meta.color }}>{meta.emoji}</Avatar>
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
        {addableMembers.length === 0 ? <Empty description="没有可添加的数字员工" /> : addableMembers.map((emp) => {
          const meta = metaOf(emp.employee_no);
          return (
            <div key={emp.employee_no} className="flex cursor-pointer items-center gap-3 rounded-control px-2 py-2 hover:bg-surface-subtle" onClick={() => setMemberNos((prev) => prev.includes(emp.employee_no) ? prev.filter((n) => n !== emp.employee_no) : [...prev, emp.employee_no])}>
              <Avatar size={34} style={{ background: meta.bg, color: meta.color }}>{meta.emoji}</Avatar>
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
        onClose={() => { setProfileOpen(false); setCapabilityEmployeeNo(undefined); }}
        width={440}
        extra={capabilityEmployee?.type === 'twin' ? (
          <Button type="primary" size="small" icon={<UploadOutlined />} onClick={() => setSkillModalOpen(true)}>添加工作方法</Button>
        ) : null}
      >
        {capabilityEmployee && (
          <>
            <div className="flex items-center gap-3">
              <Avatar size={56} style={{ background: metaOf(capabilityEmployee.employee_no).bg, color: metaOf(capabilityEmployee.employee_no).color, fontSize: 24 }}>
                {metaOf(capabilityEmployee.employee_no).emoji}
              </Avatar>
              <div>
                <div className="text-lg font-semibold text-ink">{capabilityEmployee.name}</div>
                <Text type="secondary" style={{ fontSize: 12 }}>{capabilityEmployee.department} · {metaOf(capabilityEmployee.employee_no).label}</Text>
              </div>
            </div>
            <Paragraph className="mt-3 rounded-control bg-surface-subtle p-3">{capabilityEmployee.role_prompt || '（尚未配置职责与擅长方向）'}</Paragraph>
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
                    <Popconfirm title="确定删除该技能？" onConfirm={() => void removeSkill(skill)}>
                      <Button type="text" size="small" danger icon={<DeleteOutlined />} aria-label={`删除技能 ${skill.name}`} />
                    </Popconfirm>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </Drawer>

      {/* 引用预览侧栏 */}
      <CitationPreview cards={lastAssistantCards} open={citationOpen} onClose={() => setCitationOpen(false)} />

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
