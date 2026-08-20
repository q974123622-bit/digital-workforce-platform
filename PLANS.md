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
- [x]（Sprint 4 通过：deepseek-chat 连通成功；官方接口无 v4-flash 模型名，本地 .env 覆盖为 deepseek-chat；Key 仅存本地 gitignored .env）
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

## Sprint 3 — Enterprise Resource & Security Layer（已完成，2026-08-17）

> 里程碑：正式员工 B 接棒，企业资源与安全层落地（不接真实系统/AgentTeams/Harness）。

### S3-01 Knowledge Adapter
- [x]
  - Owner Role：B
  - Input：契约 Knowledge Adapter Interface、虚构知识库
  - Output：`backend/app/services/knowledge_adapter.py`：统一 `search(employee_id, knowledge_base_id, query, trace_id)`；MockKnowledgeAdapter（读 mock-data/kb/）+ InternalKnowledgeAdapterStub（仅接口与配置结构）
  - Dependency：T1-04
  - Acceptance Criteria：Mock 返回虚构片段；Stub 不接真实内容；调用经 Gateway 不绕过

### S3-02 Knowledge Resource Registry
- [x]
  - Owner Role：B
  - Input：知识库资源模型
  - Output：knowledge_base 表扩展（resource_type/data_level/allowed_employment_type/department_scope）；登记 KB-PUBLIC / KB-INTERNAL / KB-FINTECH
  - Dependency：S3-01
  - Acceptance Criteria：GET /api/v1/knowledge-bases 返回 3 个登记资源及资源字段

### S3-03 安全资源边界
- [x]
  - Owner Role：B（Policy 复用 A 的 Sprint 2 实现）
  - Input：SECURITY_BOUNDARY 权限模型
  - Output：`/internal/knowledge/search` 链路：正式分身 KB-INTERNAL Allow；实习生 DENY；虚拟员工仅授权库
  - Dependency：S3-02、T1-02
  - Acceptance Criteria：测试覆盖 正式 ALLOW / 实习 DENY / 虚拟未授权 DENY

### S3-04 Sandbox Policy（Mock Executor）
- [x]
  - Owner Role：B
  - Input：Sandbox 规则
  - Output：`backend/app/services/sandbox_policy.py`（runtime_location/internet_access/filesystem_scope）+ MockExecutor + `/internal/sandbox/run`（先 Policy 后执行）
  - Dependency：T1-02
  - Acceptance Criteria：remote_only 拒绝 local（POLICY-004）；internet deny 拒绝非 none 网络（POLICY-003）；远程执行 allow；被拒不启动

### S3-05 Secret / Config 与 Audit
- [x]
  - Owner Role：B
  - Input：安全边界规则
  - Output：`backend/app/services/config.py`（环境变量引用，禁止入 Git/Prompt/日志）；AuditEvent 增加 knowledge_base_id
  - Dependency：S3-01
  - Acceptance Criteria：仓库无真实凭据；知识库访问审计含 employee_id/knowledge_base_id/decision/trace_id

---

## Phase 1 — 问答与安全（Day 2–3）

### T1-01 LLM Provider（SAFEMODE）
- [x]（Sprint 4 完成：services/llm.py 统一 chat/tool_call/structured_output；DeepSeekProvider；SAFEMODE 非 demo 段拒发；Key 仅环境变量）
  - Owner Role：A
  - Input：G1 结论、契约
  - Output：统一 Provider（DeepSeek backend、`DEEPSEEK_API_KEY` 唯一持有点、prompt 段 source 标签校验）
  - Dependency：T0-02、T0-05
  - Acceptance Criteria：单测通过（非 demo 段拒发、缺 Key 返回 LLM_UNAVAILABLE）；真实调用成功一次

