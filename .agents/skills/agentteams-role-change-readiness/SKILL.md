---
name: agentteams-role-change-readiness
description: Use when a Manager must evaluate whether an employee role change can complete on schedule.
whenToUse: Coordinate HR, IT, and audit evidence for a role-change readiness decision.
---

# Role Change Readiness

把用户的简短目标转换为分阶段证据核查，但不要预先伪造结论。

## 第一轮：取得一手证据

Manager必须在同一批`task.delegate`中并行派发两个无依赖任务：HR核查岗位调整审批、目标岗位和生效日期；IT核查需要撤销和新增的权限，并确认账号是否具备变更条件。两项任务的`depends_on`都为空。

## Manager复核

第一轮两个Worker都返回后，Manager再次读取直接子任务结果。只要IT证据表明权限激活仍需独立审计，就不得直接汇总为完成，必须开启第二轮。

## 第二轮：先审计，后激活

第二轮仍由Manager派发两个直接子任务：审计Agent独立复核权限范围；IT Agent执行最终权限激活。IT激活任务的`depends_on`必须引用本轮审计任务的`client_task_key`，因此平台会先保持IT任务为blocked，审计完成后才释放。

审计结果必须给出`approval_ref`。IT激活Tool必须从依赖结果读取并提交该值；本演示的批准编号为`AUD-RC-001`。没有这个编号不得激活。

## 完成条件

只有HR调整材料完整、IT变更计划明确、独立审计通过、最终权限激活完成四类证据都存在时，Manager才以中文输出成功结论，并列出已完成项、风险、信息缺口和后续操作。最终权限激活的“已完成”证据优先于第一轮的“待审计复核”中间状态；前一轮只表示当时尚未执行，不表示最终仍有缺口。不得因为状态文字而跳过Tool证据。

最终结论的叙述字段必须使用中文状态词，例如“已就绪”“已批准”“已完成”“待审计复核”；`ready`只允许出现在结构化的`readiness_decision`字段中。
