# AgentTeams 严格三层协作设计

## 目标

所有新建 AgentTeams 任务统一采用真实的 `Manager → Leader → Worker` 组织关系。前端只展示数据库中实际创建和执行的节点，不补画虚拟节点；历史任务保持原样。

## 身份与职责

- Manager：根任务负责人，只能把部门任务派给直属 Leader，验收 Leader 汇总并决定下一轮或最终汇总。
- Leader：接收一个边界明确的部门任务，只能派给自己的直属 Worker，验收 Worker 结果后形成部门汇总。
- Worker：执行叶子业务任务和受控 Tool 调用，不允许继续派发。

团队成员表中的 `reports_to_employee_id` 和 `capabilities` 是组织关系与协调能力的事实来源。严格 Matrix 运行配置用于确认身份和消息通道，但不能扩大数据库定义的关系。

## 运行规则

1. Manager 的计划必须整批通过人员、能力、依赖、深度、节点数和轮次校验，之后才创建任务。
2. Leader 收到任务后，大模型根据自己的关系图和直属 Worker 能力生成第二层计划。
3. Worker 提交结果后先进入验收；Leader 接受后才完成节点并解锁汇总。
4. Leader 汇总也由 Manager 验收。驳回时保留原结果并创建可追踪修订任务。
5. 负责人协调能力是组织能力，不按插件权限过滤；Worker 的 Tool 能力继续经过个人、团队和全局权限裁决。
6. 默认允许初始轮后最多五次 Manager 追加轮次，同时继续受最大深度、总节点数和单轮子任务数限制。

## 场景拓扑

TEAM-ONBOARD 使用一个 Manager、HR/IT/审计三个 Leaders，以及 HR 材料、HR 培训、IT 账号、IT 设备、审计复核五个 Workers。岗位调整复用同一组织树；数据库团队使用运行诊断和性能诊断两个 Leaders，下辖五个数据库 Workers。

## 前端

协作调用树递归渲染持久化父子节点，展示 Manager 任务、Leader 任务、Worker 任务及其 Tool 结果。协作页不再展示评测面板。

## 验收

- 本地 Demo 与真实 Matrix 形态都必须出现实际三层节点和二轮派发事件。
- Manager 不得越过 Leader，Leader 不得跨部门派发，Worker 不得派发。
- 审计链路必须为 `Manager → 审计 Leader → 审计 Worker`。
- 后端全量测试、前端测试和生产构建通过。
