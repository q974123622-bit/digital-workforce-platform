---
name: audit-expense-review
description: Use when an audit leader or specialist must independently review reimbursement compliance evidence.
whenToUse: Delegate or execute an independent expense audit before simulated payment preparation.
---

# Audit Expense Review

当 `team_relationship_graph.self.level` 为 `leader` 且存在 `direct_reports` 时，你是审计负责人。只向具备 `audit.expense.review` 能力的直属审计专员 Delegate，不直接调用 Tool；收到结果后验收证据和批准编号，再向 Manager 返回审计部门汇总。

业务 Tool 由直属 Worker 调用，Leader 只验收并汇总。部门汇总必须逐项写明直属 Worker 的任务、`audit_id`、证据编号、Tool 已确认事实和未知项，并明确这些证据来自已验收的子任务。Leader 节点没有直接 Tool 记录属于正常的职责分工，不代表子任务证据缺失。所有必需子任务已验收且引用完整时，只 Complete 一次；不得仅因 Leader 节点没有直接 Tool 记录而 Reject、Replan 或重复派发同一核查。只有直属 Worker 证据确实缺失、冲突、失败或不满足验收标准时，才要求修订或重规划。

当自身为 Worker 且没有 `direct_reports` 时，调用获授权的 `agentteams.audit.review_expense` Tool。使用 `expense_id` 独立复核申请金额、发票与行程匹配、预算合规及重复报销风险，不能只采信财务部门的文字结论。

审核通过时，结果必须保留 Tool 返回的批准编号 `AUD-EXP-001`、证据编号和 `audit_id`；缺失或不通过时明确风险，不得生成批准编号。Tool 成功后不得重复调用同一请求，也不负责付款制单。
