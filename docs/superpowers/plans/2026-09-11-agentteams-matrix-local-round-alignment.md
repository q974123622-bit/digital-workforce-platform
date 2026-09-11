# AgentTeams Matrix 与本地多轮协作对齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 TEAM-ONBOARD 岗位调整场景在本地和真实 Matrix 下具有一致的两轮 Manager 协作语义与可展开前端过程。

**Architecture:** 提取共享岗位调整场景契约，保留 Local 与 Matrix 两种 Transport。LocalDemoTransport 继续通过协议驱动同一个 CollaborationService，但补齐 Manager 复核、第二轮依赖派发和安全 decision.trace；Matrix Runtime 只复用契约常量，不改变传输及身份安全边界。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy、Pydantic、React、TypeScript、Vitest、pytest、Matrix/Synapse

**Spec:** `docs/superpowers/specs/2026-09-11-agentteams-matrix-local-round-alignment-design.md`

## Global Constraints

- 不运行或修改既有数据库评测结果，`TEAM-DB-BENCH` 行为保持不变。
- Matrix 未就绪时禁止自动回退到 Local Transport。
- 决策依据只能来自持久化 `reasoning_segments`，不得生成或展示私有思维链。
- Tool 必须经过现有三层权限计算、Gateway 和 Audit。
- 保护性基线提交为 `61849f7`。

---

### Task 1: 共享岗位调整场景契约

**Files:**
- Create: `backend/app/services/agentteams_role_change_scenario.py`
- Modify: `backend/app/services/agentteams_local_scenario.py`
- Modify: `backend/app/services/agentteams_decisions.py`
- Test: `backend/tests/test_agentteams_role_change_demo.py`

**Interfaces:**
- Produces: `ROLE_CHANGE_SKILL_ID: str`、`ROLE_CHANGE_TITLE: str`、`ROLE_CHANGE_INSTRUCTION: str`、`ROLE_CHANGE_FIRST_ROUND_CAPABILITIES: frozenset[str]`、`ROLE_CHANGE_SECOND_ROUND_CAPABILITIES: frozenset[str]`、`ROLE_CHANGE_APPROVAL_REF: str`。
- Consumes: 现有 `ManagerContext`、`ManagerDecision` 和固定 Demo fixture。

- [ ] **Step 1: 写失败测试**

```python
def test_local_and_matrix_role_change_rules_share_one_contract():
    assert LOCAL_DEMO_TITLE == ROLE_CHANGE_TITLE
    assert LOCAL_DEMO_INSTRUCTION == ROLE_CHANGE_INSTRUCTION
    assert ROLE_CHANGE_FIRST_ROUND_CAPABILITIES == {
        "hr.role_change.read", "it.permission.plan",
    }
    assert ROLE_CHANGE_SECOND_ROUND_CAPABILITIES == {
        "audit.permission.review", "it.permission.activate",
    }
```

- [ ] **Step 2: 运行该测试，确认因共享模块不存在而失败。**

Run: `backend\\.venv\\Scripts\\python.exe -m pytest backend/tests/test_agentteams_role_change_demo.py::test_local_and_matrix_role_change_rules_share_one_contract -q -p no:cacheprovider`

- [ ] **Step 3: 创建共享常量，并让 local scenario 和 Manager 规则引用这些常量。**

```python
ROLE_CHANGE_SKILL_ID = "agentteams-role-change-readiness"
ROLE_CHANGE_FIRST_ROUND_CAPABILITIES = frozenset({"hr.role_change.read", "it.permission.plan"})
ROLE_CHANGE_SECOND_ROUND_CAPABILITIES = frozenset({"audit.permission.review", "it.permission.activate"})
```

- [ ] **Step 4: 运行岗位调整与 Manager 决策测试。**

Run: `backend\\.venv\\Scripts\\python.exe -m pytest backend/tests/test_agentteams_role_change_demo.py backend/tests/test_agentteams_decisions.py -q -p no:cacheprovider`

### Task 2: 本地协议实现真实 Manager 第二轮

**Files:**
- Modify: `backend/app/services/agentteams_local_demo.py`
- Modify: `backend/tests/test_agentteams_local_demo.py`
- Modify: `backend/tests/test_agentteams_local_demo_e2e.py`

**Interfaces:**
- Consumes: Task 1 的岗位调整场景契约、现有 `AgentProtocolMessage`。
- Produces: `round=1` 与 `round=2` 的 `task.delegate`、可展示 `decision.trace`、四个经 Gateway 执行的岗位调整 Tool 请求。

- [ ] **Step 1: 写失败测试，证明第一轮汇总请求目前错误地直接结束。**

