import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CloseCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SendOutlined,
} from '@ant-design/icons';
import { Alert, Button, Card, Col, Empty, Input, Progress, Row, Select, Space, Spin, Tag, Typography } from 'antd';
import type {
  AgentCollaborationHealth,
  AgentCollaborationTask,
  AgentFinalResult,
  AgentMessage,
  AgentTaskEvent,
  AgentTaskEvaluation,
  AgentTaskGraph,
  AgentTaskNode,
  Team,
} from '@dwp/shared-schema';
import { api } from '../../api/client';
import { useCurrentUser } from '../../context/CurrentUserContext';
import { TaskTree, AGENT_TASK_STATUS_META } from './TaskTree';
import { EvaluationPanel } from './EvaluationPanel';
import { DatabaseBenchmarkPanel } from './DatabaseBenchmarkPanel';
import { NodeProcessDetails } from './NodeProcessDetails';
import { TimelinePanel } from './TimelinePanel';

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'timed_out', 'cancelled']);
const LOCAL_DEMO_TEAM_ID = 'TEAM-ONBOARD';
const LOCAL_DEMO_TITLE = '岗位调整评估';
const LOCAL_DEMO_INSTRUCTION = '评估张明的岗位调整是否可以按期完成。';
const DEFAULT_TASK_TITLE = '岗位调整评估';
const DEFAULT_TASK_GOAL = '评估张明的岗位调整是否可以按期完成。';
const DATABASE_TEAM_ID = 'TEAM-DB-BENCH';
const DATABASE_SCENARIOS = [
  { value: 'lock_contention', label: '锁等待与并发阻塞' },
  { value: 'bulk_writes', label: '批量写入压力' },
  { value: 'vacuum_pressure', label: '清理滞后与表膨胀' },
  { value: 'index_anomaly', label: '索引异常' },
  { value: 'large_query', label: '大查询资源占用' },
];

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

