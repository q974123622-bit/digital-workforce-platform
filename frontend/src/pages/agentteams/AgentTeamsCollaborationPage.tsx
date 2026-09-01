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
  AgentTaskGraph,
  AgentTaskNode,
  Team,
} from '@dwp/shared-schema';
import { api } from '../../api/client';
import { TaskTree, AGENT_TASK_STATUS_META } from './TaskTree';
import { TimelinePanel } from './TimelinePanel';

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'timed_out', 'cancelled']);

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

function workerHealthTag(workers: AgentCollaborationHealth['workers']) {
  const readyWorkers = workers.filter((worker) => worker.status === 'ready').length;
  const configuredWorkers = workers.filter((worker) => worker.status === 'configured').length;
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

interface LoadedTask {
  task: AgentCollaborationTask;
  graph: AgentTaskGraph;
  events: AgentTaskEvent[];
  messages: AgentMessage[];
  result: AgentFinalResult;
}

export default function AgentTeamsCollaborationPage() {
  const [health, setHealth] = useState<AgentCollaborationHealth | null>(null);
  const [teams, setTeams] = useState<Team[]>([]);
  const [tasks, setTasks] = useState<AgentCollaborationTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<AgentCollaborationTask | null>(null);
  const [graph, setGraph] = useState<AgentTaskGraph>(EMPTY_GRAPH);
  const [events, setEvents] = useState<AgentTaskEvent[]>([]);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [result, setResult] = useState<AgentFinalResult | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [teamId, setTeamId] = useState<string | undefined>();
  const [title, setTitle] = useState('');
  const [instruction, setInstruction] = useState('');
  const [material, setMaterial] = useState('');
  const [loading, setLoading] = useState(true);
  const [taskLoading, setTaskLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [acting, setActing] = useState(false);
  const [loadError, setLoadError] = useState<string>();
  const [formError, setFormError] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  const taskSelectionVersionRef = useRef(0);
  const selectTask = useCallback((taskId: string | null) => {
    if (taskId === selectedTaskId) return;
    taskSelectionVersionRef.current += 1;
    setSelectedTaskId(taskId);
  }, [selectedTaskId]);
  const selectedTeam = useMemo(() => teams.find((team) => team.id === teamId), [teamId, teams]);
  const selectedNode = useMemo(() => {
    const nodes = graph.nodes.length > 0 ? graph.nodes : selectedTask?.nodes ?? [];
    return nodes.find((node) => node.id === selectedNodeId) ?? null;
  }, [graph.nodes, selectedNodeId, selectedTask?.nodes]);

  const loadInitial = useCallback(async () => {
    setLoading(true);
    setLoadError(undefined);
    try {
      const [nextHealth, nextTeams, nextTasks] = await Promise.all([
        api.agentTeamsHealth(),
        api.listTeams(),
        api.listAgentTeamsTasks(),
      ]);
      setHealth(nextHealth);
      setTeams(nextTeams);
      setTasks(nextTasks);
      setTeamId((current) => current ?? nextTeams[0]?.id);
      setSelectedTaskId((current) => current ?? nextTasks[0]?.id ?? null);
    } catch (error) {
      setLoadError(errorText(error));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTask = useCallback(async (
    taskId: string,
    requestVersion: number,
    afterEventId?: number,
  ): Promise<LoadedTask | null> => {
    const isCurrentTask = () => taskSelectionVersionRef.current === requestVersion;
    if (!isCurrentTask()) return null;
    setTaskLoading(true);
    try {
      const [task, nextGraph, nextEvents, nextMessages, nextResult] = await Promise.all([
        api.getAgentTeamsTask(taskId),
        api.getAgentTeamsGraph(taskId),
        api.getAgentTeamsEvents(taskId, afterEventId),
        api.getAgentTeamsMessages(taskId),
        api.getAgentTeamsResult(taskId),
      ]);
      if (!isCurrentTask()) return null;
      setSelectedTask(task);
      setGraph(nextGraph);
      setMessages(nextMessages);
      setResult(nextResult);
      setEvents((current) => afterEventId === undefined ? nextEvents : mergeEvents(current, nextEvents));
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setSelectedNodeId((current) => current ?? task.id);
      return { task, graph: nextGraph, events: nextEvents, messages: nextMessages, result: nextResult };
    } catch (error) {
      if (isCurrentTask()) setActionError(`任务详情请求失败：${errorText(error)}`);
      return null;
    } finally {
      if (isCurrentTask()) setTaskLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadInitial();
  }, [loadInitial]);

  useEffect(() => {
    if (!selectedTaskId) return;
    const requestVersion = ++taskSelectionVersionRef.current;
    setEvents([]);
    setMessages([]);
    setResult(null);
    setSelectedNodeId(selectedTaskId);
    void loadTask(selectedTaskId, requestVersion);
  }, [loadTask, selectedTaskId]);

  useEffect(() => {
    if (!selectedTaskId || selectedTask?.id !== selectedTaskId || isTerminal(selectedTask.status)) return;
    const requestVersion = taskSelectionVersionRef.current;
    const timer = window.setTimeout(() => {
      void loadTask(selectedTaskId, requestVersion, selectedTask.latest_event_id);
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [loadTask, selectedTask, selectedTaskId]);

  const handleSubmit = async () => {
    if (!selectedTeam || !health?.ready_for_demo || submitting) return;
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
    try {
      const task = await api.createAgentTeamsTask({
        team_id: selectedTeam.id,
        leader_agent_id: selectedTeam.leader_employee_id,
        title: trimmedTitle,
        instruction: trimmedInstruction,
        material: parsedMaterial,
      });
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setSelectedTask(task);
      setGraph({ ...EMPTY_GRAPH, nodes: task.nodes });
      setEvents([]);
      setMessages([]);
      setResult(null);
      setSelectedNodeId(task.id);
      selectTask(task.id);
      setTitle('');
      setInstruction('');
      setMaterial('');
    } catch (error) {
      setActionError(`提交协作任务失败：${errorText(error)}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = async () => {
    if (!selectedTask || acting) return;
    setActing(true);
    setActionError(undefined);
    try {
      const task = await api.cancelAgentTeamsTask(selectedTask.id);
      setSelectedTask(task);
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setGraph((current) => ({ ...current, nodes: task.nodes }));
      setResult(null);
    } catch (error) {
      setActionError(`取消任务失败：${errorText(error)}`);
    } finally {
      setActing(false);
    }
  };

  const handleRetry = async () => {
    if (!selectedTask || acting) return;
    setActing(true);
    setActionError(undefined);
    try {
      const task = await api.retryAgentTeamsTask(selectedTask.id);
      setTasks((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      setSelectedTask(task);
      setGraph({ ...EMPTY_GRAPH, nodes: task.nodes });
      setEvents([]);
      setMessages([]);
      setResult(null);
      setSelectedNodeId(task.id);
      selectTask(task.id);
    } catch (error) {
      setActionError(`重试任务失败：${errorText(error)}`);
    } finally {
      setActing(false);
    }
  };

  const canSubmit = Boolean(health?.ready_for_demo && selectedTeam && title.trim() && instruction.trim() && !submitting);
  const nodes: AgentTaskNode[] = graph.nodes.length > 0 ? graph.nodes : selectedTask?.nodes ?? [];

  return (
    <div className="agentteams-page">
      <div className="at-page-heading">
        <div>
          <Typography.Title level={3}>AgentTeams 协作演示</Typography.Title>
          <Typography.Paragraph type="secondary">
            数据只来自服务端已持久化的协作状态；页面不会生成本地演示任务，也不会自行判定 Matrix 已连接。
          </Typography.Paragraph>
        </div>
        <Tag icon={<RobotOutlined />} color="geekblue">AgentTeams 协作链路</Tag>
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
                    {healthTag('Matrix', health.matrix)}
                    {healthTag('房间', health.room)}
                    {healthTag('Manager', health.manager)}
                    {workerHealthTag(health.workers)}
                  </Space>
                  <Space size={4}>
                    <Typography.Text>服务端原因：</Typography.Text>
                    <Typography.Text>{health.reason}</Typography.Text>
                  </Space>
                </Space>
              }
              className="at-alert"
            />
          )}

          <Row gutter={[16, 16]}>
            <Col xs={24} xl={8}>
              <Card title="提交协作任务" className="at-card" extra={<Tag color="blue">服务端预检</Tag>}>
                <div className="at-form-fields">
                  <label htmlFor="agentteams-team">协作团队</label>
                  <Select
                    id="agentteams-team"
                    aria-label="协作团队"
                    value={teamId}
                    onChange={setTeamId}
                    options={teams.map((team) => ({ value: team.id, label: `${team.name}（${team.id}）` }))}
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
                  <label htmlFor="agentteams-material">补充材料（可选 JSON）</label>
                  <Input.TextArea
                    id="agentteams-material"
                    value={material}
                    onChange={(event) => setMaterial(event.target.value)}
                    autoSize={{ minRows: 2, maxRows: 5 }}
                    placeholder='例如：{"policy_version":"2026.09"}'
                  />
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
                      <Button danger icon={<CloseCircleOutlined />} loading={acting} onClick={() => void handleCancel()}>
                        取消任务
                      </Button>
                    )}
                    {isTerminal(selectedTask.status) && selectedTask.status !== 'completed' && (
                      <Button icon={<ReloadOutlined />} loading={acting} onClick={() => void handleRetry()}>
                        重试
                      </Button>
                    )}
                  </Space>
                }
              >
                <Typography.Paragraph>{selectedTask.instruction}</Typography.Paragraph>
                {taskLoading && <Typography.Text type="secondary">正在刷新服务端协作轨迹…</Typography.Text>}
                <Row gutter={[16, 16]}>
                  <Col xs={24} lg={12}>
                    <TaskTree nodes={nodes} graph={graph} selectedId={selectedNodeId} onSelect={setSelectedNodeId} />
                  </Col>
                  <Col xs={24} lg={12}>
                    <Card size="small" title="当前节点状态" className="at-detail-card">
                      {selectedNode ? (
                        <Space direction="vertical" size={10} style={{ width: '100%' }}>
                          <Space size={[6, 6]} wrap>
                            <Typography.Text strong>{selectedNode.title}</Typography.Text>
                            {statusTag(selectedNode.status)}
                            <Tag>执行人：{selectedNode.assignee_agent_id}</Tag>
                            <Tag>协调人：{selectedNode.coordinator_agent_id}</Tag>
                          </Space>
                          <Progress percent={Math.max(0, Math.min(100, selectedNode.progress))} />
                          {selectedNode.error_message && <Alert type="error" showIcon message={selectedNode.error_message} />}
                          {Object.keys(selectedNode.output_payload).length > 0 && (
                            <Typography.Text type="secondary">服务端已保存该节点的受控结果，可在最终汇总中核对。</Typography.Text>
                          )}
                        </Space>
                      ) : (
                        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择一个节点查看状态" />
                      )}
                    </Card>
                  </Col>
                </Row>
              </Card>

              <TimelinePanel events={events} messages={messages} selectedTaskId={selectedNodeId} />

              {isTerminal(selectedTask.status) && (
                <Card title="最终汇总" className="at-card at-final-result">
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    <Space size={8}>{statusTag(result?.status ?? selectedTask.status)}<Typography.Text type="secondary">服务端根任务结果</Typography.Text></Space>
                    <Typography.Paragraph>
                      {result?.summary || selectedTask.summary || '服务端尚未返回最终汇总。'}
                    </Typography.Paragraph>
                  </Space>
                </Card>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
