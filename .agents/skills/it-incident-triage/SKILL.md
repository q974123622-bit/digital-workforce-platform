---
name: it-incident-triage
description: Triage fictional IT incidents, assess the affected scope, and request IT collaboration when appropriate.
whenToUse: Use when service status, IT knowledge, and optional collaboration form a stable multi-step incident triage flow.
---
description: 当用户需要排查虚构 IT 故障事件、判断影响范围或请求 IT 协作时使用。
whenToUse: 任务需要按服务状态、IT 知识和可选协作进行稳定的多步骤事件分诊时使用。
---

# IT 事件分诊

若当前 Runtime 暴露正式工具 `triage_it_incident`，则优先使用它。服务状态必须来自状态工具，不能由模型凭感觉判断；该工作流不执行真实修复。

- Allow：说明服务健康度、知识依据和分诊提示。
- Deny：停止访问并说明原因；Approval：等待审批。
- Empty：说明服务未找到；Error：说明工具失败。

不得编造监控数据、事故结论、生产操作或员工权限。最终区分检索事实、结构化证据、推断、未确认项。
