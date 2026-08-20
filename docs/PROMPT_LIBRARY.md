# 数字员工平台 Codex Prompt 库

> 版本：v1.0（2026-08-18）
> 用途：面向 Codex 的、按当前仓库实际状态生成的开发 Prompt 库。每个 Prompt 可直接复制到 Codex（桌面端新对话或 `codex exec`）执行。
> 依据：`PLANS.md`（任务清单）、`docs/API_CONTRACT.md`（冻结契约）、`docs/ARCHITECTURE.md`（五层架构）、`docs/SECURITY_BOUNDARY.md`（数据与权限边界）、`docs/DEVELOPMENT_HANDOFF.md`（交接基线）、`docs/DEMO_SCENARIO.md`（演示脚本）。

## 0. 使用说明与通用上下文

### 0.1 怎么用

1. 从下文选择一个任务 Prompt，复制整块粘贴给 Codex（工作目录为该仓库根目录）。
2. 每个 Prompt 都自带「先读哪些文件」和「验证命令」，Codex 会自动读取文件后开工。
3. 完成后跑 Prompt 内的验证命令，全绿后手动在 `PLANS.md` 勾选对应任务。
4. 一次只执行一个任务，避免跨任务上下文污染；契约类变更（见 P11）必须单独走审批流程。

### 0.2 通用上下文块（每个 Prompt 已内嵌，可自行调整角色）

```text
你是数字员工平台 PoC 仓库的资深工程师（负责人角色：{ROLE}）。开始前先通读以下文件：
- PLANS.md：任务清单、门禁（G1/G2/G3）、Acceptance Criteria
- docs/ARCHITECTURE.md：五层架构、统一资源访问链、不变量
- docs/API_CONTRACT.md：冻结契约（v1.1），实现以契约为准
- docs/SECURITY_BOUNDARY.md：数据与权限边界铁律
- docs/DEVELOPMENT_HANDOFF.md：交接基线、启动/测试命令、允许修改目录

硬性约束（违反即返工）：
1. 仓库只允许虚构/Mock 数据；禁止引入真实 Token、真实知识库内容、真实 Endpoint、内部日志/截图。
2. 业务模块不得直连知识库/DB/Workflow/RPA；所有资源调用必须走
   Employee Identity → Policy Engine → Plugin Gateway → Adapter → Resource。
3. 进入 LLM 的每个 prompt 段必须带 source=demo（SAFEMODE 强制校验），否则拒发。
4. 冻结契约（docs/API_CONTRACT.md）不得擅自修改；如需变更必须显式说明并走变更登记（三处同步：OpenAPI / shared-schema/types.ts / API_CONTRACT.md）。
5. 权限判断只允许存在于 Policy Engine；前端、Runtime、插件内部不得做权限判断。
6. 测试就近存放（backend/tests、frontend/src），禁止把测试挪到顶层 tests/。
7. 可修改目录：backend/app/、backend/tests/、adapters/、frontend/src/、mock-data/、docs/、scripts/；shared-schema 与契约文档需 A 批准。
8. 环境：Windows + PowerShell；后端 venv 在 backend/.venv；前端为 pnpm workspace。
9. 后端每个插件调用必须产生一条 audit_event（allow/deny/approval）。
```

### 0.3 Prompt 生成模板（新任务时套用）

```text
【任务】{任务 ID：任务名}（Owner：{角色}，优先级：{P0/P1/P2}）
【背景】{当前实现状态 + 相关契约/表/服务引用}
【先读】{文件清单}
【目标】{一句话目标}
【交付物】{输出文件与接口清单}
【约束】{来自通用上下文 + 本条特有约束}
【验收标准】{可验证的 Acceptance Criteria}
【验证命令】{具体命令}
```

---

## P1. T2-01 TeamTaskOrchestrator（P0，Owner A）

```text
你是数字员工平台 PoC 仓库的资深后端工程师（负责人角色：A 正式员工/架构总装）。开始前先通读：
- PLANS.md 中 T2-01 条目与门禁说明
- docs/API_CONTRACT.md §3.6（Team API）与 §7（TaskRunDto）
- docs/ARCHITECTURE.md（L2 协作编排层与不变量）
- docs/SECURITY_BOUNDARY.md（Agent Team 权限规则：成员权限独立、Leader 不自动继承）
- docs/DEMO_SCENARIO.md 步骤 6-10（黄金链路的 Team 部分）

背景与现状：
- task_run 表已建（backend/app/models.py：id/team_id/trace_id/request/status/subtasks/summary/created_at），尚无编排逻辑。
- Team API 目前只有只读列表/详情（backend/app/routers/teams.py）。
- 可复用：backend/app/services/gateway.py 的 invoke_plugin（Policy→Gateway→Adapter→Audit）、
  backend/app/services/policy.py、backend/app/services/adapters.py 的 Mock Adapter
  （hr-employee-mcp / adp-onboarding / rpa-report / knowledge-l1 / knowledge-l2）、
  backend/app/services/llm.py（chat/tool_call/structured_output，SAFEMODE）。
- TaskRunDto 契约（已冻结）：status = parsing|running|approval|completed|denied|failed；
  subtasks 元素含 worker_id/worker_no/summary/plugin_ids/status/result/approval。

目标：实现 P0-lite 团队任务编排：发起任务 → LLM/模板拆 3 个子任务 → Worker 执行（走 Gateway）
→ 触发审批挂起 → 人工批准续跑 → Leader 汇总 → completed；失败/拒绝路径可回退。

交付物：
1. backend/app/services/team_orchestrator.py：TaskRun 状态机（parsing→running→approval→completed/denied/failed）+ 子任务执行 + Leader 汇总（LLM structured_output 或模板拼接）。
2. 路由扩展：
   - POST /teams/{team_id}/tasks：{request} → 201 {task_id, trace_id}（生成 trace_id，贯穿审计）
   - GET /teams/{team_id}/tasks/{task_id}：返回 TaskRunDto（轮询用）
   - POST /tasks/{task_id}/approve：{approve: bool, actor_no}；非 approval 态 → 409 STATE_CONFLICT
3. backend/app/schemas.py 增加对应请求/响应模型（不得改变已冻结字段名）。
4. backend/tests/test_team_orchestrator.py：发起→3 子任务→approval 挂起→批准→completed；
   denied 路径；非 approval 态审批 409；每个 Worker 调用落审计且可按 trace_id 聚合。

约束：
- 子任务调插件必须经 gateway.invoke_plugin，禁止绕过。
- 审批动作本身也要落审计（decision=approval）。
- 所有进入 LLM 的文本 source=demo。
- 默认拒绝：未授权 Worker/插件返回 deny，任务进入 denied 或失败态。

验收标准：
- 三条链路的 pytest 通过：正常审批续跑、拒绝、非法审批 409。
- GET 任务详情返回的 subtasks 与契约字段一致。
- 审计中心可按 trace_id 查到 发起→插件调用→审批→汇总 完整时间线。
- 后端全量 pytest（现有 51 项 + 新增）全绿。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
```

---

## P2. T1-07 前端聊天页（P0，Owner C）

```text
你是数字员工平台 PoC 仓库的前端工程师（负责人角色：C 实习生/前端）。开始前先通读：
- docs/API_CONTRACT.md §3.1（Employee API）与 §3.5（Chat API、SSE 事件枚举）
- docs/DEVELOPMENT_HANDOFF.md（启动方式、前端约束）
- docs/SECURITY_BOUNDARY.md（前端只展示后端决策，不做权限判断）
- frontend/src/api/client.ts 与 shared-schema/types.ts（现有类型与 client 风格）

背景与现状：
- 后端聊天已就绪：POST /api/v1/employees/{employee_no}/chat 返回整段 JSON
  {session_id, trace_id, message, tool_cards[], policy_denied?}；
  GET /api/v1/chat/sessions/{session_id}/messages 返回会话历史。
- 前端已有员工列表/详情页（frontend/src/pages/Employees.tsx、EmployeeDetail.tsx），
  路由在 frontend/src/App.tsx，但还没有聊天界面。
- tool_cards 元素：{plugin_id, name, decision: allow|deny|approval, policy_id?, reason?}。

目标：在员工详情页加入可用聊天面板（或独立路由 /employees/:employeeNo/chat），
完成「输入→回答→工具卡片→Deny 卡片」闭环。

交付物：
1. frontend/src/api/client.ts 增加 chat 与会话历史方法（沿用现有 request<T> 封装）。
2. 聊天 UI（Ant Design）：消息列表、输入框、发送按钮、加载/错误态；
   回答区渲染 tool_cards 为工具卡片；policy_denied 渲染为醒目的 Deny 卡片
   （展示 policy_id 与 reason，样式参考 AntD Alert 或 Tag）。
3. 会话历史：进入页面时加载已有消息；消息带身份标识（当前数字员工）。
4. frontend/src 下新增组件测试（vitest）：给定 mock 响应渲染工具卡片与 Deny 卡片。

约束：
- 前端只渲染后端返回的 decision，禁止自行判断/修改权限语义。
- 请求头按契约携带 X-Demo-Actor。
- 样式沿用现有 AntD 主题，typecheck / vitest / build 全绿。

验收标准：
- 场景 A：以 DT-E10281 问「查询一下内部制度」→ 正常回答 + 工具卡片（allow）。
- 场景 B：以 DT-E20999 问同一问题 → Deny 卡片（policy_id=POLICY-002，reason 可见）。
- 会话历史刷新后仍可加载。
- pnpm --filter frontend typecheck / test / build 全通过。

验证命令：
pnpm --filter frontend typecheck
pnpm --filter frontend test
pnpm --filter frontend build
```

---

## P3. T2-04 前端 Team 任务页（P1，Owner C）

