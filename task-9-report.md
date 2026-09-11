# Task 9 完成报告：AgentTeams 协作前端契约与客户端

提交：`260fc72 feat: add agentteams collaboration client`

## 已实现

- 在 `shared-schema/types.ts` 增加协作任务、节点、关系图、事件、Matrix 消息、健康检查、最终结果和提交请求类型。
- 明确终态为 `completed`、`failed`、`timed_out`、`cancelled`；健康 DTO 仅传递后端返回的 `ready_for_demo`，未把 Matrix 配置或 Worker 绑定伪装为已连接。
- 在 `frontend/src/api/client.ts` 增加 health、创建、列表、详情、图、增量事件、消息、结果、取消和重试调用；任务 ID 均通过 `encodeURIComponent` 编码，`after_event_id=0` 会被保留。
- HTTP 错误处理兼容 FastAPI 的字符串 `detail` 和结构化 `detail.message`，因此 503 健康未就绪能展示后端的实际文案。
- 新增指定前端测试，覆盖 URL、请求体、零事件游标和结构化 503 错误。

## TDD 证据

先运行新增测试，得到 3 个预期失败：`api.agentTeamsHealth is not a function`、`api.createAgentTeamsTask is not a function`。完成最小实现后，聚焦测试变为 `3 passed`。

## 验证

在隔离工作树执行：

```powershell
pnpm typecheck
pnpm exec vitest run --reporter=dot
pnpm build
```

- TypeScript 类型检查通过。
- 全量前端测试命令成功结束；输出仅有项目既有的 React Router v7 迁移提示，无测试失败。
- 生产构建通过。Vite 报告既有单 JS chunk 超过 500 kB 的体积提示，未由本任务引入，也不影响构建结果。

本报告按要求保持未提交。未执行真实 Matrix/AgentTeams 连通性检查；前端单元测试只验证客户端如何忠实传递后端响应。
