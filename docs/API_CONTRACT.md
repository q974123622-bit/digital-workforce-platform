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
| POST | `/employees/{employee_no}/chat` | ✅ | 单聊（Sprint 4，整段 JSON 返回，SSE 为弹性项） | `{message, session_id?}` | `{session_id, trace_id, message, tool_cards[], policy_denied?}` |

### 3.2 Policy API（✅ 骨架已实现；评估接口走内部 API）

| 方法 | 路径 | 状态 | 说明 | 关键请求 | 关键响应 |
|---|---|---|---|---|---|
| GET | `/policies` | ✅ | 策略列表（只读） | query: `effect`, `enabled` | `PolicyDto[]` |
| GET | `/policies/{policy_id}` | ✅ | 策略详情 | — | `PolicyDto` |
| POST | `/policies` | ✅ | 创建策略 | `PolicyCreateDto` | 201 `PolicyDto` |
| PUT | `/policies/{policy_id}` | ✅ | 更新策略 | `PolicyUpdateDto` | `PolicyDto` |
| DELETE | `/policies/{policy_id}` | ✅ | 删除策略 | — | 204 |
| POST | `/internal/policy/evaluate` | ✅ | 策略评估（内部，Sprint 2） | 见 §6.1 | `{decision, policy_id?, reason}` |

### 3.3 Plugin API（✅ 骨架已实现；调用走 Gateway）

| 方法 | 路径 | 状态 | 说明 | 关键请求 | 关键响应 |
|---|---|---|---|---|---|
| GET | `/plugins` | ✅ | 插件列表 | query: `type`, `status` | `PluginDto[]` |
| GET | `/plugins/{plugin_id}` | ✅ | 插件详情 | — | `PluginDto` |
| POST | `/plugins` | ✅ | 登记插件 | `PluginCreateDto` | 201 `PluginDto` |
| PUT | `/plugins/{plugin_id}` | ✅ | 更新插件 | `PluginUpdateDto` | `PluginDto` |
| DELETE | `/plugins/{plugin_id}` | ✅ | 删除插件 | — | 204 |
| POST | `/plugins/{plugin_id}/test` | 📋 | 插件测试调用（经 Gateway） | `{params, actor_no}` | 200 `{ok, result, decision}` |
| POST | `/internal/gateway/invoke` | ✅ | 插件执行（内部，Sprint 2） | 见 §6.2 | `{ok, data, decision, audit_ids[]}` |

### 3.4 Audit API（✅ 骨架已实现；Trace 时间线待实现）

| 方法 | 路径 | 状态 | 说明 | 关键请求 | 关键响应 |
|---|---|---|---|---|---|
| GET | `/audit` | ✅ | 审计日志 | query: `trace_id`, `employee_id`, `decision` | `AuditEventDto[]` |
| GET | `/audit/{event_id}` | ✅ | 单条事件 | — | `AuditEventDto` |
| POST | `/audit` | ✅ | 写入事件（供服务内部使用） | `AuditCreateDto` | 201 `AuditEventDto` |
| DELETE | `/audit/{event_id}` | ✅ | 删除事件（演示清理用） | — | 204 |
| GET | `/traces/{trace_id}` | 📋 | Trace 时间线（聚合） | — | `TraceTimelineDto` |

### 3.5 Chat API（✅ Sprint 4 已实现，整段 JSON）

| 方法 | 路径 | 说明 | 关键请求 | 关键响应 |
|---|---|---|---|---|
| POST | `/employees/{employee_no}/chat` | 单聊（整段 JSON 返回；SSE 为 P1 弹性项） | `{message, session_id?}` | `{session_id, trace_id, message, tool_cards, policy_denied}` |
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
| POST | `/teams/{team_id}/tasks` | 发起任务 `{request}` → 201 `{task_id, trace_id}`（✅ Sprint 5） |
| GET | `/teams/{team_id}/tasks/{task_id}` | 任务详情（轮询）（✅ Sprint 5） |
| POST | `/tasks/{task_id}/approve` | 审批 `{approve: bool, actor_no}`（✅ Sprint 5） |

### 3.7 Knowledge API（✅ 已实现；查询走 Knowledge Adapter）

