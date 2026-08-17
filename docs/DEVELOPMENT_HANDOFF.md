# 开发交接文档（Sprint 1.5 Handoff）

> 版本：v1.0（2026-08-17）
> 目的：让两名正式员工（A 架构/总装、B 安全/企业资源）在本基线上串行开发，实习生（C 前端、D Mock/测试）按契约并行。
> 状态：Sprint 1 Platform Skeleton 已完成并通过全量验证；本文件为稳定基线交接。

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
| 后端 API 测试（10 用例） | `cd backend; .\.venv\Scripts\python.exe -m pytest tests -q` | ✅ 10 passed |
| 前端类型检查 | `pnpm --filter frontend typecheck` | ✅ |
| 前端冒烟测试 | `pnpm --filter frontend test` | ✅ 1 passed |
| 前端生产构建 | `pnpm --filter frontend build` | ✅（chunk 体积提示非阻塞） |

测试布局说明见 `tests/README.md`。后端测试使用内存 SQLite + 种子，不触碰 `backend/dwp.db`。

## 4. Mock 数据位置

| 内容 | 位置 |
|---|---|
| 种子数据（员工/插件/策略/审计/团队/知识库登记） | `mock-data/seed.json` |
| 虚构知识库文档（L1 ×1、L2 ×3） | `mock-data/kb/` |
| 重建命令 | `cd backend; .\.venv\Scripts\python.exe -m app.seed --reset` |

所有数据均为虚构，知识文档头部含「虚构」声明。**禁止**用真实内容替换。

## 5. 下一阶段负责人

| 方向 | 负责人 | 说明 |
|---|---|---|
| LLM Provider + Chat Orchestrator | A（正式/架构总装） | Sprint 2 主线，依赖 G1 DeepSeek 连通性 |
| Policy Engine + Plugin Gateway | B（正式/安全） | Sprint 2 主线，与 A 并行 |
| 前端聊天页 / 安全页增强 | C（实习生） | 等契约冻结后按 `API_CONTRACT.md` 实现 |
| Mock Adapter 内容 + 自动化测试扩展 | D（实习生） | 按契约补 Mock 数据与用例 |
| Sandbox / Harness 集成 | A/B 串行 | Sprint 3，门禁 G2/G3 |

串行建议：A 先完成 LLM Provider（T1-01）→ B 完成 Policy/Gateway（T1-02/03）→ 联调 Chat（T1-06）。

## 6. 下一阶段允许修改哪些目录

✅ 可修改（按契约）：`backend/app/`、`backend/tests/`、`adapters/`、`frontend/src/`、`mock-data/`、`docs/`、`scripts/`

⚠️ 需 A 批准：`shared-schema/types.ts`、`docs/API_CONTRACT.md`、`docs/ARCHITECTURE.md`（契约/架构变更，三处同步）

🚫 禁止：`higress/`、`deepseek-harness/`（外部依赖，本机引用不入库）；Secure Overlay（仓库外，仅 A/B）；任何真实数据/Token/端点写入仓库。

## 7. 已冻结、原则上不能修改的接口

冻结清单（修改需 A 批准 + 变更登记）：

1. 通用约定：Base Path `/api/v1`、错误形状、错误码枚举、`X-Demo-Actor`、`trace_id`。
2. Employee API（路径与 DTO 字段：平铺结构）。
3. Policy API（DTO：`effect/enabled/priority`）。
4. Plugin API（DTO：`endpoint_ref/data_level/status`）。
5. Audit API（DTO 字段与过滤参数）。
6. Chat API（SSE 事件类型枚举）。
7. Runtime Adapter Interface（`run(subject, task, context) -> RuntimeResult`）。
8. Knowledge Adapter Interface（search 请求/响应结构）。
9. 统一资源访问链（Identity → Policy → Gateway → Adapter → Resource）不可绕过。

## 8. 已知决策记录（Sprint 1.5 冻结）

- DigitalTwin / VirtualEmployee 用 `digital_employee.type` 区分，不建独立表。
- RuntimeBinding 不建独立表：`runtime_type` / `runtime_ref` 内嵌于 `digital_employee`；Sandbox 配置（`location` / `internet` / `max_data_level` / `allowed_domains`）同样内嵌。P0-lite 场景足够；需多 Runtime 时再建模。
- EmployeeDto 采用平铺结构（v1.0 嵌套草案作废）。
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
