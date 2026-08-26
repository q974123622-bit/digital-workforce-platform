# 开发交接文档（Sprint 1.5 Handoff）

> 版本：v1.0（2026-08-17）
> 目的：让两名正式员工（A 架构/总装、B 安全/企业资源）在本基线上串行开发，实习生（C 前端、D Mock/测试）按契约并行。
> 状态：Sprint 13 能力契约与执行边界重构已完成（2026-08-20）；下方早期 Sprint 记录保留用于追溯，最新状态以 1.23 为准。

## 1.23 Sprint 13：统一 Capability Contract + Harness 执行边界（2026-08-20）

- 新增 `Capability Contract v1.0`：Skill 与 Plugin 共用目录 DTO。Skill 明确为不可执行 instruction；Plugin 声明 actions、input_schema、executor、fallback、ready/issues。
- 新增 `GET /api/v1/capabilities?actor_no=` 和前端“能力中心”，统一展示个人 Skills 与平台 Plugins，并显示契约版本、执行器和就绪状态。
- MCP/Workflow/RPA 默认声明 `executor.primary=harness, tool=adapter, fallback=demo_adapter`：员工 Harness 先形成工具调用计划，Gateway 再调用一次 Adapter 工具；Harness 不可用时仍只调用一次 Adapter，并明确标记 `Demo Adapter 降级`。
- VE-0002、VE-0003、RPA-0001、VE-0004 使用独立 DSH_HOME/workspace；Prompt 注入工号、人设、职责、Task ID、用户任务、子任务和 AgentTeams 协作结论。
- TeamTaskOrchestrator 已移除 Gateway 完成后的 Harness 二次调用；执行模式由 Gateway 返回并写入子任务。
- Gateway 增加插件状态、契约就绪度、契约动作校验；Policy grant 精确匹配 action；拒绝均写审计。
- Skill 更新/删除增加 owner 校验和字段长度/状态约束；插件存在授权时禁止直接删除。
- 验证：后端 147 项、前端 20 项、TypeScript 类型检查与生产构建全部通过；Docker 冒烟已验证 Harness 计划与 Adapter 工具回执同时返回。

## 1.22 Sprint 12：AgentTeams × DeepSeek Harness 协同执行重构（2026-08-20）

- **职责拆分**：数字分身只负责识别意图、组织团队和汇总；AgentTeams 只负责讨论、认领、风险提示；真正业务动作统一走 `Identity → Policy/Plugin Gateway → 员工 Harness → Adapter Tool`。AgentTeams 消息被明确禁止直接产生业务副作用。
- **唯一任务实例**：先持久化 TaskRun，再携带同一 `task_id` 发起协作。反馈按任务 ID、参与者、时间窗口和 Matrix event ID 过滤；ACK 不再算完成；协作超时后继续执行同一 TaskRun，不再创建一份内置降级任务。
- **审批修复**：批准后子任务回到 pending 并重新执行，只有 Adapter 返回结果才进入 completed；审批人必须是当前会话所有者或正式员工。
- **并发/交互修复**：会话按触发消息 seq 绑定；同一会话串行处理；前端只在收到晚于本次消息的回复或对应任务后停止轮询；普通清空只清本地会话，不再重置全局 AgentTeams Manager。
- **角色重构**：保留 VE-0001 入职协调、VE-0002 HR、VE-0003 IT；新增 RPA-0001 报表自动化和 VE-0004 采购助理。周报/报表授权迁到 RPA，采购授权迁到采购助理；数字分身不创建 worker。
- **配置/生命周期**：worker 模型与 runtime 改由 `AGENTTEAMS_WORKER_MODEL` / `AGENTTEAMS_WORKER_RUNTIME` 配置；创建、切换、删除增加外部资源补偿；活动任务执行者不能删除。
- **验证**：后端 137 项、前端 20 项、TypeScript 类型检查和生产构建通过。生产化仍需持久队列、统一认证/RBAC、数据库级并发锁和真实 AgentTeams/Harness 受控环境压测。

## 1.13 黄金链路联调（T3-02，2026-08-18）

