# 数字员工平台 Demo

面向券商内部汇报的「数字员工平台」PoC。本仓库 **只包含虚构演示数据**，不包含任何真实内部数据、真实 Token 或真实内部端点。

## 当前状态（Sprint 1-6 + RAG + 黄金链路）

- **Sprint 1/1.5**：前后端骨架、五层架构、API 契约 v1.1 冻结、目录整理。
- **Sprint 2（Core Control Plane）**：Employee Identity、Policy Engine（四维评估，POLICY-001~005）、Plugin Gateway、Mock Adapter、全决策审计。
- **Sprint 3（Enterprise Resource & Security）**：Knowledge Adapter（Mock + Stub + 多格式解析）、Resource Registry（8 个知识库）、Sandbox Policy + Mock Executor、Secret/Config 环境变量引用。
- **Sprint 4（Chat + DeepSeek）**：LLMProvider（chat/tool_call/structured_output + SAFEMODE）、Session Manager、Chat Orchestrator（≤3 轮工具，Deny 卡片）。
- **Sprint 5（Team）**：TeamTaskOrchestrator（模板拆解 + Worker 执行 + 审批 + LLM 汇总）、DeepSeek Harness Docker 接入（`DWP_HARNESS_ENABLED=1` 启用）、AgentTeams Adapter 桩。
- **Sprint 6（工作台）**：前端聊天页 + 员工工作台面板（角色配色 / 插件授权 / 知识库权限 / 安全策略）+ 人设注入（role_prompt 进 system prompt）+ 前端 Team 任务页。
- **RAG 检索**：Qwen Embedding（qwen3.7-text-embedding）+ kb_chunk 向量索引 + 余弦 top-k，`DWP_KB_MODE=rag` 时启用，失败自动降级 Mock。
- **黄金链路联调（T3-02）**：`scripts/golden_chain.py` 8/8 通过——问答 → RAG → 团队任务 → 审批 → 审计。

Mock 数据：正式员工 2、实习生 2、数字分身 2、虚拟员工 3、插件 6、策略 9、虚构知识库资源 8、团队 1。

## 目录结构

```text
logicalNpc/
  frontend/            # React + TS + Vite + AntD（含聊天页/工作台/Team 任务页）
  backend/             # FastAPI + SQLAlchemy + SQLite（services/ 为身份/策略/网关/RAG/Harness）
  adapters/            # Runtime / Knowledge / Workflow / RPA Adapter 预留（仅契约）
  mock-data/           # 虚构种子数据 + 虚构知识库（含四分类文档目录）
  docker/              # DeepSeek Harness 镜像（Dockerfile.dsh）
  tests/               # 测试布局索引
  shared-schema/       # 前后端统一 TS 类型（契约）
  scripts/             # init_demo.ps1 / run_demo.ps1 / golden_chain.py
  docs/                # 架构 / 安全边界 / API 契约 / 交接 / 演示脚本
  output/pdf/          # 项目计划与完成度交接 PDF
  PLANS.md             # 任务清单（checkbox）
```

## 环境要求

- Python 3.11+
- Node.js 20+（本机 v24 已确认）
- pnpm 9+
- Docker Desktop（可选，Harness 用）

## 一键启动（推荐）

```powershell
.\scripts\run_demo.ps1
```

一条命令完成：检查/安装依赖 → 重置数据库与种子 → 启动后端（8000）→ 启动前端（5173）。

可选参数：

```powershell
.\scripts\run_demo.ps1 -NoReset     # 跳过重置数据
.\scripts\run_demo.ps1 -Docker      # 启用 Harness Docker 模式（构建 dwp-dsh 镜像，Team 子任务真实容器执行）
```

启动后访问 <http://localhost:5173>。

## 手动启动

### 后端（端口 8000）

```powershell
cd backend
python -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
# 复制 .env.example 为 .env 并填入 DEEPSEEK_API_KEY（.env 已被 gitignore，绝不提交）
.\\.venv\\Scripts\\python.exe -m app.seed --reset
.\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8000
```

健康检查：<http://127.0.0.1:8000/health>

### 前端（端口 5173）

```powershell
pnpm install
pnpm --filter frontend dev
```

## 关键环境变量（backend/.env，gitignore）

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek Key（问答/汇总必需） |
| `DEEPSEEK_MODEL` | 模型名（官方接口用 `deepseek-chat`；`v4-flash` 为预留） |
| `DWP_EMBED_API_KEY`（兼容 `DASHSCOPE_API_KEY`） | 阿里百炼 Key（RAG 向量检索必需） |
| `DWP_KB_MODE` | `mock` / `rag` / `internal`（默认 rag，失败自动降级 mock） |
| `DWP_HARNESS_ENABLED` | `1` 启用 Harness（需 dwp-dsh 镜像），`0` 用 demo 模式（演示稳定） |

## 测试

```powershell
# 后端 78 项
cd backend
.\\.venv\\Scripts\\python.exe -m pytest tests -q

# 前端（类型检查 + 单测 11 项）
pnpm --filter frontend typecheck
pnpm --filter frontend test

# 黄金链路联调（问答 → RAG → 团队任务 → 审批 → 审计，8 步）
cd backend
.\\.venv\\Scripts\\python.exe ..\\scripts\\golden_chain.py
```

## 数据与安全说明

- 所有数据均为虚构（见 `mock-data/`），知识库文件内容同样为虚构示例。
- 真实 Key（DeepSeek / 阿里百炼）只存本地 gitignored `backend/.env` 或环境变量，绝不入库。
- RAG 向量化调用阿里公网 embedding 服务，内容全部为虚构 mock 文档（符合 SAFEMODE `source=demo`）。
- 外部依赖 `deepseek-harness/`、`higress/` 不入库（见 `.gitignore`）。
- 契约文档：`docs/API_CONTRACT.md`；共享类型：`shared-schema/types.ts`（与后端 OpenAPI 手动同步）。

## 端口

- 前端：5173
- 后端：8000
- Harness（Docker）：容器内运行，无对外端口
