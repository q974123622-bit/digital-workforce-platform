# API 契约 v1.1（冻结）

> 版本：v1.1（2026-08-17，Sprint 1.5 Architecture Freeze）
> 契约单一来源：后端 Pydantic Schema → 导出 OpenAPI → `shared-schema/` 前端类型。本文件为人类可读版。
> 状态标注：✅ = Sprint 1 已实现；📋 = 已冻结、后续 Sprint 实现。
> 变更规则：任何修改需 A 批准，并同步 OpenAPI、前端类型与本文件三处；变更登记在文末。

## 1. 通用约定

- Base Path：`/api/v1`；请求/响应均为 JSON（`POST /employees/{id}/chat` 除外，见流式约定）。
- 演示鉴权：请求头 `X-Demo-Actor: <human_employee_no>`（如 `E10281`）；后端据此确定 subject。无真实 IAM。
- Trace：服务端生成 `trace_id`，响应头 `X-Trace-Id`；所有审计事件按 trace_id 聚合。
- 流式：聊天为 `text/event-stream`，事件格式 `data: {"type":"message_chunk|tool_card|policy_denied|done|error", "content": ...}`；实现不了流式时允许整段 JSON 返回（P1 弹性项）。
- 错误统一形状：`{"error": {"code": "<CODE>", "message": "...", "detail": {...}}}`
- 空值约定：可选字段缺省返回 `null`，不返回 `undefined`。

### 错误码

| Code | HTTP | 说明 |
|---|---|---|
| VALIDATION_ERROR | 400 | 参数错误 |
| POLICY_DENIED | 403 | 策略拒绝，`detail.policy_id` + `detail.reason` 必填 |
| NOT_FOUND | 404 | 资源不存在 |
| STATE_CONFLICT | 409 | 状态不允许该操作（如任务非 approval 态却审批） |
| LLM_UNAVAILABLE | 503 | DeepSeek 不可用/Key 缺失（SAFEMODE 拒发也走此码） |
| RUNTIME_UNAVAILABLE | 503 | Runtime/Sandbox 不可用 |

## 2. 统一资源访问链（强制约束）

**业务模块不得直接访问知识库、数据库、Workflow 或 RPA。**

```text
Employee Identity
  → Policy Engine（唯一授权源，默认拒绝）
  → Plugin Gateway（唯一执行入口）
  → Adapter（Runtime / Knowledge / Workflow / RPA）
  → Enterprise Resource
```

- 前端只允许调用 §3 的公开 API；§6 内部接口只允许服务间调用，前端不得直连。
- 任何绕过 Gateway 的资源访问视为架构违规，Code Review 一票否决。

## 3. 前端 ↔ 门户后端公开 API

### 3.1 Employee API（✅ 骨架已实现；📋 授权/安全/聊天接口待实现）

| 方法 | 路径 | 状态 | 说明 | 关键请求 | 关键响应 |
|---|---|---|---|---|---|
| GET | `/employees` | ✅ | 员工列表 | query: `type`(twin/virtual/rpa), `department` | `EmployeeDto[]` |
| GET | `/employees/{employee_no}` | ✅ | 员工详情 | — | `EmployeeDto` |
| POST | `/employees` | ✅ | 创建数字员工 | `EmployeeCreateDto` | 201 `EmployeeDto` |
| PUT | `/employees/{employee_no}` | ✅ | 更新数字员工 | `EmployeeUpdateDto` | `EmployeeDto` |
| DELETE | `/employees/{employee_no}` | ✅ | 删除数字员工 | — | 204 |
| PUT | `/employees/{employee_no}/plugins` | 📋 | 插件授权 | `{grants: [{plugin_id, action, decision_mode}]}` | 200 `{ok: true}` |
| PUT | `/employees/{employee_no}/security` | 📋 | 安全配置 | `{location, internet, max_data_level, allowed_domains}` | 200 `{ok: true}` |
| POST | `/employees/{employee_no}/chat` | 📋 | 单聊（SSE） | `{message, session_id?}` | SSE 事件流 |

