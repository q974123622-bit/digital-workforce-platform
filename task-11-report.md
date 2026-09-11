# Task 11 — AgentTeams 真实环境预检与烟测脚本

## 范围与提交

- Worktree: `D:\lincwc\Desktop\digital workforce platform-agentteams-collaboration`
- Branch: `codex/agentteams-collaboration`
- Commit: `0fc479e test: verify real agentteams collaboration loop`
- 本报告刻意未暂存、未提交；已有 Task 8/9/10 未跟踪报告未作任何修改。

## RED（严格 TDD）

先创建 `backend/tests/test_agentteams_collaboration_smoke_contract.py`，再在旧路由上执行：

```powershell
D:\lincwc\Desktop\digital workforce platform\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_agentteams_collaboration_smoke_contract.py -q
```

实际 RED 失败输出（关键断言原文）：

```text
F                                                                        [100%]
================================== FAILURES ===================================
______________ test_health_requires_room_manager_and_two_workers ______________

    assert response.status_code == 200
>   assert response.json()["ready_for_demo"] is False
E   assert True is False

backend\tests\test_agentteams_collaboration_smoke_contract.py:28: AssertionError
=========================== short test summary info ===========================
FAILED backend/tests/test_agentteams_collaboration_smoke_contract.py::test_health_requires_room_manager_and_two_workers
1 failed, 3 warnings in 0.35s
```

随后增加创建任务门禁的同类契约，旧实现的完整失败结论为：

```text
FF                                                                       [100%]
E       assert True is False
E       assert 201 == 503
2 failed, 3 warnings in 0.30s
```

这两次 RED 分别证明旧 GET 路由会原样返回伪造的 `ready_for_demo=true`，以及旧 `_require_ready` 会让该伪造状态绕过提交门禁。

## 实现行为

- 健康检查逐项派生并返回：协作开关、Matrix 登录和房间成员资格、Manager MXID 是否仅“已配置”、至少一个平台 Team、以及 Worker 绑定。
- Matrix/房间只有 `AgentTeamsGateway.health()` 的登录加 `joined_rooms` 检查成功时才为 `ready`。
- Manager 和 Worker 数据库绑定均只表述为 `configured`，不再把配置存在误报为远端连接。
- Worker 必须有 runtime ref 所得的可解析 Matrix MXID；演示至少要求两个不同 Worker 和两个不同 Matrix ID。
- `GET /health` 与 `_require_ready` 都执行同一归一化逻辑，所以 monkeypatch/raw 调用方单独写入 `ready_for_demo=true` 无法绕过门禁。
- Task 提交依旧由 `AgentTeamsCollaborationService.create_task()` 校验实际 `team_id` 和 leader/member 关系。

## 公共接口裁定（记录的偏差）

计划原文以“selected platform team exists”表述，但 `/health` 请求没有 UI 选定的 team。故公开健康响应新增：

```text
platform_team: "ready" | "missing"
```

它仅表示数据库至少存在一个 `Team`，不是当前待提交任务的 team 校验，也不暴露名称、凭据或 Matrix token。后端 `AgentCollaborationHealthOut` 与 `shared-schema/types.ts` 已同步此字段。提交任务的实际 team 验证保持在 `create_task`，未被健康检查替代。

## 真实烟测脚本

新增 `scripts/agentteams_collaboration_smoke.py`，只使用 `httpx` 访问已运行的后端 API：

- `DWP_BASE_URL` 默认 `http://127.0.0.1:8000/api/v1`。
- 先 GET health；未就绪时只输出安全的任务/参与者/Matrix ID/summary/缺项 JSON，并以退出码 2 结束。
- 就绪后 POST 固定的新员工入职复核任务，最多轮询 600 秒至终态。
- 再 GET detail、events、messages、result；证据要求 `completed`、非空 summary、至少两个子节点、至少两个不同的子任务 Worker sender 且每个有 Matrix event ID、`task.progress`，以及 `agent.message` 或深度至少 2 的节点。
- 输出不包含请求 payload、健康检查原文、消息正文、token 或凭据；只包含 task ID、participant IDs、Matrix event IDs、summary 和 missing criteria。

脚本帮助验证实际输出：

```text
usage: agentteams_collaboration_smoke.py [-h]

Run a configured AgentTeams collaboration smoke check without starting
services.

options:
  -h, --help  show this help message and exit
```

## GREEN 与静态验证

最终聚焦测试命令：

```powershell
D:\lincwc\Desktop\digital workforce platform\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_agentteams_collaboration_smoke_contract.py backend/tests/test_agentteams_collaboration_api.py -q -p no:cacheprovider
```

实际 GREEN 输出：

```text
..........                                                               [100%]
============================== warnings summary ===============================
..\digital workforce platform\backend\.venv\Lib\site-packages\fastapi\testclient.py:1
  StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.

10 passed, 1 warning in 0.41s
```

