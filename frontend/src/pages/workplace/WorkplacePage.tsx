import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  CheckCircleOutlined,
  CommentOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  DownOutlined,
  ExperimentOutlined,
  LoadingOutlined,
  PlusOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
  ToolOutlined,
  TeamOutlined,
  ThunderboltOutlined,
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
  Skill,
  TaskRun,
  Workflow,
  WorkplaceHome,
} from '@dwp/shared-schema';
import { api } from '../../api/client';
import { ErrorState, LoadingState } from '../../components/PageState';
import MarkdownText from '../../components/MarkdownText';
import { useCurrentUser } from '../../context/CurrentUserContext';
import { useAsyncData } from '../../hooks/useAsyncData';

const { Text, Paragraph } = Typography;

interface ContactMeta {
  label: string;
  emoji: string;
  color: string;
  bg: string;
}

const GROUP_META: Record<string, ContactMeta> = {
  twin: { label: '我的分身', emoji: '⭐', color: '#2f54eb', bg: '#eef4ff' },
  virtual: { label: '数字员工', emoji: '员', color: '#165dff', bg: '#e8f3ff' },
  rpa: { label: '自动化小助手', emoji: '⚙️', color: '#fa8c16', bg: '#fff7e6' },
};

const TASK_STATUS: Record<string, { label: string; color: string; icon: ReactNode }> = {
  pending: { label: '待执行', color: 'default', icon: <ClockCircleOutlined /> },
  running: { label: '执行中', color: 'processing', icon: <ExperimentOutlined /> },
  approval: { label: '待审批', color: 'warning', icon: <ClockCircleOutlined /> },
  completed: { label: '已完成', color: 'success', icon: <CheckCircleOutlined /> },
  denied: { label: '已拒绝', color: 'error', icon: <StopOutlined /> },
  failed: { label: '失败', color: 'error', icon: <StopOutlined /> },
};

const EXECUTION_MODE_LABEL: Record<string, string> = {
  harness: 'DeepSeek Harness',
  demo_adapter: 'Demo Adapter 降级',
  knowledge_adapter: 'Knowledge Adapter',
  pending: '待选择执行器',
  failed: '执行失败',
};

const TOOL_TYPE_LABEL: Record<string, string> = {
  mcp: 'MCP',
  workflow: 'Workflow',
  rpa: 'RPA',
  http: 'HTTP',
};

interface TaskCardProps {
  task: TaskRun;
  workerName: (employeeNo: string) => string;
  metaOf: (employeeNo: string) => ContactMeta;
  onApprove: (task: TaskRun, approve: boolean) => void;
  acting: boolean;
}

interface ExecutionStep {
  id: string;
  stage: string;
  title: string;
  detail: string;
  status: string;
  employeeId: string;
  knowledgeBaseId?: string;
  targetAgentId?: string;
  hitCount?: number;
}

