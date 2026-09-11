# AgentTeams 数据库诊断 A/B 基准

## 定位

这是一个基于 MultiAgentBench/MARBLE Database 场景改编的本地对照评测，用于在相同场景、模型配置、只读 Tool 和预算下比较 Single Agent 与 AgentTeams。结果是本项目内部实验，不是 MARBLE 官方榜单成绩。

评测使用仓库内的确定性 JSON 监控快照，不连接生产数据库，也不会执行任意 SQL。标准答案位于服务端 fixture 的隐藏评测文件中，不会进入 Agent Prompt、Skill、Tool 返回值或前端运行态。

为避免“诊断语义正确、标签名称不同”被误记为错误，Single Agent 和 Manager 在最终诊断阶段都会收到同一份公开候选标签表，并由结构化输出 Schema 与服务端各校验一次。候选表只公开五个场景的规范标签，不标注当前场景答案；Manager 的分工阶段和五个 Worker 不接收该标签表。当前契约版本为 `database-diagnosis-taxonomy-v1`。

## 场景与角色

首期包含五个场景：批量写入、锁竞争、VACUUM 压力、索引异常和大查询。AgentTeams 使用一个 Manager 和五个专项 Worker：

| 角色 | Agent ID | 密钥环境变量 | Manager 可分配信号 |
|---|---|---|---|
| Manager / Single Agent | `VE-0001` | `DEEPSEEK_API_KEY_MANAGER` | 汇总或顺序调查 |
| 写入诊断 | `DB-WRITE-01` | `DEEPSEEK_API_KEY_DB_WRITE` | `statements`、`table_stats` |
| 锁诊断 | `DB-LOCK-01` | `DEEPSEEK_API_KEY_DB_LOCK` | `locks` |
| 清理诊断 | `DB-VACUUM-01` | `DEEPSEEK_API_KEY_DB_VACUUM` | `vacuum`、`table_stats` |
| 索引诊断 | `DB-INDEX-01` | `DEEPSEEK_API_KEY_DB_INDEX` | `indexes`、`statements` |
| 查询诊断 | `DB-QUERY-01` | `DEEPSEEK_API_KEY_DB_QUERY` | `statements`、`table_stats` |

六个变量都必须由启动进程环境提供。实现没有共享密钥回退，也不会把密钥保存到数据库、审计、日志、fixture、Skill 或 API 响应。`backend/.env.example` 只列变量名。数据库基准的 DeepSeek 单次请求超时为 90 秒；该值和提示契约版本都会写入公平性配置并参与指纹计算，防止不同运行契约的数据被错误合并。

## 运行路径

Single Agent 使用 Manager 的模型配置，可循环选择五类信号，但其结构化动作契约没有委派动作。

AgentTeams 运行通过现有协作服务创建真实 `TaskRun` 和 `AgentTaskNode`。Manager 首先读取公开场景描述和受限团队能力目录，生成第一轮结构化分工；服务端校验角色、Agent ID 和信号授权后才记录 `task.delegate`。随后依次记录 `task.accepted`、`tool.request`、`task.result` 与 `task.summary` 消息/事件。五个 Worker 的独立模型步骤并行执行，全部必要子结果到齐后 Manager 才能总结。数据库观测统一经过：

```text
Policy → Plugin Gateway → Capability Executor → Adapter → Audit
```

当前基准在服务进程内应用与 Matrix 线上运行相同的协议状态机，并持久化消息、事件和 outbox；计时不包含外部 Matrix 网络传输延迟。需要验证真实 Matrix 传输时，应单独运行现有严格 Matrix smoke，不能把两种计时混为一谈。

## 启动

1. 在受控的进程环境中设置六个角色密钥变量，并按项目现有方式设置 `AGENTTEAMS_WORKER_MODEL`。
2. 启动后端和前端。
3. 打开 AgentTeams 页面，在“数据库诊断 A/B 基准”面板点击“开始对照评测”。
4. 默认生成 `5 场景 × 5 次重复 × 2 模式 = 50 runs`。

实验创建时会以增量方式同步专用团队、五个 Worker、只读 Tool 和授权，不清空已有任务或审计记录。运行记录可以在服务重启后恢复；已终态的 run 不会重复执行。

## 指标解释

- 结果：主答案正确率、Top-2 命中率、必要证据覆盖率、证据有效率。
- 效率：端到端耗时、模型步骤、Tool 调用、重复调用和关键路径步骤。
- 协作：Manager 规划/汇总轮次、返工、必要子结果接收数和丢失数。
- 稳定性：成功率以及失败/无效运行数。

越权信号、缺审计、伪造或跨场景证据、必要子结果丢失、非法终态、预算耗尽和超时会触发 hard gate，不能由平均准确率掩盖。

P95 只有在单组有效样本至少达到 20 时才具有解释价值。默认每组 25 次，适合报告 P50/P95；少量测试运行只用于功能检查。

## 测试与真实调用边界

自动化测试全部使用 fake provider 和本地 fixture，不消耗模型额度、不访问外部 Matrix 或真实数据库。真实 DeepSeek 运行由页面创建实验后在后台执行；API 会在缺少角色密钥或运行环境不可用时只返回安全错误码，不回显底层异常或凭据。模型网络、限流、客户端、服务端、HTTP、响应格式等失败会保存为有限的 `llm_*` 分类码，未知异常统一保存为 `run_execution_failed`。

来源和许可证见 `mock-data/agentteams-benchmark/database/ATTRIBUTION.md`。
