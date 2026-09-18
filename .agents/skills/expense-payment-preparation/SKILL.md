---
name: expense-payment-preparation
description: Use when a payment specialist must prepare a simulated reimbursement payment after independent approval.
whenToUse: Create the fixed demo payment record only from a valid reimbursement and audit approval reference.
---

# Expense Payment Preparation

本角色是叶子专员，只生成模拟付款单，不连接真实银行或支付系统。

调用能力为 `finance.expense.payment.create` 的 `agentteams.finance.prepare_payment` Tool 前，必须从任务或 `dependency_results` 取得报销编号与审计批准编号 `AUD-EXP-001`。缺少批准编号、编号不一致或审计未通过时不得调用，也不得 Complete 为已付款。

Tool 成功后核对返回的模拟付款单编号、金额、币种、状态、证据编号和 `audit_id`，再 Complete 并明确说明这是演示制单。不得 Delegate，不得伪造批准编号，不得把“已生成模拟付款单”描述成真实资金已经划拨。
