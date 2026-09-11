# AgentTeams Matrix 与本地多轮协作对齐设计

## 目标

让 `TEAM-ONBOARD` 的岗位调整演示在 `local_demo` 与 `agentteams_strict` 两种运行模式下呈现相同的业务轮次：Manager 第一轮并行派发 HR 与 IT 计划核查，回收结果后第二轮派发审计与 IT 激活，最后形成总结。Matrix 继续负责真实消息传输和身份校验，本地模式继续使用无网络的确定性传输。

## 当前事实

- Matrix Runtime 已实现 `task.summary` 复核、`task.delegate` 后续派发和 `round=2` 状态递增。
- 岗位调整 Skill、四个 Tool、固定脱敏证据、Matrix 账号配置和三层插件权限已经存在。
- `local_demo` 仍使用旧的单轮入职脚本，根任务收到汇总请求后直接返回固定总结。
- 前端 Manager 详情能够显示第二轮，但 Worker 详情忽略 `task.delegate`，因此嵌套委派不可见。

## 设计

### 共享场景契约

新增一个纯数据模块维护岗位调整场景的稳定标识、标题、目标、Skill ID、两轮能力集合、固定员工引用和审计批准引用。Matrix 决策验证与本地确定性传输都从这里读取规则，避免分别手写第二轮能力集合。

共享的是业务契约，不共享传输实现：

```text
岗位调整场景契约
        │
        ├── Matrix AgentRuntime + Matrix Transport
        └── Local deterministic runtime + Local Transport
                     │
              同一 CollaborationService
```

### 本地确定性运行

本地模式继续通过现有 `DWP_EVENT` 协议产生真实任务节点和 Gateway 调用，不直接修改数据库任务状态。它按以下顺序响应：

1. 根任务提交：记录场景 Skill 与安全的决策摘要，派发 HR 证据和 IT 权限计划，`round=1`。
2. Worker 接单：记录 Skill 与安全的决策摘要，通过 Gateway 调用对应岗位调整 Tool。
3. 第一轮完成：平台向根 Manager 发出 `task.summary` 请求；本地 Manager 返回新的 `task.delegate`，派发审计与 IT 激活，`round=2`。IT 激活依赖审计任务。
4. 审计完成：平台释放 IT 激活任务；激活请求从依赖结果中读取 `approval_ref`，不能自行扩大参数。
5. 第二轮完成：本地 Manager 返回结构化最终总结，根任务完成。

`decision.trace` 只保存可展示的决策摘要，不伪造或暴露模型私有思维链。

### Matrix 运行

不修改 Matrix 的传输、账号登录、房间、反伪造和多轮状态机。Matrix Manager 仍由真实模型根据同一岗位调整契约生成计划，并由后端验证：

- 第一轮必须是 HR 与 IT 计划的并行任务；
- 第二轮必须是审计与 IT 激活，且激活依赖审计；
- 最终总结必须建立在激活完成证据上。

### 三层权限

所有 Tool 调用继续经过统一权限解析与 Gateway：

```text
全局策略 → TEAM-ONBOARD 团队授权 → 当前数字分身个人授权 → Gateway
```

本次不新增权限模型，不改变 `TEAM-DB-BENCH`。本地和 Matrix 只是在消息传输方式上不同，Tool 执行必须得到相同的权限裁决和审计记录。

### 前端

- `local_demo` 的固定普通场景改为岗位调整，不再把用户输入强制改回旧入职标题。
- Manager 根节点按持久化的 `round` 展示第一轮、结果回收、第二轮和最终汇总。
- Worker 节点识别 `task.delegate`，显示为“嵌套委派”，不得误标为 Manager 第二轮。
- 思考内容只显示持久化的 `reasoning_segments`。

## 失败边界

- Matrix 未就绪时不得回退到本地模式。
- 第二轮超出轮次、节点或子任务预算时继续由现有后端拒绝。
- 审计未返回批准编号时不得执行 IT 激活。
- Tool 被任一权限层拒绝时，任务保留真实拒绝结果，不伪造成功。
- 数据库评测团队及既有评测结果不参与此次运行。

## 验收

- 本地 E2E 任务包含根节点及四个直接子节点，任务图 `current_round=2`。
- 第二轮 IT 激活节点最初为 blocked，审计完成后才 assigned。
- 四个岗位调整 Tool 都经过 Gateway 并产生审计记录。
- Manager 与 Worker 过程详情都能展开安全决策摘要；Worker 嵌套委派单独标识。
- 既有 Matrix 多轮、三层权限、数据库评测和前端测试全部保持通过。
- 真实 Matrix 启动后健康状态为 `mode=agentteams_strict`、`transport=matrix`、`strict_ready=true`，并完成一次岗位调整两轮任务。
