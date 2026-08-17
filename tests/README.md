# tests/ — 测试布局说明

> 状态：Sprint 1.5 冻结（2026-08-17）。测试保留在各模块就近位置，本目录为索引说明，避免迁移破坏既有配置。

## 测试位置与运行方式

| 测试 | 位置 | 运行命令 | 覆盖 |
|---|---|---|---|
| 后端 API 测试 | `backend/tests/` | `cd backend; .\.venv\Scripts\python.exe -m pytest tests -q` | 健康检查、种子数量、员工/插件/策略/审计 CRUD、错误统一形状、团队与知识库只读 |
| 前端冒烟测试 | `frontend/src/App.test.tsx` | `pnpm --filter frontend test` | 路由与首页渲染 |
| 前端类型检查 | — | `pnpm --filter frontend typecheck` | TS 类型一致性（含 shared-schema 契约类型） |

## 约定

- 后端测试使用内存 SQLite（`sqlite://`）+ 种子数据，不触碰 `backend/dwp.db`。
- 新增后端测试放入 `backend/tests/`；新增前端测试放在对应 `frontend/src/` 文件旁（`*.test.tsx`）。
- 测试数据一律来自 `mock-data/`，禁止引入真实内容。
