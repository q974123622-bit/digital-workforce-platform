# 实施计划与任务清单

> 版本：v0.3（2026-08-17，Sprint 1.5 Architecture Freeze）
> 用法：`- [ ]` 未完成，`- [x]` 已完成。每个任务必须满足 Acceptance Criteria 才能勾选。
> Owner Role：A=正式（架构/总装），B=正式（安全/企业资源），C=实习生（前端），D=实习生（Mock/测试），All=全员。
> 门禁：G1=DeepSeek 连通性（Day 1），G2=Harness 真接（Day 3），G3=Docker Sandbox（Day 4）。不过门禁即启用对应降级。

## 里程碑总览

| 天 | 目标 | 关键产出 | 门禁 |
|---|---|---|---|
| Day 1 | 仓库卫生 + 契约 + 骨架 | 契约冻结、DB 种子、前后端骨架 | G1 DeepSeek 连通性 |
| Day 2 | 问答与安全 | LLM、Policy、Gateway、Audit、聊天页 | — |
| Day 3 | 团队协作 | TeamTaskOrchestrator、审批、安全页 | G2 Harness |
| Day 4 | 隔离与联调 | Sandbox、测试、首轮彩排录屏 | G3 Docker |
| Day 5 | 收尾演示 | 全量演练、正式录屏、门禁复核 | — |

## Phase 0 — 项目启动（Day 1）

### T0-01 仓库初始化与卫生
- [x]
  - Owner Role：A（B 复核）
  - Input：工作区现状（无 git、含 deepseek-harness/、higress/）
  - Output：git 仓库、.gitignore（deepseek-harness/、higress/、.env、node_modules/、__pycache__/、*.db、secure 路径）、README
  - Dependency：无
  - Acceptance Criteria：`git status` 无敏感文件；`higress/tools/appservice_tokens.txt` 已移出工作区（备份至工作区外 external_tokens_backup/，2026-08-17）；README 记录外部依赖版本

### T0-02 DeepSeek 连通性冒烟（门禁 G1）
- [ ]
  - Owner Role：A
  - Input：`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`（可配置）
  - Output：`.env.example`；冒烟脚本；连通性结论（通过/代理方案/降级预案）
  - Dependency：无
  - Acceptance Criteria：真实 Key 环境下一次 chat 调用返回成功；G1 结论记录在 docs 或演示脚本中；Key 不出现在任何提交文件

### T0-03 API 契约冻结
- [x]
  - Owner Role：A（B/C/D 评审）
  - Input：docs/API_CONTRACT.md 草案
  - Output：OpenAPI 导出、前端 TS 类型、契约 v1.0 评审通过
  - Dependency：无
  - Acceptance Criteria：docs/API_CONTRACT.md 定稿（v0.1，2026-08-17）；shared-schema/types.ts 与后端 OpenAPI 手动同步；C/D 可独立开发

### T0-04 数据模型与种子数据
- [x]
  - Owner Role：A（Schema）+ D（数据内容）
  - Input：契约 DTO、SECURITY_BOUNDARY 数据分级
  - Output：SQLAlchemy 表（human_employee、digital_employee、plugin、employee_plugin_grant、policy、audit_event、team、team_member、knowledge_base、chat_session、chat_message、task_run）；mock-data 种子 JSON
  - Dependency：T0-03
  - Acceptance Criteria：`scripts/init_demo.ps1` 可重建 DB 并灌入虚构种子（2 正式 + 2 实习 + 2 分身 + 3 虚拟 + 5 插件 + 4 知识库 + 1 Team）；无真实内容；L1/L2 标记正确

### T0-05 后端骨架
- [x]
  - Owner Role：A
  - Input：契约、表模型
  - Output：FastAPI 应用（core/config、异常处理、CORS、/health）
  - Dependency：T0-03
  - Acceptance Criteria：uvicorn 启动，/health 200；employee/plugin/policy/audit CRUD 与 knowledge/teams 只读接口可用；pytest 10 用例通过

