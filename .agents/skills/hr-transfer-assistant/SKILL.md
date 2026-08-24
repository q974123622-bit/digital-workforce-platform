---
name: hr-transfer-assistant
description: Review fictional employee transfer or role-adjustment materials and unresolved items.
whenToUse: Use when a stable flow must read a transfer document, find the employee, retrieve HR policy, and optionally collaborate.
---
description: 当用户需要核查虚构员工转岗、岗位调整材料或待确认事项时使用。
whenToUse: 需要稳定地读取材料、匹配员工、检索 HR 制度并可选请求 HR 协作时使用。
---

# HR 岗位调整核查

若当前 Runtime 暴露正式工具 `review_hr_transfer`，则优先使用它；否则只在正式工具存在时分别使用 `read_document`、`search_knowledge` 与协作工具。

- Allow：汇总材料、制度和确认节点。
- Deny：如实说明权限拒绝，不改换身份。
- Approval：说明需要人工审批；Empty：说明材料或证据缺失；Error：说明读取失败。

不得批准/拒绝转岗，不得编造真实人事信息或 Policy。最终区分检索事实、结构化证据、推断、未确认项。
