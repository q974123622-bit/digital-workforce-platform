import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Conversation, ConversationMessage, Employee } from '@dwp/shared-schema';
import MessageList from './MessageList';

const employee = (overrides: Partial<Employee>): Employee => ({
  id: '', employee_no: '', name: '', type: 'virtual', employment_type: 'formal', source_human_no: null,
  owner_human_no: '', department: '', role_prompt: '', status: 'active', runtime_type: 'container', runtime_ref: null,
  location: 'local', internet: 'deny', max_data_level: 'L1', allowed_domains: [], grants: [],
  ...overrides,
});

const twin = employee({ id: 'DT-E10281', employee_no: 'DT-E10281', name: '张三的数字分身', type: 'twin' });
const assistant = employee({ id: 'VE-0001', employee_no: 'VE-0001', name: '新员工入职助手', type: 'virtual' });

const message = (overrides: Partial<ConversationMessage>): ConversationMessage => ({
  id: 1,
  conversation_id: 'CONV-G',
  participant_no: 'VE-0001',
  participant_name: '新员工入职助手',
  role: 'assistant',
  content: '收到',
  tool_cards: [],
  seq: 1,
  ...overrides,
});

/** 群聊：同一屏里同时有本人、分身、AI员工与「已不在名册」的说话人。 */
const conversation: Conversation = {
  id: 'CONV-G',
  kind: 'group',
  title: '新员工入职协作',
  owner_human_no: 'E10281',
  participants: [
    { employee_no: 'DT-E10281', name: '张三的数字分身', role: 'organizer', employee_type: 'twin' },
    { employee_no: 'VE-0001', name: '新员工入职助手', role: 'member', employee_type: 'virtual' },
  ],
  messages: [
    message({ id: 1, seq: 1, participant_no: 'E10281', participant_name: '张三', role: 'user', content: '帮我准备入职' }),
    message({ id: 2, seq: 2, participant_no: 'DT-E10281', participant_name: '张三的数字分身', content: '先把材料清单拉出来' }),
    message({ id: 3, seq: 3, participant_no: 'VE-0001', participant_name: '新员工入职助手', content: '清单已整理好' }),
    message({ id: 4, seq: 4, participant_no: 'VE-0009', participant_name: '已离职的同事助手', content: '这条查不到员工类型' }),
  ],
  tasks: [],
  updated_at: '2026-08-19T10:05:00',
};

const renderList = (data: Conversation = conversation) =>
  render(
    <MessageList
      conversation={data}
      streamingLength={0}
      renderMessageExtra={() => null}
      emptyState={<div>还没有消息</div>}
      employeeOf={(employeeNo) => [twin, assistant].find((item) => item.employee_no === employeeNo) ?? null}
      onOpenProfile={vi.fn()}
      attachmentOf={() => undefined}
      onOpenAttachment={vi.fn()}
    />,
  );

describe('MessageList 说话人类型徽章', () => {
  it('分身消息标注「我的分身」，AI员工消息标注「AI员工」', () => {
    renderList();

    expect(screen.getByText('我的分身')).toHaveClass('type-badge', 'type-badge--twin');
    expect(screen.getByText('AI员工')).toHaveClass('type-badge', 'type-badge--virtual');
  });

  it('自己的消息不显示类型徽章', () => {
    renderList();

    // 自己的气泡不渲染说话人姓名，按正文定位这一行。
    const row = screen.getByText('帮我准备入职').closest('.message-row');
    expect(row).not.toBeNull();
    expect(row?.querySelector('.type-badge')).toBeNull();
  });

  it('说话人已不在名册（查不到类型）时不渲染徽章，只保留姓名', () => {
    renderList();

    const row = screen.getByText('已离职的同事助手').closest('.message-row');
    expect(row).not.toBeNull();
    expect(row?.querySelector('.type-badge')).toBeNull();
  });
});