```text
你是数字员工平台 PoC 仓库的前端工程师（负责人角色：C 实习生/前端）。开始前先通读：
- docs/API_CONTRACT.md §3.6（Team API：发起任务/任务详情/审批）
- docs/ARCHITECTURE.md（L2 协作编排层）
- docs/DEMO_SCENARIO.md 步骤 6-10（团队任务的演示预期）
- frontend/src/pages/Teams.tsx、frontend/src/api/client.ts

背景：Team API 只读部分已实现；T2-01 后端（发起任务/轮询/审批）为依赖项。
若 T2-01 未完成，先按契约用 Mock 数据开发，接口字段不得臆造。

目标：实现 Team 任务页：发起自然语言任务 → 展示任务状态/子任务进度 → 审批按钮 → Leader 汇总。

交付物：
1. frontend/src/api/client.ts 增加 createTeamTask / getTeamTask / approveTask 方法。
2. Team 详情或任务视图：任务发起表单；任务状态（parsing/running/approval/completed/denied/failed）徽章；
   子任务列表（worker_no/summary/status/result）；approval 态展示审批按钮
   （POST /tasks/{task_id}/approve，approve: true/false）；Leader 汇总文本展示。
3. 轮询任务详情（如 2-3 秒一次），组件卸载时清理定时器。
4. 组件测试：审批点击后调用 approve 接口并刷新状态。

约束：与 P2 相同（只展示后端状态、AntD 风格、typecheck/test/build 全绿）。

验收标准：
- 与 T2-01 联调：发起任务后能看到 3 个子任务与状态流转；审批按钮点击后状态更新。
- 无 T2-01 时 Mock 数据可正常展示全部状态。

验证命令：
pnpm --filter frontend typecheck
pnpm --filter frontend test
pnpm --filter frontend build
```

---

## P4. T2-03 Sandbox Docker 真启动（P1，Owner B）

```text
你是数字员工平台 PoC 仓库的安全/后端工程师（负责人角色：B 正式员工/安全与企业资源）。
注意：负责人 B 对 Sandbox 概念较新，交付必须自解释、可逐步验收；不要引入超出本任务的设计。
开始前先通读：
- docs/SECURITY_BOUNDARY.md §7（Sandbox 边界：先 Policy 后执行；Docker 不可用自动 local 降级；Sandbox 不是权限来源）
- docs/API_CONTRACT.md §6.3（/internal/sandbox/run 请求响应：{employee_id, task_id, command, mount_dir, network, execution_location} → {mode, status, logs}）
- docs/ARCHITECTURE.md（L5 隔离与资源层；统一资源访问链）
- backend/app/services/sandbox_policy.py、backend/app/routers/internal.py、backend/app/services/gateway.py（write_audit 可复用）

背景与现状：
- SandboxPolicy（runtime_location / internet_access / filesystem_scope）+ MockExecutor（仅打印日志，返回 mode=local）
  + POST /internal/sandbox/run 已实现；
- 路由层已做 POLICY-004（remote_only 请求 local → 403）与 POLICY-003（internet=deny 请求非 none 网络 → 403）检查，
  这两段授权逻辑不要动；
- 本机：Docker CLI 已装（28.3.2）但 daemon 未运行（docker info 连不上引擎）——本机验收默认走 local 降级路径；
- 需求：把 MockExecutor 替换为"真容器优先、local 兜底"的 SandboxManager，接口与契约不变。

目标：实现 SandboxManager：Docker 可用 → 真容器执行；不可用/失败 → local 降级；两种情况都写审计；
被拒请求不启动任何容器。

交付物：
1. backend/app/services/sandbox_manager.py：
   - docker_available()：探测 daemon（subprocess 调 `docker info` 或 `docker version --format ...`，超时 3 秒，
     任何异常返回 False）；函数独立、可被测试 monkeypatch；
   - execute()：组装并执行 docker run，参数固定为：
     docker run --rm --network none -v <宿主工作区>:/workspace/{employee_id} python:3.11-slim <command>
     - 宿主工作区默认 backend/sandbox-workspaces/{employee_id}（自动创建；.gitignore 增加 backend/sandbox-workspaces/）；
     - 执行超时（30 秒）：超时后 docker kill + docker rm，返回超时状态并写审计，不挂起请求；
     - daemon 探测失败 / docker run 失败 / 镜像缺失 → 自动降级 local（复用 MockExecutor 行为），mode=local；
   - 不产出授权决策；写入审计时命令与结果只放摘要（前 200 字符），不记录真实凭据。
2. backend/app/routers/internal.py：sandbox_run 的"执行"部分改用 SandboxManager；
   Policy 检查、审计写入、返回结构 {mode, status, logs} 保持现状。
3. backend/tests/test_sandbox.py（mock 检测器，不依赖本机 daemon）：
   - docker 可用（monkeypatch docker_available=True 并 mock subprocess）→ mode=docker；
   - docker 不可用（monkeypatch False）→ mode=local，且审计 result_summary 含 mode=local；
   - 拒绝路径：remote_only+local → 403 POLICY-004；internet=deny+非 none → 403 POLICY-003，
     并断言 docker 启动函数未被调用；
   - 超时路径：subprocess 超时 → 返回失败/降级且不挂起。

约束：
- Sandbox 只做隔离，不做授权；Policy 检查顺序与逻辑一律不动。
- 不得修改 /internal/sandbox/run 的请求响应契约与错误码。
- 凭据/Key 只经 config.py 环境变量引用，禁止写入镜像、命令或日志。
- 不能因为 Docker 不可用让请求返回 5xx（演示可降级）。
- 不要做生产级能力（K8s、seccomp、资源配额、镜像签名），PoC 只到单容器隔离 + 降级 + 审计。

验收标准（B 可逐步自查）：
1. cd backend; .\.venv\Scripts\python.exe -m pytest tests -q → 全绿（现有 63 + 新增沙箱用例）。
2. 后端启动后（Docker daemon 停着）调用允许场景 → mode=local、审计存在；调用拒绝场景 → 403。
3. 可选：启动 Docker Desktop 后重测允许场景 → mode=docker（daemon 起来了才验这条）。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
# 手动冒烟（后端 8000 跑着时）：
# 允许：POST /internal/sandbox/run {"employee_id":"VE-0001","task_id":"T1","command":"echo hi","mount_dir":"","network":"none","execution_location":"remote"}
# 拒绝1：execution_location 改 local → 期望 403 POLICY-004
# 拒绝2：network 改 bridge → 期望 403 POLICY-003
```

---

## P5. T2-02 Harness 集成尝试（P1，Owner A/B）

```text
你是数字员工平台 PoC 仓库的集成工程师（负责人角色：A/B 正式员工）。开始前先通读：
- docs/API_CONTRACT.md §4（Runtime Adapter Interface：run(subject, task, context) -> RuntimeResult，
  mode = harness|demo|openclaw-stub|agentteams-stub）
- docs/ARCHITECTURE.md（L3 Runtime 执行层）
- docs/SECURITY_BOUNDARY.md（Harness 同规则：输入必须 source=demo）
- docs/DEVELOPMENT_HANDOFF.md（deepseek-harness 为本机外部依赖，gitignore，不入库）

背景：Runtime Adapter 接口已冻结但未实现；门禁 G2：能真接 Harness 则接，接不通用
Adapter 演示模式（mode=demo）且 UI 标注「Adapter 演示模式」，不阻塞主链路。

目标：实现 RuntimeAdapter（demo backend 必做，harness backend 尽力），供 TeamTaskOrchestrator 与 VE-0001 调用。

交付物：
1. backend/app/services/runtime_adapter.py：统一 run() 接口；demo 模式返回固定/模板结果；
   harness 模式探测 dsh（pnpm dsh --profile headless）可用性，可用则调用并翻译 events。
2. POST /internal/runtime/run 路由（契约请求/响应）。
3. 探测与失败处理：dsh 不存在/超时 → 返回 RUNTIME_UNAVAILABLE（503）或自动降级 demo
   （降级需在响应 events 中体现，UI 标注演示模式）。
4. G2 结论记录到 docs/（通过/降级，附命令与输出摘要）。

约束：
- Adapter 不持有权限逻辑；调用前 Policy 已评估（或由 RuntimeLauncher 调 Policy）。
- 禁止把真实凭据、真实任务内容写入仓库或 prompt。
- mode=demo 时 UI 必须能标注「Adapter 演示模式」。

验收标准：
- demo 模式可跑通黄金链路步骤 5（VE-0001 问答带 Harness 徽章）。
- G2 结论有文档记录；无任何真实凭据入库。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
```

---

## P6. T2-05 自动化测试扩展（P1，Owner D）

```text
你是数字员工平台 PoC 仓库的测试工程师（负责人角色：D 实习生/Mock 与测试）。开始前先通读：
- docs/API_CONTRACT.md（全部接口）
- PLANS.md T2-05 条目
- backend/tests/ 现有用例（test_api / test_control_plane / test_enterprise_resources / test_chat）
- tests/README.md（测试布局约定）

背景：后端现有 51 项全绿；T2-01（Team 编排）、T2-03（Sandbox 降级）为新增覆盖目标。

目标：补齐自动化测试，覆盖团队编排、审批、Sandbox 降级、越权直呼拒绝。

交付物（backend/tests/ 内新增，就近存放）：
1. Team 编排用例：发起→拆 3 子任务→approval 挂起→批准续跑→completed；拒绝路径；非法审批 409。
2. Sandbox 降级用例：Docker 不可用 → local 模式 + 审计；POLICY-003/004 拒绝且不启动。
3. 越权直呼用例：未授权插件直接调用 /internal/gateway/invoke → 403 POLICY_DENIED 且审计存在；
   绕过 Gateway 直读 mock-data/kb/ 的行为应被测试标记为违规（架构断言）。
4. 前端 vitest 补充：聊天工具卡片与 Deny 卡片渲染（依赖 T1-07 交付）。

约束：
- 测试只使用内存 SQLite + 种子数据，不触碰 backend/dwp.db。
- 用例名称与断言必须可读：给出「场景 → 期望」注释。

验收标准：
- 后端 pytest 全绿（新增用例 ≥15 项）。
- 前端 typecheck / test / build 全绿。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
pnpm --filter frontend typecheck
pnpm --filter frontend test
pnpm --filter frontend build
```

