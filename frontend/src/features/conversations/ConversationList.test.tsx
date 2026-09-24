import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ConversationSearchHit, ConversationSummary, Employee } from '@dwp/shared-schema';
import ConversationList from './ConversationList';

const employee = (overrides: Partial<Employee>): Employee => ({
  id: '', employee_no: '', name: '', type: 'virtual', employment_type: 'formal', source_human_no: null,
  owner_human_no: '', department: '', role_prompt: '', status: 'active', runtime_type: 'container', runtime_ref: null,
  location: 'local', internet: 'deny', max_data_level: 'L1', allowed_domains: [], grants: [],
  ...overrides,
});

const twinEmployee = employee({ id: 'DT-E10281', employee_no: 'DT-E10281', name: '张三的数字分身', type: 'twin' });
const veEmployee = employee({ id: 'VE-0001', employee_no: 'VE-0001', name: '新员工入职助手', type: 'virtual' });

const conversation = (id: string, lastMessage: string): ConversationSummary => ({
  id,
  kind: 'direct',
  title: '',
  owner_human_no: 'E10281',
  participants: [{ employee_no: 'VE-0001', name: '新员工入职助手', role: 'member', employee_type: 'virtual' }],
  last_message: lastMessage,
  updated_at: '2026-08-19T09:05:00',
});

/** 分身直聊：participants[0].employee_type 是后端按 DigitalEmployee.type 回填的分段依据。 */
const twinConversation = (id: string, lastMessage: string): ConversationSummary => ({
  ...conversation(id, lastMessage),
  participants: [{ employee_no: 'DT-E10281', name: '张三的数字分身', role: 'member', employee_type: 'twin' }],
});

const groupConversation = (id: string, title: string, lastMessage: string): ConversationSummary => ({
  ...conversation(id, lastMessage),
  kind: 'group',
  title,
  participants: [
    { employee_no: 'DT-E10281', name: '张三的数字分身', role: 'organizer', employee_type: 'twin' },
    { employee_no: 'VE-0001', name: '新员工入职助手', role: 'member', employee_type: 'virtual' },
  ],
});

const baseProps = {
  twin: null,
  employees: [],
  selectedId: null,
  onKeywordChange: vi.fn(),
  onSelect: vi.fn(),
  onSelectHit: vi.fn(),
  onOpenTwin: vi.fn(),
  onOpenProfile: vi.fn(),
  onOpenDirectory: vi.fn(),
  onDelete: vi.fn(),
};

describe('ConversationList 会话检索', () => {
  it('命中消息正文时渲染片段、高亮关键字与命中处数，点击上报命中项', () => {
    const hit: ConversationSearchHit = {
      conversation: conversation('CONV-1', '你好呀，需要我帮忙吗？'),
      matched_in: 'content',
      match_count: 3,
      match_seq: 2,
      match_role: 'assistant',
      match_speaker: '新员工入职助手',
      snippet: '…请把报销单据在周五前交到财务处…',
    };
    const onSelectHit = vi.fn();
    render(
      <ConversationList
        {...baseProps}
        conversations={[hit.conversation]}
        keyword="报销"
        hits={[hit]}
        searching={false}
        onSelectHit={onSelectHit}
      />,
    );

    expect(screen.getByText('报销', { selector: 'mark' })).toBeInTheDocument();
    expect(screen.getByText('新员工入职助手：')).toBeInTheDocument();
    expect(screen.getByText('共 3 处提到')).toBeInTheDocument();

    // 命中行是 `div role="button"`，无障碍名由内容推导、不适合做查询键；
    // 直接点行内文本，事件冒泡到行本身。
    fireEvent.click(screen.getByText('共 3 处提到'));
    expect(onSelectHit).toHaveBeenCalledWith(hit);
  });

  it('无命中时给出空态与「去同事找AI员工」入口', () => {
    const onOpenDirectory = vi.fn();
    render(
      <ConversationList
        {...baseProps}
        conversations={[conversation('CONV-1', '你好呀')]}
        keyword="报销"
        hits={[]}
        searching={false}
        onOpenDirectory={onOpenDirectory}
      />,
    );

    expect(screen.getByText('没有找到相关会话')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '去「同事」按姓名找AI员工' }));
    expect(onOpenDirectory).toHaveBeenCalledTimes(1);
  });

  it('内容检索不可用时退回本地按会话名/最近消息筛选并提示', () => {
    render(
      <ConversationList
        {...baseProps}
        conversations={[conversation('CONV-1', '报销单据请交到财务处'), conversation('CONV-2', '今天天气不错')]}
        keyword="报销"
        hits={[]}
        searching={false}
        searchError="检索服务不可用"
      />,
    );

    expect(screen.getByText('内容检索暂不可用，已按会话名筛选')).toBeInTheDocument();
    expect(screen.getByText('报销单据请交到财务处')).toBeInTheDocument();
    expect(screen.queryByText('今天天气不错')).not.toBeInTheDocument();
    expect(screen.queryByText('没有找到相关会话')).not.toBeInTheDocument();
  });
});

