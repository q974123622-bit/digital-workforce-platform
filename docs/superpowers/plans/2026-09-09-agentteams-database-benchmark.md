# AgentTeams Database Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Use superpowers:test-driven-development for every behavior change and superpowers:verification-before-completion before claiming success.

## Goal

在不污染现有 `feature/personal-memory` 主工作目录的前提下，为 AgentTeams 页面增加一套可复现的数据库故障诊断 A/B 评测：同一场景、同一数据、同一模型参数和同一预算下，对比 Single Agent 与 AgentTeams 的正确率、证据质量、耗时、模型迭代次数、工具调用次数、重复调用、关键路径和返工。

## Architecture

评测使用从 MultiAgentBench Database 场景改编的 5 个本地确定性 JSON 快照，不运行 PostgreSQL，也不导入 MARBLE runtime。所有数据库观测统一通过新的只读 `inspect_database_signal` Tool，并继续经过现有 `Policy → Plugin Gateway → Capability Executor → Adapter → Audit`。评测编排器把每次运行落库为 `BenchmarkExperiment` 和 `BenchmarkRun`；Single Agent 禁止委派，AgentTeams 复用现有 Manager、TaskNode、消息和事件事实。前端新增独立 A/B 面板，不覆盖当前单任务 100 分评测。

## Tech Stack

- Backend: Python 3.11、FastAPI、SQLAlchemy 2、Pydantic 2、pytest
- Agent runtime: 现有 DeepSeek OpenAI-compatible provider、AgentDecisionEngine、Matrix/local transport 协议
- Frontend: React 18、TypeScript、Ant Design、Vitest、Testing Library
- Data: SQLite 持久化 + Git 内只读 JSON fixtures

## Spec

- 设计基线：`docs/superpowers/specs/2026-09-09-agentteams-database-benchmark-design.md`
- 首期场景：`bulk_writes`、`lock_contention`、`vacuum_pressure`、`index_anomaly`、`large_query`
- 信号枚举：`statements`、`locks`、`indexes`、`vacuum`、`table_stats`
- 模式枚举：`single_agent`、`agentteams`
- 默认实验规模：5 场景 × 5 重复 × 2 模式 = 50 runs
- 结果必须称为“基于 MultiAgentBench Database 场景改编的本地对照评测”，不得声称是官方榜单成绩。

## Global Constraints

- 用户提供的模型 API Key 只允许从进程环境变量读取。源码、Skill、fixture、测试、文档、日志、异常、审计记录和 Git 中不得出现真实值。
- 5 个数据库 Worker 的密钥环境变量名固定为：`DEEPSEEK_API_KEY_DB_WRITE`、`DEEPSEEK_API_KEY_DB_LOCK`、`DEEPSEEK_API_KEY_DB_VACUUM`、`DEEPSEEK_API_KEY_DB_INDEX`、`DEEPSEEK_API_KEY_DB_QUERY`。Manager 继续使用既有 `DEEPSEEK_API_KEY_MANAGER`。
- 测试仅使用 fake provider/fake transport，不访问真实模型、Matrix 或数据库服务。
- 不自动 commit、push、stash、reset；每个任务结束只做工作树 review checkpoint。
- 基准计时排除前端演示用的 5 秒可视化暂停；后端轮询使用固定最小间隔。
- Single Agent 与 AgentTeams 的 scenario snapshot、Tool schema、模型名、temperature/thinking、最大模型步数、最大 Tool 次数、超时、缓存和记忆开关必须相同，否则 run 标记 `invalid`。
- 任何越权 signal、绕过 Gateway、缺少 audit、伪造或跨场景 evidence_id、丢失 child result、非法终态、预算耗尽、死锁或超时都触发 hard gate；准确率不得记为通过。

---

### Task 1: Add deterministic database benchmark fixtures and loader

**Files:**

- Create: `mock-data/agentteams-benchmark/database/ATTRIBUTION.md`
- Create: `mock-data/agentteams-benchmark/database/scenarios.jsonl`
- Create: `mock-data/agentteams-benchmark/database/{bulk_writes,lock_contention,vacuum_pressure,index_anomaly,large_query}/{statements,locks,indexes,vacuum,table_stats,expected}.json`
- Create: `backend/app/services/agentteams_database_scenarios.py`
- Test: `backend/tests/test_agentteams_database_scenarios.py`

