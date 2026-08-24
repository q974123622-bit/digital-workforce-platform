---
name: audit-trace-review
description: Review fictional audit traces for operations, plugins, and workflows visible to the current subject.
whenToUse: Use when trace ID, plugin, decision, or limit filters are needed for safe audit-event review.
---
description: 当用户需要查看当前主体可见的虚构操作、插件或工作流审计 Trace 时使用。
whenToUse: 需要基于 Trace ID、插件、决策或数量限制查询平台审计事件时使用。
---

# 审计 Trace 核查

若当前 Runtime 暴露 `query_audit_events`，则优先使用。不得用任意 `employee_id` 查询其他主体的审计记录；工具治理边界优先于 Skill。

- Allow：按事件顺序说明 Trace、插件、动作和决策。
- Deny：说明权限拒绝；Approval：等待审批。
- Empty：说明没有匹配事件；Error：说明查询失败。

不得输出 Token、完整 Prompt、秘密信息、敏感结果摘要或正式审计结论。最终区分检索事实、结构化 Trace、推断、未确认项。
