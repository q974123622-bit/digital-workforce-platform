# Task 8 P1 修复报告：取消任务后抑制陈旧出站消息

提交：`e9e8747 fix: suppress cancelled agentteams outbox`

## 问题

取消根协作任务会新增 `task.cancel` 出站消息，但先前创建任务时已入队的
`task.submit` 仍是 `pending`。Runner 按时间顺序发送待处理出站消息，因此会把
`task.submit` 先发送到 Matrix，随后才发送取消通知。

## 修复

- Runner 在处理每条待发送消息前读取根 `TaskRun`/根节点的取消状态。
- 根任务已取消时，所有非 `task.cancel` 的待发送消息都改为
  `delivery_status=cancelled` 并跳过发送。
- `task.cancel` 保持可发送，因此 Matrix 仍会收到取消通知。
- 非取消任务没有筛选变化，既有出站发送行为保持不变。

## 回归测试

新增测试执行真实的持久化流程：`create_task -> cancel_task -> runner.tick_once`。
断言 Matrix 发送序列仅为 `task.cancel`，旧 `task.submit` 状态为 `cancelled`，取消
通知状态为 `sent`。

验证命令：

```powershell
$env:PYTHONPATH = (Resolve-Path 'backend').Path
& 'D:\lincwc\Desktop\digital workforce platform\backend\.venv\Scripts\python.exe' -m pytest backend\tests\test_agentteams_collaboration_api.py backend\tests\test_agentteams_runner.py -q
```

结果：`41 passed`。仅有既有 TestClient/httpx 弃用警告以及 sandbox 下 pytest cache
临时路径警告，未影响测试结果。

本报告及先前的 `task-8-report.md` 均按要求保持未提交。