---

## P7. T3-01 一键启动/重置脚本（P1，Owner D）

```text
你是数字员工平台 PoC 仓库的脚本/基础设施工程师（负责人角色：D 实习生）。开始前先通读：
- README.md「快速开始」与 scripts/init_demo.ps1（现有实现）
- docs/DEVELOPMENT_HANDOFF.md「当前如何启动」
- PLANS.md T3-01 条目

背景：init_demo.ps1 已覆盖 venv+依赖+种子；缺「起前后端」与 30 秒一键恢复。

目标：新增 scripts/reset_demo.ps1（重建 DB+种子+起前后端+可选 docker）与 scripts/run_demo.ps1。

交付物：
1. scripts/reset_demo.ps1：停止旧进程（谨慎：只按端口/进程名清理本项目服务，提示确认）→
   重建 DB+种子 → 启动后端（backend/.venv，端口 8000）→ 启动前端（pnpm --filter frontend dev，端口 5173）
   → 健康检查（/health 与前端 HTTP 200）→ 输出访问地址。
2. scripts/run_demo.ps1：只负责启动（不重置数据）。
3. 端口占用检查与明确报错；PowerShell 执行策略说明写进注释。
4. README.md 增加一键命令段落。

约束：
- 脚本不得包含任何真实凭据；Key 仍从 backend/.env 读取。
- 不得使用破坏性命令清理无关进程或用户目录。

验收标准：
- 干净机器/新克隆仓库：执行 reset_demo.ps1 后 30 秒内前后端可访问。
- 二次执行不报端口冲突（有检测与提示）。

验证命令：
.\scripts\reset_demo.ps1
Invoke-WebRequest http://127.0.0.1:8000/health
```

---

## P8. T3-02 黄金链路端到端验证（P0，Owner A）

```text
你是数字员工平台 PoC 仓库的总集成人（负责人角色：A 正式员工/架构总装）。开始前先通读：
- docs/DEMO_SCENARIO.md（10 步黄金链路与 AC-01~08）
- PLANS.md T3-02 条目
- docs/DEVELOPMENT_HANDOFF.md（问答冒烟三场景命令）

背景：单员工问答已通（场景 A/B/VE），Team 编排（T2-01）、前端聊天页（T1-07）、Team 页（T2-04）
为依赖项。目标是串成一条演示链路并给出验收证据。

目标：把 10 步黄金链路端到端跑通，逐条对照 AC-01~08 收集证据。

交付物：
1. scripts/e2e_golden.ps1（或后端可执行冒烟脚本）：按 DEMO_SCENARIO 10 步依次调用后端接口，
   每步打印预期/实际结果；审批步骤可参数化（自动批准）。
2. docs/E2E_RESULTS.md：AC-01~08 对照表（通过标准 / 实际输出摘要 / 截图或命令输出引用）。
3. 阻塞项清单与责任人（发现即记录）。

约束：
- 全部使用虚构数据；LLM 段 source=demo。
- 降级预案（L0/L1/L2）逐条注明当前生效级别。

验收标准：
- 10 步脚本一次连跑通过；每条 AC 有对应证据。
- 前后端无阻断性报错；审计中心可见完整 trace。

验证命令：
.\scripts\e2e_golden.ps1
```

---

## P9. T3-05 安全门禁复核（P0，Owner B）

```text
你是数字员工平台 PoC 仓库的安全负责人（负责人角色：B 正式员工/安全与企业资源）。开始前先通读：
- docs/SECURITY_BOUNDARY.md（全部，尤其 §8 仓库卫生与 §9 安全门禁）
- docs/DEVELOPMENT_HANDOFF.md（Key 存放规则）
- PLANS.md T3-05 条目

目标：执行安全门禁复核，输出可提交的检查报告，确保仓库无真实数据/凭据/端点。

交付物：
1. 仓库扫描：用 rg 扫描疑似真实 Token/Key/内网 IP/域名/Base64 凭据模式；
   检查 .gitignore 覆盖（.env、*.db、*.log、secure 路径、外部依赖目录）。
2. SAFEMODE 验证：构造 source != demo 的 prompt 段被 LLMProvider 拒发的单测（如已有，复核并引用）。
3. 越权直呼验证：未授权插件经 /internal/gateway/invoke 调用 → 403 + 审计（如已有，复核并引用）。
4. docs/SECURITY_AUDIT.md：门禁清单逐项 通过/不通过 + 证据（命令输出摘要/文件行号）。
5. 若根目录两份 docx 属于内部材料，在报告中标注「建议撤下/移入 secure overlay」并确认是否已处理。

约束：
- 扫描过程不得把疑似敏感内容全文写入报告；只引用文件路径与模式类型。
- 复核不得修改代码，除非发现需要修复的安全问题（列清单交 A 审批）。

验收标准：
- 门禁清单全部通过或有明确修复项；无真实 Token/数据/Endpoint 在仓库。

验证命令：
rg -n -i "sk-[a-z0-9]{20,}|bearer |api[_-]?key|password\s*=|192\.168\.|10\.[0-9]+\.|\.corp\.|\.internal\." --glob "!pnpm-lock.yaml" --glob "!*.md" .
git status
```

---

## P10. T3-03/04/06 演示脚本与彩排（All）

```text
你是数字员工平台 PoC 仓库的演示负责人（负责人角色：A 总装 + C 前端 + B 安全）。开始前先通读：
- docs/DEMO_SCENARIO.md（10 步脚本、降级预案、当天 Checklist）
- docs/SECURITY_BOUNDARY.md §6.5（演示话术：虚构数据 + P0-lite 声明）
- PLANS.md T3-03/04/06 条目

目标：产出 8-10 分钟正式录屏所需的口播稿、演示时长控制表、降级彩排记录与问题清单。

交付物：
1. docs/DEMO_SCRIPT.md：逐画面口播稿（每 30-60 秒一个画面，含操作与预期画面），
   开头与结尾必须声明「全部为虚构数据 + P0-lite 模板化协作」。
2. docs/DEMO_RUNBOOK.md：演示当天 30 分钟 Checklist（按 DEMO_SCENARIO §5）+ L0/L1/L2 降级对照表。
3. 彩排记录：3 次全量彩排的问题清单与关闭状态；连续 2 次全量演练通过记录。

约束：
- 不得宣称已接入真实系统/真实知识库；不得展示真实数据或真实凭据。
- 60% 内容应不依赖 LLM 在线（保证断网可讲）。

验收标准：
- 口播稿可直接录屏使用；成片可独立播放；降级预案可现场执行。

验证命令：
（人工彩排；无自动化命令）
```

---

## P11. 契约变更 Prompt（需 A 批准，三处同步）

```text
你是数字员工平台 PoC 仓库的架构负责人（负责人角色：A 正式员工）。本次是契约变更任务，必须谨慎。

变更需求：{描述：例如新增字段/接口/状态值}
影响面预估：{前端类型 / 后端 Schema / OpenAPI / 种子数据 / 测试}

步骤：
1. 先通读 docs/API_CONTRACT.md（冻结版）与 shared-schema/types.ts、backend/app/schemas.py，
   确认当前三处是否已同步。
2. 设计变更：字段名、类型、可选性、向后兼容性（必须可选或缺省，禁止破坏现有调用方）。
3. 三处同步实施：后端 Pydantic Schema → 导出 OpenAPI → shared-schema/types.ts → API_CONTRACT.md 更新；
   在 API_CONTRACT.md 文末「契约变更登记」追加一行（日期/变更/批准人/涉及文件）。
4. 更新受影响的种子数据与测试，跑全量验证。

约束：
- 未获 A 批准前不得修改契约文档与 shared-schema；本 prompt 即视为 A 批准流程的一部分，
  但变更登记必须写明批准人。
- 保持向后兼容：新增字段可选、缺省返回 null，不返回 undefined。

验收标准：
- 三处一致（Schema / OpenAPI / TS 类型 / 契约文档）；pytest 与前端 typecheck 全绿。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
pnpm --filter frontend typecheck
```

---

## P12. Bug 修复通用 Prompt

```text
你是数字员工平台 PoC 仓库的工程师。请修复以下 Bug，并遵守仓库通用约束
（先读 PLANS.md / docs/ARCHITECTURE.md / docs/API_CONTRACT.md / docs/SECURITY_BOUNDARY.md）。

Bug 描述：{复现步骤 / 预期行为 / 实际行为 / 错误日志或报错码}
相关文件：{疑似文件与行号}

要求：
1. 先定位根因，说明证据（读代码/跑复现），再改代码；禁止盲改。
2. 最小改动，不破坏冻结契约与架构不变量（尤其不得绕过 Plugin Gateway、不得引入真实数据）。
3. 为 Bug 补充一条回归测试（后端 pytest 或前端 vitest，就近存放）。
4. 跑相关验证命令。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
pnpm --filter frontend typecheck
```

---

## P13. Code Review 通用 Prompt