```python
def test_root_summary_after_round_one_delegates_a_second_manager_round():
    request = _message("task.summary", payload={"request": "task.summary", "current_round": 1})
    messages = _messages_after_send(LocalDemoTransport(), request)
    assert messages[-1].type == "task.delegate"
    assert messages[-1].payload["round"] == 2
    assert {item["capability"] for item in messages[-1].payload["subtasks"]} == ROLE_CHANGE_SECOND_ROUND_CAPABILITIES
```

- [ ] **Step 2: 运行协议测试，确认失败原因是旧固定总结。**

Run: `backend\\.venv\\Scripts\\python.exe -m pytest backend/tests/test_agentteams_local_demo.py -q -p no:cacheprovider`

- [ ] **Step 3: 实现两轮响应和 task-id 到 client-task-key 的本地状态映射。**

```python
self._task_keys: dict[str, str] = {}

if message.type == "task.summary" and current_round == 1:
    return [decision_trace(...), second_round_delegate(...)]
```

- [ ] **Step 4: 写失败 E2E 测试，断言 `current_round=2`、激活依赖审计、四个 Tool 均有审计记录。**

```python
assert graph["current_round"] == 2
assert activation_dependency.depends_on_task_id == audit.id
assert {row.plugin_id for row in audits} == EXPECTED_ROLE_CHANGE_PLUGIN_IDS
```

- [ ] **Step 5: 实现 Worker Tool 请求、证据回传、批准编号传递和最终结构化总结。**

- [ ] **Step 6: 运行本地协议、E2E 和权限测试。**

Run: `backend\\.venv\\Scripts\\python.exe -m pytest backend/tests/test_agentteams_local_demo.py backend/tests/test_agentteams_local_demo_e2e.py backend/tests/test_plugin_permission_layers.py -q -p no:cacheprovider`

### Task 3: 前端区分 Manager 第二轮与 Worker 嵌套委派

**Files:**
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.tsx`
- Modify: `frontend/src/pages/agentteams/NodeProcessDetails.tsx`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

**Interfaces:**
- Consumes: `task.delegate.payload.round`、`reasoning_segments` 和节点父子关系。
- Produces: Manager 的“第 N 轮”与 Worker 的“嵌套委派”两种独立展示。

- [ ] **Step 1: 写失败测试，提交本地任务时使用岗位调整标题和目标。**

```typescript
expect(api.createAgentTeamsTask).toHaveBeenCalledWith('E10281', expect.objectContaining({
  title: '岗位调整评估',
  instruction: '评估张明的岗位调整是否可以按期完成。',
}));
```

- [ ] **Step 2: 写失败测试，Worker 的 `task.delegate` 显示“嵌套委派”。**

```typescript
expect(screen.getByText(/嵌套委派/)).toBeInTheDocument();
```

- [ ] **Step 3: 运行页面测试，确认两个断言因现有行为缺失而失败。**

Run: `npm test -- --run src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

- [ ] **Step 4: 最小修改固定场景常量以及 `workerActions` 的 `task.delegate` 分支。**

- [ ] **Step 5: 运行页面测试和前端构建。**

Run: `npm test -- --run src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

Run: `npm run build`

### Task 4: 回归与真实 Matrix 验收

**Files:**
- Modify only if a failing acceptance test identifies a concrete defect.
- Verify: `scripts/start_agentteams_real_demo.ps1`
- Verify: `scripts/agentteams_collaboration_smoke.py`

**Interfaces:**
- Consumes: 现有严格 Matrix 启动、四身份 Runtime 和协作健康接口。
- Produces: 一次带真实 Matrix Event ID 的两轮岗位调整完成任务。

- [ ] **Step 1: 运行相关后端回归。**

Run: `backend\\.venv\\Scripts\\python.exe -m pytest backend/tests/test_agentteams_collaboration.py backend/tests/test_agentteams_agent_runtime.py backend/tests/test_agentteams_role_change_demo.py backend/tests/test_plugin_permission_layers.py -q -p no:cacheprovider`

- [ ] **Step 2: 启动真实 Matrix 演示并核对 `mode=agentteams_strict`、`transport=matrix`、`strict_ready=true`。**

Run: `powershell -ExecutionPolicy Bypass -File scripts/start_agentteams_real_demo.ps1`

- [ ] **Step 3: 提交岗位调整任务，等待根任务 `completed` 与 `current_round=2`。**

- [ ] **Step 4: 核对事件 ID 以 `$` 开头、四个 Tool 均有 Audit、没有本地 synthetic event ID、没有启动数据库评测。**

- [ ] **Step 5: 创建实现提交。**

```bash
git add backend frontend docs
git commit -m "feat(agentteams): align local and Matrix manager rounds"
```
