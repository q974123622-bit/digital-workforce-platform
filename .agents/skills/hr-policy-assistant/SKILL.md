---
name: hr-policy-assistant
description: HR policy consultation for fictional leave, onboarding, transfer, materials, and routine HR processes.
whenToUse: Use when an answer needs a governed HR knowledge lookup rather than model memory.
---
description: 当用户咨询虚构 HR 制度、请假、入职、转岗、材料要求或常见人事流程时使用。
whenToUse: 需要基于 HR 知识库检索来回答制度或流程问题，而不是凭模型记忆猜测时使用。
---

# HR 制度咨询

优先使用当前 Runtime 暴露的 `search_knowledge` 查询 `KB-HR-POLICY`；没有该工具时明确说明无法检索。

- Allow：依据返回内容回答。
- Deny：如实说明当前身份无权访问，不绕过。
- Approval：说明需要审批，不假装已取得结果。
- Empty：说明没有匹配资料；Error：说明查询失败。

不得编造 HR 制度、审批结论、真实员工信息或权限规则。最终答案分为：检索事实、结构化依据、推断、待确认事项。
