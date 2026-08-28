# AgentTeams 可独立运行多 Agent 协作系统设计

## 1. 正式要求

围绕 AgentTeams 探索并实现一个可独立运行的多 Agent 协作小系统，支持：

- 主 Agent 拆解任务；
- 主 Agent 向子 Agent 派发任务；
- Agent 间消息通信；
- 执行状态跟踪；
- 子任务结果逐层返回与最终汇总；
- 页面展示协作关系、工作轨迹和日志；
- 交付可运行代码、简要架构、通信协议、测试用例、部署启动说明；
- 完成“任务提交—多 Agent 协作—过程可追踪—结果汇总”的演示闭环。

本系统保留在当前数字员工平台仓库中，但提供独立页面、独立启动脚本和不依赖企微的演示入口。企微统一数字分身入口及机器人调用属于后续渠道扩展，不是本阶段主线。

## 2. 当前基础与缺口

当前项目已经具备：

- `TeamTaskOrchestrator`：任务创建、一层拆解、子任务执行、审批和汇总；
- `AgentTeamsGateway`：通过 Matrix Client-Server API 登录、发送、拉取和解析房间消息；
- `group_chat`：把平台任务与 AgentTeams 协作阶段衔接；
- `TaskRun`：保存任务状态、平铺子任务和总结；
- `Teams` 页面：提交任务、查看子任务卡片、审批和 Leader 汇总；
- 团队任务、策略、审计和前端页面测试；
- 前后端一键启动脚本。

与正式要求相比仍缺少：

1. 子 Agent 不能作为局部主 Agent 继续递归拆解；
2. `TaskRun.subtasks` 是平铺 JSON，不能可靠表达任务树和并发状态；
3. AgentTeams 房间消息主要是自然语言文本，缺少稳定的结构化通信协议；
4. 当前完成解析依赖文本关键词，消息关联和错误诊断能力不足；
5. 页面没有协作关系图、完整工作时间线和消息/工具日志；
6. 页面主要展示最近任务，缺少可回放的任务历史；
7. AgentTeams 不可用时会降级，可能让演示者误以为真实 AgentTeams 已参与；
8. 缺少专门面向本要求的独立启动、部署说明和完整演示脚本。

## 3. 设计原则

### 3.1 AgentTeams 是通信与协作通道

AgentTeams 负责：

- 团队身份和协作房间；
- 主 Agent 与子 Agent 的任务消息；
- 接单、进度、结果和失败回报；
- 面向人的协作过程展示。

### 3.2 平台数据库是任务状态真相源

Matrix 房间消息不是唯一状态存储。平台以数据库中的任务节点、消息和事件为真相源，防止房间历史、重复事件、轮询窗口或进程重启导致状态错误。

### 3.3 业务副作用仍经过 Gateway

AgentTeams 消息只能表达协作意图和执行结果，不直接绕过权限调用业务系统。Skill、Workflow、知识库、RPA、HTTP Adapter 等调用继续经过：

```text
Identity -> Policy -> Gateway -> Runtime/Adapter -> Audit
```

### 3.4 主 Agent 是任务角色，不是永久员工类型

根任务的负责人是根主 Agent。任何子 Agent 接到任务后，都可以在该子任务范围内成为局部主 Agent并继续委派。任务树中的角色由任务关系决定，不修改数字员工永久类型。

### 3.5 演示模式必须标明真实边界

系统支持两种运行模式：

- `local_demo`：使用确定性的本地 AgentTeams 兼容 Transport，保证无外部凭据时可以独立演示；
- `agentteams_strict`：必须连接真实 AgentTeams/Matrix，连接失败即任务失败，不允许静默降级。

页面、接口和日志必须显示当前运行模式。`local_demo` 不能被描述为真实 Matrix 通信，`agentteams_strict` 也不能在失败后伪装成本地成功。

## 4. 总体架构

```text
AgentTeams 协作页面
  │ 提交任务
  ▼
Collaboration API
  │ 创建根任务
  ▼
RecursiveTeamOrchestrator
  ├─ 加载团队与能力
  ├─ 主 Agent 拆解任务
  ├─ 创建任务节点
  ├─ 调用 InvocationRouter
  ├─ 控制深度/循环/并发/超时
  └─ 逐层汇总
  ▼
AgentTeamsTransport
  ├─ LocalDemoTransport
  └─ MatrixAgentTeamsTransport
  │
  ├─ task.assign
  ├─ task.accepted
  ├─ task.progress
  ├─ agent.message
  ├─ task.result
  └─ task.failed
  ▼
数字员工 Agent Runtime
  ├─ 继续委派下游 Agent
  └─ Policy -> Gateway -> Skill/Workflow/KB/Plugin
  ▼
TaskNode + AgentMessage + TaskEvent
  ▼
任务关系图 + 工作时间线 + 通信/工具日志 + 最终汇总
```

