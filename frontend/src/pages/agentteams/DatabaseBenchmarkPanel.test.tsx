import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { api } from '../../api/client';
import { DatabaseBenchmarkPanel } from './DatabaseBenchmarkPanel';


afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});


describe('DatabaseBenchmarkPanel', () => {
  it('restores the latest persisted experiment when the page is reopened', async () => {
    const persistedExperiment = {
      id: 'DBE-PERSISTED',
      benchmark_id: 'multiagentbench-database-local-v1',
      status: 'completed',
      scenario_ids: ['lock_contention'],
      repeats: 1,
      fairness_config: { max_model_steps: 20, max_tool_calls: 10, timeout_ms: 300000 },
      fairness_fingerprint: 'persisted123',
      total_runs: 3,
      completed_runs: 3,
      failed_runs: 0,
      invalid_runs: 0,
      created_at: '2026-09-09T10:00:00',
      started_at: '2026-09-09T10:00:01',
      finished_at: '2026-09-09T10:01:00',
      summary: {},
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const body = String(input).includes('/runs') ? [] : persistedExperiment;
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    render(<DatabaseBenchmarkPanel actorNo="E10281" />);

    expect(await screen.findByText('已完成 3 / 3')).toBeInTheDocument();
  });

  it('starts only the three complex scenarios and renders coordination metrics', async () => {
    const experiment = {
      id: 'DBE-1',
      benchmark_id: 'multiagentbench-database-local-v1',
      status: 'completed',
      scenario_ids: ['compound_lock_index', 'conflicting_signals_followup', 'budgeted_incident_triage'],
      repeats: 5,
      fairness_config: { max_model_steps: 20, max_tool_calls: 10, timeout_ms: 30000 },
      fairness_fingerprint: 'abc123',
      total_runs: 45,
      completed_runs: 44,
      failed_runs: 0,
      invalid_runs: 1,
      created_at: '2026-09-09T10:00:00',
      started_at: '2026-09-09T10:00:01',
      finished_at: '2026-09-09T10:01:00',
      summary: {
        provenance: { label: '基于 MultiAgentBench Database 场景改编的本地对照评测' },
        agentteams_serial: {
          total_runs: 15, valid_runs: 15, failed_or_invalid_runs: 0,
          primary_accuracy: 0.6, top2_hit: 0.72, evidence_coverage: 0.7,
          evidence_validity: 1, success_rate: 0.6, average_model_steps: 6.2,
          average_critical_path_steps: 6.2, average_tool_calls: 4.8,
          average_duplicate_tool_calls: 0.8, average_rework_count: 1.2,
          secondary_cause_hit: 0.8, conflict_resolution_rate: 0.75,
          effective_rework_rate: 0.6, budget_success_rate: 0.72,
          p50_end_to_end_ms: 4200, p95_end_to_end_ms: 7100,
        },
        agentteams_full: {
          total_runs: 15, valid_runs: 14, failed_or_invalid_runs: 1,
          primary_accuracy: 0.84, top2_hit: 0.92, evidence_coverage: 0.95,
          evidence_validity: 1, success_rate: 0.84, average_model_steps: 8.4,
          average_critical_path_steps: 2, average_tool_calls: 5,
          average_duplicate_tool_calls: 0, average_rework_count: 0.4,
          secondary_cause_hit: 0.93, conflict_resolution_rate: 0.9,
          effective_rework_rate: 0.85, budget_success_rate: 0.84,
          p50_end_to_end_ms: 3100, p95_end_to_end_ms: 5300,
          p50_valid_answer_ms: 3100, average_parallel_speedup: 1.61,
        },
        agentteams_uncoordinated: {
          total_runs: 15, valid_runs: 15, failed_or_invalid_runs: 0,
          primary_accuracy: 0.6, top2_hit: 0.72, evidence_coverage: 0.7,
          evidence_validity: 1, success_rate: 0.6, average_model_steps: 6.2,
          average_critical_path_steps: 2, average_tool_calls: 5,
          average_duplicate_tool_calls: 0, average_rework_count: 0,
          secondary_cause_hit: 0.4, conflict_resolution_rate: 0.2,
          effective_rework_rate: 0, budget_success_rate: 0.3,
          p50_end_to_end_ms: 2900, p95_end_to_end_ms: 5100,
          p50_valid_answer_ms: 3300, average_parallel_speedup: 1.5,
        },
        attribution: {
          parallel_time_reduction: 0.2619,
          coordination_accuracy_gain: 0.24,
          coordination_evidence_gain: 0.25,
          full_team_parallel_speedup: 1.61,
        },
      },
    };
    vi.spyOn(api, 'getLatestDatabaseBenchmark').mockResolvedValue(null);
    const create = vi.spyOn(api, 'createDatabaseBenchmark').mockResolvedValue(experiment);
    vi.spyOn(api, 'listDatabaseBenchmarkRuns').mockResolvedValue([{
      id: 'DBR-1', experiment_id: 'DBE-1', ordinal: 1, scenario_id: 'compound_lock_index',
      repeat_index: 1, mode: 'agentteams_full', status: 'completed', task_run_id: 'AT-DB-1',
      fairness_fingerprint: 'abc123', metrics: { primary_accuracy: 1, end_to_end_ms: 3100 },
      diagnosis: { root_cause_id: 'lock_wait_chain' }, failure_code: '',
      created_at: '2026-09-09T10:00:00', started_at: '2026-09-09T10:00:01', finished_at: '2026-09-09T10:00:04',
    }]);
    const selectTask = vi.fn();

    render(<DatabaseBenchmarkPanel actorNo="E10281" onSelectTask={selectTask} />);

    expect(screen.getByText('数据库诊断协作机制消融评测')).toBeInTheDocument();
    expect(screen.getByText('基于 MultiAgentBench Database 场景改编的本地对照评测')).toBeInTheDocument();
    expect(screen.getByText('3 个复杂场景 × 5 次 × 3 种模式 = 45 runs')).toBeInTheDocument();

    const startButton = screen.getByRole('button', { name: '开始对照评测' });
    await waitFor(() => expect(startButton).not.toHaveClass('ant-btn-loading'));
    fireEvent.click(startButton);

    await waitFor(() => expect(create).toHaveBeenCalledWith('E10281', expect.objectContaining({
      scenario_ids: ['compound_lock_index', 'conflicting_signals_followup', 'budgeted_incident_triage'],
      repeats: 5,
    })));
    expect((await screen.findAllByText('完整 AgentTeam')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('串行 AgentTeam').length).toBeGreaterThan(0);
    expect(screen.getAllByText('无协调 AgentTeam').length).toBeGreaterThan(0);
    expect(screen.getByText('并行执行贡献：正确答案耗时降低 26.2%')).toBeInTheDocument();
    expect(screen.getByText('Manager 协调贡献：根因准确率提高 24.0%')).toBeInTheDocument();
    const accuracyRow = screen.getByText('主答案正确率').closest('tr');
    const p50Row = screen.getByText('P50 耗时').closest('tr');
    expect(accuracyRow).not.toBeNull();
    expect(p50Row).not.toBeNull();
    expect(within(accuracyRow!).getAllByText('60.0%')).toHaveLength(2);
    expect(within(accuracyRow!).getByText('84.0%')).toBeInTheDocument();
    expect(within(p50Row!).getByText('4.20s')).toBeInTheDocument();
    expect(within(p50Row!).getByText('3.10s')).toBeInTheDocument();
    expect(screen.getByText('伴随原因命中率')).toBeInTheDocument();
    expect(screen.getByText('冲突解决率')).toBeInTheDocument();
    expect(screen.getByText('有效返工率')).toBeInTheDocument();
    expect(screen.getByText('预算内成功率')).toBeInTheDocument();
    expect(screen.getByText('已完成 44 / 45')).toBeInTheDocument();
    expect(screen.getByText('无效/失败 1')).toBeInTheDocument();
    expect(await screen.findByText('运行明细')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看协作树 AT-DB-1' }));
    expect(selectTask).toHaveBeenCalledWith('AT-DB-1');
  });

  it('does not allow a second experiment while the latest experiment is active', async () => {
    const activeExperiment = {
      id: 'DBE-ACTIVE',
      benchmark_id: 'multiagentbench-database-local-v1',
      status: 'running',
      scenario_ids: ['compound_lock_index', 'conflicting_signals_followup', 'budgeted_incident_triage'],
      repeats: 5,
      fairness_config: { max_model_steps: 20, max_tool_calls: 10, timeout_ms: 30000 },
      fairness_fingerprint: 'active123',
      total_runs: 45,
      completed_runs: 2,
      failed_runs: 0,
      invalid_runs: 0,
      created_at: '2026-09-10T13:12:01',
      started_at: '2026-09-10T13:12:02',
      finished_at: null,
      summary: {},
    };
    vi.spyOn(api, 'getLatestDatabaseBenchmark').mockResolvedValue(activeExperiment);
    vi.spyOn(api, 'getDatabaseBenchmark').mockResolvedValue(activeExperiment);
    vi.spyOn(api, 'listDatabaseBenchmarkRuns').mockResolvedValue([]);
    const create = vi.spyOn(api, 'createDatabaseBenchmark');

    render(<DatabaseBenchmarkPanel actorNo="E10281" />);

    const button = await screen.findByRole('button', { name: '评测进行中' });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(create).not.toHaveBeenCalled();
  });
});
