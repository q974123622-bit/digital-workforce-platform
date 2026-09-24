# AI员工平台 Demo

面向券商内部汇报的「AI员工平台」PoC。本仓库 **只包含虚构演示数据**，不包含任何真实内部数据、真实 Token 或真实内部端点。

## 当前 MVP

- **用户端与管理端分离**：`/` 是嵌入聊天工具风格的用户端，`/admin/` 是账号、AI 同事、知识授权、运行状态和人设管理端。UI 使用 Tailwind CSS + Ant Design，移动端可作为卡片式 H5 使用。
- **三账号 MVP**：`E10281`（张三）和 `E20999`（陈晓萌）是独立员工账号，分别拥有自己的数字分身；`admin` 是独立管理员账号，不拥有数字分身，也不出现在通讯录。登录页只输入访问密钥，由密钥直接识别身份。
- **三类主体**：真实员工、每人一个数字分身、可复用的岗位型 AI员工。当前核心 AI员工为“AI员工平台”和“投资分析AI员工”。
- **分身自主委派**：分身先判断能否自己回答；需要专业知识时，由模型在可用 AI员工清单中选择一名同事并委派。V1 每次最多一跳、一个子员工，禁止子员工继续委派，以规避循环等待、消息风暴和死锁。
- **知识域强隔离**：“AI员工平台”授权内规、外规、IT 服务等通用知识；“投资分析AI员工”授权证券业务、投行咨询等投资知识。工具清单由实时授权动态生成，不能凭提示词越权访问。
- **企微统一入口预留**：Mock 通讯录和回调路由已具备；按 `corp_id + external_user_id` 映射到本人分身，不为每个分身创建企微机器人。后续可在一个企微应用入口中使用不同身份键路由。
- **统一插件体系**：能力统一归入 `Plugin`，只分 `Skill` 与 `MCP`。支持 ZIP 安全扫描、版本、审核、手动发布、回滚、AI员工授权和员工启停；知识库是知识库 MCP 下的受控资源。
- **五轮记忆**：Harness 默认携带最近五组完整问答；更早轮次异步压缩，通过 `MemoryAdapter` 写入本地 MockMemory 或后续内部 mem0。长期记忆按“租户 × 请求员工 × Agent”隔离。
- **会话文件分析**：聊天中可上传 Markdown、TXT、DOCX、文本型 PDF、XLSX 和 CSV。文件在当前会话内持续可用，Harness 通过受控工具按需检索片段或读取表格范围；旧 Office 与扫描 PDF 分别预留转换、OCR MCP，不会静默转发到公共服务。
- **四个独立 Harness 实例**：AI员工平台、投资分析AI员工、张三数字分身和陈晓萌数字分身分别运行在稳定的独立容器中。每轮自动装载当前有效 Skill，并通过平台 Tool Gateway 使用知识检索、一次委派和显式记忆工具。
- **知识库适配层**：知识检索统一走内部知识引擎（HTTP 只读检索，经平台 KB ID 映射到远端知识库），失败会明确报错且不会静默回退演示数据。真实地址和认证只允许进入本地 `.env` 或公司 Secret 系统。
- **Agent Teams 延后**：旧协作代码保留为实验能力，但 V1 用户界面不开放群聊编排与复杂 Team 工作流，先验证知识问答、身份、权限、委派和部署。

仓库只包含虚构种子账号、人物、知识库与业务数据，不包含真实内部数据和真实密钥。

## 目录结构

```text
logicalNpc/
  frontend/            # React + TS + Vite + AntD（工作台、插件中心、记忆页、管理后台）
  backend/             # FastAPI + SQLAlchemy + SQLite（身份、策略、插件、记忆、网关、Harness）
  adapters/            # Runtime / Knowledge / MCP Adapter 预留（仅契约）
  mock-data/           # 虚构种子数据（员工/岗位/盈米技能）
  docker/              # DeepSeek Harness 镜像（Dockerfile.dsh）
  tests/               # 测试布局索引
  shared-schema/       # 前后端统一 TS 类型（契约）
  scripts/             # init_demo.ps1 / run_demo.ps1 / golden_chain.py
  docs/                # 架构 / 安全边界 / API 契约 / 测试指南 / 交接 / 演示脚本
  deploy/offline/      # 堡垒机离线部署、备份、状态与自检脚本
  PLANS.md             # 任务清单（checkbox）
```

