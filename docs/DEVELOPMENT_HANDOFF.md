# 开发交接文档（Sprint 1.5 Handoff）

> 版本：v1.0（2026-08-17）
> 目的：让两名正式员工（A 架构/总装、B 安全/企业资源）在本基线上串行开发，实习生（C 前端、D Mock/测试）按契约并行。
> 状态：Sprint 1-4 已完成；Sprint 5 TeamTaskOrchestrator 已完成（2026-08-18）；Harness 尝试进行中（G2 止损中）；本文件为稳定基线交接。

## 1.13 黄金链路联调（T3-02，2026-08-18）

- **脚本**：`scripts/golden_chain.py`（`cd backend; .\.venv\Scripts\python.exe ..\scripts\golden_chain.py`），8 步全链路可重复验证。
- **结果**：8/8 通过——健康检查；正式分身问内部制度 Allow；实习生同问 POLICY-002 Deny；RAG 向量检索命中 KB-IT-SERVICE；团队任务 3 子任务审批挂起；审批完成 + Leader 汇总；审计 trace 贯穿（create/execute×3/approve/summarize）；会话历史持久化。
- **注意**：内部接口（`/internal/knowledge/search` 等）不带 `/api/v1` 前缀，契约 §6 定义于 `/internal/`。

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
