import { Fragment } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';

/**
 * 表格单元格等「行内上下文」的 Markdown 渲染。
 *
 * 背景（2026-09-18）：`TableBlock` 原先直接把单元格字符串塞进 `<td>`，模型写在单元格里的
 * `**加粗**`、`` `代码` ``、`[链接](url)` 全部以裸符号显示（正文片段走 ReactMarkdown 所以正常）。
 * 这里复用 react-markdown 的**行内**能力，并把块级包裹（`<p>`/`<ul>`/标题/引用）拆掉，
 * 避免 `td` 里出现块级元素撑高行。
 *
 * 两个细节：
 *  - 纯文本单元格走快速路径原样输出：表格列多时创建成百上千个 ReactMarkdown 实例会拖慢流式渲染；
 *  - `<br>` 手动切成 `<br />`：react-markdown 未挂 rehype-raw 时原始 HTML 会被丢弃，
 *    而模型常把 `<br>` 当作单元格内换行；其余裸 `<` 用反斜杠转义成字面量，避免被吞。
 */

/** 触发「需要解析」的行内标记（`<` 不参与触发：单独出现时按纯文本输出更安全）。 */
const INLINE_MARK_RE = /[*_`~[\]\\]/;

const BR_RE = /<br\s*\/?>/i;

/** 未挂 rehype-raw 时 react-markdown 会丢弃原始 HTML，把非 `<br>` 的裸 `<` 转义成字面量。 */
function escapeRawAngle(text: string): string {
  return text.replace(/</g, '\\<');
}

const inlineComponents: Components = {
  // 单元格是行内上下文：拆掉 <p> 包裹，否则 td 里出现块级元素、行高被撑开
  p: ({ children }) => <>{children}</>,
  // 单元格里若出现列表/标题/引用，同样降级为纯文本流
  ul: ({ children }) => <>{children}</>,
  ol: ({ children }) => <>{children}</>,
  li: ({ children }) => <>{children}</>,
  h1: ({ children }) => <>{children}</>,
  h2: ({ children }) => <>{children}</>,
  h3: ({ children }) => <>{children}</>,
  h4: ({ children }) => <>{children}</>,
  h5: ({ children }) => <>{children}</>,
  h6: ({ children }) => <>{children}</>,
  blockquote: ({ children }) => <>{children}</>,
  hr: () => null,
  pre: ({ children }) => <>{children}</>,
  code: ({ children }) => <code className="md-inline-code">{children}</code>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer">{children}</a>
  ),
  // 单元格里不渲染外链图片，转成链接，避免破图图标
  img: ({ src, alt }) => (
    <a href={typeof src === 'string' ? src : undefined} target="_blank" rel="noreferrer">
      {alt || '图片'}
    </a>
  ),
};

function renderSegment(segment: string) {
  if (!segment) return null;
  if (!INLINE_MARK_RE.test(segment)) return segment;
  return <ReactMarkdown components={inlineComponents}>{escapeRawAngle(segment)}</ReactMarkdown>;
}

export default function InlineMarkdown({ text }: { text: string }) {
  if (!text) return null;
  if (!BR_RE.test(text)) return <>{renderSegment(text)}</>;

  return (
    <>
      {text.split(BR_RE).map((segment, index) => (
        // 段落内容不可重排，按序拼接，index 作为 key 稳定
        <Fragment key={index}>
          {index > 0 && <br />}
          {renderSegment(segment)}
        </Fragment>
      ))}
    </>
  );
}