**Step 1: Write failing loader tests**

Tests must assert:

```python
catalog = DatabaseScenarioCatalog.from_root(PROJECT_ROOT)
assert catalog.ids() == [
    "bulk_writes", "lock_contention", "vacuum_pressure", "index_anomaly", "large_query"
]
scenario = catalog.get("lock_contention")
assert scenario.expected.root_cause_id
assert set(scenario.available_signals) == {"statements", "locks", "indexes", "vacuum", "table_stats"}
assert catalog.inspect("lock_contention", "locks", filters={}, limit=20).evidence_id.startswith("db:lock_contention:locks:")
```

Also reject unknown scenarios/signals, invalid JSON shape, limits outside `1..100`, path traversal, and hidden expected labels leaking through `inspect`.

**Step 2: Run red test**

Run: `python -m pytest backend/tests/test_agentteams_database_scenarios.py -q -p no:cacheprovider`

Expected: import/file failures.

**Step 3: Implement immutable typed loader**

Add frozen dataclasses/Pydantic models:

```python
class DatabaseScenarioCatalog:
    @classmethod
    def from_root(cls, root: Path) -> "DatabaseScenarioCatalog": ...
    def ids(self) -> list[str]: ...
    def get(self, scenario_id: str) -> DatabaseScenario: ...
    def inspect(self, scenario_id: str, signal: SignalName, *, filters: dict, limit: int) -> SignalInspection: ...
```

Compute `evidence_id` from scenario ID, signal, canonical filters, and SHA-256 of the fixture bytes. Never return `expected.json` through the Tool-facing API.

**Step 4: Add five bounded fixtures**

Each scenario must include one canonical root cause, acceptable aliases, required evidence IDs/signals, distractors, Chinese task text, and all five signal snapshots. `lock_contention` and `large_query` must contain plausible distractor observations.

**Step 5: Run green test and checkpoint**

Run the Task 1 test command; inspect `git diff --check` and `git status --short` without committing.

---

### Task 2: Register `inspect_database_signal` through the existing controlled Tool pipeline

**Files:**

- Modify: `backend/app/services/adapters.py`
- Modify: `backend/app/seed.py`
- Modify: `mock-data/seed.json`
- Modify: `backend/app/services/agentteams_runtime_config.py`
- Test: `backend/tests/test_agentteams_database_tool.py`
- Test: `backend/tests/test_agentteams_strict_runtime_config.py`

**Step 1: Write failing Tool tests**

Create a seeded DB, call `gateway.invoke_plugin` for plugin `agentteams-database-inspector`, then assert:

```python
result = invoke_plugin(
    db_session,
    employee_id="DB-WRITE-01",
    plugin_id="agentteams-database-inspector",
    action="read",
    params={"scenario_id": "bulk_writes", "signal": "statements", "filters": {}, "limit": 20},
    trace_id="BENCH-RUN-1",
)
assert result["ok"] is True
assert result["data"]["source"] == "local_fixture"
assert result["audit_ids"]
```

Test deny cases: ungranted employee, unknown signal, extra parameter, invalid limit, filter fields outside the allowlist, and direct adapter path not bypassing validation.

**Step 2: Run red test**

Run: `python -m pytest backend/tests/test_agentteams_database_tool.py -q -p no:cacheprovider`

Expected: unknown plugin/adapter.

**Step 3: Implement adapter and schema validation**

Add one handler at endpoint `mock://agentteams/database-inspector`. Extend the strict safe Tool schema validator to support this bounded schema subset:

```json
{
  "type": "object",
  "required": ["scenario_id", "signal", "filters", "limit"],
  "properties": {
    "scenario_id": {"type": "string", "enum": ["bulk_writes", "lock_contention", "vacuum_pressure", "index_anomaly", "large_query"]},
    "signal": {"type": "string", "enum": ["statements", "locks", "indexes", "vacuum", "table_stats"]},
    "filters": {"type": "object", "additionalProperties": {"type": "string"}, "maxProperties": 4},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100}
  },
  "additionalProperties": false
}
```

**Step 4: Seed plugin, grants and five DB employees**

Create IDs `DB-WRITE-01`, `DB-LOCK-01`, `DB-VACUUM-01`, `DB-INDEX-01`, `DB-QUERY-01`, each granted read-only access to the same plugin. Add strict sync constants without removing existing onboarding/role-change registrations.