| 方法 | 路径 | 状态 | 说明 |
|---|---|---|---|
| GET | `/knowledge-bases` | ✅ | 知识库资源登记列表（Resource Registry） |
| GET | `/knowledge-bases/{kb_id}` | ✅ | 知识库资源登记详情 |
| POST | `/internal/gateway/invoke` | ✅ | 通用插件执行（含知识查询） |
| POST | `/internal/knowledge/search` | ✅ | 知识库专用入口（Sprint 3）：`{employee_id, knowledge_base_id, query, trace_id}` |

### 3.8 个人工作中心（职场）API（✅ Sprint 7 已实现）

> 员工视角的会话工作台：技能上传与「私聊/协作群聊」统一由 Conversation 承载。
> 演示鉴权：新接口显式传 `actor_no`（无真实 IAM）。

| 方法 | 路径 | 说明 | 关键请求 | 关键响应 |
|---|---|---|---|---|
| GET | `/workplace?actor_no=` | 职场聚合：本人信息 + 我的分身 + 可用数字员工（仅 virtual/rpa）+ 技能 + 最近会话 | — | `WorkplaceHomeDto` |
| POST | `/skills` | 上传技能（文本/Markdown） | `{actor_no, name, description?, content?}` | 201 `SkillDto` |
| GET | `/skills?actor_no=` | 我的技能列表 | — | `SkillDto[]` |
| PUT | `/skills/{skill_id}?actor_no=` | 更新本人的技能（含 status 启停） | `{name?, description?, content?, status?}` | `SkillDto` |
| DELETE | `/skills/{skill_id}?actor_no=` | 删除本人的技能 | — | 204 |
| GET | `/capabilities?actor_no=` | 统一能力目录（全部 Plugin + 本人 Skill） | — | `CapabilityDto[]` |

`CapabilityDto` 统一字段：`contract_version / id / name / source_type / kind / status / executable / actions / input_schema / executor / owner_human_no / ready / issues`。
其中 Skill 固定为 `kind=instruction, executable=false, executor.primary=prompt`；Plugin 才能进入 Policy/Gateway 执行链。
| DELETE | `/skills/{skill_id}` | 删除技能 | — | 204 |
| POST | `/conversations` | 创建会话（direct 恰 1 名成员且幂等复用；group 自动带头分身，≥1 名 virtual/rpa） | `{actor_no, kind, title?, participant_employee_nos}` | 201 `ConversationDto` |
| GET | `/conversations?actor_no=` | 会话摘要列表（按 updated_at 倒序） | — | `ConversationSummaryDto[]` |
| GET | `/conversations/{id}` | 会话详情（参与者 + 按 seq 消息） | — | `ConversationDto` |
| POST | `/conversations/{id}/messages` | 统一发送入口：私聊单成员回复；群聊先由分身判断「任务/闲聊」——任务型走拆解→指派→Gateway 执行→审批→Leader 汇总，闲聊仅一位成员回复（点名某成员则那位回，否则分身回） | `{actor_no, content}` | `ConversationDto` |
| POST | `/conversations/{id}/participants` | 添加群成员（仅 virtual/rpa） | `{employee_no}` | `ConversationDto` |
| DELETE | `/conversations/{id}?actor_no=` | 清空会话（删除消息与协作任务，演示清洁用） | — | `{ok: true}` |

`ConversationDto`：`{id, kind: direct|group, title, owner_human_no, participants: [{employee_no, name, role, employee_type}], messages: [{participant_no, participant_name, role, content, tool_cards, seq}], tasks: TaskRunDto[], updated_at}`；`TaskRunDto.trigger_message_seq` 用于任务卡片内联到触发消息之后。
技能注入：分身（twin）对话 system prompt 自动追加「【你掌握的技能】」段落（仅本人可见，内容上限 4000 字符）。
协作任务：群聊任务由 `TeamTaskOrchestrator` 创建（conversation_id + trigger_message_seq 关联），执行走员工独立 Harness → Policy/Gateway → Plugin Adapter Tool，审批复用 `POST /tasks/{id}/approve`；子任务结果为格式化工具回执，Harness 输出单独保存为计划摘要；同会话同请求 10 分钟内自动去重复用。

### 3.9 Access Request API（✅ P20 已实现：L3 敏感资源白名单申请/审批）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/access-requests?applicant_no={employee_no}` | 发起敏感资源申请；仅 employment_type=formal 可申请，实习生返回 403 POLICY_DENIED（不落申请单） |
| POST | `/access-requests/{request_id}/approve` | 管理员一键通过/拒绝；终态再审批返回 409 STATE_CONFLICT |
| GET | `/access-requests?applicant_no=&status=` | 查询申请单（含待审批列表），按申请人/状态过滤 |

