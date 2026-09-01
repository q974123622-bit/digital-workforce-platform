import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AgentTeamsCollaborationPage from './AgentTeamsCollaborationPage';
import type {
  AgentCollaborationHealth,
  AgentCollaborationTask,
  AgentFinalResult,
  AgentMessage,
  AgentTaskEvent,
  AgentTaskGraph,
  Team,
} from '@dwp/shared-schema';
import { api } from '../../api/client';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

afterEach(() => {
  vi.unstubAllGlobals();
});

const readyHealth: AgentCollaborationHealth = {
  enabled: true,
  ready_for_demo: true,
  reason: '',
  matrix: 'ready',
  room: 'ready',
  manager: 'configured',
  workers: [
    { agent_id: 'VE-0001', matrix_user_id: '@ve-0001:matrix.test', status: 'configured' },
    { agent_id: 'VE-0002', matrix_user_id: '@ve-0002:matrix.test', status: 'configured' },
  ],
};

const teams: Team[] = [
  {
    id: 'TEAM-ONBOARD',
    name: '新员工入职团队',
    leader_employee_id: 'DT-E10281',
    description: '负责新员工入职协作',
  },
];

const rootNode = {
  id: 'AT-ROOT',
  root_task_id: 'AT-ROOT',
  parent_task_id: null,
  title: '入职协作分析',
  coordinator_agent_id: 'DT-E10281',
  assignee_agent_id: 'DT-E10281',
  status: 'completed' as const,
  progress: 100,
  depth: 0,
  required: true,
  output_payload: {},
  error_code: '',
  error_message: '',
};

const workerNode = {
  id: 'AT-POLICY',
  root_task_id: 'AT-ROOT',
  parent_task_id: 'AT-ROOT',
  title: '制度核查',
  coordinator_agent_id: 'DT-E10281',
  assignee_agent_id: 'VE-0001',
  status: 'completed' as const,
  progress: 100,
  depth: 1,
  required: true,
  output_payload: { conclusion: '制度材料已核对' },
  error_code: '',
  error_message: '',
};

const completedTask: AgentCollaborationTask = {
  id: 'AT-ROOT',
  team_id: 'TEAM-ONBOARD',
  trace_id: 'AT-ROOT',
  title: '入职协作分析',
  instruction: '完成制度和账号核查',
  status: 'completed',
  source: 'agentteams',
  summary: '制度材料和账号准备均已确认。',
  nodes: [rootNode, workerNode],
  latest_event_id: 2,
  created_at: '2026-09-01T10:00:00+08:00',
};

const completedGraph: AgentTaskGraph = {
  nodes: [rootNode, workerNode],
  parent_edges: [{ parent_task_id: 'AT-ROOT', task_id: 'AT-POLICY' }],
  dependency_edges: [{ depends_on_task_id: 'AT-ROOT', task_id: 'AT-POLICY' }],
  communication_edges: [
    {
      id: 'AM-1',
      task_id: 'AT-POLICY',
      parent_task_id: 'AT-ROOT',
      sender_agent_id: 'VE-0001',
      receiver_agent_id: 'DT-E10281',
      message_type: 'agent.message',
      direction: 'inbound',
      delivery_status: 'received',
    },
  ],
};

const completedEvents: AgentTaskEvent[] = [
  {
    id: 1,
    root_task_id: 'AT-ROOT',
    task_id: 'AT-ROOT',
    event_seq: 1,
    event_type: 'task.submit',
    actor_agent_id: 'platform',
    target_agent_id: 'DT-E10281',
    summary: '根任务已提交',
    detail: {},
    created_at: '2026-09-01T10:00:00+08:00',
  },
  {
    id: 2,
    root_task_id: 'AT-ROOT',
    task_id: 'AT-POLICY',
    event_seq: 2,
    event_type: 'task.result',
    actor_agent_id: 'VE-0001',
    target_agent_id: 'DT-E10281',
    summary: '制度核查完成',
    detail: {},
    created_at: '2026-09-01T10:00:01+08:00',
  },
];

const completedMessages: AgentMessage[] = [
  {
    id: 'AM-1',
    root_task_id: 'AT-ROOT',
    task_id: 'AT-POLICY',
    parent_task_id: 'AT-ROOT',
    message_type: 'agent.message',
    sender_agent_id: 'VE-0001',
    receiver_agent_id: 'DT-E10281',
    body: '制度核查完成，请Manager汇总。',
    payload: {},
    matrix_room_id: '!onboard:matrix.test',
    matrix_event_id: '$matrix-policy-1',
    direction: 'inbound',
    delivery_status: 'received',
    created_at: '2026-09-01T10:00:01+08:00',
  },
];