**Step 5: Run targeted tests and checkpoint**

Run Task 2 tests plus existing `test_capabilities.py` and strict config tests; inspect diff without committing.

---

### Task 3: Add one scenario Skill and five professional Skills

**Files:**

- Create: `.agents/skills/agentteams-database-diagnosis/SKILL.md`
- Create: `.agents/skills/db-write-investigation/SKILL.md`
- Create: `.agents/skills/db-lock-investigation/SKILL.md`
- Create: `.agents/skills/db-vacuum-investigation/SKILL.md`
- Create: `.agents/skills/db-index-investigation/SKILL.md`
- Create: `.agents/skills/db-query-investigation/SKILL.md`
- Modify: `backend/app/services/agentteams_agent_runtime.py`
- Modify: `scripts/run_agentteams_agent.py`
- Test: `backend/tests/test_agentteams_database_skills.py`
- Test: `backend/tests/test_agentteams_agent_runtime.py`

**Step 1: Write failing registry/runtime tests**

Assert all six skills load through `ProjectSkillRegistry`, each DB Worker sees only `employee-collaboration` plus its own professional Skill, and the CLI maps each database role to the corresponding environment variable name without a shared-key fallback.

**Step 2: Run red tests**

Run: `python -m pytest backend/tests/test_agentteams_database_skills.py backend/tests/test_agentteams_agent_runtime.py -q -p no:cacheprovider`

**Step 3: Implement Skills**

The scenario Skill defines the diagnosis output contract, evidence citation rules, manager partitioning, fairness and stop conditions. Professional Skills define only role-specific hypotheses and signal selection; none may contain expected answers from fixtures.

**Step 4: Extend role-neutral runtime safely**

Replace fixed role assumptions with a validated role catalog while keeping legacy `manager/hr/it/audit` behavior. Add DB role→Skill and DB role→environment-variable mappings. Do not serialize any API key.

**Step 5: Run tests and checkpoint**

Run the targeted tests and verify existing onboarding/role-change tests remain green.

---

### Task 4: Add structured diagnosis contracts and deterministic scoring

**Files:**

- Create: `backend/app/services/agentteams_database_evaluation.py`
- Modify: `backend/app/services/agentteams_decisions.py`
- Test: `backend/tests/test_agentteams_database_evaluation.py`
- Test: `backend/tests/test_agentteams_decisions.py`

**Step 1: Write failing evaluator tests**

Define and test:

```python
class DiagnosisResult(BaseModel):
    root_cause_id: str
    top_candidates: list[str]
    evidence_ids: list[str]
    conclusion: str
    confidence: float

score = evaluate_diagnosis(expected, result, valid_audit_evidence_ids)
assert score.primary_accuracy in {0.0, 1.0}
assert 0.0 <= score.evidence_coverage <= 1.0
assert score.hard_gate_passed is True
```

Test forged IDs, cross-scenario IDs, missing audits, wrong answer with correct distractor evidence, top-2 hit, duplicate Tool calls, lost child results, and illegal terminal states.

**Step 2: Run red tests**

Run: `python -m pytest backend/tests/test_agentteams_database_evaluation.py -q -p no:cacheprovider`

**Step 3: Implement pure scoring functions**

Return separate result quality, process, performance and reliability fields. Accuracy is label-based; evidence validity is audit-backed; `parallel_speedup = serial_sum_ms / critical_path_ms` only when both values are positive, otherwise `None`.

**Step 4: Add database-specific decision validation**

When selected Skill is `agentteams-database-diagnosis`, require Manager to delegate only known DB capabilities and to summarize into `DiagnosisResult`. Single Agent uses the same result schema and Skill content but its action schema omits delegation.

**Step 5: Run tests and checkpoint**

Run both targeted suites and current AgentTeams decision tests.

---

### Task 5: Implement fair Single Agent and AgentTeams run engines

**Files:**

- Create: `backend/app/services/agentteams_database_benchmark_runner.py`
- Modify: `backend/app/services/agentteams_collaboration.py`
- Modify: `backend/app/services/agentteams_runner.py`
- Test: `backend/tests/test_agentteams_database_benchmark_runner.py`

