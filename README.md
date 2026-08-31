# 数字员工平台 Demo

面向券商内部汇报的「数字员工平台」PoC。本仓库 **只包含虚构演示数据**，不包含任何真实内部数据、真实 Token 或真实内部端点。

## 当前状态（Sprint 1-13 + RAG + 协同执行链路）

- **Sprint 1/1.5**：前后端骨架、五层架构、API 契约 v1.1 冻结、目录整理。
- **Sprint 2（Core Control Plane）**：Employee Identity、Policy Engine（四维评估，POLICY-001~005）、Plugin Gateway、Mock Adapter、全决策审计。
- **Sprint 3（Enterprise Resource & Security）**：Knowledge Adapter（Mock + Stub + 多格式解析）、Resource Registry（9 个知识库）、Sandbox Policy + Mock Executor、Secret/Config 环境变量引用。
- **权限治理**：L3 读取采用“正式员工申请 → 正式员工数字分身审批 → 只读白名单”；L3 执行/导出/删除仍由 POLICY-005 人工审批，聊天仅注入当前身份可访问的知识库清单。
- **Sprint 4（Chat + DeepSeek）**：LLMProvider（chat/tool_call/structured_output + SAFEMODE）、Session Manager、Chat Orchestrator（≤3 轮工具，Deny 卡片）。
- **Sprint 5（Team）**：TeamTaskOrchestrator（模板拆解 + Worker 执行 + 审批 + LLM 汇总）、DeepSeek Harness Docker 接入（`DWP_HARNESS_ENABLED=1` 启用）。
- **Sprint 6（工作台）**：前端聊天页 + 员工工作台面板（角色配色 / 插件授权 / 知识库权限 / 安全策略）+ 人设注入（role_prompt 进 system prompt）+ 前端 Team 任务页。
- **Sprint 7（职场会话台）**：以消息、通讯录为主入口；流程目录收敛为使用指南；通讯录展示数字员工工号、部门和负责人；群聊分发（分身判断任务/闲聊）、SubtaskExecutor、SandboxManager Docker 真启动。
- **Sprint 8-12（协同执行）**：先持久化唯一 TaskRun；AgentTeams 负责讨论、认领与风险提示；Identity → Policy/Gateway → 员工 DeepSeek Harness → Plugin Adapter Tool 负责受控执行；审批通过后才执行原子任务。协作事件按 `task_id`、发送者和时间窗口隔离，超时不会再复制任务或触发双重副作用。
- **Sprint 13（统一能力契约）**：Skill 明确为 instruction capability，Plugin 声明统一 actions/input_schema/executor；每个数字员工由独立 Harness 上下文驱动，Plugin Adapter 作为受控工具调用。Harness 不可用时 UI 明确显示 `Demo Adapter 降级`。
- **Sprint 7（我的职场）**：企业微信式个人工作中心——会话列表（我的分身置顶）/ 通讯录 / 微信气泡对话 / 技能上传（文本/Markdown 注入分身人设）/ 工作流目录卡片（点击查看步骤与授权成员）；私聊与群聊统一由 Conversation 承载。群聊消息由分身判断「任务/闲聊」：任务型接入 TeamTaskOrchestrator（拆解→指派→Gateway 执行→审批→Leader 汇总，任务卡片内联到触发消息之后，子任务结果可读化，同请求自动去重，支持一键清空会话），闲聊仅一位成员回复；执行器为 SubtaskExecutor 接口（默认 Gateway，真实 RPA/Workflow 后续接入）。
- **RAG 检索**：Qwen Embedding（qwen3.7-text-embedding）+ kb_chunk 向量索引 + 余弦 top-k，`DWP_KB_MODE=rag` 时启用，失败自动降级 Mock。
- **黄金链路联调（T3-02）**：`scripts/golden_chain.py` 8/8 通过——问答 → RAG → 团队任务 → 审批 → 审计。

Mock 数据：正式员工 2、实习生 2、数字分身 2、通用虚拟员工 4、RPA 员工 1、插件 12（含 L3 敏感知识入口）、策略 9、虚构知识库资源 9、团队 1、技能 5（张三 4 + 陈晓萌 1）、职场会话 2。角色已拆分为入职协调、HR、IT、采购和报表自动化，避免 IT/HR 身份越界代办采购或 RPA。

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
| `DWP_KB_MODE` | `mock` / `rag` / `internal`（默认 mock；rag 嵌入失败自动降级 mock） |
| `DWP_INTERNAL_KB_BASE_URL` | 内部知识引擎基础地址（仅 `internal` 模式） |
| `DWP_INTERNAL_KB_X_ORG` / `DWP_INTERNAL_KB_X_TENANT` / `DWP_INTERNAL_KB_X_USER` | 内部知识引擎身份上下文 |
| `DWP_INTERNAL_KB_AUTHORIZATION` | 内部知识引擎认证值，仅通过受控环境或部署 Secret 注入 |
| `DWP_INTERNAL_KB_ID_MAP` | 平台知识库 ID 到内部数字 ID 的 JSON 映射，例如 `{"KB-IT-SERVICE":751}` |
| `DWP_HARNESS_ENABLED` | `1` 启用 Harness（需 dwp-dsh 镜像），`0` 用 demo 模式（演示稳定） |
| `DWP_TEAM_BACKEND` | `auto` 启用 AgentTeams 协作；`builtin` 仅运行平台编排 |
| `AGENTTEAMS_COLLAB_TIMEOUT` | AgentTeams 协作等待秒数（5-120，默认 30） |
| `AGENTTEAMS_WORKER_MODEL` | AgentTeams worker 模型（默认 `deepseek-chat`） |

## 测试

完整的环境准备、自动化命令、UI 样例、验收标准和故障排查见：
[测试指南](docs/TESTING_GUIDE.md)。

内部知识引擎的配置、只读探测、正式链路验证、增加知识库及 AI 员工权限注意事项见：
[内部知识检索接入交接手册](docs/INTERNAL_KB_INTEGRATION_HANDOFF.md)。

```powershell
# 后端 182 项
cd backend
.\\.venv\\Scripts\\python.exe -m pytest tests -q

# 回到仓库根目录后执行前端（类型检查 + 单测 21 项 + 生产构建）
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
- 内部知识引擎配置只存 `backend/.env`、受控环境变量或部署 Secret；`internal` 模式强制保留平台 Policy 与内部 `enable_filters=true`，审计不记录命中片段。
- RAG 向量化调用阿里公网 embedding 服务，内容全部为虚构 mock 文档（符合 SAFEMODE `source=demo`）。
- 外部依赖 `deepseek-harness/`、`higress/` 不入库（见 `.gitignore`）。
- 契约文档：`docs/API_CONTRACT.md`；共享类型：`shared-schema/types.ts`（与后端 OpenAPI 手动同步）。

## 端口

- 前端：5173
- 后端：8000
- Harness（Docker）：容器内运行，无对外端口
