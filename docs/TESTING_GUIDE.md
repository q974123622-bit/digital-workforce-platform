# 数字员工平台测试指南

本文面向开发、测试和演示人员，说明如何验证 AgentTeams、DeepSeek Harness、
Policy/Gateway、Plugin Adapter、审批状态机及前端持续轮询。仓库只使用虚构演示数据。

## 1. 测试前准备

环境要求：

- Python 3.11+
- Node.js 20+
- pnpm 9+
- Docker Desktop（真实 DeepSeek Harness 模式需要）
- `backend/.env` 已按 `backend/.env.example` 配置；不要提交真实 Key

推荐以真实 Harness 模式启动：

```powershell
# 在仓库根目录执行
.\scripts\run_demo.ps1 -Docker
```

该命令会停止旧进程、重置 SQLite 演示数据并启动：

- 前端：<http://localhost:5173>
- 后端：<http://127.0.0.1:8000>

需要保留现有任务和会话时使用：

```powershell
.\scripts\run_demo.ps1 -Docker -NoReset
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

预期返回 `status=ok`。

## 2. 自动化测试

### 2.1 后端

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -q
```

当前基线：`149 passed`。测试覆盖身份、策略、Gateway、AgentTeams、Harness、
Adapter 单次调用、审批、生命周期、会话轮询数据和长文本完整保留。

### 2.2 前端

```powershell
# 回到仓库根目录
cd ..
pnpm --filter frontend typecheck
pnpm --filter frontend test
pnpm --filter frontend build
```

当前基线：类型检查通过、`21 passed`、生产构建成功。Vite 的大 chunk 提示是性能建议，
不影响本轮功能验收。

### 2.3 黄金链路

```powershell
cd backend
.\.venv\Scripts\python.exe ..\scripts\golden_chain.py
```

预期 8/8 通过，覆盖问答、RAG、团队任务、审批和审计。

## 3. UI 主流程：新员工入职

1. 打开 <http://localhost:5173>。
2. 进入“我的职场”中的“新员工入职协作”群。
3. 为避免短时间内重复任务判断，每次使用不同的虚构姓名。
4. 发送：

```text
请帮新员工令狐冲办理入职，完成 HR 材料确认、IT 账号开通，并生成入职权限报表
```

预期任务卡：

| 数字员工 | 运行时 | 工具 | 预期状态 |
|---|---|---|---|
| VE-0002 HR 助理 | DeepSeek Harness | 员工查询 MCP | 已完成 |
| VE-0003 IT 助理 | DeepSeek Harness | 入职流程 Workflow | 已完成 |
| RPA-0001 自动化报表机器人 | 审批前为 pending | 报表机器人 · RPA | 待审批 |

点击“批准”后，RPA-0001 应显示 `运行时：DeepSeek Harness` 并完成报表工具调用；
Leader 最终汇总必须基于三个 Adapter 工具回执生成。

重点检查：

- 任务来源显示 `AgentTeams 团队协作`；AgentTeams 不可用时允许显示内置降级。
- Harness 计划包含员工工号、人设、职责、Task ID、当前子任务和协作结论。
- Harness 计划只是工具调用计划，不能冒充工具已经执行。
- 业务结果来自 MCP、Workflow 或 RPA Adapter 回执。
- 页面同时显示“运行时”和“工具”，不得只显示笼统的 `Adapter`。

## 4. 审批状态机

### 4.1 批准

对入职权限报表点击“批准”。预期：

- 审批请求立即返回，RPA 在后台继续执行。
- 页面持续轮询，直到 RPA 和任务进入完成状态。
- 审计中先出现 `approval`，再出现 `allow`。
- 只有 Adapter 回执成功后才能把 RPA 子任务标记为完成。

### 4.2 拒绝

重新创建一个使用新姓名的入职任务，在报表环节点击“拒绝”。预期：

- RPA 子任务和总任务进入 `denied`。
- 不生成报表成功回执。
- 已完成的 HR、IT 子任务不重复执行。

## 5. 持续轮询

发送任务后无需手动刷新。预期前端每 2.5 秒轮询一次：

