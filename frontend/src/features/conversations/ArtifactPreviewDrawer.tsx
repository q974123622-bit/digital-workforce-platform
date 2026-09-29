import { DownloadOutlined } from '@ant-design/icons';
import { Alert, Button, Drawer, Empty, Spin } from 'antd';
import { useEffect, useState } from 'react';
import type { ExecutionArtifact } from '@dwp/shared-schema';
import MarkdownText from '../../components/MarkdownText';
import { artifactContentUrl, artifactKindOf, fetchArtifactText, formatArtifactSize } from './artifacts';

/**
 * 产物预览抽屉：核对表（Markdown）用站内渲染器，HTML 用沙箱 iframe，csv/txt 用等宽文本。
 *
 * 三种形态都先 `fetch` 文本（带 cookie 鉴权），HTML 走 `srcDoc`：
 * `sandbox=""` 让报告里的任何脚本都不执行 —— 报告内容来自材料，不能假设它干净。
 */
export default function ArtifactPreviewDrawer({
  conversationId,
  artifact,
  onClose,
}: {
  conversationId: string;
  artifact: ExecutionArtifact | null;
  onClose: () => void;
}) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const kind = artifactKindOf(artifact);
  const artifactId = artifact?.artifact_id ?? '';

  useEffect(() => {
    if (!artifact) {
      setText('');
      setError('');
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError('');
    setText('');
    fetchArtifactText(conversationId, artifact.artifact_id)
      .then((value) => { if (!cancelled) setText(value); })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '读取产物失败');
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [artifact, artifactId, conversationId]);

  return (
    <Drawer
      title={artifact ? artifact.filename : '产物预览'}
      open={Boolean(artifact)}
      onClose={onClose}
      width={1000}
      styles={{ body: { padding: 0, background: 'var(--dwp-page)' } }}
      extra={artifact && (
        <Button
          icon={<DownloadOutlined />}
          href={artifactContentUrl(conversationId, artifact.artifact_id, true)}
        >
          下载
        </Button>
      )}
    >
      {artifact && (
        <div className="px-4 py-3 text-xs text-ink-tertiary">
          {artifact.name} · {formatArtifactSize(artifact.size)}
        </div>
      )}
      {loading && (
        <div className="flex justify-center py-16"><Spin /></div>
      )}
      {!loading && error && (
        <div className="p-4">
          <Alert type="error" showIcon message="产物读取失败" description={error} />
        </div>
      )}
      {!loading && !error && artifact && !text && (
        <Empty description="产物内容为空" className="py-16" />
      )}
      {!loading && !error && artifact && text && (
        kind === 'html' ? (
          <iframe
            title={artifact.filename}
            // sandbox 留空：报告来自材料，不能假设它干净；脚本一律不执行
            sandbox=""
            srcDoc={text}
            className="h-[calc(100vh-140px)] w-full border-0 bg-white"
          />
        ) : kind === 'markdown' ? (
          <div className="mx-auto max-w-[900px] px-5 pb-10">
            <MarkdownText text={text} />
          </div>
        ) : (
          <pre className="mx-4 mb-6 max-h-[calc(100vh-200px)] overflow-auto rounded-control border border-line bg-surface p-4 text-xs leading-relaxed text-ink">
            {text}
          </pre>
        )
      )}
    </Drawer>
  );
}