```text
你是数字员工平台 PoC 仓库的资深 Reviewer（负责人角色：A/B 正式员工）。请对以下变更做 Code Review：

变更范围：{git diff 范围或文件清单}
重点检查（按优先级）：
1. 架构违规：业务模块是否直连知识库/DB/Workflow/RPA；是否绕过 Policy Engine / Plugin Gateway。
2. 数据安全：是否出现真实 Token/Key/Endpoint/内部内容；是否违反 source=demo 与 SAFEMODE。
3. 契约一致性：是否修改冻结契约/shared-schema；若修改是否三处同步并登记。
4. 审计完整性：插件调用是否都有 audit_event；trace_id 是否贯穿。
5. 测试与可运行性：是否就近补测试；验证命令是否全绿。

输出格式：按严重程度分级（Blocker / Major / Minor / Nit），每条给出文件:行号、问题与建议修复；
若无 Blocker/Major 再给出合并建议。

验证命令：
git diff --stat
```

---

## P14. Sprint 5 Secure Enterprise Integration（知识库 Adapter 安全接入 + 双模式切换）（Owner B，P0）

```text
你是数字员工平台 PoC 仓库的安全与企业资源负责人（负责人角色：B 正式员工）。开始前先通读：
- docs/API_CONTRACT.md §5（Knowledge Adapter Interface）与 §6.3（Sandbox）
- docs/SECURITY_BOUNDARY.md（数据分级、Secret/Config 边界、统一资源访问链、LLM 与外部服务边界）
- docs/ARCHITECTURE.md（L5 隔离与资源层；统一资源访问链不可绕过）
- docs/DEVELOPMENT_HANDOFF.md（Sprint 3 交付：Knowledge Adapter / Stub 现状）
- backend/app/services/knowledge_adapter.py、backend/app/services/config.py、backend/app/services/gateway.py

任务：Sprint 5 — Secure Enterprise Integration（模拟知识库安全接入 + Sandbox）。

重要边界（违反即返工）：
- 本任务是「接 Adapter」：只允许修改 Adapter 层（knowledge_adapter.py、config.py、.env.example、backend/tests/），
  不得修改业务代码（chat.py、routers/、gateway.py 的编排流、schemas.py、契约文档、前端）。
- 不得开始 AgentTeams 相关工作；完成后即停止。
- 测试与演示一律以 mock-data/ 模拟数据库为准（负责人已确认）；实际知识库只作为文件名/目录结构参考，
  不接入真实数据、不真实调用。

现状：
- MockKnowledgeAdapter：读 mock-data/kb/ 虚构 Markdown 返回 hits[]，source=demo。
- InternalKnowledgeAdapterStub：仅返回 stub 状态，未接入真实受控端点。
- select_adapter() 目前按 endpoint_ref 前缀 / resource_type 选择 Stub 或 Mock。
- config.py 已预留 DWP_INTERNAL_KB_ENDPOINT / DWP_INTERNAL_KB_CREDENTIAL_REF（仅引用名，无真实值）。
- 调用链已固定：业务模块 → gateway.search_knowledge() → select_adapter() → Adapter.search() → Audit。

目标：在不改变上层业务接口的前提下，把 Sprint 3 的 Stub/Mock 资源替换为可配置的受控企业资源 Adapter；
同时必须保留 Mock 兜底，保证 Demo 永不破坏。最终支持两种模式，切换只靠环境变量、零代码改动：
- DEMO_MODE=mock（默认）→ MockKnowledgeAdapter（mock-data/kb/ 虚构数据）
- SECURE_MODE=internal → InternalKnowledgeAdapter（经 DWP_INTERNAL_KB_ENDPOINT 调用受控内部端点）

交付物：
1. backend/app/services/config.py：
   - 新增两个模式开关（环境变量），切换只改配置、不改代码：
     DEMO_MODE=mock（默认）→ MockKnowledgeAdapter；
     SECURE_MODE=internal → InternalKnowledgeAdapter。
   - 优先级规则：SECURE_MODE=internal 且 DWP_INTERNAL_KB_ENDPOINT / DWP_INTERNAL_KB_CREDENTIAL_REF
     配置完整时启用 internal；未配置完整或上游不可达时自动回退 mock，并在审计中记录降级原因；
     两者均未设置时默认 mock。
   - 凭据只返回引用名/运行时解析，绝不落日志、绝不进 Prompt、绝不进前端。
2. backend/app/services/knowledge_adapter.py：
   - InternalKnowledgeAdapter 正式实现：httpx 访问受控端点，超时/5xx 映射为 503 RUNTIME_UNAVAILABLE；
     上游 403 映射为 POLICY_DENIED（含 policy_id/reason）并落审计；响应归一化为统一结构
     {source, knowledge_base_id, hits: [{title, snippet}]}。
   - Adapter 出口做敏感内容守卫：命中 L3/敏感标记的内容不得进入 LLM（未获批准即截断或返回拒绝），
     内容必须带 source 标签（demo / internal），LLMProvider 的 SAFEMODE 继续保持强制。
   - select_adapter() 按模式选择，internal 不可用时回退 Mock（不抛错、不破坏 Demo）。
3. backend/app/services/gateway.py 不修改编排逻辑；如确有必要的极小适配点（如把 mode 透传给审计）
   必须在交付说明中列出并说明原因（默认不做）。
4. backend/tests/test_secure_integration.py，逐条覆盖以下 7 个验证点：

重点验证（对应验收标准）：
1. 模拟知识库接口能否由 KnowledgeAdapter 接入：
   DEMO_MODE=mock 下 POST /internal/knowledge/search 返回 hits[]（source=demo），结构与契约一致。
2. 正式员工身份权限如何映射：
   DT-E10281（正式分身）访问 KB-INTERNAL（L2）→ allow（POLICY-001），hits 返回；
   VE-0001 仅可访问其被授权的库（KB-ONBOARD / KB-PUBLIC），未授权库 deny。
3. 实习生身份必须强制拒绝：
   DT-E20999（实习生分身）访问任何 L2 内部库 → 403 POLICY_DENIED（POLICY-002），且审计存在。
4. 不允许数据绕过 Plugin Gateway：
   业务模块只能经 gateway.search_knowledge()；测试断言绕过 Gateway 直调 Adapter 的行为为架构违规
   （或无法从业务层触达）；未授权插件经 /internal/gateway/invoke 直呼 → deny + 审计。
5. 不允许 Credential 进入前端或 Prompt：
   测试断言：LLMProvider 收到的 prompt 段不含凭据值；审计/日志不含凭据值；config 对外只暴露引用名。
6. 所有调用必须产生 Audit Trace：
   mock 与 internal 两种模式、allow/deny/approval/降级，每条调用都落 audit_event
   （employee_id / knowledge_base_id / decision / trace_id），可按 trace_id 聚合。
7. Sensitive content 不得未经批准发送至公网模型 API：
   SAFEMODE 单测（source != demo 拒发）+ 新增敏感内容守卫用例（L3 内容被截断/拒绝，不进入模型调用）。

约束：
- 不得把任何真实凭据/端点值写入仓库（含 .env.example，只允许占位名）。
- 不得修改冻结契约；/internal/knowledge/search 请求响应保持兼容。
- internal 模式是「受控演示/验证用 Adapter」，即使配置了端点也只应访问模拟受控服务，
  不得在测试中连真实企业系统（负责人确认：一律以模拟数据库为准）。
- 完成后停止，不开始 AgentTeams。

验收标准：
- 7 个验证点各有对应 pytest 用例，全部通过；后端全量 pytest（现有 51 + 新增）全绿。
- 手动验证：DEMO_MODE=mock 与 SECURE_MODE=internal（指向本地模拟受控端点）两种配置下，
  /internal/knowledge/search 均可工作，且无需修改任何业务代码。
- 文档记录：docs/ 下新增或更新「Sprint 5 接入说明」（模式开关、配置项、降级行为），
  并登记在 PLANS.md（T2-03/T3-05 相关条目勾选或备注）。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
# 双模式冒烟（由你本地受控环境执行，不接真实系统）
$env:DWP_KB_MODE="mock"; .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

---

## P15. Mock 知识库生成（按真实目录结构仿真，Owner B/D，P0）

> 更新（2026-08-18）：真实知识库样例已放入仓库（见 P16/P17）。仅当不参考真实内容时使用本 Prompt；
> 否则请使用 P17（先执行 P16 整理真实库）。

```text
你是数字员工平台 PoC 仓库的 Mock 数据工程师（负责人 B 主责、D 配合）。开始前先读：
- mock-data/kb/ 现有 5 篇（格式与「虚构」声明样例）
- backend/app/services/knowledge_adapter.py（Mock 解析规则：# 标题 → hits，只读文本）
- mock-data/seed.json（knowledge_bases / employee_plugin_grants 结构）
- docs/SECURITY_BOUNDARY.md（数据分级与虚构规则）

任务：按真实知识库的目录结构，生成 23 篇全虚构的 Markdown 模拟知识库。
说明：统一用 Markdown 是因为现有 MockKnowledgeAdapter 按纯文本解析；演示验证的是权限链路而非文档格式，
所以不做真实格式文件。文件名沿用真实名称仅为仿真目录结构。
边界：测试一律以 mock-data 为准；不接真实库、不改业务代码；禁止复制/改写真实内容。

目录镜像：
mock-data/kb/it-service/（6 篇）：
  企业微信相关使用QA.md  办公IT服务目录.md  VPN系统相关使用QA.md
  笔记本电脑管理规范.md  IT专员名录.md  协同办公系统相关使用QA.md
mock-data/kb/securities/（6 篇）：
  APP智能订单功能QA.md  融资融券业务QA.md  北交所相关业务QA.md
  上交所交易规则相关业务QA.md  科创板科创成长层相关业务QA.md  深交所新股申购相关业务QA.md
