import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Progress, Space, Tag, Typography } from 'antd';
import type {
  DatabaseBenchmarkExperiment,
  DatabaseBenchmarkModeSummary,
  DatabaseBenchmarkRun,
} from '@dwp/shared-schema';
import { api } from '../../api/client';


const SCENARIOS = [
  // 旧五场景保留在后端供历史实验读取，不再加入新实验：
  // bulk_writes, lock_contention, vacuum_pressure, index_anomaly, large_query
  ['compound_lock_index', '锁等待 + 索引异常'],
  ['conflicting_signals_followup', '冲突信号二次复核'],
  ['budgeted_incident_triage', '受限预算复合分诊'],
] as const;

const TERMINAL = new Set(['completed', 'failed']);

function errorText(error: unknown) {
  return error instanceof Error ? error.message : '请求失败，请稍后重试';
}

function percent(value: number | null | undefined) {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`;
}

function seconds(value: number | null | undefined) {
  return value == null ? '—' : `${(value / 1000).toFixed(2)}s`;
}

function number(value: number | null | undefined) {
  return value == null ? '—' : value.toFixed(1);
}

interface MetricRow {
  label: string;
  value: (summary: DatabaseBenchmarkModeSummary) => string;
}

const METRICS: MetricRow[] = [
  { label: '主答案正确率', value: (row) => percent(row.primary_accuracy) },
  { label: 'Top-2 命中率', value: (row) => percent(row.top2_hit) },
  { label: '证据覆盖率', value: (row) => percent(row.evidence_coverage) },
  { label: '伴随原因命中率', value: (row) => percent(row.secondary_cause_hit) },
  { label: '冲突解决率', value: (row) => percent(row.conflict_resolution_rate) },
  { label: '有效返工率', value: (row) => percent(row.effective_rework_rate) },
  { label: '预算内成功率', value: (row) => percent(row.budget_success_rate) },
  { label: 'P50 耗时', value: (row) => seconds(row.p50_end_to_end_ms) },
  { label: '正确答案 P50', value: (row) => seconds(row.p50_valid_answer_ms) },
  { label: 'P95 耗时', value: (row) => seconds(row.p95_end_to_end_ms) },
  { label: '平均模型步骤', value: (row) => number(row.average_model_steps) },
  { label: '平均关键路径步骤', value: (row) => number(row.average_critical_path_steps) },
  { label: '平均 Tool 调用', value: (row) => number(row.average_tool_calls) },
  { label: '平均重复 Tool 调用', value: (row) => number(row.average_duplicate_tool_calls) },
  { label: '平均返工次数', value: (row) => number(row.average_rework_count) },
  { label: '平均并行加速比', value: (row) => row.average_parallel_speedup == null ? '—' : `${row.average_parallel_speedup.toFixed(2)}×` },
  { label: '成功率', value: (row) => percent(row.success_rate) },
];

export function DatabaseBenchmarkPanel({
  actorNo,
  onSelectTask,
}: {
  actorNo: string;
  onSelectTask?: (taskId: string) => void;
}) {
  const [experiment, setExperiment] = useState<DatabaseBenchmarkExperiment | null>(null);
  const [runs, setRuns] = useState<DatabaseBenchmarkRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    let cancelled = false;
    setExperiment(null);
    setRuns([]);
    setError(undefined);
    setLoading(true);
    void api.getLatestDatabaseBenchmark(actorNo)
      .then((latest) => {
        if (!cancelled) setExperiment(latest);
      })
      .catch((latestError) => {
        if (!cancelled) setError(`最近评测读取失败：${errorText(latestError)}`);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [actorNo]);

  useEffect(() => {
    if (!experiment || TERMINAL.has(experiment.status)) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const next = await api.getDatabaseBenchmark(actorNo, experiment.id);
        if (!cancelled) setExperiment(next);
      } catch (pollError) {
        if (!cancelled) setError(`评测进度读取失败：${errorText(pollError)}`);
      }
    }, 2000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [actorNo, experiment]);

  useEffect(() => {
    if (!experiment) return;
    let cancelled = false;
    void api.listDatabaseBenchmarkRuns(actorNo, experiment.id)
      .then((nextRuns) => {
        if (!cancelled) setRuns(nextRuns);
      })
      .catch((runsError) => {
        if (!cancelled) setError(`运行明细读取失败：${errorText(runsError)}`);
      });
    return () => {
      cancelled = true;
    };
  }, [actorNo, experiment]);

  const completed = experiment?.completed_runs ?? 0;
  const total = experiment?.total_runs ?? 45;
  const failedOrInvalid = (experiment?.failed_runs ?? 0) + (experiment?.invalid_runs ?? 0);
  const progress = total > 0 ? Math.round(((completed + failedOrInvalid) / total) * 100) : 0;
  const experimentActive = experiment?.status === 'pending' || experiment?.status === 'running';
  const comparison = useMemo(() => {
    const summary = experiment?.summary;
    return summary?.agentteams_full && summary.agentteams_serial && summary.agentteams_uncoordinated
      ? {
          full: summary.agentteams_full,
          serial: summary.agentteams_serial,
          uncoordinated: summary.agentteams_uncoordinated,
        }
      : null;
  }, [experiment]);

  const start = async () => {
    if (loading || experimentActive) return;
    setLoading(true);
    setError(undefined);
    try {
      setExperiment(await api.createDatabaseBenchmark(actorNo, {
        scenario_ids: SCENARIOS.map(([id]) => id),
        repeats: 5,
        max_model_steps: 20,
        max_tool_calls: 10,
        timeout_ms: 30_000,
      }));
    } catch (startError) {
      setError(`启动评测失败：${errorText(startError)}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card
      title="数据库诊断协作机制消融评测"
      className="at-card at-database-benchmark"
      data-testid="database-benchmark-panel"
      extra={<Tag color="purple">独立于 100 分任务评测</Tag>}
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Text>基于 MultiAgentBench Database 场景改编的本地对照评测</Typography.Text>
        <Space size={[6, 6]} wrap>
          {SCENARIOS.map(([id, label]) => <Tag key={id}>{label}</Tag>)}
        </Space>
        <Space size={[10, 10]} wrap>
          <Typography.Text type="secondary">3 个复杂场景 × 5 次 × 3 种模式 = 45 runs</Typography.Text>
          <Button
            type="primary"
            onClick={() => void start()}
            loading={loading}
            disabled={experimentActive}
            aria-label={experimentActive ? '评测进行中' : '开始对照评测'}
          >
            {experimentActive ? '评测进行中' : '开始对照评测'}
          </Button>
        </Space>
        {error && <Alert type="error" showIcon message={error} />}
        {experiment && (
          <>
            <Progress percent={progress} status={experiment.status === 'failed' ? 'exception' : 'active'} />
            <Space size={[8, 8]} wrap>
              <Tag color="processing">已完成 {completed} / {total}</Tag>
              <Tag color={failedOrInvalid ? 'warning' : 'success'}>无效/失败 {failedOrInvalid}</Tag>
              <Tag>公平性指纹 {experiment.fairness_fingerprint.slice(0, 12)}</Tag>
            </Space>
            {experiment.summary.failure_code && (
              <Alert type="warning" showIcon message={`运行环境不可用：${experiment.summary.failure_code}`} />
            )}
          </>
        )}
        {comparison && (
          <div>
            {experiment?.summary.attribution && (
              <Space direction="vertical" size={4} className="at-benchmark-attribution">
                <Typography.Text strong>协作收益归因</Typography.Text>
                <Typography.Text>
                  并行执行贡献：正确答案耗时降低 {percent(experiment.summary.attribution.parallel_time_reduction)}
                </Typography.Text>
                <Typography.Text>
                  Manager 协调贡献：根因准确率提高 {percent(experiment.summary.attribution.coordination_accuracy_gain)}
                </Typography.Text>
                <Typography.Text>
                  协调证据贡献：证据覆盖率提高 {percent(experiment.summary.attribution.coordination_evidence_gain)}
                </Typography.Text>
                <Typography.Text>
                  完整团队内部并行加速：{experiment.summary.attribution.full_team_parallel_speedup == null
                    ? '—'
                    : `${experiment.summary.attribution.full_team_parallel_speedup.toFixed(2)}×`}
                </Typography.Text>
              </Space>
            )}
            <div className="at-benchmark-table-wrap">
              <table className="at-benchmark-table">
              <thead>
                <tr><th>指标</th><th>完整 AgentTeam</th><th>串行 AgentTeam</th><th>无协调 AgentTeam</th></tr>
              </thead>
              <tbody>
                {METRICS.map((metric) => (
                  <tr key={metric.label}>
                    <td>{metric.label}</td>
                    <td>{metric.value(comparison.full)}</td>
                    <td>{metric.value(comparison.serial)}</td>
                    <td>{metric.value(comparison.uncoordinated)}</td>
                  </tr>
                ))}
              </tbody>
              </table>
            </div>
          </div>
        )}
        {runs.length > 0 && (
          <div>
            <Typography.Title level={5}>运行明细</Typography.Title>
            <div className="at-benchmark-table-wrap">
              <table className="at-benchmark-table at-benchmark-runs">
                <thead>
                  <tr>
                    <th>场景</th><th>轮次</th><th>模式</th><th>状态</th><th>根因</th><th>耗时</th><th>证据/任务树</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={run.id}>
                      <td>{run.scenario_id}</td>
                      <td>{run.repeat_index}</td>
                      <td>{run.mode === 'agentteams_full'
                        ? '完整 AgentTeam'
                        : run.mode === 'agentteams_serial'
                          ? '串行 AgentTeam'
                          : run.mode === 'agentteams_uncoordinated'
                            ? '无协调 AgentTeam'
                            : run.mode === 'agentteams'
                              ? 'AgentTeams（旧）'
                              : 'Single Agent（旧）'}</td>
                      <td>{run.status}</td>
                      <td>{String(run.diagnosis.root_cause_id ?? '—')}</td>
                      <td>{seconds(typeof run.metrics.end_to_end_ms === 'number' ? run.metrics.end_to_end_ms : null)}</td>
                      <td>
                        {run.task_run_id && onSelectTask ? (
                          <Button
                            type="link"
                            size="small"
                            aria-label={`查看协作树 ${run.task_run_id}`}
                            onClick={() => onSelectTask(run.task_run_id!)}
                          >
                            查看协作树
                          </Button>
                        ) : (run.failure_code || '—')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Space>
    </Card>
  );
}
