# AgentTeams 数据库异常诊断对照评测设计

## 1. 目标

在现有 AgentTeams 协作系统中增加一个基于 MultiAgentBench/MARBLE Database 场景改编的本地评测，使用同一模型、同一工具、同一场景数据和同一资源预算，对比 Single Agent 与 AgentTeams 两种执行方式。

评测同时回答三个问题：

1. 两种方式能否正确识别数据库异常根因；
2. AgentTeams 的并行分工是否缩短端到端时间和关键路径；
3. AgentTeams 是否以更多总调用为代价，降低遗漏、无效查询和返工。

该评测是本项目内部 A/B 实验，不申报为 MARBLE 官方成绩，也不与 MARBLE 排行榜直接比较。

## 2. 已确认边界

- 开发分支为 `feature/agentteams-database-benchmark`，工作目录为现有独立 worktree `D:\lincwc\Desktop\digital workforce platform-agentteams-collaboration`。
- 使用确定性 JSON 监控快照，不启动 PostgreSQL，不注入真实高负载，不依赖外部评测服务。
- 保留现有 Manager、TaskNode、Matrix、Plugin Gateway、Policy、Audit 和前端任务树。
- 不引入 MARBLE Runtime，不替换当前 AgentTeams 协议和状态机。
- 数据库诊断 Tool 只读且参数受限，不提供任意 SQL 执行能力。
- MARBLE 标准答案只供服务端评测器读取，不进入 Skill、Agent Prompt、Tool 返回值或前端运行态详情。
- 现有“规则 60 分 + 模型 40 分”评测不作为新实验主结果；新基准以可复现的 A/B 指标单独展示。
- 不自动提交或推送代码，等待用户最终 review。

## 3. 开源来源与使用方式

场景结构参考 MARBLE 的 `multiagentbench/database/database_main.jsonl`。其任务包含数据库背景、初始化结构、候选根因、异常配置、Agent 专业方向、标准根因、准确率和响应时间等信息。

本项目仅移植并改编少量场景定义、角色划分和标准标签，运行环境、工具协议、协作过程与指标采集均使用本项目实现。仓库内增加来源说明，并保留 MARBLE MIT License 要求的版权和许可文本。

来源：

- https://github.com/ulab-uiuc/MARBLE
- https://github.com/ulab-uiuc/MARBLE/blob/main/multiagentbench/database/database_main.jsonl
- https://github.com/ulab-uiuc/MARBLE/blob/main/LICENSE

## 4. 总体结构

```text
Benchmark Experiment
        │
        ├─ Scenario Fixture + Hidden Ground Truth
        │
        ├─ Single Agent Runner
        │      └─ 同一诊断 Skill + 同一只读 Tool
        │
        ├─ AgentTeams Runner
        │      └─ Manager + 专业子 Agent + 同一只读 Tool
        │
        ├─ Metrics Collector
        │      ├─ 时间
        │      ├─ 模型步骤
        │      ├─ Tool 调用
        │      ├─ 关键路径
        │      └─ 返工/协作轮次
        │
        └─ Deterministic Evaluator
               ├─ 根因标签
               ├─ 证据覆盖
               └─ 治理硬门槛
```

一次实验选择若干场景和重复次数。运行器依次为每个场景创建相互隔离的 Single Agent 运行和 AgentTeams 运行，完成后汇总成同一份实验报告。

## 5. Skill 设计

### 5.1 Manager 场景 Skill

新增：

```text
.agents/skills/agentteams-database-diagnosis/SKILL.md
```

它定义：

- 候选根因集合；
- 第一轮需要覆盖的证据方向；
- 独立方向应并行调查；
- 证据冲突或不足时必须复核；
- 结论必须引用 Tool 返回的 `evidence_id`；
- 最终结构化输出契约；
- 不得从任务描述猜测未观察事实。

该 Skill 不包含任何场景的真实根因。

### 5.2 专业 Skill

新增五个轻量专业 Skill：

```text
.agents/skills/db-write-investigation/SKILL.md
.agents/skills/db-lock-investigation/SKILL.md
.agents/skills/db-vacuum-investigation/SKILL.md
.agents/skills/db-index-investigation/SKILL.md
.agents/skills/db-query-investigation/SKILL.md
```

每个专业 Skill 只规定该方向允许读取的信号、支持/排除根因的证据规则、返回字段和职责边界。子 Agent 不做全局最终判断。

Single Agent 获得相同的候选根因、证据要求、Tool 定义和最终输出契约，但禁止使用 `task.delegate`。因此两组获得的业务知识一致，唯一实验变量是协作架构。

## 6. Agent 设计

保留现有 Manager，新增五个可授权数字员工：

