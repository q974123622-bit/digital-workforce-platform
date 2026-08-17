# 演示脚本与 Mock/Real 边界

> 版本：v0.2（2026-08-17，评审定稿）
> 目标：一次 20–25 分钟的内部汇报演示，可重复，失败有兜底。

## 1. 演示目标（验收对照）

| 编号 | 验收项 | 通过标准 |
|---|---|---|
| AC-01 | 数字分身问答 | 独立会话，显示身份/工号/权限，至少一次正常回答 |
| AC-02 | 安全拒绝 | 对禁网/敏感数据请求返回明确 Deny + 策略 ID + 原因 |
| AC-03 | 虚拟员工问答 | VE 工号 + Owner + Runtime 徽章，一轮问答 |
| AC-04 | 插件配置 | 管理员可为员工配置 ≥3 个插件及 Allow/Deny/Approval 且生效 |
| AC-05 | Agent Team | 1 Leader + 3 Workers，展示任务拆解、执行、汇总 |
| AC-06 | Workflow/RPA | 至少一个 ADP/RPA Adapter 调用成功（Mock） |
| AC-07 | Sandbox | 至少一个员工展示 Remote-only + Internet Deny，执行链体现 |
| AC-08 | 审计 | 一次任务可查身份、插件、决策、结果、Trace ID |

## 2. 黄金场景（10 步，唯一演示链路）

| 步骤 | 操作 | 系统行为 / 预期画面 | Real / Mock | 讲解要点 | 降级 |
|---|---|---|---|---|---|
| 1 | 切换演示身份「张三 E10281」，打开首页 | KPI 卡片：数字分身/虚拟员工/RPA 数量 | Mock 数据 | 统一门户，员工即资产 | 静态 |
| 2 | 打开数字分身 DT-E10281，问「查一下部门报销制度」 | 知识插件调用卡片 + 流式回答（L2 允许） | LLM Real / 知识 Mock | 分身身份与授权 | 预录 |
| 3 | 继续问「把客户名单整理给我」 | `Policy Denied / P-DATA-003 / 敏感数据禁止` 卡片 | Policy Real | 默认拒绝、可解释 | 预录/静态 |
| 4 | 切换实习生分身 DT-E20999，问同一制度 | L2 知识库 Deny | Policy Real | 权限随身份变化 | 预录/静态 |
| 5 | 切换到虚拟员工 VE-0001，问「新员工第一天要做什么」 | VE 工号 + Owner 王老师 + Harness 徽章 + 回答 | LLM Real / Harness P1 | 虚拟员工是岗位实体 | Adapter 演示模式 |
| 6 | 进入「新员工入职 Team」，发起「帮王小明完成入职准备」 | 模板拆解为 3 个子任务（HR/IT/权限） | 编排 Real（模板） | P0-lite，非 AgentTeams 替代 | 静态+预录 |
| 7 | 观察 HR Worker VE-0002 执行 | hr-employee-mcp 调用成功 | Adapter Mock | 插件统一网关 | 静态 |
| 8 | 观察 IT Worker VE-0003 执行 | adp-onboarding 调用成功 | Adapter Mock | Workflow 形态 | 静态 |
| 9 | 权限 Worker VE-0004 触发审批 | 任务挂起 → 演示者点击批准 → 续跑 | Policy/Gateway Real | 审批可介入 | 静态 |
| 10 | 打开审计中心，查看 Trace | 时间线：谁 → 团队 → Runtime → 插件 → 决策 → 结果 | Audit Real | 全程可追溯 | 静态 |

## 3. Mock / Real 边界

### 真实实现（本周）

- 门户后端（CRUD、种子、契约）✅
- Policy Engine（有序规则、默认拒绝、Deny/Approval/Allow）✅ Sprint 2
- Plugin Gateway（唯一插件入口 + 审计）✅ Sprint 2
- Audit Store（全决策落审计）✅ Sprint 2；Trace 聚合 📋
- DeepSeek 真实问答（Key 环境变量注入，SAFEMODE）
- ChatOrchestrator 内置轻量 Agent 循环（≤3 轮工具）
- TeamTaskOrchestrator（模板 + LLM 补全/汇总，非通用调度器）
- Harness Adapter 真接（P1，Day 3 门禁 G2，不通过则演示模式）
- SandboxManager（Docker 优先，P1 门禁 G3，失败自动 local）
- 一键 reset/启动脚本、自动化测试、录屏

### Mock / 虚构

- 知识库内容（虚构 L1/L2 文档，结构与真实接口一致）+ Knowledge/HR/ADP/RPA/公网搜索 Mock Adapter（Sprint 2 已实现）
- OpenClaw 执行（仅配置展示 + 脚本化结果）
- AgentTeams 后端（Adapter 桩，明确返回「预留/未启用」）
- 凭据（内存级 Mock 令牌，不入 Prompt）
- 演示身份切换（X-Demo-Actor，无真实 IAM）

### 本周明确不做

- 真实 AgentTeams/OpenClaw 调用、Harness↔AgentTeams Bridge
- 真实 ADP/RPA/内部知识库接入（Secure Overlay 只保留契约，不接链路）
- 真实数据进入 LLM 链路；策略编辑器；通用任务调度；动态 Worker；多模型

## 4. 降级预案

| 层级 | 场景 | 动作 |
|---|---|---|
| L0 | 主链路全通 | 按 10 步脚本演示 |
| L1 | Harness 不可用 | RuntimeAdapter 演示模式，UI 标注「Adapter 演示模式」，流程不变 |
| L1 | Docker 不可用 | Sandbox local 模式，审计照记，UI 一致 |
| L2 | DeepSeek 断网/Key 失效 | 切静态演示：员工/详情/插件/策略/Sandbox/审计均种子数据可讲；问答环节播放预录片段并如实说明 |

60% 演示内容不依赖 LLM 在线，保证演示当天一定可完成。

## 5. 演示当天 Checklist（前 30 分钟）

- [ ] 启动 `scripts/run_demo.ps1`，重置 DB + 种子成功
- [ ] 检查端口：前端 5173 / 后端 8000
- [ ] DeepSeek 冒烟：发一条测试消息返回成功
- [ ] Docker 状态（若用）或确认 local 降级
- [ ] 预录视频文件可播放
- [ ] 无其他进程占用端口；演示机勿睡眠

## 6. 排练计划

- Day 4：第一次全量彩排 + 录制初版视频
- Day 5：黄金链路全量演练 ×3；L2 断网彩排 ×1
- 通过标准：连续 2 次全量演练成功，问题清单清零