### 3.2 Policy API（✅ 骨架已实现；评估接口走内部 API）

| 方法 | 路径 | 状态 | 说明 | 关键请求 | 关键响应 |
|---|---|---|---|---|---|
| GET | `/policies` | ✅ | 策略列表（只读） | query: `effect`, `enabled` | `PolicyDto[]` |
| GET | `/policies/{policy_id}` | ✅ | 策略详情 | — | `PolicyDto` |
| POST | `/policies` | ✅ | 创建策略 | `PolicyCreateDto` | 201 `PolicyDto` |
| PUT | `/policies/{policy_id}` | ✅ | 更新策略 | `PolicyUpdateDto` | `PolicyDto` |
| DELETE | `/policies/{policy_id}` | ✅ | 删除策略 | — | 204 |
| POST | `/internal/policy/evaluate` | 📋 | 策略评估（内部） | 见 §6.1 | `{decision, policy_id?, reason}` |

### 3.3 Plugin API（✅ 骨架已实现；调用走 Gateway）

| 方法 | 路径 | 状态 | 说明 | 关键请求 | 关键响应 |
|---|---|---|---|---|---|
| GET | `/plugins` | ✅ | 插件列表 | query: `type`, `status` | `PluginDto[]` |
| GET | `/plugins/{plugin_id}` | ✅ | 插件详情 | — | `PluginDto` |
| POST | `/plugins` | ✅ | 登记插件 | `PluginCreateDto` | 201 `PluginDto` |
| PUT | `/plugins/{plugin_id}` | ✅ | 更新插件 | `PluginUpdateDto` | `PluginDto` |
| DELETE | `/plugins/{plugin_id}` | ✅ | 删除插件 | — | 204 |
| POST | `/plugins/{plugin_id}/test` | 📋 | 插件测试调用（经 Gateway） | `{params, actor_no}` | 200 `{ok, result, decision}` |
| POST | `/internal/gateway/invoke` | 📋 | 插件执行（内部） | 见 §6.2 | `{ok, data, decision, audit_ids[]}` |

### 3.4 Audit API（✅ 骨架已实现；Trace 时间线待实现）

| 方法 | 路径 | 状态 | 说明 | 关键请求 | 关键响应 |
|---|---|---|---|---|---|
| GET | `/audit` | ✅ | 审计日志 | query: `trace_id`, `employee_id`, `decision` | `AuditEventDto[]` |
| GET | `/audit/{event_id}` | ✅ | 单条事件 | — | `AuditEventDto` |
| POST | `/audit` | ✅ | 写入事件（供服务内部使用） | `AuditCreateDto` | 201 `AuditEventDto` |
| DELETE | `/audit/{event_id}` | ✅ | 删除事件（演示清理用） | — | 204 |
| GET | `/traces/{trace_id}` | 📋 | Trace 时间线（聚合） | — | `TraceTimelineDto` |

### 3.5 Chat API（📋 冻结，Sprint 2 实现）

| 方法 | 路径 | 说明 | 关键请求 | 关键响应 |
|---|---|---|---|---|
| POST | `/employees/{employee_no}/chat` | 单聊（SSE） | `{message, session_id?}` | SSE 事件流 |
| GET | `/chat/sessions/{session_id}/messages` | 会话历史 | — | `ChatMessageDto[]` |

SSE 事件类型（固定枚举）：

```json
{"type": "message_chunk", "content": "文本增量"}
{"type": "tool_card", "content": {"plugin_id": "...", "name": "...", "decision": "allow|deny|approval"}}
{"type": "policy_denied", "content": {"policy_id": "...", "reason": "...", "plugin_id": "..."}}
{"type": "done", "content": null}
{"type": "error", "content": {"code": "...", "message": "..."}}
```

