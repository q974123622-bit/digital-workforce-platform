import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AgentTeamsCollaborationPage from './AgentTeamsCollaborationPage';
import { TaskTree } from './TaskTree';
import { CurrentUserProvider, useCurrentUser } from '../../context/CurrentUserContext';
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
  localStorage.removeItem('dwp.current-actor');
});

const readyHealth: AgentCollaborationHealth = {
  enabled: true,
  ready_for_demo: true,
  reason: '',
  mode: 'agentteams_strict',
  transport: 'matrix',
  matrix: 'ready',
  room: 'ready',
  manager: 'configured',
  platform_team: 'ready',
  workers: [
    { agent_id: 'VE-0001', matrix_user_id: '@ve-0001:matrix.test', status: 'configured' },
    { agent_id: 'VE-0002', matrix_user_id: '@ve-0002:matrix.test', status: 'configured' },
  ],
};

const localReadyHealth: AgentCollaborationHealth = {
  ...readyHealth,
  mode: 'local_demo',
  transport: 'local',
  matrix: 'not_connected',
  room: 'local',
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
  node_kind: 'mission' as const,
  revision_of_task_id: null,
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
  node_kind: 'worker_task' as const,
  revision_of_task_id: null,
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

const processMessages: AgentMessage[] = [
  {
    ...completedMessages[0],
    id: 'AM-ROOT-SKILL',
    task_id: 'AT-ROOT',
    parent_task_id: null,
    message_type: 'agent.message',
    sender_agent_id: 'DT-E10281',
    receiver_agent_id: 'platform',
    payload: {
      payload: {
        kind: 'skill.selected',
        selected_skill_ids: ['agentteams-onboarding-readiness'],
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-ROOT-TRACE-1',
    task_id: 'AT-ROOT',
    parent_task_id: null,
    message_type: 'agent.message',
    sender_agent_id: 'DT-E10281',
    receiver_agent_id: 'platform',
    payload: {
      payload: {
        kind: 'decision.trace',
        stage: 'skill_selection',
        iteration: 1,
        reasoning_segments: ['这是入职准备度场景，需要先使用对应的场景Skill。'],
        action: 'select_skill',
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-ROOT-TRACE-2',
    task_id: 'AT-ROOT',
    parent_task_id: null,
    message_type: 'agent.message',
    sender_agent_id: 'DT-E10281',
    receiver_agent_id: 'platform',
    payload: {
      payload: {
        kind: 'decision.trace',
        stage: 'decision',
        iteration: 1,
        reasoning_segments: ['制度核查是当前必需证据，先派发给HR节点。'],
        action: 'delegate',
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-ROOT-DELEGATE-1',
    task_id: 'AT-ROOT',
    parent_task_id: null,
    message_type: 'task.delegate',
    sender_agent_id: 'DT-E10281',
    receiver_agent_id: 'platform',
    payload: {
      payload: {
        round: 1,
        subtasks: [
          {
            client_task_key: 'policy-check',
            title: '制度核查',
            instruction: '核对员工制度材料并确认是否存在缺口。',
            assignee_agent_id: 'VE-0001',
            required_capability: 'hr.onboarding.read',
            depends_on: [],
            required: true,
            material: {},
          },
        ],
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-WORKER-ASSIGN',
    message_type: 'task.assign',
    sender_agent_id: 'DT-E10281',
    receiver_agent_id: 'VE-0001',
    payload: {
      payload: {
        instruction: '核对员工制度材料并确认是否存在缺口。',
        required_capability: 'hr.onboarding.read',
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-WORKER-SKILL',
    message_type: 'agent.message',
    payload: {
      payload: {
        kind: 'skill.selected',
        selected_skill_ids: ['hr-onboarding-assistant'],
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-WORKER-TRACE-1',
    message_type: 'agent.message',
    payload: {
      payload: {
        kind: 'decision.trace',
        stage: 'skill_selection',
        iteration: 1,
        reasoning_segments: ['任务要求核查制度材料，因此选择HR入职Skill。'],
        action: 'select_skill',
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-WORKER-TRACE-2',
    message_type: 'agent.message',
    payload: {
      payload: {
        kind: 'decision.trace',
        stage: 'decision',
        iteration: 1,
        reasoning_segments: ['需要先查询制度材料，才能给出有证据的结论。'],
        action: 'tool',
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-WORKER-TOOL-REQUEST',
    message_type: 'tool.request',
    receiver_agent_id: 'platform',
    payload: {
      payload: {
        tool_name: 'query_onboarding_employee',
        required_capability: 'hr.onboarding.read',
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-WORKER-TOOL-RESULT',
    message_type: 'tool.result',
    sender_agent_id: 'platform',
    receiver_agent_id: 'VE-0001',
    payload: {
      tool_name: 'query_onboarding_employee',
      decision: 'allow',
      audit_ids: [101],
      data: '制度材料齐全',
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-WORKER-TRACE-3',
    message_type: 'agent.message',
    payload: {
      payload: {
        kind: 'decision.trace',
        stage: 'decision',
        iteration: 2,
        reasoning_segments: ['Gateway已返回材料齐全，可以结束本节点并提交结果。'],
        action: 'complete',
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-WORKER-RESULT',
    message_type: 'task.result',
    receiver_agent_id: 'platform',
    payload: {
      payload: {
        result: '制度材料已核对，等待Manager汇总。',
        audit_ids: [101],
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-ROOT-REVIEW-1',
    task_id: 'AT-ROOT',
    parent_task_id: null,
    message_type: 'task.summary',
    sender_agent_id: 'platform',
    receiver_agent_id: 'DT-E10281',
    payload: {
      request: 'task.summary',
      current_round: 1,
      child_results: [{ client_task_key: 'policy-check', status: 'completed' }],
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-ROOT-TRACE-3',
    task_id: 'AT-ROOT',
    parent_task_id: null,
    message_type: 'agent.message',
    sender_agent_id: 'DT-E10281',
    receiver_agent_id: 'platform',
    payload: {
      payload: {
        kind: 'decision.trace',
        stage: 'decision',
        iteration: 2,
        reasoning_segments: ['已有制度结果，但还缺审计证据，需要补充派发。'],
        action: 'delegate',
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-ROOT-DELEGATE-2',
    task_id: 'AT-ROOT',
    parent_task_id: null,
    message_type: 'task.delegate',
    sender_agent_id: 'DT-E10281',
    receiver_agent_id: 'platform',
    payload: {
      payload: {
        round: 2,
        subtasks: [
          {
            client_task_key: 'audit-review',
            title: '补充审计复核',
            instruction: '补充核对审计证据。',
            assignee_agent_id: 'VE-0005',
            required_capability: 'audit.evidence.read',
            depends_on: [],
            required: true,
            material: {},
          },
        ],
      },
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-ROOT-REVIEW-2',
    task_id: 'AT-ROOT',
    parent_task_id: null,
    message_type: 'task.summary',
    sender_agent_id: 'platform',
    receiver_agent_id: 'DT-E10281',
    payload: {
      request: 'task.summary',
      current_round: 2,
      child_results: [{ client_task_key: 'audit-review', status: 'completed' }],
    },
  },
  {
    ...completedMessages[0],
    id: 'AM-ROOT-TRACE-4',
    task_id: 'AT-ROOT',
    parent_task_id: null,
    message_type: 'agent.message',
    sender_agent_id: 'DT-E10281',
    receiver_agent_id: 'platform',
    payload: {
      payload: {
        kind: 'decision.trace',
        stage: 'decision',
        iteration: 3,
        reasoning_segments: ['制度与审计证据都已返回，满足最终汇总条件。'],
        action: 'complete',
      },
    },
    created_at: '2026-09-01T10:01:06+08:00',
  },
];

const completedResult: AgentFinalResult = {
  task_id: 'AT-ROOT',
  status: 'completed',
  summary: '制度材料和账号准备均已确认。',
  node_results: [rootNode, workerNode],
};

const completedEvaluation = {
  version: 'agentteams-evaluation-v1',
  evaluated_at: '2026-09-07T09:30:00+08:00',
  rule_score: 58,
  rule_max_score: 60,
  rule_dimensions: [
    { key: 'completion', label: '任务完成', score: 10, max_score: 10, reason: '必需任务和最终汇总均已完成。' },
    { key: 'delegation', label: '拆解与派发', score: 10, max_score: 10, reason: '任务已拆给不同职责的Agent。' },
  ],
  rule_deductions: ['一条消息发生过重试。'],
  model_status: 'completed',
  model_score: 34,
  model_max_score: 40,
  model_dimensions: [
    { key: 'decomposition', label: '任务拆解', score: 9, max_score: 10, reason: '拆解清楚。' },
    { key: 'routing', label: 'Agent选择', score: 9, max_score: 10, reason: '职责匹配。' },
    { key: 'follow_up', label: '后续判断', score: 8, max_score: 10, reason: '收到结果后继续派发。' },
    { key: 'final_answer', label: '最终答案', score: 8, max_score: 10, reason: '结论完整。' },
  ],
  model_strengths: ['并行收集证据后继续派发'],
  model_deductions: ['最终答案可以更精简。'],
  model_verdict: '协作过程完整，结果可信。',
  total_score: 92,
  total_max_score: 100,
} as const;

const roleChangeNodes = [
  { ...rootNode, id: 'AT-RC', root_task_id: 'AT-RC', client_task_key: 'root', title: '岗位调整评估' },
  { ...workerNode, id: 'AT-RC-HR', root_task_id: 'AT-RC', parent_task_id: 'AT-RC', client_task_key: 'hr-check', title: 'HR岗位调整核查', assignee_agent_id: 'VE-0002' },
  { ...workerNode, id: 'AT-RC-PLAN', root_task_id: 'AT-RC', parent_task_id: 'AT-RC', client_task_key: 'it-plan', title: 'IT权限计划核查', assignee_agent_id: 'VE-0003' },
  { ...workerNode, id: 'AT-RC-AUDIT', root_task_id: 'AT-RC', parent_task_id: 'AT-RC', client_task_key: 'audit-review', title: '权限审计复核', assignee_agent_id: 'VE-0005' },
  { ...workerNode, id: 'AT-RC-ACTIVATE', root_task_id: 'AT-RC', parent_task_id: 'AT-RC', client_task_key: 'it-activate', title: 'IT权限激活', assignee_agent_id: 'VE-0003' },
];

const roleChangeEvents: AgentTaskEvent[] = [
  { ...completedEvents[0], root_task_id: 'AT-RC', task_id: 'AT-RC', id: 10, event_seq: 10, event_type: 'task.delegate', detail: { round: 1, client_task_keys: ['hr-check', 'it-plan'] } },
  { ...completedEvents[0], root_task_id: 'AT-RC', task_id: 'AT-RC', id: 20, event_seq: 20, event_type: 'task.summarizing', summary: 'Manager复核第一轮结果', detail: { round: 1 } },
  { ...completedEvents[0], root_task_id: 'AT-RC', task_id: 'AT-RC', id: 30, event_seq: 30, event_type: 'task.delegate', detail: { round: 2, client_task_keys: ['audit-review', 'it-activate'] } },
];

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
    const rawRequestKey = `${method} ${String(input)}`;
    const requestKey = `${method} ${String(input).split('?')[0]}`;
    const matching = Object.entries(routes).find(([key]) => rawRequestKey.endsWith(key))
      ?? Object.entries(routes).find(([key]) => requestKey.endsWith(key));
    if (!matching) throw new Error(`未匹配的页面请求：${requestKey}`);
    const value = await Promise.resolve(
      typeof matching[1] === 'function'
        ? (matching[1] as (request?: RequestInit) => unknown | Promise<unknown>)(init)
        : matching[1],
    );
    if (value === undefined) throw new Error(`页面请求返回 undefined：${matching[0]}`);
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
    'POST /api/v1/agentteams-collaboration/tasks/AT-ROOT/evaluation': completedEvaluation,
  };
}

function actionRaceTask(
  id: string,
  title: string,
  status: AgentCollaborationTask['status'],
  summary: string,
): AgentCollaborationTask {
  const node = {
    ...rootNode,
    id,
    root_task_id: id,
    title: `${title} 节点`,
    status,
    progress: status === 'running' ? 40 : 100,
  };
  return { ...completedTask, id, trace_id: id, title, instruction: `${title} 说明`, status, summary, nodes: [node], latest_event_id: 1 };
}

function renderPage() {
  return render(
    <CurrentUserProvider>
      <MemoryRouter>
        <AgentTeamsCollaborationPage />
      </MemoryRouter>
    </CurrentUserProvider>,
  );
}

function ActorSwitcher() {
  const { setActor } = useCurrentUser();
  return <button type="button" onClick={() => setActor('E20999')}>切换为 E20999</button>;
}

function renderPageWithActorSwitcher() {
  return render(
    <CurrentUserProvider>
      <MemoryRouter>
        <ActorSwitcher />
        <AgentTeamsCollaborationPage />
      </MemoryRouter>
    </CurrentUserProvider>,
  );
}

describe('AgentTeams collaboration API client', () => {
  it('uses the collaboration endpoints, encodes task IDs, and preserves zero event cursors', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({}));
    vi.stubGlobal('fetch', fetchMock);
    const actorNo = 'E10281';

    await api.agentTeamsHealth();
    await api.listAgentTeamsTasks(actorNo);
    await api.getAgentTeamsTask(actorNo, 'AT /1');
    await api.getAgentTeamsGraph(actorNo, 'AT /1');
    await api.getAgentTeamsEvents(actorNo, 'AT /1', 0);
    await api.getAgentTeamsMessages(actorNo, 'AT /1');
    await api.getAgentTeamsResult(actorNo, 'AT /1');
    await api.evaluateAgentTeamsTask(actorNo, 'AT /1');
    await api.cancelAgentTeamsTask(actorNo, 'AT /1');
    await api.retryAgentTeamsTask(actorNo, 'AT /1');

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/v1/agentteams-collaboration/health',
      '/api/v1/agentteams-collaboration/tasks?actor_no=E10281',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1?actor_no=E10281',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/graph?actor_no=E10281',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/events?actor_no=E10281&after_event_id=0',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/messages?actor_no=E10281',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/result?actor_no=E10281',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/evaluation?actor_no=E10281',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/cancel?actor_no=E10281',
      '/api/v1/agentteams-collaboration/tasks/AT%20%2F1/retry?actor_no=E10281',
    ]);
    expect(fetchMock.mock.calls[7][1]).toMatchObject({ method: 'POST' });
    expect(fetchMock.mock.calls[8][1]).toMatchObject({ method: 'POST' });
    expect(fetchMock.mock.calls[9][1]).toMatchObject({ method: 'POST' });
  });

  it('submits the complete collaboration task request body', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({}));
    vi.stubGlobal('fetch', fetchMock);

    await api.createAgentTeamsTask('E10281', {
      team_id: 'TEAM-1',
      leader_agent_id: 'DT-E10281',
      title: '入职协作分析',
      instruction: '请拆解并完成分析',
      material: { policy_version: '2026.08' },
      thinking_enabled: false,
    });

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/v1/agentteams-collaboration/tasks?actor_no=E10281');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST' });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      team_id: 'TEAM-1',
      leader_agent_id: 'DT-E10281',
      title: '入职协作分析',
      instruction: '请拆解并完成分析',
      material: { policy_version: '2026.08' },
      thinking_enabled: false,
    });
  });

  it('surfaces the backend readiness message from a structured HTTP error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json({ detail: { message: 'AgentTeams 协作环境未就绪' } }, 503)),
    );

    await expect(
      api.createAgentTeamsTask('E10281', {
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
  it('renders direct delegation as a hierarchy chart with compact Tool leaves', () => {
    const toolMessages: AgentMessage[] = [
      {
        ...completedMessages[0],
        id: 'AM-RC-HR-REQUEST',
        task_id: 'AT-RC-HR',
        message_type: 'tool.request',
        sender_agent_id: 'VE-0002',
        receiver_agent_id: 'platform',
        payload: { tool_name: 'agentteams.hr.lookup_role_change' },
      },
      {
        ...completedMessages[0],
        id: 'AM-RC-HR-RESULT',
        task_id: 'AT-RC-HR',
        message_type: 'tool.result',
        sender_agent_id: 'platform',
        receiver_agent_id: 'VE-0002',
        payload: {
          tool_name: 'agentteams.hr.lookup_role_change',
          decision: 'allow',
          audit_ids: [89],
        },
      },
    ];
    render(
      <TaskTree
        nodes={roleChangeNodes}
        graph={{
          nodes: roleChangeNodes,
          parent_edges: roleChangeNodes.slice(1).map((node) => ({ parent_task_id: 'AT-RC', task_id: node.id })),
          dependency_edges: [{ depends_on_task_id: 'AT-RC-AUDIT', task_id: 'AT-RC-ACTIVATE' }],
          communication_edges: [],
        }}
        events={roleChangeEvents}
        messages={toolMessages}
        selectedId="AT-RC"
        onSelect={() => undefined}
      />,
    );

    expect(screen.getByRole('button', { name: /选择任务 岗位调整评估/ })).toBeInTheDocument();
    const chart = screen.getByTestId('agentteams-task-chart');
    expect(chart.querySelector('[data-parent-task-id="AT-RC"]')).not.toBeNull();
    expect(chart.querySelectorAll('.at-chart-node-card')).toHaveLength(5);
    expect(chart).toHaveTextContent('HR岗位调整核查');
    expect(chart).toHaveTextContent('IT权限计划核查');
    expect(chart).toHaveTextContent('权限审计复核');
    expect(chart).toHaveTextContent('IT权限激活');
    expect(screen.getByText('岗位调整HR记录查询Tool · Gateway通过 · 审计#89')).toBeInTheDocument();
    expect(screen.queryByText('第1轮')).not.toBeInTheDocument();
    expect(screen.queryByText('第2轮')).not.toBeInTheDocument();
  });

  it('sizes department branches from their direct reports so every specialist remains in the chart flow', () => {
    const manager = { ...rootNode, id: 'AT-EXPANDED', root_task_id: 'AT-EXPANDED', assignee_agent_id: 'VE-0001' };
    const hr = { ...workerNode, id: 'AT-HR', root_task_id: 'AT-EXPANDED', parent_task_id: 'AT-EXPANDED', title: 'HR 部门负责人', assignee_agent_id: 'VE-0002' };
    const it = { ...workerNode, id: 'AT-IT', root_task_id: 'AT-EXPANDED', parent_task_id: 'AT-EXPANDED', title: 'IT 部门负责人', assignee_agent_id: 'VE-0003' };
    const audit = { ...workerNode, id: 'AT-AUDIT', root_task_id: 'AT-EXPANDED', parent_task_id: 'AT-EXPANDED', title: '审计复核', assignee_agent_id: 'VE-0005' };
    const nodes = [
      manager,
      hr,
      it,
      audit,
      { ...workerNode, id: 'AT-HR-MATERIAL', root_task_id: 'AT-EXPANDED', parent_task_id: 'AT-HR', title: 'HR 材料专员', assignee_agent_id: 'VE-HR-MATERIAL' },
      { ...workerNode, id: 'AT-HR-TRAINING', root_task_id: 'AT-EXPANDED', parent_task_id: 'AT-HR', title: 'HR 培训专员', assignee_agent_id: 'VE-HR-TRAINING' },
      { ...workerNode, id: 'AT-IT-ACCOUNT', root_task_id: 'AT-EXPANDED', parent_task_id: 'AT-IT', title: 'IT 账号专员', assignee_agent_id: 'VE-IT-ACCOUNT' },
      { ...workerNode, id: 'AT-IT-DEVICE', root_task_id: 'AT-EXPANDED', parent_task_id: 'AT-IT', title: 'IT 设备专员', assignee_agent_id: 'VE-IT-DEVICE' },
    ];

    render(<TaskTree nodes={nodes} messages={[]} selectedId="AT-EXPANDED" onSelect={() => undefined} />);

    expect(screen.getByTestId('agentteams-task-chart').querySelectorAll('.at-chart-node-card')).toHaveLength(8);
    expect(screen.getByRole('button', { name: /选择任务 入职协作分析/ }).closest('li')).toHaveClass('at-chart-branch');
    expect(screen.getByRole('button', { name: /选择任务 HR 部门负责人/ }).closest('li')).toHaveClass('at-chart-branch');
    expect(screen.getByRole('button', { name: /选择任务 IT 部门负责人/ }).closest('li')).toHaveClass('at-chart-branch');
    expect(screen.getByRole('button', { name: /选择任务 HR 材料专员/ }).closest('li')).not.toHaveClass('at-chart-branch');
  });

  it('shows explicit Leader assignment and result-review states', () => {
    const leaderAssignment = {
      ...workerNode,
      id: 'AT-LEADER',
      node_kind: 'team_assignment' as const,
      status: 'reviewing' as const,
      title: 'HR 团队任务',
      assignee_agent_id: 'VE-0002',
    };
    const revision = {
      ...workerNode,
      id: 'AT-REVISION',
      parent_task_id: 'AT-LEADER',
      node_kind: 'worker_task' as const,
      revision_of_task_id: 'AT-WORKER-V1',
      status: 'revision_requested' as const,
      title: '补充审批证据',
    };
    const workerReview = {
      ...workerNode,
      id: 'AT-WORKER-REVIEW',
      parent_task_id: 'AT-LEADER',
      node_kind: 'worker_task' as const,
      status: 'reviewing' as const,
      title: '等待负责人验收',
    };

    render(
      <TaskTree
        nodes={[rootNode, leaderAssignment, revision, workerReview]}
        messages={[]}
        selectedId="AT-LEADER"
        onSelect={() => undefined}
      />,
    );

    const chart = screen.getByTestId('agentteams-task-chart');
    expect(chart).toHaveTextContent('Leader 任务');
    expect(within(screen.getByRole('button', { name: /选择任务 HR 团队任务/ })).getByText('待 Manager 验收')).toBeInTheDocument();
    expect(chart).toHaveTextContent('待 Leader 验收');
    expect(chart).toHaveTextContent('Worker 任务');
    expect(chart).toHaveTextContent('已要求修订');
    expect(chart).toHaveTextContent('修订自：AT-WORKER-V1');
  });

  it('keeps persisted hierarchy separate from serial dependencies when nodes arrive out of order', () => {
    const mixedNodes = [
      roleChangeNodes[0],
      { ...roleChangeNodes[4], client_task_key: 'activate' },
      { ...roleChangeNodes[2], client_task_key: 'plan' },
      { ...roleChangeNodes[1], client_task_key: 'hr' },
    ];
    render(
      <TaskTree
        nodes={mixedNodes}
        graph={{
          nodes: mixedNodes,
          parent_edges: mixedNodes.slice(1).map((node) => ({ parent_task_id: 'AT-RC', task_id: node.id })),
          dependency_edges: [{ depends_on_task_id: 'AT-RC-PLAN', task_id: 'AT-RC-ACTIVATE' }],
          communication_edges: [],
        }}
        events={[{ ...roleChangeEvents[0], detail: { round: 1, client_task_keys: ['activate', 'plan', 'hr'] } }]}
        messages={[]}
        selectedId="AT-RC"
        onSelect={() => undefined}
      />,
    );

    const chart = screen.getByTestId('agentteams-task-chart');
    const planBranch = chart.querySelector('[data-parent-task-id="AT-RC-PLAN"]');
    expect(planBranch).toBeNull();
    const managerBranch = chart.querySelector('[data-parent-task-id="AT-RC"]');
    expect(managerBranch).not.toBeNull();
    expect(managerBranch).toHaveTextContent('IT权限激活');
    expect(managerBranch).toHaveTextContent('IT权限计划');
    expect(screen.queryByText('第1轮')).not.toBeInTheDocument();
  });

  it('displays a completed Manager node as 100 percent when persisted progress has not caught up', async () => {
    const laggingRoot = { ...rootNode, progress: 0 };
    const task = { ...completedTask, nodes: [laggingRoot] };
    const routes = completedTaskRoutes(task);
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/graph'] = {
      nodes: [laggingRoot], parent_edges: [], dependency_edges: [], communication_edges: [],
    };
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/result'] = {
      ...completedResult, node_results: [laggingRoot],
    };
    mockPageApi(routes);
    renderPage();

    const detailPanel = await screen.findByTestId('agentteams-node-detail');
    expect(within(detailPanel).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
  });

  it('renders persisted skills, round, blocked dependency, audit, and real Matrix evidence', async () => {
    const blockedNode = {
      ...workerNode,
      id: 'AT-IT',
      title: 'IT 依赖核查',
      status: 'blocked' as const,
      progress: 0,
      output_payload: {},
    };
    const task = { ...completedTask, nodes: [rootNode, workerNode, blockedNode] };
    const graph = {
      ...completedGraph,
      nodes: [rootNode, workerNode, blockedNode],
      current_round: 2,
      selected_skill_ids: ['agentteams-onboarding-readiness'],
      dependency_edges: [{ depends_on_task_id: 'AT-POLICY', task_id: 'AT-IT' }],
    } as unknown as AgentTaskGraph;
    const messages = [
      {
        ...completedMessages[0],
        message_type: 'tool.result',
        task_id: 'AT-ROOT',
        parent_task_id: null,
        matrix_event_id: '$event-101',
        payload: {
          tool_name: 'agentteams-onboarding-employee',
          decision: 'allow',
          audit_ids: [101],
          source: 'demo',
        },
      },
    ];
    mockPageApi({
      ...completedTaskRoutes(task),
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/graph': graph,
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages': messages,
    });
    renderPage();

    expect(await screen.findByText('已选择 Skill：agentteams-onboarding-readiness')).toBeInTheDocument();
    expect(screen.getByText('第 2 轮')).toBeInTheDocument();
    expect(screen.getByText('等待前置任务：AT-POLICY')).toBeInTheDocument();
    expect(screen.getByText('工具调用：agentteams-onboarding-employee')).toBeInTheDocument();
    expect(screen.getByText('权限检查：允许')).toBeInTheDocument();
    expect(screen.getByText('审计记录ID：101')).toBeInTheDocument();
    expect(screen.getByText(/Matrix事件：\$event-101/)).toBeInTheDocument();
  });

  it('keeps the one-line goal editable instead of pre-filling a detailed scenario', async () => {
    mockPageApi({
      'GET /api/v1/agentteams-collaboration/health': readyHealth,
      'GET /api/v1/teams': teams,
      'GET /api/v1/agentteams-collaboration/tasks': [],
    });
    renderPage();

    const instruction = await screen.findByLabelText('任务说明');
    expect(instruction).toHaveValue('评估张明的岗位调整是否可以按期完成。');
    expect(instruction).not.toBeDisabled();
    expect(screen.getByLabelText('任务标题')).not.toBeDisabled();
    expect(screen.getByLabelText('补充材料（可选 JSON）')).not.toBeDisabled();
  });

  it('shows the server-reported local boundary and labels local message IDs as transport events', async () => {
    mockPageApi({
      ...completedTaskRoutes(),
      'GET /api/v1/agentteams-collaboration/health': localReadyHealth,
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages': [
        { ...completedMessages[0], task_id: 'AT-ROOT', parent_task_id: null },
      ],
    });
    renderPage();

    expect(await screen.findByText('本地Demo（未连接Matrix）')).toBeInTheDocument();
    expect(screen.queryByText('真实AgentTeams / Matrix')).not.toBeInTheDocument();
    expect(await screen.findByText(/Transport事件ID：\$matrix-policy-1/)).toBeInTheDocument();
    expect(screen.queryByText(/Matrix事件ID：/)).not.toBeInTheDocument();
  });

  it('shows the strict boundary and labels strict message IDs as Matrix events', async () => {
    mockPageApi({
      ...completedTaskRoutes(),
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages': [
        { ...completedMessages[0], task_id: 'AT-ROOT', parent_task_id: null },
      ],
    });
    renderPage();

    expect(await screen.findByText('真实AgentTeams / Matrix')).toBeInTheDocument();
    expect(screen.queryByText('本地Demo（未连接Matrix）')).not.toBeInTheDocument();
    expect(await screen.findByText(/Matrix事件：\$matrix-policy-1/)).toBeInTheDocument();
  });

  it('hides Team selection and lets the Manager route the local task', async () => {
    const fetchMock = mockPageApi({
      'GET /api/v1/agentteams-collaboration/health': localReadyHealth,
      'GET /api/v1/agentteams-collaboration/tasks': [],
      'POST /api/v1/agentteams-collaboration/tasks': completedTask,
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT': completedTask,
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/graph': completedGraph,
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/events': completedEvents,
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages': completedMessages,
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/result': completedResult,
    });
    renderPage();

    expect(await screen.findByText('Manager 自动路由')).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '协作团队' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('任务标题')).not.toBeDisabled();
    expect(screen.getByLabelText('任务标题')).toHaveValue('岗位调整评估');
    expect(screen.getByLabelText('任务说明')).not.toBeDisabled();
    expect(screen.getByLabelText('任务说明')).toHaveValue('评估张明的岗位调整是否可以按期完成。');
    expect(screen.getByLabelText('补充材料（可选 JSON）')).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '提交协作任务' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) => String(input).split('?')[0].endsWith('/api/v1/agentteams-collaboration/tasks') && init?.method === 'POST',
      );
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
        title: '岗位调整评估',
        instruction: '评估张明的岗位调整是否可以按期完成。',
        material: {},
      });
      expect(JSON.parse(String(call?.[1]?.body))).not.toHaveProperty('team_id');
      expect(JSON.parse(String(call?.[1]?.body))).not.toHaveProperty('leader_agent_id');
    });
  });

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
        ([input, init]) => String(input).split('?')[0].endsWith('/api/v1/agentteams-collaboration/tasks') && init?.method === 'POST',
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        title: '入职协作分析',
        instruction: '完成制度和账号核查',
        material: {},
        thinking_enabled: true,
      });
    });
  });

  it('lets the user disable thinking mode for an ordinary collaboration task', async () => {
    const submittedTask: AgentCollaborationTask = {
      ...completedTask,
      id: 'AT-NON-THINKING',
      trace_id: 'AT-NON-THINKING',
      status: 'planning',
      latest_event_id: 0,
      nodes: [{ ...rootNode, id: 'AT-NON-THINKING', root_task_id: 'AT-NON-THINKING', status: 'planning', progress: 0 }],
      summary: '',
    };
    const fetchMock = mockPageApi({
      'GET /api/v1/agentteams-collaboration/health': readyHealth,
      'GET /api/v1/teams': teams,
      'GET /api/v1/agentteams-collaboration/tasks': [],
      'POST /api/v1/agentteams-collaboration/tasks': submittedTask,
      'GET /api/v1/agentteams-collaboration/tasks/AT-NON-THINKING': submittedTask,
      'GET /api/v1/agentteams-collaboration/tasks/AT-NON-THINKING/graph': {
        nodes: submittedTask.nodes,
        parent_edges: [],
        dependency_edges: [],
        communication_edges: [],
      },
      'GET /api/v1/agentteams-collaboration/tasks/AT-NON-THINKING/events': [],
      'GET /api/v1/agentteams-collaboration/tasks/AT-NON-THINKING/messages': [],
      'GET /api/v1/agentteams-collaboration/tasks/AT-NON-THINKING/result': {
        task_id: 'AT-NON-THINKING', status: 'planning', summary: '', node_results: submittedTask.nodes,
      },
    });
    renderPage();

    const thinkingSwitch = await screen.findByRole('switch', { name: '使用大模型思考模式' });
    expect(thinkingSwitch).toBeChecked();
    fireEvent.click(thinkingSwitch);
    expect(thinkingSwitch).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: '提交协作任务' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) => String(input).split('?')[0].endsWith('/api/v1/agentteams-collaboration/tasks') && init?.method === 'POST',
      );
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
        thinking_enabled: false,
      });
      expect(JSON.parse(String(call?.[1]?.body))).not.toHaveProperty('team_id');
    });
    expect(screen.getByText('响应更快，但不会展示模型返回的决策依据。')).toBeInTheDocument();
  });

  it('shows a configured Manager as configured rather than claiming it is running', async () => {
    mockPageApi({
      'GET /api/v1/agentteams-collaboration/health': readyHealth,
      'GET /api/v1/teams': teams,
      'GET /api/v1/agentteams-collaboration/tasks': [],
    });
    renderPage();

    expect(await screen.findByText('Manager已配置')).toHaveClass('ant-tag-processing');
    expect(screen.queryByText('Manager已就绪')).not.toBeInTheDocument();
    expect(screen.getByText('Worker 已配置/待实测：2/2')).toBeInTheDocument();
    expect(screen.queryByText(/Manager未就绪/)).not.toBeInTheDocument();
  });

  it('shows both ready and configured Worker counts when the server reports a mixed state', async () => {
    mockPageApi({
      'GET /api/v1/agentteams-collaboration/health': {
        ...readyHealth,
        workers: [
          { ...readyHealth.workers[0], status: 'ready' },
          { ...readyHealth.workers[1], status: 'ready' },
          { agent_id: 'VE-0003', matrix_user_id: '@ve-0003:matrix.test', status: 'configured' },
        ],
      },
      'GET /api/v1/teams': teams,
      'GET /api/v1/agentteams-collaboration/tasks': [],
    });
    renderPage();

    expect(await screen.findByText('Worker已就绪：2/3；已配置/待实测：1/3')).toBeInTheDocument();
  });

  it('clears prior actor task data before loading the newly selected actor', async () => {
    const fetchMock = mockPageApi({
      'GET /api/v1/agentteams-collaboration/health': readyHealth,
      'GET /api/v1/teams': teams,
      'GET /api/v1/agentteams-collaboration/tasks?actor_no=E10281': [completedTask],
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT?actor_no=E10281': completedTask,
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/graph?actor_no=E10281': completedGraph,
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/events?actor_no=E10281': completedEvents,
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages?actor_no=E10281': completedMessages,
      'GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/result?actor_no=E10281': completedResult,
      'GET /api/v1/agentteams-collaboration/tasks?actor_no=E20999': [],
    });
    renderPageWithActorSwitcher();

    expect(await screen.findByText('制度材料和账号准备均已确认。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /选择任务 制度核查/ }));
    expect(await screen.findByText('制度核查完成，请Manager汇总。')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '切换为 E20999' }));

    await waitFor(() => {
      expect(screen.queryByText('制度材料和账号准备均已确认。')).not.toBeInTheDocument();
      expect(screen.queryByText('制度核查完成，请Manager汇总。')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '查看协作任务 入职协作分析' })).not.toBeInTheDocument();
      expect(screen.queryByText('最终汇总')).not.toBeInTheDocument();
    });
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes('AT-ROOT?actor_no=E20999')),
    ).toBe(false);
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
      expect(fetchMock.mock.calls.some(([input]) => String(input).split('?')[0].endsWith('/agentteams-collaboration/tasks/AT-A/result'))).toBe(true);
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
      expect(screen.getAllByText('B 节点')).toHaveLength(1);
      expect(screen.getByText('B 的轨迹事件')).toBeInTheDocument();
      expect(screen.getByText('B 的 Matrix 消息')).toBeInTheDocument();
      expect(screen.getByText('B 的最终汇总')).toBeInTheDocument();
      expect(screen.queryAllByText('A 节点')).toHaveLength(0);
      expect(screen.queryByText('A 的轨迹事件')).not.toBeInTheDocument();
      expect(screen.queryByText('A 的 Matrix 消息')).not.toBeInTheDocument();
      expect(screen.queryByText('A 的最终汇总')).not.toBeInTheDocument();
    });
  });

  it('does not let a deferred cancel for A overwrite the selected B task', async () => {
    const taskA = actionRaceTask('AT-A-CANCEL', 'A 运行任务', 'running', '');
    const taskB = actionRaceTask('AT-B-CANCEL', 'B 失败任务', 'failed', 'B 的最终汇总');
    const cancelledA: AgentCollaborationTask = {
      ...taskA,
      status: 'cancelled',
      nodes: taskA.nodes.map((node) => ({ ...node, status: 'cancelled' })),
    };
    const cancelA = deferred<AgentCollaborationTask>();
    mockPageApi({
      'GET /api/v1/agentteams-collaboration/health': readyHealth,
      'GET /api/v1/teams': teams,
      'GET /api/v1/agentteams-collaboration/tasks': [taskA, taskB],
      'GET /api/v1/agentteams-collaboration/tasks/AT-A-CANCEL': taskA,
      'GET /api/v1/agentteams-collaboration/tasks/AT-A-CANCEL/graph': { nodes: taskA.nodes, parent_edges: [], dependency_edges: [], communication_edges: [] },
      'GET /api/v1/agentteams-collaboration/tasks/AT-A-CANCEL/events': [{ ...completedEvents[0], root_task_id: taskA.id, task_id: taskA.id, summary: 'A 的轨迹事件' }],
      'GET /api/v1/agentteams-collaboration/tasks/AT-A-CANCEL/messages': [{ ...completedMessages[0], root_task_id: taskA.id, task_id: taskA.id, body: 'A 的 Matrix 消息' }],
      'GET /api/v1/agentteams-collaboration/tasks/AT-A-CANCEL/result': { task_id: taskA.id, status: taskA.status, summary: taskA.summary, node_results: taskA.nodes },
      'GET /api/v1/agentteams-collaboration/tasks/AT-B-CANCEL': taskB,
      'GET /api/v1/agentteams-collaboration/tasks/AT-B-CANCEL/graph': { nodes: taskB.nodes, parent_edges: [], dependency_edges: [], communication_edges: [] },
      'GET /api/v1/agentteams-collaboration/tasks/AT-B-CANCEL/events': [{ ...completedEvents[0], root_task_id: taskB.id, task_id: taskB.id, summary: 'B 的轨迹事件' }],
      'GET /api/v1/agentteams-collaboration/tasks/AT-B-CANCEL/messages': [{ ...completedMessages[0], root_task_id: taskB.id, task_id: taskB.id, body: 'B 的 Matrix 消息' }],
      'GET /api/v1/agentteams-collaboration/tasks/AT-B-CANCEL/result': { task_id: taskB.id, status: taskB.status, summary: taskB.summary, node_results: taskB.nodes },
      'POST /api/v1/agentteams-collaboration/tasks/AT-A-CANCEL/cancel': () => cancelA.promise,
    });
    renderPage();

    expect(await screen.findByText('A 运行任务 说明')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /取消任务/ }));
    fireEvent.click(screen.getByRole('button', { name: '查看协作任务 B 失败任务' }));

    const retryB = await screen.findByRole('button', { name: /重试/ });
    expect(retryB).not.toBeDisabled();
    expect(screen.getByText('B 的最终汇总')).toBeInTheDocument();
    expect(screen.getAllByText('B 失败任务 节点')).toHaveLength(1);
    expect(screen.queryByText(/取消任务失败/)).not.toBeInTheDocument();

    await act(async () => {
      cancelA.resolve(cancelledA);
    });

    await waitFor(() => {
      expect(screen.getByText('B 的最终汇总')).toBeInTheDocument();
      expect(screen.getAllByText('B 失败任务 节点')).toHaveLength(1);
      expect(screen.getByRole('button', { name: /重试/ })).not.toBeDisabled();
      expect(screen.queryByText('A 运行任务 节点')).not.toBeInTheDocument();
      expect(screen.queryByText(/取消任务失败/)).not.toBeInTheDocument();
      expect(screen.queryByText('正在刷新服务端协作轨迹…')).not.toBeInTheDocument();
    });
  });

  it('does not let a deferred retry for A overwrite the selected B task', async () => {
    const taskA = actionRaceTask('AT-A-RETRY', 'A 失败任务', 'failed', 'A 的最终汇总');
    const taskB = actionRaceTask('AT-B-RETRY', 'B 失败任务', 'failed', 'B 的最终汇总');
    const retriedA: AgentCollaborationTask = {
      ...taskA,
      id: 'AT-A-RETRY-NEW',
      trace_id: 'AT-A-RETRY-NEW',
      title: 'A 重试任务',
      status: 'running',
      summary: '',
      nodes: taskA.nodes.map((node) => ({ ...node, id: 'AT-A-RETRY-NEW', root_task_id: 'AT-A-RETRY-NEW', title: 'A 重试任务 节点', status: 'running' })),
    };
    const retryA = deferred<AgentCollaborationTask>();
    mockPageApi({
      'GET /api/v1/agentteams-collaboration/health': readyHealth,
      'GET /api/v1/teams': teams,
      'GET /api/v1/agentteams-collaboration/tasks': [taskA, taskB],
      'GET /api/v1/agentteams-collaboration/tasks/AT-A-RETRY': taskA,
      'GET /api/v1/agentteams-collaboration/tasks/AT-A-RETRY/graph': { nodes: taskA.nodes, parent_edges: [], dependency_edges: [], communication_edges: [] },
      'GET /api/v1/agentteams-collaboration/tasks/AT-A-RETRY/events': [{ ...completedEvents[0], root_task_id: taskA.id, task_id: taskA.id, summary: 'A 的轨迹事件' }],
      'GET /api/v1/agentteams-collaboration/tasks/AT-A-RETRY/messages': [{ ...completedMessages[0], root_task_id: taskA.id, task_id: taskA.id, body: 'A 的 Matrix 消息' }],
      'GET /api/v1/agentteams-collaboration/tasks/AT-A-RETRY/result': { task_id: taskA.id, status: taskA.status, summary: taskA.summary, node_results: taskA.nodes },
      'GET /api/v1/agentteams-collaboration/tasks/AT-B-RETRY': taskB,
      'GET /api/v1/agentteams-collaboration/tasks/AT-B-RETRY/graph': { nodes: taskB.nodes, parent_edges: [], dependency_edges: [], communication_edges: [] },
      'GET /api/v1/agentteams-collaboration/tasks/AT-B-RETRY/events': [{ ...completedEvents[0], root_task_id: taskB.id, task_id: taskB.id, summary: 'B 的轨迹事件' }],
      'GET /api/v1/agentteams-collaboration/tasks/AT-B-RETRY/messages': [{ ...completedMessages[0], root_task_id: taskB.id, task_id: taskB.id, body: 'B 的 Matrix 消息' }],
      'GET /api/v1/agentteams-collaboration/tasks/AT-B-RETRY/result': { task_id: taskB.id, status: taskB.status, summary: taskB.summary, node_results: taskB.nodes },
      'POST /api/v1/agentteams-collaboration/tasks/AT-A-RETRY/retry': () => retryA.promise,
    });
    renderPage();

    expect(await screen.findByText('A 失败任务 说明')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /重试/ }));
    fireEvent.click(screen.getByRole('button', { name: '查看协作任务 B 失败任务' }));

    const retryB = await screen.findByRole('button', { name: /重试/ });
    expect(retryB).not.toBeDisabled();
    expect(screen.getByText('B 的最终汇总')).toBeInTheDocument();
    expect(screen.getAllByText('B 失败任务 节点')).toHaveLength(1);
    expect(screen.queryByText(/重试任务失败/)).not.toBeInTheDocument();

    await act(async () => {
      retryA.resolve(retriedA);
    });

    await waitFor(() => {
      expect(screen.getByText('B 的最终汇总')).toBeInTheDocument();
      expect(screen.getAllByText('B 失败任务 节点')).toHaveLength(1);
      expect(screen.getByRole('button', { name: /重试/ })).not.toBeDisabled();
      expect(screen.queryByText('A 重试任务 节点')).not.toBeInTheDocument();
      expect(screen.queryByText(/重试任务失败/)).not.toBeInTheDocument();
      expect(screen.queryByText('正在刷新服务端协作轨迹…')).not.toBeInTheDocument();
    });
  });

  it('renders persisted delegation as a collaboration hierarchy and keeps messages in node detail', async () => {
    mockPageApi(completedTaskRoutes());
    renderPage();

    expect(await screen.findByText('入职协作分析')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /选择任务 制度核查/ })).toBeInTheDocument();
    expect(screen.getByText('协作调用树')).toBeInTheDocument();
    expect(screen.getByTestId('agentteams-task-chart')).toBeInTheDocument();
    expect(screen.queryByText('VE-0001 → DT-E10281：发送协作消息')).not.toBeInTheDocument();
    expect(screen.queryByText('父子委派')).not.toBeInTheDocument();
    expect(screen.queryByText('任务依赖')).not.toBeInTheDocument();
    expect(screen.queryByText('消息关系')).not.toBeInTheDocument();
    expect(await screen.findByText('制度材料和账号准备均已确认。')).toBeInTheDocument();
  });

  it('shows the selected worker process from persisted instructions, skills, tools, and results', async () => {
    const routes = completedTaskRoutes();
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages'] = processMessages;
    mockPageApi(routes);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /选择任务 制度核查/ }));

    const detail = screen.getByTestId('agentteams-node-detail');
    expect(detail).toHaveTextContent('制度材料已核对，等待Manager汇总。');
    expect(detail).toHaveTextContent('Worker处理过程');
    expect(detail).toHaveTextContent('核对员工制度材料并确认是否存在缺口。');
    expect(detail).toHaveTextContent('hr-onboarding-assistant');
    expect(detail).toHaveTextContent('hr.onboarding.read');
    expect(detail).toHaveTextContent('接收 Manager 派发的任务');
    expect(detail).toHaveTextContent('第1轮：选择 Skill');
    expect(detail).toHaveTextContent('第1轮：决定调用 Tool');
    expect(detail).toHaveTextContent('Gateway 返回：查询员工入职信息');
    expect(detail).toHaveTextContent('第2轮：决定形成节点结果');
    expect(detail).toHaveTextContent('向 Manager 返回节点结果');
    expect(detail).not.toHaveTextContent('决策依据');
    expect(detail).not.toHaveTextContent('需要先查询制度材料，才能给出有证据的结论。');
    expect(detail).not.toHaveTextContent('Gateway已返回材料齐全，可以结束本节点并提交结果。');
    expect(within(detail).queryByRole('button', { name: /Gateway 返回：查询员工入职信息/ })).not.toBeInTheDocument();

    fireEvent.click(within(detail).getByRole('button', { name: '展开决策依据：第1轮：选择 Skill' }));
    expect(detail).toHaveTextContent('任务要求核查制度材料，因此选择HR入职Skill。');
    expect(detail).not.toHaveTextContent('需要先查询制度材料，才能给出有证据的结论。');
    fireEvent.click(within(detail).getByRole('button', { name: '收起决策依据：第1轮：选择 Skill' }));

    fireEvent.click(within(detail).getByRole('button', { name: '展开决策依据：第1轮：决定调用 Tool' }));
    expect(detail).toHaveTextContent('需要先查询制度材料，才能给出有证据的结论。');
    expect(detail).not.toHaveTextContent('Gateway已返回材料齐全，可以结束本节点并提交结果。');
    fireEvent.click(within(detail).getByRole('button', { name: '收起决策依据：第1轮：决定调用 Tool' }));
    expect(detail).not.toHaveTextContent('需要先查询制度材料，才能给出有证据的结论。');

    fireEvent.click(within(detail).getByRole('button', { name: /展开决策依据，用时/ }));

    expect(detail).toHaveTextContent('Worker处理过程');
    expect(detail).toHaveTextContent('决策依据');
    expect(detail).toHaveTextContent('需要先查询制度材料，才能给出有证据的结论。');
    expect(detail).toHaveTextContent('第1轮：决定调用 Tool');
    expect(detail).toHaveTextContent('Gateway通过');
    expect(detail).toHaveTextContent('审计#101');
    expect(detail).toHaveTextContent('Gateway已返回材料齐全，可以结束本节点并提交结果。');
    expect(detail).toHaveTextContent('制度材料已核对，等待Manager汇总。');
    expect(detail).not.toHaveTextContent('DeepSeek');
    expect(detail).not.toHaveTextContent('DWP_EVENT');

    fireEvent.click(within(detail).getByRole('button', { name: /收起决策依据，用时/ }));
    expect(detail).toHaveTextContent('第1轮：决定调用 Tool');
    expect(detail).toHaveTextContent('向 Manager 返回节点结果');
    expect(detail).not.toHaveTextContent('需要先查询制度材料，才能给出有证据的结论。');
  });

  it('shows a worker-created subtask as nested delegation instead of a Manager round', async () => {
    const routes = completedTaskRoutes();
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages'] = [
      ...processMessages,
      {
        ...completedMessages[0],
        id: 'AM-WORKER-DELEGATE',
        message_type: 'task.delegate',
        sender_agent_id: 'VE-0001',
        receiver_agent_id: 'platform',
        payload: {
          payload: {
            round: 2,
            subtasks: [
              {
                client_task_key: 'worker-follow-up',
                title: '补充材料复核',
                instruction: '请协助确认岗位材料的生效时间。',
                assignee_agent_id: 'VE-0002',
              },
            ],
          },
        },
      },
    ];
    mockPageApi(routes);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /选择任务 制度核查/ }));

    const detail = screen.getByTestId('agentteams-node-detail');
    expect(detail).toHaveTextContent('嵌套委派：VE-0001 → VE-0002');
    expect(detail).toHaveTextContent('补充材料复核：请协助确认岗位材料的生效时间。');
    expect(detail).not.toHaveTextContent('第2轮：派发子任务');
  });

  it('shows the Manager review and supplementary delegation in decision order', async () => {
    const routes = completedTaskRoutes();
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages'] = processMessages;
    mockPageApi(routes);
    renderPage();

    const detail = await screen.findByTestId('agentteams-node-detail');
    expect(detail).toHaveTextContent('已形成最终汇总，可在上方“最终汇总”区域核对。');
    expect(detail).toHaveTextContent('Manager决策过程');
    expect(detail).toHaveTextContent('第1轮：选择场景 Skill');
    expect(detail).toHaveTextContent('第1轮：派发子任务');
    expect(detail).toHaveTextContent('接收第1轮子任务结果');
    expect(detail).toHaveTextContent('第2轮：派发子任务');
    expect(detail).toHaveTextContent('接收第2轮子任务结果');
    expect(detail).toHaveTextContent('第3轮：形成最终汇总');
    expect(detail).not.toHaveTextContent('制度核查是当前必需证据，先派发给HR节点。');
    expect(detail).not.toHaveTextContent('已有制度结果，但还缺审计证据，需要补充派发。');

    fireEvent.click(within(detail).getByRole('button', { name: /展开决策依据，用时/ }));

    expect(detail).toHaveTextContent('Manager决策过程');
    expect(detail).toHaveTextContent('agentteams-onboarding-readiness');
    expect(detail).toHaveTextContent('第1轮：选择场景 Skill');
    expect(detail).toHaveTextContent('第1轮：派发子任务');
    expect(detail).toHaveTextContent('接收第1轮子任务结果');
    expect(detail).toHaveTextContent('第2轮：派发子任务');
    expect(detail).toHaveTextContent('接收第2轮子任务结果');
    expect(detail).toHaveTextContent('第3轮：形成最终汇总');
    expect(detail).toHaveTextContent('已有制度结果，但还缺审计证据，需要补充派发。');
    expect(detail).toHaveTextContent('制度与审计证据都已返回，满足最终汇总条件。');
    const dispatches = [...detail.querySelectorAll<HTMLElement>('.at-process-step-delegate')];
    const reviews = [...detail.querySelectorAll<HTMLElement>('.at-process-step-review')];
    expect(dispatches).toHaveLength(2);
    expect(reviews).toHaveLength(2);
    expect(dispatches.every((step) => step.getAttribute('style') === null)).toBe(true);
    expect(reviews.every((step) => step.getAttribute('style') === null)).toBe(true);
    const content = detail.textContent ?? '';
    expect(content.indexOf('第1轮：选择场景 Skill')).toBeLessThan(content.indexOf('第1轮：派发子任务'));
    expect(content.indexOf('第1轮：派发子任务')).toBeLessThan(content.indexOf('接收第1轮子任务结果'));
    expect(content.indexOf('接收第1轮子任务结果')).toBeLessThan(content.indexOf('第2轮：派发子任务'));
    expect(content.indexOf('第2轮：派发子任务')).toBeLessThan(content.indexOf('接收第2轮子任务结果'));
    expect(content.indexOf('接收第2轮子任务结果')).toBeLessThan(content.indexOf('第3轮：形成最终汇总'));

    fireEvent.click(within(detail).getByRole('button', { name: /收起决策依据，用时/ }));
    expect(detail).toHaveTextContent('第1轮：派发子任务');
    expect(detail).toHaveTextContent('第3轮：形成最终汇总');
    expect(detail).not.toHaveTextContent('制度与审计证据都已返回，满足最终汇总条件。');
  });

  it('places the final result full-width below the submission and task-record cards', async () => {
    mockPageApi(completedTaskRoutes());
    renderPage();

    const overviewRow = await screen.findByTestId('agentteams-overview-row');
    const resultPanel = screen.getByTestId('agentteams-result-panel');
    const workspace = await screen.findByTestId('agentteams-workspace');
    const sideStack = within(workspace).getByTestId('agentteams-side-stack');
    expect(overviewRow).toHaveTextContent('提交协作任务');
    expect(overviewRow).toHaveTextContent('协作任务记录');
    expect(resultPanel).toHaveAttribute('data-layout', 'full-width');
    expect(overviewRow.compareDocumentPosition(resultPanel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(resultPanel.compareDocumentPosition(workspace) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(workspace).queryByTestId('agentteams-result-panel')).not.toBeInTheDocument();
    expect(within(sideStack).queryByTestId('agentteams-result-panel')).not.toBeInTheDocument();
    expect(sideStack).toHaveTextContent('当前节点状态');
  });

  it('keeps evaluation panels and evaluation requests out of the collaboration page', async () => {
    const fetchMock = mockPageApi(completedTaskRoutes());
    renderPage();

    expect(await screen.findByText('协作调用树')).toBeInTheDocument();
    expect(screen.queryByTestId('agentteams-evaluation-panel')).not.toBeInTheDocument();
    expect(screen.queryByTestId('database-benchmark-panel')).not.toBeInTheDocument();
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => (
      String(url).includes('/evaluation') || String(url).includes('/agentteams-benchmarks/database')
    ))).toBe(false));
  });

  it('shows a compact node execution chain and renders the final result above the workspace', async () => {
    const routes = completedTaskRoutes();
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/messages'] = [
      completedMessages[0],
      {
        ...completedMessages[0],
        id: 'AM-TOOL-REQUEST',
        message_type: 'tool.request',
        sender_agent_id: 'VE-0001',
        receiver_agent_id: 'platform',
        payload: {
          payload: {
            tool_name: 'query_onboarding_employee',
            plugin_id: 'agentteams-onboarding-employee',
          },
        },
      },
      {
        ...completedMessages[0],
        id: 'AM-TOOL-RESULT',
        message_type: 'tool.result',
        sender_agent_id: 'platform',
        receiver_agent_id: 'VE-0001',
        payload: {
          tool_name: 'query_onboarding_employee',
          decision: 'allow',
          audit_ids: [101],
          data: '身份核验与合同签署已完成',
        },
      },
    ];
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT/result'] = {
      ...completedResult,
      node_results: [
        {
          ...rootNode,
          output_payload: {
            readiness_decision: 'incomplete',
            completed_items: ['身份核验和合同签署已完成'],
            risks: ['MFA注册确认材料缺失'],
            unknowns: [],
            next_actions: ['补齐IT-A-003后重新核查'],
          },
        },
        workerNode,
      ],
    };
    mockPageApi(routes);
    renderPage();

    expect(await screen.findByText('查询员工入职信息 · Gateway通过 · 审计#101')).toBeInTheDocument();
    expect(screen.queryByText('本节点执行链')).not.toBeInTheDocument();
    expect(screen.getByText('结论：信息不足，暂无法判断')).toBeInTheDocument();
    expect(screen.getByText('身份核验和合同签署已完成')).toBeInTheDocument();
    expect(screen.getByText('MFA注册确认材料缺失')).toBeInTheDocument();
    expect(screen.getByText('补齐IT-A-003后重新核查')).toBeInTheDocument();

    expect(screen.getByTestId('agentteams-result-panel')).toHaveTextContent('最终汇总');
    expect(screen.getByTestId('agentteams-result-panel')).toHaveTextContent('MFA注册确认材料缺失');
    expect(screen.getByTestId('agentteams-result-panel')).toHaveTextContent('制度材料和账号准备均已确认。');

    fireEvent.click(screen.getByRole('button', { name: /选择任务 制度核查/ }));
    const auditPanel = screen.getByText('工具审计').closest('.ant-card');
    expect(auditPanel).not.toBeNull();
    expect(auditPanel).toHaveTextContent('工具调用：query_onboarding_employee');
    expect(auditPanel).toHaveTextContent('调用者：VE-0001');
    expect(auditPanel?.querySelectorAll('.at-tool-row')).toHaveLength(1);
    expect(auditPanel).not.toHaveTextContent('工具调用：未提供');
    expect(auditPanel).not.toHaveTextContent('权限检查：未产生');
  });

  it('keeps protocol message details out of the compact call tree', () => {
    render(
      <TaskTree
        nodes={[rootNode]}
        graph={{ nodes: [rootNode], parent_edges: [], dependency_edges: [], communication_edges: [] }}
        events={[]}
        messages={[{ ...completedMessages[0], message_type: 'task.submit', task_id: rootNode.id }]}
        selectedId={rootNode.id}
        onSelect={() => undefined}
      />,
    );

    expect(screen.getByTestId('agentteams-task-chart')).toBeInTheDocument();
    expect(screen.queryByText(/提交根任务/)).not.toBeInTheDocument();
    expect(screen.queryByText(/task\.submit/)).not.toBeInTheDocument();
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
    const healthAlert = screen.getByText('协作环境未就绪').closest('.ant-alert');
    expect(healthAlert).not.toBeNull();
    expect(healthAlert).toHaveTextContent('TEAM-DB-BENCH 数据库诊断场景独立运行，不受此状态影响');
    expect(screen.queryByTestId('database-benchmark-panel')).not.toBeInTheDocument();
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
    const before = fetchMock.mock.calls.filter(([input]) => String(input).split('?')[0].endsWith('/api/v1/agentteams-collaboration/tasks/AT-ROOT')).length;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1100));
    });
    const after = fetchMock.mock.calls.filter(([input]) => String(input).split('?')[0].endsWith('/api/v1/agentteams-collaboration/tasks/AT-ROOT')).length;
    expect(after).toBe(before);
  }, 3000);

  it('polls a running root task every three seconds without changing server state locally', async () => {
    const runningNode = { ...rootNode, status: 'running' as const, progress: 30 };
    const runningTask: AgentCollaborationTask = {
      ...completedTask,
      status: 'running',
      nodes: [runningNode],
      latest_event_id: 0,
      summary: '',
    };
    const pendingRefresh = deferred<AgentCollaborationTask>();
    let taskReads = 0;
    const routes = completedTaskRoutes(runningTask);
    routes['GET /api/v1/agentteams-collaboration/tasks/AT-ROOT'] = () => {
      taskReads += 1;
      return taskReads === 1 ? runningTask : pendingRefresh.promise;
    };
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
    const before = fetchMock.mock.calls.filter(([input]) => String(input).split('?')[0].endsWith('/api/v1/agentteams-collaboration/tasks/AT-ROOT')).length;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1100));
    });
    const beforeThreeSeconds = fetchMock.mock.calls.filter(([input]) => String(input).split('?')[0].endsWith('/api/v1/agentteams-collaboration/tasks/AT-ROOT')).length;
    expect(beforeThreeSeconds).toBe(before);
    expect(screen.getAllByText('执行中').length).toBeGreaterThan(0);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 2100));
    });
    await waitFor(() => {
      const after = fetchMock.mock.calls.filter(([input]) => String(input).split('?')[0].endsWith('/api/v1/agentteams-collaboration/tasks/AT-ROOT')).length;
      expect(after).toBeGreaterThan(before);
    });
    expect(screen.queryByText('正在刷新服务端协作轨迹…')).not.toBeInTheDocument();
    await act(async () => {
      pendingRefresh.resolve(runningTask);
    });
  }, 6000);

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
      fetchMock.mock.calls.filter(([input]) => String(input).split('?')[0].endsWith('/api/v1/agentteams-collaboration/tasks')).length,
    ).toBeGreaterThanOrEqual(2);
  });
});