## 环境要求

- Python 3.11+
- Node.js 20+（本机 v24 已确认）
- pnpm 9+
- Docker Desktop（可选，Harness 用）

## 一键启动（推荐）

```powershell
.\scripts\run_demo.ps1 -Docker
```

一条命令完成：检查/安装依赖 → 初始化数据库 → 确保四个 Harness 容器 → 启动后端（8000）→ 启动前端（5173）。

可选参数：

```powershell
.\scripts\run_demo.ps1 -NoReset -Docker  # 保留会话、插件和记忆并重启完整 Harness 模式
.\scripts\run_demo.ps1 -Docker           # 重置演示数据并启动完整 Harness 模式
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
| `DWP_REQUIRE_AUTH` | `1` 强制登录（部署默认），测试兼容模式可设为 `0` |
| `DWP_HARNESS_ENABLED` | `1` 启用四个独立 Harness 容器 |
| `DWP_AGENT_EXECUTION_MODE` | MVP 固定使用 `harness`；失败不回退旧 ChatOrchestrator |
| `DWP_MEMORY_MODE` | `mock` / `internal`；默认使用本地 MockMemory |
| `DWP_INTERNAL_MEM0_BASE_URL` | 内部 mem0 地址，仅由受控 Secret 配置 |
| `DWP_ARTIFACT_ROOT` | 插件 Staging 与 Published 制品目录 |
| `DWP_MCP_RUNTIME_MODE` | 本地为 `mock`，测试环境可配置标准化沙箱运行时 |
| `DWP_LOGIN_KEY_E10281_FILE` | 张三访问密钥 Secret 文件路径 |
| `DWP_LOGIN_KEY_E20999_FILE` | 陈晓萌访问密钥 Secret 文件路径 |
| `DWP_LOGIN_KEY_ADMIN_FILE` | 管理员访问密钥 Secret 文件路径 |
| `DWP_ENABLE_LEGACY_PASSWORD_LOGIN` | `1` 临时启用旧账号密码接口，默认 `0` |

## 测试

完整的环境准备、自动化命令、UI 样例、验收标准和故障排查见：
[测试指南](docs/TESTING_GUIDE.md)。

```powershell
# 后端 183 项
cd backend
.\\.venv\\Scripts\\python.exe -m pytest tests -q

# 回到仓库根目录后执行前端（类型检查 + V1 单测 + 生产构建）
cd ..
pnpm --filter frontend typecheck
pnpm --filter frontend test
pnpm --filter frontend build

```

推荐的三条 UI 黄金链路：

```text
AI员工平台：公司 VPN 怎么申请？
投资分析AI员工：融资融券有哪些风险控制要求？
张三数字分身：帮我查一下公司 VPN 申请流程。
```

第三条应显示“张三数字分身 → AI员工平台”的一次受控委派；刷新后执行轨迹和回答仍应存在。

## 数据与安全说明

- 所有数据均为虚构（见 `mock-data/`），知识库文件内容同样为虚构示例。
- 真实 Key（DeepSeek / 阿里百炼）只存本地 gitignored `backend/.env` 或环境变量，绝不入库。
- 默认 Mock 模式只使用虚构知识文档；切换内部知识或 mem0 前必须通过受控 Secret 配置，并完成授权和审计验收。
- 外部依赖 `deepseek-harness/`、`higress/` 不入库（见 `.gitignore`）。
- 契约文档：`docs/API_CONTRACT.md`；共享类型：`shared-schema/types.ts`（与后端 OpenAPI 手动同步）。

## 端口

- 前端：5173
- 后端：8000
- Harness（Docker）：容器内运行，无对外端口