| Agent ID | 职责 | 主要信号 |
|---|---|---|
| `db-write-investigator` | 批量写入与写入突增 | `statements`, `table_stats` |
| `db-lock-investigator` | 锁等待与阻塞链 | `locks` |
| `db-vacuum-investigator` | VACUUM、死元组与表膨胀 | `vacuum`, `table_stats` |
| `db-index-investigator` | 缺失、低效或冗余索引 | `indexes`, `statements` |
| `db-query-investigator` | 大结果集、慢查询和异常读取 | `statements`, `table_stats` |

Manager 根据场景 Skill 和授权团队目录生成第一轮任务，不允许评测运行器直接伪造子节点。第一轮结果存在矛盾、证据缺失或低置信度时，Manager 可以进行第二轮定向派发。

## 7. Tool 与 Gateway

新增一个平台 Tool：

```text
inspect_database_signal
```

输入：

```json
{
  "scenario_id": "marble-db-001",
  "signal": "locks",
  "filters": {},
  "limit": 20
}
```

`signal` 仅允许：

```text
statements | locks | indexes | vacuum | table_stats
```

输出：

```json
{
  "scenario_id": "marble-db-001",
  "signal": "locks",
  "rows": [],
  "evidence_id": "DB-EV-LOCK-001",
  "source": "benchmark_fixture"
}
```

Tool 通过现有 `Policy → Plugin Gateway → Adapter → Audit` 链路执行。Policy 根据 Agent 身份和 `signal` 限制可访问的数据方向；Prompt 中的职责说明不能代替后端授权。

禁止：

- 任意 SQL；
- 修改数据库状态；
- 返回 `expected_primary`、标准标签或可直接定位答案的内部字段；
- 绕过 Gateway 直接读取 fixture；
- 使用不属于当前 `scenario_id` 的证据。

## 8. 场景数据

新增：

```text
mock-data/agentteams-benchmark/database/
├─ ATTRIBUTION.md
├─ scenarios.jsonl
├─ marble-db-001/
│  ├─ statements.json
│  ├─ locks.json
│  ├─ indexes.json
│  ├─ vacuum.json
│  ├─ table_stats.json
│  └─ expected.json
└─ ...
```

第一阶段选取并改编 5 个场景，分别覆盖：

1. 大批量写入；
2. 锁竞争；
3. VACUUM 异常；
4. 索引异常；
5. 大查询或异常读取。

至少两个场景包含干扰信号，用于检验是否会把相关但非主要的异常误判为根因。每个运行开始时读取不可变快照，不允许前一次运行改变下一次运行的数据。

`expected.json` 仅由评测服务读取，包含标准主根因、可接受的次要根因、必要信号和必要证据规则。运行期 Agent 只能通过 Tool 读取公开监控快照。

## 9. 执行模式

### 9.1 Single Agent

- 一个 Agent 完成全部调查；
- 使用与 AgentTeams 相同的模型、模型参数、Tool、场景快照和最终输出契约；
- 禁止委派；
- 允许多轮模型—工具循环；
- 每次模型请求记为一个 `model_step`。

### 9.2 AgentTeams

- Manager 使用现有 Skill 选择和任务规划链路；
- 子 Agent 通过真实 `task.delegate`、`task.assign`、`tool.request` 和 `task.result` 执行；
- 无依赖调查可以并行；
- Manager 必须读取子结果再总结；
- 追加调查计入新的 Manager round 和返工指标。

### 9.3 公平性约束

两组固定：

- 模型提供方与模型版本；
- 温度和输出约束；
- Tool Schema；
- 场景数据版本；
- 超时；
- 最大模型步骤；
- 最大 Tool 调用；
- 缓存与长期记忆开关；
- 最终输出 Schema。

若任一固定条件不一致，该组运行标为 `invalid_run`，不进入汇总比较。

## 10. 指标定义

### 10.1 结果指标

- `primary_accuracy`：第一根因与标准主根因一致为 1，否则为 0。
- `top2_hit`：标准主根因出现在前两个结论中为 1，否则为 0。
- `evidence_coverage`：已取得的必要信号数除以场景规定的必要信号数。
- `evidence_validity`：最终引用且属于当前场景的有效证据数除以最终引用证据总数；无引用时为 0。

### 10.2 效率指标

- `end_to_end_ms`：服务端接收运行请求到持久化终态结果的单调时钟耗时。
- `total_model_steps`：该运行内所有 Agent 的模型请求数之和。
- `tool_call_count`：已进入 Gateway 的数据库诊断 Tool 请求数。
- `duplicate_tool_calls`：同一 Agent 对相同 `scenario_id + signal + filters` 的重复成功请求数。
- `critical_path_steps`：从根任务到终态的最长依赖路径上的模型步骤数；并行兄弟节点不相加。
- `parallel_speedup`：Single Agent 中位耗时除以 AgentTeams 中位耗时。大于 1 表示 AgentTeams 更快。

### 10.3 协作指标

- `manager_rounds`：Manager 已验证的规划/追加派发轮数。
- `rework_count`：因为证据不足、冲突或失败而产生的追加调查、重试或结论修订次数。
- `lost_child_results`：已完成但未被 Manager 最终上下文引用的必要子结果数。