### T1-02 Policy Engine
- [x]（Sprint 2 完成：services/policy.py 四维评估，POLICY-001~005 + P-DATA-003/P-PLUGIN-007/P-DEFAULT-001 内置规则，身份以 DB 为准不可伪造）
  - Owner Role：B
  - Input：SECURITY_BOUNDARY 权限模型、种子策略
  - Output：`evaluate()` 实现（有序规则、默认拒绝、Deny>Approval>Allow、reason 与 policy_id）
  - Dependency：T0-04
  - Acceptance Criteria：单测 ≥8 场景（实现 18 项：ALLOW/DENY/APPROVAL/Internet Deny/Local Deny/身份防伪造/默认拒绝）

### T1-03 Plugin Gateway
- [x]（Sprint 2 完成：services/gateway.py + routers/internal.py，Identity → Policy → Gateway → Adapter → Audit）
  - Owner Role：B
  - Input：T1-02、契约内部接口
  - Output：`/internal/gateway/invoke`（policy → Mock 凭据 → adapter → audit）
  - Dependency：T1-02
  - Acceptance Criteria：allow 路径返回数据；deny 路径返回 POLICY_DENIED；未知插件默认拒绝；每调用一条审计

### T1-04 插件 Adapter 注册表（Mock）
- [x]（Sprint 2 完成：services/adapters.py 六个 Mock Adapter：knowledge-l1/l2、hr-employee-mcp、adp-onboarding、internet-search、rpa-report）
  - Owner Role：B（契约）+ D（Mock 内容）
  - Input：契约、Mock 知识库
  - Output：knowledge-l1/l2、hr-employee-mcp、adp-onboarding、rpa-report（+internet-search）Mock Adapter
  - Dependency：T0-07
  - Acceptance Criteria：Gateway 可调通全部 Mock 插件，返回结构符合契约

### T1-05 Audit Store
- [x]（Sprint 2 完成：Gateway 每次调用落一条审计，含 allow/deny/approval 全决策；字段齐全 trace_id/employee_id/plugin_id/action/decision/reason/ts/result_summary）
  - Owner Role：B
  - Input：契约 AuditEventDto
  - Output：audit_event 写入与查询（trace_id / employee_id / decision 过滤）
  - Dependency：T0-04
  - Acceptance Criteria：决策、工具调用、审批均落审计；按 trace_id 可聚合

### T1-06 Chat Orchestrator
- [x]（Sprint 4 完成：services/chat.py 严格链路 User→Employee→LLM→Tool→Policy→Gateway→Adapter→LLM→Answer；≤3 轮工具；Deny 卡片；Session 历史）
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
- [x]（Sprint 5 完成：services/team_orchestrator.py 模板拆解 + Worker 执行（走 Gateway）+ 审批挂起/续跑 + LLM 汇总（失败降级模板）；端到端真实验证通过）
  - Owner Role：A
  - Input：契约 TaskRunDto、预置模板
  - Output：task_run + JSON subtasks；模板 + LLM 补全/汇总；状态流转；审批端点
  - Dependency：T1-01、T1-03
  - Acceptance Criteria：发起任务 → 3 子任务 → Approval 挂起 → 批准续跑 → 完成；失败态可返回

## Sprint 5 — TeamTaskOrchestrator（已完成，2026-08-18）

## Sprint 8 — AgentTeams 最小接入（2026-08-19）

> 里程碑：增量接入，保留自研兜底；Harness 作为我方执行引擎。

### S8-01 AgentTeamsGateway（Matrix 客户端）
- [x]
  - Owner Role：A
  - Input：AgentTeams 容器（Docker 恢复）、Matrix 凭据（容器 env）
  - Output：`backend/app/services/agentteams_gateway.py`：login / joined_rooms / send_message / poll_messages / parse_completion；失败统一 AgentTeamsUnavailableError
  - Dependency：容器恢复
  - Acceptance Criteria：Matrix 登录成功、房间可枚举；测试 4 项（Fake Matrix）