### T0-06 前端骨架
- [x]
  - Owner Role：C
  - Input：契约、种子数据样例
  - Output：Vite + React + TS + AntD 工程，路由与页面占位，Mock 数据可浏览
  - Dependency：T0-03
  - Acceptance Criteria：Vite dev 可浏览（首页/员工列表/详情/插件/安全/Team 占位）；typecheck 通过；vitest 冒烟通过；`vite build` 通过

### T0-07 Mock 知识库内容
- [x]
  - Owner Role：D
  - Input：数据分级规则、契约
  - Output：虚构 L1/L2 知识文档（结构仿真、内容虚构）
  - Dependency：T0-04
  - Acceptance Criteria：mock-data/kb/ 共 4 篇（L1 公开 1 + L2 三类 3），内容全部虚构并带声明；可按 data_level 检索；无真实内部信息

## Sprint 1.5 — Architecture Freeze & Handoff（已完成，2026-08-17）

> 里程碑：不新增业务功能，把 Sprint 1 骨架整理为稳定基线并冻结契约，供后续两名正式员工串行开发。

### S1.5-01 目录整理
- [x]
  - Owner Role：A（D 复核）
  - Input：Sprint 1 仓库
  - Output：`mock-data/`（由 demo-data 更名，同步 seed.py / seed.json / README / docs 引用）；`adapters/README.md`（预留目录说明）；`tests/README.md`（测试布局索引）
  - Dependency：T0-01~T0-07
  - Acceptance Criteria：全仓无 `demo-data` 残留引用；seed 重建正常；测试运行不受影响

### S1.5-02 架构与安全文档冻结
- [x]
  - Owner Role：A（B 评审）
  - Input：Sprint 1 实现 + v0.2 文档
  - Output：docs/ARCHITECTURE.md v0.3（五层架构 + 实现状态 + 统一资源访问链 + 数据模型决策）；docs/SECURITY_BOUNDARY.md v0.3（角色边界 / 数据分级 / 调用链强制 / 仓库卫生）
  - Dependency：S1.5-01
  - Acceptance Criteria：文档与代码实现一致；明确「业务模块不得直连知识库/DB/Workflow/RPA」

### S1.5-03 API 契约冻结 v1.1
- [x]
  - Owner Role：A（B/C/D 评审）
  - Input：实际路由实现 + v1.0 契约
  - Output：docs/API_CONTRACT.md v1.1：Employee / Policy / Plugin / Audit / Chat / Runtime Adapter / Knowledge Adapter 七组接口；已实现与待实现状态标注；EmployeeDto 平铺对齐实现
  - Dependency：S1.5-02
  - Acceptance Criteria：C/D 可按契约独立开发；变更登记表更新

### S1.5-04 交接文档
- [x]
  - Owner Role：A
  - Input：S1.5-02 / S1.5-03
  - Output：docs/DEVELOPMENT_HANDOFF.md（Sprint 1 完成情况、启动方式、测试情况、Mock 数据位置、下一阶段负责人、允许修改目录、冻结接口清单）
  - Dependency：S1.5-03
  - Acceptance Criteria：两名正式员工无需追问即可接手

### S1.5-05 全量验证与 Git Checkpoint
- [x]
  - Owner Role：A
  - Input：整理后的仓库
  - Output：后端 pytest 全绿；前端 typecheck / vitest / build 全绿；单实例后端 + 前端可运行；git 提交 checkpoint
  - Dependency：S1.5-01~S1.5-04
  - Acceptance Criteria：`git status` 干净；README 与交接文档命令可复现

---

## Phase 1 — 问答与安全（Day 2–3）

### T1-01 LLM Provider（SAFEMODE）
- [ ]
  - Owner Role：A
  - Input：G1 结论、契约
  - Output：统一 Provider（DeepSeek backend、`DEEPSEEK_API_KEY` 唯一持有点、prompt 段 source 标签校验）
  - Dependency：T0-02、T0-05
  - Acceptance Criteria：单测通过（非 demo 段拒发、缺 Key 返回 LLM_UNAVAILABLE）；真实调用成功一次