interface LiveExecution {
  id: string;
  triggerSeq: number;
  employeeId: string;
  status: AgentExecution['status'];
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
  const steps: ExecutionStep[] = events
    .filter((event) => !['answer_chunk', 'answer_done', 'error'].includes(event.event_type) && event.title)
    .map((event) => ({
      id: String(event.event_seq),
      stage: event.stage,
      title: event.title,
      detail: event.detail,
      status: event.status,
      employeeId: event.actor_employee_id,
      knowledgeBaseId: event.knowledge_base_id ?? undefined,
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
    startedAt: new Date(run.started_at).getTime(),
    finishedAt: run.completed_at ? new Date(run.completed_at).getTime() : undefined,
    expanded: active || run.status === 'failed',
    steps,
    answers,
    error: failure?.detail || run.error_message || undefined,
    retryable: active ? run.retryable : false,
    resumeAfter: active && lastEvent ? String(lastEvent.event_seq) : undefined,
  };
}

function ExecutionCard({ run, employeeName, onToggle, onRetry }: {
  run: LiveExecution;
  employeeName: (id: string) => string;
  onToggle: () => void;
  onRetry: () => void;
}) {
  const running = ['queued', 'running', 'streaming', 'waiting_approval'].includes(run.status);
  const failed = run.status === 'failed';
  const elapsed = Math.max(0, Math.round(((run.finishedAt ?? Date.now()) - run.startedAt) / 1000));
  return (
    <section className={`execution-card ${failed ? 'failed' : running ? 'running' : 'completed'}`}>
      <button className="execution-head" type="button" onClick={onToggle}>
        <span className="execution-brand"><RobotOutlined /></span>
        <span className="execution-heading">
          <strong>{employeeName(run.employeeId) || '数字员工'}</strong>
          <span>{running ? 'Harness 正在运行' : failed ? '执行未完成' : `已完成 ${run.steps.length} 个步骤 · 用时 ${elapsed} 秒`}</span>
        </span>
        <Tag color={failed ? 'error' : running ? 'processing' : 'success'}>
          {running ? <><LoadingOutlined /> 执行中</> : failed ? '失败' : '已完成'}
        </Tag>
        {run.expanded ? <UpOutlined /> : <DownOutlined />}
      </button>
      {run.expanded && (
        <div className="execution-body">
          <div className="execution-safety-note">展示可审计的操作轨迹，不包含模型隐藏推理或知识原文</div>
          <div className="execution-timeline">
            {run.steps.map((step, index) => (
              <div className={`execution-step ${step.status}`} key={step.id}>
                <span className="execution-dot">
                  {step.status === 'failed' ? <StopOutlined /> : index === run.steps.length - 1 && running ? <LoadingOutlined /> : <CheckCircleOutlined />}
                </span>
                <div>
                  <div className="execution-step-title">{step.title}</div>
                  {step.detail && <div className="execution-step-detail">{step.detail}</div>}
                  <Space size={4} wrap>
                    {step.knowledgeBaseId && <Tag>{step.knowledgeBaseId}</Tag>}
                    {step.hitCount != null && <Tag color="blue">命中 {step.hitCount} 条</Tag>}
                    {step.targetAgentId && <Tag color="geekblue">委派至 {employeeName(step.targetAgentId)}</Tag>}
                  </Space>
                </div>
              </div>
            ))}
          </div>
          {failed && (
            <Alert
              type="error"
              showIcon
              message={run.error ?? '数字员工执行失败，可稍后重试'}
              action={run.retryable ? <Button size="small" onClick={onRetry}>重新执行</Button> : undefined}
            />
          )}
        </div>
      )}
      {Object.entries(run.answers).map(([employeeId, answer]) => answer ? (
        <div className="execution-answer" key={employeeId}>
          <div className="execution-answer-name">{employeeName(employeeId)}</div>
          <div className="execution-answer-text">{answer}<span className={running ? 'stream-caret' : ''} /></div>
        </div>
      ) : null)}
    </section>
  );
}

function TaskCard({ task, workerName, metaOf, onApprove, acting }: TaskCardProps) {
  const [expanded, setExpanded] = useState(task.status !== 'completed');
  const meta = TASK_STATUS[task.status] ?? TASK_STATUS.pending;
  return (
    <div className="wp-task-card">
      <div
        className="wp-task-head"
        role="button"
        tabIndex={0}
        onClick={() => setExpanded((value) => !value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setExpanded((value) => !value);
          }
        }}
      >
        <Avatar size={30} style={{ background: '#eef4ff', color: '#2f54eb' }} icon={<RobotOutlined />} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="wp-task-title">{task.request}</div>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {task.id} ·{' '}
            {task.source === 'agentteams'
              ? 'AgentTeams 团队协作 · Harness 驱动 · Policy/Gateway 工具调用'
              : '内置协作 · Harness 驱动 · Policy/Gateway 工具调用'}
          </Text>
        </div>
        <Space size={4}>
          <Tag icon={meta.icon} color={meta.color}>
            {meta.label}
          </Tag>
          <Button
            type="text"
            size="small"
            aria-label={expanded ? '收起任务详情' : '展开任务详情'}
            icon={expanded ? <UpOutlined /> : <DownOutlined />}
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((value) => !value);
            }}
          />
        </Space>
      </div>
      {expanded ? (
        <>
          <div className="subtask-list" style={{ padding: '10px 12px 0' }}>
            {task.subtasks.map((sub, index) => {
              const subMeta = TASK_STATUS[sub.status] ?? TASK_STATUS.pending;
              const workerMeta = metaOf(sub.worker_id);
              const runtimeMode = sub.runtime_mode ?? sub.execution_mode;
              return (
                <div className={`subtask ${sub.status}`} key={`${sub.worker_no}-${index}`}>
                  <div className="subtask-top">
                    <Space size={8}>
                      <Avatar
                        size={24}
                        style={{ background: workerMeta.bg, color: workerMeta.color, fontSize: 12 }}
                      >
                        {workerMeta.emoji}
                      </Avatar>
                      <Text strong>{workerName(sub.worker_id)}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {sub.summary}
                      </Text>
                    </Space>
                    <Space size={4}>
                      {runtimeMode && runtimeMode !== 'pending' && (
                        <Tag color={runtimeMode === 'harness' ? 'geekblue' : 'orange'}>
                          运行时：{EXECUTION_MODE_LABEL[runtimeMode] ?? runtimeMode}
                        </Tag>
                      )}
                      {sub.tool_name && (
                        <Tag color="cyan">
                          工具：{sub.tool_name}
                          {sub.tool_type && !sub.tool_name.toLowerCase().includes(sub.tool_type.toLowerCase())
                            ? ` · ${TOOL_TYPE_LABEL[sub.tool_type] ?? sub.tool_type}`
                            : ''}
                        </Tag>
                      )}
                      <Tag icon={subMeta.icon} color={subMeta.color}>
                        {subMeta.label}
                      </Tag>
                    </Space>
                  </div>
                  {sub.result && (
                    <Paragraph type="secondary" style={{ margin: '6px 0 0', fontSize: 12 }}>
                      {sub.result}
                    </Paragraph>
                  )}
                  {sub.runtime_summary && runtimeMode === 'harness' && (
                    <Paragraph type="secondary" style={{ margin: '4px 0 0', fontSize: 11 }}>
                      Harness 计划：{sub.runtime_summary}
                    </Paragraph>
                  )}
                  {(sub.collaboration_messages?.length ?? 0) > 0 && (
                    <div style={{ marginTop: 6 }}>
                      <Text type="secondary" style={{ fontSize: 11 }}>AgentTeams 协作记录</Text>
                      {sub.collaboration_messages?.map((item, messageIndex) => (
                        <Paragraph key={`${sub.worker_no}-collab-${messageIndex}`} style={{ margin: '2px 0', fontSize: 12 }}>
                          {item}
                        </Paragraph>
                      ))}
                    </div>
                  )}
                  {sub.approval && (
                    <Alert
                      type="warning"
                      showIcon
                      style={{ marginTop: 8 }}
                      message={`敏感操作需审批${sub.approval.policy_id ? `（${sub.approval.policy_id}）` : ''}`}
                    />
                  )}
                </div>
              );
            })}
          </div>
          {task.status === 'approval' && (
            <div className="approval-zone" style={{ margin: 10 }}>
              <Text strong style={{ marginRight: 12 }}>
                等待审批：
              </Text>
              <Space>
                <Button type="primary" loading={acting} onClick={() => onApprove(task, true)}>
                  批准
                </Button>
                <Button danger loading={acting} onClick={() => onApprove(task, false)}>
                  拒绝
                </Button>
              </Space>
            </div>
          )}
          {task.status === 'completed' && task.summary && (
            <div className="summary-card" style={{ margin: 10 }}>
              <Text strong>👑 Leader 汇总</Text>
              <Paragraph style={{ margin: '8px 0 0' }}>{task.summary}</Paragraph>
            </div>
          )}
        </>
      ) : (
        task.summary && <div className="wp-task-collapsed">{task.summary}</div>
      )}
    </div>
  );
}

const formatTime = (iso: string) => {
  const date = new Date(iso);
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return sameDay
    ? date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
};

