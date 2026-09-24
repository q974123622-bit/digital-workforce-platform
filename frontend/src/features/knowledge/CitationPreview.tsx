import { FileSearchOutlined } from '@ant-design/icons';
import { Alert, Drawer, Empty, Tag, Typography } from 'antd';
import type { ToolCard } from '@dwp/shared-schema';
import { collectKnowledgeSources, sourceLabel } from './CitationList';
import type { KnowledgeSourceFallback } from './CitationList';

const { Text } = Typography;

/**
 * 引用预览侧栏：从来源行打开，展示知识库、检索人、命中文档与来源类型。
 * 因审计不落盘命中正文，一律明确提示「仅提供检索依据」，不展示原文。
 */
export default function CitationPreview({
  cards,
  fallback = [],
  open,
  onClose,
}: {
  cards: ToolCard[] | unknown[];
  fallback?: KnowledgeSourceFallback[];
  open: boolean;
  onClose: () => void;
}) {
  const sources = open ? collectKnowledgeSources(cards, fallback) : [];
  return (
    <Drawer
      title={
        <span className="flex items-center gap-2">
          <FileSearchOutlined className="text-brand" /> 来源与依据
        </span>
      }
      open={open}
      onClose={onClose}
      width={400}
    >
      <Alert
        type="info"
        showIcon
        className="mb-4"
        message="仅提供检索依据"
        description="为保护知识内容安全，此处只展示来源元数据，不展示命中原文。"
      />
      {sources.length === 0 ? (
        <Empty description="暂无来源记录" />
      ) : (
        <div className="space-y-3">
          {sources.map((row) => (
            <div key={row.key} className="rounded-container border border-line p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-medium text-ink">{row.name}</div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {sourceLabel(row.source)}
                    {row.agentLabel ? ` · 由 ${row.agentLabel} 检索` : ''}
                    {row.hitCount ? ` · 命中 ${row.hitCount} 条` : ''}
                  </Text>
                </div>
                <Tag color="blue">{sourceLabel(row.source)}</Tag>
              </div>
              {row.documents.length > 0 && (
                <div className="mt-2 space-y-0.5">
                  {row.documents.map((doc, index) => (
                    <div key={`${row.key}-${index}`} className="text-xs text-ink-secondary">· {doc}</div>
                  ))}
                </div>
              )}
              {row.version && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  版本：{row.version}
                </Text>
              )}
            </div>
          ))}
        </div>
      )}
    </Drawer>
  );
}