### T1-02 Policy Engine
- [ ]
  - Owner Role：B
  - Input：SECURITY_BOUNDARY 权限模型、种子策略
  - Output：`evaluate()` 实现（有序规则、默认拒绝、Deny>Approval>Allow、reason 与 policy_id）
  - Dependency：T0-04
  - Acceptance Criteria：单测 ≥8 场景，含 L3 deny、实习生 deny、remote-only、未授权插件 deny

### T1-03 Plugin Gateway
- [ ]
  - Owner Role：B
  - Input：T1-02、契约内部接口
  - Output：`/internal/gateway/invoke`（policy → Mock 凭据 → adapter → audit）
  - Dependency：T1-02
  - Acceptance Criteria：allow 路径返回数据；deny 路径返回 POLICY_DENIED；未知插件默认拒绝；每调用一条审计

### T1-04 插件 Adapter 注册表（Mock）
- [ ]
  - Owner Role：B（契约）+ D（Mock 内容）
  - Input：契约、Mock 知识库
  - Output：knowledge-l1/l2、hr-employee-mcp、adp-onboarding、rpa-report 四个 Adapter
  - Dependency：T0-07
  - Acceptance Criteria：Gateway 可调通全部 Mock 插件，返回结构符合契约

### T1-05 Audit Store
- [ ]（Sprint 1 已完成 CRUD 部分：audit_event 写入与按 trace_id/employee_id/decision 过滤查询；决策/工具调用/审批落审计待 T1-02/T1-03 完成后收口）
  - Owner Role：B
  - Input：契约 AuditEventDto
  - Output：audit_event 写入与查询（trace_id / employee_id / decision 过滤）
  - Dependency：T0-04
  - Acceptance Criteria：决策、工具调用、审批均落审计；按 trace_id 可聚合（审计事件源待 Policy/Gateway 接通）

### T1-06 Chat Orchestrator
- [ ]
  - Owner Role：A
  - Input：T1-01、T1-03、契约
  - Output：SSE 聊天 + 轻量 Agent 循环（≤3 轮工具，仅经 Gateway）；Policy Denied 卡片事件
  - Dependency：T1-01、T1-03
  - Acceptance Criteria：DT-E10281 问答带工具卡片；DT-E20999 问 L2 返回 POLICY_DENIED

### T1-07 前端：员工列表/详情/聊天页
- [ ]
  - Owner Role：C
  - Input：契约、T1-06 行为
  - Output：列表、详情（身份/Runtime/Sandbox/插件授权）、聊天页（流式/卡片/Denied 态）
  - Dependency：T0-06、T1-06
  - Acceptance Criteria：与真实后端联调通过；Deny 卡片正确渲染

### T1-08 前端：插件中心/安全中心/审计页
- [ ]
  - Owner Role：C
  - Input：契约、种子策略/Sandbox/审计
  - Output：插件登记与授权表单、策略只读展示、Sandbox 状态、审计列表与 Trace 时间线
  - Dependency：T0-06、T1-02、T1-05
  - Acceptance Criteria：页面展示种子数据；授权提交生效

## Phase 2 — 团队协作与隔离（Day 3–4）

### T2-01 TeamTaskOrchestrator
- [ ]
  - Owner Role：A
  - Input：契约 TaskRunDto、预置模板
  - Output：task_run + JSON subtasks；模板 + LLM 补全/汇总；状态流转；审批端点
  - Dependency：T1-01、T1-03
  - Acceptance Criteria：发起任务 → 3 子任务 → Approval 挂起 → 批准续跑 → 完成；失败态可返回

### T2-02 Harness 集成尝试（门禁 G2）
- [ ]
  - Owner Role：A
  - Input：deepseek-harness 外部依赖、G1
  - Output：RuntimeAdapter `harness` backend（`pnpm dsh --profile headless`）或 `demo` backend；G2 结论
  - Dependency：T1-06
  - Acceptance Criteria：G2 通过 → VE-0001 真实 Harness 一轮回答；不通过 → demo 模式可用且 UI 标注，不阻塞主链路

### T2-03 Sandbox Manager（门禁 G3）
- [ ]
  - Owner Role：B
  - Input：SECURITY_BOUNDARY Sandbox 规则、docker/ 目录
  - Output：Docker 后端（network=none、挂载 /workspace/{employee_id}、超时）+ local 后端；Sandbox 决策审计
  - Dependency：T0-05
  - Acceptance Criteria：Docker 可用时真实启动并返回 mode=docker；不可用自动 local 且审计记录；被拒请求不启动

