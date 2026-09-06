# AgentTeams 真实 Matrix 与独立 LLM Agent 系统设计

日期：2026-09-04
状态：已批准，进入实施

## 1. 目标

在现有数字员工平台的 AgentTeams 协作能力上，补齐一套可独立运行的真实演示环境：Manager、HR Worker、IT Worker 和审计 Worker 分别作为独立进程运行，使用独立 Matrix 账号通信，并真实调用 DeepSeek 完成任务规划、专业执行、动态委派和最终汇总。

标准演示只输入：

> 评估新员工A是否可以按期入职。

系统不得在前端、启动脚本或固定场景代码中预先写死 HR、IT、审计的派发顺序和最终业务结果。业务事实来自项目内自研 Tool 与虚构 Mock 数据；不连接企业内部系统。

## 2. 事实边界

### 2.1 真实运行部分

- 独立 Manager / Worker 操作系统进程。
- 独立 Matrix 账号、房间消息和 Matrix Event ID。
- DeepSeek 任务理解、Skill 选择、任务规划、专业分析和最终汇总。
- AgentTeams 协议消息、任务状态机、任务树和消息幂等。
- Tool 请求经过 Identity、Policy、Plugin Gateway 和审计。
- 平台数据库保存任务、节点、事件、消息和结果。

### 2.2 Demo 部分

- 新员工A及其入职材料、账号状态和审计证据均为虚构数据。
- HR、IT、审计 Tool 由项目自行实现并读取本地 Fixture 或 Mock 知识库。
- 不声称连接真实 HR、IT、审计或生产系统。

### 2.3 保留的旧模式

现有 `local_demo` 仅用于离线测试和回归验证。真实演示只使用 `agentteams_strict`，不得在 Matrix、Agent 或 DeepSeek 失败后自动回退到固定 Demo 输出。

## 3. 总体架构

```text
React 演示页面
      │ HTTP
      ▼
数字员工平台后端（@platform-bot）
      │ Matrix Client-Server API
      ▼
Synapse Matrix（Docker）
      ├── @manager      → Manager 独立进程
      ├── @hr-worker    → HR Worker 独立进程
      ├── @it-worker    → IT Worker 独立进程
      └── @audit-worker → 审计 Worker 独立进程
                              │
                              └── DeepSeek + 平台 Gateway Tool
```

### 3.1 平台后端

- 创建根任务和持久化 outbox。
- 使用 `@platform-bot` 发送平台消息。
- 同步 Matrix 事件并校验真实发送者与数字员工绑定。
- 校验团队成员、任务层级、依赖、状态和权限。
- 执行 Tool 请求并返回脱敏结果。
- 保存任务树、工作轨迹、消息、审计和最终汇总。

### 3.2 Synapse

- 只负责账号、房间和真实消息传输。
- 不负责任务拆解、业务判断或 Tool 执行。
- 使用 Docker 持久卷保留账号、房间和消息历史。

### 3.3 Agent Runtime

- 每个 Agent 使用独立 Matrix Token 和独立进程。
- 监听共享协作房间，只处理协议接收者为自己的消息。
- 持久化 Matrix 同步游标；成功处理后再推进游标。
- 重放时复用相同幂等键，避免平台重复改变状态。
- Agent 不直接读写平台任务数据库。

### 3.4 Manager

- 理解用户整体目标。
- 使用 Manager 团队运行 Skill。
- 选择团队场景 Skill。
- 进行粗粒度拆分和第一轮派发。
- 收集阶段结果，判断信息是否完整。
- 信息不足时进行后续轮次派发。
- 信息完整时生成最终汇总。

### 3.5 Worker

- 选择与当前子任务匹配的专业执行 Skill。
- 使用 Tool 获取业务事实。
- 需要同级信息时发送 `agent.message`。
- 需要独立专业工作时发送 `task.delegate` 创建下一级任务。
- 整合下级结果后向上级返回自己的完整结果。

## 4. Matrix 身份与团队范围

真实演示需要五个 Matrix 账号：

