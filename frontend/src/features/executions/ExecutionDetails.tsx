import { CheckCircleOutlined, LoadingOutlined, StopOutlined } from '@ant-design/icons';
import { Alert, Button, Space, Tag } from 'antd';
import type { AgentExecution } from '@dwp/shared-schema';

export interface ExecutionStep {
  id: string;
  title: string;
  detail: string;
  status: string;
  durationMs: number;
  knowledgeBaseId?: string;
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
  run: { status: AgentExecution['status']; error?: string; retryable?: boolean };
  steps: ExecutionStep[];
  employeeName: (id: string) => string;
  onRetry: () => void;
}) {
  const running = ACTIVE.includes(run.status);
  const failed = run.status === 'failed';
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
                <span className="shrink-0 text-xs text-ink-tertiary">{(step.durationMs / 1000).toFixed(1)}s</span>
              </div>
              {step.detail && <div className="text-xs text-ink-secondary">{step.detail}</div>}
              <Space size={4} wrap className="mt-0.5">
                {step.knowledgeBaseId && <Tag className="!m-0">{step.knowledgeBaseId}</Tag>}
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
          message={run.error ?? '数字员工执行失败，可稍后重试'}
          action={run.retryable ? <Button size="small" onClick={onRetry}>重新执行</Button> : undefined}
        />
      )}
    </div>
  );
}
