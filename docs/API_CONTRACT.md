# API 契约 v1.0（冻结）

> 版本：v1.0（2026-08-17，Day 1 冻结）
> 契约单一来源：后端 Pydantic Schema → 导出 OpenAPI → `shared-schema/` 前端类型。本文件为人类可读版。
> 变更规则：任何修改需 A 批准，并同步 OpenAPI、前端类型与本文件三处；变更登记在文末。

## 1. 通用约定

- Base Path：`/api/v1`；请求/响应均为 JSON（`POST /employees/{id}/chat` 除外，见流式约定）。
- 演示鉴权：请求头 `X-Demo-Actor: <human_employee_no>`（如 `E10281`）；后端据此确定 subject。无真实 IAM。
- Trace：服务端生成 `trace_id`，响应头 `X-Trace-Id`；所有审计事件按 trace_id 聚合。
- 流式：聊天为 `text/event-stream`，事件格式 `data: {"type":"message_chunk|tool_card|policy_denied|done|error", "content": ...}`；实现不了流式时允许整段 JSON 返回（P1 弹性项）。
- 错误统一形状：`{"error": {"code": "<CODE>", "message": "...", "detail": {...}}}`

### 错误码

| Code | HTTP | 说明 |
|---|---|---|
| VALIDATION_ERROR | 400 | 参数错误 |
| POLICY_DENIED | 403 | 策略拒绝，`detail.policy_id` + `detail.reason` 必填 |
| NOT_FOUND | 404 | 资源不存在 |
| STATE_CONFLICT | 409 | 状态不允许该操作（如任务非 approval 态却审批） |
| LLM_UNAVAILABLE | 503 | DeepSeek 不可用/Key 缺失（SAFEMODE 拒发也走此码） |
| RUNTIME_UNAVAILABLE | 503 | Runtime/Sandbox 不可用 |

## 2. 前端 ↔ 门户后端（P0）

| 方法 | 路径 | 说明 | 关键请求 | 关键响应 |
|---|---|---|---|---|
| GET | /employees | 员工列表 | query: `type`(twin/virtual/rpa), `department`, `status` | `EmployeeDto[]` |
| GET | /employees/{id} | 员工详情 | — | `EmployeeDto`（含 runtime、sandbox、grants） |
| POST | /employees | 创建 VE/分身 | `{name, type, owner_human_no, department, role_prompt, runtime_type, sandbox_policy_id}` | 201 `EmployeeDto` |
| PUT | /employees/{id}/plugins | 插件授权 | `{grants: [{plugin_id, action, decision_mode}]}` | 200 `{ok: true}` |
| PUT | /employees/{id}/security | 安全配置 | `{sandbox_policy_id, data_scope, internet}` | 200 `{ok: true}` |
| POST | /employees/{id}/chat | 单聊（SSE） | `{message, session_id?}` | SSE 事件流 |
| GET | /chat/sessions/{session_id}/messages | 会话历史 | — | `ChatMessageDto[]` |
| GET | /teams | 团队列表 | — | `TeamDto[]` |
| GET | /teams/{id}/tasks/{task_id} | 任务详情（轮询） | — | `TaskRunDto` |
| POST | /teams/{id}/tasks | 发起任务 | `{request}` | 201 `{task_id, trace_id}` |
| POST | /tasks/{task_id}/approve | 审批 | `{approve: bool, actor_no}` | 200 `TaskRunDto` |
| GET | /plugins | 插件列表 | query: `type`, `status` | `PluginDto[]` |
| POST | /plugins/{id}/test | 插件测试调用 | `{params, actor_no}` | 200 `{ok, result}` |
| GET | /policies | 策略列表（只读） | — | `PolicyDto[]` |
| GET | /audit | 审计日志 | query: `trace_id`, `employee_id`, `decision` | `AuditEventDto[]` |
| GET | /traces/{trace_id} | Trace 时间线 | — | `TraceTimelineDto` |
| GET | /dashboard/kpis | 首页指标 | — | `{total_employees, twins, virtual, rpa, running}` |

## 3. 内部接口（服务间，不暴露前端）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | /internal/policy/evaluate | `{subject: {type, id, no}, resource: {type, id, data_level}, action, context: {location, internet, team_id?, task_id?}}` | `{decision: allow/deny/approval, policy_id?, reason}` |
| POST | /internal/gateway/invoke | `{employee_id, plugin_id, action, params, trace_id}` | `{ok, data, decision, audit_ids[]}` |
| POST | /internal/runtime/run | `{employee_id, task, trace_id, context}` | `{result, events[]}` |
| POST | /internal/sandbox/run | `{employee_id, task_id, command, mount_dir, network}` | `{mode: docker/local, status, logs}` |

内部接口只允许服务间调用（后端代码级边界），前端不得直连。

## 4. 核心 DTO 字段

### EmployeeDto

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
  "runtime": {"runtime_type": "harness|openclaw|agentteams|rpa", "runtime_ref": "...", "profile": "headless"},
  "sandbox": {"sandbox_policy_id": "SBX-001", "location": "remote", "internet": "deny"},
  "data_scope": {"max_level": "L2", "allowed_domains": ["HR_L1", "HR_L2_DEMO"]},
  "plugins": [{"plugin_id": "knowledge-l2", "name": "内部知识库", "type": "knowledge", "action": "read", "decision_mode": "allow", "data_level": "L2"}]
}
```

### TaskRunDto

```json
{
  "id": "T-20260817-001",
  "team_id": "TEAM-ONBOARD",
  "trace_id": "T-20260817-001",
  "request": "帮王小明完成入职准备",
  "status": "parsing|running|approval|completed|denied|failed",
  "subtasks": [
    {"worker_id": "VE-0002", "worker_no": "VE-0002", "summary": "确认入职制度", "plugin_ids": ["hr-employee-mcp"], "status": "completed", "result": "...", "approval": null}
  ],
  "summary": "Leader 汇总文本",
  "created_at": "2026-08-17T10:00:00Z"
}
```

### AuditEventDto / PolicyDto

```json
{"id": 1, "trace_id": "T-...", "ts": "...", "actor": "DT-E10281", "employee_id": "DT-E10281",
 "team_id": null, "plugin_id": "knowledge-l2", "action": "read", "decision": "allow",
 "policy_id": "P-PLUGIN-007", "reason": "...", "result_summary": "..."}
```

```json
{"id": "P-DATA-003", "name": "敏感数据禁止", "effect": "deny",
 "description": "resource.data_level == L3 -> Deny", "enabled": true}
```

### ChatMessageDto

`{id, session_id, employee_id, trace_id, role: user/assistant/tool, content, tool_cards: [{plugin_id, name, decision}], created_at}`

## 5. 契约变更登记

| 日期 | 变更 | 批准人 | 涉及文件 |
|---|---|---|---|
| 2026-08-17 | v1.0 初始冻结 | A | 本文件 / OpenAPI / 前端 types |