| Matrix 账号 | 逻辑身份 | 作用 |
|---|---|---|
| `@platform-bot` | `platform` | 平台发送任务和 Tool 结果，接收全部状态消息 |
| `@manager` | `VE-0001` | 拆分、派发、阶段复核和汇总 |
| `@hr-worker` | `VE-0002` | HR 资料、制度和入职材料核查 |
| `@it-worker` | `VE-0003` | 账号、设备和 IT 服务核查 |
| `@audit-worker` | `VE-0005` | 材料证据和审计依据复核 |

Agent 只能选择当前 Team 中已授权并存在 Runtime 绑定的数字员工。第一版不允许从全平台员工池动态扩展团队。

Manager 通过任务载荷中的团队目录知道“谁会做什么”。团队目录只包含安全元数据：员工号、名称、部门、职责摘要、能力标识和可用 Tool 摘要，不包含凭据或内部端点。

## 5. 三层 Skill 模型

### 5.1 团队运行 Skill

由角色固定加载，不需要模型选择：

- `agentteams-manager-coordination`：约束 Manager 粗粒度拆分、阶段复核、多轮派发和最终汇总。
- 升级后的 `employee-collaboration`：指导 Worker 在 Tool、Ask、Delegate、Handoff 之间选择并避免循环。

### 5.2 团队场景 Skill

由 Manager 根据用户目标选择：

- `agentteams-onboarding-readiness`：定义入职就绪评估所需证据、完成条件和风险边界，不写死具体 Agent ID。

### 5.3 专业执行 Skill

复用领域能力分支的既有 Skill：

- HR：`hr-onboarding-assistant` 等。
- IT：`it-service-assistant` 等。
- 审计：`audit-evidence-review` 等。

项目级 Skill 文件来自 `.agents/skills/*/SKILL.md`。Agent 每次开始新任务时获得有权使用的全部项目 Skill 的 `name + description + whenToUse` 目录；模型先返回选中的 Skill ID，Runtime 再加载选中 Skill 的完整正文进行第二次推理。

不增加 `search_skills` 或 `load_skill` 业务 Tool。Skill 目录读取和正文加载属于 Runtime 内部上下文准备。Skill 不授予 Tool 权限；Tool 权限仍由 Plugin Grant、Policy 和 Gateway 决定。

数据库个人 Skill 继续服务真人数字分身，不与本次虚拟 Agent 演示混用。

## 6. 任务规划与模型校验

### 6.1 Manager 第一轮

输入：

- 用户目标。
- 固定 Manager 运行 Skill。
- 全部团队场景 Skill 简略目录。
- 当前团队安全能力目录。

第一轮模型选择场景 Skill。Runtime 加载完整正文后，第二轮模型返回结构化计划：

固定入职演示中，Manager 第一轮只创建 HR 材料核查和 IT 就绪核查两个直接子任务，两项任务的 `depends_on` 都为空并在同一批释放。Manager 不在第一轮预先创建审计任务；审计是否需要参与，由拿到一手 Tool 证据的 Worker 再判断。

```json
{
  "selected_skill_ids": ["agentteams-onboarding-readiness"],
  "subtasks": [
    {
      "client_task_key": "...",
      "goal": "...",
      "required_capability": "...",
      "assignee_agent_id": "...",
      "depends_on": [],
      "required": true,
      "reason": "..."
    }
  ]
}
```

### 6.2 后端强校验

模型输出不直接执行。平台校验：

- JSON 字段和类型。
- Agent 是否存在、启用且属于当前 Team。
- Agent 是否具有所需能力和 Runtime 绑定。
- 依赖是否只引用同级任务且无环。
- 子任务数量、层级、总节点数和派发轮次。
- 当前发送者是否具有委派权限。

第一次无效时把有界错误反馈给模型重试；第二次仍无效则失败，不生成固定计划。

## 7. 渐进式动态协作

任务树不在首次规划时一次写死。

