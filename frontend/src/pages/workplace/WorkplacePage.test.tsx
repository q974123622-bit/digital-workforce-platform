import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import type {
  Conversation,
  ConversationSearchHit,
  ConversationSummary,
  Employee,
  Skill,
  TaskRun,
  WorkplaceHome,
} from '@dwp/shared-schema';
import { CurrentUserProvider } from '../../context/CurrentUserContext';
import WorkplacePage from './WorkplacePage';

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

function stubFetch(routes: Record<string, unknown | ((init?: RequestInit) => unknown)>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const route = Object.entries(routes).find(([key]) => url.endsWith(key));
    if (!route) {
      return new Response(JSON.stringify({ error: { message: `未 mock：${url}` } }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    const body = typeof route[1] === 'function' ? (route[1] as (init?: RequestInit) => unknown)(init) : route[1];
    return json(body);
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

const twin: Employee = {
  id: 'DT-E10281',
  employee_no: 'DT-E10281',
  name: '张三的数字分身',
  type: 'twin',
  employment_type: 'formal',
  source_human_no: 'E10281',
  owner_human_no: 'E10281',
  department: '架构部',
  role_prompt: '我是张三的数字分身，熟悉部门制度与流程。',
  status: 'active',
  runtime_type: 'demo',
  runtime_ref: null,
  location: 'remote',
  internet: 'deny',
  max_data_level: 'L2',
  allowed_domains: [],
  grants: [
    {
      plugin_id: 'knowledge-l1',
      name: '公开制度知识库',
      type: 'knowledge',
      action: 'read',
      decision_mode: 'allow',
      data_level: 'L1',
    },
    {
      plugin_id: 'knowledge-l2',
      name: '内部流程知识库',
      type: 'knowledge',
      action: 'read',
      decision_mode: 'allow',
      data_level: 'L2',
    },
  ],
};

const ve1: Employee = {
  id: 'VE-0001',
  employee_no: 'VE-0001',
  name: '新员工入职助手',
  type: 'virtual',
  employment_type: 'formal',
  source_human_no: null,
  owner_human_no: 'E10021',
  department: '人力资源部',
  role_prompt: '负责新员工入职准备与制度咨询。',
  status: 'active',
  runtime_type: 'harness',
  runtime_ref: 'headless',
  location: 'remote',
  internet: 'deny',
  max_data_level: 'L2',
  allowed_domains: [],
  grants: [],
};

const rpa: Employee = {
  id: 'RPA-0001',
  employee_no: 'RPA-0001',
  name: '报表机器人',
  type: 'rpa',
  employment_type: 'formal',
  source_human_no: null,
  owner_human_no: 'E10021',
  department: '财务部',
  role_prompt: '自动生成日报与周报。',
  status: 'active',
  runtime_type: 'demo',
  runtime_ref: null,
  location: 'remote',
  internet: 'allow',
  max_data_level: 'L3',
  allowed_domains: [],
  grants: [],
};

const skill: Skill = {
  id: 'SK-0001',
  owner_human_no: 'E10281',
  name: '报销制度速答',
  description: '掌握差旅报销标准与流程。',
  content: '住宿标准：一线城市每晚不超过 600 元。',
  status: 'active',
  created_at: '2026-08-19T08:00:00',
};

const home: WorkplaceHome = {
  actor: { employee_no: 'E10281', name: '张三', department: '架构部', employment_type: 'formal' },
  twin,
  available_employees: [ve1, rpa],
  skills: [skill],
  recent_conversations: [],
};

const conv1Summary: ConversationSummary = {
  id: 'CONV-1',
  kind: 'direct',
  title: '',
  owner_human_no: 'E10281',
  participants: [{ employee_no: 'VE-0001', name: '新员工入职助手', role: 'member', employee_type: 'virtual' }],
  last_message: '你好呀，需要我帮忙吗？',
  updated_at: '2026-08-19T09:05:00',
};

const conv1: Conversation = {
  id: 'CONV-1',
  kind: 'direct',
  title: '',
  owner_human_no: 'E10281',
  participants: [{ employee_no: 'VE-0001', name: '新员工入职助手', role: 'member', employee_type: 'virtual' }],
  messages: [
    {
      id: 1,
      conversation_id: 'CONV-1',
      participant_no: 'E10281',
      participant_name: '张三',
      role: 'user',
      content: '你好',
      tool_cards: [],
      seq: 1,
    },
    {
      id: 2,
      conversation_id: 'CONV-1',
      participant_no: 'VE-0001',
      participant_name: '新员工入职助手',
      role: 'assistant',
      content: '你好呀，需要我帮忙吗？',
      tool_cards: [],
      seq: 2,
    },
  ],
  tasks: [],
  updated_at: '2026-08-19T09:05:00',
};

const conv1WithReply: Conversation = {
  ...conv1,
  messages: [
    ...conv1.messages,
    {
      id: 3,
      conversation_id: 'CONV-1',
      participant_no: 'VE-0001',
      participant_name: '新员工入职助手',
      role: 'assistant',
      content: '收到，**马上**帮你处理。',
      tool_cards: [],
      seq: 3,
    },
  ],
  updated_at: '2026-08-19T09:06:00',
};

const groupTask: TaskRun = {
  id: 'T-DEMO-1',
  team_id: 'CONV-G',
  conversation_id: 'CONV-G',
  source: 'builtin',
  trigger_message_seq: 1,
  trace_id: 'T-DEMO-1',
  request: '整理新员工入职准备清单',
  status: 'approval',
  subtasks: [
    {
      worker_id: 'VE-0001',
      worker_no: 'VE-0001',
      summary: '确认入职制度与材料清单',
      plugin_ids: ['adp-onboarding'],
      status: 'completed',
      result: '材料清单已确认：身份证、学历证书复印件、银行卡复印件。',
      execution_mode: 'harness',
      runtime_mode: 'harness',
      runtime_context_id: 'VE-0001:T-DEMO-1',
      runtime_summary: `计划开始：${'完整执行细节。'.repeat(180)}：计划结束`,
      tool_name: '入职流程 Workflow',
      tool_type: 'workflow',
      approval: null,
    },
    {
      worker_id: 'VE-0003',
      worker_no: 'VE-0003',
      summary: '生成入职权限报表（敏感操作）',
      plugin_ids: ['rpa-report'],
      status: 'approval',
      result: null,
      approval: { policy_id: 'POLICY-005', reason: '敏感操作需人工审批' },
    },
  ],
  summary: '',
  created_at: '2026-08-19T10:00:00',
};

const groupTaskCompleted: TaskRun = {
  ...groupTask,
  status: 'completed',
  subtasks: groupTask.subtasks.map((sub, index) => ({
    ...sub,
    status: 'completed',
    result: sub.result ?? '已批准执行',
    ...(index === 1
      ? {
          execution_mode: 'demo_adapter' as const,
          runtime_mode: 'demo_adapter' as const,
          runtime_context_id: 'RPA-0001:T-DEMO-1',
          tool_name: '报表机器人',
          tool_type: 'rpa',
        }
      : {}),
  })),
  summary: '材料清单与 IT 账号已确认，权限报表已生成，入职准备就绪。',
};

const groupParticipants = [
  { employee_no: 'DT-E10281', name: '张三的数字分身', role: 'organizer' as const, employee_type: 'twin' },
  { employee_no: 'VE-0001', name: '新员工入职助手', role: 'member' as const, employee_type: 'virtual' },
  { employee_no: 'VE-0003', name: 'IT 助理', role: 'member' as const, employee_type: 'virtual' },
];

const groupConv: Conversation = {
  id: 'CONV-G',
  kind: 'group',
  title: '新员工入职协作',
  owner_human_no: 'E10281',
  participants: groupParticipants,
  messages: [
    {
      id: 1,
      conversation_id: 'CONV-G',
      participant_no: 'E10281',
      participant_name: '张三',
      role: 'user',
      content: '帮我整理入职清单',
      tool_cards: [],
      seq: 1,
    },
  ],
  tasks: [groupTask],
  updated_at: '2026-08-19T10:05:00',
};

const groupConvCompleted: Conversation = {
  ...groupConv,
  tasks: [groupTaskCompleted],
  updated_at: '2026-08-19T10:08:00',
};

const groupConvEmpty: Conversation = {
  ...groupConv,
  messages: [],
  tasks: [],
};

const groupConvWithTask: Conversation = {
  ...groupConvEmpty,
  messages: [
    {
      id: 10,
      conversation_id: 'CONV-G',
      participant_no: 'E10281',
      participant_name: '张三',
      role: 'user',
      content: '帮我写周报',
      tool_cards: [],
      seq: 10,
    },
  ],
  tasks: [groupTaskCompleted],
  updated_at: '2026-08-19T10:10:00',
};

const groupSummary: ConversationSummary = {
  id: 'CONV-G',
  kind: 'group',
  title: '新员工入职协作',
  owner_human_no: 'E10281',
  participants: groupParticipants,
  last_message: '帮我整理入职清单',
  updated_at: '2026-08-19T10:05:00',
};

function renderPage() {
  return render(
    <ConfigProvider locale={zhCN} button={{ autoInsertSpace: false }}>
      <MemoryRouter>
        <CurrentUserProvider>
          <WorkplacePage />
        </CurrentUserProvider>
      </MemoryRouter>
    </ConfigProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('WorkplacePage', () => {
  it('渲染会话列表：我的分身置顶 + 最后消息预览', async () => {
    stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [conv1Summary],
    });
    renderPage();

    expect(await screen.findByText('张三的数字分身')).toBeInTheDocument();
    expect(screen.getByText('开始和我的分身聊聊吧')).toBeInTheDocument();
    // 分身身份由段头表达（会话行内的 Tag 被 .conversation-row .ant-tag 全局隐藏，已移除）。
    expect(screen.getByRole('heading', { name: /我的分身/ })).toHaveTextContent('1');
    expect(screen.getByRole('heading', { name: /AI员工/ })).toHaveTextContent('1');
    expect(screen.getByText('新员工入职助手')).toBeInTheDocument();
    expect(screen.getByText('你好呀，需要我帮忙吗？')).toBeInTheDocument();
  });

  it('发送消息：微信式气泡渲染 + 调用消息接口', async () => {
    class MockEventSource {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSED = 2;
      readyState = 1;
      withCredentials = true;
      url: string;
      listeners = new Map<string, Array<(event: MessageEvent) => void>>();
      constructor(url: string | URL) {
        this.url = String(url);
        setTimeout(() => {
          '收到，马上帮你处理。'.split('').forEach((delta, index) => this.emit('answer_delta', {
            employee_id: 'VE-0001', delta, offset: index,
          }, `3.${index}`));
          this.emit('answer_done', { message_id: 3, trace_id: 'T-TEST', tool_cards: [] }, '4');
        }, 0);
      }
      addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
        const callback = typeof listener === 'function' ? listener : listener.handleEvent.bind(listener);
        this.listeners.set(type, [...(this.listeners.get(type) ?? []), callback as (event: MessageEvent) => void]);
      }
      removeEventListener() {}
      dispatchEvent() { return true; }
      close() { this.readyState = 2; }
      emit(type: string, data: unknown, id: string) {
        const event = new MessageEvent(type, { data: JSON.stringify(data), lastEventId: id });
        (this.listeners.get(type) ?? []).forEach((listener) => listener(event));
      }
    }
    vi.stubGlobal('EventSource', MockEventSource);
    let conversationReads = 0;
    const fetchMock = stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [conv1Summary],
      '/api/v1/conversations/CONV-1/runs/active': null,
      '/api/v1/conversations/CONV-1': () => (++conversationReads >= 2 ? conv1WithReply : conv1),
      '/api/v1/conversations/CONV-1/runs': {
        execution_id: 'EX-TEST',
        trigger_message_seq: 3,
        conversation: conv1,
      },
    });
    const view = renderPage();

    fireEvent.click(await screen.findByText('新员工入职助手'));
    expect(await screen.findByText('你好呀，需要我帮忙吗？')).toBeInTheDocument();
    expect(screen.getByText('你好')).toBeInTheDocument();

    // 标题栏：类型徽章表达身份，副标题只留状态（类型不再重复出现）。
    const headerBadge = view.container.querySelector('.type-badge--header');
    expect(headerBadge).not.toBeNull();
    expect(headerBadge).toHaveTextContent('AI员工');
    expect(screen.getByText('当前可用')).toBeInTheDocument();

    const input = screen.getByPlaceholderText('发消息…');
    fireEvent.change(input, { target: { value: '帮我准备入职' } });
    fireEvent.click(screen.getByRole('button', { name: /发送/ }));

    // 自己的消息应立即渲染，不需要等成员回复
    expect(screen.getByText('帮我准备入职')).toBeInTheDocument();

    await waitFor(() => {
      expect(view.container.textContent).toContain('收到，马上帮你处理。');
      expect(view.container.querySelector('strong')?.textContent).toBe('马上');
    });
    const call = fetchMock.mock.calls.find(([inputUrl]) =>
      String(inputUrl).endsWith('/api/v1/conversations/CONV-1/runs'),
    );
    const body = (call as [RequestInfo | URL, RequestInit | undefined] | undefined)?.[1]?.body;
    expect(JSON.parse(String(body))).toEqual({ actor_no: 'E10281', content: '帮我准备入职' });
  });

  it('重新进入会话后保留已完成的执行轨迹', async () => {
    stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [conv1Summary],
      '/api/v1/conversations/CONV-1/runs/history': [{
        execution: {
          id: 'EX-DONE', conversation_id: 'CONV-1', trigger_message_seq: 1,
          trace_id: 'T-DONE', primary_employee_id: 'VE-0001', status: 'completed',
          stage: 'completed', error_code: '', error_message: '', retryable: false,
          started_at: '2026-08-19T09:00:00', updated_at: '2026-08-19T09:00:03',
          completed_at: '2026-08-19T09:00:03',
        },
        events: [
          {
            event_seq: 1, event_type: 'queued', actor_employee_id: 'VE-0001',
            stage: 'queued', status: 'queued', title: '任务已进入执行队列', detail: '等待接收',
            knowledge_base_id: null, target_agent_id: null, hit_count: null, payload: {},
            created_at: '2026-08-19T09:00:00',
          },
          {
            event_seq: 2, event_type: 'knowledge_completed', actor_employee_id: 'VE-0001',
            stage: 'knowledge_search', status: 'running', title: '知识库检索完成', detail: '找到 2 条资料',
            knowledge_base_id: 'KB-IT-SERVICE', target_agent_id: null, hit_count: 2, payload: {},
            created_at: '2026-08-19T09:00:02',
          },
          {
            event_seq: 3, event_type: 'answer_done', actor_employee_id: 'VE-0001',
            stage: 'completed', status: 'completed', title: '执行完成', detail: '',
            knowledge_base_id: null, target_agent_id: null, hit_count: null, payload: {},
            created_at: '2026-08-19T09:00:03',
          },
        ],
      }],
      '/api/v1/conversations/CONV-1': conv1,
    });
    renderPage();
    fireEvent.click(await screen.findByText('新员工入职助手'));
    expect(await screen.findByText(/已查阅 1 个知识库/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /已查阅 1 个知识库/ }));
    expect(screen.getByText('知识库检索完成')).toBeInTheDocument();
    expect(screen.getByText('找到 2 条资料')).toBeInTheDocument();
  });

  it('能力档案：通过资料抽屉查看能力并管理个人工作方法', async () => {
    const twinSummary: ConversationSummary = {
      id: 'CONV-TWIN', kind: 'direct', title: '', owner_human_no: 'E10281',
      participants: [{ employee_no: 'DT-E10281', name: '张三的数字分身', role: 'member', employee_type: 'twin' }],
      last_message: '开始和我的分身聊聊吧', updated_at: '2026-08-19T09:05:00',
    };
    const twinConv: Conversation = {
      id: 'CONV-TWIN', kind: 'direct', title: '', owner_human_no: 'E10281',
      participants: [{ employee_no: 'DT-E10281', name: '张三的数字分身', role: 'member', employee_type: 'twin' }],
      messages: [], tasks: [], updated_at: '2026-08-19T09:05:00',
    };
    const fetchMock = stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [twinSummary],
      '/api/v1/conversations/CONV-TWIN': twinConv,
      '/api/v1/agents/DT-E10281/effective-capabilities': {
        employee_id: 'DT-E10281', display_name: '张三的数字分身', identity_kind: 'human_twin',
        runtime_engine: 'harness', runtime_state: 'ready', container_name: 'dwp-harness-dt-e10281',
        knowledge_mode: 'internal', available_count: 2, attention_count: 0,
        capabilities: [
          {
            id: 'knowledge:KB-PUBLIC', name: '公共知识', kind: 'knowledge', source_type: 'knowledge_base',
            description: '公开制度与常见问题', actions: ['read'], status: 'available', decision: 'allow',
            reason: '已就绪', authorized: true, installed: true, healthy: true, data_level: 'L1',
            knowledge_base_id: 'KB-PUBLIC', target_employee_ids: [], example_prompts: ['请查询公共制度。'],
          },
          {
            id: 'platform:delegate', name: '向AI员工求助', kind: 'delegation', source_type: 'platform_tool',
            description: '分身不会时向岗位员工委派一次', actions: ['delegate'], status: 'available', decision: 'allow',
            reason: '委派深度固定为 1', authorized: true, installed: true, healthy: true, data_level: null,
            knowledge_base_id: null, target_employee_ids: ['AI-GENERAL'], example_prompts: ['请咨询AI员工。'],
          },
        ],
      },
      '/api/v1/skills': { id: 'SK-0002', owner_human_no: 'E10281', name: '会议纪要模板', status: 'active', created_at: '2026-08-19T10:00:00', description: '', content: '结论先行' },
      '/api/v1/skills/SK-0001?actor_no=E10281': { ...skill, status: 'disabled' },
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /张三的数字分身/ }));
    fireEvent.click(await screen.findByRole('button', { name: '资料' }));
    expect(await screen.findByText('报销制度速答')).toBeInTheDocument();
    expect(await screen.findByText('公共知识')).toBeInTheDocument();
    expect(screen.getByText('向AI员工求助')).toBeInTheDocument();

    // 添加个人工作方法
    fireEvent.click(screen.getByRole('button', { name: /添加工作方法/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '会议纪要模板' } });
    fireEvent.change(screen.getByLabelText('工作方法内容'), { target: { value: '结论先行，行动项带负责人。' } });
    fireEvent.click(screen.getByRole('button', { name: '上传' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([inputUrl, init]) => String(inputUrl).endsWith('/api/v1/skills') && init?.method === 'POST');
      expect(call).toBeTruthy();
      const body = (call as [RequestInfo | URL, RequestInit | undefined] | undefined)?.[1]?.body;
      expect(JSON.parse(String(body))).toMatchObject({
        actor_no: 'E10281',
        name: '会议纪要模板',
      });
    });

    // 启停开关
    fireEvent.click(screen.getByLabelText('切换技能 报销制度速答'));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([inputUrl, init]) =>
        String(inputUrl).endsWith('/api/v1/skills/SK-0001?actor_no=E10281') && init?.method === 'PUT',
      );
      expect(call).toBeTruthy();
      const body = (call as [RequestInfo | URL, RequestInit | undefined] | undefined)?.[1]?.body;
      expect(JSON.parse(String(body))).toEqual({ status: 'disabled' });
    });
  });

  it('工具缺凭据：执行轨迹里弹出补全气泡，提交后自动续跑', async () => {
    const pendingRequest = {
      id: 'TI-1', conversation_id: 'CONV-1', execution_id: 'EX-INPUT', trace_id: 'T-INPUT',
      plugin_id: 'xyzq-ehome-mcp', tool_name: 'getEmpCardBalance',
      fields: [{
        key: 'X-MCP-JWT', label: '兴证e家用户令牌', kind: 'secret', required: true,
        hint: '从兴证e家 App 复制你的个人令牌', source: 'user_secret', masked: '', reason: 'missing',
      }],
      reason: 'missing', status: 'pending', submitted_values: {}, resolved_at: null,
      resolved_by: '', created_at: '2026-09-22T10:00:00',
    };
    // 提交后续跑会打开 SSE；jsdom 没有 EventSource，给个静默实现即可（本用例只关心提交与刷新）。
    class SilentEventSource {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSED = 2;
      readyState = 1;
      withCredentials = true;
      url = '';
      constructor(url: string | URL) {
        this.url = String(url);
      }
      addEventListener() {}
      removeEventListener() {}
      dispatchEvent() { return true; }
      close() { this.readyState = 2; }
    }
    vi.stubGlobal('EventSource', SilentEventSource);
    let submitted = false;
    const fetchMock = stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [conv1Summary],
      '/api/v1/conversations/CONV-1/runs/active': null,
      '/api/v1/conversations/CONV-1/runs/history': [{
        execution: {
          id: 'EX-INPUT', conversation_id: 'CONV-1', trigger_message_seq: 1,
          trace_id: 'T-INPUT', primary_employee_id: 'VE-0001', status: 'completed',
          stage: 'completed', error_code: '', error_message: '', retryable: false,
          started_at: '2026-09-22T10:00:00', updated_at: '2026-09-22T10:00:05',
          completed_at: '2026-09-22T10:00:05',
        },
        events: [
          {
            event_seq: 1, event_type: 'queued', actor_employee_id: 'VE-0001', stage: 'queued',
            status: 'queued', title: '任务已进入执行队列', detail: '', knowledge_base_id: null,
            target_agent_id: null, hit_count: null, payload: {}, created_at: '2026-09-22T10:00:00',
          },
          {
            event_seq: 2, event_type: 'input_required', actor_employee_id: 'VE-0001',
            stage: 'external_mcp', status: 'running', title: '需要你补充信息',
            detail: '兴证e家需要你补充：X-MCP-JWT', knowledge_base_id: null, target_agent_id: null,
            hit_count: null, payload: { input_request_id: 'TI-1' }, created_at: '2026-09-22T10:00:03',
          },
        ],
      }],
      '/api/v1/conversations/CONV-1/input-requests': () => (
        submitted
          ? [{ ...pendingRequest, status: 'submitted', submitted_values: { 'X-MCP-JWT': '****hYB0' } }]
          : [pendingRequest]
      ),
      '/api/v1/conversations/CONV-1': conv1,
      '/api/v1/conversations/CONV-1/runs/EX-INPUT/input': () => {
        submitted = true;
        return { execution_id: 'EX-INPUT-2', trigger_message_seq: 3, conversation: conv1 };
      },
    });
    renderPage();
    fireEvent.click(await screen.findByText('新员工入职助手'));

    // 该执行已完成 → 轨迹默认折叠；补全气泡不能放在折叠区里，否则用户根本看不到。
    expect(await screen.findByText('需要补充信息')).toBeInTheDocument();
    expect(screen.getByText('X-MCP-JWT')).toBeInTheDocument();
    expect(screen.getByText('从兴证e家 App 复制你的个人令牌')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('粘贴你的个人凭据'), {
      target: { value: 'jwt-value-from-employee' },
    });
    fireEvent.click(screen.getByRole('button', { name: '提交并继续' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) =>
        String(url).endsWith('/api/v1/conversations/CONV-1/runs/EX-INPUT/input'),
      );
      expect(call).toBeTruthy();
      const body = (call as [RequestInfo | URL, RequestInit | undefined] | undefined)?.[1]?.body;
      expect(JSON.parse(String(body))).toEqual({
        actor_no: 'E10281', request_id: 'TI-1', values: { 'X-MCP-JWT': 'jwt-value-from-employee' },
      });
    });
    // 提交后刷新为已提交态，卡片收起为结果条（明文不回显）
    expect(await screen.findByText('已补充 X-MCP-JWT · 已继续执行')).toBeInTheDocument();
  });

  it('补全气泡可以忽略：落库为 cancelled，刷新后不再弹出', async () => {
    class SilentEventSource {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSED = 2;
      readyState = 1;
      withCredentials = true;
      url = '';
      constructor(url: string | URL) {
        this.url = String(url);
      }
      addEventListener() {}
      removeEventListener() {}
      dispatchEvent() { return true; }
      close() { this.readyState = 2; }
    }
    vi.stubGlobal('EventSource', SilentEventSource);
    const fetchMock = stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [conv1Summary],
      '/api/v1/conversations/CONV-1/runs/active': null,
      '/api/v1/conversations/CONV-1/runs/history': [{
        execution: {
          id: 'EX-INPUT', conversation_id: 'CONV-1', trigger_message_seq: 1,
          trace_id: 'T-INPUT', primary_employee_id: 'VE-0001', status: 'completed',
          stage: 'completed', error_code: '', error_message: '', retryable: false,
          started_at: '2026-09-22T10:00:00', updated_at: '2026-09-22T10:00:05',
          completed_at: '2026-09-22T10:00:05',
        },
        events: [],
      }],
      '/api/v1/conversations/CONV-1/input-requests': [{
        id: 'TI-9', conversation_id: 'CONV-1', execution_id: 'EX-INPUT', trace_id: 'T-INPUT',
        plugin_id: 'xyzq-ehome-mcp', tool_name: 'getEmpCardBalance',
        fields: [{
          key: 'X-MCP-JWT', label: '兴证e家用户令牌', kind: 'secret', required: true,
          hint: '从兴证e家 App 复制你的个人令牌', source: 'user_secret', masked: '', reason: 'missing',
        }],
        reason: 'missing', status: 'pending', submitted_values: {}, resolved_at: null,
        resolved_by: '', created_at: '2026-09-22T10:00:00',
      }],
      '/api/v1/conversations/CONV-1': conv1,
      '/api/v1/conversations/CONV-1/runs/EX-INPUT/input-requests/TI-9/ignore': {
        id: 'TI-9', status: 'cancelled',
      },
    });
    renderPage();
    fireEvent.click(await screen.findByText('新员工入职助手'));

    expect(await screen.findByText('需要补充信息')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '忽略' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) =>
        String(url).endsWith('/api/v1/conversations/CONV-1/runs/EX-INPUT/input-requests/TI-9/ignore'),
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call as [RequestInfo | URL, RequestInit | undefined] | undefined)?.[1]?.body)))
        .toEqual({ actor_no: 'E10281' });
    });
    expect(await screen.findByText(/已忽略本次信息补充/)).toBeInTheDocument();
  });

  it('退役旧入口：不再暴露使用指南按钮', async () => {
    stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [groupSummary],
    });
    renderPage();

    expect(await screen.findByText('张三的数字分身')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '使用指南' })).not.toBeInTheDocument();
  });

  it.skip('群聊任务卡片：V1 暂不开放 Agent Teams', async () => {
    let state: Conversation = groupConv;
    const fetchMock = stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [groupSummary],
      '/api/v1/conversations/CONV-G': () => state,
      '/api/v1/tasks/T-DEMO-1/approve': () => {
        state = groupConvCompleted;
        return groupTaskCompleted;
      },
    });
    renderPage();

    fireEvent.click(await screen.findByText('新员工入职协作（3）'));
    expect(await screen.findByText('整理新员工入职准备清单')).toBeInTheDocument();
    expect(screen.getByText('确认入职制度与材料清单')).toBeInTheDocument();
    expect(screen.getByText('运行时：DeepSeek Harness')).toBeInTheDocument();
    expect(screen.getByText('工具：入职流程 Workflow')).toBeInTheDocument();
    expect(screen.getByText(/计划结束/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '展开' })).not.toBeInTheDocument();
    expect(screen.getByText('敏感操作需审批（POLICY-005）')).toBeInTheDocument();
    expect(screen.getAllByText('待审批').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: '批准' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([inputUrl, init]) =>
        String(inputUrl).endsWith('/api/v1/tasks/T-DEMO-1/approve') && init?.method === 'POST',
      );
      expect(call).toBeTruthy();
      const body = (call as [RequestInfo | URL, RequestInit | undefined] | undefined)?.[1]?.body;
      expect(JSON.parse(String(body))).toEqual({ approve: true, actor_no: 'E10281' });
    });
    expect(await screen.findByText('材料清单与 IT 账号已确认，权限报表已生成，入职准备就绪。')).toBeInTheDocument();
    expect(screen.getByText('运行时：Demo Adapter 降级')).toBeInTheDocument();
    expect(screen.getByText('工具：报表机器人 · RPA')).toBeInTheDocument();
    expect(screen.getAllByText('已完成').length).toBeGreaterThan(0);
  });

  it.skip('群聊发送：V1 暂不开放 Agent Teams', async () => {
    const fetchMock = stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [groupSummary],
      '/api/v1/conversations/CONV-G': groupConvEmpty,
      '/api/v1/conversations/CONV-G/messages': groupConvWithTask,
    });
    renderPage();

    fireEvent.click(await screen.findByText('新员工入职协作（3）'));
    await screen.findByText('打个招呼，开始今天的协作吧。');

    const input = screen.getByPlaceholderText('描述一个任务，我来拆解安排给同事们…');
    fireEvent.change(input, { target: { value: '帮我写周报' } });
    fireEvent.click(screen.getByRole('button', { name: /发送/ }));

    expect(screen.getByText('帮我写周报')).toBeInTheDocument();
    expect(await screen.findByText('整理新员工入职准备清单')).toBeInTheDocument();
    // 已完成任务默认折叠，点展开看子任务
    expect(screen.queryByText('确认入职制度与材料清单')).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('展开任务详情'));
    expect(await screen.findByText('确认入职制度与材料清单')).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([inputUrl]) =>
      String(inputUrl).endsWith('/api/v1/conversations/CONV-G/messages'),
    );
    expect(call).toBeTruthy();
  });

  it.skip('后台群任务轮询：V1 暂不开放 Agent Teams', async () => {
    const triggerSeq = 20;
    const waitingConv: Conversation = {
      ...groupConvEmpty,
      messages: [
        {
          id: 20,
          conversation_id: 'CONV-G',
          participant_no: 'E10281',
          participant_name: '张三',
          role: 'user',
          content: '请持续执行入职协作测试',
          tool_cards: [],
          seq: triggerSeq,
        },
      ],
    };
    const runningTask: TaskRun = {
      ...groupTask,
      id: 'T-POLL-1',
      trace_id: 'T-POLL-1',
      trigger_message_seq: triggerSeq,
      request: '请持续执行入职协作测试',
      status: 'running',
      subtasks: groupTask.subtasks.map((sub) => ({ ...sub, status: 'running' })),
    };
    const completedTask: TaskRun = {
      ...runningTask,
      status: 'completed',
      subtasks: runningTask.subtasks.map((sub) => ({ ...sub, status: 'completed' })),
      summary: '持续轮询任务已完成。',
    };
    let sent = false;
    let pollsAfterSend = 0;
    stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [groupSummary],
      '/api/v1/conversations/CONV-G': () => {
        if (!sent) return groupConvEmpty;
        pollsAfterSend += 1;
        if (pollsAfterSend === 1) return waitingConv;
        if (pollsAfterSend === 2) return { ...waitingConv, tasks: [runningTask] };
        return { ...waitingConv, tasks: [completedTask] };
      },
      '/api/v1/conversations/CONV-G/messages': () => {
        sent = true;
        return waitingConv;
      },
    });
    const view = renderPage();

    fireEvent.click(await screen.findByText('新员工入职协作（3）'));
    await screen.findByText('打个招呼，开始今天的协作吧。');
    // 群聊标题栏固定标注「协作空间」。
    expect(view.container.querySelector('.type-badge--header')).toHaveTextContent('协作空间');
    fireEvent.change(screen.getByPlaceholderText('描述一个任务，我来拆解安排给同事们…'), {
      target: { value: '请持续执行入职协作测试' },
    });
    fireEvent.click(screen.getByRole('button', { name: /发送/ }));

    expect(await screen.findByText('持续轮询任务已完成。', {}, { timeout: 12000 })).toBeInTheDocument();
    expect(pollsAfterSend).toBeGreaterThanOrEqual(3);
  }, 15000);

  it.skip('群聊清空：V1 暂不开放 Agent Teams', async () => {
    let state: Conversation = groupConv;
    const fetchMock = stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [groupSummary],
      '/api/v1/conversations/CONV-G': () => state,
      '/api/v1/conversations/CONV-G?actor_no=E10281': () => {
        state = groupConvEmpty;
        return { ok: true };
      },
    });
    renderPage();

    fireEvent.click(await screen.findByText('新员工入职协作（3）'));
    expect(await screen.findByText('整理新员工入职准备清单')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '清空会话' }));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([inputUrl, init]) =>
        String(inputUrl).endsWith('/api/v1/conversations/CONV-G?actor_no=E10281') && init?.method === 'DELETE',
      );
      expect(call).toBeTruthy();
    });
    expect(await screen.findByText('打个招呼，开始今天的协作吧。')).toBeInTheDocument();
    expect(screen.queryByText('整理新员工入职准备清单')).not.toBeInTheDocument();
  });

  it('按消息正文搜索：命中片段 → 点击 → 打开会话并高亮那条消息', async () => {
    const searchHit: ConversationSearchHit = {
      conversation: conv1Summary,
      matched_in: 'content',
      match_count: 1,
      match_seq: 2,
      match_role: 'assistant',
      match_speaker: '新员工入职助手',
      snippet: '…需要我帮忙吗？…',
    };
    const fetchMock = stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [conv1Summary],
      '&limit=20': [searchHit],
      '/api/v1/conversations/CONV-1': conv1,
    });
    const view = renderPage();

    fireEvent.change(await screen.findByPlaceholderText('搜索会话'), { target: { value: '帮忙' } });

    // 250ms 防抖之后才会真的打检索接口，关键字按 URL 编码透传。
    const hitMeta = await screen.findByText('1 处提到');
    expect(screen.getByText('帮忙', { selector: 'mark' })).toBeInTheDocument();
    const searchCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/conversations/search'));
    expect(String(searchCall?.[0])).toContain('q=%E5%B8%AE%E5%BF%99');

    // 命中行是 `div role="button"`，点行内文本（事件冒泡到行）。
    fireEvent.click(hitMeta);

    expect(await screen.findByText('你好呀，需要我帮忙吗？')).toBeInTheDocument();
    await waitFor(() => {
      expect(view.container.querySelector('.message-row--focus')).toBeTruthy();
    });
  }, 10000);

});