- 第一次尚无回复时继续轮询。
- 出现 `pending / parsing / running` 任务时继续轮询。
- 出现对应任务卡片或数字员工回复后解除“等待回复”。
- 任务进入 `completed / approval / failed / denied` 后停止轮询。
- 临时网络错误不会直接终止轮询。

如果数据库中已经出现任务、页面却长期只有用户消息，应检查浏览器是否仍加载旧前端资源，
可按 `Ctrl+F5` 强制刷新。

## 6. Harness 降级

以不带 `-Docker` 的模式重启并保留数据：

```powershell
# 在仓库根目录执行
.\scripts\run_demo.ps1 -NoReset
```

使用新姓名再次发送入职任务。预期：

- UI 显示 `运行时：Demo Adapter 降级`。
- 工具仍分别显示 MCP、Workflow、RPA。
- Adapter 仍只调用一次。
- 不应显示泛化的 `Adapter` 执行器标签。

测试后恢复真实 Harness：

```powershell
.\scripts\run_demo.ps1 -Docker -NoReset
```

## 7. 独立 Harness 上下文

分别触发 HR、IT、报表和采购任务，检查返回的 `runtime_context_id`：

```text
VE-0002:<Task ID>
VE-0003:<Task ID>
RPA-0001:<Task ID>
VE-0004:<Task ID>
```

本地目录应按员工隔离：

```text
backend/harness-workspaces/ve-0002/
backend/harness-workspaces/ve-0003/
backend/harness-workspaces/rpa-0001/
backend/harness-workspaces/ve-0004/
```

每个目录包含独立的 `dsh-home` 和 `workspace`。这些运行数据已被 Git 忽略。

采购示例：

```text
请采购助理为研发部申请 5 台测试显示器，预算 10000 元，完成需求澄清、比价和采购申请
```

预期由 VE-0004 执行；超过策略阈值时必须等待审批，不能误分配给 HR 或 IT。

## 8. 长回答完整性

Harness 计划、AgentTeams Manager 汇报、成员反馈、协作结论和 Adapter 结果均不做用户内容截断。
测试时展开任务卡并检查：

- 长回答末尾仍存在，不应在固定 300/500/1000 字处停止。
- Harness 计划默认完整展示，不出现三行省略号或“展开”按钮。
- 已经在旧版本中截断并落库的历史消息无法自动恢复，应创建新任务验证。

审计表中的 `result_summary` 是审计摘要，不是用户回答正文，仍允许保持摘要长度。

## 9. AgentTeams 与降级判断

正常路径预期任务卡显示：

```text
AgentTeams 团队协作 · Harness 驱动 · Policy/Gateway 工具调用
```

若 AgentTeams Matrix 房间、Manager 或 Worker 不可用：

- 尚未发送业务任务时，可以安全降级到内置编排。
- 已发送后通道中断时应失败停机，避免重复执行业务动作。
- 查看 `backend/uvicorn-out.log` 和 `backend/uvicorn-err.log` 定位失败阶段。

Docker 检查：

```powershell
docker ps
docker images dwp-dsh:rc6
```

## 10. 常见问题

### 页面一直等待

先检查后端是否已经写入任务：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/conversations/CONV-0002
```

再检查健康状态和日志。新版前端会持续轮询，不需要手动刷新。

### Harness 显示 Demo Adapter 降级

检查：

- 是否使用 `run_demo.ps1 -Docker` 启动。
- `DEEPSEEK_API_KEY` 是否只配置在本地 `backend/.env`。
- `dwp-dsh:rc6` 镜像是否存在。
- Docker Desktop 是否处于运行状态。

### 同一指令提示重复任务

系统会对短时间内相同请求做幂等保护。使用不同的虚构姓名、等待去重窗口结束，
或在演示前清空该会话。

## 11. 测试通过标准

发布前至少满足：

- 后端、前端、类型检查和生产构建全部通过。
- 正常 Harness、Demo Adapter 降级各验证一次。
- AgentTeams 协作结论能够注入各员工 Harness Prompt。
- MCP、Workflow、RPA 三类工具名称与业务回执正确显示。
- 审批批准和拒绝路径各验证一次。
- 发消息后持续轮询，无需刷新即可看到任务卡片。
- 长回答完整显示，无固定长度截断。
- 日志和仓库中不存在真实 Token、Key 或真实内部数据。