```text
Manager 同批派发 HR 与 IT
  → HR 调用 HR Tool，形成材料核查结果
  → IT 调用 IT Tool，发现 MFA 确认证据缺失
    → IT 从最新团队能力目录选择审计 Agent
    → IT 发送 task.delegate，创建受跟踪的二级审计任务
    → 审计 Agent 调用 Audit Tool，回传独立复核结果
    → IT 等待并整合审计结果后完成
  → Manager 收到 HR、IT 两个直接子任务结果
    → 信息完整：生成最终汇总
    → 仍有新的独立缺口：进入下一轮派发
```

这条标准演示链只固定职责边界和必须核验的证据，不固定模型输出文本。业务事实来自本地 Mock；Skill 选择、Tool/Delegate/Complete 决策、Agent 选择、Matrix 消息和最终汇总由各独立 Agent 的真实 DeepSeek 调用产生。

### 7.1 三种缺口处理

- 缺业务数据：`tool.request`。
- 只需同级信息或专业意见：`agent.message`，不创建任务节点。
- 需要他人完成可跟踪工作：`task.delegate`，创建下一级任务。

### 7.2 同级依赖与层级委派

`depends_on` 表示同级任务的执行顺序，不代表 SubAgent 创建 SubSubAgent。只有当前 Worker 发送 `task.delegate` 才会创建下一级任务。

被依赖任务初始状态为 `blocked`。其全部前置任务完成后，平台才进入 `assigned` 并发送 `task.assign`，同时附带经过脱敏的依赖输出。

### 7.3 Manager 多轮派发

全部当前必需子任务完成后，平台发送带有安全子任务结果的 `task.summary` 请求。Manager 可以：

- 返回最终 `task.summary`，完成根任务；或
- 发送新一轮 `task.delegate`，使根任务重新进入 `waiting_children`。

## 8. Tool 与 Mock 数据

业务 Tool 由项目自行实现，不连接内部系统：

- `query_onboarding_employee`：读取新员工A及入职材料 Fixture。
- `query_it_readiness`：读取账号、设备和服务准备 Fixture。
- `review_onboarding_evidence`：读取审计证据 Fixture 并返回证据缺口。

标准演示中，IT Tool 返回 MFA 确认证据缺失。IT 不能直接把该缺口当成已复核结论，而是按团队能力目录动态委派审计 Agent；审计 Agent 必须再调用自己的 Tool，IT 收到其结果后才能完成本节点。

执行链：

```text
Worker 决定调用 Tool
  → Matrix tool.request
  → 平台身份和任务校验
  → Policy / Plugin Gateway
  → 自研 Adapter 读取本地 Mock
  → AuditEvent
  → 脱敏 tool.result
  → Matrix 返回 Worker
```

模型不得直接编造业务事实。Worker 未取得必要 Tool 证据时，不得把任务标记为成功。

## 9. 协议与状态机调整

继续使用现有 AgentTeams v1.0 消息封装。主要调整：

- `task.submit` 增加安全团队能力目录。
- `task.delegate` 保留规划 Skill、轮次和能力理由等审计元数据。
- `task.assign` 对依赖任务延迟发送，并包含安全依赖输出。
- `task.summary` 请求包含安全的直接子任务结果。
- Skill 选择使用 `agent.message` 的结构化 `kind=skill.selected` 记录，不为 Skill 新增业务 Tool。
- 状态机增加 `blocked → assigned`。
- 状态机允许 `summarizing → waiting_children` 进行下一轮委派。

限制：

- 最大深度 3。
- 单轮最多 5 个子任务。
- Manager 最多 3 个补充派发轮次。
- 单根任务最多 20 个节点。
- 总任务超时 10 分钟。
- 禁止把任务派回当前父链中的 Agent。
- 禁止重复语义目标和 A→B→A 委派循环。

## 10. 可观察演示

前端标准示例只填入：

> 评估新员工A是否可以按期入职。

页面展示：

- 当前真实/本地模式。
- Matrix、Manager 和 Worker 就绪情况。
- 当前派发轮次。
- Agent 选择的 Skill。
- 父子任务树、同级依赖和并行状态。
- Agent 消息、Tool 请求、Policy 决策和审计 ID。
- Matrix Event ID。
- 最终汇总、风险和后续动作。

