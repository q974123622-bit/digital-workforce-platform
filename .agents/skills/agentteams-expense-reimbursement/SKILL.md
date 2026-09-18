---
name: agentteams-expense-reimbursement
description: Use when a Manager must coordinate invoice, budget, audit, and payment evidence for an expense reimbursement.
whenToUse: Coordinate a fixed reimbursement request through Finance and independent Audit leaders.
---

# Expense Reimbursement

将报销任务交给团队 Leader，不得由 Manager 绕过负责人直接派给 Worker。

第一轮并行派发两个无依赖的 Leader 任务：财务负责人核验报销单的发票、行程、金额和预算；审计负责人独立核验报销合规性。财务负责人必须再向直属发票专员和预算专员拆分，审计负责人必须向直属审计专员拆分。Manager 只接收两个 Leader 的部门汇总。

审计汇总必须包含 Tool 返回的批准编号 `AUD-EXP-001`。只有财务核验合格、审计批准且证据可追踪时，Manager 才向财务负责人派发第二轮付款任务，并在任务中带上该批准编号。财务负责人再将付款制单派给直属付款专员。

付款仅生成模拟付款单，不连接真实支付系统。最终结果必须列出报销编号、发票与预算结论、审计批准编号、模拟付款单编号、风险与信息缺口；任何关键证据缺失时都不得宣称付款完成。

## 最终判定口径

以下四项是本演示场景唯一的阻断性证据：财务 Tool 已确认发票、行程和申报金额一致；预算 Tool 返回 `within_limit`；审计 Tool 返回批准编号 `AUD-EXP-001` 且重复报销检查为 `clear`；付款 Tool 返回模拟付款单编号且状态为 `simulated_created`。四项均有证据编号和 `audit_id` 时，结论必须判定为通过，并明确“模拟付款单已生成，但不等于真实付款完成”。

Tool 契约没有返回的发票代码、发票号码、购销方名称、发票张数、税额、成本中心适用明细、审批层级明细、收款方名称、收款账户和制单时间，属于非阻断补充项，可以写入信息缺口或后续操作，但不得仅因这些字段未返回而判定“信息不足、暂无法判断”，也不得触发返工。只有上述阻断性证据缺失、冲突或明确失败时，才判定信息不足或不通过。
