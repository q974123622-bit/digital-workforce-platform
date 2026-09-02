# AgentTeams Collaboration Protocol v1.0

This document describes the protocol implemented by `backend/app/services/agentteams_protocol.py`, the persisted collaboration service, and the Matrix runner. It is the transport contract for the standalone collaboration loop; the database remains the source of truth for task state, messages, and events.

## Wire format

Each state-bearing Matrix body line is exactly one prefix followed by one JSON object on the same line:

```text
DWP_EVENT {"version":"1.0","message_id":"MSG-001","type":"task.progress","root_task_id":"AT-root","task_id":"ATN-child","parent_task_id":"AT-root","trace_id":"AT-root","sender":"VE-0002","receiver":"platform","timestamp":"2026-09-02T09:00:00+08:00","idempotency_key":"ATN-child:task.progress:1","payload":{"progress":50,"summary":"Checked account prerequisites"}}
```

Ordinary Matrix text is not interpreted as a state event. `DWP_EVENT` is case-sensitive. A body can contain ordinary text and multiple prefixed lines; only lines beginning with the exact prefix are parsed.

| Field | Required | Meaning and validation |
|---|---:|---|
| `version` | Yes | Exactly `"1.0"` in this release. |
| `message_id` | Yes | Non-empty protocol-message identifier. Outbound messages use the persisted `AgentMessage.id`. |
| `type` | Yes | One of the twelve message types below. |
| `root_task_id` | Yes | Root `TaskRun` identifier for the collaboration tree. |
| `task_id` | Yes | Current `AgentTaskNode` identifier. |
| `parent_task_id` | No, defaults to `null` | Parent node ID. When present it must equal the persisted node parent; `null` is valid only for a root node. |
| `trace_id` | Yes | Non-empty trace identifier. It must equal both the node and root run trace ID. A newly created root uses its Task ID as the trace ID. |
| `sender` | Yes | Logical platform or employee ID. For inbound employee messages it must match the employee bound to the Matrix sender. |
| `receiver` | Yes | Logical destination. `tool.request` must use `platform`; the service does not infer a tool receiver. |
| `timestamp` | Yes | Non-empty producer timestamp string. It is retained in the received protocol payload; durable event order is not derived from it. |
| `idempotency_key` | Yes | Non-empty key used to make a received protocol message effective once. |
| `payload` | No, defaults to `{}` | JSON object. Individual message handlers validate the payload fields they consume; all other fields are retained for audit or result data. |

The envelope forbids extra top-level fields. JSON is rendered compactly by the platform but receivers must rely on the parsed object, not field order or whitespace.

## Message types and sender roles