### S8-02 群聊任务接入（auto 降级）
- [x]
  - Owner Role：A
  - Input：S8-01、group_chat 任务路径
  - Output：`DWP_TEAM_BACKEND=auto`：任务优先发 AgentTeams 房间并回收汇报回填 TaskRun（source=agentteams）；失败自动降级内置编排（source=builtin）；审计 agentteams:send/receive；TaskRun 增加 source 字段；前端任务卡标注来源
  - Dependency：S8-01
  - Acceptance Criteria：单元测试 121 项全绿（含 agentteams 路径与降级路径）

### S8-03 环境接入验证与降级结论
- [x]
  - Owner Role：A
  - Input：真实 AgentTeams 环境
  - Output：容器恢复、凭据找回（admin/manager/worker/platform-bot）、房间枚举；**初版结论：团队房间为私有且所有可用用户为 guest/未加入，自动发送通道被 Matrix 权限阻塞 → 运行时自动降级内置编排**
  - Dependency：S8-02
  - Acceptance Criteria：降级路径实测通过（121 测试全绿）；结论记录于交接文档

### S8-04 修复 AgentTeams 房间成员关系（自动通道打通）
- [x]（2026-08-20 实测通过）
  - Owner Role：A
  - Input：S8-03 阻塞结论、Matrix/tuwunel 管理机制
  - Output：
    1. 新建正确团队 `team-onboard`（admin=platform-bot + manager(coordinator) + leader=kai + worker=xiaoming），新团队房间 `!yoYNJCwHes3orszqGx` 四成员全部 joined（`tmp/team-onboard.yaml` 可复现）
    2. Manager `groupAllowFrom` 白名单加入 `@platform-bot`（MinIO + 本地挂载，重启 manager 生效）
    3. 群聊任务消息带 `@manager:...` mention（`AGENTTEAMS_MANAGER_MXID` 可配，group_chat 已改）
    4. `backend/.env`（gitignored）更新房间 ID 与 platform-bot 真实 token
  - Dependency：S8-03
  - Acceptance Criteria：platform-bot → 团队房间 → Manager 受理回执 → 派发 kai；网关轮询 parse_completion 命中；后端 121 测试全绿

### S8-05 AgentTeams Worker 对齐平台数字员工
- [x]（2026-08-20 实测通过）
  - Owner Role：A
  - Input：S8-04、平台数字员工名册（VE-0001/0002/0003）
  - Output：
    1. 新建 worker：onboard-assistant（新员工入职助手）/ hr-assistant（HR 助理）/ it-assistant（IT 助理），SOUL 注入人设（`tmp/soul-*.md`）
    2. 重建 team-onboard：组长 onboard-assistant + 组员 hr-assistant、it-assistant（`tmp/team-onboard.yaml`）；删除测试 worker kai/xiaoming
    3. 清 Manager 旧活动任务状态与 stale 会话；群聊白名单补 hr/it-assistant
    4. 网关任务消息附带"先查名册再按角色派活"指令
  - Dependency：S8-04
  - Acceptance Criteria：平台任务 → Manager → onboard-assistant 拆解 → hr/it-assistant 执行并产出交付物（MinIO 实测可见）；后端 121 测试全绿

### S9-01 数字员工生命周期绑定（创建即建容器 + 命名规则）
- [x]（2026-08-20 实测通过）
  - Owner Role：A
  - Input：employees 路由、AgentTeams controller（agt CLI）
  - Output：`agentteams_lifecycle.py`（docker exec agt 封装 + 命名规则 dwp-{type}-{no}）；创建/删除/更新员工级联容器；GET runtime 接口；种子 VE-0001/2/3 绑定 dwp-ve-0001/2/3
  - Dependency：S8-05
  - Acceptance Criteria：POST 员工 → 容器 Running；DELETE → 容器清理；修改人设 → SOUL 更新；runtime 接口返回状态

