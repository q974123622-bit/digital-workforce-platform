import { FileSearchOutlined } from '@ant-design/icons';
import { Alert, Drawer, Empty, Tag, Typography } from 'antd';
import type { Citation, ToolCard } from '@dwp/shared-schema';
import { extractCitations } from './CitationList';

const { Text } = Typography;

const SOURCE_LABEL: Record<string, string> = {
  internal: '内部知识库',
  demo: '演示知识库',
  mock: '演示知识库',
  rag: '本地 RAG',
  volcengine_mcp: 'Volcengine MCP',
  unknown: '未知来源',
};

/**
 * 引用预览侧栏：从来源卡片打开，展示文档名、知识库、来源类型与版本。
 * 因审计不落盘命中正文，一律明确提示「仅提供检索依据」，不展示原文。
 */
export default function CitationPreview({
  cards,
  open,
  onClose,
}: {
  cards: ToolCard[] | unknown[];
  open: boolean;
  onClose: () => void;
}) {
  const citations: Citation[] = open ? extractCitations(cards) : [];
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
      {citations.length === 0 ? (
        <Empty description="暂无来源记录" />
      ) : (
        <div className="space-y-3">
          {citations.map((cite) => (
            <div key={cite.citation_id} className="rounded-container border border-line p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-medium text-ink">
                    {cite.document_title || cite.knowledge_base_name}
                  </div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {cite.knowledge_base_name && cite.document_title
                      ? `${cite.knowledge_base_name} · `
                      : ''}
                    {SOURCE_LABEL[cite.source] ?? cite.source}
                  </Text>
                </div>
                <Tag color="blue">{SOURCE_LABEL[cite.source] ?? cite.source}</Tag>
              </div>
              {cite.version && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  版本：{cite.version}
                </Text>
              )}
            </div>
          ))}
        </div>
      )}
    </Drawer>
  );
}