## 5. 核心组件

### 5.1 `RecursiveTeamOrchestrator`

职责：

1. 创建根任务节点；
2. 让当前协调 Agent 生成结构化拆解结果；
3. 校验被调用 Agent 的能力和权限；
4. 创建子任务节点；
5. 通过 Transport 发送任务；
6. 接收状态和结果消息；
7. 允许子 Agent 继续创建下一层子任务；
8. 等待必要子任务进入终态；
9. 逐层生成局部汇总，最终生成根任务汇总。

拆解结果必须是受控 JSON，不直接执行模型生成的任意代码：

```json
{
  "subtasks": [
    {
      "assignee_agent_id": "VE-RESEARCH-001",
      "title": "完成行业与公司研究",
      "capability": "industry.research",
      "input": {
        "company": "示例公司"
      },
      "depends_on": []
    }
  ]
}
```

### 5.2 `InvocationRouter`

负责在统一调用接口下路由不同目标：

- `digital_employee`：调用平台内部数字员工；
- `managed_assistant_bot`：调用公开助理机器人绑定的内部 Agent；
- `external_api_bot`：通过获准的 HTTP、MCP 或 Workflow Adapter 调用外部机器人；
- `wecom_only_bot`：没有可编程入口时明确拒绝自动调用。

本阶段验收重点是 `digital_employee`。机器人目标保留可运行 Mock 用例，真实企微接入不属于本阶段。

### 5.3 `AgentTeamsTransport`

统一接口：

```text
send(message) -> transport_event_id
receive(cursor, task_id?) -> messages
health() -> mode/status/detail
close()
```

实现：

- `LocalDemoTransport`：在本地进程内模拟队列、Agent响应和消息顺序，使用相同通信协议；
- `MatrixAgentTeamsTransport`：复用并改造现有 `AgentTeamsGateway`，通过 Matrix发送带结构化内容的消息并拉取事件。

Transport 只负责传输，不修改任务业务状态；收到的消息必须先经过协议校验和幂等处理，再由 Orchestrator 更新数据库。

### 5.4 `AgentCapabilityRegistry`

每个 Agent 注册：

- Agent ID和对应数字员工编号；
- 显示名称、角色、人设摘要；
- 能力列表；
- 可接受的输入类型；
- 权限和最大数据等级；
- 可使用的 Skill、Workflow、知识库、插件；
- 最大并发数；
- 运行时和可用状态。

主 Agent 依据能力与权限选择下游，不只依据名称或关键词硬编码。

### 5.5 `TaskProjectionService`

为页面构建只读视图：

- 根任务详情；
- 节点和父子边；
- 节点状态、耗时和负责人；
- Agent 间通信时间线；
- 工具调用和状态事件；
- 最终汇总和失败原因。

前端不自行推断任务关系和业务状态。

## 6. 数据模型

保留 `TaskRun` 作为对现有接口兼容的根任务摘要，新增三张表作为正式协作数据：

### 6.1 `agent_task_node`

| 字段 | 说明 |
| --- | --- |
| `id` | 节点任务 ID，主键 |
| `root_task_id` | 根 `TaskRun.id`，索引 |
| `parent_task_id` | 父节点 ID；根节点为空，索引 |
| `trace_id` | 贯穿审计的 Trace ID，索引 |
| `title` | 可读任务标题 |
| `instruction` | 当前节点执行说明 |
| `capability` | 所需能力 |
| `coordinator_agent_id` | 当前子树协调者 |
| `assignee_agent_id` | 当前节点执行者 |
| `depth` | 根为0 |
| `status` | 节点状态 |
| `input_payload` | 经过校验的输入 JSON |
| `output_payload` | 结构化输出 JSON |
| `result_summary` | 页面展示摘要 |
| `error_code`、`error_message` | 失败信息，不含密钥和敏感正文 |
| `deadline_at` | 截止时间 |
| `started_at`、`finished_at` | 执行时间 |
| `created_at`、`updated_at` | 记录时间 |

### 6.2 `agent_message`