### 3.6 Team API（📋 冻结，Sprint 3 实现）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/teams` | 团队列表（✅ 骨架已实现） |
| GET | `/teams/{team_id}` | 团队详情（✅ 骨架已实现） |
| POST | `/teams/{team_id}/tasks` | 发起任务 `{request}` → 201 `{task_id, trace_id}` |
| GET | `/teams/{team_id}/tasks/{task_id}` | 任务详情（轮询） |
| POST | `/tasks/{task_id}/approve` | 审批 `{approve: bool, actor_no}` |

### 3.7 Knowledge API（✅ 骨架已实现，只读；查询走 Adapter）

| 方法 | 路径 | 状态 | 说明 |
|---|---|---|---|
| GET | `/knowledge-bases` | ✅ | 知识库登记列表 |
| GET | `/knowledge-bases/{kb_id}` | ✅ | 知识库登记详情 |
| POST | `/internal/gateway/invoke` | 📋 | 知识查询（经 Knowledge Adapter） |

## 4. Runtime Adapter Interface（📋 冻结，Sprint 3 实现）

统一 Runtime 调用形态，由 RuntimeLauncher 调用；**不区分 harness/openclaw/agentteams 的差异，由 adapter 内部翻译**。

```text
RuntimeAdapter.run(subject, task, context) -> RuntimeResult
```

### 请求（内部，`POST /internal/runtime/run`）

```json
{
  "employee_id": "VE-0001",
  "task": {"kind": "chat|subtask", "prompt": "新员工第一天要做什么", "plugin_ids": ["knowledge-l1"]},
  "trace_id": "T-20260817-001",
  "context": {"location": "remote", "internet": "deny", "max_data_level": "L2"}
}
```

### 响应

```json
{
  "ok": true,
  "mode": "harness|demo|openclaw-stub|agentteams-stub",
  "result": "文本结果",
  "events": [{"ts": "...", "kind": "plugin_call|plugin_result|deny", "plugin_id": "...", "decision": "allow|deny|approval"}]
}
```

约束：
- Adapter 不持有权限逻辑；调用前 Policy 已评估（或 RuntimeLauncher 已调 Policy）。
- `mode=demo` 时 UI 必须标注「Adapter 演示模式」。
- 失败返回 `RUNTIME_UNAVAILABLE`（503），不吞错误。

## 5. Knowledge Adapter Interface（📋 冻结，Sprint 2 实现）

知识查询统一经 Plugin Gateway → Knowledge Adapter；前端与 Chat 层不得直读 `mock-data/kb/` 文件。

### 请求（Gateway 内部调用）

```json
{
  "plugin_id": "knowledge-l1",
  "action": "search",
  "params": {"query": "新员工入职流程", "level": "L2", "domain": "HR"},
  "trace_id": "T-20260817-001"
}
```

### 响应

```json
{
  "ok": true,
  "data": {"hits": [{"kb_id": "KB-L2-HR", "level": "L2", "title": "入职流程", "snippet": "..."}]},
  "decision": "allow",
  "audit_ids": [1]
}
```

约束：
- 仅返回已授权数据等级（`data.level ≤ subject.max_data_level` 且 grant 允许）；越级查询由 Policy Engine 拒绝。
- 内容全部来自 `mock-data/kb/`（虚构），不得引入真实文档。

## 6. 内部接口（服务间，不暴露前端）

### 6.1 Policy Evaluate（📋）

`POST /internal/policy/evaluate`

```json
{
  "subject": {"type": "twin|virtual|rpa", "id": "DT-E10281", "employee_no": "DT-E10281", "employment_type": "formal|intern"},
  "resource": {"type": "plugin|knowledge|workflow|rpa|data", "id": "knowledge-l2", "data_level": "L2"},
  "action": "read",
  "context": {"location": "remote", "internet": "deny", "team_id": null, "task_id": null}
}
```

响应：`{"decision": "allow|deny|approval", "policy_id": "P-DATA-003", "reason": "..."}`

