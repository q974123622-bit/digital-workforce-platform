import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CloseCircleOutlined,
  DownOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
} from '@ant-design/icons';
import { Alert, Button, Card, Col, Dropdown, Empty, Input, Progress, Row, Space, Spin, Switch, Tag, Typography } from 'antd';
import type {
  AgentCollaborationHealth,
  AgentCollaborationTask,
  AgentFinalResult,
  AgentMessage,
  AgentTaskEvent,
  AgentTaskGraph,
  AgentTaskNode,
} from '@dwp/shared-schema';
import { api } from '../../api/client';
import { useCurrentUser } from '../../context/CurrentUserContext';
import { TaskTree, AGENT_TASK_STATUS_META } from './TaskTree';
import { NodeProcessDetails } from './NodeProcessDetails';
import { TimelinePanel } from './TimelinePanel';

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'timed_out', 'cancelled']);
const LOCAL_DEMO_TITLE = '岗位调整评估';
const LOCAL_DEMO_INSTRUCTION = '评估张明的岗位调整是否可以按期完成。';
const DEFAULT_TASK_TITLE = '岗位调整评估';
const DEFAULT_TASK_GOAL = '评估张明的岗位调整是否可以按期完成。';
const TASK_PRESETS = [
  {
    key: 'onboarding',
    label: '新员工入职评估',
    title: '新员工入职评估',
    instruction: '评估新员工A是否可以按期入职，核验HR材料、培训、IT账号、IT设备及独立审计结果。',
  },
  {
    key: 'expense-reimbursement',
    label: '财务报销协同审核',
    title: '财务报销协同审核',
    instruction: '审核李明的差旅报销单 EXP-2026-0915-001，核验发票、行程、预算与合规性；全部通过后生成模拟付款单。',
  },
] as const;

const EMPTY_GRAPH: AgentTaskGraph = {
  nodes: [],
  parent_edges: [],
  dependency_edges: [],
  communication_edges: [],
};

