import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Conversation, ConversationAttachment, ConversationMessage, Employee } from '@dwp/shared-schema';
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

const imageAttachment: ConversationAttachment = {
  attachment_id: 'ATT-IMG',
  conversation_id: 'CONV-G',
  filename: '开户截图.png',
  file_type: 'png',
  mime_type: 'image/png',
  size: 2048,
  status: 'ready',
  processing_hint: null,
  error_code: '',
  page_count: null,
  sheet_count: null,
  chunk_count: 0,
  message_seq: 1,
  preview_kind: 'image',
  created_at: '2026-09-15T09:00:00',
};

const documentAttachment: ConversationAttachment = {
  ...imageAttachment,
  attachment_id: 'ATT-DOC',
  filename: '制度说明.docx',
  file_type: 'docx',
  mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  preview_kind: 'none',
};

const attachmentConversation: Conversation = {
  ...conversation,
  messages: [
    message({
      id: 11, seq: 1, participant_no: 'E10281', participant_name: '张三', role: 'user',
      content: '看看这些材料', attachment_ids: ['ATT-IMG', 'ATT-DOC'],
    }),
  ],
};

const renderAttachmentList = (onPreviewAttachment?: (id: string) => void) =>
  render(
    <MessageList
      conversation={attachmentConversation}
      streamingLength={0}
      renderMessageExtra={() => null}
      emptyState={<div>还没有消息</div>}
      employeeOf={() => null}
      onOpenProfile={vi.fn()}
      attachmentOf={(id) => (id === 'ATT-IMG' ? imageAttachment : documentAttachment)}
      onOpenAttachment={vi.fn()}
      onPreviewAttachment={onPreviewAttachment}
    />,
  );

describe('MessageList 附件缩略图', () => {
  it('图片附件渲染缩略图，点击触发预览；不可预览类型保持文件卡片', () => {
    const onPreviewAttachment = vi.fn();
    renderAttachmentList(onPreviewAttachment);

    const thumb = screen.getByRole('img', { name: '开户截图.png' });
    expect(thumb).toHaveAttribute('src', expect.stringContaining('/thumbnail'));
    // 不可预览的 docx 仍走原来的「图标 + 文件名」卡片。
    expect(screen.getByText('制度说明.docx')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '预览 开户截图.png' }));
    expect(onPreviewAttachment).toHaveBeenCalledWith('ATT-IMG');
  });

  it('缩略图加载失败时退回文件卡片，不整块崩', () => {
    renderAttachmentList(vi.fn());

    fireEvent.error(screen.getByRole('img', { name: '开户截图.png' }));

    expect(screen.queryByRole('button', { name: '预览 开户截图.png' })).toBeNull();
    expect(screen.getByText('开户截图.png')).toBeInTheDocument();
  });
});

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