- `python -m py_compile backend/app/routers/agentteams_collaboration.py scripts/agentteams_collaboration_smoke.py`: exit 0, 无输出。
- `python scripts/agentteams_collaboration_smoke.py --help`: exit 0（输出见上）。
- `git diff --check` 与已暂存差异检查：exit 0；仅有 Git 的 LF→CRLF 提示，无空白错误。

## 真实环境边界

**真实 Matrix/AgentTeams smoke 未运行，也未被声称为已运行。** 本次没有启动 backend、没有启动 Matrix、没有发起远端 Matrix 调用；只验证了脚本的语法和帮助路径。真实烟测只有在操作者明确准备好真实环境后，手动运行脚本并以退出码 0 和打印出的 Matrix 证据为准。

---

## Fix round 1 — 审查问题加固

### RED 证据

先添加纯 smoke evidence、空白 MXID、客户端关闭与安全 JSON 测试，再执行：

```powershell
D:\lincwc\Desktop\digital workforce platform\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_agentteams_collaboration_smoke_contract.py backend/tests/test_agentteams_collaboration_smoke_evidence.py -q -p no:cacheprovider
```

实际 RED 结果：`7 failed, 2 passed, 1 warning in 2.29s`。失败逐项对应审查结论：

- 两条 `direction="outbound"`、`delivery_status="sent"`、带 Matrix event ID 的逻辑 Worker 记录仍使旧 `build_evidence()` 返回 `ok=True`。
- 只有 DB/result 的 `summary`、没有 Manager 入站 `task.summary` 时，旧 `build_evidence()` 仍返回 `ok=True`。
- 有真实 Manager summary 的正向 fixture 里，旧证据输出遗漏 Manager participant。
- 两个内部包含空白字符的 runtime ref（`worker one`、`worker two`）在模拟 Matrix/room/Manager/Team 已就绪时，旧 health 错误返回 `ready_for_demo=True`。
- room health 抛出异常后旧 gateway 未调用 `close()`。
- 200 但非 JSON 的 health 与 result 都在旧 `main()` 中向外抛 `ValueError`，包含未过滤的错误内容。

另补充服务实际落库形状的 RED：Manager `task.summary` 的空 body、嵌套 `payload={"payload":{"summary":"..."}}` 在旧判断下失败，输出为 `1 failed, 1 warning in 0.16s`。

### 修复行为

- Worker 证据只接受 `direction == "inbound"`、`delivery_status == "received"`、非空 `matrix_room_id` 与非空 `matrix_event_id` 的房间消息；发送记录无论逻辑 sender 或 event ID 均不能算作 Worker 回执。
- 任务完成除了非空 `result.summary`，还必须有 root `assignee_agent_id`（Manager）发送的、入站已接收且带房间/event ID 的 `task.summary`。该消息需要非空 body、平铺 `payload.summary` 或实际协议落库的 `payload.payload.summary`。
- 证据输出会把这条 Manager summary 的 Matrix event ID 和 Manager ID 一并加入 participants；仅有 Worker 或仅有 DB summary 时必然失败。
- 严格 MXID 解析抽为 `matrix_user_id_for_runtime()`，服务原有静态入口委托该函数，health 直接复用它，不再维护较宽松的解析器。
- `inspect_environment()` 对实际创建的 `AgentTeamsGateway` 使用 `finally: close()`；异常路径同样关闭客户端且仍按原语义报告 unavailable。
- `backend/tests/conftest.py` 在导入 app/TestClient 前强制 `DWP_AGENTTEAMS_COLLAB_ENABLED="0"`，所以所有 pytest API/contract/evidence 测试隔离于本地 shell 与 backend/.env 的真实开关。Matrix smoke 仍只能由显式脚本触发。
- `main()` 将预检 GET 的 HTTP、JSON 和形状错误固定为安全 JSON + exit 2；预检通过后的 task/detail/events/messages/result 错误固定为安全 JSON + exit 1。错误正文、响应内容与凭据都不输出。

### GREEN、静态验证与自审

最终聚焦回归：

```powershell
D:\lincwc\Desktop\digital workforce platform\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_agentteams_collaboration.py backend/tests/test_agentteams_collaboration_api.py backend/tests/test_agentteams_collaboration_smoke_contract.py backend/tests/test_agentteams_collaboration_smoke_evidence.py -q -p no:cacheprovider
```

实际输出：`37 passed, 1 warning in 2.16s`；唯一 warning 是依赖 TestClient 对 httpx 的弃用提示。