- **脚本**：`scripts/golden_chain.py`（`cd backend; .\.venv\Scripts\python.exe ..\scripts\golden_chain.py`），8 步全链路可重复验证。
- **结果**：8/8 通过——健康检查；正式分身问内部制度 Allow；实习生同问 POLICY-002 Deny；RAG 向量检索命中 KB-IT-SERVICE；团队任务 3 子任务审批挂起；审批完成 + Leader 汇总；审计 trace 贯穿（create/execute×3/approve/summarize）；会话历史持久化。
- **注意**：内部接口（`/internal/knowledge/search` 等）不带 `/api/v1` 前缀，契约 §6 定义于 `/internal/`。

## 1.14 前端 Team 任务页（T2-04，2026-08-18）

- **页面**：`frontend/src/pages/Teams.tsx` 新增「任务协作」标签：发起任务（TextArea + 按钮）、任务状态徽章、子任务进度卡（左侧色条 + 状态 Tag + 结果摘要）、敏感操作审批 Alert、审批通过/拒绝按钮（默认审批人 E10281）、Leader 汇总卡；任务在 pending/running/approval 状态每 3 秒轮询刷新。
- **接口**：`api.createTask / getTask / approveTask` 已封装；端到端验证：create→approval（3 子任务）→ getTask→approval → approve→completed + LLM 汇总。

## 1.15 一键启动/重置脚本（T3-01，2026-08-18）

- **脚本**：`scripts/run_demo.ps1`（推荐演示用）：依赖检查 → 种子重置（`-NoReset` 跳过）→ 启动后端（8000，日志 backend/uvicorn-*.log）→ 启动前端（5173，日志 frontend/vite-dev*.log）→ 健康检查确认；`-Docker` 可选：构建 dwp-dsh 镜像并启用 Harness 模式（`DWP_HARNESS_ENABLED=1`）。
- **注意**：脚本含中文，文件带 UTF-8 BOM（Windows PowerShell 5.1 需 BOM 才能正确解析）。

## 1.16 AgentTeams 最小接入（Sprint 8，2026-08-19）

- **代码接入已完成**：`backend/app/services/agentteams_gateway.py`（Matrix Client-Server API：login/joined_rooms/send/poll/parse）；`group_chat` 任务路径 `DWP_TEAM_BACKEND=auto`——任务优先发 AgentTeams 房间并回收汇报（TaskRun.source=agentteams），失败自动降级内置编排（source=builtin）；审计 `agentteams:send/receive`；前端任务卡标注来源。
- **房间成员关系已修复（2026-08-20）**：原 `.env` 指向的团队房间（`test-hadm-1958.teamRoomID`）成员全为 guest/未加入，自动发送通道被 Matrix 权限阻塞。修复动作：
  1. 经 controller 新建正确团队 `team-onboard`（`agt apply -f tmp/team-onboard.yaml`：admin=platform-bot、humanMembers=[manager(coordinator)]、leader=kai、worker=xiaoming），新团队房间 `!yoYNJCwHes3orszqGx` 四名成员（admin/manager/kai/xiaoming）**全部 joined**，`guest_access=can_join`。
  2. 根因二：Manager 的 Matrix 通道 `groupAllowFrom` 白名单不含 platform-bot，群聊消息被忽略。已在 MinIO `agents/manager/openclaw.json` + 本地挂载写入 `@platform-bot` 白名单（manager 容器重启后生效；controller 合并逻辑会保留该增量）。
  3. 根因三：Manager 只处理群聊房间中 **@mention 自己的消息**（AGENTS.md @Mention 协议）。`group_chat._try_agentteams_task` 已改为发送 `@manager:... [平台任务] ...`（MXID 由 `AGENTTEAMS_MANAGER_MXID` 配置，默认值已内置）。
  4. 本地 `backend/.env`（gitignored）已更新：`AGENTTEAMS_ROOM_ID=!yoYNJCwHes3orszqGx:...`、`AGENTTEAMS_MATRIX_TOKEN`=platform-bot 真实 token（非 guest）。
  **端到端实测通过**：platform-bot 发 `@manager [平台任务]` → Manager 受理并在团队房间回执（含任务 ID/执行人/状态）→ 派发 `task-20260820-011713` 给 kai 执行；平台网关轮询 `parse_completion` 命中回执，TaskRun.source=agentteams 链路可用。
