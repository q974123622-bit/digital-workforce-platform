# 两轮稳定性修复与运行恢复说明

本文记录 2026-09-10 完成的会话执行与运行稳定性基线。若旧文档仍描述 AgentTeams 为默认路径，以本文和当前代码为准。

## 1. 当前执行主链路

```text
POST /api/v1/conversations/{id}/runs
  → ConversationRunService（统一创建用户消息与 AgentExecution）
  → 对应数字员工 Harness 容器
  → 受控知识、插件与一次委派工具
  → AgentExecutionEvent
  → SSE /runs/{execution_id}/events
  → 正式 assistant 消息
```

旧 `POST /messages` 仅为兼容入口，内部调用同一 Run 服务，并返回 `Deprecation: true` 与 `X-DWP-Execution-ID`；新前端不得再调用它。

## 2. 清空、重启和重试

- 会话、消息和执行均带 `generation`。清空会话会递增 generation、取消活动执行并立即隐藏旧代消息。
- Harness 返回前再次校验执行状态和 generation，旧任务不能在清空后把答案写回当前会话。
- 服务启动时将遗留的 `queued/running/streaming/waiting_approval` 执行标记为 `AGENT_PROCESS_RESTARTED`，允许用户显式重试，但不会自动重放。
- 数据库保证同一会话最多一个活动执行，冲突返回 HTTP 409。
- 历史重试使用 `POST /runs/{execution_id}/retry`，后端从原执行绑定的消息序号读取问题，不依赖浏览器内存。

## 3. SSE 与前端恢复

- 最终 `answer_done` 到正式消息加载完成之间保留流式正文，避免答案闪烁。
- 事件按 SSE ID 去重；页面恢复后可以从持久化事件继续读取。
- 连续三次 SSE 连接失败后停止自动重连并显示手动恢复入口。
- 旧任务轮询采用 2.5、5、10、20、30 秒退避；连续五次失败后暂停。
- 401 统一触发登录过期状态；普通网络错误只显示断线，不误判为退出登录。
- 执行历史默认加载最近 20 次，通过 `before_execution_id` 分页加载更早记录。

## 4. 长期记忆同步

`DWP_MEMORY_MODE=internal` 时启动数据库 Outbox Worker：

```text
MemorySyncJob pending
  → 原子领取 running
  → 内部 mem0 upsert/update/delete
  → completed
  或按 5/15/45/120/300 秒退避，最多五次后 failed
```

自动记忆先写本地再异步同步，不阻塞回答；显式“请记住”仍要求同步确认。服务重启后，遗留 `running` 任务恢复为 `pending`。管理员手动重试会重置次数并立即重新入队。

内部 mem0 的真实地址和 Authorization 只能通过受控环境变量或 Secret 注入，不得进入源码、镜像、日志或审计。

## 5. AgentTeams 状态

当前 MVP 固定：

```dotenv
DWP_TEAM_BACKEND=builtin
```

兼容识别旧变量 `DWP_TEAM_BACKEND_MODE`，但无论旧值是否为 `auto`，会话和群聊都不会进入 AgentTeams、Workflow 或 RPA 路径。代码和历史数据暂时保留归档，不参与启动、执行或失败降级。

## 6. 验证命令

```bash
cd backend && pytest -q
pnpm --filter @dwp/frontend test
pnpm --filter @dwp/frontend typecheck
pnpm --filter @dwp/frontend build
```

验收重点：运行中清空不会复活答案、服务重启后没有僵尸执行、历史问题可正确重试、SSE 最终答案无闪烁、旧 `/messages` 也产生完整执行记录、mem0 失败不阻塞普通对话。
