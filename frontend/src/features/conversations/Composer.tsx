import { CloseOutlined, FileTextOutlined, PaperClipOutlined, ReloadOutlined, SendOutlined, StopOutlined } from '@ant-design/icons';
import { Button, Input, Tooltip, Upload } from 'antd';
import { useState } from 'react';
import type { DragEvent, KeyboardEvent } from 'react';
import type { ConversationAttachment } from '@dwp/shared-schema';
import { attachmentThumbnailUrl, previewKindOf } from './attachmentPreview';

const STATUS_LABEL: Record<string, string> = {
  pending: '等待解析', processing: '正在解析', ready: '可供分析', failed: '解析失败',
  ocr_required: '需要 OCR', conversion_required: '需要转换', deleted: '已删除',
};

/**
 * 多行输入区：Enter 发送、Shift+Enter 换行，正确处理中文输入法组合态。
 * 受控组件，草稿按会话持久化由调用方负责（见 onSend 与 value/onChange）。
 */
export default function Composer({
  value,
  onChange,
  onSend,
  disabled = false,
  sending = false,
  active = false,
  stopping = false,
  onStop,
  placeholder = '发消息…',
  attachments = [],
  onFiles,
  onPack,
  onRemoveAttachment,
  onRetryAttachment,
  onPreviewAttachment,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled?: boolean;
  sending?: boolean;
  /** 当前有执行在跑：右侧按钮由「发送」换成「停止」，输入框仍可继续打字准备下一条。 */
  active?: boolean;
  stopping?: boolean;
  onStop?: () => void;
  placeholder?: string;
  attachments?: ConversationAttachment[];
  onFiles?: (files: File[]) => void;
  /** 材料通道：一个 zip，或一次多个文件。不受「单次 5 个」限制，物化到 /workspace/materials */
  onPack?: (files: File[]) => void;
  onRemoveAttachment?: (attachment: ConversationAttachment) => void;
  onRetryAttachment?: (attachment: ConversationAttachment) => void;
  /** 点击待发送附件的缩略图时打开预览灯箱（发送前也能确认选对了图）。 */
  onPreviewAttachment?: (attachmentId: string) => void;
}) {
  const [composing, setComposing] = useState(false);
  // 缩略图加载失败时退回图标，不整块崩。
  const [failedThumbs, setFailedThumbs] = useState<string[]>([]);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // 中文输入法组合态（拼音候选）下按 Enter 仅确认候选词，不触发发送。
    if (e.key === 'Enter' && !e.shiftKey && !composing && !e.nativeEvent.isComposing) {
      e.preventDefault();
      onSend();
    }
  };

  const acceptFiles = (files: File[]) => {
    // antd Upload 的 beforeUpload 第二个参数是 FileList 形态，没有 some/length 之外的数组方法
    // （真机踩过：直接 .some 会抛异常、整个上传静默失败），统一转成真数组再判。
    const list = Array.from(files as ArrayLike<File>);
    if (disabled || !list.length) return;
    // 含 zip，或一次选了多个文件 → 材料通道（不受「单次 5 个」限制，落到 /workspace/materials）
    if (list.some((file) => file.name.toLowerCase().endsWith('.zip')) || list.length >= 2) {
      onPack?.(list);
      return;
    }
    onFiles?.(list);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    acceptFiles(Array.from(event.dataTransfer.files));
  };

  const hasBlockingAttachment = attachments.some((item) => item.status !== 'ready');

  return (
    <div
      className="composer-box mx-auto w-full max-w-[960px] rounded-[14px] border border-line bg-surface p-2 transition-shadow focus-within:border-brand"
      onDragOver={(event) => event.preventDefault()}
      onDrop={handleDrop}
    >
      {attachments.length > 0 && (
        <div className="mb-2 flex max-h-28 flex-wrap gap-2 overflow-y-auto border-b border-line-light px-1 pb-2">
          {attachments.map((item) => {
            const thumbable = previewKindOf(item) !== 'none' && !failedThumbs.includes(item.attachment_id);
            return (
            <div key={item.attachment_id} className="flex min-w-0 max-w-[280px] items-center gap-2 rounded-control border border-line-light bg-surface-subtle px-2.5 py-1.5">
              {thumbable ? (
                <button
                  type="button"
                  title={item.filename}
                  aria-label={`预览 ${item.filename}`}
                  className="h-14 w-14 shrink-0 overflow-hidden rounded-control border border-line transition-colors hover:border-brand"
                  onClick={() => onPreviewAttachment?.(item.attachment_id)}
                >
                  <img
                    src={attachmentThumbnailUrl(item.conversation_id, item.attachment_id)}
                    alt={item.filename}
                    loading="lazy"
                    className="h-full w-full object-cover"
                    onError={() => setFailedThumbs((current) => (
                      current.includes(item.attachment_id) ? current : [...current, item.attachment_id]
                    ))}
                  />
                </button>
              ) : (
                <FileTextOutlined className="shrink-0 text-brand" />
              )}
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium text-ink">{item.filename}</div>
                <div className={`text-[10px] ${item.status === 'ready' ? 'text-success' : item.status === 'failed' ? 'text-danger' : 'text-ink-tertiary'}`}>
                  {STATUS_LABEL[item.status] ?? item.status}
                </div>
              </div>
              {item.status === 'failed' && (
                <Tooltip title="重新解析"><Button type="text" size="small" icon={<ReloadOutlined />} onClick={() => onRetryAttachment?.(item)} /></Tooltip>
              )}
              <Tooltip title="移除"><Button type="text" size="small" icon={<CloseOutlined />} onClick={() => onRemoveAttachment?.(item)} /></Tooltip>
            </div>
            );
          })}
        </div>
      )}
      <Input.TextArea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={() => setComposing(false)}
        placeholder={placeholder}
        autoSize={{ minRows: 1, maxRows: 4 }}
        disabled={disabled && !active}
        variant="borderless"
        className="w-full resize-none !px-2 !py-1.5"
      />
      <div className="mt-1 flex items-center justify-between pl-2">
        <div className="flex items-center gap-1">
          <Upload
            multiple accept=".zip,.md,.markdown,.txt,.doc,.docx,.pdf,.xls,.xlsx,.csv,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.gif"
            showUploadList={false}
            beforeUpload={(file, batch) => { if (file.uid === batch[0]?.uid) acceptFiles(batch as unknown as File[]); return Upload.LIST_IGNORE; }}
          >
            <Tooltip title="上传材料（可多选；也可选一个 zip，或直接拖入输入区）"><Button type="text" size="small" disabled={disabled} icon={<PaperClipOutlined />} aria-label="上传材料" /></Tooltip>
          </Upload>
          <span className="text-[11px] text-ink-tertiary">Enter 发送 · 支持图片、文档、PDF 与表格</span>
        </div>
        {active ? (
          <Tooltip title="停止执行（已完成的步骤会保留，不可续跑）">
            <Button
              type="primary"
              danger
              shape="circle"
              icon={<StopOutlined />}
              onClick={onStop}
              loading={stopping}
              aria-label="停止执行"
            />
          </Tooltip>
        ) : (
          <Tooltip title={hasBlockingAttachment ? '等待附件解析完成后发送' : undefined}>
            <Button
              type="primary"
              shape="circle"
              icon={<SendOutlined />}
              onClick={onSend}
              loading={sending}
              disabled={disabled || !value.trim() || hasBlockingAttachment}
              aria-label="发送消息"
            />
          </Tooltip>
        )}
      </div>
    </div>
  );
}