请求（创建）：

```json
{"resource_type": "knowledge|plugin|data", "resource_id": "KB-CUSTOMER-SENSITIVE", "reason": "演示申请"}
```

请求（审批）：`{"approve": true, "actor_no": "DT-E10281"}`

响应（AccessRequestDto）：

```json
{"id": 1, "applicant_no": "DT-E10281", "resource_type": "knowledge", "resource_id": "KB-CUSTOMER-SENSITIVE",
 "reason": "演示申请", "status": "pending|approved|rejected|granted", "approval_chain": [],
 "decided_by": null, "decided_at": null, "created_at": "2026-08-19T..."}
```

状态码：`201`（创建成功）/ `200`（审批、列表）/ `403 POLICY_DENIED`（非正式员工）/ `404`（资源或申请单不存在）/ `409 STATE_CONFLICT`（终态重复审批）/ `422`（参数不合法）。

说明：
- 通过审批时后端写入 `employee_plugin_grant`（action=read，decision_mode=allow，grant_source=whitelist）并置状态 granted；拒绝置 rejected；
- 策略 P-DATA-003：L3 资源访问（知识读 / 插件执行与读取）无白名单授权默认 DENY，有白名单授权 ALLOW；L3 一律走白名单申请；
- 审计：access_apply / access_approve / access_grant / read 四类事件按 trace_id（默认 `ARQ-{id}`，访问调用方传入同值）可聚合追溯。

### 3.10 Memory API（✅ P23 已实现：记忆插件后端核心，Phase 1）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/memory` | 写入一条记忆（7 维度标签：subject_type/subject_no/kind/content/content_type/related_subject_no/trace_id/file_ref/visibility/data_level/lifecycle） |
| GET | `/memory?subject_no=&kind=&related_subject_no=&visibility=&data_level=` | 查询记忆（时间倒序，最新在前）；按 X-Demo-Actor 过滤权限 |
| POST | `/memory/summarize` | 压缩过期会话为摘要（Step 6），返回 `{"summarized": count}` |
| POST | `/memory/attachments` | 上传文本附件（multipart：subject_no + file），存 backend/storage/ 并生成 kind=attachment 记忆 |

请求（写入）：

```json
{"subject_type": "human", "subject_no": "E10021", "kind": "fact", "content": "...", "visibility": "personal", "data_level": "L2"}
```

响应（MemoryDto）：`{id, subject_type, subject_no, kind, content, content_type, related_subject_no, trace_id, file_ref, visibility, data_level, lifecycle, created_at, updated_at}`

状态码：`201`（写入/附件）/ `200`（查询、压缩）。权限规则（PoC）：无 X-Demo-Actor 视为系统内部调用；管理员（E10021）可读全部；public 任何人；confidential 仅管理员；personal/shared 仅本人或 owner（后续接三级权限/白名单）。

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
- Harness 上下文必须包含员工工号、人设、职责、Task ID、子任务和 AgentTeams 协作结论；不同员工使用独立 DSH_HOME/workspace。
- `mode=harness` 时 UI 显示「运行时：DeepSeek Harness」，并单独显示工具名称/类型。
- Harness 未启用或不可用时 UI 必须标注「运行时：Demo Adapter 降级」，不得笼统显示 Adapter。
- Harness 输出是工具调用计划，不是业务成功结果；任务完成状态只能来自 Gateway 后的 Adapter 工具回执。
- 失败返回 `RUNTIME_UNAVAILABLE`（503），不吞错误。

## 5. Knowledge Adapter Interface（✅ Sprint 3 已实现）

知识查询统一经 Plugin Gateway → Knowledge Adapter；前端与 Chat 层不得直读 `mock-data/kb/` 文件。

统一签名：`search(employee_id, knowledge_base_id, query, trace_id) -> KnowledgeSearchResult`

实现（`backend/app/services/knowledge_adapter.py`）：
- `MockKnowledgeAdapter`：读取 `mock-data/kb/` 虚构文档返回片段（source=demo）
- `InternalKnowledgeAdapterStub`：只保留接口与配置结构（endpoint/credential 走环境变量引用），不接入真实内容（source=stub）

### 请求（`POST /internal/knowledge/search`）