mock-data/kb/internal-reg/（6 篇）：
  反洗钱内部审计实施细则.md  债券与衍生产品业务部风险管理操作规程.md
  信息技术劳务外包管理规范.md  董事高级管理人员及证券从业人员投资行为管理办法.md
  文化建设管理办法.md  全国中小企业股份转让系统推荐业务立项标准.md
mock-data/kb/external-reg/（5 篇）：
  私募投资基金登记备案办法.md  证券公司投资者权益保护工作规范.md
  证券公司从业人员业务培训细则.md  证券从业人员职业道德准则.md  结算在线业务操作手册.md

质量要求（逐篇落实，宁可精不要滥；不赶工，写一篇自查一篇）：
1. 每篇文件头：# {主题}（虚构）+ 一行虚构声明（格式仿现有 kb-l2-hr.md）。
2. 内容与文件名强相关、写实不跑题，术语专业、表述自然；禁止空话、凑字数、占位符。
3. 按文档类型写：
   - QA 类：4-6 个贴近日常场景的问答（回答含操作步骤/要点/注意事项），并埋 3-5 个演示可搜关键词
     （如 VPN 篇：远程接入、双因素认证、连接失败）。
   - 目录/名录类：Markdown 表格 6-10 行（服务类别/服务项/渠道/时间/虚构联系人岗位），人名工号电话全虚构。
   - 规范/办法/细则类：仿制度文体（第一章 总则：目的/适用范围/职责 → 管理要求条款 → 附则），5-10 条；
     不得使用真实制度编号。
4. 严禁真实数据：不得出现真实人名、电话、邮箱、公司名、证券代码、制度原文；
   证券类统一用「示例证券 600XXX」式虚构占位。

种子注册（mock-data/seed.json）：
- 保留 KB-PUBLIC / KB-ONBOARD / KB-INTERNAL / KB-FINTECH 不变。
- 新增 4 个资源：KB-IT-SERVICE（L1，formal+intern）、KB-SECURITIES（L2，formal）、
  KB-REG-INTERNAL（L2，formal）、KB-REG-EXTERNAL（L1，formal+intern），doc_path 指向新文件。
- 补齐授权：DT-E10281 可读新增 L2 库；DT-E20999 无任何新增 L2 授权（保持 deny 演示）；
  VE-0001 不变（仅 KB-PUBLIC + KB-ONBOARD）。

执行顺序：
1. 先写四个子目录共 23 篇，每篇自查：声明、主题贴合、可检索、无真实数据。
2. 再改 seed.json 注册与授权。
3. 最后跑验证并汇报：seed 重建成功；GET /knowledge-bases 返回 8 个资源；
   每篇新库 search 返回非空 hits；DT-E10281 问「VPN 怎么连」allow、
   DT-E20999 问「融资融券流程」403 POLICY_DENIED；pytest 全绿。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m app.seed --reset
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
```

---

## P16. 解压并整理真实知识库（Owner B，P0）

```text
你是数字员工平台 PoC 仓库的文件整理工程师（负责人角色：B 正式员工）。开始前先读：
- .gitignore（secure-overlay/ 已忽略，真实数据只允许放这里）
- docs/SECURITY_BOUNDARY.md（仓库卫生：真实数据不得入库）

任务一：解压并整理真实知识库样例。
输入（仓库根目录 4 个 zip）：
- IT服务知识库.zip（31 文件，xlsx/docx/pdf）
- 证券业务知识库.zip（50 文件，xlsx/pdf/html/docx）
- 内规.zip（50 文件，docx/doc）
- 外规.zip（50 文件，pdf/doc/docx）

安全边界（违反即返工）：
- 先把 4 个 zip 移入 secure-overlay/raw/（该目录已被 .gitignore 忽略），根目录不再保留副本；
  若 zip 已被 git 跟踪，用 git rm --cached 解除跟踪（不删除本地文件）。
- 真实数据只存在于 secure-overlay/ 内；不得写入 mock-data/、docs/ 等可提交路径。
- 整理只依据文件名与元数据，不读取文档正文内容（避免真实内容进入模型上下文）。
- 不删除任何文件；疑似测试/重复文件只归档或标记，不丢弃。

执行步骤：
1. secure-overlay/raw/{it-service,securities,internal-reg,external-reg}/ 按分类解压（原样保留）。
2. secure-overlay/kb-real/organized/{同四分类}/ 生成整理副本：
   - 文件名规范化：去 "(n)" 序号、多余空格、重复扩展名（.doc.doc / .pdf.pdf）、"（更新）" 等冗余后缀；
   - 保留修订日期/版本信息（如 2026年5月修订）不得删除；
   - 重复文件：保留一份主文件，其余移入 organized/_duplicates/；
   - 疑似测试文件（含 测试 / test / （测试作废） / 新建测试 等字样）：移入 organized/_test_archive/。
3. 生成 secure-overlay/kb-real/MANIFEST.json：逐文件记录
   原始路径 / 规范化文件名 / 分类 / 格式 / 大小 / 处理动作（kept|renamed|duplicate|test|empty）/ 版本说明；
   末尾附统计（各分类数量、重复数、测试文件数）。
4. 汇报整理结果摘要。

约束：
- 不修改文档内容；只做复制、改名、归档；解压用 PowerShell Expand-Archive。
- 中文文件名注意编码；完成后 git status 不得出现任何真实文件。

验收标准：
- raw/ 与 organized/ 结构完整；MANIFEST.json 可打开、统计准确；
- 仓库可提交路径无真实文件；根目录 zip 已移入 secure-overlay/raw/。
```

---

## P17. 参考真实知识库生成模拟知识库（Owner B/D，P0，依赖 P16 + P18）

```text
你是数字员工平台 PoC 仓库的 Mock 数据工程师（负责人 B 主责、D 配合）。开始前先读：
- secure-overlay/kb-real/SELECTED.json（P18 筛选结果：22 个代表性文件，本任务以此为准）
- secure-overlay/kb-real/MANIFEST.json 与 organized/ 目录（真实知识库整理结果，仅作结构/主题参考）
- backend/app/services/knowledge_adapter.py（Mock 解析规则：现为 # 标题 → hits，本次扩展为多格式解析）
- backend/requirements.txt（本次需新增解析依赖）
- mock-data/seed.json（knowledge_bases / employee_plugin_grants 结构）
- docs/SECURITY_BOUNDARY.md（数据分级与虚构规则）

任务：按 SELECTED.json「照葫芦画瓢」生成全虚构的模拟知识库，且文件使用真实原格式
（xlsx / docx；真实 .doc 为旧版二进制、纯 Python 无法可靠生成，统一生成 .docx 并在交付说明中注明）。
范围：共 22 篇，放 mock-data/kb/{it-service,securities,internal-reg,external-reg}/（按 category 分目录）；
文件名 = SELECTED.json 的 mock_filename，但扩展名跟随 source_filename（.xlsx→.xlsx、.docx→.docx、.doc→.docx）。
不新增清单外文件，不做全量镜像。

安全边界（违反即返工）：
- 真实内容绝不允许进入 mock-data/：不得复制、不得近义改写真实条款/问答/数字/名单；所有内容必须重新创作。
- 真实公司名「兴业证券」→「示例证券」；真实产品/系统名（优理宝、知己优投、兴证e家、UF2.0、巡天平台等）→ 虚构等价名；
  通用软件名（企业微信、VPN、企业邮箱、协同办公）可保留，但内容全新创作；
  真实人名/电话/邮箱/证券代码/金额/日期一律虚构；证券规则不得引用真实条文/编号。
- 本任务由负责人 B 授权阅读真实文件（文件名 + 主题方向），真实内容不得进入仓库、不得外发。

按原格式生成（内容模板对应 doc_type，照葫芦画瓢）：
- qa → .xlsx：第一个 sheet 建表，列 = 问题/回答/分类/关键词，4-6 行；回答含操作步骤/要点/注意事项；
  每行问题埋 3-5 个演示可搜关键词（如 VPN：远程接入、双因素认证、连接失败）。
- catalog / roster → .xlsx：第一个 sheet 建表 6-10 行（服务类别/服务项/申请入口/服务时间/联系人岗位，
  或 部门/岗位/服务范围/姓名工号），人名电话全虚构。
- regulation → .docx：标题 + 第一章 总则（目的/适用范围/职责）→ 管理要求条款（5-10 条）→ 附则。
- guide → .docx：标题 + 前置条件 → 操作步骤（3-6 步）→ 注意事项/常见问题。
- 每个文件首部（docx 第一段 / xlsx 顶部行）写明「虚构演示数据」声明。
统一风格：简体中文、术语专业、表述自然；禁止空话/凑字数/占位符；宁可精不要滥，写一篇自查一篇。

交付物：
1. 22 个原格式虚构文件（按 SELECTED.json 的 category 分目录、扩展名规则命名）。
2. 多格式解析（仅 Adapter 层，不改业务代码）：
   - backend/requirements.txt 增加 python-docx、openpyxl、pdfplumber；
   - MockKnowledgeAdapter 的 kb.doc_path 指向目录时，递归读取目录内受支持文件并解析：
     .md 保持现有 # 标题解析；.docx 用 python-docx 提取段落/表格文本；.xlsx 用 openpyxl 逐行读取
     （首列作命中标题、次列作 snippet）；.pdf 用 pdfplumber 逐页提取；.doc 无法可靠解析则跳过并告警；
   - 统一返回 {source=demo, knowledge_base_id, hits[{title, snippet}]}，契约不变。
