import { useMemo } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';

import { splitBlocks } from './blocks';
import CodeBlock from './CodeBlock';
import TableBlock from './TableBlock';

/**
 * 零依赖渲染器：围栏代码块与 GFM 表格走自研组件，其余正文交给 `react-markdown`
 * （不加插件也能渲染加粗、列表、行内代码、引用、链接）。
 *
 * 间距统一交给 `styles/product.css` 的 `.message-bubble` 规则，这里不再写内联 margin，
 * 避免内联样式与主题样式互相打架（旧版 `p: { margin: 0 }` 就是段落糊成一坨的直接原因）。
 */
const components: Components = {
  p: ({ children }) => <p>{children}</p>,
  ul: ({ children }) => <ul>{children}</ul>,
  ol: ({ children }) => <ol>{children}</ol>,
  li: ({ children }) => <li>{children}</li>,
  h1: ({ children }) => <div className="md-heading">{children}</div>,
  h2: ({ children }) => <div className="md-heading">{children}</div>,
  h3: ({ children }) => <div className="md-heading">{children}</div>,
  h4: ({ children }) => <div className="md-heading">{children}</div>,
  blockquote: ({ children }) => <blockquote>{children}</blockquote>,
  // 正文片段里的围栏已被 splitBlocks 摘走，这里的 pre/code 只覆盖缩进代码块与行内代码。
  pre: ({ children }) => <>{children}</>,
  code: ({ className, children }) => {
    const lang = /language-([\w+#-]+)/.exec(className ?? '')?.[1];
    const raw = String(children).replace(/\n$/, '');
    if (lang || raw.includes('\n')) return <CodeBlock code={raw} lang={lang} />;
    return <code className="md-inline-code">{children}</code>;
  },
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer">{children}</a>
  ),
  // 本轮不做图片外链渲染：转成链接，避免出现破图图标
  img: ({ src, alt }) => (
    <a href={typeof src === 'string' ? src : undefined} target="_blank" rel="noreferrer">
      {alt || '图片'}
    </a>
  ),
  hr: () => <hr />,
};

export default function BuiltinMarkdown({ text }: { text: string }) {
  const blocks = useMemo(() => splitBlocks(text), [text]);
  return (
    <>
      {blocks.map((block, index) => {
        if (block.kind === 'code') {
          return <CodeBlock key={index} code={block.code} lang={block.lang} />;
        }
        if (block.kind === 'table') {
          return <TableBlock key={index} header={block.header} rows={block.rows} />;
        }
        return (
          <ReactMarkdown key={index} components={components}>
            {block.text}
          </ReactMarkdown>
        );
      })}
    </>
  );
}