- **已知限制**：Manager→Worker 的任务文件（MinIO `shared/tasks/...`）存在偶发未落盘/同步延迟（与旧测评一致：taskflow ack/submit 协议不匹配），Worker 侧执行可能重试或卡住；平台侧 `DWP_TEAM_BACKEND=auto` 超时自动降级内置编排，演示有兜底。
- **测试**：后端 121 项全绿（含网关单元 3 + agentteams 路径 + 降级路径）。

## 1.17 AgentTeams Worker 对齐数字员工（2026-08-20）

- **问题**：AgentTeams 执行实例此前是测试 worker（kai/xiaoming），与平台数字员工（VE-0001 新员工入职助手 / VE-0002 HR 助理 / VE-0003 IT 助理）不对应，演示语义错位。
- **修复**：
  1. 新建三个数字员工 worker（copaw + deepseek-v4-flash，SOUL 注入人设）：`onboard-assistant`（新员工入职助手）、`hr-assistant`（HR 助理）、`it-assistant`（IT 助理）；人设文件 `tmp/soul-*.md` 可复现。
  2. 重建团队 `team-onboard`（`tmp/team-onboard.yaml`）：组长 onboard-assistant，组员 hr-assistant + it-assistant，admin=platform-bot、manager 为 coordinator；团队房间 `!yoYNJCwHes3orszqGx` 五成员全部 joined。
  3. 删除旧测试 worker kai/xiaoming（`agt delete worker`，容器已清）；清空 Manager `state.json` 的旧活动任务与其 stale 会话，避免 Manager 继续派活给已删除 worker。
  4. Manager 群聊白名单（`groupAllowFrom`）补充 hr-assistant / it-assistant；平台网关任务消息附带"先 `agt get workers` 确认名册再按角色派活"的指令（`group_chat._try_agentteams_task`），演示路由稳定。
- **端到端实测**：平台发"新员工岳灵珊入职" → Manager 受理并交组长 onboard-assistant → 组长 @mention hr-assistant（制度与材料）与 it-assistant（账号与权限）→ 两助手真实执行并产出交付物（MinIO `teams/team-onboard/shared/tasks/task-20260820-021701/`：it-part、HR 汇总、result.md）→ 房间内 TASK_COMPLETED。
- **测试**：后端 121 项全绿；前端 20 项。

## 1.18 Sprint 9：数字员工生命周期化 + Harness 执行引擎 + 群聊逐人反馈（2026-08-20）

- **生命周期绑定**：`backend/app/services/agentteams_lifecycle.py` 封装 `docker exec agentteams-controller agt ...`（create/delete/get worker、team apply）。命名规则 `dwp-{ve|twin|rpa}-{工号}`（VE-0001→dwp-ve-0001、DT-E10281→dwp-twin-e10281）。
- **创建即建容器**：`POST /api/v1/employees` 默认 `runtime_type=agentteams`——生成工号 → 自动建 worker 容器（SOUL=role_prompt，deepseek-v4-flash/copaw）→ 加入 team-onboard → 回填 runtime_ref；`DELETE` 先从团队移除再删容器；`PUT` 同步 SOUL；`GET /api/v1/employees/{no}/runtime` 返回实例状态。
- **DeepSeek Harness 执行引擎**：`POST /internal/harness/execute`——Policy（POLICY-HARNESS-001 远程允许）→ `DockerHarnessRuntimeAdapter`（dwp-dsh:rc6 真执行）→ 审计 `harness:execute`。实测 5.6s 返回真实 DeepSeek 结果。
- **群聊逐人反馈**：团队任务轮询时把每个数字员工在房间的动态写入 `ConversationMessage`（按 sender MXID→员工映射），同步 `TaskRun.subtasks` 状态（completed/running）；`AgentTeamsGateway.parse_completion` 支持 `since_ts` 过滤旧消息，避免误匹配历史汇报。
- **修正**：`TeamTaskOrchestrator._to_out` 补传 `source` 字段（此前 API 响应 source 恒为 builtin）。
- **测试**：后端 129 项全绿（新增 8 项：lifecycle 4 + harness 3 + 群聊反馈 1）；前端 20 项全绿。

## 1.19 记忆/会话管理修复（2026-08-20）