### S9-02 DeepSeek Harness 执行引擎（壳回调平台）
- [x]（2026-08-20 实测通过）
  - Owner Role：A
  - Output：`POST /internal/harness/execute`（Policy POLICY-HARNESS-001 → Docker dsh → 审计）；实测返回真实 DeepSeek 结果
  - Dependency：S9-01
  - Acceptance Criteria：allow 路径真实执行；local 员工 deny；审计落库

### S9-03 群聊逐人协作反馈
- [x]（2026-08-20 实测通过）
  - Owner Role：A
  - Output：团队任务轮询回传成员动态到会话消息 + subtasks 状态；parse_completion since_ts 过滤；前端消息气泡 ✅/⏳ 徽章
  - Dependency：S8-04、S9-01
  - Acceptance Criteria：真实任务后会话出现 HR/IT 助理逐人动态；subtasks 反映 completed/running；后端 129 + 前端 20 全绿

### S9-04 记忆/会话管理（清空联动 + 防串台 + 持久化去重）
- [x]（2026-08-20 实测通过）
  - Owner Role：A
  - Output：反馈轮询 since_ts 过滤；`agentteams_event_seen` 持久化去重；`reset_agentteams_context()`（state.json/memory/sessions/MinIO 任务目录）；清空会话联动；前端提示
  - Dependency：S9-03
  - Acceptance Criteria：清空后新任务不再串旧任务内容；重启不重放反馈；后端 131 + 前端 20 全绿

### S10-01 关系理顺 + 统一执行链路 + 工作流角色化
- [x]（2026-08-20 实测通过）
  - Owner Role：A
  - Output：分身=demo 组织者（不建容器）；虚拟员工=agentteams；结构化任务消息（task_id/请求者/目标）；parse_completion 按 task_id 匹配；WORKFLOW_META owner_employee；Mock adapter/模板去硬编码；前端来源三态徽章 + 工作流负责人
  - Dependency：S9-04
  - Acceptance Criteria：请假任务带 task_id 且 Manager 围绕 task_id 派单（实测）；内置降级显示"该员工"而非"王小明"；后端 135 + 前端 20 全绿

### S10-02 修复任务原文误当回执
- [x]（2026-08-20 实测通过）
  - Owner Role：A
  - Output：`parse_completion` 增加 `exclude_senders`（排除 platform-bot 自身消息），任务消息不再被当回执；实测任务汇总为 Manager 真实回执
  - Dependency：S10-01
  - Acceptance Criteria：新任务 summary 不含任务原文；后端 136 全绿

### S11-01 任务交互异步化（消除同步阻塞/卡死）
- [x]（2026-08-20 实测通过）
  - Owner Role：A
  - Output：发消息/审批异步化（BackgroundTasks + 独立 Session）；AgentTeams 轮询 60s；去重 2min 且只拦 running/approval；前端 fetch 超时 + 发送后轮询
  - Dependency：S10-02
  - Acceptance Criteria：发消息 <2s 返回且后台正确写回；审批异步续跑；后端 136 + 前端 20 全绿

> 里程碑：团队协作主链路（方案 A：门户自研编排 + Harness 并行尝试 + AgentTeams 留桩）。

### S5-01 TeamTaskOrchestrator
- [x]
  - Owner Role：A
  - Input：契约 §3.6、Policy/Gateway/Audit（Sprint 2 就绪）
  - Output：`backend/app/services/team_orchestrator.py`（TEAM-ONBOARD 模板 3 子任务：HR 制度 -> IT 账号 -> 敏感报表审批）；`POST /teams/{id}/tasks`、`GET /teams/{id}/tasks/{id}`、`POST /tasks/{id}/approve`
  - Dependency：T1-02、T1-03
  - Acceptance Criteria：发起 -> approval 挂起 -> 批准 -> completed（LLM 汇总，失败降级）；审计 trace 贯穿 6 条；测试 7 项

