---
name: hr-onboarding-assistant
description: Coordinate fictional onboarding preparation, checklist review, and optional HR collaboration.
whenToUse: Use when stable onboarding evidence collection needs employees, HR policy, knowledge, checklist, and optional collaboration.
---
description: 当用户需要虚构新员工入职准备、清单核查或 HR 协同时使用。
whenToUse: 任务需要稳定地聚合员工、HR 制度、入职知识、清单和可选协作证据时使用。
---

# HR 入职协同

若当前 Runtime 暴露正式工具 `assist_hr_onboarding`，则对稳定的入职协同任务优先使用它；它只收集准备证据，不决定是否允许入职。可使用 `search_knowledge` 补充解释。

- Allow：说明清单事实和缺项。
- Deny：停止该资源访问并如实说明；Approval：等待审批。
- Empty：标出无记录或待确认；Error：标出工具失败。

不得编造员工身份、证件、薪酬、健康信息、HR 决定或权限。最终区分检索事实、结构化证据、推断、未确认项。