- **问题**：反馈轮询会把历史任务（如宋青书）的消息写进新任务（赵仁杰）会话；去重仅存内存（重启重放）；"清空会话"只删平台本地，AgentTeams 侧记忆/任务状态残留。
- **修复**：
  1. 反馈轮询只回传**任务发送之后**的消息（`since_ts` 过滤），历史任务不再串会话。
  2. 新增 `agentteams_event_seen` 表做**持久化去重**（按 event_id），进程重启/重复轮询不重放。
  3. `agentteams_lifecycle.reset_agentteams_context()`：清空 Manager `state.json`（active_tasks）、memory/会话/缓存文件、MinIO 旧任务目录。
  4. `DELETE /api/v1/conversations/{id}`（清空会话）联动调用重置，并清理该会话已回传事件记录；前端按钮提示"将重置数字员工记忆"。
- **验证**：清空后发"赵仁杰"任务，会话不含"宋青书"；Manager reset 后按新名册正常派单；后端 131 项、前端 20 项全绿。

## 1.20 关系理顺 + 统一执行链路 + 工作流角色化（2026-08-20）

- **概念模型**：数字分身（twin）= 对话组织者（demo，不建容器，映射 AgentTeams Manager）；数字员工（virtual/rpa）= 执行者（agentteams，自动建容器）。`POST /api/v1/employees` 按 type 默认 runtime_type。
- **统一 AgentTeams 主链路**：群聊任务默认走 AgentTeams；内置 TeamTaskOrchestrator 仅降级，前端标注"内置降级（AgentTeams 不可用）"。
- **结构化任务消息**：平台发 Manager 的消息带 `[平台任务 id={task_id}] 请求者={姓名}({工号}) 请求={内容} 目标={员工若有}`；`parse_completion` 优先按 task_id 精确匹配回执（Manager 回执实测引用 task_id，不再自由发挥/串任务）。
- **工作流角色化 + 参数化**：`WORKFLOW_META` 增加 `owner_employee`（adp-onboarding→VE-0001；请假/报销/采购/报表→VE-0002；会议纪要→VE-0001），前端工作流卡片显示"由 XX 处理"；Mock adapter 与模板去掉硬编码"王小明"（默认"该员工"，请求中提取员工名注入）。
- **验证**：实测"帮我申请请假2天"→ 消息 `id=T-20260820-02D4DA 请求者=张三(E10281)` → Manager 围绕 task_id 派单给 HR 助理；后端 135 项、前端 20 项全绿。
- **修复（同日）**：`parse_completion` 曾把平台自身发送的任务消息（含 task_id）误当回执，导致任务汇总变成任务原文。已增加 `exclude_senders`（排除 platform-bot），回执只认 Manager/数字员工的消息；实测新任务汇总为 Manager 真实回执（受理+按角色派发），不再出现任务原文。后端 136 项全绿。

## 1.21 Sprint 11：任务交互异步化（消除卡死）

- **根因**：`POST /conversations/{id}/messages` 曾同步执行「分类 → AgentTeams 轮询（最长 90s）→ 内置编排（LLM 拆解/执行/汇总）」，整段堵在一个请求；前端 fetch 无超时且发送期间禁用输入，导致"点完卡住"。
- **改造**：
  1. 发送消息端点只落用户消息并立即返回，用 `BackgroundTasks` 后台执行 `process_conversation`（独立 DB session）；任务/回复写回后由前端轮询 `GET /conversations/{id}` 获取。
  2. 审批端点 `POST /tasks/{id}/approve` 受理即返回，后台续跑 `_run_loop`。
  3. AgentTeams 轮询窗口 90s→60s；任务去重窗口 10min→2min 且只拦 running/approval（completed 允许重发）。
  4. 前端 fetch 加 30s 超时（AbortController），发送后进入轮询（`pendingReply` 态），审批按钮保留。
- **测试**：后端 136 项全绿；前端 20 项全绿；实测发消息 1.32s 返回（原 90s）。

## 1.11 Sprint 5 完成情况（TeamTaskOrchestrator，负责人 A）