### S5-02 DeepSeek Harness 尝试（门禁 G2）
- [x]（结论：Docker 方案接入成功——`docker/Dockerfile.dsh` 构建 dwp-dsh:rc6 镜像，容器内 dsh headless 真实调用 DeepSeek（直连 4-6s）；Windows 无控制台进程（uvicorn Hidden）内直接调 dsh 或 docker CLI 均慢/不稳定，故服务内默认 demo 模式（0.7s），`DWP_HARNESS_ENABLED=1` 启用 Docker Harness；交互终端/受控环境已验证真实执行）
  - Owner Role：A
  - Input：deepseek-harness 源码（本机外部依赖）
  - Output：dsh headless/API 可调用则接入 RuntimeAdapter harness backend；否则记录降级
  - Dependency：S5-01
  - Acceptance Criteria：可跑则 Worker 执行接 dsh 一轮；不可跑则 UI 标注 Adapter 演示模式，不阻塞

### S5-03 AgentTeams Adapter 桩
- [x]
  - Owner Role：A
  - Input：higress/AgentTeams（外部依赖，K8s/Docker 形态）
  - Output：adapters/ 契约注明预留接入位；Demo 口播"已预留 AgentTeams 协作平台接入"
  - Dependency：无
  - Acceptance Criteria：文档明确本周不接入原因（需 K8s/Docker + Matrix 形态不适配门户），不阻塞主链路

## Sprint 7 — 个人工作中心（职场）（已完成，2026-08-19）

> 里程碑：会议纪要「本周重点」落地——企业微信式个人工作中心 + 技能上传 + 插件/协作能力展示；管理功能收进「管理后台」，职场页面拟人化文案。

### S7-01 职场后端：模型 / 种子 / 接口
- [x]
  - Owner Role：A
  - Input：会议纪要（个人工作中心 / 数字分身 / 技能上传 / 数字员工目录）
  - Output：`Skill`（文本/Markdown，owner_human_no）、`Conversation`（kind=direct|group，participants JSON）、`ConversationMessage`（seq 有序）；`backend/app/routers/workplace.py`：职场聚合 + 技能 CRUD + 会话接口；种子 3 技能 + 2 会话（分身私聊欢迎语 + 入职协作群聊）
  - Dependency：T0-04（模型/种子基线）
  - Acceptance Criteria：direct 幂等复用、group 自动带头分身且拒绝重复/未知成员；聚合仅含 virtual/rpa；越权发消息 403

### S7-02 技能注入与群聊编排
- [x]
  - Owner Role：A
  - Input：ChatOrchestrator（Sprint 4）
  - Output：`handle_message` 向后兼容扩展（system_context / history_override / persist / trace_id）；分身 system prompt 注入启用技能（上限 4000 字符）；`services/group_chat.py` 顺序编排（成员依次独立走 Policy→Gateway→审计，单成员失败降级，全败 503）
  - Dependency：T1-01、T1-03
  - Acceptance Criteria：Fake LLM 断言技能注入与回复顺序；单成员失败继续、全部失败 503；trace 前缀 T-GRP-

### S7-03 前端：企业微信式会话台
- [x]
  - Owner Role：C（A 复核）
  - Input：S7-01/S7-02 接口、共享类型
  - Output：`/workplace` 主入口（`/` 重定向）；侧栏「我的职场 + 管理后台」分组；CurrentUserProvider 身份切换（张三/陈晓萌）；会话列表（我的分身置顶）/ 通讯录（分组+搜索+私聊/群聊）/ 微信气泡对话窗口 / 分身资料抽屉（技能启停+上传 .md/.txt）；Dashboard 移至 `/admin`
  - Dependency：S7-01
  - Acceptance Criteria：会话列表预览与时间；发送消息后成员具名回复与工具卡片；上传技能即时生效；typecheck/build 通过

