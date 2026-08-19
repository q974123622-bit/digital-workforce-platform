import ReactMarkdown from 'react-markdown';

/** 轻量 Markdown 渲染：让模型回复中的 **加粗**、- 列表等以样式呈现，而不是裸符号。 */
export default function MarkdownText({ text }: { text: string }) {
  return (
    <ReactMarkdown
      components={{
        p: ({ children }) => <p style={{ margin: 0 }}>{children}</p>,
        ul: ({ children }) => <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{children}</ul>,
        ol: ({ children }) => <ol style={{ margin: '4px 0 0', paddingLeft: 20 }}>{children}</ol>,
        li: ({ children }) => <li style={{ margin: '2px 0' }}>{children}</li>,
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