- **TeamTaskOrchestrator**：`backend/app/services/team_orchestrator.py`，TEAM-ONBOARD 模板 3 子任务（VE-0002 HR 制度 -> VE-0003 IT 账号 -> VE-0003 敏感报表审批）；子任务执行一律经 Plugin Gateway；状态机 parsing/running/approval/completed/denied/failed。
- **接口**：`POST /api/v1/teams/{id}/tasks`、`GET /api/v1/teams/{id}/tasks/{id}`、`POST /api/v1/tasks/{id}/approve`（非挂起态审批 409）。
- **汇总**：LLM（DeepSeek）生成 Leader 汇总，失败自动降级模板拼接。
- **端到端验证**（真实链路）：发起"帮王小明完成入职准备" -> 3 子任务（2 completed + 1 approval）-> 审批通过 -> completed + 真实汇总文本；审计 6 条按 trace 贯穿（create/execute×3/approve/summarize）。
- **测试**：`backend/tests/test_team.py` 7 项，后端共 58 项全绿。
- **Harness（G2，Docker 方案）**：`docker/Dockerfile.dsh` 构建 `dwp-dsh:rc6` 镜像；`DockerHarnessRuntimeAdapter` 用 `docker run --env-file <临时文件> dwp-dsh:rc6 --profile headless <task>` 真实执行（交互环境 4-6s 验证通过，VE-0002 子任务已出现 `[Harness 执行]` 真实结果）。Windows 无控制台进程（uvicorn Hidden 启动）内 docker CLI/dsh 调用慢且偶发超时，故服务内默认 `DWP_HARNESS_ENABLED=0` 走 demo 模式（0.7s）；演示真实 Harness 可：a) 交互终端跑 `dsh --profile headless "<任务>"`；b) `docker run --rm --env-file tmp/dsh-docker.env dwp-dsh:rc6 --profile headless "<任务>"`。
- **AgentTeams**：保持 Adapter 桩（需 K8s/Docker + Matrix 形态，本周不接入，Demo 口播"已预留"）。
- **运维注意**：本机 venv python 启动 uvicorn 时会派生一个 Anaconda 解释器进程（conda launcher 行为），以实际加载的 venv site-packages 为准；`database.py` 已设 `expire_on_commit=False`（避免 Team 编排 JSON 字段在 commit 后过期报错）。

## 1.9 Sprint 4 完成情况（Digital Employee Chat + DeepSeek Provider，负责人 A）

- **LLM Provider**：`backend/app/services/llm.py` 统一 `chat() / tool_call() / structured_output()`；`DeepSeekProvider`（OpenAI 兼容，httpx）；业务代码不直连 DeepSeek；Key 仅环境变量（本地 gitignored `.env`）。
- **SAFEMODE**：所有发送消息必须带 `source=demo`，非 demo 段拒发（`LLMUnavailableError`）。
- **Session Manager**：`backend/app/services/session.py` 保存 employee_id / session_id / message history（chat_session / chat_message 表）。
- **Chat Orchestrator**：`backend/app/services/chat.py` 严格链路 User → Employee → LLM → Tool Intent → Policy → Gateway → Knowledge Adapter → Result → LLM → Answer；≤3 轮工具；Deny 卡片（policy_id/reason）；工具调用一律经 Gateway。
- **Demo 场景**（真实 DeepSeek deepseek-chat 验证通过）：
  - 场景 A：DT-E10281 问"查询一下内部制度" → KB-INTERNAL Allow（POLICY-001）→ 正常回答 ✅
  - 场景 B：DT-E20999 同样问题 → DENY（POLICY-002）→ "当前身份无权访问该知识库" ✅
  - VE-0001：仅 KB-PUBLIC + KB-ONBOARD（新增入职 Demo 知识库），问入职流程正常回答；问内部制度被拒 ✅
- **接口**：`POST /api/v1/employees/{employee_no}/chat`（整段 JSON）、`GET /api/v1/chat/sessions/{session_id}/messages`。
- **测试**：新增 `backend/tests/test_chat.py`（FakeLLM，7 项），后端共 51 项全绿。

> ⚠️ 模型名说明：官方 DeepSeek API 无 `v4-flash` 模型名（合法为 deepseek-chat / deepseek-reasoner）。代码默认值保留 v4-flash，本地 `backend/.env` 覆盖为 `DEEPSEEK_MODEL=deepseek-chat`；若内部网关支持 v4-flash，把 `DEEPSEEK_BASE_URL` 指向该网关即可。

