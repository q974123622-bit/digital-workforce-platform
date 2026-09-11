---
name: audit-role-change-review
description: Use when audit must independently review a role-change permission plan.
whenToUse: Verify permission scope and separation-of-duties before IT activation.
---

# Audit Role Change Review

调用获授权的岗位调整审计Tool，独立核查拟撤销和拟新增权限是否与目标岗位匹配，以及职责分离检查是否通过。不能只采信IT Agent的文字结论。

审核通过时，必须在中文结果中保留Tool返回的批准编号`AUD-RC-001`和证据编号，让后续依赖任务能够从`dependency_results`取得`approval_ref`。审核不通过或证据缺失时，明确保留风险，不得产生批准编号。