**Step 1: Write failing runner contract tests**

Use a scripted fake provider and fake monotonic clock. Assert both modes receive identical `BenchmarkBudget` and `ProviderConfig`, Single Agent rejects delegation, AgentTeams creates Manager + specialized child nodes, all Tool requests pass through `invoke_plugin`, and counters are derived from actual calls/events rather than model self-report.

Required interfaces:

```python
@dataclass(frozen=True)
class BenchmarkBudget:
    max_model_steps: int
    max_tool_calls: int
    timeout_ms: int

class DatabaseBenchmarkRunEngine:
    def run_single(self, request: BenchmarkRunRequest) -> BenchmarkRunResult: ...
    def run_agentteams(self, request: BenchmarkRunRequest) -> BenchmarkRunResult: ...
```

**Step 2: Run red test**

Run: `python -m pytest backend/tests/test_agentteams_database_benchmark_runner.py -q -p no:cacheprovider`

**Step 3: Implement Single Agent loop**

The one agent may inspect any of the five signals but cannot create child tasks or send peer messages. It loops model decision → controlled Tool → audit-backed observation → final diagnosis until success or budget/timeout.

**Step 4: Implement AgentTeams bridge**

Submit through the existing collaboration service and measure from persisted TaskNode/AgentMessage/AgentTaskEvent/AuditEvent facts. A run becomes complete only when root terminal state and every required child result are present.

**Step 5: Add deterministic fake execution path**

The fake path must exercise the same decision/Tool/scoring interfaces so CI can validate the benchmark without real keys or network.

**Step 6: Run tests and checkpoint**

Run targeted runner, collaboration and runner regression suites.

---

### Task 6: Persist experiments/runs and execute asynchronously

**Files:**

- Modify: `backend/app/models.py`
- Modify: `backend/app/database.py`
- Modify: `backend/app/schemas.py`
- Create: `backend/app/services/agentteams_database_benchmark.py`
- Test: `backend/tests/test_agentteams_database_benchmark_service.py`

**Step 1: Write failing persistence tests**

Test experiment creation expands the matrix in deterministic order, per-run failure does not abort remaining runs, restart resumes only pending/running rows, summaries aggregate only valid runs, and one mode cannot use a different fairness fingerprint.

**Step 2: Run red test**

Run: `python -m pytest backend/tests/test_agentteams_database_benchmark_service.py -q -p no:cacheprovider`

**Step 3: Add models**

```python
class BenchmarkExperiment(Base):
    id, owner_human_no, benchmark_id, status, scenario_ids, repeats,
    fairness_config, fairness_fingerprint, summary, created_at, started_at, finished_at

class BenchmarkRun(Base):
    id, experiment_id, scenario_id, repeat_index, mode, status,
    task_run_id, metrics, diagnosis, failure_code, started_at, finished_at
```

Add only additive SQLite compatibility columns/tables; never rewrite existing tables.

**Step 4: Implement service and background worker**

Use bounded daemon execution with one experiment lock, independent DB sessions, status transitions `pending → running → completed|failed|invalid`, and per-run exception containment. Store no prompts containing secrets and no credentials.

**Step 5: Run tests and checkpoint**

Run service/model/schema tests and restart-resume tests.

---

### Task 7: Expose benchmark API

**Files:**

- Create: `backend/app/routers/agentteams_benchmarks.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_agentteams_database_benchmark_api.py`

**Step 1: Write failing API tests**

Cover:

```text
POST /api/v1/agentteams-benchmarks/database/experiments?actor_no=...
GET  /api/v1/agentteams-benchmarks/database/experiments/{id}?actor_no=...
GET  /api/v1/agentteams-benchmarks/database/experiments/{id}/runs?actor_no=...
```

Assert owner isolation, 404 for unknown IDs, 422 for invalid repeats/scenarios, async `pending|running` response, stable run order, and no credential material in JSON.

**Step 2: Run red test**

Run: `python -m pytest backend/tests/test_agentteams_database_benchmark_api.py -q -p no:cacheprovider`

**Step 3: Implement router and dependency wiring**

Use existing `get_db`, error conventions and actor validation. POST queues work after committing experiment rows; GET endpoints are read-only.

**Step 4: Run tests and checkpoint**

Run API tests plus current collaboration API tests.

---

### Task 8: Add the frontend A/B benchmark panel