## 1.7 Sprint 3 完成情况（Enterprise Resource & Security Layer，负责人 B）

- **Knowledge Adapter**：`backend/app/services/knowledge_adapter.py` 统一接口 `search(employee_id, knowledge_base_id, query, trace_id)`；`MockKnowledgeAdapter`（读 mock-data/kb/ 虚构文档）+ `InternalKnowledgeAdapterStub`（仅接口与配置结构，不接真实内容）。
- **Knowledge Resource Registry**：knowledge_base 表扩展 resource_type / data_level / allowed_employment_type / department_scope；登记 KB-PUBLIC（L1 公共）、KB-INTERNAL（L2 正式内部）、KB-FINTECH（L2 金融科技部门）。
- **安全资源边界**：`POST /internal/knowledge/search` 统一经 Policy → Gateway → Adapter；正式分身 KB-INTERNAL Allow（POLICY-001）、实习生 DENY（POLICY-002）、虚拟员工仅按单独授权。
- **Sandbox Policy**：`backend/app/services/sandbox_policy.py`（runtime_location / internet_access / filesystem_scope）+ MockExecutor + `POST /internal/sandbox/run`（先 Policy 后执行；remote_only 拒绝 local → POLICY-004；internet deny 拒绝非 none 网络 → POLICY-003）。
- **Secret/Config**：`backend/app/services/config.py` 环境变量引用（DWP_*）；禁止入 Git/Prompt/日志。
- **Audit**：AuditEvent 增加 `knowledge_base_id`，知识库访问完整记录 employee_id / knowledge_base_id / decision / trace_id。
- **测试**：新增 `backend/tests/test_enterprise_resources.py`（16 项），后端共 44 项全绿；前端 typecheck 通过。

## 1.5 Sprint 2 完成情况（Core Control Plane）

- **Employee Identity**：`backend/app/services/identity.py` 按数据库解析 identity（employee_id / employee_type / employment_type / department / owner_id + 环境绑定配置）；身份不可伪造（以 DB 为准，防越权声明）。
- **Policy Engine**：`backend/app/services/policy.py` 四维评估（subject/resource/action/environment），内置规则 POLICY-001~005 + P-DATA-003 / P-PLUGIN-007 / P-DEFAULT-001；Deny > Approval > Allow；未授权默认拒绝。
- **Plugin Gateway**：`backend/app/services/gateway.py` + `routers/internal.py` 实现 `/internal/policy/evaluate`、`/internal/gateway/invoke`；调用链 Identity → Policy → Gateway → Adapter → Result + Audit。
- **Mock Adapter**：`backend/app/services/adapters.py` 六个虚构 Adapter（knowledge-l1/l2、hr-employee-mcp、adp-onboarding、internet-search、rpa-report），无任何真实系统。
- **Audit**：Gateway 每次调用落一条审计（allow/deny/approval 全覆盖），字段齐全（trace_id / employee_id / plugin_id / action / decision / reason / timestamp / result_summary）。
- **测试**：新增 `backend/tests/test_control_plane.py`（18 项），后端共 28 项测试全绿；前端 typecheck / vitest / build 全绿。

## 1. Sprint 1 已完成什么

### 工程骨架
- 前端：React 18 + TypeScript + Vite + Ant Design 5，路由与页面（首页、员工列表、员工详情、插件中心、安全中心、Team 占位）。
- 后端：FastAPI + SQLAlchemy + SQLite，`/health` + 员工/插件/策略/审计 CRUD + 团队/知识库只读接口，统一错误形状。
- 共享契约：`shared-schema/types.ts` 与后端 Pydantic Schema 手动同步。
- Mock 数据：正式员工 2、实习生 2、数字分身 2、虚拟员工 3、插件 5、策略 6、审计样例 3、团队 1（Leader + 3 成员）、虚构知识库 4。
- 仓库卫生：git 已初始化（2 次提交）；敏感 token 文件已移出工作区；`.gitignore` 覆盖外部依赖/密钥/DB/日志。