3. mock-data/seed.json 注册：
   - 保留 KB-PUBLIC / KB-ONBOARD / KB-INTERNAL / KB-FINTECH 不变；
   - 新增 4 个资源：KB-IT-SERVICE（L1，formal+intern）、KB-SECURITIES（L2，formal）、
     KB-REG-INTERNAL（L2，formal）、KB-REG-EXTERNAL（L1，formal+intern）；
     doc_path 分别指向 mock-data/kb/it-service、mock-data/kb/securities、mock-data/kb/internal-reg、mock-data/kb/external-reg；
   - 授权：DT-E10281 可读新增 L2 库；DT-E20999 无任何新增 L2 授权（保持 deny 演示）；VE-0001 不变。

执行顺序：
1. 以 SELECTED.json 为准确定文件清单与主题；必要时对照 organized/ 确认主题方向（不摘录内容、不扩大范围）。
2. 分四个分类生成原格式模拟文件，逐篇自查：虚构声明、主题贴合、可检索、无真实数据。
3. 加解析依赖、改 MockKnowledgeAdapter（多格式目录读取）、改 seed.json。
4. 验证并汇报：seed 重建成功；GET /knowledge-bases 返回 8 个资源；
   每个新分类目录内所有文件经 search 均能返回非空 hits（逐文件抽查）；
   POST /internal/knowledge/search：DT-E10281 + KB-IT-SERVICE + 「VPN 怎么连」→ allow + hits；
   DT-E20999 + KB-SECURITIES + 「融资融券流程」→ 403 POLICY_DENIED；pytest 全绿。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd backend; .\.venv\Scripts\python.exe -m app.seed --reset
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
```

---

## P18. 筛选真实知识库代表性文件（Owner B，P0，P17 的前置步骤）

```text
你是数字员工平台 PoC 仓库的知识库筛选工程师（负责人角色：B 正式员工）。开始前先读：
- secure-overlay/kb-real/MANIFEST.json（整理清单：action / 分类 / 规范化文件名 / 版本说明）
- secure-overlay/kb-real/organized/ 目录结构（只列目录与文件名，不读正文）

任务：从整理后的真实知识库中，为每个分类挑选少量有代表性的「独立」文件，
输出筛选清单 SELECTED.json，作为下一步生成模拟知识库（P17）的唯一参考。
背景：当前是测试/演示阶段，不需要全量镜像；只挑独立、能代表该库主题与文档类型的文件。
注意：以 MANIFEST 与 organized/ 实际文件为准；此前人工整理的参考文件名若与真实文件不符，一律以真实文件为准。

筛选规则：
1. 数据源：只从 organized/{it-service,securities,internal-reg,external-reg}/ 中
   action ∈ {kept, renamed} 的主文件挑选；排除 _duplicates/ 与 _test_archive/。
2. 排除附件/配套类文件：文件名含「附件」「起草说明」「编制说明」「修订说明」「暂缓实施条文」
   「模板」「报名表」「服务协议」「数据交换接口」等配套文档一律不选（依附主文件，非独立知识文档）。
3. 独立性与代表性：优先选
   - 覆盖不同文档类型（QA / 制度规范 / 操作手册 / 目录 / 名录）；
   - 覆盖高频演示主题，例如：
     IT 侧：企业微信、VPN、企业邮箱、协同办公、办公设备、IT 服务目录、笔记本规范、IT 专员名录；
     证券侧：融资融券、沪深交易规则、北交所、科创板、新股申购、APP 智能订单、港股通、股票期权；
     内规：反洗钱审计、风险管理、投资行为、合规、档案、印章、适当性、结算资金；
     外规：职业道德准则、业务培训、交易结算对账、操作手册、反垄断、投资者保护类示范实践；
   - 内容自包含、可独立回答一类问题。
4. 数量：每分类 4-6 篇，四类合计约 20 篇；宁缺毋滥，质量优先；
   同一文档多修订版本只保留最新一版；若某分类候选不足（如外规大量为附件类），允许 3-4 篇并在清单注明。

交付物：secure-overlay/kb-real/SELECTED.json（gitignored 目录，不进仓库），结构：
{
  "schema_version": "1.0",
  "generated_at": "...",
  "criteria": "筛选规则摘要",
  "selected": [
    {
      "category": "it-service|securities|internal-reg|external-reg",
      "source_filename": "organized/ 下的规范化文件名",
      "doc_type": "qa|regulation|manual|catalog|roster|guide",
      "topic": "一句话主题（供 mock 创作参考）",
      "reason": "选择理由（类型覆盖/高频主题/独立自包含）",
      "mock_filename": "建议模拟文件名（转 .md）"
    }
  ]
}
并在对话中打印每分类选中摘要表，供 B 审核。

约束：
- 只依据文件名与 MANIFEST 元数据判断，不读取文档正文内容。
- 不修改、不移动任何真实文件；只新增 SELECTED.json 到 secure-overlay/。
- 不要为了让清单好看而混入附件类文件。

验收标准：
- SELECTED.json 生成且可解析；每分类 3-6 篇；无附件/重复/测试文件混入；
- 摘要表完整；B 审核通过后再执行 P17。
```

---

## P19. 企业知识库 RAG 检索改造（Qwen Embedding，Owner B，P0，依赖 P17）

```text
你是数字员工平台 PoC 仓库的后端/检索工程师（负责人 B 正式员工）。开始前先读：
- docs/API_CONTRACT.md §5（Knowledge Adapter Interface：search 统一签名与 hits 结构）
- docs/SECURITY_BOUNDARY.md（Secret/Config 边界：API Key 只走环境变量；统一资源访问链）
- docs/ARCHITECTURE.md（L5 隔离与资源层；业务模块禁止绕过 Gateway）
- backend/app/services/knowledge_adapter.py、config.py、gateway.py
- backend/requirements.txt、mock-data/seed.json、mock-data/kb/（P17 生成的原格式模拟库）

任务：把企业知识库接口（Knowledge Adapter）从"读文件抽片段"升级为 RAG 检索形式，
嵌入使用 Qwen Embedding（DashScope OpenAI 兼容 API）。
范围：仅 Adapter 层与索引管线；不得修改业务代码（chat.py、routers/、gateway.py 编排、schemas、契约、前端）。

目标架构（接口与调用链不变）：
查询 → /internal/knowledge/search（不变）→ Policy（不变）→ Gateway（不变）
     → KnowledgeAdapter（RAG 实现）
          query 嵌入 → 向量余弦 top-k → hits[{title, snippet, score}]
     → Audit（不变）

交付物：
1. backend/app/services/embedding.py：QwenEmbeddingClient（httpx 调
   POST {base_url}/embeddings，模型 qwen3.7-text-embedding，dimensions=1024，encoding_format=float，
   支持批量 input，超时与错误统一映射 EmbeddingUnavailableError；响应取 data[i].embedding）。
   配置（.env.example 只放占位名，真实 Key 仅本地 gitignored .env）：
   DWP_EMBED_BASE_URL（默认 https://dashscope.aliyuncs.com/compatible-mode/v1；
     若使用百炼 MaaS 工作空间端点，填 https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1，
     把 {WorkspaceId} 换成真实业务空间 ID）
   DWP_EMBED_API_KEY（兼容读取 DASHSCOPE_API_KEY）
   DWP_EMBED_MODEL（默认 qwen3.7-text-embedding）
   DWP_EMBED_DIMENSIONS（默认 1024）
2. backend/app/services/kb_index.py：索引构建
   - 切块：xlsx → 每行一个问题+回答 chunk；docx → 按标题分段；pdf → 按页；md → 按现有 # 标题；
   - 嵌入 mock-data/kb/ 四分类全部文件，写入 SQLite 表 kb_chunk
     （id/kb_id/source_file/title/content/embedding BLOB(float32)/dims/created_at）；
   - CLI：python -m app.kb_index --rebuild；app.seed --reset 时顺带重建。
3. backend/app/services/rag_knowledge_adapter.py：RAGKnowledgeAdapter 实现同一 search() 接口：
   - query 嵌入 → numpy 余弦相似度 → top-k（默认 5）→ hits[{title, snippet, score}]，source=rag；
   - 嵌入不可用（无 Key/超时/网络失败）时抛 EmbeddingUnavailableError。
4. 模式开关（config.py）：DWP_KB_MODE=mock|rag|internal，默认 mock；
   select_adapter() 按模式路由：rag → RAGKnowledgeAdapter（嵌入失败自动降级 MockKnowledgeAdapter，
   降级写审计并保持 Demo 可用）；internal → 受控内部端点；mock → 原 Mock。
5. 测试 backend/tests/test_rag_adapter.py（用 FakeEmbedding 固定向量，不依赖真实 API）：
   - 索引构建：四分类全部文件均产生 chunk；
   - 检索：top-k 排序与 score 正确；hits 结构与契约一致（新增 score 可选字段，向后兼容）；
   - 链路：DT-E10281 + KB-IT-SERVICE allow；DT-E20999 + KB-SECURITIES 403 POLICY_DENIED 且审计存在；
   - 降级：嵌入不可用 → 返回 mock hits，不抛 5xx。

约束：
- API Key 只存在于本地环境变量，绝不入 Git/Prompt/日志；嵌入内容全部为 mock-data 虚构数据。
- 不修改冻结契约；hits 增加 score 为可选字段，不破坏现有字段。
- 演示与测试一律以 mock-data 模拟库为准；不接真实知识库。
- 本期检索仅 Embed + 余弦 top-k，不做 Rerank（语料规模小，rerank 收益低且增加延迟/成本）；
  RAGKnowledgeAdapter 中预留 rerank 扩展点即可，本期不实现。
- 完成后停止，不开始 AgentTeams。

验收标准：
- DWP_KB_MODE=rag 下：kb_index --rebuild 成功；/internal/knowledge/search 对 KB-IT-SERVICE 问
  「VPN 怎么连」返回 allow + 带 score 的 hits；DT-E20999 问 KB-SECURITIES 仍 403；
