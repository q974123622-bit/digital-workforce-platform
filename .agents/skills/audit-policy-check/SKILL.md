---
name: audit-policy-check
description: Retrieve fictional audit procedures, internal policy, and external rule requirements.
whenToUse: Use when an answer needs governed evidence from audit-procedure, internal-policy, or external-rule knowledge bases.
---
description: 当用户查询虚构审计依据、审计程序、内部制度或外部规则要求时使用。
whenToUse: 需要从审计程序、内部制度或外部规则知识库检索依据后再回答时使用。
---

# 审计依据查询

使用当前 Runtime 可用的 `search_knowledge`，优先查询 `KB-AUDIT-PROCEDURE`、`KB-REG-INTERNAL` 或 `KB-REG-EXTERNAL`。

- Allow：引用检索到的依据；Deny：说明无权访问且不绕过。
- Approval：说明需要审批；Empty：说明资料不足；Error：说明检索失败。

不得编造审计准则、正式合规/违规结论、敏感业务事实或权限。最终区分检索事实、结构化依据、推断、未确认项。
