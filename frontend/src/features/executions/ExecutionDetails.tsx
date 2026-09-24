import { CheckCircleOutlined, LoadingOutlined, StopOutlined } from '@ant-design/icons';
import { Alert, Button, Space, Tag } from 'antd';
import type { AgentExecution } from '@dwp/shared-schema';
import { useTicker } from '../../hooks/useTicker';
import { formatDuration } from '../../utils/duration';

export interface ExecutionStep {
  id: string;
  title: string;
  detail: string;
  status: string;
  /** 该步骤的开始时间（服务端时间戳解析后的 epoch ms）。 */
  startedAt: number;
  knowledgeBaseId?: string;
  /** 知识库中文名（事件 payload 带下来），优先于原始 id 展示。 */
  knowledgeBaseName?: string;
  /** 该步骤的执行人（委派检索时即被委派方）。 */
  actorEmployeeId?: string;
  targetAgentId?: string;
  hitCount?: number;
}

const ACTIVE = ['queued', 'running', 'streaming', 'waiting_approval'];

/** 展开后的执行轨迹：业务语言状态 + 知识库命中 + 委派，失败时提供重试。 */
export default function ExecutionDetails({
  run,
  steps,
  employeeName,
  onRetry,
}: {
  run: { status: AgentExecution['status']; startedAt: number; finishedAt?: number; error?: string; retryable?: boolean };
  steps: ExecutionStep[];
  employeeName: (id: string) => string;
  onRetry: () => void;
}) {
  const running = ACTIVE.includes(run.status);
  const failed = run.status === 'failed';
  // 运行中按固定间隔重渲染，让最后一步的耗时实时增长，而不是等下一个事件才跳一次。
  const now = useTicker(running, 200);

  /**
   * 每步耗时 = 下一步开始时间 − 本步开始时间（即这一步实际花了多久）。
   * 最后一步用执行完成时间；若仍在运行则用当前时间，因此会实时增长。
   */
  const durationOf = (index: number): number => {
    const step = steps[index];
    const next = steps[index + 1];
    const end = next ? next.startedAt : (run.finishedAt ?? (running ? now : step.startedAt));
    return end - step.startedAt;
  };

  return (
    <div className="mt-2 space-y-2">
      <div className="text-xs text-ink-tertiary">
        展示可审计的操作轨迹，不包含模型隐藏推理或知识原文
      </div>
      <div className="space-y-1">
        {steps.map((step, index) => (
          <div key={step.id} className="flex gap-2.5">
            <span className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10px] ${step.status === 'failed' ? 'bg-danger text-white' : 'bg-brand-soft text-brand'}`}>
              {step.status === 'failed' ? <StopOutlined /> : index === steps.length - 1 && running ? <LoadingOutlined /> : <CheckCircleOutlined />}
            </span>
            <div className="min-w-0 flex-1 pb-1">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm text-ink">{step.title}</span>
                <span className="shrink-0 text-xs text-ink-tertiary">{formatDuration(durationOf(index))}</span>
              </div>
              {step.detail && <div className="text-xs text-ink-secondary">{step.detail}</div>}
              <Space size={4} wrap className="mt-0.5">
                {step.knowledgeBaseId && <Tag className="!m-0">{step.knowledgeBaseName ?? step.knowledgeBaseId}</Tag>}
                {step.hitCount != null && <Tag color="blue" className="!m-0">命中 {step.hitCount} 条</Tag>}
                {step.targetAgentId && <Tag color="geekblue" className="!m-0">委派至 {employeeName(step.targetAgentId)}</Tag>}
              </Space>
            </div>
          </div>
        ))}
      </div>
      {failed && (
        <Alert
          type="error"
          showIcon
          message={run.error ?? 'AI员工执行失败，可稍后重试'}
          action={run.retryable ? <Button size="small" onClick={onRetry}>重新执行</Button> : undefined}
        />
      )}
    </div>
  );
}
