import { useMemo } from 'react';

import { normalizeMarkdown } from './markdown/normalize';
import { resolveMarkdownRenderer } from './markdown/variant';

const Renderer = resolveMarkdownRenderer();

/**
 * 会话气泡内的 Markdown 渲染入口（薄壳）：
 *   1) `normalizeMarkdown` 先修模型输出的缺陷（未闭合围栏、缺分隔行的表格、围栏外多余空行）；
 *   2) 交给渲染器（当前为 `markdown/builtin`，切换方式见 `markdown/variant.ts`）。
 *
 * 归一化结果按 `text` 缓存：SSE 流式输出时父组件高频重渲染，不缓存会对同一条消息反复归一化。
 */
export default function MarkdownText({ text }: { text: string }) {
  const normalized = useMemo(() => normalizeMarkdown(text), [text]);
  return <Renderer text={normalized} />;
}
