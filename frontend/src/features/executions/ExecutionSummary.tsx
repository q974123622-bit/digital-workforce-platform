import { CheckCircleOutlined, DownOutlined, LoadingOutlined, RobotOutlined, StopOutlined, UpOutlined } from '@ant-design/icons';
import { Button, Tag } from 'antd';
import type { AgentExecution } from '@dwp/shared-schema';
import { useTicker } from '../../hooks/useTicker';
import { formatDuration } from '../../utils/duration';

const ACTIVE = ['queued', 'running', 'streaming', 'waiting_approval'];

/**
 * 执行记录折叠摘要：默认折叠为「已查阅 N 个知识库 · X」。
 * 失败时默认展开并保留简短摘要与重试入口（retryable 为真才显示重试）；
 * 已停止（cancelled）同样给出重试入口 —— 停止后原消息不会再被自动执行。
 *
 * 耗时一律用服务端时间戳（见 utils/serverTime）：运行中用当前时间实时增长，
 * 结束后用服务端 `completed_at`；两者都拿不到时不臆造时间，避免出现
 * 「28842 秒」这类把时区差算进耗时的错误数字。
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
    steps: { title?: string; status?: string; knowledgeBaseId?: string }[];
  };
  employeeName: string;
  expanded: boolean;
  onToggle: () => void;
  onRetry: () => void;
  knowledgeBaseCount?: number;
}) {
  const running = ACTIVE.includes(run.status);
  const failed = run.status === 'failed';
  const cancelled = run.status === 'cancelled';
  const now = useTicker(running, 500);
  const endAt = run.finishedAt ?? (running ? now : undefined);
  const elapsed = endAt === undefined ? undefined : Math.max(0, endAt - run.startedAt);
  const kbCount = knowledgeBaseCount || new Set(run.steps.map((s) => s.knowledgeBaseId).filter(Boolean)).size;
  // 折叠状态下用最后一条步骤标题当副标题，实时体现「现在在做什么」
  //（如「正在检索 IT 服务知识库」），不必展开轨迹。
  const currentTitle = run.steps.length ? run.steps[run.steps.length - 1].title?.trim() : undefined;
  const frame = failed
    ? 'border-danger bg-danger-soft/40'
    : running
      ? 'border-brand-soft bg-brand-soft/40'
      : 'border-line bg-surface-subtle';
  const badge = failed
    ? 'bg-danger text-white'
    : cancelled
      ? 'bg-line-light text-ink-secondary'
      : 'bg-brand-soft text-brand';

  return (
    <div className={`rounded-container border px-3 py-2.5 ${frame}`}>
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full cursor-pointer items-center gap-2 border-0 bg-transparent p-0 text-left"
      >
        <span className={`grid h-7 w-7 place-items-center rounded-control ${badge}`}>
          {failed ? <RobotOutlined /> : running ? <LoadingOutlined /> : cancelled ? <StopOutlined /> : <CheckCircleOutlined />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-ink">
            {employeeName || 'AI员工'}
          </span>
          <span className="block truncate text-xs text-ink-secondary">
            {running
              ? `${currentTitle || '执行中'} · ${formatDuration(elapsed ?? 0)}`
              : failed
                ? '执行未完成，可查看轨迹'
                : cancelled
                  ? `已停止 · ${formatDuration(elapsed ?? 0)}`
                  : `已查阅 ${kbCount} 个知识库 · ${formatDuration(elapsed ?? 0)}`}
          </span>
        </span>
        <Tag color={failed ? 'error' : running ? 'processing' : cancelled ? 'default' : 'success'} className="!m-0">
          {running ? '执行中' : failed ? '失败' : cancelled ? '已停止' : '已完成'}
        </Tag>
        {expanded ? <UpOutlined className="text-ink-tertiary" /> : <DownOutlined className="text-ink-tertiary" />}
      </button>
      {(failed || cancelled) && !expanded && run.retryable && (
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className={`truncate text-xs ${failed ? 'text-danger' : 'text-ink-secondary'}`}>
            {failed ? (run.error ?? '执行失败') : '已停止执行，可重新执行'}
          </span>
          <Button size="small" onClick={onRetry}>重新执行</Button>
        </div>
      )}
    </div>
  );
}