describe('ConversationList 分身 / AI员工 / 协作空间 分段', () => {
  const sectionProps = {
    ...baseProps,
    twin: twinEmployee,
    employees: [veEmployee],
    keyword: '',
    hits: [],
    searching: false,
  };

  it('直聊按类型分段，群聊单独成「协作空间」段，段头各自计数', () => {
    render(
      <ConversationList
        {...sectionProps}
        conversations={[
          twinConversation('CONV-TWIN', '分身会话：方案已发你'),
          conversation('CONV-VE', '你好呀，需要我帮忙吗？'),
          groupConversation('CONV-G', '新员工入职协作', '帮我把入职清单发出来'),
        ]}
      />,
    );

    const twinSection = screen.getByRole('region', { name: '我的分身' });
    expect(within(twinSection).getByText('张三的数字分身')).toBeInTheDocument();
    expect(within(twinSection).getByText('分身会话：方案已发你')).toBeInTheDocument();
    expect(within(twinSection).queryByText('你好呀，需要我帮忙吗？')).not.toBeInTheDocument();

    const employeeSection = screen.getByRole('region', { name: 'AI员工' });
    expect(within(employeeSection).getByText('你好呀，需要我帮忙吗？')).toBeInTheDocument();
    expect(within(employeeSection).queryByText('张三的数字分身')).not.toBeInTheDocument();

    // 群聊成员里有分身，但仍按 kind 归入「协作空间」，不混进分身段。
    const groupSection = screen.getByRole('region', { name: '协作空间' });
    expect(within(groupSection).getByText('新员工入职协作（2）')).toBeInTheDocument();
    expect(within(groupSection).getByText('帮我把入职清单发出来')).toBeInTheDocument();

    expect(screen.getByRole('heading', { name: /我的分身/ })).toHaveTextContent('1');
    expect(screen.getByRole('heading', { name: /AI员工/ })).toHaveTextContent('1');
    expect(screen.getByRole('heading', { name: /协作空间/ })).toHaveTextContent('1');
  });

  it('首页数据未加载（twin 为 null）时分身直聊仍归入「我的分身」段', () => {
    render(
      <ConversationList
        {...baseProps}
        conversations={[twinConversation('CONV-TWIN', '分身会话：方案已发你'), conversation('CONV-VE', '你好呀，需要我帮忙吗？')]}
        keyword=""
        hits={[]}
        searching={false}
      />,
    );

    const twinSection = screen.getByRole('region', { name: '我的分身' });
    expect(within(twinSection).getByText('分身会话：方案已发你')).toBeInTheDocument();
    // twin 未加载时不渲染「置顶分身入口」，分身段里只会有真实会话。
    expect(screen.queryByText('开始和我的分身聊聊吧')).not.toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'AI员工' })).queryByText('分身会话：方案已发你')).not.toBeInTheDocument();
  });

  it('检索命中态保持平铺，不渲染分段容器', () => {
    const hit: ConversationSearchHit = {
      conversation: groupConversation('CONV-G', '新员工入职协作', '帮我把入职清单发出来'),
      matched_in: 'content',
      match_count: 1,
      match_seq: 2,
      match_role: 'assistant',
      match_speaker: '新员工入职助手',
      snippet: '…入职清单已整理好…',
    };
    render(<ConversationList {...sectionProps} conversations={[hit.conversation]} keyword="入职" hits={[hit]} searching={false} />);

    expect(screen.queryByRole('region', { name: '协作空间' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'AI员工' })).not.toBeInTheDocument();
    expect(screen.getByText('入职', { selector: 'mark' })).toBeInTheDocument();
  });

  it('三段都为空时只给一个空态提示', () => {
    render(<ConversationList {...sectionProps} twin={null} conversations={[]} />);

    expect(screen.getByText('还没有会话')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '我的分身' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'AI员工' })).not.toBeInTheDocument();
  });
});