const completedResult: AgentFinalResult = {
  task_id: 'AT-ROOT',
  status: 'completed',
  summary: '制度材料和账号准备均已确认。',
  node_results: [rootNode, workerNode],
};

type MockRoute = unknown | ((init?: RequestInit) => unknown | Promise<unknown>);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function mockPageApi(routes: Record<string, MockRoute>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const requestKey = `${method} ${String(input).split('?')[0]}`;
    const matching = Object.entries(routes).find(([key]) => requestKey.endsWith(key));
    if (!matching) return json({});
    const value = await Promise.resolve(
      typeof matching[1] === 'function'
        ? (matching[1] as (request?: RequestInit) => unknown | Promise<unknown>)(init)
        : matching[1],
    );
    if (value instanceof Error) throw value;
    return json(value);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function completedTaskRoutes(task: AgentCollaborationTask = completedTask): Record<string, MockRoute> {
  return {
    'GET /api/v1/agentteams-collaboration/health': readyHealth,
    'GET /api/v1/teams': teams,
    'GET /api/v1/agentteams-collaboration/tasks': [task],
    'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/graph': completedGraph,
    'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/events': completedEvents,
    'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages': completedMessages,
    'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/result': completedResult,
    'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT': task,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AgentTeamsCollaborationPage />
    </MemoryRouter>,
  );
}

describe('AgentTeams collaboration API client', () => {
  it('uses the collaboration endpoints, encodes task IDs, and preserves zero event cursors', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({}));
    vi.stubGlobal('fetch', fetchMock);

    await api.agentTeamsHealth();
    await api.listAgentTeamsTasks();
    await api.getAgentTeamsTask('AT /1');
    await api.getAgentTeamsGraph('AT /1');
    await api.getAgentTeamsEvents('AT /1', 0);
    await api.getAgentTeamsMessages('AT /1');
    await api.getAgentTeamsResult('AT /1');
    await api.cancelAgentTeamsTask('AT /1');
    await api.retryAgentTeamsTask('AT /1');

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/v1/agentteams-collaboration/health',
      '/api/v1/agentteams-collaboration/tasks',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/graph',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/events?after_event_id=0',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/messages',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/result',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/cancel',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/retry',
    ]);
    expect(fetchMock.mock.calls[7][1]).toMatchObject({ method: 'POST' });
    expect(fetchMock.mock.calls[8][1]).toMatchObject({ method: 'POST' });
  });

  it('submits the complete collaboration task request body', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({}));
    vi.stubGlobal('fetch', fetchMock);

    await api.createAgentTeamsTask({
      team_id: 'TEAM-1',
      leader_agent_id: 'DT-E10281',
      title: '入职协作分析',
      instruction: '请拆解并完成分析',
      material: { policy_version: '2026.08' },
    });

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/v1/agentteams-collaboration/tasks');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST' });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      team_id: 'TEAM-1',
      leader_agent_id: 'DT-E10281',
      title: '入职协作分析',
      instruction: '请拆解并完成分析',
      material: { policy_version: '2026.08' },
    });
  });

  it('surfaces the backend readiness message from a structured HTTP error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json({ detail: { message: 'AgentTeams 协作环境未就绪' } }, 503)),
    );

    await expect(
      api.createAgentTeamsTask({
        team_id: 'TEAM-1',
        leader_agent_id: 'DT-E10281',
        title: '入职协作分析',
        instruction: '请拆解并完成分析',
        material: {},
      }),
    ).rejects.toThrow('AgentTeams 协作环境未就绪');
  });
});