function readinessLabel(value: unknown, roleChange = false) {
  if (value === 'ready') return roleChange ? '岗位调整可以按期完成' : '可以按期入职';
  if (value === 'not_ready') return roleChange ? '岗位调整暂不能按期完成' : '暂不能按期入职';
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

export default function AgentTeamsCollaborationPage() {
  const { actor } = useCurrentUser();
  const actorNo = actor.employee_no;
  const [health, setHealth] = useState<AgentCollaborationHealth | null>(null);
  const [teams, setTeams] = useState<Team[]>([]);
  const [tasks, setTasks] = useState<AgentCollaborationTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<AgentCollaborationTask | null>(null);
  const [graph, setGraph] = useState<AgentTaskGraph>(EMPTY_GRAPH);
  const [events, setEvents] = useState<AgentTaskEvent[]>([]);
  const [newEventIds, setNewEventIds] = useState<Set<number>>(() => new Set());
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [result, setResult] = useState<AgentFinalResult | null>(null);
  const [evaluation, setEvaluation] = useState<AgentTaskEvaluation | null>(null);
  const [evaluationLoading, setEvaluationLoading] = useState(false);
  const [evaluationError, setEvaluationError] = useState<string>();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [teamId, setTeamId] = useState<string | undefined>();
  const [title, setTitle] = useState('');
  const [instruction, setInstruction] = useState('');
  const [material, setMaterial] = useState('');
  const [databaseScenarioId, setDatabaseScenarioId] = useState('lock_contention');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [actingTaskIds, setActingTaskIds] = useState<Set<string>>(() => new Set());
  const [loadError, setLoadError] = useState<string>();
  const [formError, setFormError] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  const taskSelectionVersionRef = useRef(0);
  const actorVersionRef = useRef(0);
  const selectedTaskActorRef = useRef(actorNo);
  const evaluationRequestRef = useRef(0);
  const selectTask = useCallback((taskId: string | null) => {
    if (taskId === selectedTaskId) return;
    taskSelectionVersionRef.current += 1;
    setSelectedTaskId(taskId);
  }, [selectedTaskId]);
  const selectedTeam = useMemo(() => teams.find((team) => team.id === teamId), [teamId, teams]);
  const isDatabaseTeam = selectedTeam?.id === DATABASE_TEAM_ID;
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
  const isRoleChangeScenario = (graph.selected_skill_ids ?? []).includes('agentteams-role-change-readiness');
  const isDatabaseBenchmarkScenario = (
    selectedTask?.id === selectedTaskId
    && (graph.selected_skill_ids ?? []).includes('agentteams-database-diagnosis')
  );

  const loadInitial = useCallback(async () => {
    const actorVersion = actorVersionRef.current;
    setLoading(true);
    setLoadError(undefined);
    try {
      const [nextHealth, nextTeams, nextTasks] = await Promise.all([
        api.agentTeamsHealth(),
        api.listTeams(),
        api.listAgentTeamsTasks(actorNo),
      ]);
      if (actorVersionRef.current !== actorVersion) return;
      setHealth(nextHealth);
      setTeams(nextTeams);
      setTasks(nextTasks);
      setTitle((current) => current || DEFAULT_TASK_TITLE);
      setInstruction((current) => current || DEFAULT_TASK_GOAL);
      setTeamId((current) => {
        const supportedTeams = nextHealth.mode === 'local_demo'
          ? nextTeams.filter((team) => [LOCAL_DEMO_TEAM_ID, DATABASE_TEAM_ID].includes(team.id))
          : nextTeams;
        return supportedTeams.some((team) => team.id === current)
          ? current
          : supportedTeams.find((team) => team.id === LOCAL_DEMO_TEAM_ID)?.id ?? supportedTeams[0]?.id;
      });
      setSelectedTaskId((current) => current ?? nextTasks[0]?.id ?? null);
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

  const loadEvaluation = useCallback(async (taskId: string, selectionVersion: number) => {
    const requestVersion = ++evaluationRequestRef.current;
    const isCurrent = () => (
      evaluationRequestRef.current === requestVersion
      && taskSelectionVersionRef.current === selectionVersion
    );
    setEvaluationLoading(true);
    setEvaluationError(undefined);
    try {
      const nextEvaluation = await api.evaluateAgentTeamsTask(actorNo, taskId);
      if (!isCurrent()) return;
      setEvaluation(nextEvaluation);
    } catch (error) {
      if (!isCurrent()) return;
      setEvaluation(null);
      setEvaluationError(`评测暂不可用：${errorText(error)}`);
    } finally {
      if (isCurrent()) setEvaluationLoading(false);
    }
  }, [actorNo]);

  useEffect(() => {
    actorVersionRef.current += 1;
    taskSelectionVersionRef.current += 1;
    setTasks([]);
    setSelectedTaskId(null);
    setSelectedTask(null);
    setGraph(EMPTY_GRAPH);
    setEvents([]);
    setNewEventIds(new Set());
    setMessages([]);
    setResult(null);
    evaluationRequestRef.current += 1;
    setEvaluation(null);
    setEvaluationLoading(false);
    setEvaluationError(undefined);
    setSelectedNodeId(null);
    setSubmitting(false);
    setActingTaskIds(new Set());
    setLoadError(undefined);
    setFormError(undefined);
    setActionError(undefined);
  }, [actorNo]);

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
    evaluationRequestRef.current += 1;
    setEvaluation(null);
    setEvaluationLoading(false);
    setEvaluationError(undefined);
    setSelectedNodeId(selectedTaskId);
    void loadTask(selectedTaskId, requestVersion);
  }, [actorNo, loadTask, selectedTaskId]);

  useEffect(() => {
    if (!selectedTaskId || selectedTask?.id !== selectedTaskId || selectedTask.status !== 'completed') return;
    if (isDatabaseBenchmarkScenario) return;
    if (evaluation || evaluationLoading || evaluationError) return;
    void loadEvaluation(selectedTaskId, taskSelectionVersionRef.current);
  }, [evaluation, evaluationError, evaluationLoading, isDatabaseBenchmarkScenario, loadEvaluation, selectedTask, selectedTaskId]);

  useEffect(() => {
    if (!selectedTaskId || selectedTask?.id !== selectedTaskId || isTerminal(selectedTask.status)) return;
    const requestVersion = taskSelectionVersionRef.current;
    const timer = window.setTimeout(() => {
      void loadTask(selectedTaskId, requestVersion, selectedTask.latest_event_id);
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [loadTask, selectedTask, selectedTaskId]);

  const handleSubmit = async () => {
    const selectedTeamReady = isDatabaseTeam ? health?.enabled : health?.ready_for_demo;
    if (!selectedTeam || !selectedTeamReady || submitting) return;
    const trimmedTitle = title.trim();
    const trimmedInstruction = instruction.trim();
    if (!trimmedTitle || !trimmedInstruction) {
      setFormError('请填写任务标题和任务说明');
      return;
    }
    let parsedMaterial: Record<string, unknown> = {};
    if (!isDatabaseTeam && material.trim()) {
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
        team_id: selectedTeam.id,
        leader_agent_id: selectedTeam.leader_employee_id,
        title: health?.mode === 'local_demo' && !isDatabaseTeam ? LOCAL_DEMO_TITLE : trimmedTitle,
        instruction: health?.mode === 'local_demo' && !isDatabaseTeam ? LOCAL_DEMO_INSTRUCTION : trimmedInstruction,
        material: isDatabaseTeam
          ? { scenario_id: databaseScenarioId }
          : health?.mode === 'local_demo'
            ? {}
            : parsedMaterial,
      });
      if (actorVersionRef.current !== actorVersion) return;
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setSelectedTask(task);
      setGraph({ ...EMPTY_GRAPH, nodes: task.nodes });
      setEvents([]);
      setNewEventIds(new Set());
      setMessages([]);
      setResult(null);
      evaluationRequestRef.current += 1;
      setEvaluation(null);
      setEvaluationLoading(false);
      setEvaluationError(undefined);
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

  const handleEvaluationRetry = () => {
    if (!selectedTaskId || evaluationLoading || isDatabaseBenchmarkScenario) return;
    setEvaluationError(undefined);
    void loadEvaluation(selectedTaskId, taskSelectionVersionRef.current);
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
      evaluationRequestRef.current += 1;
      setEvaluation(null);
      setEvaluationLoading(false);
      setEvaluationError(undefined);
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
      evaluationRequestRef.current += 1;
      setEvaluation(null);
      setEvaluationLoading(false);
      setEvaluationError(undefined);
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

  const selectedTeamReady = isDatabaseTeam ? health?.enabled : health?.ready_for_demo;
  const canSubmit = Boolean(selectedTeamReady && selectedTeam && title.trim() && instruction.trim() && !submitting);
  const nodes: AgentTaskNode[] = graph.nodes.length > 0 ? graph.nodes : selectedTask?.nodes ?? [];
  const isLocalDemo = health?.mode === 'local_demo';
  const selectableTeams = isLocalDemo
    ? teams.filter((team) => [LOCAL_DEMO_TEAM_ID, DATABASE_TEAM_ID].includes(team.id))
    : teams;

  return (
    <div className="agentteams-page">
      <div className="at-page-heading">
        <div>
          <Typography.Title level={3}>AgentTeams 协作演示</Typography.Title>
          <Typography.Paragraph type="secondary">
            数据只来自服务端已持久化的协作状态；页面不会生成本地演示任务，也不会自行判定 Matrix 已连接。
          </Typography.Paragraph>
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
                  {!health.ready_for_demo && (
                    <Typography.Text type="secondary">
                      此状态只控制 TEAM-ONBOARD；TEAM-DB-BENCH 场景和数据库诊断 A/B 基准独立运行，不受此状态影响。
                    </Typography.Text>
                  )}
                </Space>
              }
              className="at-alert"
            />
          )}

          <Row gutter={[16, 16]} data-testid="agentteams-overview-row">
            <Col xs={24} xl={8}>
              <Card title="提交协作任务" className="at-card" extra={<Tag color="blue">服务端预检</Tag>}>
                <div className="at-form-fields">
                  <label htmlFor="agentteams-team">协作团队</label>
                  <Select
                    id="agentteams-team"
                    aria-label="协作团队"
                    value={teamId}
                    onChange={(nextTeamId) => {
                      setTeamId(nextTeamId);
                      if (nextTeamId === DATABASE_TEAM_ID) {
                        setTitle('数据库锁等待诊断');
                        setInstruction('请由数据库诊断团队并行检查写入、锁、清理、索引和查询信号，汇总根因、证据与优化建议。');
                        setMaterial('');
                      }
                    }}
                    options={selectableTeams.map((team) => ({ value: team.id, label: `${team.name}（${team.id}）` }))}
                    disabled={selectableTeams.length < 2}
                    placeholder="选择协作团队"
                  />
                  <Typography.Text type="secondary" className="at-leader-text">
                    负责人 Agent：{selectedTeam?.leader_employee_id ?? '未选择'}
                  </Typography.Text>

                  <label htmlFor="agentteams-title">任务标题</label>
                  <Input id="agentteams-title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} />
                  <label htmlFor="agentteams-instruction">任务说明</label>
                  <Input.TextArea
                    id="agentteams-instruction"
                    value={instruction}
                    onChange={(event) => setInstruction(event.target.value)}
                    autoSize={{ minRows: 4, maxRows: 8 }}
                    maxLength={2000}
                  />
                  {isDatabaseTeam ? (
                    <>
                      <label htmlFor="agentteams-database-scenario">数据库诊断场景</label>
                      <Select
                        id="agentteams-database-scenario"
                        aria-label="数据库诊断场景"
                        value={databaseScenarioId}
                        onChange={setDatabaseScenarioId}
                        options={DATABASE_SCENARIOS}
                      />
                      <Typography.Text type="secondary">
                        运行时只读取所选的脱敏固定场景；任务标题和说明不会作为数据库材料发送给模型。
                      </Typography.Text>
                    </>
                  ) : (
                    <>
                      <label htmlFor="agentteams-material">补充材料（可选 JSON）</label>
                      <Input.TextArea
                        id="agentteams-material"
                        value={material}
                        onChange={(event) => setMaterial(event.target.value)}
                        autoSize={{ minRows: 2, maxRows: 5 }}
                        placeholder='例如：{"policy_version":"2026.09"}'
                      />
                    </>
                  )}
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
                  {!selectedTeamReady && (
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
                {readinessLabel(finalPayload.readiness_decision, isRoleChangeScenario) && (
                  <Typography.Text strong className="at-final-decision">
                    结论：{readinessLabel(finalPayload.readiness_decision, isRoleChangeScenario)}
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
                  <Col xs={24} lg={16}>
                    <div className="at-main-stack" data-testid="agentteams-main-stack">
                      <TaskTree nodes={nodes} graph={graph} events={events} messages={messages} selectedId={selectedNodeId} onSelect={setSelectedNodeId} />
                      {selectedTask.status === 'completed' && isDatabaseBenchmarkScenario && (
                        <Alert
                          type="info"
                          showIcon
                          data-testid="database-task-evaluation-routing"
                          message="该任务使用数据库 A/B 基准评测"
                          description="当前协作树来自页面下方的数据库诊断对照实验；量化结果请在下方 A/B 指标和运行明细中核对，不调用旧版 100 分任务评测。"
                        />
                      )}
                      {selectedTask.status === 'completed' && !isDatabaseBenchmarkScenario && (
                        <EvaluationPanel
                          evaluation={evaluation}
                          loading={evaluationLoading}
                          error={evaluationError}
                          onRetry={handleEvaluationRetry}
                        />
                      )}
                    </div>
                  </Col>
                  <Col xs={24} lg={8}>
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
          <DatabaseBenchmarkPanel actorNo={actorNo} onSelectTask={(taskId) => selectTask(taskId)} />
        </>
      )}
    </div>
  );
}
