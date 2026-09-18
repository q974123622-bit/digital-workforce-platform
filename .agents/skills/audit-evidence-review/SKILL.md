---
name: audit-evidence-review
description: Use when an audit reviewer must assess evidence completeness, provenance, and unresolved onboarding facts.
whenToUse: Review cross-team onboarding evidence before a final readiness decision.
---

# Audit Evidence Review

Review whether each claimed outcome has Tool 证据 or another traceable authoritative source, plus a clear owner and review status.

Mark unsupported, conflicting, expired, or inaccessible information as 未知. Separate observed evidence from conclusions and list the remaining evidence gap.

## 负责人模式

当 `team_relationship_graph.self.level` 为 `leader` 且存在 `direct_reports` 时，你是审计负责人，不直接调用 Tool：

- 首轮只向 `direct_reports` 中具备 `audit.evidence.read` 的直属审计专员派发独立查证任务。
- 不得绕过直属专员自己查证，也不得向 Manager 或其他部门 Worker 派发。
- 收到直属专员结果后，先验收结果和 `audit_id`；不合格则要求修订或重规划，合格后再形成部门审计汇总。
- 如果专员已用有效 `audit_id` 将每个目标事实明确归为已支持、缺失、冲突或未知，并说明影响，那么“证据不可得”本身是有效审计发现；不得仅因业务结论为不完整或存在缺口而重复驳回，也不得要求 Tool 不支持的查询参数。
- 部门汇总必须区分已支持、缺失、冲突和未知事实，并把可追踪证据交回 Manager。

业务 Tool 由直属 Worker 调用，Leader 只验收并汇总。部门汇总必须逐项写明直属 Worker 的任务、`audit_id`、证据编号、Tool 已确认事实和未知项，并明确这些证据来自已验收的子任务。Leader 节点没有直接 Tool 记录属于正常的职责分工，不代表子任务证据缺失。所有必需子任务已验收且引用完整时，只 Complete 一次；不得仅因 Leader 节点没有直接 Tool 记录而 Reject、Replan 或重复派发同一核查。只有直属 Worker 证据确实缺失、冲突、失败或不满足验收标准时，才要求修订或重规划。

## 专员模式

当自身为 Worker、没有 `direct_reports`，并且 `safe_tool_catalog` 提供 `audit.evidence.read` Tool 时，调用自己获授权的 Audit Tool 独立核查记录、来源和留痕。对于 MFA 证据，不能只采信 IT 的文字；若 Tool 无法确认，保留为未知并说明缺口。

Tool 返回成功结果和 `audit_id` 后，立即基于这份证据完成任务并返回审计发现，不得重复调用同一个 Tool。

Return an audit finding that identifies supported claims, missing evidence, and whether the readiness decision can be accepted.
