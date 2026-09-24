import { DownOutlined, FileTextOutlined, UpOutlined } from '@ant-design/icons';
import { useState } from 'react';
import type { Citation, ToolCard } from '@dwp/shared-schema';

const SOURCE_LABEL: Record<string, string> = {
  internal: '内部知识库',
  rag: '知识库检索',
  demo: '演示数据',
  mock: '演示数据',
  unknown: '未知来源',
};

export function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? source;
}

/**
 * 执行事件兜底来源：工具卡片缺失时（如分身委派给岗位员工检索，审计主体是被委派方）
 * 仍然显示「本轮查了哪个库、谁查的、命中几条」。真源是执行事件，卡片只是增强信息。
 */
export interface KnowledgeSourceFallback {
  knowledgeBaseId: string;
  knowledgeBaseName?: string;
  hitCount?: number;
  agentEmployeeId?: string;
  agentEmployeeName?: string;
}

/** 合并后的一行来源（一个知识库一行）。 */
export interface KnowledgeSource {
  key: string;
  name: string;
  source: string;
  /** 非空表示由被委派的岗位AI员工检索。 */
  agentLabel?: string;
  hitCount?: number;
  documents: string[];
  version?: string | null;
}

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

/**
 * 合并「工具卡片」与「执行事件兜底」：同一知识库只保留一条。
 * 卡片信息更全（含命中文档名），优先；事件兜底保证「只要查过就有展示」。
 */
export function collectKnowledgeSources(
  cards: ToolCard[] | unknown[],
  fallback: KnowledgeSourceFallback[] = [],
): KnowledgeSource[] {
  const rows: KnowledgeSource[] = [];
  const seen = new Set<string>();
  for (const card of cards as Array<ToolCard & { name?: string; source?: string | null }>) {
    const citation = card?.citation ?? null;
    const key = card?.knowledge_base_id || citation?.knowledge_base_name || card?.name || '';
    if (!key || seen.has(key)) continue;
    seen.add(key);
    const documents = citation?.document_titles?.length
      ? citation.document_titles
      : citation?.document_title
        ? [citation.document_title]
        : [];
    rows.push({
      key,
      name: citation?.knowledge_base_name || card?.name || key,
      source: citation?.source ?? card?.source ?? 'unknown',
      agentLabel: citation?.agent_employee_name || citation?.agent_employee_id || undefined,
      hitCount: citation?.hit_count ?? card?.hit_count ?? undefined,
      documents,
      version: citation?.version ?? null,
    });
  }
  for (const item of fallback) {
    if (!item.knowledgeBaseId || seen.has(item.knowledgeBaseId)) continue;
    seen.add(item.knowledgeBaseId);
    rows.push({
      key: item.knowledgeBaseId,
      name: item.knowledgeBaseName || item.knowledgeBaseId,
      source: 'internal',
      agentLabel: item.agentEmployeeName || item.agentEmployeeId || undefined,
      hitCount: item.hitCount,
      documents: [],
      version: null,
    });
  }
  return rows;
}

/**
 * 答案下方的来源行（L2）：默认可见的一行 pill，展示「查了哪个库 / 谁查的 / 命中几条」，
 * 点「查看依据」展开命中文档标题列表（审计只落元数据，不展示命中原文）。
 */
export default function CitationList({
  cards,
  fallback = [],
}: {
  cards: ToolCard[] | unknown[];
  fallback?: KnowledgeSourceFallback[];
}) {
  // 展开状态放组件内本地 state：portal 壳里不用 antd 浮层（定位不可靠）。
  const [openKeys, setOpenKeys] = useState<string[]>([]);
  const sources = collectKnowledgeSources(cards, fallback);
  if (sources.length === 0) return null;
  const toggle = (key: string) =>
    setOpenKeys((current) => (current.includes(key) ? current.filter((item) => item !== key) : [...current, key]));
  const opened = sources.filter((row) => openKeys.includes(row.key));
  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs">
        <span className="font-medium text-ink-secondary">来源</span>
        {sources.map((row) => (
          <span
            key={row.key}
            className="inline-flex max-w-full items-center gap-1.5 rounded-control border border-line bg-surface-subtle px-2 py-1"
          >
            <FileTextOutlined className="shrink-0 text-brand" />
            <span className="truncate text-ink">{row.name}</span>
            {row.agentLabel && <span className="shrink-0 text-ink-tertiary">由 {row.agentLabel} 检索</span>}
            {row.hitCount ? <span className="shrink-0 text-ink-tertiary">命中 {row.hitCount} 条</span> : null}
            <button
              type="button"
              className="shrink-0 cursor-pointer border-0 bg-transparent p-0 text-brand"
              onClick={() => toggle(row.key)}
            >
              {openKeys.includes(row.key)
                ? <span className="inline-flex items-center gap-0.5">收起依据 <UpOutlined className="text-[10px]" /></span>
                : <span className="inline-flex items-center gap-0.5">查看依据 <DownOutlined className="text-[10px]" /></span>}
            </button>
          </span>
        ))}
      </div>
      {opened.map((row) => (
        <div key={`${row.key}-docs`} className="ml-1 mt-1.5 space-y-0.5 border-l-2 border-line pl-3">
          {row.documents.length > 0 ? (
            row.documents.map((doc, index) => (
              <div key={`${row.key}-doc-${index}`} className="text-xs text-ink-secondary">· {doc}</div>
            ))
          ) : (
            <div className="text-xs text-ink-tertiary">未记录命中文档名，仅提供检索依据</div>
          )}
          <div className="text-[11px] text-ink-tertiary">
            {sourceLabel(row.source)}{row.version ? ` · ${row.version}` : ''} · 为保护知识内容安全，不展示命中原文
          </div>
        </div>
      ))}
    </div>
  );
}