### S7-04 测试与回归
- [x]
  - Owner Role：D
  - Input：S7-01~S7-03
  - Output：`backend/tests/test_workplace.py` 16 项（技能 CRUD/注入/聚合/幂等/群聊顺序/降级）；前端 WorkplacePage.test 4 项 + App/AppLayout 路由改造
  - Dependency：S7-01~S7-03
  - Acceptance Criteria：后端 pytest 100 全绿；前端 vitest 16 全绿；typecheck/build 通过；golden_chain.py 8/8 回归

### S7-05 协作空间接入团队任务编排（C 档）
- [x]
  - Owner Role：A
  - Input：TeamTaskOrchestrator（Sprint 5）、职场群聊（S7-01~S7-03）
  - Output：`POST /conversations/{id}/messages` 群聊分发——分身 LLM 判断「任务/闲聊」（失败默认闲聊）；任务型追加分身受理消息并调 `create_conversation_task`（拆解 1-3 子任务指派成员 → Gateway 执行 → 审批 → Leader 汇总；请求关键词兜底指派对应 workflow）；闲聊仅单成员回复（点名命中则那位回，否则分身回）；`ConversationOut.tasks` 返回任务列表；`SubtaskExecutor` 接口（默认 GatewaySubtaskExecutor，真实 RPA/Workflow/Harness 后续接入）；新增 5 个虚构 workflow/RPA 插件（报销/请假/纪要/周报/采购）+ 种子任务 3 条（待审批 1 + 已完成 2）
  - Dependency：S7-01、S7-02
  - Acceptance Criteria：任务消息 → TaskRun 落库并完成/挂起审批；闲聊单回复；点名路由；审批通过→completed+汇总、拒绝→denied；前端任务卡片（子任务/审批按钮/汇总）+ running/approval 轮询；后端 109 项、前端 18 项、build 全绿

### T2-02 Harness 集成尝试（门禁 G2）
- [ ]
  - Owner Role：A
  - Input：deepseek-harness 外部依赖、G1
  - Output：RuntimeAdapter `harness` backend（`pnpm dsh --profile headless`）或 `demo` backend；G2 结论
  - Dependency：T1-06
  - Acceptance Criteria：G2 通过 → VE-0001 真实 Harness 一轮回答；不通过 → demo 模式可用且 UI 标注，不阻塞主链路

### T2-03 Sandbox Manager（门禁 G3）
- [ ]（Sprint 3 已完成 Sandbox Policy + Mock Executor + /internal/sandbox/run；Docker 真启动留待 G3 门禁通过后）
  - Owner Role：B
  - Input：SECURITY_BOUNDARY Sandbox 规则、docker/ 目录
  - Output：Docker 后端（network=none、挂载 /workspace/{employee_id}、超时）+ local 后端；Sandbox 决策审计
  - Dependency：T0-05
  - Acceptance Criteria：Docker 可用时真实启动并返回 mode=docker；不可用自动 local 且审计记录；被拒请求不启动

### T2-04 前端：Team 群聊/任务页
- [x]（2026-08-18 完成：Teams 页新增「任务协作」标签——发起任务表单、状态徽章、子任务进度（完成/待审批/执行中）、审批通过/拒绝按钮、Leader 汇总卡、运行中自动轮询 3s）
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
- [x]（2026-08-18 完成：scripts/run_demo.ps1 一键启动——依赖检查、种子重置、启动前后端、可选 -Docker（构建 dwp-dsh 镜像 + 启用 Harness）、-NoReset 跳过；实测 8000/5173 就绪 + 健康检查通过）
  - Owner Role：D
  - Input：全部运行方式
  - Output：`scripts/reset_demo.ps1`（重建 DB + 种子 + 起前后端 + 可选 docker）、`run_demo.ps1`
  - Dependency：T0-04、T0-05、T0-06
  - Acceptance Criteria：新机器可 30 秒内一键恢复演示环境（当前 init_demo.ps1 已覆盖环境+种子，起服务部分未覆盖）