**Files:**

- Modify: `shared-schema/types.ts`
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/pages/agentteams/DatabaseBenchmarkPanel.tsx`
- Create: `frontend/src/pages/agentteams/DatabaseBenchmarkPanel.test.tsx`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.tsx`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

**Step 1: Write failing UI tests**

Mock API responses and assert the panel shows:

- benchmark attribution label;
- 5 scenarios and repeat count;
- start button and progress `completed / total`;
- side-by-side Single Agent vs AgentTeams values for accuracy, P50/P95 time, model steps, Tool calls, rework and success rate;
- invalid/failure count and fairness fingerprint mismatch warning;
- no mixing with current 100-point evaluation.

**Step 2: Run red tests**

Run: `npm --workspace @dwp/frontend test -- DatabaseBenchmarkPanel AgentTeamsCollaborationPage`

**Step 3: Implement types/client/panel**

Add exact backend response types, API methods, bounded polling, loading/error/empty states and a compact comparison table. Stop polling at terminal experiment state or unmount.

**Step 4: Mount below existing evaluation**

Keep the current collaboration tree and `EvaluationPanel`; add a visually separate “数据库诊断 A/B 基准” section.

**Step 5: Run tests and checkpoint**

Run targeted UI tests and `npm --workspace @dwp/frontend run typecheck`.

---

### Task 9: Security, regression and end-to-end verification

**Files:**

- Modify: `backend/.env.example`
- Create: `docs/agentteams/DATABASE_BENCHMARK.md`
- Create: `backend/tests/test_agentteams_database_benchmark_security.py`
- Optional local-only, gitignored: `.agentteams-runtime/database-benchmark.env`

**Step 1: Add security regression tests**

Scan tracked benchmark files and serialized API/model outputs for credential patterns; assert environment values never appear. Verify unknown Tool/signal requests fail closed and every successful observation references an allow audit event.

**Step 2: Document local real-run setup without values**

List only environment variable names and role mapping. Explain that tests are deterministic, real DeepSeek/Matrix smoke is optional, and results are a local adapted benchmark rather than official MultiAgentBench scores.

**Step 3: Run focused backend suite**

Run:

```powershell
python -m pytest backend/tests/test_agentteams_database_scenarios.py backend/tests/test_agentteams_database_tool.py backend/tests/test_agentteams_database_skills.py backend/tests/test_agentteams_database_evaluation.py backend/tests/test_agentteams_database_benchmark_runner.py backend/tests/test_agentteams_database_benchmark_service.py backend/tests/test_agentteams_database_benchmark_api.py backend/tests/test_agentteams_database_benchmark_security.py -q -p no:cacheprovider
```

**Step 4: Run AgentTeams regressions**

Run:

```powershell
python -m pytest backend/tests/test_agentteams_decisions.py backend/tests/test_agentteams_agent_runtime.py backend/tests/test_agentteams_runner.py backend/tests/test_agentteams_collaboration_api.py backend/tests/test_capabilities.py -q -p no:cacheprovider
```

**Step 5: Run frontend verification**

Run:

```powershell
npm --workspace @dwp/frontend test -- DatabaseBenchmarkPanel AgentTeamsCollaborationPage
npm --workspace @dwp/frontend run typecheck
npm --workspace @dwp/frontend run build
```

**Step 6: Verify repository hygiene**

Run `git diff --check`, inspect `git status --short`, and scan the diff for secret-shaped values. Do not run a real paid/network benchmark unless the user separately asks for that smoke and the environment is configured outside Git.

## Plan Self-Review

- Spec coverage: scenarios, Skill, five specialists, one Tool, Gateway/audit, Single vs AgentTeams, persistence, APIs, UI, metrics, fairness and hard gates all have implementation tasks and tests.
- Placeholder audit: no TODO/TBD/ellipsis is used as an implementation instruction; Python `...` appears only in interface signatures illustrating abstract implementation points.
- Type consistency: scenario/mode/signal enums are identical across loader, runner, persistence, API and TypeScript sections.
- Secret safety: only environment variable names are specified; no credential values are present or requested in files.
- Regression safety: legacy onboarding and role-change AgentTeams suites remain explicit gates.
- Integration policy: no commit/push/stash/reset steps are included; checkpoints are read-only reviews.
