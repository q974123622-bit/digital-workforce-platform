import { Alert, Button, Card, Progress, Space, Tag, Typography } from 'antd';
import type { AgentEvaluationDimension, AgentTaskEvaluation } from '@dwp/shared-schema';

interface Props {
  evaluation: AgentTaskEvaluation | null;
  loading: boolean;
  error?: string;
  onRetry?: () => void;
}

function DimensionList({ items }: { items: AgentEvaluationDimension[] }) {
  return (
    <div className="at-evaluation-dimensions">
      {items.map((item) => (
        <div className="at-evaluation-dimension" key={item.key}>
          <span>{item.label}</span>
          <strong>{item.score}/{item.max_score}</strong>
          <Typography.Text type="secondary">{item.reason}</Typography.Text>
        </div>
      ))}
    </div>
  );
}

export function EvaluationPanel({ evaluation, loading, error, onRetry }: Props) {
  return (
    <Card
      title="旧版单任务协作评分"
      extra={<Tag>仅固定虚构任务</Tag>}
      className="at-evaluation-panel"
      data-testid="agentteams-evaluation-panel"
      data-layout="horizontal"
    >
      {loading && <Typography.Text type="secondary" aria-live="polite">正在评测协作过程和最终答案…</Typography.Text>}
      {error && (
        <Alert
          type="warning"
          showIcon
          message={error}
          action={onRetry ? <Button size="small" onClick={onRetry}>重新评测</Button> : undefined}
        />
      )}
      {!loading && !error && !evaluation && <Typography.Text type="secondary">任务完成后生成评测。</Typography.Text>}
      {evaluation && (
        <div className="at-evaluation-layout">
          <div className="at-evaluation-total">
            <div>
              <Typography.Text type="secondary">综合得分</Typography.Text>
              <Typography.Title level={3}>
                {evaluation.total_score === null ? '暂不可用' : `${evaluation.total_score}/${evaluation.total_max_score}`}
              </Typography.Title>
            </div>
            {evaluation.total_score !== null && (
              <Progress type="circle" size={62} percent={evaluation.total_score} format={(percent) => `${percent}分`} />
            )}
          </div>
          <div className="at-evaluation-groups">
            <section>
              <Typography.Text strong>规则评测 {evaluation.rule_score}/{evaluation.rule_max_score}</Typography.Text>
              <DimensionList items={evaluation.rule_dimensions} />
            </section>
            <section>
              <Space size={6} wrap>
                <Typography.Text strong>
                  大模型评测 {evaluation.model_score === null ? '暂不可用' : `${evaluation.model_score}/${evaluation.model_max_score}`}
                </Typography.Text>
                <Tag color={evaluation.model_status === 'completed' ? 'success' : 'warning'}>
                  {evaluation.model_status === 'completed' ? '已完成' : '不可用'}
                </Tag>
              </Space>
              <DimensionList items={evaluation.model_dimensions} />
            </section>
          </div>
          {(evaluation.rule_deductions.length > 0 || evaluation.model_deductions.length > 0) && (
            <section className="at-evaluation-deductions">
              <Typography.Text strong>扣分项</Typography.Text>
              <ul>
                {[...evaluation.rule_deductions, ...evaluation.model_deductions].map((item) => <li key={item}>{item}</li>)}
              </ul>
            </section>
          )}
          {evaluation.model_verdict && <Alert className="at-evaluation-verdict" type="info" showIcon message="评测结论" description={evaluation.model_verdict} />}
        </div>
      )}
    </Card>
  );
}