export default function WorkplacePage() {
  const { actor } = useCurrentUser();
  const actorNo = actor.employee_no;

  const homeFetcher = useCallback(() => api.getWorkplace(actorNo), [actorNo]);
  const { data: home, loading, error, reload } = useAsyncData<WorkplaceHome>(homeFetcher);

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [convsLoading, setConvsLoading] = useState(true);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selectedWorkflow, setSelectedWorkflow] = useState<Workflow | null>(null);
  const refreshConversations = useCallback(() => {
    setConvsLoading(true);
    api
      .listConversations(actorNo)
      .then(setConversations)
      .catch(() => setConversations([]))
      .finally(() => setConvsLoading(false));
  }, [actorNo]);

  useEffect(() => {
    refreshConversations();
    api
      .listWorkflows()
      .then(setWorkflows)
      .catch(() => setWorkflows([]));
  }, [refreshConversations]);

  const [tab, setTab] = useState<'messages' | 'contacts' | 'guide'>('messages');
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
  const lastPrompt = useRef('');
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const [userScrolledUp, setUserScrolledUp] = useState(false);

  const [newChatOpen, setNewChatOpen] = useState(false);
  const [chatMode] = useState<'direct' | 'group'>('direct');
  const [groupTitle, setGroupTitle] = useState('');
  const [selectedNos, setSelectedNos] = useState<string[]>([]);

  const [skillDrawerOpen, setSkillDrawerOpen] = useState(false);
  const [capabilityEmployeeNo, setCapabilityEmployeeNo] = useState<string>();
  const [effectiveCapabilities, setEffectiveCapabilities] = useState<EffectiveCapabilities>();
  const [capabilityLoading, setCapabilityLoading] = useState(false);
  const [capabilityError, setCapabilityError] = useState<string>();
  const [skillModalOpen, setSkillModalOpen] = useState(false);
  const [skillForm] = Form.useForm();

  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const [memberNos, setMemberNos] = useState<string[]>([]);

  // 切换身份时重置会话选择
  useEffect(() => {
    setSelectedId(null);
    setSelected(null);
    setInput('');
    setPendingAfterSeq(null);
    setLiveExecution(null);
    setSavedExecutions([]);
  }, [actorNo]);

  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      return;
    }
    let active = true;
    api
      .getConversation(selectedId)
      .then((conv) => {
        if (active) setSelected(conv);
      })
      .catch(() => {
        if (active) setSelected(null);
      });
    return () => {
      active = false;
    };
  }, [selectedId]);

  // 页面切换或刷新后恢复该会话每一轮的安全执行轨迹。
  useEffect(() => {
    if (!selectedId) {
      setLiveExecution(null);
      setSavedExecutions([]);
      return;
    }
    let cancelled = false;
    api.getConversationRunHistory(selectedId).then((details) => {
      if (cancelled) return;
      const restored = details.map(restoreExecution);
      const activeRun = [...restored].reverse().find((run) => executionIsActive(run.status)) ?? null;
      connectedExecutionId.current = activeRun?.id;
      executionEventIds.current.clear();
      setLiveExecution(activeRun);
      setSavedExecutions(restored.filter((run) => !executionIsActive(run.status)));
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId || !liveExecution || ['completed', 'failed', 'cancelled'].includes(liveExecution.status)) return;
    if (connectedExecutionId.current !== liveExecution.id) {
      connectedExecutionId.current = liveExecution.id;
      executionEventIds.current.clear();
    }
    const source = new EventSource(
      api.conversationRunEventUrl(selectedId, liveExecution.id, liveExecution.resumeAfter),
      { withCredentials: true },
    );
    const progress = (event: MessageEvent) => {
      if (event.lastEventId && executionEventIds.current.has(event.lastEventId)) return;
      if (event.lastEventId) executionEventIds.current.add(event.lastEventId);
      const data = JSON.parse(event.data) as Record<string, unknown>;
      setLiveExecution((current) => {
        if (!current || current.id !== liveExecution.id) return current;
        const step: ExecutionStep = {
          id: event.lastEventId || `${Date.now()}-${current.steps.length}`,
          stage: String(data.stage ?? ''),
          title: String(data.title ?? '正在执行'),
          detail: String(data.detail ?? ''),
          status: String(data.status ?? 'running'),
          employeeId: String(data.employee_id ?? current.employeeId),
          knowledgeBaseId: data.knowledge_base_id ? String(data.knowledge_base_id) : undefined,
          targetAgentId: data.target_agent_id ? String(data.target_agent_id) : undefined,
          hitCount: typeof data.hit_count === 'number' ? data.hit_count : undefined,
        };
        return {
          ...current, status: 'running', expanded: true,
          steps: [...current.steps, step], resumeAfter: event.lastEventId || current.resumeAfter,
        };
      });
    };
    const delta = (event: MessageEvent) => {
      if (event.lastEventId && executionEventIds.current.has(event.lastEventId)) return;
      if (event.lastEventId) executionEventIds.current.add(event.lastEventId);
      const data = JSON.parse(event.data) as { employee_id?: string; delta?: string };
      setLiveExecution((current) => {
        if (!current || current.id !== liveExecution.id) return current;
        const employeeId = data.employee_id || current.employeeId;
        return {
          ...current,
          status: 'streaming',
          resumeAfter: event.lastEventId || current.resumeAfter,
          answers: { ...current.answers, [employeeId]: (current.answers[employeeId] ?? '') + (data.delta ?? '') },
        };
      });
    };
    const done = () => {
      source.close();
      setLiveExecution((current) => current?.id === liveExecution.id ? {
        ...current, status: 'completed', expanded: false, finishedAt: Date.now(), answers: {},
      } : current);
      api.getConversation(selectedId).then(setSelected).catch(() => undefined);
      void refreshConversations();
    };
    const failed = (event: Event) => {
      if (!(event instanceof MessageEvent) || !event.data) return;
      source.close();
      const data = JSON.parse(event.data) as { message?: string; retryable?: boolean };
      setLiveExecution((current) => current?.id === liveExecution.id ? {
        ...current, status: 'failed', expanded: true, finishedAt: Date.now(),
        error: data.message, retryable: data.retryable,
      } : current);
    };
    source.addEventListener('progress', progress);
    source.addEventListener('answer_delta', delta);
    source.addEventListener('answer_done', done);
    source.addEventListener('error', failed);
    return () => source.close();
  }, [liveExecution?.id, liveExecution?.status, refreshConversations, selectedId]);

  useEffect(() => {
    if (!liveExecution || executionIsActive(liveExecution.status)) return;
    setSavedExecutions((current) => [
      ...current.filter((run) => run.id !== liveExecution.id),
      liveExecution,
    ]);
  }, [liveExecution]);

  const streamedLength = liveExecution
    ? Object.values(liveExecution.answers).reduce((total, answer) => total + answer.length, 0)
    : 0;
  useEffect(() => {
    if (userScrolledUp) return;
    const frame = requestAnimationFrame(() => {
      const node = chatScrollRef.current;
      if (node) node.scrollTop = node.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [selected?.messages.length, streamedLength, userScrolledUp]);

  // 等待后台回复，或任务尚未进入 completed / approval / failed / denied 时持续轮询。
  const hasPollableTask = selected?.tasks.some((task) =>
    ['pending', 'parsing', 'running'].includes(task.status),
  );
  useEffect(() => {
    if ((!hasPollableTask && !pendingReply) || !selectedId) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      try {
        const conv = await api.getConversation(selectedId);
        if (cancelled) return;
        setSelected(conv);
        const hasMatchingReply =
          pendingAfterSeq != null &&
          conv.messages.some((m) => m.role === 'assistant' && m.seq > pendingAfterSeq);
        // trigger_message_seq 是前端在拿到 Task ID 前关联本次消息和任务卡片的稳定键。
        const matchingTask =
          pendingAfterSeq == null
            ? undefined
            : conv.tasks.find((task) => task.trigger_message_seq === pendingAfterSeq);
        const responseAppeared = hasMatchingReply || matchingTask != null;
        if (responseAppeared) setPendingAfterSeq(null);
        await refreshConversations();

        const taskStillRunning = conv.tasks.some((task) =>
          ['pending', 'parsing', 'running'].includes(task.status),
        );
        const replyStillPending = pendingAfterSeq != null && !responseAppeared;
        if (!cancelled && (taskStillRunning || replyStillPending)) {
          timer = setTimeout(poll, 2500);
        }
      } catch {
        // 临时网络失败不终止轮询；保留当前状态并继续重试。
        if (!cancelled) timer = setTimeout(poll, 2500);
      }
    };

    timer = setTimeout(poll, 2500);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [hasPollableTask, pendingReply, pendingAfterSeq, selectedId, refreshConversations]);

  const twin = home?.twin ?? null;
  const employees = home?.available_employees ?? [];
  const skills = home?.skills ?? [];

  const capabilityEmployee = [twin, ...employees].find((employee) => employee?.employee_no === capabilityEmployeeNo) ?? null;

  useEffect(() => {
    if (!skillDrawerOpen || !capabilityEmployeeNo) return;
    let cancelled = false;
    setCapabilityLoading(true);
    setCapabilityError(undefined);
    api.getEffectiveCapabilities(capabilityEmployeeNo)
      .then((result) => { if (!cancelled) setEffectiveCapabilities(result); })
      .catch((cause: Error) => { if (!cancelled) setCapabilityError(cause.message); })
      .finally(() => { if (!cancelled) setCapabilityLoading(false); });
    return () => { cancelled = true; };
  }, [capabilityEmployeeNo, skillDrawerOpen]);

  const openCapability = (employeeNo: string) => {
    setCapabilityEmployeeNo(employeeNo);
    setEffectiveCapabilities(undefined);
    setSkillDrawerOpen(true);
  };

  const metaOf = useCallback(
    (employeeNo: string): ContactMeta => {
      if (twin && employeeNo === twin.employee_no) return GROUP_META.twin;
      const emp = employees.find((e) => e.employee_no === employeeNo);
      if (emp?.type === 'rpa') return GROUP_META.rpa;
      if (emp?.type === 'virtual') return GROUP_META.virtual;
      return { label: employeeNo, emoji: '👤', color: '#13c2c2', bg: '#e6fffb' };
    },
    [twin, employees],
  );

  const contactGroups = useMemo(() => {
    if (!home) return [];
    return [
      { key: 'twin', label: '我的分身', items: [home.twin] },
      { key: 'virtual', label: '数字员工', items: home.available_employees.filter((e) => e.type === 'virtual') },
      { key: 'rpa', label: '自动化小助手', items: home.available_employees.filter((e) => e.type === 'rpa') },
    ]
      .map((group) => ({
        ...group,
        items: group.items.filter(
          (emp) =>
            !keyword ||
            emp.name.includes(keyword) ||
            emp.employee_no.includes(keyword) ||
            emp.department.includes(keyword) ||
            emp.owner_human_no.includes(keyword) ||
            (emp.owner_name ?? '').includes(keyword) ||
            emp.role_prompt.includes(keyword),
        ),
      }))
      .filter((group) => group.items.length > 0);
  }, [home, keyword]);

  const twinConv = useMemo(
    () =>
      twin
        ? conversations.find(
            (conv) => conv.kind === 'direct' && conv.participants.some((p) => p.employee_no === twin.employee_no),
          )
        : undefined,
    [conversations, twin],
  );
  const otherConvs = useMemo(
    () => conversations.filter((conv) => conv.id !== twinConv?.id && conv.kind === 'direct'),
    [conversations, twinConv],
  );
  const visibleConvs = useMemo(
    () =>
      keyword
        ? otherConvs.filter((conv) => {
            const name = conv.kind === 'group' ? conv.title : conv.participants[0]?.name ?? '';
            return name.includes(keyword) || conv.last_message.includes(keyword);
          })
        : otherConvs,
    [keyword, otherConvs],
  );
  const visibleWorkflows = useMemo(
    () =>
      keyword
        ? workflows.filter((wf) =>
            wf.name.includes(keyword) ||
            wf.description.includes(keyword) ||
            wf.steps.some((step) => step.includes(keyword)) ||
            wf.authorized_employees.some((employee) => employee.name.includes(keyword) || employee.employee_no.includes(keyword)),
          )
        : workflows,
    [keyword, workflows],
  );

  const copyDemoPrompt = async (workflow: Workflow) => {
    try {
      await navigator.clipboard.writeText(workflow.demo_prompt);
      message.success('示例指令已复制，去群聊粘贴试试');
    } catch {
      message.warning('复制失败，请手动选择示例指令');
    }
  };

  const openDirect = async (employeeNo: string) => {
    setTab('messages');
    const existing = conversations.find(
      (conv) => conv.kind === 'direct' && conv.participants.some((p) => p.employee_no === employeeNo),
    );
    if (existing) {
      setSelectedId(existing.id);
      return;
    }
    try {
      const conv = await api.createConversation({
        actor_no: actorNo,
        kind: 'direct',
        participant_employee_nos: [employeeNo],
      });
      await refreshConversations();
      setSelectedId(conv.id);
    } catch (err) {
      message.error(`发起会话失败：${(err as Error).message}`);
    }
  };

  const send = async (overrideText?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || sending || (liveExecution && ['queued', 'running', 'streaming', 'waiting_approval'].includes(liveExecution.status)) || !selectedId) return;
    lastPrompt.current = text;
    setInput('');
    setSending(true);
    setChatError(undefined);
    // 先本地渲染自己的消息，成员思考期间立即可见；回复到达后用服务端会话整体替换
    setSelected((prev) => {
      if (!prev) return prev;
      const lastSeq = prev.messages.reduce((max, m) => Math.max(max, m.seq), 0);
      return {
        ...prev,
        messages: [
          ...prev.messages,
          {
            id: -Date.now(),
            conversation_id: prev.id,
            participant_no: actorNo,
            participant_name: actor.name,
            role: 'user',
            content: text,
            tool_cards: [],
            seq: lastSeq + 1,
          },
        ],
      };
    });
    try {
      const run = await api.startConversationRun(selectedId, actorNo, text);
      setSelected(run.conversation);
      setPendingAfterSeq(null);
      executionEventIds.current.clear();
      connectedExecutionId.current = run.execution_id;
      const primary = run.conversation.participants[0]?.employee_no ?? '';
      setLiveExecution({
        id: run.execution_id,
        triggerSeq: run.trigger_message_seq,
        employeeId: primary,
        status: 'queued',
        startedAt: Date.now(),
        expanded: true,
        steps: [],
        answers: {},
      });
      await refreshConversations();
    } catch (err) {
      setInput(text);
      setChatError((err as Error).message);
      // 发送失败：撤掉本地临时消息，避免与恢复在输入框的文本重复
      setSelected((prev) =>
        prev ? { ...prev, messages: prev.messages.filter((m) => m.id >= 0) } : prev,
      );
    } finally {
      setSending(false);
    }
  };

  const workerName = (employeeNo: string) => {
    const participant = selected?.participants.find((p) => p.employee_no === employeeNo);
    if (participant) return participant.name;
    if (twin && employeeNo === twin.employee_no) return twin.name;
    const emp = employees.find((e) => e.employee_no === employeeNo);
    return emp?.name ?? employeeNo;
  };

  const handleApprove = async (task: TaskRun, approve: boolean) => {
    if (!selectedId || acting) return;
    setActing(true);
    try {
      await api.approveTask(task.id, approve, actorNo);
      const conv = await api.getConversation(selectedId);
      setSelected(conv);
      await refreshConversations();
    } catch (err) {
      message.error(`审批失败：${(err as Error).message}`);
    } finally {
      setActing(false);
    }
  };

  const handleClear = async () => {
    if (!selectedId) return;
    try {
      await api.clearConversation(selectedId, actorNo);
      setPendingAfterSeq(null);
      setLiveExecution(null);
      setSavedExecutions([]);
      const conv = await api.getConversation(selectedId);
      setSelected(conv);
      await refreshConversations();
    } catch (err) {
      message.error(`清空失败：${(err as Error).message}`);
    }
  };

  const submitNewChat = async () => {
    if (chatMode === 'direct' && selectedNos.length !== 1) {
      message.warning('请选择 1 位联系人');
      return;
    }
    if (chatMode === 'group' && selectedNos.length === 0) {
      message.warning('请至少选择 1 位数字员工');
      return;
    }
    try {
      const conv = await api.createConversation({
        actor_no: actorNo,
        kind: chatMode,
        title: chatMode === 'group' && groupTitle.trim() ? groupTitle.trim() : undefined,
        participant_employee_nos: selectedNos,
      });
      setNewChatOpen(false);
      setSelectedNos([]);
      setGroupTitle('');
      await refreshConversations();
      setSelectedId(conv.id);
      setTab('messages');
    } catch (err) {
      message.error(`创建会话失败：${(err as Error).message}`);
    }
  };

  const submitSkill = async () => {
    const values = await skillForm.validateFields();
    try {
      await api.createSkill({
        actor_no: actorNo,
        name: values.name,
        description: values.description ?? '',
        content: values.content ?? '',
      });
      setSkillModalOpen(false);
      skillForm.resetFields();
      reload();
      message.success('个人工作方法已保存，并会用于我的数字分身');
    } catch (err) {
      message.error(`上传失败：${(err as Error).message}`);
    }
  };

  const toggleSkill = async (skill: Skill, enabled: boolean) => {
    try {
      await api.updateSkill(skill.id, actorNo, { status: enabled ? 'active' : 'disabled' });
      reload();
    } catch (err) {
      message.error(`更新失败：${(err as Error).message}`);
    }
  };

  const removeSkill = async (skill: Skill) => {
    try {
      await api.deleteSkill(skill.id, actorNo);
      reload();
    } catch (err) {
      message.error(`删除失败：${(err as Error).message}`);
    }
  };

  const submitAddMembers = async () => {
    if (!selectedId) return;
    try {
      let current = selected;
      for (const no of memberNos) {
        current = await api.addConversationParticipant(selectedId, no);
      }
      setSelected(current);
      setAddMemberOpen(false);
      setMemberNos([]);
      await refreshConversations();
    } catch (err) {
      message.error(`添加成员失败：${(err as Error).message}`);
    }
  };

  const pickEmployee = (no: string) => {
    if (chatMode === 'direct') {
      setSelectedNos([no]);
      return;
    }
    setSelectedNos((prev) => (prev.includes(no) ? prev.filter((n) => n !== no) : [...prev, no]));
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
      list.push(task);
      taskGroups.set(task.trigger_message_seq, list);
    } else {
      unattachedTasks.push(task);
    }
  }
  const selectedTitle =
    selected && (selected.kind === 'group' ? selected.title || '协作空间' : selected.participants[0]?.name ?? '');
  const selectedMeta = selected ? metaOf(selected.participants[0]?.employee_no ?? '') : GROUP_META.twin;
  const addableMembers = employees.filter(
    (emp) => !chatParticipants.some((p) => p.employee_no === emp.employee_no),
  );

  return (
    <div className="workplace">
      {/* 左栏：会话/通讯录 */}
      <aside className="wp-sidebar">
        <div className="wp-sidebar-head">
          <Space size={10}>
            <Avatar size={36} style={{ background: '#2f54eb' }}>
              {actor.name.slice(0, 1)}
            </Avatar>
            <div style={{ minWidth: 0 }}>
              <div className="wp-sidebar-title">我的职场</div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {actor.name} · {actor.department}
              </Text>
            </div>
          </Space>
            <Input
              allowClear
              placeholder={tab === 'messages' ? '搜索会话' : tab === 'contacts' ? '搜索联系人' : '搜索使用指南'}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            style={{ marginTop: 12 }}
          />
          <div className="wp-tabs">
            <Button
              type={tab === 'messages' ? 'primary' : 'text'}
              size="small"
              onClick={() => {
                setTab('messages');
                setKeyword('');
              }}
            >
              消息
            </Button>
            <Button
              type={tab === 'contacts' ? 'primary' : 'text'}
              size="small"
              onClick={() => {
                setTab('contacts');
                setKeyword('');
              }}
            >
              通讯录
            </Button>
            <div style={{ flex: 1 }} />
            <Button
              type="primary"
              shape="circle"
              size="small"
              icon={<PlusOutlined />}
              aria-label="发起会话"
              onClick={() => setNewChatOpen(true)}
            />
          </div>
        </div>

        <div className="wp-list">
          {tab === 'messages' ? (
            <>
              {!keyword && twin && (
                <div
                  className="wp-row pinned"
                  role="button"
                  tabIndex={0}
                  onClick={() => void openDirect(twin.employee_no)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      void openDirect(twin.employee_no);
                    }
                  }}
                >
                  <Avatar size={42} style={{ background: GROUP_META.twin.bg, color: GROUP_META.twin.color }}>
                    {GROUP_META.twin.emoji}
                  </Avatar>
                  <div className="wp-row-main">
                    <div className="wp-row-top">
                      <span className="wp-row-name">{twin.name}</span>
                      {twinConv && <span className="wp-row-time">{formatTime(twinConv.updated_at)}</span>}
                    </div>
                    <div className="wp-row-preview">
                      {twinConv ? twinConv.last_message : '开始和我的分身聊聊吧'}
                    </div>
                  </div>
                  <Tag className="wp-pinned-tag" color="blue">
                    我的分身
                  </Tag>
                </div>
              )}
              {visibleConvs.length === 0 && !convsLoading && (
                <Empty
                  style={{ marginTop: 48 }}
                  description={keyword ? '没有找到相关会话' : '还没有会话，点 + 发起一个吧'}
                />
              )}
              {visibleConvs.map((conv) => {
                const isGroupConv = conv.kind === 'group';
                const participant = conv.participants[0];
                const meta = isGroupConv ? GROUP_META.twin : metaOf(participant?.employee_no ?? '');
                return (
                  <div
                    key={conv.id}
                    className={`wp-row ${selectedId === conv.id ? 'active' : ''}`}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedId(conv.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setSelectedId(conv.id);
                      }
                    }}
                  >
                    {isGroupConv ? (
                      <Avatar size={42} style={{ background: '#e6fffb', color: '#13c2c2' }}>
                        <TeamOutlined />
                      </Avatar>
                    ) : (
                      <Avatar size={42} style={{ background: meta.bg, color: meta.color }}>
                        {meta.emoji}
                      </Avatar>
                    )}
                    <div className="wp-row-main">
                      <div className="wp-row-top">
                        <span className="wp-row-name">
                          {isGroupConv ? `${conv.title || '协作空间'}（${conv.participants.length}）` : participant?.name ?? ''}
                        </span>
                        <span className="wp-row-time">{formatTime(conv.updated_at)}</span>
                      </div>
                      <div className="wp-row-preview">{conv.last_message || '暂无消息'}</div>
                    </div>
                  </div>
                );
              })}
            </>
          ) : tab === 'contacts' ? (
            <>
              <div className="wp-contacts-head">
                <Text type="secondary" style={{ fontSize: 12 }}>
                  选择一位同事开始知识问答
                </Text>
                <Button size="small" type="link" onClick={() => setNewChatOpen(true)}>
                  新会话
                </Button>
              </div>
              {contactGroups.map((group) => (
                <div key={group.key} className="wp-contact-group">
                  <div className="wp-contact-label">{group.label}</div>
                  {group.items.map((emp) => {
                    const meta = metaOf(emp.employee_no);
                    return (
                      <div key={emp.employee_no} className="wp-contact-row wp-employee-row">
                        <button
                          type="button"
                          className="wp-contact-avatar-button"
                          aria-label={`与${emp.name}聊天`}
                          onClick={() => void openDirect(emp.employee_no)}
                        >
                          <Avatar size={38} style={{ background: meta.bg, color: meta.color }}>
                            {meta.emoji}
                          </Avatar>
                        </button>
                        <div className="wp-row-main">
                          <button
                            type="button"
                            className="wp-contact-name-link"
                            onClick={() => void openDirect(emp.employee_no)}
                          >
                            {emp.name}
                          </button>
                          <div className="wp-row-preview">
                            {emp.employee_no} · {emp.department || '未设置部门'}
                          </div>
                          <div className="wp-row-preview">
                            负责人：{emp.owner_name || emp.owner_human_no}（{emp.owner_human_no}）
                          </div>
                        </div>
                        <Space size={2} className="wp-contact-actions">
                          <Tooltip title="查看能力">
                            <Button
                              type="text"
                              shape="circle"
                              size="small"
                              aria-label={`能力 ${emp.name}`}
                              icon={<ToolOutlined />}
                              onClick={() => openCapability(emp.employee_no)}
                            />
                          </Tooltip>
                          <Tooltip title="发起私聊">
                            <Button
                              className="wp-chat-action"
                              type="text"
                              shape="circle"
                              size="small"
                              aria-label="私聊"
                              icon={<CommentOutlined />}
                              onClick={() => void openDirect(emp.employee_no)}
                            />
                          </Tooltip>
                        </Space>
                      </div>
                    );
                  })}
                </div>
              ))}
              {contactGroups.length === 0 && <Empty style={{ marginTop: 48 }} description="没有找到相关联系人" />}
            </>
          ) : (
            <>
              <div className="wp-contacts-head">
                <Text type="secondary" style={{ fontSize: 12 }}>
                  使用指南 · 了解可由数字员工执行的流程、步骤和示例指令
                </Text>
              </div>
              {visibleWorkflows.map((workflow) => {
                const isRpa = workflow.type === 'rpa';
                const meta = isRpa ? GROUP_META.rpa : GROUP_META.virtual;
                return (
                  <div
                    key={workflow.plugin_id}
                    className="wp-contact-row clickable-card"
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedWorkflow(workflow)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setSelectedWorkflow(workflow);
                      }
                    }}
                  >
                    <Avatar size={38} style={{ background: meta.bg, color: meta.color }}>
                      {isRpa ? <ThunderboltOutlined /> : <ExperimentOutlined />}
                    </Avatar>
                    <div className="wp-row-main">
                      <div className="wp-row-top">
                        <span className="wp-row-name">{workflow.name}</span>
                        <Tag color={isRpa ? 'orange' : 'purple'} style={{ marginInlineEnd: 0 }}>
                          {isRpa ? 'RPA' : '流程'}
                        </Tag>
                      </div>
                      <div className="wp-row-preview">{workflow.description}</div>
                      {workflow.owner_employee && (
                        <div className="wp-row-preview">
                          由 {workflow.owner_employee.name} 处理
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
              {visibleWorkflows.length === 0 && <Empty style={{ marginTop: 48 }} description="没有找到相关指南" />}
            </>
          )}
        </div>
      </aside>

      {/* 右栏：对话窗口 */}
      <main className="wp-chat">
        {selected ? (
          <>
            <div className="wp-chat-head">
              {isGroup ? (
                <Avatar size={40} style={{ background: '#e6fffb', color: '#13c2c2' }}>
                  <TeamOutlined />
                </Avatar>
              ) : (
                <Avatar size={40} style={{ background: selectedMeta.bg, color: selectedMeta.color }}>
                  {selectedMeta.emoji}
                </Avatar>
              )}
              <div style={{ minWidth: 0 }}>
                <div className="wp-chat-title">{selectedTitle}</div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {isGroup ? `${selected.participants.length} 人 · ${selected.participants.map((p) => p.name).join('、')}` : selectedMeta.label}
                </Text>
              </div>
              <div style={{ flex: 1 }} />
              <Popconfirm
                title="清空本会话？将删除本会话的消息与任务，不影响其他协作空间。"
                onConfirm={() => void handleClear()}
              >
                <Button size="small" danger>
                  清空会话
                </Button>
              </Popconfirm>
              {isGroup && addableMembers.length > 0 && (
                <Button size="small" onClick={() => setAddMemberOpen(true)}>
                  添加成员
                </Button>
              )}
              {!isGroup && selected.participants[0]?.employee_no === twin?.employee_no && (
                <Button size="small" onClick={() => setSkillDrawerOpen(true)}>
                  分身资料
                </Button>
              )}
            </div>

            <div
              className="wp-chat-scroll"
              ref={chatScrollRef}
              onScroll={(event) => {
                const node = event.currentTarget;
                setUserScrolledUp(node.scrollHeight - node.scrollTop - node.clientHeight > 80);
              }}
            >
              {selected.messages.length === 0 && (
                <div className="wp-empty">
                  <div className="wp-empty-emoji">{isGroup ? '🤝' : selectedMeta.emoji}</div>
                  <Paragraph>打个招呼，开始今天的协作吧。</Paragraph>
                </div>
              )}
              {selected.messages.map((msg) => {
                const mine = msg.role === 'user';
                const messageExecution =
                  (liveExecution?.triggerSeq === msg.seq ? liveExecution : undefined) ??
                  savedExecutions.find((run) => run.triggerSeq === msg.seq);
                return (
                  <div key={msg.id}>
                    <div className={`wp-msg ${mine ? 'me' : ''}`}>
                      {!mine && (
                        <Avatar size={34} style={{ background: metaOf(msg.participant_no).bg, color: metaOf(msg.participant_no).color }}>
                          {metaOf(msg.participant_no).emoji}
                        </Avatar>
                      )}
                      <div className="wp-msg-col">
                        {!mine && isGroup && (
                          <div className="wp-msg-name">
                            {msg.participant_name}
                            {/完成|TASK_COMPLETED|交付/.test(msg.content) && (
                              <span className="wp-feedback-ok">✅ 已完成</span>
                            )}
                            {!/完成|TASK_COMPLETED|交付/.test(msg.content) && /收到|开始|处理|认领/.test(msg.content) && (
                              <span className="wp-feedback-run">⏳ 执行中</span>
                            )}
                          </div>
                        )}
                        <div className={`wp-bubble ${mine ? 'me' : 'other'}`}>
                          <MarkdownText text={msg.content} />
                        </div>
                        {msg.tool_cards.length > 0 && (
                          <div className="tool-cards">
                            {(msg.tool_cards as Array<{ name: string; decision: string; policy_id?: string | null }>).map(
                              (card, index) => (
                                <div className={`tool-card ${card.decision}`} key={`${msg.id}-${index}`}>
                                  <div className="tool-card-head">{card.name}</div>
                                  <div className="tool-card-sub">决策：{card.decision}</div>
                                </div>
                              ),
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                    {isGroup &&
                      taskGroups
                        .get(msg.seq)
                        ?.map((task) => (
                          <TaskCard
                            key={task.id}
                            task={task}
                            workerName={workerName}
                            metaOf={metaOf}
                            onApprove={handleApprove}
                            acting={acting}
                          />
                        ))}
                    {messageExecution && (
                      <ExecutionCard
                        run={messageExecution}
                        employeeName={workerName}
                        onToggle={() => {
                          if (liveExecution?.id === messageExecution.id) {
                            setLiveExecution((current) => current ? { ...current, expanded: !current.expanded } : current);
                          } else {
                            setSavedExecutions((current) => current.map((run) =>
                              run.id === messageExecution.id ? { ...run, expanded: !run.expanded } : run,
                            ));
                          }
                        }}
                        onRetry={() => void send(lastPrompt.current)}
                      />
                    )}
                  </div>
                );
              })}
              {sending && (
                <div className="wp-thinking">
                  <RobotOutlined spin />
                  正在安全提交任务…
                </div>
              )}
              {isGroup && unattachedTasks.length > 0 && (
                <div className="wp-tasks">
                  {unattachedTasks.map((task) => (
                    <TaskCard
                      key={task.id}
                      task={task}
                      workerName={workerName}
                      metaOf={metaOf}
                      onApprove={handleApprove}
                      acting={acting}
                    />
                  ))}
                </div>
              )}
              {chatError && (
                <div className="wp-chat-error">发送失败：{chatError}，消息已保留在输入框</div>
              )}
              {userScrolledUp && (
                <Button
                  className="scroll-bottom"
                  size="small"
                  onClick={() => {
                    const node = chatScrollRef.current;
                    if (node) node.scrollTop = node.scrollHeight;
                    setUserScrolledUp(false);
                  }}
                >
                  回到底部
                </Button>
              )}
            </div>

            <div className="wp-chat-input">
              <Input.TextArea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPressEnter={(e) => {
                  if (!e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                placeholder={isGroup ? '描述一个任务，我来拆解安排给同事们…' : '发消息…'}
                autoSize={{ minRows: 1, maxRows: 4 }}
                disabled={Boolean(liveExecution && ['queued', 'running', 'streaming', 'waiting_approval'].includes(liveExecution.status))}
              />
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={() => void send()}
                loading={sending}
                disabled={Boolean(liveExecution && ['queued', 'running', 'streaming', 'waiting_approval'].includes(liveExecution.status))}
              >
                发送
              </Button>
            </div>
          </>
        ) : (
          <div className="wp-empty wp-empty-page">
            <div className="wp-empty-emoji">👋</div>
            <Typography.Title level={4} style={{ margin: 0 }}>
              早上好，{actor.name}
            </Typography.Title>
            <Paragraph type="secondary">可以和我的分身聊聊，或找一位数字员工帮忙。</Paragraph>
            <Space>
              {twin && (
                <Button type="primary" icon={<RobotOutlined />} onClick={() => void openDirect(twin.employee_no)}>
                  和我的分身聊聊
                </Button>
              )}
              <Button icon={<CommentOutlined />} onClick={() => setNewChatOpen(true)}>
                查找数字员工
              </Button>
            </Space>
          </div>
        )}
      </main>

      {/* 发起会话弹窗 */}
      <Modal
        title="发起会话"
        open={newChatOpen}
        onCancel={() => {
          setNewChatOpen(false);
          setSelectedNos([]);
          setGroupTitle('');
        }}
        onOk={() => void submitNewChat()}
        okText="创建"
      >
        <Paragraph type="secondary">
          首版一次只与一位数字员工对话；你的数字分身会在需要时自主向专业数字员工求助。
        </Paragraph>
        <div className="wp-modal-contacts">
          {contactGroups.map((group) => (
            <div key={group.key} className="wp-contact-group">
              <div className="wp-contact-label">{group.label}</div>
              {group.items.map((emp) => {
                const meta = metaOf(emp.employee_no);
                const checked = selectedNos.includes(emp.employee_no);
                return (
                  <div key={emp.employee_no} className="wp-contact-row" onClick={() => pickEmployee(emp.employee_no)}>
                    <Avatar size={34} style={{ background: meta.bg, color: meta.color }}>
                      {meta.emoji}
                    </Avatar>
                    <div className="wp-row-main">
                      <div className="wp-row-name">{emp.name}</div>
                      <div className="wp-row-preview">{emp.department}</div>
                    </div>
                    <Checkbox checked={checked} onChange={() => pickEmployee(emp.employee_no)} />
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </Modal>

      {/* 添加群成员弹窗 */}
      <Modal
        title="添加成员"
        open={addMemberOpen}
        onCancel={() => {
          setAddMemberOpen(false);
          setMemberNos([]);
        }}
        onOk={() => void submitAddMembers()}
        okText="添加"
        okButtonProps={{ disabled: memberNos.length === 0 }}
      >
        {addableMembers.length === 0 ? (
          <Empty description="没有可添加的数字员工" />
        ) : (
          addableMembers.map((emp) => {
            const meta = metaOf(emp.employee_no);
            return (
              <div
                key={emp.employee_no}
                className="wp-contact-row"
                onClick={() =>
                  setMemberNos((prev) => (prev.includes(emp.employee_no) ? prev.filter((n) => n !== emp.employee_no) : [...prev, emp.employee_no]))
                }
              >
                <Avatar size={34} style={{ background: meta.bg, color: meta.color }}>
                  {meta.emoji}
                </Avatar>
                <div className="wp-row-main">
                  <div className="wp-row-name">{emp.name}</div>
                  <div className="wp-row-preview">{emp.role_prompt || emp.department}</div>
                </div>
                <Checkbox checked={memberNos.includes(emp.employee_no)} />
              </div>
            );
          })
        )}
      </Modal>

      {/* 数字员工能力档案；分身额外支持个人工作方法管理 */}
      <Drawer
        title="能力档案"
        open={skillDrawerOpen}
        onClose={() => { setSkillDrawerOpen(false); setCapabilityEmployeeNo(undefined); }}
        width={440}
        extra={capabilityEmployee?.type === 'twin' ? (
          <Button type="primary" size="small" icon={<UploadOutlined />} onClick={() => setSkillModalOpen(true)}>
            添加工作方法
          </Button>
        ) : null}
      >
        {capabilityEmployee && (
          <>
            <div className="wp-drawer-hero">
              <Avatar size={56} style={{ background: metaOf(capabilityEmployee.employee_no).bg, color: metaOf(capabilityEmployee.employee_no).color, fontSize: 24 }}>
                {metaOf(capabilityEmployee.employee_no).emoji}
              </Avatar>
              <div>
                <div className="wp-chat-title">{capabilityEmployee.name}</div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {capabilityEmployee.department} · {metaOf(capabilityEmployee.employee_no).label}
                </Text>
              </div>
            </div>
            <Paragraph className="wp-persona">{capabilityEmployee.role_prompt || '（尚未配置职责与擅长方向）'}</Paragraph>

            {capabilityLoading && <LoadingState rows={4} />}
            {capabilityError && <Alert type="error" showIcon message="能力状态加载失败" description={capabilityError} />}
            {effectiveCapabilities && (
              <>
                <div className="wp-drawer-section">
                  <div className="wp-drawer-section-title">当前运行状态</div>
                  <Space size={[6, 6]} wrap>
                    <Tag color={['ready', 'busy'].includes(effectiveCapabilities.runtime_state) ? 'green' : 'red'}>
                      Harness {['ready', 'busy'].includes(effectiveCapabilities.runtime_state) ? '已就绪' : effectiveCapabilities.runtime_state}
                    </Tag>
                    <Tag color={effectiveCapabilities.knowledge_mode === 'internal' ? 'blue' : 'gold'}>
                      {effectiveCapabilities.knowledge_mode === 'internal' ? '内部知识引擎' : 'Mock 知识'}
                    </Tag>
                    <Tag color="blue">{effectiveCapabilities.available_count} 项可用</Tag>
                  </Space>
                </div>
                <div className="wp-drawer-section">
                  <div className="wp-drawer-section-title">能做什么</div>
                  {effectiveCapabilities.capabilities.filter((item) => item.authorized).length === 0 && <Empty description="暂未授权能力" />}
                  {effectiveCapabilities.capabilities.filter((item) => item.authorized).map((item) => (
                    <div className="wp-skill" key={item.id}>
                      <div style={{ minWidth: 0 }}>
                        <div className="wp-row-name">{item.name}</div>
                        <div className="wp-row-preview">{item.description}</div>
                        {item.example_prompts[0] && <div className="wp-row-preview">示例：{item.example_prompts[0]}</div>}
                      </div>
                      <Tag color={item.status === 'available' ? 'success' : item.status === 'approval' ? 'warning' : 'error'}>
                        {item.status === 'available' ? '可用' : item.status === 'approval' ? '需审批' : '环境未就绪'}
                      </Tag>
                    </div>
                  ))}
                </div>
              </>
            )}

            {capabilityEmployee.type === 'twin' && <div className="wp-drawer-section">
              <div className="wp-drawer-section-title">
                个人工作方法（{skills.length}）
                <Text type="secondary" style={{ fontSize: 12 }}>已启用 {skills.filter((s) => s.status === 'active').length} 项</Text>
              </div>
              {skills.length === 0 && <Empty description="还没有个人工作方法，点击右上角添加" />}
              {skills.map((skill) => (
                <div key={skill.id} className="wp-skill">
                  <div style={{ minWidth: 0 }}>
                    <div className="wp-row-name">{skill.name}</div>
                    <div className="wp-row-preview">{skill.description || skill.content.slice(0, 60)}</div>
                  </div>
                  <Switch
                    size="small"
                    checked={skill.status === 'active'}
                    onChange={(checked) => void toggleSkill(skill, checked)}
                    aria-label={`切换技能 ${skill.name}`}
                  />
                  <Popconfirm title="确定删除该技能？" onConfirm={() => void removeSkill(skill)}>
                    <Button type="text" size="small" danger icon={<DeleteOutlined />} aria-label={`删除技能 ${skill.name}`} />
                  </Popconfirm>
                </div>
              ))}
            </div>}
          </>
        )}
      </Drawer>

      {/* 使用指南详情抽屉 */}
      <Drawer
        title="使用指南"
        open={selectedWorkflow !== null}
        onClose={() => setSelectedWorkflow(null)}
        width={400}
      >
        {selectedWorkflow && (
          <>
            <div className="wp-drawer-hero">
              <Avatar
                size={52}
                style={{
                  background: selectedWorkflow.type === 'rpa' ? GROUP_META.rpa.bg : GROUP_META.virtual.bg,
                  color: selectedWorkflow.type === 'rpa' ? GROUP_META.rpa.color : GROUP_META.virtual.color,
                  fontSize: 24,
                }}
              >
                {selectedWorkflow.type === 'rpa' ? <ThunderboltOutlined /> : <ExperimentOutlined />}
              </Avatar>
              <div>
                <div className="wp-chat-title">{selectedWorkflow.name}</div>
                <Space size={4} wrap>
                  <Tag color={selectedWorkflow.type === 'rpa' ? 'orange' : 'purple'}>
                    {selectedWorkflow.type === 'rpa' ? 'RPA 自动化' : '流程（Workflow）'}
                  </Tag>
                  <Tag color={selectedWorkflow.data_level === 'L3' ? 'red' : selectedWorkflow.data_level === 'L2' ? 'blue' : 'green'}>
                    {selectedWorkflow.data_level === 'L3' ? '敏感 L3' : selectedWorkflow.data_level === 'L2' ? '内部 L2' : '公开 L1'}
                  </Tag>
                </Space>
              </div>
            </div>
            <Paragraph className="wp-persona">{selectedWorkflow.description}</Paragraph>

            <div className="wp-drawer-section">
              <div className="wp-drawer-section-title">执行步骤</div>
              <div className="wp-wf-steps">
                {selectedWorkflow.steps.map((step, index) => (
                  <div className="wp-wf-step" key={step}>
                    <span className="wp-wf-step-no">{index + 1}</span>
                    <span>{step}</span>
                  </div>
                ))}
                {selectedWorkflow.steps.length === 0 && <Text type="secondary">暂无步骤说明</Text>}
              </div>
            </div>

            <div className="wp-drawer-section">
              <div className="wp-drawer-section-title">授权成员（可执行）</div>
              <div className="wp-wf-members">
                {selectedWorkflow.authorized_employees.map((emp) => {
                  const meta = metaOf(emp.employee_no);
                  return (
                    <div className="wp-wf-member" key={emp.employee_no}>
                      <Avatar size={28} style={{ background: meta.bg, color: meta.color }}>
                        {meta.emoji}
                      </Avatar>
                      <Text>{emp.name}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {emp.employee_no}
                      </Text>
                    </div>
                  );
                })}
                {selectedWorkflow.authorized_employees.length === 0 && <Text type="secondary">暂未授权</Text>}
              </div>
            </div>

            {selectedWorkflow.demo_prompt && (
              <div className="wp-drawer-section">
                <div className="wp-drawer-section-title">示例指令</div>
                <div className="wp-wf-demo">
                  <span>「{selectedWorkflow.demo_prompt}」</span>
                  <Button size="small" onClick={() => void copyDemoPrompt(selectedWorkflow)}>
                    复制
                  </Button>
                </div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  把这句话发给协作空间，分身会拆解并指派给对应成员执行
                </Text>
              </div>
            )}
          </>
        )}
      </Drawer>

      {/* 添加个人工作方法弹窗 */}
      <Modal
        title="添加个人工作方法"
        open={skillModalOpen}
        onCancel={() => {
          setSkillModalOpen(false);
          skillForm.resetFields();
        }}
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
            <Input.TextArea rows={7} placeholder="粘贴 Markdown / 纯文本内容，或直接拖入 .md / .txt 文件" />
          </Form.Item>
          <Upload.Dragger
            accept=".md,.txt,.markdown"
            maxCount={1}
            beforeUpload={(file) => {
              const reader = new FileReader();
              reader.onload = () => {
                skillForm.setFieldValue('content', String(reader.result ?? ''));
              };
              reader.readAsText(file);
              return false;
            }}
            showUploadList={false}
          >
            <p className="ant-upload-drag-icon">
              <UploadOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽 .md / .txt 文件导入内容</p>
          </Upload.Dragger>
        </Form>
      </Modal>
    </div>
  );
}