### T2-04 前端：Team 群聊/任务页
- [ ]
  - Owner Role：C
  - Input：契约 TaskRunDto、T2-01
  - Output：团队列表、任务发起、子任务状态、审批卡、汇总展示
  - Dependency：T1-08、T2-01
  - Acceptance Criteria：与后端联调；审批卡点击后状态更新

### T2-05 自动化测试
- [ ]
  - Owner Role：D
  - Input：契约、种子
  - Output：pytest 覆盖 Policy、Gateway、Chat、Task、Audit、Sandbox 降级
  - Dependency：T1-02、T1-03、T1-06、T2-01
  - Acceptance Criteria：全绿；含越权直呼插件拒绝用例

### T2-06 Dashboard KPI 与 SSE 优化（弹性）
- [ ]
  - Owner Role：A（SSE）+ C（KPI UI）
  - Input：契约
  - Output：首页 KPI 聚合接口与展示；SSE 流式（若无时间则整段返回）
  - Dependency：T0-06、T1-06
  - Acceptance Criteria：可选；不做则整段返回，验收不阻塞

## Phase 3 — 联调收尾（Day 4–5）

### T3-01 一键启动/重置脚本
- [ ]（Sprint 1 已完成 init_demo.ps1：重建 DB + 种子 + 依赖安装；reset_demo.ps1 / run_demo.ps1 与"30 秒一键恢复"留待 Sprint 3 收口）
  - Owner Role：D
  - Input：全部运行方式
  - Output：`scripts/reset_demo.ps1`（重建 DB + 种子 + 起前后端 + 可选 docker）、`run_demo.ps1`
  - Dependency：T0-04、T0-05、T0-06
  - Acceptance Criteria：新机器可 30 秒内一键恢复演示环境（当前 init_demo.ps1 已覆盖环境+种子，起服务部分未覆盖）

### T3-02 黄金链路端到端验证
- [ ]
  - Owner Role：A（B/C/D 参与）
  - Input：docs/DEMO_SCENARIO.md 10 步
  - Output：AC-01~08 全通过记录
  - Dependency：T1-06、T1-08、T2-01、T2-03
  - Acceptance Criteria：10 步脚本连跑通过；每条 AC 有对应证据

### T3-03 降级彩排
- [ ]
  - Owner Role：A（B 支持）
  - Input：降级预案 L0/L1/L2
  - Output：断网/Key 失效/Docker 停/无 Harness 四种场景演练记录
  - Dependency：T3-02
  - Acceptance Criteria：L2 静态演示可完整讲 20 分钟；预录视频可播放

### T3-04 录屏与演示脚本
- [ ]
  - Owner Role：A + C
  - Input：T3-02 通过的链路
  - Output：8–10 分钟正式录屏、口播稿、演示时长控制表
  - Dependency：T3-02
  - Acceptance Criteria：成片可独立播放；口播稿含"虚构数据 + P0-lite"声明

### T3-05 安全门禁复核
- [ ]
  - Owner Role：B
  - Input：SECURITY_BOUNDARY 门禁清单
  - Output：仓库扫描结果、SAFEMODE 验证、越权直呼拒绝用例
  - Dependency：T1-01、T1-02、T2-05
  - Acceptance Criteria：无真实 Token/数据/Endpoint；全部门禁通过

### T3-06 全员演练
- [ ]
  - Owner Role：A
  - Input：演示脚本与录屏
  - Output：3 次全量彩排记录 + 问题清单清零
  - Dependency：T3-02、T3-03
  - Acceptance Criteria：连续 2 次全量演练成功；问题清单关闭

## 每日协作纪律

- [ ] Day 1–5 每日傍晚 30 分钟同步站会（A 主持）
  - Owner Role：All
  - Input：当日变更
  - Output：当日合并、阻塞清单、次日目标
  - Dependency：—（贯穿全周）
  - Acceptance Criteria：主分支每日可运行；阻塞项有明确 Owner 与截止时间
