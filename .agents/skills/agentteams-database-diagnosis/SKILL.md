---
name: agentteams-database-diagnosis
description: Use when diagnosing one bounded database benchmark scenario with audit-backed evidence.
whenToUse: Compare Single Agent and AgentTeams on the same deterministic database observations and budget.
---

# Database Diagnosis Benchmark

只诊断任务给定的 `scenario_id`。观测必须调用获授权的 `inspect_database_signal` Tool；不得读取文件路径、隐藏答案文件、其他场景或生产数据库。

## 证据规则

每条事实必须引用 Tool 返回的 `evidence_id`。最终结果包含 `root_cause_id`、按置信度排序的 `top_candidates`、去重后的 `evidence_ids`、中文 `conclusion` 和 `confidence`。没有 Tool 证据时不得完成；观察不到的信息标为未知。

## AgentTeams 模式

Manager 根据缺少的证据把写入、锁、清理、索引和查询分析分给对应能力的 Worker。可并行的任务不得制造虚假依赖；收到所有 required child result 后才能汇总。若已有相同 signal 的有效结果，不要重复委派。

## Single Agent 模式

Single Agent 使用完全相同的 Skill、Tool schema、场景快照、模型配置、步骤与时间预算，但不能 Delegate、Ask 或创建子任务。它只能选择 Tool 或最终诊断。

## 停止条件

达到模型步数、Tool 次数或超时预算时立即失败。越权 signal、绕过 Gateway、缺少 allow audit、跨场景 evidence_id、丢失 required child result 或非法终态均视为 hard-gate failure。