- 无 Key 或无网络时自动降级 mock，链路不中断；
- 后端 pytest 全绿；git status 无 Key/真实数据。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m app.kb_index --rebuild
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
```

---

## P20-1. 三级权限第一步：构造内部敏感（L3）知识库（Owner B，P0，数据层）

```text
你是数字员工平台 PoC 仓库的数据/后端工程师（负责人角色：B 正式员工）。
开始前先读：
- backend/app/services/knowledge_registry.py、knowledge_adapter.py、adapters.py
- mock-data/seed.json（knowledge_bases / plugins 结构）
- mock-data/kb/ 现有文件（虚构声明格式；四分类目录已入库）
- docs/API_CONTRACT.md §3.7（Knowledge API）与 §7（KnowledgeBaseDto）

任务：为三级权限体系构造「内部敏感（L3）」演示知识库。
本期不做申请/审批流（下一步 P20-2 做），只让敏感库存在且"未授权默认拒绝"行为生效。

交付物：
1. 新增插件 knowledge-l3（seed.json plugins）：id=knowledge-l3，name=敏感数据知识库，
   type=knowledge，endpoint_ref=mock://kb/l3，data_level=L3，status=active。
2. backend/app/services/knowledge_registry.py：plugin_id_for_level 支持 L3 → knowledge-l3。
3. 敏感库内容（纯数据工作，质量优先）：
   - mock-data/kb/customer-sensitive/ 目录，新增 2 个虚构文件（建议 xlsx 表格形态）：
     a) 示例客户KYC信息.xlsx：列=客户编号(虚构)/客户类型/KYC状态/风险等级/开户日期（虚构占位如 C0001）；
     b) 示例客户资产与交易KPI.xlsx：列=客户编号/资产区间/交易频次/持仓品种/收益区间（虚构汇总形态）；
   - 每个文件头带「虚构演示数据」声明；不得出现真实人名/电话/证件号/真实证券代码/真实金额；
   - 数据量 8-15 行，表格形态要像"敏感数据"而不是 QA。
4. seed.json knowledge_bases 增加：
   KB-CUSTOMER-SENSITIVE：name=示例客户敏感信息库，data_level=L3，resource_type=knowledge，
   allowed_employment_type=[formal]，department_scope=[*]，doc_path=mock-data/kb/customer-sensitive，
   description 注明「虚构敏感数据，仅供白名单申请演示」。
5. 不新增任何员工授权（默认全拒）；不修改策略规则（现有 P-DATA-003 对 L3 读即 deny，正好满足"未授权拒绝"）。

约束：
- 不修改现有 8 个知识库；不碰 access_request/接口/策略（下一步做）。
- 所有内容虚构；不引入真实数据/Key；不改前端。
- 若 customer-sensitive 目录被 .gitignore 误伤，仅将该目录纳入跟踪（git add -f），不影响其他 ignore 规则。

验收标准：
- cd backend; .\.venv\Scripts\python.exe -m app.seed --reset 成功；
- GET /api/v1/knowledge-bases 返回 9 个（含 KB-CUSTOMER-SENSITIVE，L3）；
- POST /internal/knowledge/search：DT-E10281 与 DT-E20999 访问 KB-CUSTOMER-SENSITIVE → 403 P-DATA-003
  （未授权默认拒绝）；
- 后端 pytest 全绿（现有 84 项不受影响）。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m app.seed --reset
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
```

---

## P20-2. 三级权限第二步：内部敏感白名单申请/审批（Owner B，P0，依赖 P20-1）

```text
你是数字员工平台 PoC 仓库的后端工程师（负责人角色：B 正式员工/安全与企业资源）。
本任务只做后端，前端由同事 A 后续整合；交付必须包含清晰接口说明，供 A 的 Codex 按 OpenAPI 对接。
开始前先通读：
- docs/API_CONTRACT.md（冻结契约 v1.1；本次为契约扩展，需在变更登记表追加记录）
- docs/SECURITY_BOUNDARY.md（数据分级 L1/L2/L3、审计、统一资源访问链）
- docs/ARCHITECTURE.md（五层架构、Policy/Gateway/Adapter 职责）
- backend/app/models.py、schemas.py、services/policy.py、services/gateway.py、services/knowledge_registry.py、
  services/knowledge_adapter.py、services/adapters.py、routers/、shared-schema/types.ts、mock-data/seed.json

背景与已定稿设计（B 已确认，不要偏离）：
- 三级权限：外部公开（L1，全员）、内部公开（L2，正式+授权岗位）、内部敏感（L3，白名单，仅正式员工可申请）；
- 所有资源（知识库/插件/数据）统一带三级权限等级：L1/L2 按既有 grant 规则，L3 一律走白名单申请；
- 审批：单级管理员审批（后端一键通过/拒绝）；access_request 保留 approval_chain 字段（JSON，预留多级审批，本期不实现）；
- 敏感库：新增虚构 KB-CUSTOMER-SENSITIVE「示例客户敏感信息库」（L3），内容全虚构、表格/数据形态；
- 演示链路：实习生访问敏感库被拒 → 正式员工发起申请 → 管理员批准 → 白名单授权生效 → 再访问 allow → 审计可追溯；
- 前端不做（A 负责）；但 shared-schema/types.ts 必须同步新 DTO（三处同步：OpenAPI / shared-schema / API_CONTRACT.md）。

交付物：
1. backend/app/models.py：新增 AccessRequest 表
   （id / applicant_no / resource_type / resource_id / reason / status: pending|approved|rejected|granted /
    approval_chain JSON 预留 / decided_by / decided_at / created_at）。
2. backend/app/schemas.py：AccessRequestCreate（resource_type/resource_id/reason）、
   AccessRequestOut、AccessRequestApproveIn（approve: bool, actor_no）。
3. backend/app/routers/access.py：
   - POST /api/v1/access-requests：发起申请；仅 employment_type=formal 可申请，
     实习生返回 403 POLICY_DENIED（策略拒绝，不落申请单）；
     resource_type 枚举：knowledge | plugin | data（申请对象不限于知识库）；
   - POST /api/v1/access-requests/{id}/approve：管理员一键通过/拒绝；
     通过时写 employee_plugin_grant（employee_id=申请人，plugin_id=资源对应插件，action=read，
     decision_mode=allow，并在备注/来源字段标记 whitelist；plugin/data 类资源按资源映射的插件写入）
     并置状态 granted；拒绝置 rejected；
     已终态再审批返回 409 STATE_CONFLICT；
   - GET /api/v1/access-requests：按 applicant_no / status 过滤查询（含待审批列表）。
4. 策略调整（backend/app/services/policy.py）：
   - P-DATA-003 由「L3 读一律 Deny」改为「L3 资源访问：存在已批准白名单授权（employee_plugin_grant
     decision_mode=allow 且来源 whitelist）→ Allow，否则 Deny」；覆盖 L3 知识读与 L3 插件执行/读取；
     默认拒绝语义不变；
   - 申请入口限制放在路由层校验（formal）与审计记录，不新增前端可见权限逻辑。
5. 敏感库与资源注册：
   - 新增插件 knowledge-l3（endpoint_ref mock://kb/l3，data_level=L3）；
   - knowledge_registry.plugin_id_for_level 支持 L3 → knowledge-l3；
   - mock-data/kb/customer-sensitive/ 新增 1-2 个虚构文件（表格/数据形态：虚构客户 KYC、
     资产区间、交易 KPI 汇总；文件头带「虚构演示数据」声明；不得包含真实数据）；
   - seed.json：新增 KB-CUSTOMER-SENSITIVE（data_level=L3，allowed_employment_type=[formal]，
     doc_path=mock-data/kb/customer-sensitive）；不授权任何员工（默认全拒，靠申请）。
   - 插件等级统一标注：核对 seed.json 全部插件 data_level 与三级语义一致
     （rpa-report 已是 L3，作为「敏感插件走白名单」演示对象；其余插件按实际数据等级标注）。
6. 审计：申请、审批、授权（写 grant）、访问四类事件全部落 audit_event，
   用 trace_id 串成一条链路；knowledge_base_id 字段记录 KB-CUSTOMER-SENSITIVE。
7. 契约三处同步：backend OpenAPI（FastAPI 自动）、shared-schema/types.ts 增加
   AccessRequest DTO、docs/API_CONTRACT.md 增加 §3.8 Access Request API 并在变更登记表追加一行
   （日期/变更/批准人=待 A 确认/涉及文件）。
8. backend/tests/test_access.py：
   - 实习生发起申请 → 403；正式员工发起 → 201 pending；
   - 批准 → status=granted + employee_plugin_grant 落库；再调 /internal/knowledge/search
     访问 KB-CUSTOMER-SENSITIVE → allow；拒绝 → 仍 deny（403 P-DATA-003）；
   - L3 插件场景：未白名单时 VE-0001 执行 rpa-report → deny；申请并批准后 → allow；
   - 终态重复审批 → 409；审计按 trace 可聚合。

约束：
- 不修改业务模块对现有 L1/L2 的访问行为；不改变冻结的既有接口字段。
- 沙箱策略本期不动（敏感资源执行更严隔离列为后续可选）。
- 全部内容虚构；不引入真实客户/资产/交易数据；不引入真实 Key。
- 前端不改；交付说明中附接口摘要（路径/请求/响应/状态码）与 OpenAPI 地址说明。