### 已冻结的接口（实现现状见 `docs/API_CONTRACT.md`）
- Employee API（列表/详情/CRUD ✅）
- Policy API（列表/详情/CRUD ✅；evaluate 📋）
- Plugin API（列表/详情/CRUD ✅；gateway invoke 📋）
- Audit API（列表/详情/写入/删除 ✅；Trace 时间线 📋）
- Chat API（📋）
- Runtime Adapter Interface（📋）
- Knowledge Adapter Interface（📋）

### 已冻结的架构约束
- 统一资源访问链：`Employee Identity → Policy Engine → Plugin Gateway → Adapter → Enterprise Resource`。
- 业务模块禁止直连知识库/数据库/Workflow/RPA。
- 权限只存在于 Policy Engine；Sandbox 只隔离不授权。

## 2. 当前如何启动

### 后端（venv，端口 8000）

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.seed --reset      # 重建 SQLite + 灌入虚构种子
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

健康检查：<http://127.0.0.1:8000/health>；OpenAPI：<http://127.0.0.1:8000/openapi.json>

### 前端（Vite dev，端口 5173）

```powershell
# 仓库根目录
pnpm install
pnpm --filter frontend dev
```

打开 <http://localhost:5173>（`/api` 代理到后端 8000）。

### 一键初始化

```powershell
.\scripts\init_demo.ps1    # venv + 依赖 + 重建种子
```

> 注意：同一台机器只保留一个后端进程占用 8000（曾出现多个 uvicorn 实例并存，已清理；重启前先确认端口空闲）。

## 3. 当前测试情况

| 项 | 命令 | 结果 |
|---|---|---|
| 后端 API 测试（58 用例：骨架 10 + 控制链路 18 + 企业资源 16 + Chat 7 + Team 7） | `cd backend; .\.venv\Scripts\python.exe -m pytest tests -q` | ✅ 58 passed |
| 前端类型检查 | `pnpm --filter frontend typecheck` | ✅ |
| 前端冒烟测试 | `pnpm --filter frontend test` | ✅ 1 passed |
| 前端生产构建 | `pnpm --filter frontend build` | ✅（chunk 体积提示非阻塞） |

测试布局说明见 `tests/README.md`。后端测试使用内存 SQLite + 种子，不触碰 `backend/dwp.db`。

## 4. Mock 数据位置

| 内容 | 位置 |
|---|---|
| 种子数据（员工/插件 6/策略 9/授权 13/审计/团队/知识库资源 3） | `mock-data/seed.json` |
| 虚构知识库文档（L1 ×1、L2 ×3） | `mock-data/kb/` |
| 重建命令 | `cd backend; .\.venv\Scripts\python.exe -m app.seed --reset` |

所有数据均为虚构，知识文档头部含「虚构」声明。**禁止**用真实内容替换。

## 5. 下一阶段负责人

| 方向 | 负责人 | 说明 |
|---|---|---|
| TeamTaskOrchestrator（P0-lite 编排） | A（正式/架构总装） | Sprint 5 主线（Chat/Policy/Gateway 均就绪） |
| Sandbox Docker 真启动 / Harness | B / A-B 串行 | Sprint 5，门禁 G2/G3（Sandbox Mock 已就绪） |
| 前端聊天页 | C（实习生） | 按 Chat API 契约实现（后端已就绪） |
| 前端聊天页 / 安全页增强 | C（实习生） | 等契约冻结后按 `API_CONTRACT.md` 实现 |
| Mock Adapter 内容 + 自动化测试扩展 | D（实习生） | 按契约补 Mock 数据与用例 |

串行建议：A 完成 Chat Orchestrator（T1-06，调已就绪的 Policy/Gateway）→ TeamTaskOrchestrator（T2-01）→ Sandbox/Harness（T2-02/03）。

## 6. 下一阶段允许修改哪些目录

✅ 可修改（按契约）：`backend/app/`、`backend/tests/`、`adapters/`、`frontend/src/`、`mock-data/`、`docs/`、`scripts/`

⚠️ 需 A 批准：`shared-schema/types.ts`、`docs/API_CONTRACT.md`、`docs/ARCHITECTURE.md`（契约/架构变更，三处同步）

🚫 禁止：`higress/`、`deepseek-harness/`（外部依赖，本机引用不入库）；Secure Overlay（仓库外，仅 A/B）；任何真实数据/Token/端点写入仓库。