| 字段 | 说明 |
| --- | --- |
| `id` | 平台消息 ID，主键 |
| `protocol_version` | 协议版本 |
| `transport_event_id` | Matrix或本地 Transport事件 ID，唯一索引 |
| `root_task_id`、`task_id`、`parent_task_id` | 任务关联 |
| `message_type` | assign/accepted/progress/message/result/failed/cancel |
| `sender_agent_id`、`receiver_agent_id` | 通信双方 |
| `payload` | 结构化消息内容 |
| `sequence_no` | 同任务消息顺序 |
| `delivery_status` | pending/sent/delivered/processed/failed |
| `idempotency_key` | 幂等键，唯一索引 |
| `created_at`、`processed_at` | 时间 |

### 6.3 `agent_task_event`

| 字段 | 说明 |
| --- | --- |
| `id` | 自增主键 |
| `root_task_id`、`task_id`、`trace_id` | 任务关联和索引 |
| `event_type` | task_created/task_started/delegated/tool_called/status_changed等 |
| `actor_agent_id` | 事件发起者 |
| `target_agent_id` | 可选目标 Agent |
| `source` | orchestrator/agentteams/gateway/policy/runtime |
| `level` | info/warning/error |
| `summary` | 可读摘要 |
| `detail` | 去敏后的结构化细节 |
| `created_at` | 事件时间 |

### 6.4 与 `TaskRun` 的兼容

- 根任务创建时同时创建一个 `TaskRun` 和深度为0的 `AgentTaskNode`；
- `TaskRun.status`、`summary` 和 `source`作为列表页兼容字段；
- 新页面的任务树、通信和日志以三张新表为准；
- 现有 `TaskRun.subtasks` 仅为旧接口兼容投影，不再作为递归任务真相源；
- 更新根任务时由 Projection Service 生成一层子任务摘要写回旧字段。

## 7. Agent通信协议

### 7.1 消息信封

```json
{
  "protocol": "dwp.agentteams",
  "version": "1.0",
  "message_id": "MSG-0001",
  "message_type": "task.assign",
  "root_task_id": "TASK-1000",
  "task_id": "TASK-1001",
  "parent_task_id": "TASK-1000",
  "trace_id": "TRACE-1000",
  "sender_agent_id": "DT-E10281",
  "receiver_agent_id": "VE-RESEARCH-001",
  "sequence_no": 1,
  "timestamp": "2026-08-28T18:00:00+08:00",
  "idempotency_key": "TASK-1001:task.assign:1",
  "payload": {
    "capability": "industry.research",
    "instruction": "完成行业与公司研究",
    "input": {},
    "constraints": {
      "max_depth": 4,
      "deadline": "2026-08-28T18:05:00+08:00"
    }
  }
}
```

### 7.2 消息类型

| 类型 | 方向 | 作用 |
| --- | --- | --- |
| `task.assign` | 协调者 -> 执行者 | 派发子任务 |
| `task.accepted` | 执行者 -> 协调者 | 确认接单 |
| `task.progress` | 执行者 -> 协调者 | 报告阶段进度 |
| `agent.message` | Agent <-> Agent | 非终态协作消息或澄清 |
| `task.result` | 执行者 -> 协调者 | 返回结构化成功结果 |
| `task.failed` | 执行者 -> 协调者 | 返回失败类型和可重试性 |
| `task.cancel` | 协调者 -> 执行者 | 取消任务 |

### 7.3 Matrix承载形式

为兼容当前 AgentTeams 房间和便于人工阅读，Matrix `m.text` 的 `body` 保存简短摘要，同时在自定义字段中保存结构化信封：

```json
{
  "msgtype": "m.text",
  "body": "[TASK-1001] 数字分身 -> 研究AI员工：完成行业与公司研究",
  "dwp_agent_message": {
    "protocol": "dwp.agentteams",
    "version": "1.0",
    "message_id": "MSG-0001",
    "message_type": "task.assign",
    "root_task_id": "TASK-1000",
    "task_id": "TASK-1001",
    "parent_task_id": "TASK-1000",
    "trace_id": "TRACE-1000",
    "sender_agent_id": "DT-E10281",
    "receiver_agent_id": "VE-RESEARCH-001",
    "sequence_no": 1,
    "timestamp": "2026-08-28T18:00:00+08:00",
    "idempotency_key": "TASK-1001:task.assign:1",
    "payload": {
      "capability": "industry.research",
      "instruction": "完成行业与公司研究",
      "input": {}
    }
  }
}
```

完整 `dwp_agent_message` 内容遵循消息信封定义。若现有 AgentTeams Worker 只能输出文本，则允许在 `body` 中附带一个带协议标记的 JSON代码块，由适配器解析；结构化字段是首选，文本解析只作为兼容路径。

### 7.4 校验与幂等

