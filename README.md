# 数字员工平台 Demo

面向券商内部汇报的「数字员工平台」PoC。本仓库 **只包含虚构演示数据**，不包含任何真实内部数据、真实 Token 或真实内部端点。

## 当前 MVP

- **用户端与管理端分离**：`/` 是嵌入聊天工具风格的用户端，`/admin/` 是账号、AI 同事、知识授权、运行状态和人设管理端。UI 使用 Tailwind CSS + Ant Design，移动端可作为卡片式 H5 使用。
- **账号登录**：服务端 Session + HttpOnly Cookie；会话接口校验登录身份，管理员接口校验角色。Mock 账号 `E10281`，初始密码 `Demo@123456`（仅本地演示，可用 `DWP_DEMO_PASSWORD` 覆盖）。
- **三类主体**：真实员工、每人一个数字分身、可复用的岗位型 AI 员工。当前核心 AI 员工为“AI员工平台”和“投资分析AI员工”。
- **分身自主委派**：分身先判断能否自己回答；需要专业知识时，由模型在可用 AI 员工清单中选择一名同事并委派。V1 每次最多一跳、一个子员工，禁止子员工继续委派，以规避循环等待、消息风暴和死锁。
- **知识域强隔离**：“AI员工平台”授权内规、外规、IT 服务等通用知识；“投资分析AI员工”授权证券业务、投行咨询等投资知识。工具清单由实时授权动态生成，不能凭提示词越权访问。
- **企微统一入口预留**：Mock 通讯录和回调路由已具备；按 `corp_id + external_user_id` 映射到本人分身，不为每个分身创建企微机器人。后续可在一个企微应用入口中使用不同身份键路由。
- **独立 Harness 注册表**：每个分身/AI 员工有稳定容器名、工作区和 `stopped/ready/busy/failed` 状态，管理端可启停。当前完成的是运行时管理边界和状态机，真实容器镜像拉起仍需测试环境确定镜像与网络策略。
- **知识库适配层**：默认读取 `mock-data/kb`。已预留 `volcengine_mcp` 模式，未来通过 MCP 调用火山引擎；未配置端点或服务契约时会失败关闭，不伪造生产结果。
- **Agent Teams 延后**：旧协作代码保留为实验能力，但 V1 用户界面不开放群聊编排与复杂 Team 工作流，先验证知识问答、身份、权限、委派和部署。

仓库只包含虚构种子账号、人物、知识库与业务数据，不包含真实内部数据和真实密钥。

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
  docs/                # 架构 / 安全边界 / API 契约 / 测试指南 / 交接 / 演示脚本
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

启动后访问用户端 <http://localhost:5173>，管理端 <http://localhost:5173/admin/>。

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
| `DEEPSEEK_MODEL` | 模型名；本次已用 `deepseek-v4-flash` 完成真实链路验证 |
| `DWP_EMBED_API_KEY`（兼容 `DASHSCOPE_API_KEY`） | 阿里百炼 Key（RAG 向量检索必需） |
| `DWP_KB_MODE` | `mock` / `rag` / `internal` / `volcengine_mcp`；MVP 使用 `mock` |
| `DWP_VOLCENGINE_MCP_ENDPOINT` | 火山引擎 MCP 服务地址（后续接入） |
| `DWP_VOLCENGINE_MCP_CREDENTIAL_REF` | 凭据引用名，只保存引用，不保存明文凭据 |
| `DWP_REQUIRE_AUTH` | `1` 强制登录（部署默认），测试兼容模式可设为 `0` |
| `DWP_HARNESS_ENABLED` | `1` 启用 Harness（需 dwp-dsh 镜像），`0` 用 demo 模式（演示稳定） |
| `DWP_TEAM_BACKEND` | `auto` 启用 AgentTeams 协作；`builtin` 仅运行平台编排 |
| `AGENTTEAMS_COLLAB_TIMEOUT` | AgentTeams 协作等待秒数（5-120，默认 30） |
| `AGENTTEAMS_WORKER_MODEL` | AgentTeams worker 模型（默认 `deepseek-chat`） |

## 测试

完整的环境准备、自动化命令、UI 样例、验收标准和故障排查见：
[测试指南](docs/TESTING_GUIDE.md)。

```powershell
# 后端 162 项
cd backend
.\\.venv\\Scripts\\python.exe -m pytest tests -q

# 回到仓库根目录后执行前端（类型检查 + V1 单测 + 生产构建）
cd ..
pnpm --filter frontend typecheck
pnpm --filter frontend test
pnpm --filter frontend build

# 黄金链路联调（问答 → RAG → 团队任务 → 审批 → 审计，8 步）
cd backend
.\\.venv\\Scripts\\python.exe ..\\scripts\\golden_chain.py
```

推荐的 UI 冒烟指令：

```text
请帮新员工令狐冲办理入职，完成 HR 材料确认、IT 账号开通，并生成入职权限报表
```

预期任务卡分别显示 `DeepSeek Harness + 员工查询 MCP`、
`DeepSeek Harness + 入职流程 Workflow`，以及等待审批的 `报表机器人 · RPA`。

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
