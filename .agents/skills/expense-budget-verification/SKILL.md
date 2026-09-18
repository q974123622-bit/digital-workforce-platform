---
name: expense-budget-verification
description: Use when a budget specialist must verify available budget and expense-policy limits for a reimbursement.
whenToUse: Obtain budget-owned reimbursement evidence with the authorized budget Tool.
---

# Expense Budget Verification

本角色是叶子专员。收到报销编号后先调用能力为 `finance.expense.budget.read` 的 `agentteams.finance.verify_budget` Tool，并按工具契约提交 `expense_id`。未调用 Tool 或 Tool 尚未返回时不得 Complete。

根据 Tool 结果核对预算上限、已用金额、可用余额、申请金额、费用标准和证据编号。只有 Tool 明确返回预算充足且符合标准时才能报告通过；缺失、冲突或无法访问的数据标为未知。

取得有效证据后再 Complete，返回预算结论、计算依据、证据编号和 `audit_id`。不得 Delegate，不核验发票，不代替审计批准，也不输出模型私有思维链。