真实运行统一采用可观察节奏，关键阶段默认间隔约 3 秒。产品页面不提供正常/演示速度开关；测试通过依赖注入使用零等待。

## 11. 本地部署

采用混合方式：

- Synapse 在 Docker 中运行。
- 平台后端、前端和四个 Agent 在 Windows 独立进程中运行。

一键脚本：

- `scripts/start_agentteams_real_demo.ps1`
- `scripts/stop_agentteams_real_demo.ps1`
- `scripts/bootstrap_agentteams_matrix.py`

启动顺序：Docker / Synapse → 账号房间初始化 → 后端 → Manager → HR → IT → 审计 → 前端 → 健康检查。停止脚本只停止本次创建的进程和容器，不广泛终止其他 Python、Node 或 Docker 工作负载。

凭据只写入 gitignored 的本地运行目录，不输出到日志、页面、截图或提交。

## 12. 错误处理

- Matrix 未就绪：阻止真实任务提交。
- Matrix 发送不确定：outbox 保持 pending 并使用原幂等键重试。
- Agent 未接受：达到接受超时后标记 `timed_out`。
- DeepSeek 失败：重试一次，仍失败则当前节点失败。
- 模型计划无效：反馈有界校验错误并重试一次，仍无效则 `planning_failed`。
- Tool 被拒绝：返回安全 `deny` 结果并保留审计，不绕过权限。
- 必需子任务失败：根任务失败，不伪造成功汇总。
- 重复 Matrix 事件：按 Event ID 和幂等键去重。
- 非法 Matrix 发送者：记录 `protocol.rejected`，不改变任务状态。

## 13. 测试策略

### 13.1 单元测试

- Skill frontmatter 和正文解析。
- Skill 目录选择与模型 JSON 校验。
- Manager 第一轮和多轮规划。
- Worker Tool、Ask 和 Delegate 决策。
- 团队外 Agent、循环、深度、轮次和节点限制。
- blocked 依赖释放和依赖输出传递。
- Matrix 幂等、游标和敏感字段脱敏。

### 13.2 集成测试

使用可控 LLM 和 Matrix 替身验证完整状态机：任务提交、并行分派、Worker 递归委派、Tool、阶段复核和最终汇总。

### 13.3 真实 Matrix 测试

真实 Synapse + 可控 LLM，验证五个账号、房间、独立进程、Matrix Event ID、重启游标和重复事件。

### 13.4 完整真实演示测试

真实 Synapse + 四个独立 Agent + 真实 DeepSeek + 真实 Gateway + 本地 Mock 数据。该测试由操作者显式运行，不进入普通 CI。

## 14. 完成标准

- 一键启动真实演示环境。
- 五个 Matrix 账号加入同一房间。
- 四个独立 Agent 进程正常运行。
- 页面提交自由文本目标成功。
- Manager 真实选择 Skill、拆解和派发。
- Manager 第一轮同批、无依赖地释放 HR 和 IT 两个直接子任务；以持久化事件顺序验收，不以模型调用耗时是否恰好重叠推断。
- IT Tool 先返回 MFA 证据缺口，随后由 IT Worker 向审计 Agent 动态委派一个受跟踪的二级任务。
- 审计 Agent 调用自己的受治理 Tool；审计完成后 IT 才能整合并完成，HR 和 IT 均完成后 Manager 才能最终汇总。
- 至少一次真实 Gateway Tool 调用和审计记录。
- 每个关键协议消息具有真实 Matrix Event ID。
- Manager 基于 Worker 结果生成最终汇总。
- 页面完整展示任务树、工作轨迹和日志。
- 单元、集成、前端和真实演示验证通过。

## 15. 教学与文档要求

最终文档按一次真实任务的执行顺序编写，不按文件列表堆砌术语。每一步说明：参与者、输入、动作、输出、状态变化、下一触发条件和代码位置。明确标识源码支持、实测验证、Demo 数据和未连接范围，并使用真实页面、进程日志、Matrix Event ID 和数据库状态完成慢速讲解。
