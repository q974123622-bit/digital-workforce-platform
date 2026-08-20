import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import type {
  Conversation,
  ConversationSummary,
  Employee,
  Skill,
  TaskRun,
  Workflow,
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
    expect(screen.getByText('我的分身')).toBeInTheDocument();
    expect(screen.getByText('新员工入职助手')).toBeInTheDocument();
    expect(screen.getByText('你好呀，需要我帮忙吗？')).toBeInTheDocument();
  });

  it('通讯录分组与搜索，点击私聊发起会话', async () => {
    const fetchMock = stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [],
      '/api/v1/conversations': conv1,
      '/api/v1/conversations/CONV-1': conv1,
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '通讯录' }));
    expect(screen.getByText('我的分身')).toBeInTheDocument();
    expect(screen.getByText('智能助理')).toBeInTheDocument();
    expect(screen.getByText('自动化小助手')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('搜索联系人'), { target: { value: '报表' } });
    expect(screen.getByText('报表机器人')).toBeInTheDocument();
    expect(screen.queryByText('新员工入职助手')).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('搜索联系人'), { target: { value: '入职' } });
    fireEvent.click(screen.getByRole('button', { name: '私聊' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith('/api/v1/conversations') && init?.method === 'POST');
      expect(call).toBeTruthy();
      const body = (call as [RequestInfo | URL, RequestInit | undefined] | undefined)?.[1]?.body;
      expect(JSON.parse(String(body))).toEqual({
        actor_no: 'E10281',
        kind: 'direct',
        participant_employee_nos: ['VE-0001'],
      });
    });
    expect(await screen.findByText('你好呀，需要我帮忙吗？')).toBeInTheDocument();
  });

  it('发送消息：微信式气泡渲染 + 调用消息接口', async () => {
    const fetchMock = stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [conv1Summary],
      '/api/v1/conversations/CONV-1': conv1,
      '/api/v1/conversations/CONV-1/messages': conv1WithReply,
    });
    const view = renderPage();

    fireEvent.click(await screen.findByText('新员工入职助手'));
    expect(await screen.findByText('你好呀，需要我帮忙吗？')).toBeInTheDocument();
    expect(screen.getByText('你好')).toBeInTheDocument();

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
      String(inputUrl).endsWith('/api/v1/conversations/CONV-1/messages'),
    );
    const body = (call as [RequestInfo | URL, RequestInit | undefined] | undefined)?.[1]?.body;
    expect(JSON.parse(String(body))).toEqual({ actor_no: 'E10281', content: '帮我准备入职' });
  });

  it('技能抽屉：上传技能与启停开关', async () => {
    const fetchMock = stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [],
      '/api/v1/skills': { id: 'SK-0002', owner_human_no: 'E10281', name: '会议纪要模板', status: 'active', created_at: '2026-08-19T10:00:00', description: '', content: '结论先行' },
      '/api/v1/skills/SK-0001?actor_no=E10281': { ...skill, status: 'disabled' },
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '通讯录' }));
    fireEvent.click(screen.getByRole('button', { name: '技能' }));
    expect(await screen.findByText('报销制度速答')).toBeInTheDocument();
    // 分身插件能力也展示在资料抽屉
    expect(screen.getByText('可用能力（插件授权 · 2）')).toBeInTheDocument();
    expect(screen.getByText('公开制度知识库')).toBeInTheDocument();

    // 上传技能
    fireEvent.click(screen.getByRole('button', { name: /上传技能/ }));
    fireEvent.change(screen.getByLabelText('技能名称'), { target: { value: '会议纪要模板' } });
    fireEvent.change(screen.getByLabelText('技能内容'), { target: { value: '结论先行，行动项带负责人。' } });
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

  it('群聊任务卡片：子任务/审批警示/审批按钮与汇总', async () => {
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

  it('群聊发送：先渲染用户气泡，再展示任务卡片', async () => {
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

  it('后台首次无结果时持续轮询，直到对应任务完成', async () => {
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
    renderPage();

    fireEvent.click(await screen.findByText('新员工入职协作（3）'));
    await screen.findByText('打个招呼，开始今天的协作吧。');
    fireEvent.change(screen.getByPlaceholderText('描述一个任务，我来拆解安排给同事们…'), {
      target: { value: '请持续执行入职协作测试' },
    });
    fireEvent.click(screen.getByRole('button', { name: /发送/ }));

    expect(await screen.findByText('持续轮询任务已完成。', {}, { timeout: 12000 })).toBeInTheDocument();
    expect(pollsAfterSend).toBeGreaterThanOrEqual(3);
  }, 15000);

  it('清空会话：确认后删除消息与任务', async () => {
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

  it('工作流卡片：点击查看步骤/授权成员/示例指令', async () => {
    const workflows: Workflow[] = [
      {
        plugin_id: 'expense-claim',
        name: '差旅报销流程',
        type: 'workflow',
        data_level: 'L2',
        description: '差旅报销申请与审批打款流程（Mock）',
        steps: ['报销申请提交', '直属领导审批', '财务复核打款'],
        demo_prompt: '帮我提交差旅报销',
        authorized_employees: [{ employee_no: 'VE-0002', name: 'HR 助理', type: 'virtual' }],
        owner_employee: { employee_no: 'VE-0002', name: 'HR 助理', type: 'virtual' },
      },
    ];
    stubFetch({
      '/api/v1/workplace?actor_no=E10281': home,
      '/api/v1/conversations?actor_no=E10281': [],
      '/api/v1/workflows': workflows,
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '工作流' }));
    expect(screen.getByText('差旅报销流程')).toBeInTheDocument();
    fireEvent.click(screen.getByText('差旅报销流程'));

    expect(await screen.findByText('执行步骤')).toBeInTheDocument();
    expect(screen.getByText('报销申请提交')).toBeInTheDocument();
    expect(screen.getByText('HR 助理')).toBeInTheDocument();
    expect(screen.getByText(/帮我提交差旅报销/)).toBeInTheDocument();
  });
});
