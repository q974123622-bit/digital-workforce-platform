---
name: audit-evidence-review
description: Collect and review fictional audit evidence from materials, policy, work records, and platform traces.
whenToUse: Use when a stable workflow must combine documents, audit procedure, internal policy, records, events, and optional collaboration.
---
description: 当用户需要收集和核查虚构审计证据、材料、制度、工作记录与平台 Trace 时使用。
whenToUse: 任务需要稳定地组合文档、审计程序、内部制度、工作记录、审计事件及可选协作时使用。
---

# 审计证据复核

若当前 Runtime 暴露正式工具 `review_audit_evidence`，则优先使用。它只聚合证据和缺口，不输出正式审计、合规或违规结论。

- Allow：分别呈现材料、依据、记录、Trace 和证据缺口。
- Deny：停止并说明权限原因；Approval：说明等待审批。
- Empty：说明缺少材料或事件；Error：说明工具失败。

不得编造 Trace、审批记录、调查结论、Token、Prompt、Secrets 或敏感结果摘要。最终区分检索事实、结构化证据、推断、未确认项。
