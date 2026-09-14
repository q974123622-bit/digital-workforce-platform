---
name: agentteams-onboarding-readiness
description: Use when coordinating employee onboarding readiness across HR, IT, and audit evidence.
whenToUse: Assess whether onboarding has the evidence needed for a completion decision.
---

# Onboarding Readiness

把用户的入职目标拆成 HR 材料、HR 培训、IT 账号、IT 设备和独立审计五类证据类别。所有结论都必须来自子任务返回的 Tool 证据；没有证据的事实保留为未知，不得根据职位、任务标题或自然语言说明补全。

## 首轮：并行派发部门负责人

Manager 大模型必须在同一批次派发两个必需且无依赖的直接子任务：

- 从当前 `team_relationship_graph.direct_reports` 中选择具备 `hr.onboarding.coordinate` 的 HR 负责人，要求其组织 HR 材料与培训核查并返回部门汇总。
- 从当前 `team_relationship_graph.direct_reports` 中选择具备 `it.onboarding.coordinate` 的 IT 负责人，要求其组织账号与设备核查并返回部门汇总。

两个子任务的 `depends_on` 都为空。首轮不得派发 Audit，也不得把四名专员直接提升为 Manager 的子任务。不要预设或编造 Agent ID；只能根据当前关系图和能力目录选择负责人。

## 第二轮：Manager 独立审计

等待 HR 与 IT 两个部门汇总都出现在 `child_results` 后，Manager 大模型先核对四类一手证据及未知项，再开启第二轮。第二轮只派发一个 `audit.evidence.read` 子任务，由 Manager 从自己的 `team_relationship_graph.direct_reports` 中选择具备该能力的审计负责人，并把两个部门汇总及其中的证据编号作为审计输入。

审计是 Manager 负责的二次复核，不得由 HR 或 IT 负责人嵌套派发。相同的两个部门汇总已经送审时不得重复派发审计。审计子任务完成前不得进入补查或总结。

## 第三轮：按缺口定向补查

审计返回后，Manager 必须依据已经返回的部门证据和审计结论开启一次定向补查。只选择确有证据缺口或证据冲突的 HR 或 IT 部门负责人；不得无依据同时重跑所有部门，也不得重复派发审计。每个补查子任务必须说明要解决的具体缺口，并由对应部门负责人继续组织自己的直属专员取得新证据。

第三轮至少派发一个必需且无依赖的部门补查任务。只能选择 `hr.onboarding.coordinate` 或 `it.onboarding.coordinate`，不得把直属专员提升为 Manager 的直接子任务。第三轮补查结果返回前不得形成最终结论。

## 第四轮：最终汇总

等待第三轮定向补查完成后，Manager 才能结合首轮部门证据、第二轮独立审计和第三轮补查结果形成最终汇总。剩余缺口必须如实保留，不能因为已经达到第四轮而把未知项写成已完成。

## 完成条件

只有 HR 材料、HR 培训、IT 账号、IT 设备四类部门证据、审计结论和第三轮定向补查结果都已返回，才满足完成条件。最终结构化状态使用 ready、not ready 或 incomplete；面向用户的总结使用中文，列出已完成项、风险、未知项和下一步负责人。

只输出可追踪的结构化动作、简要业务依据与最终证据摘要。不得输出、补写或伪造模型的私有思维链。