```json
{
  "employee_id": "DT-E10281",
  "knowledge_base_id": "KB-INTERNAL",
  "query": "入职流程",
  "trace_id": "T-20260817-001"
}
```

### 响应

```json
{
  "ok": true,
  "data": {"source": "demo", "knowledge_base_id": "KB-INTERNAL", "query": "...", "hits": [{"title": "入职流程", "snippet": "..."}]},
  "decision": "allow",
  "audit_ids": [1],
  "policy_id": "POLICY-001"
}
```

约束：
- 仅返回已授权数据等级（`data.level ≤ subject.max_data_level` 且 grant 允许）；越级查询由 Policy Engine 拒绝。
- 内容全部来自 `mock-data/kb/`（虚构），不得引入真实文档。
- 审计必须记录 `knowledge_base_id`。

## 6. 内部接口（服务间，不暴露前端）

### 6.1 Policy Evaluate（✅ Sprint 2 已实现）

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

### 6.2 Gateway Invoke（✅ Sprint 2 已实现）

`POST /internal/gateway/invoke`

```json
{"employee_id": "DT-E10281", "plugin_id": "knowledge-l2", "action": "read", "params": {}, "trace_id": "T-..."}
```

响应：`{"ok": true, "data": {...}, "decision": "allow", "audit_ids": [1]}`

### 6.3 Runtime / Sandbox（Sandbox ✅ Mock Executor，Sprint 3；Runtime 📋）

`POST /internal/runtime/run`、`POST /internal/sandbox/run`

Sandbox 请求：`{"employee_id", "task_id", "command", "mount_dir", "network", "execution_location": "remote|local"}` → `{"mode": "docker|local", "status", "logs"}`

约束：先 Policy 后执行；remote_only 请求 local → 403 POLICY_DENIED（POLICY-004）；internet=deny 请求非 none 网络 → 403（POLICY-003）；被拒请求不启动执行器。

## 7. 核心 DTO 字段

### EmployeeDto（✅ 与实现一致，平铺结构）

```json
{
  "id": "DT-E10281",
  "employee_no": "DT-E10281",
  "name": "张三的分身",
  "type": "twin | virtual | rpa",
  "employment_type": "formal | intern",
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
> v1.1 补充（2026-08-19）：EmployeeDto 增加 `employment_type`（twin 取真人、virtual/rpa 取 Owner），
> 供前端渲染「正式员工/实习生」身份标识。

### PluginDto / PolicyDto / AuditEventDto

```json
{"id": "knowledge-l2", "name": "内部流程知识库", "type": "knowledge", "endpoint_ref": "mock://kb/l2", "data_level": "L2", "status": "active", "description": "..."}
```

```json
{"id": "P-DATA-003", "name": "敏感数据禁止", "effect": "deny", "description": "resource.data_level == L3 -> Deny", "enabled": true, "priority": 100}
```

```json
{"id": 1, "trace_id": "T-...", "ts": "...", "actor": "DT-E10281", "employee_id": "DT-E10281",
 "team_id": null, "plugin_id": "knowledge-l2", "knowledge_base_id": "KB-INTERNAL", "action": "read", "decision": "allow",
 "reason": null, "result_summary": "..."}
```

### KnowledgeBaseDto（✅ Sprint 3 资源模型）

```json
{"id": "KB-INTERNAL", "name": "正式员工内部知识库", "level": "L2", "data_level": "L2",
 "resource_type": "knowledge", "allowed_employment_type": ["formal"],
 "department_scope": ["*"], "domain": "综合", "description": "...", "status": "active", "doc_path": "mock-data/kb/..."}
