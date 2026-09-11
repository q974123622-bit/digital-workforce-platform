import ReactMarkdown from 'react-markdown';

/**
 * 压缩多余换行与空行，让回复更紧凑。
 * maxNewlines 控制连续换行最多保留几个（1 = 全部折成单换行；2 = 保留一个空行用于段落分隔）。
 */
export function compactLines(text: string, maxNewlines = 1): string {
  return text
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(new RegExp(`\\n{${maxNewlines + 1},}`, 'g'), '\n'.repeat(maxNewlines))
    .replace(/^\n+|\n+$/g, '');
}

/** 轻量 Markdown 渲染：让模型回复中的 **加粗**、- 列表等以样式呈现，而不是裸符号。 */
export default function MarkdownText({ text }: { text: string }) {
  const compact = compactLines(text, 2);
  return (
    <ReactMarkdown
      components={{
        p: ({ children }) => <p style={{ margin: 0 }}>{children}</p>,
        ul: ({ children }) => <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{children}</ul>,
        ol: ({ children }) => <ol style={{ margin: '4px 0 0', paddingLeft: 20 }}>{children}</ol>,
        li: ({ children }) => <li style={{ margin: '2px 0' }}>{children}</li>,
        h1: ({ children }) => <div style={{ margin: '4px 0 2px', fontSize: 14, fontWeight: 700 }}>{children}</div>,
        h2: ({ children }) => <div style={{ margin: '4px 0 2px', fontSize: 14, fontWeight: 700 }}>{children}</div>,
        h3: ({ children }) => <div style={{ margin: '4px 0 2px', fontSize: 14, fontWeight: 700 }}>{children}</div>,
      }}
    >
      {compact}
    </ReactMarkdown>
  );
}