- `python -m py_compile backend/app/services/agentteams_collaboration.py backend/app/routers/agentteams_collaboration.py scripts/agentteams_collaboration_smoke.py`: exit 0，无输出；未启动服务、未发起网络。
- `git diff --check`: exit 0；仅有 Git LF/CRLF 提示，无空白错误。
- 自审：outbound 记录不能满足 Worker 或 Manager 证据；Manager 身份来自 root assignee；所有参与者都各有入站 Matrix event ID；health 继续仅报告 Manager `configured`；`platform_team` 继续只是系统存在性检查，提交实际 team 仍由 `create_task` 验证。

本轮提交：`d38e738 fix: harden agentteams smoke evidence`（仅服务、router、conftest、contract/evidence 测试和 smoke 脚本；本报告不提交）。真实 Matrix smoke 仍未运行、也未被声称为已运行。

### 全局 conftest 影响审计（补充）

`DWP_AGENTTEAMS_COLLAB_ENABLED="0"` 只在 `app.main` 决定是否安装真实 collaboration runner；它不控制 legacy `group_chat` 的 mocked AgentTeams 分支。首次尝试全量 `backend/tests` 时，该 legacy 测试在第 13 项失败：`test_group_chat_agentteams_path` 的 `run.source` 为 `builtin`，原因是测试本身未提供 `AGENTTEAMS_ROOM_ID`，而 `group_chat._try_agentteams_task()` 遇到空 room 直接回退。它不是新开关引起的逻辑分支变化，而是此前依赖开发机 `.env`/shell room 的测试 fixture 缺口。

已只在该测试加入 `monkeypatch.setenv("AGENTTEAMS_ROOM_ID", "!room:test")`，并保留既有 `FakeGatewayOK`；目标复现命令随后为 `1 passed, 1 warning in 0.09s`，不会触发网络。最终关键集扩大为：

```powershell
D:\lincwc\Desktop\digital workforce platform\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_agentteams.py backend/tests/test_agentteams_collaboration.py backend/tests/test_agentteams_collaboration_api.py backend/tests/test_agentteams_collaboration_smoke_contract.py backend/tests/test_agentteams_collaboration_smoke_evidence.py -q -p no:cacheprovider
```

实际结果：`47 passed, 1 warning in 15.70s`（唯一 warning 仍为 TestClient/httpx 弃用提示）。两次完整 `pytest backend/tests -q -p no:cacheprovider` 尝试都在本执行器约 60 秒的输出窗口后仅返回到 56% 进度、没有最终退出汇总，且没有遗留 Python 进程；因此本报告**不把完整 backend suite 写为通过**。这不是一个未定位的断言失败：唯一已得到失败栈的 legacy fixture 问题已被上述无网络定向测试和 47 项关键集覆盖。

---

## Fix round 2 — Manager 不能充当第二个 Worker

### RED 证据

新增 `test_build_evidence_rejects_manager_self_assignment_as_a_second_worker`：root Manager `DT-E10281` 自己领取一个 child，另一个 child 只由 `VE-0002`（唯一真实 Worker）领取；两者都有合法的入站、已接收、带 room/event ID 的 Matrix 消息，同时包括有效进度、Manager 的入站 `task.summary` 和 completed result。

在 `d38e738` 的旧脚本上运行：

```powershell
D:\lincwc\Desktop\digital workforce platform\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_agentteams_collaboration_smoke_evidence.py::test_build_evidence_rejects_manager_self_assignment_as_a_second_worker -q -p no:cacheprovider
```

实际结果：`1 failed, 1 warning in 0.20s`，核心断言为 `assert True is False`。这证明旧逻辑把 child 上的 Manager sender 错算成第二个 Worker。

### 修复与 GREEN 证据

`build_evidence()` 现在先计算 `worker_assignees = child_assignees - {manager_id}`，仅在该集合中统计入站已接收的 Worker sender，并仍要求其中至少两个不同 ID。Manager 仅因有效 root `task.summary` 被加入 participant 与 Matrix event evidence，绝不参与 Worker 数量判断。

- 新回归单测：`1 passed, 1 warning in 0.02s`。
- Task 11 聚焦集：

```powershell
D:\lincwc\Desktop\digital workforce platform\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_agentteams.py backend/tests/test_agentteams_collaboration.py backend/tests/test_agentteams_collaboration_api.py backend/tests/test_agentteams_collaboration_smoke_contract.py backend/tests/test_agentteams_collaboration_smoke_evidence.py -q -p no:cacheprovider
```

实际结果：`48 passed, 1 warning in 15.22s`；唯一 warning 仍是 TestClient/httpx 弃用提示。未发生真实网络访问。

- `python -m py_compile scripts/agentteams_collaboration_smoke.py`: exit 0，无输出。
- `git diff --check`: exit 0；只有 Git 的 LF/CRLF 提示，无空白错误。

本轮提交：`ac7a638 fix: require distinct agentteams workers`（仅 `scripts/agentteams_collaboration_smoke.py` 与 `backend/tests/test_agentteams_collaboration_smoke_evidence.py`）。真实 Matrix smoke 未运行、也未被声称为已运行。