验收标准：
- 后端 pytest 全绿（现有 84 + 新增 access 用例）；
- API 冒烟走通演示链路：实习生 deny → 正式申请 → 批准 → granted → 再访问 allow；
- shared-schema/types.ts 与 OpenAPI、API_CONTRACT.md 三处一致；
- git status 无真实数据/Key；新敏感库文件已纳入跟踪（customer-sensitive 目录不在 ignore 范围）。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
cd backend; .\.venv\Scripts\python.exe -m app.seed --reset
```

---

## P21. 管理员平台：员工快捷管理（新建数字员工 + 权限设置）（Owner B，P0，前端独立交付后交 A 合并）

```text
你是数字员工平台 PoC 仓库的全栈工程师（负责人角色：B 正式员工/安全与企业资源）。
本任务补齐"管理员平台"的最小闭环：前端新建数字员工 + 员工权限设置（插件授权/安全字段）；
后端缺的授权接口一并补上。完成后推分支交给同事 A 合并（前端与他个人工作中心整合时可能复用）。
开始前先通读：
- docs/API_CONTRACT.md §3.1（Employee API；PUT /employees/{no}/plugins 为 📋 待实现，本次实现并登记）
- docs/SECURITY_BOUNDARY.md（L1/L2/L3 三级语义、默认拒绝、授权只经 Policy）
- docs/P20（三级权限：L3 走白名单申请，管理员界面设置 L1/L2 授权即可，L3 提示走申请）
- backend/app/routers/employees.py、schemas.py、models.py、services/policy.py
- frontend/src/pages/Employees.tsx、EmployeeDetail.tsx、api/client.ts、shared-schema/types.ts

现状：
- 后端已有 GET/POST/PUT/DELETE /employees、/employees/{no}、/employees/{no}/chat、/employees/{no}/workspace；
- 缺 PUT /api/v1/employees/{employee_no}/plugins（插件授权批量更新，契约标 📋）；
- 前端只有员工列表/详情（授权表只读），无新建/编辑入口。

交付物：
1. 后端：PUT /api/v1/employees/{employee_no}/plugins
   - 请求 {grants: [{plugin_id, action, decision_mode: allow|deny|approval}]}；整体替换该员工的插件授权；
   - 校验：员工存在（404）、插件存在（404）、decision_mode 合法（400 VALIDATION_ERROR）；
   - 授权是配置写入（employee_plugin_grant 表），不走 Gateway；写审计（action=employee_grant_update，
     decision=allow，result_summary=插件数量摘要）；
   - 契约登记：API_CONTRACT.md §3.1 该行 📋→✅，变更登记表追加；shared-schema/types.ts 增加
     EmployeeGrantUpdate DTO（前端复用）；OpenAPI 自动同步。
2. 前端：员工管理入口（沿用 AntD 主题、中文文案、typecheck/test 全绿）
   - Employees 页顶部加「新建数字员工」按钮 + Modal 表单：name/type(twin|virtual|rpa)/owner_human_no/
     department/role_prompt/runtime_type/location/internet/max_data_level(L1|L2|L3，
     L3 旁注明"需白名单申请")/allowed_domains；提交调 POST /employees；
   - EmployeeDetail 页加「权限设置」按钮 + Modal：
     a) 插件授权编辑：现有 grants 列表可增删改（选择插件、action、decision_mode allow|deny|approval），
        提交调 PUT /employees/{no}/plugins；
     b) 安全字段调整（可选同 Modal 或单独区）：max_data_level/internet/location/allowed_domains，
        提交调 PUT /employees/{no}；
   - api/client.ts 增加 createEmployee / updateEmployeeGrants（沿用 request<T> 封装）；
   - 交互反馈：成功 message、失败错误展示（403/404/409 错误形状统一处理）。
3. 测试：
   - 后端 backend/tests/test_employee_grants.py：设 allow → gateway 调用该插件 allow；设 deny → 403；
     设 approval → 返回 approval 决策；员工/插件不存在 404；审计落库；
   - 前端 vitest：Employees 页点新建 → 表单提交调用 POST；EmployeeDetail 权限设置 → 提交调用
     PUT /plugins；typecheck/build 全绿。

约束：
- 不改变冻结的既有接口字段；新接口按契约风格实现并登记（批准人=待 A 确认）。
- 权限判断只由 Policy 执行：管理员界面只是配置写入，不自行解释权限语义。
- L3 资源在管理界面只提示"需白名单申请"，不直接给 L3 插件开 allow（除非按 P20 白名单流程）。
- 全部使用现有虚构种子；不引入真实数据/Key；不改聊天/团队等既有页面逻辑。

验收标准：
- 后端 pytest 全绿（现有 93 + 新增）；前端 typecheck/test/build 全绿；
- 手工冒烟：新建虚拟员工 → 详情可看 → 设 knowledge-l2 allow → 该员工 search KB-INTERNAL allow；
  设 deny → 403；审计可见 employee_grant_update；
- 三处契约同步一致（OpenAPI / shared-schema / API_CONTRACT.md）。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
pnpm --filter frontend typecheck
pnpm --filter frontend test
pnpm --filter frontend build
```

---

## P24-1. 记忆插件整合 Phase 1：后端记忆核心（Owner B，P0，独立 integration 分支）

```text
你是数字员工平台 PoC 仓库的后端工程师（负责人 B 正式员工）。
任务：把实习生分支 origin/feature/personal-memory 的"记忆插件"后端核心整合进一条独立的 integration 分支。

【分支安全协议（最高优先级，违反即返工）】
- 正式分支保护：master 与 codex/sprint5-mock-kb 是两名正式员工的正式分支，**严禁直接提交/推送**；
- 第一步必须新建整合分支：git checkout -b integration/memory-plugin codex/sprint5-mock-kb；
- 所有改动只提交到 integration/memory-plugin；完成后**不要 merge 回任何正式分支、不要 push**，停在本地汇报；
- 若整合过程中发现与正式分支内容冲突或需要改动正式分支，停止并汇报，不得自行解决。

开始前先读（只读，不改动）：
- git show origin/feature/personal-memory:backend/app/routers/memory.py
- git show origin/feature/personal-memory:backend/app/services/memory_permission.py / memory_compress.py / memory_attachment.py
- git show origin/feature/personal-memory:backend/app/models.py（MemoryEntry 与 ChatSession 扩展字段）
- git show origin/feature/personal-memory:backend/tests/test_memory.py
- 当前 backend/app/models.py、schemas.py、main.py、mock-data/seed.json、docs/API_CONTRACT.md

任务（Phase 1 后端记忆核心；不碰聊天自动写入，那是 Phase 2）：
1. models.py：新增 MemoryEntry（按分支模型字段落地，7 维标签：
   subject_type/subject_no/kind/content/content_type/related_subject_no/trace_id/file_ref/
   visibility/data_level/lifecycle/created_at）；ChatSession 增加 title/deleted/summarized（默认值，向后兼容）。
2. schemas.py：MemoryCreate / MemoryOut（字段与分支一致）。
3. 落地 4 个文件（内容取自 origin/feature/personal-memory，适配当前代码风格与导入路径）：
   routers/memory.py、services/memory_permission.py、services/memory_compress.py、services/memory_attachment.py。
4. main.py 注册 memory router（Base Path /api/v1，前缀 /memory）。
5. mock-data/seed.json：新增 1-2 条虚构示例记忆（E10021 的 fact，visibility=personal，data_level=L2），
   与分支 test_memory.py 的种子断言一致。
6. .gitignore 增加 backend/storage/（附件存储目录）。
7. 测试：把分支 test_memory.py 落到 backend/tests/ 并适配当前 conftest（内存库 + 现有 seed）；
   覆盖：种子样例、写入/读取（最新在前）、按主体隔离、权限过滤（X-Demo-Actor）、附件/压缩接口不报错。
8. 契约：docs/API_CONTRACT.md 增加 §Memory API（POST/GET /memory、POST /memory/summarize、
   POST /memory/attachments），变更登记追加一行（待 A 确认）。

约束：
- 不修改聊天核心（chat.py 自动写入记忆是 Phase 2，本期不做）；
- memory_permission.py 的 PoC 硬编码管理员 E10021 保留，注释标注"后续接三级权限/白名单"；
- 不引入真实数据/Key；附件仅支持文本文件；所有内容虚构；
- 保持现有测试全绿（当前 131 项 + 新增记忆测试）。

验收标准：
- 后端 pytest 全绿；
- API 冒烟：POST /api/v1/memory 写入 → GET /api/v1/memory?subject_no=E10021 能读到（最新在前）；
  X-Demo-Actor 权限过滤（非本人/非管理员读不到 personal 记忆）；POST /api/v1/memory/summarize 不报错；
- 所有改动只在 integration/memory-plugin 分支，正式分支未被触碰。

验证命令：
cd backend; .\.venv\Scripts\python.exe -m app.seed --reset
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q
```

---

## 附：常用验证命令速查

```powershell
# 后端测试
cd backend; .\.venv\Scripts\python.exe -m pytest tests -q

# 前端
pnpm --filter frontend typecheck
pnpm --filter frontend test
pnpm --filter frontend build

# 重建种子
cd backend; .\.venv\Scripts\python.exe -m app.seed --reset

# 一键初始化
.\scripts\init_demo.ps1

# 问答冒烟（三场景）
cd backend
.\venv\Scripts\python.exe -c "import httpx; r=httpx.post('http://127.0.0.1:8000/api/v1/employees/DT-E10281/chat', json={'message':'查询一下内部制度。'}, timeout=120); print(r.json()['message'])"
.\venv\Scripts\python.exe -c "import httpx; r=httpx.post('http://127.0.0.1:8000/api/v1/employees/DT-E20999/chat', json={'message':'查询一下内部制度。'}, timeout=120); print(r.json()['message'])"
.\venv\Scripts\python.exe -c "import httpx; r=httpx.post('http://127.0.0.1:8000/api/v1/employees/VE-0001/chat', json={'message':'新员工第一天要做什么？'}, timeout=120); print(r.json()['message'])"
```
