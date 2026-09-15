import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import Composer from './Composer';
import type { ConversationAttachment } from '@dwp/shared-schema';

const attachment = (status: ConversationAttachment['status']): ConversationAttachment => ({
  attachment_id: 'ATT-1', conversation_id: 'CONV-1', filename: '制度.docx',
  file_type: 'docx', mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  size: 1024, status, processing_hint: null, error_code: '', page_count: null,
  sheet_count: null, chunk_count: status === 'ready' ? 2 : 0, message_seq: null,
  created_at: '2026-09-15T09:00:00',
});

describe('Composer attachments', () => {
  it('附件未解析完成时阻止发送，完成后恢复', () => {
    const props = { value: '请分析', onChange: vi.fn(), onSend: vi.fn() };
    const { rerender } = render(<Composer {...props} attachments={[attachment('processing')]} />);
    expect(screen.getByRole('button', { name: '发送消息' })).toBeDisabled();
    rerender(<Composer {...props} attachments={[attachment('ready')]} />);
    expect(screen.getByRole('button', { name: '发送消息' })).toBeEnabled();
  });

  it('支持把文件拖入输入区', () => {
    const onFiles = vi.fn();
    render(<Composer value="分析附件" onChange={vi.fn()} onSend={vi.fn()} onFiles={onFiles} />);
    const dropZone = screen.getByPlaceholderText('发消息…').closest('.composer-box');
    const file = new File(['hello'], '说明.md', { type: 'text/markdown' });
    fireEvent.drop(dropZone!, { dataTransfer: { files: [file] } });
    expect(onFiles).toHaveBeenCalledWith([file]);
  });
});