- 协议版本、消息类型、任务关系、发送者和接收者必须校验；
- `transport_event_id` 和 `idempotency_key` 双重去重；
- 接收者必须是团队成员并且具有目标能力；
- 旧消息必须同时通过根任务 ID 和创建时间窗口过滤；
- ACK、进度和普通消息不能被解析成终态；
- 只有 `task.result`、`task.failed`、`task.cancel` 可以结束节点；
- 无效消息记录 warning 事件，但不改变任务状态。

## 8. 任务状态机

节点状态：

```text
queued
  -> assigned
  -> accepted
  -> running
  -> input_required
  -> approval_required
  -> succeeded
  -> failed
  -> cancelled
  -> expired
```

规则：

- 所有状态变化写入 `agent_task_event`；
- 根任务状态由子树投影，不由前端计算；
- 必要子任务全部成功后，父节点才能汇总成功；
- 可选子任务失败时，父 Agent可用已完成结果降级汇总；
- 取消根任务向所有未结束后代传播；
- 超时节点进入 `expired`，不得继续接受迟到结果改变终态；
- 审批状态是可恢复等待，不占用执行锁。

## 9. 递归、循环和并发保护

第一版固定限制：

- 最大任务深度：4；
- 每个节点最多直接创建3个子任务；
- 每个根任务最多20个节点；
- 每个 Agent 默认最大并发2；
- 节点默认执行超时120秒；
- 单条消息最多重试2次；
- 当前祖先路径中已经出现的 Agent 不能再次作为下游，重试不计为新委派；
- 子 Agent 不允许等待一个尚未声明依赖、并且反向依赖自己的节点；
- 父节点等待子节点时不能持有独占业务资源；
- 并发节点通过队列和租约控制，数据库终态更新使用条件更新避免重复完成。

## 10. API设计

Base Path沿用 `/api/v1`。

### 10.1 提交和控制

```text
POST /agentteams/tasks
GET  /agentteams/tasks
GET  /agentteams/tasks/{root_task_id}
POST /agentteams/tasks/{root_task_id}/cancel
```

提交请求：

```json
{
  "request": "根据给定材料完成一份投资研究摘要",
  "team_id": "TEAM-RESEARCH",
  "leader_agent_id": "DT-E10281",
  "mode": "local_demo"
}
```

### 10.2 关系、消息和轨迹

```text
GET /agentteams/tasks/{root_task_id}/graph
GET /agentteams/tasks/{root_task_id}/messages
GET /agentteams/tasks/{root_task_id}/events
GET /agentteams/transports/health
```

`graph` 返回节点和边；`messages` 返回协议消息；`events` 返回工作与工具时间线；Transport健康接口返回模式、连接状态和真实/模拟边界。

### 10.3 更新方式

第一版页面每2秒轮询任务投影接口，减少引入新基础设施。后续可增加 SSE，但不作为完成条件。

## 11. 页面设计

新增独立“AgentTeams协作”页面，包含：

### 11.1 任务提交区

- 团队选择；
- 根主 Agent 选择；
- 运行模式显示；
- 任务文本与可选演示材料；
- 提交、取消和重新运行。

### 11.2 协作关系图

- 根 Agent、子 Agent、下游 Agent 节点；
- 有向边表示委派关系；
- 节点显示负责人、状态、耗时和当前动作；
- 点击节点过滤右侧消息与日志；
- 失败、等待审批和超时使用不同视觉状态。

第一版可使用可折叠任务树实现，不强制引入大型图形库；只要父子关系和状态清晰可见即满足验收。

### 11.3 工作时间线

按时间顺序显示：

- 任务创建；
- 主 Agent 拆解；
- 委派和接单；
- Agent进度；
- Skill、Workflow、知识库和插件调用；
- 状态变化；
- 子任务结果；
- 局部和最终汇总。

### 11.4 通信与日志

提供筛选：

- 全部；
- Agent消息；
- 工具调用；
- 警告与错误。

日志展示摘要和去敏结构，默认不展示完整 Prompt、文件正文、Token、Secret或内部临时路径。

### 11.5 最终结果

- 当前任务总体状态；
- 根主 Agent 最终汇总；
- 各主要子任务的结果摘要；
- 失败或降级说明；
- Task ID、Trace ID和运行模式。

## 12. 错误处理

- AgentTeams配置缺失：`agentteams_strict` 提交立即失败并提示配置项；
- Matrix登录、房间或发送失败：节点失败并记录 transport error，不降级；
- 本地模式处理器异常：节点失败，其他无依赖分支继续；
- 协议消息不合法：忽略业务更新并记录 warning；
- 重复事件：返回已有处理结果，不重复执行；
- Agent能力不存在：拆解校验失败，由父 Agent重新规划一次；仍失败则结束该分支；
- 权限拒绝：节点进入 failed 或 approval_required，记录 Policy ID；
- 工具异常：按可重试性决定重试或失败；
- 页面轮询失败：保留最近状态并显示连接错误，不把任务改成失败；
- 进程重启：从数据库中的非终态节点恢复轮询或标记超时，不依赖内存任务列表。