“Sender” below is the logical `sender` field. The Matrix account that posts an inbound event is separately verified as described in [Strict Matrix sender validation](#strict-matrix-sender-validation). The platform runner may deliver a persisted outbound envelope on behalf of a logical coordinator.

| Type | Allowed logical sender | Receiver / implemented effect |
|---|---|---|
| `task.submit` | `platform` | Root coordinator. Created when the platform creates the root task; it is outbound-only to the collaboration service. |
| `task.delegate` | Current node assignee | `platform`. The assignee creates one or more children and moves itself to `waiting_children`. A worker can therefore delegate within its own subtree. |
| `task.assign` | Child coordinator | Child assignee. The platform queues and delivers this assignment after a valid delegation; it is outbound-only to the collaboration service. |
| `task.accepted` | Current node assignee | `platform`. Moves `assigned` to `accepted`. |
| `task.progress` | Current node assignee | `platform`. Moves `accepted` to `running` on the first update, or updates an already `running` node. |
| `agent.message` | Current node assignee or coordinator | Logical receiver named in the envelope. It records a cross-Agent communication event without changing task status. |
| `tool.request` | Current node assignee | `platform` only. The platform resolves the tool target and calls the guarded Gateway. |
| `tool.result` | `platform` | Requesting node assignee. The platform queues this safe, auditable response; it is outbound-only to the collaboration service. |
| `task.result` | Current node assignee | `platform`. Completes the node and preserves its payload as node output. |
| `task.failed` | Current node assignee | `platform`. Fails the node with the supplied error fields. |
| `task.summary` | `platform` for the summary request; current node coordinator for the summary response | The platform requests a summary after all required children complete. A coordinator response completes that node; a root response also completes the root `TaskRun` and saves its summary. |
| `task.cancel` | `platform` | Each non-terminal assignee. The HTTP cancellation action queues these outbound notifications. It is not an inbound service transition. |

The inbound state handlers are intentionally narrower than the envelope vocabulary: `task.delegate`, `task.accepted`, `task.progress`, `agent.message`, `tool.request`, `task.result`, `task.failed`, and the coordinator response form of `task.summary` are accepted. A valid envelope with another type is an `unsupported_message_type` conflict rather than an implicit action.

## Payload examples

These examples show the message-specific `payload` object only. Every example must still be placed in the full envelope above.

### Submit

```json
{
  "title": "New employee onboarding readiness review",
  "instruction": "Coordinate HR and IT checks, then summarize risks.",
  "material": {"employee": "pending-new-hire"}
}
```

### Delegate

```json
{
  "subtasks": [
    {
      "client_task_key": "hr-policy",
      "title": "HR policy and materials review",
      "instruction": "Check the onboarding policy and supplied materials.",
      "assignee_agent_id": "VE-0002",
      "depends_on": [],
      "material": {"checklist": "onboarding"},
      "required": true,
      "capability": "knowledge.read"
    },
    {
      "client_task_key": "it-account",
      "title": "IT account and service review",
      "instruction": "Check account readiness and service status.",
      "assignee_agent_id": "VE-0003",
      "depends_on": [],
      "material": {},
      "required": true,
      "capability": "service.read"
    }
  ]
}
```

`client_task_key`, `title`, `instruction`, and `assignee_agent_id` are non-empty strings. `depends_on` may name only sibling keys, must not repeat a key, and must be acyclic. The assignee must be a team member with a runtime reference. The first version applies configured depth and child-count limits before creating any child records.

### Progress

```json
{"progress": 50, "summary": "Account prerequisites checked"}
```

`progress` is an integer from 0 through 100; booleans are rejected.

### Tool request and tool result

```json
{
  "plugin_id": "knowledge-read-plugin",
  "action": "read",
  "params": {"document": "onboarding-policy"},
  "context": "Confirm the policy version required for this task."
}
```

```json
{
  "ok": true,
  "decision": "allow",
  "audit_ids": [101],
  "tool_name": "knowledge-read",
  "data": {"status": "reviewed"}
}
```

The tool response is generated by the platform, not accepted from Matrix. It contains only a bounded, sanitized result: decision, audit IDs, selected tool metadata, and safe data. Policy denials and unexpected gateway failures produce safe `tool.result` payloads rather than raw credential, endpoint, or exception data.

### Result, failure, and summary

```json
{"result": "HR materials are complete", "risks": []}
```

```json
{"error_code": "service_unavailable", "error_message": "Required service did not respond before the task deadline"}
```

```json
{
  "summary": "HR and IT checks are complete; one follow-up is recorded.",
  "risks": ["Confirm account owner on the first working day"],
  "next_actions": ["Manager reviews the follow-up"]
}
```

For result and summary, the service stores the complete payload as node output. For failure, absent fields become `failed` and an empty message; callers should still send a concise non-sensitive explanation.

## Strict Matrix sender validation

Before an inbound message can change state, the service checks all of the following:

1. `root_task_id`, `task_id`, `parent_task_id`, and `trace_id` must match the persisted collaboration tree.
2. The Matrix event sender must map to exactly one stored AgentTeams digital employee. A runtime reference may be a full MXID, or a localpart combined with the configured Matrix domain; malformed or ambiguous mappings are rejected.
3. The mapped employee number must equal envelope `sender` and must be a member of the root team.
4. The sender must have the role permitted by the message type: node assignee, node coordinator, or both as listed above.
5. `tool.request` must name `platform` as receiver, and normal state transitions must be allowed from the node’s current status.

This means an envelope cannot claim another worker’s employee number simply because it has a syntactically valid JSON line. `task.assign` is a useful distinction: its logical coordinator sender is recorded in the outbound envelope, but platform outbox/runner code delivers it; it is not accepted as an inbound assignment from an arbitrary Matrix sender.

## Idempotency and ordering

- Received protocol keys are persisted under an internal `in:` prefix. A repeated `idempotency_key` returns no events and has no second state effect.
- Matrix event IDs are also deduplicated. If the same protocol key arrives in a different Matrix event, the first protocol record remains the state-changing receipt and the later Matrix event is audit-visible as a duplicate.
- The runner sends pending outbox records in `created_at`, then ID order. It retains an unsent item as `pending` after an uncertain send, so the same idempotency key can be retried.
- Per-root task events are append-only and use the next persisted `event_seq`; the service flushes each event so multiple appends in one transaction retain order. This is the durable event order, not the producer timestamp order.
- Matrix sync stores a per-account cursor. A receive batch is processed in a savepoint and its cursor advances only after the complete batch succeeds. A failed authorization or transition rolls back that batch’s effects and cursor rather than partially advancing it.
- A root in `completed`, `failed`, `timed_out`, or `cancelled` ignores later protocol state messages. The later Matrix event remains audit-visible without reopening the root.

## Protocol errors and versions

Plain Matrix text is saved as a non-state `matrix.text` audit record. A prefixed line with invalid JSON, missing/extra envelope fields, an unknown type, or an unsupported version is recorded as `protocol.invalid` with bounded error metadata and a protocol-invalid event; no task status transition is applied.

An envelope whose version is not `1.0` fails model validation. It is logged as invalid and **must not mutate collaboration task state**. A valid-but-unauthorized or invalid-transition envelope likewise does not receive a partial state change: the runner rolls back the current receive batch and does not advance its sync cursor. Operators should correct the sender, room, payload, or state sequence and then inspect persisted task events and Matrix message records.
