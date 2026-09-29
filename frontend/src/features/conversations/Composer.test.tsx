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

  it('图片附件渲染缩略图且保留状态与移除按钮，点击触发预览', () => {
    const onPreviewAttachment = vi.fn();
    const onRemoveAttachment = vi.fn();
    const image: ConversationAttachment = {
      ...attachment('ready'),
      attachment_id: 'ATT-IMG',
      filename: '开户截图.png',
      file_type: 'png',
      mime_type: 'image/png',
      preview_kind: 'image',
    };
    render(
      <Composer
        value=""
        onChange={vi.fn()}
        onSend={vi.fn()}
        attachments={[image]}
        onPreviewAttachment={onPreviewAttachment}
        onRemoveAttachment={onRemoveAttachment}
      />,
    );

    const thumb = screen.getByRole('img', { name: '开户截图.png' });
    expect(thumb).toHaveAttribute('src', expect.stringContaining('/thumbnail'));
    // 原有的状态文字与移除入口不能因为换成缩略图而丢。
    expect(screen.getByText('可供分析')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '预览 开户截图.png' }));
    expect(onPreviewAttachment).toHaveBeenCalledWith('ATT-IMG');
    expect(onRemoveAttachment).not.toHaveBeenCalled();
  });

  it('缩略图加载失败时退回图标，仍可移除', () => {
    const image: ConversationAttachment = {
      ...attachment('ready'),
      attachment_id: 'ATT-BAD',
      filename: '坏图.png',
      file_type: 'png',
      preview_kind: 'image',
    };
    render(<Composer value="" onChange={vi.fn()} onSend={vi.fn()} attachments={[image]} onPreviewAttachment={vi.fn()} />);

    fireEvent.error(screen.getByRole('img', { name: '坏图.png' }));
    expect(screen.queryByRole('button', { name: '预览 坏图.png' })).toBeNull();
    expect(screen.getByText('坏图.png')).toBeInTheDocument();
  });
});
