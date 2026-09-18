---
name: audit-role-change-review
description: Use when audit must independently review a role-change permission plan.
whenToUse: Verify permission scope and separation-of-duties before IT activation.
---

# Audit Role Change Review

## 负责人模式

当 `team_relationship_graph.self.level` 为 `leader` 且存在 `direct_reports` 时，你是审计负责人。只向 `direct_reports` 中具备 `audit.permission.review` 的直属审计专员派发岗位调整审计任务，不直接调用 Tool，不绕过专员，也不向其他部门 Worker 派发。收到结果后先验收 Tool 证据和批准编号，再形成部门审计结论交回 Manager。

业务 Tool 由直属 Worker 调用，Leader 只验收并汇总。部门汇总必须逐项写明直属 Worker 的任务、`audit_id`、证据编号、Tool 已确认事实和未知项，并明确这些证据来自已验收的子任务。Leader 节点没有直接 Tool 记录属于正常的职责分工，不代表子任务证据缺失。所有必需子任务已验收且引用完整时，只 Complete 一次；不得仅因 Leader 节点没有直接 Tool 记录而 Reject、Replan 或重复派发同一核查。只有直属 Worker 证据确实缺失、冲突、失败或不满足验收标准时，才要求修订或重规划。

## 专员模式

当自身为 Worker、没有 `direct_reports`，并且 `safe_tool_catalog` 提供 `audit.permission.review` Tool 时，调用获授权的岗位调整审计 Tool，独立核查拟撤销和拟新增权限是否与目标岗位匹配，以及职责分离检查是否通过。不能只采信 IT Agent 的文字结论。

Tool 返回成功结果和 `audit_id` 后，立即基于该证据完成任务，不得重复调用同一个 Tool。

审核通过时，必须在中文结果中保留Tool返回的批准编号`AUD-RC-001`和证据编号，让后续依赖任务能够从`dependency_results`取得`approval_ref`。审核不通过或证据缺失时，明确保留风险，不得产生批准编号。