### T3-02 黄金链路端到端验证
- [x]（2026-08-18 完成：scripts/golden_chain.py 可重复联调脚本，8/8 通过——健康检查 / 正式分身问答 Allow / 实习生 Deny(POLICY-002) / RAG 向量检索命中 / 团队任务 3 子任务审批挂起 / 审批完成+LLM 汇总 / 审计 trace 贯穿 / 会话历史持久化）
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

## Sprint 12 — AgentTeams × DeepSeek Harness 协同重构（2026-08-20）

### S12-01 可信任务状态机
- [x] 用户消息以精确 `trigger_message_seq` 关联后台任务，同一会话进程内串行处理。
- [x] 审批通过后重新进入 Policy → Gateway → Adapter → Harness，禁止“批准即伪装完成”。
- [x] 普通执行异常落 `failed`，清空会话后通过触发消息存在性检测取消后台回写。
- [x] AgentTeams 任务发送成功后不再自动重复走内置业务执行。

### S12-02 AgentTeams 协作 + Harness 执行
- [x] 同一个 `TaskRun` 贯穿“分身规划 → AgentTeams 讨论/认领 → Gateway/Harness 执行 → 审批 → 汇总”。
- [x] AgentTeams 消息协议要求携带 `task_id`；ACK 与 `TASK_COMPLETED` 分离。
- [x] `collaboration_status/messages` 与 `execution_mode` 暴露给任务卡片，展示协作过程和执行引擎。
- [x] Matrix 不可用且尚未发送时安全降级；已发送后中断则失败停机，避免双执行。

### S12-03 数字员工角色与生命周期
- [x] 数字分身固定为 `twin + demo`，只承担用户代理、任务组织和最终汇总。
- [x] 入职任务按 HR 助理 + IT 助理分工；仅明确要求权限报表时加入 RPA 审批任务。
- [x] 新增 `RPA-0001 自动化报表机器人`、`VE-0004 采购助理`，移除 IT 助理的报表/采购越界授权。
- [x] 创建/切换/删除员工时校验 Owner、运行时类型、活动任务和 AgentTeams 生命周期。

### S12-04 工程与安全修复
- [x] AgentTeams MinIO 密码和 Manager 房间信息移出源码，改为环境变量。
- [x] Harness 本机调用改为 argv + `shell=False`；Sandbox 容器补 CPU/内存/PID 限制。
- [x] 旧 SQLite 的 `plugin.runtime_meta` 增加轻量兼容迁移；前后端契约同步。
- [x] 前端按本次用户消息 seq 轮询，不再被历史消息误判为已回复。

### S12-05 后续生产化门禁
- [ ] 用持久化队列替代 FastAPI `BackgroundTasks`，实现进程重启恢复、租约和重试。
- [ ] 统一认证中间件与 RBAC，替代请求体中的演示 `actor_no`；封闭 `/internal/*`。
- [ ] 为多 Uvicorn worker / PostgreSQL 增加数据库唯一约束和乐观锁。
- [ ] 在受控环境验证真实 AgentTeams worker 协议、真实 Harness 镜像和审批后真实 Adapter。
## Sprint 13：统一能力契约与 Harness 执行边界（2026-08-20）

- [x] 定义 Capability Contract v1.0，统一 Skill/Plugin 的目录字段和就绪检查
- [x] 将 Skill 明确为不可执行 instruction capability，限制长度并加强提示词边界
- [x] 为 Plugin 增加 actions/input_schema/executor/fallback 契约
- [x] 将 Harness 移入 Gateway Adapter 上游：员工 Harness 先规划，Adapter 作为受控工具调用一次
- [x] Harness 未启用/失败时仅降级调用一次 Adapter，并显式标记 Demo Adapter 降级
- [x] TeamTaskOrchestrator 移除业务执行后的 Harness 二次调用
- [x] Gateway 校验插件状态、契约、动作；Policy grant 精确匹配 action
- [x] 新增统一能力目录 API 与前端能力中心
- [x] 补充员工独立 Harness 上下文、Adapter 单次工具调用、契约目录、所有权与完整性测试