describe('AgentTeams collaboration page', () => {
  it('submits a task only when the server health gate is ready', async () => {
    const submittedTask: AgentCollaborationTask = {
      ...completedTask,
      id: 'AT-NEW',
      trace_id: 'AT-NEW',
      status: 'planning',
      latest_event_id: 0,
      nodes: [{ ...rootNode, id: 'AT-NEW', root_task_id: 'AT-NEW', status: 'planning', progress: 0 }],
      summary: '',
    };
    const fetchMock = mockPageApi({
      'GET /api/v1/agentteams-collaboration/health': readyHealth,
      'GET /api/v1/teams': teams,
      'GET /api/v1/agentteams-collaboration/tasks': [],
      'POST /api/v1/agentteams-collaboration/tasks': submittedTask,
      'GET /api/v1/agentteams-collaboration/tasks/AT-NEW': submittedTask,
      'GET /api/v1/agentteams-collaboration/tasks/AT-NEW/graph': {
        nodes: submittedTask.nodes,
        parent_edges: [],
        dependency_edges: [],
        communication_edges: [],
      },
      'GET /api/v1/agentteams-collaboration/tasks/AT-NEW/events': [],
      'GET /api/v1/agentteams-collaboration/tasks/AT-NEW/messages': [],
      'GET /api/v1/agentteams-collaboration/tasks/AT-NEW/result': {
        task_id: 'AT-NEW',
        status: 'planning',
        summary: '',
        node_results: submittedTask.nodes,
      },
    });
    renderPage();

    expect(await screen.findByText('Matrix已连接')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('任务标题'), { target: { value: '入职协作分析' } });
    fireEvent.change(screen.getByLabelText('任务说明'), { target: { value: '完成制度和账号核查' } });
    fireEvent.click(screen.getByRole('button', { name: '提交协作任务' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith('/api/v1/agentteams-collaboration/tasks') && init?.method === 'POST',
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        team_id: 'TEAM-ONBOARD',
        leader_agent_id: 'DT-E10281',
        title: '入职协作分析',
        instruction: '完成制度和账号核查',
        material: {},
      });
    });
  });

  it('treats configured Manager and workers as pending verification when the server gate is ready', async () => {
    mockPageApi({
      'GET /api/v1/agentteams-collaboration/health': readyHealth,
      'GET /api/v1/teams': teams,
      'GET /api/v1/agentteams-collaboration/tasks': [],
    });
    renderPage();

    expect(await screen.findByText('Manager已配置/待实测')).toBeInTheDocument();
    expect(screen.getByText('Worker 已配置/待实测：2/2')).toBeInTheDocument();
    expect(screen.queryByText(/Manager未就绪/)).not.toBeInTheDocument();
  });

  it('keeps the current task details when an earlier selection resolves after a later selection', async () => {
    const taskA: AgentCollaborationTask = {
      ...completedTask,
      id: 'AT-A',
      trace_id: 'AT-A',
      title: '任务 A',
      summary: 'A 的最终汇总',
      nodes: [{ ...rootNode, id: 'AT-A', root_task_id: 'AT-A', title: 'A 节点' }],
    };
    const taskB: AgentCollaborationTask = {
      ...completedTask,
      id: 'AT-B',
      trace_id: 'AT-B',
      title: '任务 B',
      summary: 'B 的最终汇总',
      nodes: [{ ...rootNode, id: 'AT-B', root_task_id: 'AT-B', title: 'B 节点' }],
    };
    const graphA: AgentTaskGraph = { nodes: taskA.nodes, parent_edges: [], dependency_edges: [], communication_edges: [] };
    const graphB: AgentTaskGraph = { nodes: taskB.nodes, parent_edges: [], dependency_edges: [], communication_edges: [] };
    const eventsA: AgentTaskEvent[] = [{ ...completedEvents[0], root_task_id: 'AT-A', task_id: 'AT-A', summary: 'A 的轨迹事件' }];
    const eventsB: AgentTaskEvent[] = [{ ...completedEvents[0], root_task_id: 'AT-B', task_id: 'AT-B', summary: 'B 的轨迹事件' }];
    const messagesA: AgentMessage[] = [{ ...completedMessages[0], root_task_id: 'AT-A', task_id: 'AT-A', body: 'A 的 Matrix 消息' }];
    const messagesB: AgentMessage[] = [{ ...completedMessages[0], root_task_id: 'AT-B', task_id: 'AT-B', body: 'B 的 Matrix 消息' }];
    const resultA: AgentFinalResult = { task_id: 'AT-A', status: 'completed', summary: 'A 的最终汇总', node_results: taskA.nodes };
    const resultB: AgentFinalResult = { task_id: 'AT-B', status: 'completed', summary: 'B 的最终汇总', node_results: taskB.nodes };
    const delayedA = {
      task: deferred<AgentCollaborationTask>(),
      graph: deferred<AgentTaskGraph>(),
      events: deferred<AgentTaskEvent[]>(),
      messages: deferred<AgentMessage[]>(),
      result: deferred<AgentFinalResult>(),
    };
    const fetchMock = mockPageApi({
      'GET /api/v1/agentteams-collaboration/health': readyHealth,
      'GET /api/v1/teams': teams,
      'GET /api/v1/agentteams-collaboration/tasks': [taskA, taskB],
      'GET /api/v1/agentteams-collaboration/tasks/AT-A': () => delayedA.task.promise,
      'GET /api/v1/agentteams-collaboration/tasks/AT-A/graph': () => delayedA.graph.promise,
      'GET /api/v1/agentteams-collaboration/tasks/AT-A/events': () => delayedA.events.promise,
      'GET /api/v1/agentteams-collaboration/tasks/AT-A/messages': () => delayedA.messages.promise,
      'GET /api/v1/agentteams-collaboration/tasks/AT-A/result': () => delayedA.result.promise,
      'GET /api/v1/agentteams-collaboration/tasks/AT-B': taskB,
      'GET /api/v1/agentteams-collaboration/tasks/AT-B/graph': graphB,
      'GET /api/v1/agentteams-collaboration/tasks/AT-B/events': eventsB,
      'GET /api/v1/agentteams-collaboration/tasks/AT-B/messages': messagesB,
      'GET /api/v1/agentteams-collaboration/tasks/AT-B/result': resultB,
    });
    renderPage();

    await screen.findByRole('button', { name: '查看协作任务 任务 A' });
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/agentteams-collaboration/tasks/AT-A/result'))).toBe(true);
    });
    fireEvent.click(screen.getByRole('button', { name: '查看协作任务 任务 B' }));

    expect(await screen.findByText('B 的最终汇总')).toBeInTheDocument();
    await act(async () => {
      delayedA.task.resolve(taskA);
      delayedA.graph.resolve(graphA);
      delayedA.events.resolve(eventsA);
      delayedA.messages.resolve(messagesA);
      delayedA.result.resolve(resultA);
    });

    await waitFor(() => {
      expect(screen.getAllByText('B 节点')).toHaveLength(2);
      expect(screen.getByText('B 的轨迹事件')).toBeInTheDocument();
      expect(screen.getByText('B 的 Matrix 消息')).toBeInTheDocument();
      expect(screen.getByText('B 的最终汇总')).toBeInTheDocument();
      expect(screen.queryAllByText('A 节点')).toHaveLength(0);
      expect(screen.queryByText('A 的轨迹事件')).not.toBeInTheDocument();
      expect(screen.queryByText('A 的 Matrix 消息')).not.toBeInTheDocument();
      expect(screen.queryByText('A 的最终汇总')).not.toBeInTheDocument();
    });
  });

  it('renders recursive delegation, relationship edges, and the final summary', async () => {
    mockPageApi(completedTaskRoutes());
    renderPage();

    expect(await screen.findByText('入职协作分析')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /选择任务 制度核查/ })).toBeInTheDocument();
    expect(screen.getByText('父子委派')).toBeInTheDocument();
    expect(screen.getByText('任务依赖')).toBeInTheDocument();
    expect(screen.getByText('消息关系')).toBeInTheDocument();
    expect(await screen.findByText('制度材料和账号准备均已确认。')).toBeInTheDocument();
  });

  it('filters the timeline and Matrix messages when a child task is selected', async () => {
    mockPageApi(completedTaskRoutes());
    renderPage();

    await screen.findByText('根任务已提交');
    fireEvent.click(screen.getByRole('button', { name: /选择任务 制度核查/ }));

    expect(await screen.findByText('制度核查完成')).toBeInTheDocument();
    expect(screen.getByText('制度核查完成，请Manager汇总。')).toBeInTheDocument();
    expect(screen.queryByText('根任务已提交')).not.toBeInTheDocument();
  });

  it('disables submission and shows the exact server reason when Matrix is unavailable', async () => {
    mockPageApi({
      'GET /api/v1/agentteams-collaboration/health': {
        ...readyHealth,
        ready_for_demo: false,
        matrix: 'unreachable',
        reason: 'Matrix 登录失败：连接被拒绝',
      },
      'GET /api/v1/teams': teams,
      'GET /api/v1/agentteams-collaboration/tasks': [],
    });
    renderPage();

    expect(await screen.findByText('Matrix 登录失败：连接被拒绝')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '提交协作任务' })).toBeDisabled();
  });

  it('shows protocol errors without marking a running node completed', async () => {
    const runningNode = { ...rootNode, status: 'running' as const, progress: 50 };
    const runningTask: AgentCollaborationTask = {
      ...completedTask,
      status: 'running',
      nodes: [runningNode],
      latest_event_id: 1,
      summary: '',
    };
    const routes = completedTaskRoutes(runningTask);
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/graph'] = {
      nodes: [runningNode],
      parent_edges: [],
      dependency_edges: [],
      communication_edges: [],
    };
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/events'] = [
      {
        ...completedEvents[0],
        event_type: 'protocol.invalid',
        summary: '收到无法验证的协议消息',
      },
    ];
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages'] = [];
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/result'] = { ...completedResult, status: 'running', summary: '', node_results: [runningNode] };
    mockPageApi(routes);
    renderPage();

    expect((await screen.findAllByText('收到无法验证的协议消息')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('执行中').length).toBeGreaterThan(0);
    expect(screen.queryByText('最终汇总')).not.toBeInTheDocument();
  });

  it('stops polling after a historical task is already terminal', async () => {
    const fetchMock = mockPageApi(completedTaskRoutes());
    renderPage();

    await screen.findByText('制度材料和账号准备均已确认。');
    const before = fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/api/v1/agentteams-collaboration/tasks/AT-ROOT')).length;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1100));
    });
    const after = fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/api/v1/agentteams-collaboration/tasks/AT-ROOT')).length;
    expect(after).toBe(before);
  }, 3000);

  it('polls a running root task at one-second intervals', async () => {
    const runningNode = { ...rootNode, status: 'running' as const, progress: 30 };
    const runningTask: AgentCollaborationTask = {
      ...completedTask,
      status: 'running',
      nodes: [runningNode],
      latest_event_id: 0,
      summary: '',
    };
    const routes = completedTaskRoutes(runningTask);
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/graph'] = {
      nodes: [runningNode],
      parent_edges: [],
      dependency_edges: [],
      communication_edges: [],
    };
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/events'] = [];
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages'] = [];
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/result'] = {
      task_id: 'AT-ROOT', status: 'running', summary: '', node_results: [runningNode],
    };
    const fetchMock = mockPageApi(routes);
    renderPage();

    await screen.findByText('执行中');
    const before = fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/api/v1/agentteams-collaboration/tasks/AT-ROOT')).length;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1100));
    });
    await waitFor(() => {
      const after = fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/api/v1/agentteams-collaboration/tasks/AT-ROOT')).length;
      expect(after).toBeGreaterThan(before);
    });
  }, 4000);

  it('shows the backend error when cancelling a running task fails', async () => {
    const runningNode = { ...rootNode, status: 'running' as const, progress: 30 };
    const runningTask: AgentCollaborationTask = { ...completedTask, status: 'running', nodes: [runningNode], summary: '' };
    const routes = completedTaskRoutes(runningTask);
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/graph'] = {
      nodes: [runningNode], parent_edges: [], dependency_edges: [], communication_edges: [],
    };
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/events'] = [];
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages'] = [];
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/result'] = {
      task_id: 'AT-ROOT', status: 'running', summary: '', node_results: [runningNode],
    };
    routes['POST /api/v1/agentteams-collaboration/tasks/AT-ROOT/cancel'] = new Error('任务已被其他操作者终止');
    mockPageApi(routes);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /取消任务/ }));
    expect(await screen.findByText('取消任务失败：任务已被其他操作者终止')).toBeInTheDocument();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1100));
    });
    expect(screen.getByText('取消任务失败：任务已被其他操作者终止')).toBeInTheDocument();
  });

  it('shows the backend error when retrying a failed task fails', async () => {
    const failedNode = { ...rootNode, status: 'failed' as const, progress: 30, error_message: 'Worker 中断' };
    const failedTask: AgentCollaborationTask = { ...completedTask, status: 'failed', nodes: [failedNode], summary: '任务未能完成。' };
    const routes = completedTaskRoutes(failedTask);
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/graph'] = {
      nodes: [failedNode], parent_edges: [], dependency_edges: [], communication_edges: [],
    };
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/events'] = [];
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages'] = [];
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/result'] = {
      task_id: 'AT-ROOT', status: 'failed', summary: '任务未能完成。', node_results: [failedNode],
    };
    routes['POST /api/v1/agentteams-collaboration/tasks/AT-ROOT/retry'] = new Error('健康预检未通过');
    mockPageApi(routes);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /重试/ }));
    expect(await screen.findByText('重试任务失败：健康预检未通过')).toBeInTheDocument();
  });

  it('restores the most recent historical task after the page reloads', async () => {
    const fetchMock = mockPageApi(completedTaskRoutes());
    const first = renderPage();
    expect(await screen.findByText('制度材料和账号准备均已确认。')).toBeInTheDocument();
    first.unmount();

    renderPage();
    expect(await screen.findByText('制度材料和账号准备均已确认。')).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/api/v1/agentteams-collaboration/tasks')).length,
    ).toBeGreaterThanOrEqual(2);
  });
});
