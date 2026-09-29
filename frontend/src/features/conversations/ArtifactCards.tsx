import {
  DownloadOutlined,
  EyeOutlined,
  FileMarkdownOutlined,
  FileTextOutlined,
  Html5Outlined,
  TableOutlined,
} from '@ant-design/icons';
import { Button } from 'antd';
import type { ExecutionArtifact } from '@dwp/shared-schema';
import { artifactContentUrl, artifactKindOf, formatArtifactSize } from './artifacts';
import type { ArtifactKind } from './artifacts';

const KIND_LABEL: Record<ArtifactKind, string> = {
  markdown: '核对表',
  html: '网页版',
  csv: '表格',
  text: '文本',
};

function iconOf(kind: ArtifactKind) {
  if (kind === 'markdown') return <FileMarkdownOutlined />;
  if (kind === 'html') return <Html5Outlined />;
  if (kind === 'csv') return <TableOutlined />;
  return <FileTextOutlined />;
}

/**
 * AI员工本轮产出的文件卡片：预览 + 下载。
 *
 * 渲染在答复气泡下方（`renderMessageExtra`）。没有它，用户看到的就只是答复里那句
 * 「完整核对表已保存至 /workspace/output/核对表.md」—— 一个容器内的路径，
 * 既看不到也拿不到，而那份文件才是这一轮真正要交付的东西。
 */
export default function ArtifactCards({
  conversationId,
  artifacts,
  onPreview,
}: {
  conversationId: string;
  artifacts: ExecutionArtifact[];
  onPreview: (artifact: ExecutionArtifact) => void;
}) {
  if (artifacts.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {artifacts.map((item) => {
        const kind = artifactKindOf(item);
        return (
          <div
            key={item.artifact_id}
            className="flex min-w-0 max-w-[380px] items-center gap-2 rounded-control border border-line bg-surface px-3 py-2"
          >
            <span className="shrink-0 text-base text-brand">{iconOf(kind)}</span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium text-ink" title={item.name}>
                {item.filename}
              </span>
              <span className="block text-[10px] text-ink-tertiary">
                {KIND_LABEL[kind]} · {formatArtifactSize(item.size)}
              </span>
            </span>
            <Button
              size="small"
              icon={<EyeOutlined />}
              aria-label={`预览 ${item.filename}`}
              onClick={() => onPreview(item)}
            >
              预览
            </Button>
            <Button
              size="small"
              type="text"
              icon={<DownloadOutlined />}
              aria-label={`下载 ${item.filename}`}
              href={artifactContentUrl(conversationId, item.artifact_id, true)}
            >
              下载
            </Button>
          </div>
        );
      })}
    </div>
  );
}
