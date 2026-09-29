import type { ConversationAttachment } from '@dwp/shared-schema';

/**
 * 附件预览的**唯一出口**：URL 构造 + 类型判定。
 *
 * 「能不能预览」只读后端出参 `preview_kind`（缺省 `none`），
 * 不要在别处按 `file_type` 字符串判断 —— 否则新增类型时必然漏改。
 */

export type PreviewKind = 'image' | 'pdf' | 'none';

const BASE = '/api/v1';

export function previewKindOf(item: ConversationAttachment | null | undefined): PreviewKind {
  const kind = item?.preview_kind;
  return kind === 'image' || kind === 'pdf' ? kind : 'none';
}

export function isImageAttachment(item: ConversationAttachment | null | undefined): boolean {
  return previewKindOf(item) === 'image';
}

export function isPdfAttachment(item: ConversationAttachment | null | undefined): boolean {
  return previewKindOf(item) === 'pdf';
}

export function isPreviewable(item: ConversationAttachment | null | undefined): boolean {
  return previewKindOf(item) !== 'none';
}

function attachmentUrl(conversationId: string, attachmentId: string, suffix: string): string {
  return `${BASE}/conversations/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(attachmentId)}/${suffix}`;
}

/** 缩略图 URL（长边 ≤480 JPEG，后端带 ETag/缓存头）。 */
export function attachmentThumbnailUrl(conversationId: string, attachmentId: string): string {
  return attachmentUrl(conversationId, attachmentId, 'thumbnail');
}

/**
 * 原文件 URL。默认 `Content-Disposition: inline`（PDF 可内嵌阅读），
 * `download=true` 时改 `attachment` 触发另存。
 */
export function attachmentContentUrl(
  conversationId: string,
  attachmentId: string,
  download = false,
): string {
  const url = attachmentUrl(conversationId, attachmentId, 'content');
  return download ? `${url}?download=1` : url;
}
