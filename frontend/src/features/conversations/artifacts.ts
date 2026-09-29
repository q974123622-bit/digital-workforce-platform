import type { ExecutionArtifact } from '@dwp/shared-schema';

/**
 * AI员工产物的**唯一出口**：URL 构造 + 类型判定 + 文本读取。
 *
 * 背景：模型在答复里只会写「完整核对表已保存至 /workspace/output/核对表.md」。
 * 用户看不到也拿不到那个文件 —— 平台把产物快照下来，这里给出预览/下载所需的全部口径。
 *
 * 「怎么预览」只读后端出参 `kind`（markdown/html/csv/text），不要在别处按后缀判断。
 */

export type ArtifactKind = 'markdown' | 'html' | 'csv' | 'text';

const BASE = '/api/v1';

const KNOWN_KINDS: ArtifactKind[] = ['markdown', 'html', 'csv', 'text'];

export function artifactKindOf(item: ExecutionArtifact | null | undefined): ArtifactKind {
  const kind = item?.kind;
  return KNOWN_KINDS.includes(kind as ArtifactKind) ? (kind as ArtifactKind) : 'text';
}

export function artifactContentUrl(
  conversationId: string,
  artifactId: string,
  download = false,
): string {
  const url = `${BASE}/conversations/${encodeURIComponent(conversationId)}/artifacts/${encodeURIComponent(artifactId)}/content`;
  return download ? `${url}?download=1` : url;
}

export function formatArtifactSize(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '—';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * 读取产物文本。
 *
 * 走 `fetch` + `credentials: 'include'` 而不是把 URL 直接丢给 `<iframe src>`：
 * 一是预览要拿到文本（Markdown 用站内渲染器、HTML 用 `srcDoc` + sandbox），
 * 二是这样不依赖浏览器在沙箱 iframe 里是否携带 cookie。
 */
export async function fetchArtifactText(
  conversationId: string,
  artifactId: string,
): Promise<string> {
  const response = await fetch(artifactContentUrl(conversationId, artifactId), {
    credentials: 'include',
  });
  if (!response.ok) {
    throw new Error(`读取产物失败（HTTP ${response.status}）`);
  }
  return response.text();
}

/** 从执行事件里收集产物（实时 SSE 用；历史回放走 /runs/history 的 artifacts）。 */
export function artifactsFromEvent(data: Record<string, unknown>): ExecutionArtifact[] {
  const raw = data.artifacts;
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is ExecutionArtifact => (
    Boolean(item) && typeof item === 'object'
    && typeof (item as ExecutionArtifact).artifact_id === 'string'
  ));
}
