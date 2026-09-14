import { FileTextOutlined } from '@ant-design/icons';
import { Tag } from 'antd';
import type { Citation, ToolCard } from '@dwp/shared-schema';

const SOURCE_LABEL: Record<string, string> = {
  internal: '内部知识库',
  unknown: '未知来源',
};

/** 从工具卡片列表提取结构化引用（优先 citation 字段，兼容旧 tool_cards）。 */
export function extractCitations(cards: ToolCard[] | unknown[]): Citation[] {
  const result: Citation[] = [];
  for (const card of cards as Array<ToolCard & { name?: string; source?: string | null }>) {
    if (card?.citation) {
      result.push(card.citation);
    } else if (card?.name) {
      result.push({
        citation_id: `${card.plugin_id ?? 'tool'}-${result.length}`,
        document_title: '',
        knowledge_base_name: card.name,
        source: card.source ?? 'unknown',
        version: null,
        preview_available: false,
      });
    }
  }
  return result;
}

/** 答案下方的来源列表：文档名 / 知识库名 / 版本 / 查看依据（无原文时明确标注）。 */
export default function CitationList({ cards }: { cards: ToolCard[] | unknown[] }) {
  const citations = extractCitations(cards);
  if (citations.length === 0) return null;
  return (
    <div className="mt-2 space-y-1.5">
      <div className="text-xs font-medium text-ink-secondary">来源依据</div>
      {citations.map((cite) => (
        <div
          key={cite.citation_id}
          className="flex items-start gap-2 rounded-control bg-surface-subtle px-3 py-2"
        >
          <FileTextOutlined className="mt-0.5 text-ink-tertiary" />
          <div className="min-w-0 flex-1">
            <div className="text-sm text-ink">
              {cite.document_title || cite.knowledge_base_name}
            </div>
            <div className="text-xs text-ink-secondary">
              {cite.knowledge_base_name && cite.document_title ? `${cite.knowledge_base_name} · ` : ''}
              {SOURCE_LABEL[cite.source] ?? cite.source}
              {cite.version ? ` · ${cite.version}` : ''}
            </div>
          </div>
          {cite.preview_available ? (
            <Tag color="blue">查看依据</Tag>
          ) : (
            <Tag>仅提供检索依据</Tag>
          )}
        </div>
      ))}
    </div>
  );
}