### 10.4 稳定性

- `success_rate`：有效运行中 `primary_accuracy=1` 且全部治理硬门槛通过的比例。
- 每组报告中位数、最小值和最大值；样本量达到 20 后再报告 P95。
- 第一阶段默认 `5 个场景 × 每种模式 5 次 = 每组 25 次`。

### 10.5 治理硬门槛

出现以下任一情况，本次运行记为失败，不被平均分掩盖：

- 未授权 Tool 或信号访问；
- Tool 绕过 Gateway；
- 必要 Tool 请求缺少审计；
- 最终引用伪造或跨场景证据；
- 必要子结果丢失；
- 根任务未进入合法终态；
- 超过步骤预算、死锁或超时。

## 11. 时间测量边界

UI 为演示可配置的慢速轮询不属于模型或协作能力，不能直接作为基准耗时。Benchmark 使用固定、最小化的后台轮询设置，并以服务端运行起止时间计量。

报告同时保留：

- `end_to_end_ms`：包含 Manager、Matrix、Gateway 和持久化开销；
- 节点 `started_at/finished_at`：用于还原并行执行和关键路径；
- Tool 与模型调用时间：用于定位耗时来源。

不得拿慢速演示脚本的 5 秒展示间隔与 Single Agent 直接比较。

## 12. 持久化与接口

新增独立实验记录，避免把 25 次以上运行结果塞进单个 `AgentTaskEvent.detail`：

```text
BenchmarkExperiment
- id
- scenario_version
- model_config_fingerprint
- repetitions
- status
- created_at / finished_at

BenchmarkRun
- id
- experiment_id
- scenario_id
- mode
- repetition
- root_task_id
- status
- metrics
- diagnosis
- failure_reasons
```

接口职责：

```text
POST /agentteams-benchmarks/database/experiments
GET  /agentteams-benchmarks/database/experiments/{id}
GET  /agentteams-benchmarks/database/experiments/{id}/runs
```

创建实验时验证模型、场景、重复次数和预算。实验异步执行，前端轮询实验状态；单次失败被记录，不中止其他场景运行。

## 13. 前端结果

新评测面板独立于旧百分制面板，展示：

1. Single Agent 与 AgentTeams 的成功率、准确率、中位耗时；
2. 总模型步骤与关键路径步骤；
3. Tool 调用、重复调用与返工；
4. 每个场景、每次重复运行的明细；
5. 失败硬门槛与证据定位；
6. 点击 AgentTeams 运行可回到现有任务树和节点过程详情。

前端不预填虚构结果。实验未完成时展示已完成运行数，只有真实运行完成后才显示汇总。

## 14. 错误与中断

- 场景文件缺失或校验失败：实验创建失败，不启动任何运行。
- 单次模型、Matrix 或 Tool 失败：记录该运行失败原因，继续剩余运行。
- 用户取消：停止派发新运行，允许已经进入不可中断段的调用结束，实验标为 `cancelled`。
- 服务重启：从持久化实验和运行状态恢复；不重复执行已有终态运行。
- 标准答案泄漏检测失败：场景标为不可用并停止使用。

## 15. 测试策略

### 单元测试

- fixture Schema 和标准答案隔离；
- Tool signal 白名单、Agent 授权和跨场景拒绝；
- 根因、证据、步骤、重复调用和关键路径计算；
- 硬门槛优先于聚合结果；
- Single Agent 禁止委派。

### 集成测试

- 同一 fixture 可分别完成 Single Agent 和 AgentTeams 运行；
- 所有数据库 Tool 调用经过 Gateway 并产生 Audit；
- 并行节点的关键路径不错误累加；
- 单次失败不终止整个实验；
- 服务重启不重复终态运行。

### 前端测试

- A/B 指标正确映射；
- 运行中、部分失败、取消和完成状态；
- 不显示未完成的虚构汇总；
- 可从运行明细定位 AgentTeams 任务树。

### 验证命令

实现阶段执行与改动相关的后端测试、前端测试、前端类型检查，并在最终完成前运行项目可用的全量测试。任何既有基线失败与新增回归失败分开报告。

## 16. 验收标准

- 可从页面创建包含 5 个场景、Single Agent 与 AgentTeams、每种模式各 5 次的实验。
- 两种模式使用相同模型、工具、数据、预算和输出 Schema。
- Ground truth 不出现在任何 Agent 可见输入或 Tool 输出。
- AgentTeams 的任务树来自真实委派、消息、Tool 和结果事件。
- 每次运行产生准确率、耗时、模型步骤、关键路径、Tool 调用和返工数据。
- 汇总页能横向比较两种模式，并下钻到失败运行证据。
- 未授权访问、缺审计、伪造证据、丢失必要子结果和非法终态均直接判失败。
- MARBLE 来源和 MIT 许可信息在仓库内可见。
- 不修改 `feature/personal-memory` 主目录，不自动 push。

