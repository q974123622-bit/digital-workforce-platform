import { CheckCircleOutlined, DownOutlined, LoadingOutlined, RobotOutlined, UpOutlined } from '@ant-design/icons';
import { Button, Tag } from 'antd';
import type { AgentExecution } from '@dwp/shared-schema';

const ACTIVE = ['queued', 'running', 'streaming', 'waiting_approval'];

/**
 * 执行记录折叠摘要：默认折叠为「已查阅 N 个知识库 · X 秒」。
 * 失败时默认展开并保留简短摘要与重试入口（retryable 为真才显示重试）。
 */
export default function ExecutionSummary({
  run,
  employeeName,
  expanded,
  onToggle,
  onRetry,
  knowledgeBaseCount = 0,
}: {
  run: {
    id: string;
    status: AgentExecution['status'];
    startedAt: number;
    finishedAt?: number;
    error?: string;
    retryable?: boolean;
    steps: { knowledgeBaseId?: string }[];
  };
  employeeName: string;
  expanded: boolean;
  onToggle: () => void;
  onRetry: () => void;
  knowledgeBaseCount?: number;
}) {
  const running = ACTIVE.includes(run.status);
  const failed = run.status === 'failed';
  const elapsed = Math.max(0, Math.round(((run.finishedAt ?? Date.now()) - run.startedAt) / 1000));
  const kbCount = knowledgeBaseCount || new Set(run.steps.map((s) => s.knowledgeBaseId).filter(Boolean)).size;

  return (
    <div className={`rounded-container border px-3 py-2.5 ${failed ? 'border-danger bg-danger-soft/40' : running ? 'border-brand-soft bg-brand-soft/40' : 'border-line bg-surface-subtle'}`}>
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full cursor-pointer items-center gap-2 border-0 bg-transparent p-0 text-left"
      >
        <span className={`grid h-7 w-7 place-items-center rounded-control ${failed ? 'bg-danger text-white' : 'bg-brand-soft text-brand'}`}>
          {failed ? <RobotOutlined /> : running ? <LoadingOutlined /> : <CheckCircleOutlined />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-ink">
            {employeeName || '数字员工'}
          </span>
          <span className="block text-xs text-ink-secondary">
            {running
              ? '执行中…'
              : failed
                ? '执行未完成，可查看轨迹'
                : `已查阅 ${kbCount} 个知识库 · ${elapsed} 秒`}
          </span>
        </span>
        <Tag color={failed ? 'error' : running ? 'processing' : 'success'} className="!m-0">
          {running ? '执行中' : failed ? '失败' : '已完成'}
        </Tag>
        {expanded ? <UpOutlined className="text-ink-tertiary" /> : <DownOutlined className="text-ink-tertiary" />}
      </button>
      {failed && !expanded && run.retryable && (
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="truncate text-xs text-danger">{run.error ?? '执行失败'}</span>
          <Button size="small" onClick={onRetry}>重新执行</Button>
        </div>
      )}
    </div>
  );
}