## 7. 已冻结、原则上不能修改的接口

冻结清单（修改需 A 批准 + 变更登记）：

1. 通用约定：Base Path `/api/v1`、错误形状、错误码枚举、`X-Demo-Actor`、`trace_id`。
2. Employee API（路径与 DTO 字段：平铺结构）✅ 已实现 CRUD。
3. Policy API（DTO：`effect/enabled/priority`）✅ CRUD + evaluate 已实现。
4. Plugin API（DTO：`endpoint_ref/data_level/status`）✅ CRUD + gateway invoke 已实现。
5. Audit API（DTO 字段与过滤参数）✅ 已实现；Trace 时间线 📋。
6. Chat API（SSE 事件类型枚举）📋。
7. Runtime Adapter Interface（`run(subject, task, context) -> RuntimeResult`）📋。
8. Knowledge Adapter Interface（search 请求/响应结构）✅ Mock + Stub 已实现。
9. 统一资源访问链（Identity → Policy → Gateway → Adapter → Resource）不可绕过。

## 8.5 正式员工 A 下一阶段可直接使用的接口（Sprint 3 交付）

### Knowledge Adapter Interface

```python
# backend/app/services/knowledge_adapter.py
search(employee_id: str, knowledge_base_id: str, query: str, trace_id: str) -> dict
# → {"source": "demo|stub", "knowledge_base_id": "...", "hits": [{"title", "snippet"}], ...}
```

HTTP：`POST /internal/knowledge/search` `{employee_id, knowledge_base_id, query, trace_id}` → `{ok, data, decision, audit_ids[], policy_id}`；DENY → 403 `POLICY_DENIED`（detail.policy_id/reason/audit_id）。

### Knowledge Resource Registry

```python
# backend/app/services/knowledge_registry.py
list_resources(db) -> list[KnowledgeBase]      # GET /api/v1/knowledge-bases
resolve(db, knowledge_base_id) -> KnowledgeBase | None
plugin_id_for_level(data_level) -> str          # L1→knowledge-l1，L2→knowledge-l2
```

资源字段：`id / name / resource_type / data_level / allowed_employment_type / department_scope / status`。

### Sandbox Policy Interface

```python
# backend/app/services/sandbox_policy.py
from_identity(identity) -> SandboxPolicy
# SandboxPolicy: runtime_location("remote_only"|"local") / internet_access("deny"|"allow") / filesystem_scope
MockExecutor().execute(policy, *, command, mount_dir, network, execution_location) -> {"mode", "status", "logs"}
```

HTTP：`POST /internal/sandbox/run` `{employee_id, task_id, command, mount_dir, network, execution_location}` → `{mode, status, logs}`；被拒（POLICY-003/004）→ 403。
9. 统一资源访问链（Identity → Policy → Gateway → Adapter → Resource）不可绕过。

## 8. 已知决策记录（Sprint 1.5 冻结）

- DigitalTwin / VirtualEmployee 用 `digital_employee.type` 区分，不建独立表。
- RuntimeBinding 不建独立表：`runtime_type` / `runtime_ref` 内嵌于 `digital_employee`；Sandbox 配置（`location` / `internet` / `max_data_level` / `allowed_domains`）同样内嵌。P0-lite 场景足够；需多 Runtime 时再建模。
- EmployeeDto 采用平铺结构（v1.0 嵌套草案作废）。
- Sprint 3（B）：知识库资源模型扩展 knowledge_base 表（resource_type/data_level/allowed_employment_type/department_scope）；审计增加 knowledge_base_id；均为兼容扩展，已登记 API_CONTRACT 变更。
- 测试就近存放（`backend/tests`、`frontend/src`），不迁移到顶层 `tests/`（避免破坏 pytest/vitest 配置）；顶层 `tests/README.md` 作为索引。
- pnpm 11 构建脚本需在 `pnpm-workspace.yaml` 配 `allowBuilds.esbuild: true`。

## 9. 常见命令速查

```powershell
# 后端测试
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
# 前端
pnpm --filter frontend typecheck
pnpm --filter frontend test
pnpm --filter frontend build
# 重置种子
cd backend; .\.venv\Scripts\python.exe -m app.seed --reset
```
