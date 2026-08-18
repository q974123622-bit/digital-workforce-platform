---
name: employee-collaboration
description: 当数字员工在当前任务中需要其他数字员工的信息、专业意见或协助时使用本技能。
whenToUse: 任务需要定位他人、咨询专业意见、委托子任务或把任务转交给他人时使用；自己可以独立完成时不使用。
---

# 数字员工协作（employee-collaboration）

本技能只规定「如何协作」，不实现平台员工路由。是否真的能查询、对话、委托或转交，取决于当前 Runtime 提供的协作 Tool；权限判定仍由运行时统一治理链（Identity → Policy Engine → Plugin Gateway）负责。

## 执行流程

1. 先判断是否真的需要协作。当前员工自己能够完成时，不无意义调用其他员工。

2. 确定需要协作后，明确需要什么能力、岗位或员工，再使用当前 Runtime 实际可用的员工查询/协作 Tool。不要假定 `ask_employee`、`delegate_task`、`handoff` 等 Tool 一定存在。

3. 环境提供哪些能力，就按任务需要选择：
   - employee search：查找合适的协作对象；
   - employee chat：向目标员工询问；
   - delegate：把明确子任务交给对方完成；
   - handoff：当前员工不再继续承担该任务时转交。

   若环境中暴露正式工具名为 `collaborate_employee`，则使用它，参数为 target_employee_id、action（ask / delegate / handoff）、request。

4. 严格区分三种协作方式：
   - Ask：只需要信息或专业意见；
   - Delegate：把明确子任务交给对方完成，当前员工仍对整体结果负责；
   - Handoff：当前员工不再继续承担该任务时才转交。

5. 返回结果必须保留来源：
   - 目标数字员工名称或 employee_no（如果 Tool 返回）；
   - 实际执行结果；
   - 拒绝或失败原因。

6. 目标员工返回 Policy Denied 时，不得通过切换员工、更换 Tool、改写请求等方式绕过权限。

7. 目标员工不存在或不可用时，明确说明协作失败，不得虚构对方的回答。

8. 防止循环：如果检测到同一任务已经在当前协作链中经过目标员工，不得再次形成 A→B→A 或重复委托；应立即停止并说明原因。

9. 本技能只规定协作方法，不负责员工路由、身份绑定或权限评估。