## 13. 独立运行与部署

新增专用启动脚本：

```powershell
.\scripts\run_agentteams_demo.ps1
```

默认行为：

1. 检查Python和前端依赖；
2. 初始化或迁移数据库；
3. 写入明确标注的 AgentTeams Demo团队、Agent和任务样例；
4. 设置 `DWP_AGENTTEAMS_MODE=local_demo`；
5. 启动后端和前端；
6. 健康检查；
7. 输出页面、Swagger和测试命令。

严格模式：

```powershell
.\scripts\run_agentteams_demo.ps1 -Strict
```

严格模式读取本地受控环境变量，检查真实 Matrix连通性和团队房间成员；任一检查失败即停止，不自动切换本地模式。

启动脚本不得重置未获用户确认的业务数据。演示数据使用独立标记并提供幂等初始化方式。

## 14. 测试策略

### 14.1 后端单元测试

- 消息信封校验；
- 任务节点状态机；
- 能力路由；
- 递归深度和节点数量；
- 循环调用检测；
- 幂等与重复事件；
- 父子状态投影；
- 局部和最终汇总；
- Transport失败和严格模式不降级。

### 14.2 后端集成测试

- 提交根任务并生成任务树；
- 主 Agent向两个子 Agent派发；
- 一个子 Agent继续委派下游；
- Agent消息按协议持久化；
- 工具调用事件进入时间线；
- 子任务结果逐层汇总；
- 失败、超时、取消和审批传播；
- LocalDemoTransport完整闭环；
- MatrixAgentTeamsTransport使用HTTP Mock验证登录、发送、拉取、解析和错误映射。

### 14.3 前端测试

- 任务提交；
- 任务树关系和状态；
- 节点选择和日志过滤；
- 工作时间线；
- 轮询刷新；
- 运行模式提示；
- 最终汇总；
- 错误、空状态和加载状态。

### 14.4 端到端演示测试

使用固定任务：

```text
根据给定公司材料，完成一份投资研究摘要。
```

预期任务树：

```text
数字分身（根主 Agent）
├── 研究AI员工
│   └── 知识检索Agent
└── 投资分析AI员工
    └── 文档整理Agent
```

验收：提交后页面持续显示拆解、委派、接单、进度、工具调用、结果返回和最终汇总；所有节点拥有可追踪状态和日志。

## 15. 交付物

1. 可独立启动的前后端代码；
2. `run_agentteams_demo.ps1` 启动脚本；
3. AgentTeams协作页面；
4. 任务节点、消息和事件数据模型；
5. 递归编排器与两种 Transport；
6. 架构说明；
7. Agent通信协议文档；
8. 部署启动说明；
9. 后端、前端和端到端测试；
10. 固定演示数据和操作手册。

## 16. 实施顺序

1. 为现有团队任务行为补回归测试；
2. 新增三张协作数据表和状态机测试；
3. 定义消息协议、解析器和幂等规则；
4. 抽象 AgentTeamsTransport，保持现有 Matrix能力可用；
5. 实现 LocalDemoTransport和严格 Matrix模式；
6. 实现递归任务创建、委派、结果回传和汇总；
7. 接入 InvocationRouter与现有 Policy/Gateway；
8. 提供任务图、消息和事件API；
9. 实现独立协作页面；
10. 补齐后端、前端、集成和演示测试；
11. 完成独立启动脚本、部署说明和演示手册；
12. 全量验证并记录真实能力与Mock边界。

## 17. 完成定义

只有同时满足以下条件才算完成：

- 无企微凭据时可以通过一条命令启动本地演示；
- 页面可以提交一个根任务；
- 主 Agent能够拆解并向至少两个子 Agent派发；
- 至少一个子 Agent能够继续委派一个下游 Agent；
- Agent 间 assign、accepted、progress、result消息可查询；
- 每个节点的状态、负责人、父子关系和耗时可追踪；
- 页面展示任务关系、时间线、通信/工具日志和最终汇总；
- 循环、深度、重复、超时、取消、权限和Transport错误测试通过；
- `agentteams_strict` 不允许静默降级；
- `local_demo` 在界面和文档中明确标注为本地兼容模拟；
- 部署说明、通信协议、测试说明和完整演示步骤齐全；
- 没有把其他未跟踪文件、凭据、数据库或日志加入提交。
