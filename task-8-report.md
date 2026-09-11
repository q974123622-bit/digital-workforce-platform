# Task 8 完成报告：AgentTeams 协作证据 UI

基线：`8b0ed03`。

## 已实现

- 图 API 从已持久化的协作状态返回当前轮次和已选 Skill；空值保持为空，不伪造就绪或 Matrix 证据。
- 页面展示已选 Skill、派发轮次、阻塞依赖、工具调用、Policy 决策、审计记录和最终结果。
- 事件时间线只突出服务端新返回的事件；任务未终态时在 3 秒后重新拉取服务端任务详情、图、事件、消息和结果，不在浏览器中生成或推进任务状态。
- 仅 strict + `$` 开头的真实 Matrix Event ID 显示为“Matrix事件”；本地模式明确显示“Transport事件ID”。
- 本地 Demo 页面保留可编辑的简短默认目标“评估新员工A是否可以按期入职。”，提交时发送受控 canonical 入职剧本（固定标题、说明和空材料）；strict 模式仍提交用户实际输入。

## 三方保护与提交范围

- B：`HEAD` / `8b0ed03`。
- O：`tmp/task8-before/manifest.json` 保护副本中的 9 个文件。
- A：恢复后的当前工作树。
- O 与 B 经仓库换行符清理后 9/9 语义一致。以 B/O/A 创建的临时索引三方应用无冲突、无 unmerged entries；`frontend/src/api/client.ts` 无实际变化，未纳入。
- 已提交 `c887337 feat: expose AgentTeams collaboration evidence in UI`。提交范围为 8 个文件：后端 router/schema、共享类型，以及 AgentTeams 页面、测试、任务树、时间线和页面样式。

## 新鲜验证

```powershell
$env:PYTHONPATH = (Resolve-Path 'backend').Path
& 'backend\.venv\Scripts\python.exe' -m pytest backend\tests\test_agentteams_collaboration_api.py -q
# 24 passed

pnpm --dir frontend test
# 24 passed

pnpm --dir frontend build
# TypeScript + Vite production build passed
```

前端测试覆盖持久化 Skill/轮次/阻塞依赖/Policy 审计/Matrix ID 的显示，以及 local_demo canonical 提交与 strict 用户输入提交。测试输出仍有既有 React Router future warnings；构建输出仍有 Vite 大 chunk 提示。

## 修复轮次 1：干净提交树依赖

- `c887337` 的干净导出在 router 导入 `agentteams_local_scenario` 时失败，错误为 `ModuleNotFoundError`；这是提交树遗漏受控依赖，而非前端或 Matrix 运行问题。
- `b0ccda1 fix: include AgentTeams local scenario contract` 仅新增 `backend/app/services/agentteams_local_scenario.py`（6 个 canonical 常量，无 transport/runtime 代码）并删除 router 中未调用、且引用未定义 `enabled`/`mode` 的 `_legacy_inspect_environment` dead function。
- 新提交的 service 导入图不再包含 HEAD tree 缺失模块。单独导出的干净 backend 可导入 `app.main` / AgentTeams router，并通过 API 聚焦测试 `13 passed`；当前工作树的扩展同文件测试为 `24 passed`。前端页面聚焦 `24 passed`，生产构建通过。
