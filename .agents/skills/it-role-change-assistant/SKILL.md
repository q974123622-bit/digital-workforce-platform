---
name: it-role-change-assistant
description: Use when IT must plan or activate access changes for an employee role change.
whenToUse: Inspect permission changes and perform approved activation after audit.
---

# IT Role Change Assistant

根据任务目标选择自己获授权的Tool：第一轮调用权限计划查询Tool，返回要撤销、要新增的权限、账号状态和是否需要独立审计；不得把“已提出申请”当作“已激活”。

第二轮只有在任务依赖中已经收到审计结果时，才调用最终激活Tool。从`dependency_results`里的审计结论提取`approval_ref`，本演示必须是`AUD-RC-001`，再连同`employee_ref`提交。缺少或不匹配时不得伪造。

激活成功后，用中文返回实际撤销和新增的权限、证据编号和生效日期，使Manager能够完成最终汇总。

## Leader 证据交接

当自身是负责人并存在 `direct_reports` 时，业务 Tool 由直属 Worker 调用，Leader 只验收并汇总。部门汇总必须逐项写明直属 Worker 的任务、`audit_id`、证据编号、Tool 已确认事实和未知项，并明确这些证据来自已验收的子任务。Leader 节点没有直接 Tool 记录属于正常的职责分工，不代表子任务证据缺失。所有必需子任务已验收且引用完整时，只 Complete 一次；不得仅因 Leader 节点没有直接 Tool 记录而 Reject、Replan 或重复派发同一核查。只有直属 Worker 证据确实缺失、冲突、失败或不满足验收标准时，才要求修订或重规划。
