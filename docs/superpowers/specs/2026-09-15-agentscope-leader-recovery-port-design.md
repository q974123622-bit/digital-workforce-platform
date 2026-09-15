# AgentScope AgentTeams Leader Recovery Port Design

## Purpose

Port the upstream AgentScope AgentTeams Team Leader result gate and dynamic replanning behavior into the existing Digital Workforce Platform AgentTeams runtime. The port fixes the current terminal cascade where one required Worker failure immediately fails its Leader and the Manager root task.

## Upstream baseline and license

- Repository: `https://github.com/agentscope-ai/AgentTeams`
- Pinned commit: `e9cdf2e416ecaab129b9e85ac4a7a19dd018cb7e`
- License: Apache License 2.0
- Source areas:
  - `copaw/src/copaw_worker/task.py`
  - `copaw/src/copaw_worker/hooks/tools/taskflow.py`
  - `copaw/src/copaw_worker/hooks/tools/projectflow.py`
  - `manager/agent/team-leader-agent/skills/task-management/SKILL.md`
  - `manager/agent/team-leader-agent/skills/project-management/SKILL.md`
  - `manager/agent/team-leader-agent/skills/team-coordination/SKILL.md`

Copied or closely derived code must carry an SPDX Apache-2.0 header, the upstream repository URL, and the pinned commit. A third-party notice records all imported source paths.

## Scope

### Ported behavior

1. Worker results use the upstream statuses `SUCCESS`, `SUCCESS_WITH_NOTES`, `REVISION_NEEDED`, `BLOCKED`, and `INTERRUPTED`.
2. A Worker result is only a candidate. It does not satisfy dependencies until the Leader accepts it.
3. Result checking is read-only. The result gate and graph transition remain separate operations.
4. Rejected, blocked, and interrupted tasks remain immutable terminal history.
5. The Leader decides the next graph shape and may add repair, evidence, or verifier nodes, or assign a new node to another eligible direct report.
6. Only accepted nodes satisfy dependencies returned by the ready-node resolver.
7. A Worker runtime failure returns to its Leader as an `INTERRUPTED` result instead of immediately failing required ancestors.
8. The Manager is notified only when the Leader explicitly returns `stop_blocked` or the bounded recovery budget is exhausted.
9. Matrix assignment and recovery events remain idempotent.

### Existing platform behavior retained

- `TaskRun`, `AgentTaskNode`, `AgentTaskDependency`, `AgentTaskEvent`, and `AgentMessage` remain the durable source of truth.
- Existing Manager to Leader to Worker hierarchy and direct-report validation remain enforced.
- Existing Matrix transport, Policy, Plugin Gateway, Capability Executor, permissions, and audit records remain in place.
- Existing ordinary collaboration and local deterministic demo APIs remain compatible.
- Database benchmark code is not changed by this port.

### Explicitly excluded

- Kubernetes controllers and CRDs
- MinIO and the upstream shared filesystem store
- CoPaw or QwenPaw container lifecycle
- Replacing the existing FastAPI service or frontend

## Components

### Source-derived taskflow core

`backend/app/vendor/agentscope_agentteams/taskflow_core.py` contains the smallest self-contained subset of upstream taskflow behavior used by this platform:

- result status constants and `TaskResult`
- `DagTask`
- result-to-terminal-state mapping
- DAG validation
- accepted-dependency ready-node calculation
- bounded loop decision state

The code stays storage-independent and does not depend on SQLAlchemy or Matrix.

### Database taskflow adapter

`backend/app/services/agentteams_taskflow_adapter.py` maps the source-derived types to existing rows. It:

- reads a submitted node plus immutable Tool/audit evidence as a `TaskResult`
- materializes a Leader's current child DAG
- validates a proposed complete graph before creating any node
- preserves accepted and historical terminal nodes
- creates all new nodes and dependencies atomically
- resolves ready nodes using the source-derived core

The adapter never creates a second persistent plan store.

### Leader decision contract

The Leader receives:

- original task instruction
- submitted result status and summary
- sanitized Tool result evidence and audit references
- the current department DAG
- direct-report identities and capabilities
- remaining recovery budget

It returns one structured action:

- `accept`
- `replan`
- `stop_blocked`

`replan` carries the complete desired department DAG. Existing accepted nodes that still matter are retained, obsolete pending nodes may be omitted, and new work uses new task keys. All assignments are validated against direct reports and capabilities before any database mutation.

### Runtime and collaboration transitions

The platform uses an authenticated `task.result.submitted` review request for normal results and a new authenticated `task.recovery.requested` request for runtime failures. The Leader responds with `task.result.accepted`, `task.replan`, or `task.blocked`.

For `task.replan`, the platform first validates the entire proposed graph, then atomically records the old result decision, creates the new graph nodes, rewires dependencies, and queues only the ready assignments. A validation failure creates no partial graph.

For `task.blocked`, the platform records the Leader decision and then propagates failure to the Manager. A bounded recovery counter prevents infinite loops; exhaustion follows the same blocked path and remains visible in the audit trail.

## Evidence contract

The Leader review bundle includes only evidence already authorized and persisted for the reviewed task:

- Worker result text
- result status
- audit IDs
- sanitized Tool result data
- explicit evidence references

Secrets, Matrix tokens, credentials, raw model traces, and unrelated task evidence are excluded. The Leader cannot invent an audit ID or request evidence from another root task.

## UI behavior

The task tree continues to show all historical nodes. New status labels distinguish:

- waiting for Leader review
- Leader replanning
- revision requested
- blocked and escalated
- interrupted and retained

The tree displays replacement/revision links without reopening or overwriting old nodes.

## Acceptance criteria

1. A required Worker `runtime_error` does not immediately fail its Leader or root task.
2. The Leader receives an `INTERRUPTED` recovery request containing the safe failure stage and available evidence.
3. A rejected result can produce a different Worker assignment or an added verifier task.
4. No new node is persisted when any task in the proposed graph is invalid.
5. Accepted nodes alone release dependent tasks.
6. Old submissions, rejection feedback, failed nodes, and replacement links remain queryable.
7. `stop_blocked` is the only normal Leader decision that propagates failure upward.
8. Duplicate Matrix recovery messages do not create duplicate nodes.
9. Existing strict hierarchy, frontend, runner, and local demo regression suites remain green.
10. A real Matrix smoke demonstrates Worker failure or rejection, Leader replan, replacement execution, Leader acceptance, and Manager continuation.