```

登记资源：KB-PUBLIC（L1 公共）、KB-INTERNAL（L2 正式员工内部）、KB-FINTECH（L2 金融科技部门）。

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
| 2026-08-17 | v1.1 实现状态更新（Sprint 2）：Policy Evaluate、Gateway Invoke、Knowledge Adapter 由 📋 转 ✅；接口定义无变更 | A | 本文件 |
| 2026-08-17 | v1.1 兼容扩展（Sprint 3）：KnowledgeBaseDto 增加 data_level/resource_type/allowed_employment_type/department_scope（可选）；AuditEventDto 增加 knowledge_base_id（可选）；新增 POST /internal/knowledge/search；SandboxRunIn 增加可选 execution_location；Sandbox /knowledge/search 转 ✅ | B | 本文件 / shared-schema / models / schemas |
| 2026-08-17 | v1.1 实现状态更新（Sprint 4）：Chat API 转 ✅（整段 JSON，SSE 弹性）；LLMProvider 统一 chat/tool_call/structured_output 已实现 | A | 本文件 |
| 2026-08-18 | v1.1 实现状态更新（Sprint 5）：Team API 任务/审批转 ✅（TeamTaskOrchestrator，模板拆解 + Gateway 执行 + 审批 + LLM 汇总）；接口定义无变更 | A | 本文件 |
| 2026-08-19 | v1.1 兼容扩展（Sprint 7）：新增 §3.8 职场 API（WorkplaceHomeDto / SkillDto / ConversationDto）；新接口显式传 actor_no；群聊消息走顺序编排并复用 Policy→Gateway→审计 | A | 本文件 / shared-schema / schemas / routers/workplace.py |
| 2026-08-19 | v1.1 实现更新（Sprint 7 C 档）：`POST /conversations/{id}/messages` 群聊改为「分身判断任务/闲聊」；任务型接入 TeamTaskOrchestrator（拆解/指派/审批/汇总），闲聊单成员回复；ConversationDto 增加 tasks；新增 SubtaskExecutor 接口 | A | 本文件 / shared-schema / schemas / services/team_orchestrator.py / services/group_chat.py |
| 2026-08-19 | v1.1 实现更新（Sprint 7 会话管理）：TaskRunDto 增加 trigger_message_seq（任务卡内联）；移除分身受理气泡；子任务结果格式化为可读文本；同请求去重；新增 DELETE /conversations/{id} 清空会话；会话摘要预览显示最新任务状态 | A | 本文件 / shared-schema / schemas / services/team_orchestrator.py / services/group_chat.py / routers/workplace.py |
| 2026-08-19 | v1.1 兼容扩展（P20）：新增 §3.9 Access Request API 与 AccessRequestDto（申请/审批/列表）；L3 白名单策略（P-DATA-003 改为白名单控制，POLICY-005 审批语义被取代）；新增 knowledge-l3 插件与 KB-CUSTOMER-SENSITIVE（L3）；审计新增 access_apply/access_approve/access_grant 事件 | 待 A 确认 | 本文件 / shared-schema/types.ts / routers/access.py / models / schemas / services/policy.py |
| 2026-08-19 | v1.1 兼容扩展（身份标识）：EmployeeDto 增加 employment_type（twin 取真人、virtual/rpa 取 Owner）；前端据此渲染「正式员工/实习生」标签并修正聊天页人设推断 | 待 A 确认 | 本文件 / shared-schema/types.ts / schemas.py / routers/employees.py / 前端 Employees/EmployeeDetail/ChatPage |
| 2026-08-20 | v1.1 分级修正 + 聊天守卫（P21）：KB-IT-SERVICE L1→L2（allowed_employment_type=[formal]）；ChatOrchestrator 系统提示强化（知识库/制度/流程问题必须调用 search_knowledge，不得凭记忆列举主题，POLICY_DENIED 只告知无权访问）+ 未调工具兜底轮（命中查询意图且无工具卡时重生成一次，仍无则返回明确无权限/无法确认文案）；工具描述补充 KB-CUSTOMER-SENSITIVE | 待 A 确认 | 本文件 / mock-data/seed.json / services/chat.py / tests |
| 2026-08-20 | v1.1 实现更新（P22）：聊天系统提示【知识库】改为按 subject 动态注入可访问清单（逐库 Policy evaluate=allow 才列入，输出 knowledge_base_id/name/data_level）；未授权库不得声称可访问、不得凭记忆描述内容；新增 accessible_knowledge_bases() | 待 A 确认 | 本文件 / services/knowledge_registry.py / services/chat.py / tests |
| 2026-08-20 | v1.1 兼容扩展（P23 记忆插件 Phase 1，integration/memory-plugin）：新增 §3.10 Memory API 与 MemoryDto（写入/查询/压缩/附件）；models 新增 MemoryEntry（7 维度标签）与 ChatSession title/deleted/summarized；seed 新增 personal_memories；附件存 backend/storage/（gitignore）；PoC 管理员 E10021 写死（后续接三级权限/白名单） | 待 A 确认 | 本文件 / models.py / schemas.py / routers/memory.py / services/memory_* / seed.py / seed.json / .gitignore / tests/test_memory.py |