function isTerminal(status?: string) {
  return status ? TERMINAL_STATUSES.has(status) : false;
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : '请求失败，请稍后重试';
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function mergeEvents(existing: AgentTaskEvent[], incoming: AgentTaskEvent[]) {
  const byId = new Map(existing.map((event) => [event.id, event]));
  for (const event of incoming) byId.set(event.id, event);
  return [...byId.values()].sort((left, right) => left.id - right.id);
}

function healthTag(label: string, status: string) {
  if (status === 'ready') return <Tag color="success">{label}已连接</Tag>;
  if (status === 'configured') return <Tag color="processing">{label}已配置/待实测</Tag>;
  return <Tag color="error">{label}未就绪：{status || 'unknown'}</Tag>;
}

function managerHealthTag(status: string) {
  if (status === 'ready') return <Tag color="success">Manager已就绪</Tag>;
  if (status === 'configured') return <Tag color="processing">Manager已配置</Tag>;
  return <Tag color="error">Manager未就绪：{status || 'unknown'}</Tag>;
}

function workerHealthTag(workers: AgentCollaborationHealth['workers']) {
  const readyWorkers = workers.filter((worker) => worker.status === 'ready').length;
  const configuredWorkers = workers.filter((worker) => worker.status === 'configured').length;
  if (readyWorkers > 0 && configuredWorkers > 0) {
    return <Tag color="processing">Worker已就绪：{readyWorkers}/{workers.length}；已配置/待实测：{configuredWorkers}/{workers.length}</Tag>;
  }
  if (configuredWorkers > 0) {
    return <Tag color="processing">Worker 已配置/待实测：{configuredWorkers}/{workers.length}</Tag>;
  }
  if (readyWorkers > 0) {
    return <Tag color="success">Worker 已就绪：{readyWorkers}/{workers.length}</Tag>;
  }
  return <Tag color="error">Worker 未就绪：0/{workers.length}</Tag>;
}

function statusTag(status: string) {
  const meta = AGENT_TASK_STATUS_META[status] ?? { label: status, color: 'default' };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function payloadTextList(payload: Record<string, unknown>, key: string) {
  const value = payload[key];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && item.length > 0)
    : [];
}

type DecisionScenario = 'onboarding' | 'role_change' | 'expense';

function readinessLabel(value: unknown, scenario: DecisionScenario) {
  if (value === 'ready') {
    if (scenario === 'role_change') return '岗位调整可以按期完成';
    if (scenario === 'expense') return '报销审核通过';
    return '可以按期入职';
  }
  if (value === 'not_ready') {
    if (scenario === 'role_change') return '岗位调整暂不能按期完成';
    if (scenario === 'expense') return '报销审核未通过';
    return '暂不能按期入职';
  }
  if (value === 'incomplete') return '信息不足，暂无法判断';
  return '';
}

function displayProgress(node: AgentTaskNode) {
  return node.status === 'completed' ? 100 : Math.max(0, Math.min(100, node.progress));
}

interface LoadedTask {
  task: AgentCollaborationTask;
  graph: AgentTaskGraph;
  events: AgentTaskEvent[];
  messages: AgentMessage[];
  result: AgentFinalResult;
}

const INITIALIZED_SESSION_KEY_PREFIX = 'dwp.agentteams.initialized:';
const INITIAL_SNAPSHOT_SESSION_KEY_PREFIX = 'dwp.agentteams.initial-snapshot:';

interface InitialSessionSnapshot {
  health: AgentCollaborationHealth;
  tasks: AgentCollaborationTask[];
  selectedTaskId: string | null;
}

function initializedSessionKey(actorNo: string) {
  return `${INITIALIZED_SESSION_KEY_PREFIX}${actorNo}`;
}

function initialSnapshotSessionKey(actorNo: string) {
  return `${INITIAL_SNAPSHOT_SESSION_KEY_PREFIX}${actorNo}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readInitialSessionSnapshot(actorNo: string): InitialSessionSnapshot | null {
  try {
    const raw = window.sessionStorage.getItem(initialSnapshotSessionKey(actorNo));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || !isRecord(parsed.health) || !Array.isArray(parsed.tasks)) return null;
    if (!parsed.tasks.every((task) => isRecord(task) && typeof task.id === 'string')) return null;
    if (parsed.selectedTaskId !== null && typeof parsed.selectedTaskId !== 'string') return null;
    return parsed as unknown as InitialSessionSnapshot;
  } catch {
    return null;
  }
}

function writeInitialSessionSnapshot(actorNo: string, snapshot: InitialSessionSnapshot) {
  try {
    window.sessionStorage.setItem(initialSnapshotSessionKey(actorNo), JSON.stringify(snapshot));
  } catch {
    // Session storage can be unavailable or full in restricted browser modes.
  }
}

function hasInitializedInSession(actorNo: string) {
  try {
    return window.sessionStorage.getItem(initializedSessionKey(actorNo)) === '1';
  } catch {
    return false;
  }
}

function markInitializedInSession(actorNo: string) {
  try {
    window.sessionStorage.setItem(initializedSessionKey(actorNo), '1');
  } catch {
    // Session storage can be unavailable in restricted browser modes.
  }
}

export default function AgentTeamsCollaborationPage() {
  const { actor } = useCurrentUser();
  const actorNo = actor.employee_no;
  const [initialSnapshot] = useState(() => readInitialSessionSnapshot(actorNo));
  const [health, setHealth] = useState<AgentCollaborationHealth | null>(initialSnapshot?.health ?? null);
  const [tasks, setTasks] = useState<AgentCollaborationTask[]>(initialSnapshot?.tasks ?? []);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(initialSnapshot?.selectedTaskId ?? null);
  const [selectedTask, setSelectedTask] = useState<AgentCollaborationTask | null>(null);
  const [graph, setGraph] = useState<AgentTaskGraph>(EMPTY_GRAPH);
  const [events, setEvents] = useState<AgentTaskEvent[]>([]);
  const [newEventIds, setNewEventIds] = useState<Set<number>>(() => new Set());
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [result, setResult] = useState<AgentFinalResult | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [title, setTitle] = useState(DEFAULT_TASK_TITLE);
  const [instruction, setInstruction] = useState(DEFAULT_TASK_GOAL);
  const [material, setMaterial] = useState('');
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [loading, setLoading] = useState(() => initialSnapshot === null);
  const [submitting, setSubmitting] = useState(false);
  const [actingTaskIds, setActingTaskIds] = useState<Set<string>>(() => new Set());
  const [loadError, setLoadError] = useState<string>();
  const [formError, setFormError] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  const taskSelectionVersionRef = useRef(0);
  const actorVersionRef = useRef(0);
  const selectedTaskActorRef = useRef(actorNo);
  const selectedTaskIdRef = useRef<string | null>(initialSnapshot?.selectedTaskId ?? null);
  const selectTask = useCallback((taskId: string | null) => {
    if (taskId === selectedTaskId) return;
    taskSelectionVersionRef.current += 1;
    setSelectedTaskId(taskId);
  }, [selectedTaskId]);
  const selectedNode = useMemo(() => {
    const nodes = graph.nodes.length > 0 ? graph.nodes : selectedTask?.nodes ?? [];
    return nodes.find((node) => node.id === selectedNodeId) ?? null;
  }, [graph.nodes, selectedNodeId, selectedTask?.nodes]);
  const finalPayload = useMemo(() => {
    if (!result) return {} as Record<string, unknown>;
    return result.node_results.find((node) => node.id === result.task_id)?.output_payload ?? {};
  }, [result]);
  const finalSections = useMemo(() => [
    { title: '已完成', items: payloadTextList(finalPayload, 'completed_items') },
    { title: '风险', items: payloadTextList(finalPayload, 'risks') },
    { title: '信息缺口', items: payloadTextList(finalPayload, 'unknowns') },
    { title: '后续操作', items: payloadTextList(finalPayload, 'next_actions') },
  ].filter((section) => section.items.length > 0), [finalPayload]);
  const decisionScenario: DecisionScenario = (graph.selected_skill_ids ?? []).includes('agentteams-expense-reimbursement')
    ? 'expense'
    : (graph.selected_skill_ids ?? []).includes('agentteams-role-change-readiness')
      ? 'role_change'
      : 'onboarding';

  const loadInitial = useCallback(async () => {
    const actorVersion = actorVersionRef.current;
    if (!readInitialSessionSnapshot(actorNo)) setLoading(true);
    setLoadError(undefined);
    try {
      const [nextHealth, nextTasks] = await Promise.all([
        api.agentTeamsHealth(),
        api.listAgentTeamsTasks(actorNo),
      ]);
      if (actorVersionRef.current !== actorVersion) return;
      const currentSelection = selectedTaskIdRef.current;
      const nextSelectedTaskId = currentSelection && nextTasks.some((task) => task.id === currentSelection)
        ? currentSelection
        : nextTasks[0]?.id ?? null;
      setHealth(nextHealth);
      setTasks(nextTasks);
      setTitle((current) => current || DEFAULT_TASK_TITLE);
      setInstruction((current) => current || DEFAULT_TASK_GOAL);
      selectedTaskIdRef.current = nextSelectedTaskId;
      setSelectedTaskId(nextSelectedTaskId);
      writeInitialSessionSnapshot(actorNo, {
        health: nextHealth,
        tasks: nextTasks,
        selectedTaskId: nextSelectedTaskId,
      });
      markInitializedInSession(actorNo);
    } catch (error) {
      if (actorVersionRef.current === actorVersion) setLoadError(errorText(error));
    } finally {
      if (actorVersionRef.current === actorVersion) setLoading(false);
    }
  }, [actorNo]);

  const loadTask = useCallback(async (
    taskId: string,
    requestVersion: number,
    afterEventId?: number,
  ): Promise<LoadedTask | null> => {
    const isCurrentTask = () => taskSelectionVersionRef.current === requestVersion;
    if (!isCurrentTask()) return null;
    try {
      const [task, nextGraph, nextEvents, nextMessages, nextResult] = await Promise.all([
        api.getAgentTeamsTask(actorNo, taskId),
        api.getAgentTeamsGraph(actorNo, taskId),
        api.getAgentTeamsEvents(actorNo, taskId, afterEventId),
        api.getAgentTeamsMessages(actorNo, taskId),
        api.getAgentTeamsResult(actorNo, taskId),
      ]);
      if (!isCurrentTask()) return null;
      setSelectedTask(task);
      setGraph(nextGraph);
      setMessages(nextMessages);
      setResult(nextResult);
      setEvents((current) => {
        if (afterEventId === undefined) {
          setNewEventIds(new Set());
          return nextEvents;
        }
        const known = new Set(current.map((event) => event.id));
        setNewEventIds(new Set(nextEvents.filter((event) => !known.has(event.id)).map((event) => event.id)));
        return mergeEvents(current, nextEvents);
      });
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setSelectedNodeId((current) => current ?? task.id);
      return { task, graph: nextGraph, events: nextEvents, messages: nextMessages, result: nextResult };
    } catch (error) {
      if (isCurrentTask()) setActionError(`任务详情请求失败：${errorText(error)}`);
      return null;
    }
  }, [actorNo]);

  useEffect(() => {
    const snapshot = readInitialSessionSnapshot(actorNo);
    actorVersionRef.current += 1;
    taskSelectionVersionRef.current += 1;
    setHealth(snapshot?.health ?? null);
    setTasks(snapshot?.tasks ?? []);
    selectedTaskIdRef.current = snapshot?.selectedTaskId ?? null;
    setSelectedTaskId(snapshot?.selectedTaskId ?? null);
    setSelectedTask(null);
    setGraph(EMPTY_GRAPH);
    setEvents([]);
    setNewEventIds(new Set());
    setMessages([]);
    setResult(null);
    setSelectedNodeId(null);
    setSubmitting(false);
    setActingTaskIds(new Set());
    setLoadError(undefined);
    setFormError(undefined);
    setActionError(undefined);
    setLoading(snapshot === null);
  }, [actorNo]);

  useEffect(() => {
    selectedTaskIdRef.current = selectedTaskId;
    if (!health || !hasInitializedInSession(actorNo)) return;
    writeInitialSessionSnapshot(actorNo, { health, tasks, selectedTaskId });
  }, [actorNo, health, selectedTaskId, tasks]);

  useEffect(() => {
    void loadInitial();
  }, [loadInitial]);

  useEffect(() => {
    if (selectedTaskActorRef.current !== actorNo) {
      selectedTaskActorRef.current = actorNo;
      return;
    }
    if (!selectedTaskId) return;
    const requestVersion = ++taskSelectionVersionRef.current;
    setEvents([]);
    setMessages([]);
    setResult(null);
    setSelectedNodeId(selectedTaskId);
    void loadTask(selectedTaskId, requestVersion);
  }, [actorNo, loadTask, selectedTaskId]);

  useEffect(() => {
    if (!selectedTaskId || selectedTask?.id !== selectedTaskId || isTerminal(selectedTask.status)) return;
    const requestVersion = taskSelectionVersionRef.current;
    const timer = window.setTimeout(() => {
      void loadTask(selectedTaskId, requestVersion, selectedTask.latest_event_id);
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [loadTask, selectedTask, selectedTaskId]);

  const handleSubmit = async () => {
    if (!health?.ready_for_demo || submitting) return;
    const trimmedTitle = title.trim();
    const trimmedInstruction = instruction.trim();
    if (!trimmedTitle || !trimmedInstruction) {
      setFormError('请填写任务标题和任务说明');
      return;
    }
    let parsedMaterial: Record<string, unknown> = {};
    if (material.trim()) {
      try {
        const parsed = JSON.parse(material) as unknown;
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('not object');
        parsedMaterial = parsed as Record<string, unknown>;
      } catch {
        setFormError('补充材料必须是 JSON 对象');
        return;
      }
    }

    setSubmitting(true);
    setFormError(undefined);
    setActionError(undefined);
    const actorVersion = actorVersionRef.current;
    try {
      const task = await api.createAgentTeamsTask(actorNo, {
        title: health?.mode === 'local_demo' ? LOCAL_DEMO_TITLE : trimmedTitle,
        instruction: health?.mode === 'local_demo' ? LOCAL_DEMO_INSTRUCTION : trimmedInstruction,
        material: health?.mode === 'local_demo' ? {} : parsedMaterial,
        thinking_enabled: thinkingEnabled,
      });
      if (actorVersionRef.current !== actorVersion) return;
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setSelectedTask(task);
      setGraph({ ...EMPTY_GRAPH, nodes: task.nodes });
      setEvents([]);
      setNewEventIds(new Set());
      setMessages([]);
      setResult(null);
      setSelectedNodeId(task.id);
      selectTask(task.id);
      setTitle(DEFAULT_TASK_TITLE);
      setInstruction(DEFAULT_TASK_GOAL);
      setMaterial('');
    } catch (error) {
      if (actorVersionRef.current === actorVersion) setActionError(`提交协作任务失败：${errorText(error)}`);
    } finally {
      if (actorVersionRef.current === actorVersion) setSubmitting(false);
    }
  };

  const handleCancel = async () => {
    const actionTask = selectedTask;
    if (!actionTask || actingTaskIds.has(actionTask.id)) return;
    const requestVersion = taskSelectionVersionRef.current;
    const isCurrentTask = () => taskSelectionVersionRef.current === requestVersion;
    setActingTaskIds((current) => new Set(current).add(actionTask.id));
    setActionError(undefined);
    try {
      const task = await api.cancelAgentTeamsTask(actorNo, actionTask.id);
      if (!isCurrentTask()) return;
      setSelectedTask(task);
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setGraph((current) => ({ ...current, nodes: task.nodes }));
      setResult(null);
    } catch (error) {
      if (isCurrentTask()) setActionError(`取消任务失败：${errorText(error)}`);
    } finally {
      if (!isCurrentTask()) return;
      setActingTaskIds((current) => {
        if (!current.has(actionTask.id)) return current;
        const next = new Set(current);
        next.delete(actionTask.id);
        return next;
      });
    }
  };

  const handleRetry = async () => {
    const actionTask = selectedTask;
    if (!actionTask || actingTaskIds.has(actionTask.id)) return;
    const requestVersion = taskSelectionVersionRef.current;
    const isCurrentTask = () => taskSelectionVersionRef.current === requestVersion;
    setActingTaskIds((current) => new Set(current).add(actionTask.id));
    setActionError(undefined);
    try {
      const task = await api.retryAgentTeamsTask(actorNo, actionTask.id);
      if (!isCurrentTask()) return;
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setSelectedTask(task);
      setGraph({ ...EMPTY_GRAPH, nodes: task.nodes });
      setEvents([]);
      setNewEventIds(new Set());
      setMessages([]);
      setResult(null);
      setSelectedNodeId(task.id);
      selectTask(task.id);
    } catch (error) {
      if (isCurrentTask()) setActionError(`重试任务失败：${errorText(error)}`);
    } finally {
      if (!isCurrentTask()) return;
      setActingTaskIds((current) => {
        if (!current.has(actionTask.id)) return current;
        const next = new Set(current);
        next.delete(actionTask.id);
        return next;
      });
    }
  };

  const canSubmit = Boolean(health?.ready_for_demo && title.trim() && instruction.trim() && !submitting);
  const nodes: AgentTaskNode[] = graph.nodes.length > 0 ? graph.nodes : selectedTask?.nodes ?? [];
  const isLocalDemo = health?.mode === 'local_demo';

  return (
    <div className="agentteams-page">
      <div className="at-page-heading">
        <div>
          <Typography.Title level={3}>AgentTeams 协作演示</Typography.Title>
        </div>
        {health && (
          <Tag icon={<RobotOutlined />} color={isLocalDemo ? 'gold' : 'geekblue'}>
            {isLocalDemo ? '本地Demo（未连接Matrix）' : '真实AgentTeams / Matrix'}
          </Tag>
        )}
      </div>

      {loading ? (
        <Card><Spin /> 正在读取协作环境与任务记录…</Card>
      ) : (
        <>
          {loadError && (
            <Alert
              type="error"
              showIcon
              message="协作页面初始化失败"
              description={loadError}
              action={<Button size="small" onClick={() => void loadInitial()}>重新加载</Button>}
              className="at-alert"
            />
          )}
          {health && (
            <Alert
              type={health.ready_for_demo ? 'success' : 'warning'}
              showIcon
              message={health.ready_for_demo ? '协作环境已就绪' : '协作环境未就绪'}
              description={
                <Space direction="vertical" size={6}>
                  <Space size={[6, 6]} wrap>
                    {!isLocalDemo && healthTag('Matrix', health.matrix)}
                    {!isLocalDemo && healthTag('房间', health.room)}
                    {managerHealthTag(health.manager)}
                    {workerHealthTag(health.workers)}
                  </Space>
                  <Space size={4}>
                    <Typography.Text>服务端原因：</Typography.Text>
                    <Typography.Text>{health.reason}</Typography.Text>
                  </Space>
                  <Space size={[6, 6]} wrap>
                    <Tag>运行模式：{health.mode === 'agentteams_strict' ? '真实 AgentTeams' : '本地 Demo'}</Tag>
                    <Tag>消息传输：{health.transport === 'matrix' ? 'Matrix' : '本地传输'}</Tag>
                    <Tag>严格就绪：{health.strict_ready ? '是' : '否'}</Tag>
                    <Tag>已配置团队：{health.configured_team_id || '未产生'}</Tag>
                  </Space>
                </Space>
              }
              className="at-alert"
            />
          )}

          <Row gutter={[16, 16]} data-testid="agentteams-overview-row">
            <Col xs={24} xl={8}>
              <Card title="提交协作任务" className="at-card" extra={<Tag color="blue">Manager 自动路由</Tag>}>
                <div className="at-form-fields">
                  <div className="at-field-label-row">
                    <label htmlFor="agentteams-title">任务标题</label>
                    <Dropdown
                      trigger={['click']}
                      menu={{
                        items: TASK_PRESETS.map((preset) => ({ key: preset.key, label: preset.label })),
                        onClick: ({ key }) => {
                          const preset = TASK_PRESETS.find((item) => item.key === key);
                          if (!preset) return;
                          setTitle(preset.title);
                          setInstruction(preset.instruction);
                        },
                      }}
                    >
                      <Button size="small" aria-label="选择任务预设">
                        选择预设 <DownOutlined />
                      </Button>
                    </Dropdown>
                  </div>
                  <Input id="agentteams-title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} />
                  <label htmlFor="agentteams-instruction">任务说明</label>
                  <Input.TextArea
                    id="agentteams-instruction"
                    value={instruction}
                    onChange={(event) => setInstruction(event.target.value)}
                    autoSize={{ minRows: 4, maxRows: 8 }}
                    maxLength={2000}
                  />
                  <label htmlFor="agentteams-material">补充材料（可选 JSON）</label>
                  <Input.TextArea
                    id="agentteams-material"
                    value={material}
                    onChange={(event) => setMaterial(event.target.value)}
                    autoSize={{ minRows: 2, maxRows: 5 }}
                    placeholder='例如：{"policy_version":"2026.09"}'
                  />
                  <div className="at-thinking-mode">
                    <div className="at-thinking-mode-control">
                      <div>
                        <Typography.Text strong>使用大模型思考模式</Typography.Text>
                        <Typography.Text type="secondary">
                          {thinkingEnabled
                            ? '会保留模型返回的决策依据，响应时间可能更长。'
                            : '响应更快，但不会展示模型返回的决策依据。'}
                        </Typography.Text>
                      </div>
                      <Switch
                        aria-label="使用大模型思考模式"
                        checked={thinkingEnabled}
                        onChange={setThinkingEnabled}
                      />
                    </div>
                  </div>
                  {formError && <Alert type="error" showIcon message={formError} />}
                  <Button
                    type="primary"
                    icon={<SendOutlined />}
                    loading={submitting}
                    disabled={!canSubmit}
                    aria-label="提交协作任务"
                    onClick={() => void handleSubmit()}
                    block
                  >
                    提交协作任务
                  </Button>
                  {!health?.ready_for_demo && (
                    <Typography.Text type="secondary">提交已由服务端健康预检门禁禁用。</Typography.Text>
                  )}
                </div>
              </Card>
            </Col>

            <Col xs={24} xl={16}>
              <Card title="协作任务记录" className="at-card">
                {tasks.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="服务端尚无 AgentTeams 协作任务" />
                ) : (
                  <div className="at-task-list">
                    {tasks.map((task) => (
                      <button
                        type="button"
                        key={task.id}
                        className={`at-task-row ${task.id === selectedTaskId ? 'selected' : ''}`}
                        aria-label={`查看协作任务 ${task.title}`}
                        onClick={() => selectTask(task.id)}
                      >
                        <span>
                          <Typography.Text strong>{task.title}</Typography.Text>
                          <Typography.Text type="secondary" className="at-task-row-meta">{task.id} · {formatTime(task.created_at)}</Typography.Text>
                        </span>
                        {statusTag(task.status)}
                      </button>
                    ))}
                  </div>
                )}
              </Card>
            </Col>
          </Row>

          {actionError && <Alert type="error" showIcon message={actionError} closable onClose={() => setActionError(undefined)} className="at-alert" />}

          {selectedTask && isTerminal(selectedTask.status) && (
            <Card
              title="最终汇总"
              className="at-final-result at-final-result-wide"
              data-testid="agentteams-result-panel"
              data-layout="full-width"
            >
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {statusTag(result?.status ?? selectedTask.status)}
                {readinessLabel(finalPayload.readiness_decision, decisionScenario) && (
                  <Typography.Text strong className="at-final-decision">
                    结论：{readinessLabel(finalPayload.readiness_decision, decisionScenario)}
                  </Typography.Text>
                )}
                <Typography.Paragraph className="at-final-summary">
                  {result?.summary || selectedTask.summary || '服务端尚未返回最终汇总。'}
                </Typography.Paragraph>
                {finalSections.length > 0 && (
                  <div className="at-final-sections">
                    {finalSections.map((section) => (
                      <section key={section.title} className="at-final-section">
                        <Typography.Text strong>{section.title}</Typography.Text>
                        <ul>{section.items.map((item) => <li key={item}>{item}</li>)}</ul>
                      </section>
                    ))}
                  </div>
                )}
              </Space>
            </Card>
          )}

          {selectedTask && (
            <div className="at-task-workspace">
              <Card
                className="at-card"
                title={
                  <Space size={8} wrap>
                    <span>{selectedTask.title}</span>
                    {statusTag(selectedTask.status)}
                    <Typography.Text type="secondary" className="mono">{selectedTask.id}</Typography.Text>
                  </Space>
                }
                extra={
                  <Space>
                    {!isTerminal(selectedTask.status) && (
                      <Button danger icon={<CloseCircleOutlined />} loading={actingTaskIds.has(selectedTask.id)} onClick={() => void handleCancel()}>
                        取消任务
                      </Button>
                    )}
                    {isTerminal(selectedTask.status) && selectedTask.status !== 'completed' && (
                      <Button icon={<ReloadOutlined />} loading={actingTaskIds.has(selectedTask.id)} onClick={() => void handleRetry()}>
                        重试
                      </Button>
                    )}
                  </Space>
                }
              >
                <Typography.Paragraph>{selectedTask.instruction}</Typography.Paragraph>
                <Space size={[6, 6]} wrap>
                  <Tag color="blue">{graph.current_round ? `第 ${graph.current_round} 轮` : '轮次：尚未产生'}</Tag>
                  {(graph.selected_skill_ids ?? []).length > 0
                    ? graph.selected_skill_ids?.map((skillId) => <Tag color="purple" key={skillId}>已选择 Skill：{skillId}</Tag>)
                    : <Tag>已选择 Skill：尚未产生</Tag>}
                </Space>
                <div className="at-workspace-grid" data-testid="agentteams-workspace">
                <Row gutter={[16, 16]} className="at-workspace-row">
                  <Col xs={24} xl={18} xxl={19}>
                    <div className="at-main-stack" data-testid="agentteams-main-stack">
                      <TaskTree nodes={nodes} graph={graph} events={events} messages={messages} selectedId={selectedNodeId} onSelect={setSelectedNodeId} />
                    </div>
                  </Col>
                  <Col xs={24} xl={6} xxl={5}>
                    <div className="at-side-stack" data-testid="agentteams-side-stack">
                      <Card size="small" title="当前节点状态" className="at-detail-card" data-testid="agentteams-node-detail">
                        {selectedNode ? (
                          <Space direction="vertical" size={10} style={{ width: '100%' }}>
                            <Space size={[6, 6]} wrap>
                              <Typography.Text strong>{selectedNode.title}</Typography.Text>
                              {statusTag(selectedNode.status)}
                              <Tag>执行人：{selectedNode.assignee_agent_id}</Tag>
                              <Tag>协调人：{selectedNode.coordinator_agent_id}</Tag>
                            </Space>
                            <Progress percent={displayProgress(selectedNode)} />
                            {selectedNode.error_message && <Alert type="error" showIcon message={selectedNode.error_message} />}
                            <NodeProcessDetails
                              node={selectedNode}
                              messages={messages}
                              taskSummary={selectedTask.summary}
                              rootSkillIds={graph.selected_skill_ids ?? []}
                            />
                          </Space>
                        ) : (
                          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择一个节点查看状态" />
                        )}
                      </Card>
                    </div>
                  </Col>
                </Row>
                </div>

              </Card>

              <TimelinePanel
                events={events}
                messages={messages}
                newEventIds={newEventIds}
                selectedTaskId={selectedNodeId}
                isRealMatrix={health?.transport === 'matrix'}
              />

            </div>
          )}
        </>
      )}
    </div>
  );
}
