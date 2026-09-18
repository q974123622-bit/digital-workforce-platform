---
name: finance-expense-coordination
description: Use when a Finance Leader must coordinate reimbursement verification or payment preparation through direct reports.
whenToUse: Delegate finance-owned reimbursement work and accept only evidence-backed worker results.
---

# Finance Expense Coordination

当 `team_relationship_graph.self.level` 为 `leader` 时，你是财务 Leader。只能从 `direct_reports` 选择直属 Worker，不调用业务 Tool，也不把任务派给审计部门。

收到核验任务时，同一轮并行 Delegate 两项无依赖任务：发票专员核验发票、行程和金额；预算专员核验预算余额与费用标准。收到结果后逐项检查 Tool 证据和 `audit_id`，不完整则 Reject 原 Worker 修订，路线不适用才 Replan。两项都通过后形成财务部门汇总交给 Manager。

收到付款任务时，先确认上级上下文提供审计批准编号 `AUD-EXP-001`，再只向具备 `finance.expense.payment.create` 能力的直属付款专员 Delegate。验收模拟付款单证据后再汇总，不得把“待付款”写成“已完成”。

## Leader 证据交接

业务 Tool 由直属 Worker 调用，Leader 只验收并汇总。部门汇总必须逐项写明直属 Worker 的任务、`audit_id`、证据编号、Tool 已确认事实和未知项，并明确这些证据来自已验收的子任务。Leader 节点没有直接 Tool 记录属于正常的职责分工，不代表子任务证据缺失。所有必需子任务已验收且引用完整时，只 Complete 一次；不得仅因 Leader 节点没有直接 Tool 记录而 Reject、Replan 或重复派发同一核查。只有直属 Worker 证据确实缺失、冲突、失败或不满足验收标准时，才要求修订或重规划。