### 6.2 Gateway Invoke（📋）

`POST /internal/gateway/invoke`

```json
{"employee_id": "DT-E10281", "plugin_id": "knowledge-l2", "action": "read", "params": {}, "trace_id": "T-..."}
```

响应：`{"ok": true, "data": {...}, "decision": "allow", "audit_ids": [1]}`

### 6.3 Runtime / Sandbox（📋）

`POST /internal/runtime/run`、`POST /internal/sandbox/run`

Sandbox 请求：`{"employee_id", "task_id", "command", "mount_dir", "network"}` → `{"mode": "docker|local", "status", "logs"}`

## 7. 核心 DTO 字段

### EmployeeDto（✅ 与实现一致，平铺结构）

```json
{
  "id": "DT-E10281",
  "employee_no": "DT-E10281",
  "name": "张三的分身",
  "type": "twin | virtual | rpa",
  "source_human_no": "E10281",
  "owner_human_no": "E10281",
  "department": "架构部",
  "role_prompt": "...",
  "status": "active",
  "runtime_type": "demo | harness | openclaw | agentteams",
  "runtime_ref": "dsh-profile-headless",
  "location": "remote | local",
  "internet": "allow | deny",
  "max_data_level": "L1 | L2 | L3",
  "allowed_domains": ["HR_L1", "HR_L2_DEMO"],
  "grants": [{"plugin_id": "knowledge-l2", "name": "内部知识库", "type": "knowledge", "action": "read", "decision_mode": "allow", "data_level": "L2"}]
}
```

> v1.1 变更：EmployeeDto 由 v1.0 的嵌套 `runtime/sandbox/data_scope` 改为与实现一致的平铺结构，避免双轨。

### PluginDto / PolicyDto / AuditEventDto

```json
{"id": "knowledge-l2", "name": "内部流程知识库", "type": "knowledge", "endpoint_ref": "mock://kb/l2", "data_level": "L2", "status": "active", "description": "..."}
```

```json
{"id": "P-DATA-003", "name": "敏感数据禁止", "effect": "deny", "description": "resource.data_level == L3 -> Deny", "enabled": true, "priority": 100}
```

```json
{"id": 1, "trace_id": "T-...", "ts": "...", "actor": "DT-E10281", "employee_id": "DT-E10281",
 "team_id": null, "plugin_id": "knowledge-l2", "action": "read", "decision": "allow",
 "reason": null, "result_summary": "..."}
```

### TaskRunDto（📋）

```json
{
  "id": "T-20260817-001", "team_id": "TEAM-ONBOARD", "trace_id": "T-20260817-001",
  "request": "帮王小明完成入职准备",
  "status": "parsing|running|approval|completed|denied|failed",
  "subtasks": [{"worker_id": "VE-0002", "worker_no": "VE-0002", "summary": "确认入职制度", "plugin_ids": ["hr-employee-mcp"], "status": "completed", "result": "...", "approval": null}],
  "summary": "Leader 汇总文本",
  "created_at": "2026-08-17T10:00:00Z"
}
```

### ChatMessageDto（📋）

```json
{"id": 1, "session_id": "S-...", "employee_id": "DT-E10281", "trace_id": "T-...", "role": "user|assistant|tool", "content": "...", "tool_cards": [{"plugin_id": "...", "name": "...", "decision": "allow"}], "created_at": "..."}
```

## 8. 契约变更登记

| 日期 | 变更 | 批准人 | 涉及文件 |
|---|---|---|---|
| 2026-08-17 | v1.0 初始冻结 | A | 本文件 / OpenAPI / 前端 types |
| 2026-08-17 | v1.1 Sprint 1.5 冻结：EmployeeDto 平铺对齐实现；补 Chat / Runtime Adapter / Knowledge Adapter 三组接口；新增统一资源访问链约束 | A | 本文件 / shared-schema / 后续实现 |
