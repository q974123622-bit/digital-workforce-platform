# 数字员工平台 Demo（Sprint 1：Platform Skeleton）

面向券商内部汇报的「数字员工平台」PoC。本仓库 **只包含虚构演示数据**，不包含任何真实内部数据、真实 Token 或真实内部端点。

## 当前状态（Sprint 1）

- 前端：React + TypeScript + Vite + Ant Design，包含首页 / 数字员工列表与详情 / 插件中心 / 安全中心 / 协作团队占位页。
- 后端：FastAPI + SQLite，提供 `/health`、员工 / 插件 / 策略 / 审计 CRUD，以及只读的知识库与团队接口。
- Mock 数据：正式员工 2、实习生 2、数字分身 2、虚拟员工 3、插件 5、虚构知识库 4、团队 1。
- 测试：后端 pytest（健康检查 + 四组 CRUD + 种子数量断言）；前端 vitest 冒烟 + `tsc` 类型检查。

尚未实现（后续 Sprint）：LLM 问答、Policy 评估执行、Plugin Gateway、Team 任务协作、Harness / Sandbox。

## 目录结构

```text
logicalNpc/
  frontend/            # React + TS + Vite + AntD
  backend/             # FastAPI + SQLAlchemy + SQLite
  shared-schema/       # 前后端统一 TypeScript 类型（契约）
  demo-data/           # 虚构种子数据 + 虚构知识库
  docs/                # 架构 / 安全边界 / API 契约 / 演示脚本
  scripts/             # 一键初始化脚本
  PLANS.md             # 任务清单（checkbox）
```

## 环境要求

- Python 3.11+
- Node.js 20+（本机 v24 已确认）
- pnpm 9+

## 快速开始

### 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.seed --reset   # 重建 SQLite 并灌入虚构种子
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

健康检查：<http://127.0.0.1:8000/health>

### 前端

在仓库根目录执行：

```powershell
pnpm install
pnpm --filter frontend dev
```

打开 <http://localhost:5173>（`/api` 由 Vite 代理到后端 8000）。

### 一键初始化

```powershell
.\scripts\init_demo.ps1
```

## 测试

```powershell
# 后端
cd backend
.\\.venv\\Scripts\\python.exe -m pytest tests -q

# 前端（类型检查 + 单测）
pnpm --filter frontend typecheck
pnpm --filter frontend test
```

## 数据与安全说明

- 所有数据均为虚构（见 `demo-data/`），知识库文件内容同样为虚构示例。
- 无真实 Token：`DEEPSEEK_API_KEY` 等密钥以环境变量注入、绝不入库（当前 Sprint 尚未接入 LLM）。
- 外部依赖 `deepseek-harness/`、`higress/` 不入库（见 `.gitignore`）。
- 契约文档：`docs/API_CONTRACT.md`；共享类型：`shared-schema/types.ts`（与后端 OpenAPI 手动同步，变更以契约文档为准）。

## 端口

- 前端：5173
- 后端：8000
