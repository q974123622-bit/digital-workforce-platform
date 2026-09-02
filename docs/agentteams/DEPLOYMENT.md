# AgentTeams collaboration deployment and operations

This runbook starts the platform-side collaboration runner. It does **not** create Matrix, AgentTeams, Workers, containers, or database data. The first demo version depends on an already configured Matrix and AgentTeams environment and does not require Redis, Celery, or PostgreSQL; its durable demo state uses the platform’s existing database. The demo script requires an initialized `backend/dwp.db` and disables automatic seed recovery, so it cannot delete or reseed platform data.

## Prerequisites

Before starting the local platform process, confirm all of the following outside this repository:

- An existing Matrix homeserver, a usable collaboration room, and the AgentTeams controller that serves that environment.
- A Manager plus at least two Worker identities that have already joined the intended room. The platform health endpoint checks the room using its configured Matrix account, but it does not remotely prove that Manager or Worker runtimes are alive.
- A team already exists in the platform database. Its leader and at least two AgentTeams digital employees must have distinct, resolvable Matrix runtime references. The Worker records must be `runtime_type=agentteams`; health reports these bindings as `configured`, not as remotely connected.
- An initialized local SQLite database already exists at `backend/dwp.db`. The demo script blocks before startup when this file is absent: database initialization is a separate, explicitly approved operation and is never performed by the script.
- The repository has an existing `backend/.venv/Scripts/python.exe` with the project dependencies and frontend dependencies already installed. The startup script never installs dependencies.
- Ports 8000 and, unless `-NoFrontend` is used, 5173 are free. The script refuses to stop or replace a process using either port.

The controller check is intentionally read-only: if Docker is available, the script inspects the configured controller container. It never runs Docker Compose, creates a container, starts a service, or resets platform data.

## Required configuration names

Set deployment configuration through the normal environment or the ignored `backend/.env`; never commit values. The platform code requires the following names for a live Matrix-and-room readiness check:

| Purpose | Names |
|---|---|
| Enable the runner | `DWP_AGENTTEAMS_COLLAB_ENABLED` — the demo script sets this only in its own process and child processes; do not persist it merely to run the demo. |
| Demo seed safety | `DWP_SKIP_AUTO_SEED` — an internal, non-secret safety switch set only by the demo script in its process and child processes. It skips `seed_if_empty()` while retaining normal schema compatibility checks for the existing database. |
| Matrix endpoint and authentication | `AGENTTEAMS_MATRIX_URL`, plus either `AGENTTEAMS_MATRIX_TOKEN` or both `AGENTTEAMS_ADMIN_USER` and `AGENTTEAMS_ADMIN_PASSWORD` |
| Target room and Manager identity | `AGENTTEAMS_ROOM_ID`, `AGENTTEAMS_MANAGER_MXID` |
| Runtime-reference resolution | `AGENTTEAMS_MATRIX_DOMAIN` when stored Worker references use localparts rather than full MXIDs |
| Controller preflight | `AGENTTEAMS_CONTROLLER_CONTAINER` when the existing controller uses a name other than the platform default |

The following runner controls are optional because the code has bounded defaults: `DWP_AGENTTEAMS_COLLAB_POLL_SECONDS`, `DWP_AGENTTEAMS_TASK_TIMEOUT_SECONDS`, `DWP_AGENTTEAMS_ACCEPT_TIMEOUT_SECONDS`, `DWP_AGENTTEAMS_MAX_DEPTH`, and `DWP_AGENTTEAMS_MAX_CHILDREN`.

Other AgentTeams integration names may exist for a surrounding deployment, such as `AGENTTEAMS_TEAM_NAME`, `AGENTTEAMS_MANAGER_CONTAINER`, `AGENTTEAMS_MANAGER_DM_ROOM_ID`, `AGENTTEAMS_WORKER_MODEL`, and `AGENTTEAMS_WORKER_RUNTIME`. They are not substitutes for the readiness inputs above. Keep all credentials, endpoints, tokens, and passwords out of terminal output, Markdown, screenshots, and commits.

## Local start and stop

From the repository root, run:

```powershell
.\scripts\run_agentteams_demo.ps1
```

For an API-only session, run:

```powershell
.\scripts\run_agentteams_demo.ps1 -NoFrontend
```

The script performs only these checks/actions:

1. Checks the exact `backend/.venv/Scripts/python.exe` path and an existing `backend/dwp.db`. A missing database blocks startup; the script does not initialize a database or seed data.
2. Sets `DWP_AGENTTEAMS_COLLAB_ENABLED=1` and the internal `DWP_SKIP_AUTO_SEED=1` only in its process environment and inherited child processes. The backend therefore retains its normal schema compatibility path but skips `seed_if_empty()`.
3. Reads whether `backend/.env` exists, checks Docker availability, and, when Docker is reachable, inspects the configured controller container. These are advisory, read-only diagnostics; a missing `.env` is acceptable when the process environment supplies configuration. The script does not print configuration values.
4. Starts FastAPI and, unless skipped, Vite as hidden child processes with explicit working directories. Standard output and error go directly to project log files.
5. Waits for `/health`, then requests `/api/v1/agentteams-collaboration/health` and prints only a health status and reason. Its real collaboration preflight is determined only by that endpoint’s `ready_for_demo` result, not by `.env`, Docker, or controller diagnostic presence.

It prints the URLs, launched PIDs, log locations, the independent smoke command, and exact `Stop-Process` commands. Use the PIDs printed by that run; do not broadly terminate all Python, Node, or Docker processes.

If collaboration health is not ready, the script prints a blocking warning and the smoke command. A missing `.env`, unavailable Docker, or an unconfirmed controller is advisory only; process environment configuration can still yield a ready collaboration health result. The backend or UI being available is not a claim that AgentTeams is connected. The frontend is UI-only: it renders persisted server state and cannot independently prove Matrix connectivity.

## Formal-environment service order

For a managed environment, bring components up in this order and verify each layer before proceeding:

1. Existing Matrix homeserver and target room.
2. Existing AgentTeams controller associated with that Matrix environment.
3. Manager and at least two distinct Worker accounts, with room membership and the expected AgentTeams runtimes.
4. Platform database with a Team, its leader, and Worker-to-Matrix runtime-reference bindings.
5. Platform backend with the collaboration runner enabled. The runner owns outbox delivery, Matrix sync, receive validation, task expiry, and durable audit records. The demo script requires the existing initialized `backend/dwp.db`; it does not seed it. Backend startup still runs its normal `create_all` and schema-compatibility routines against that existing database, so schema initialization or migration approval is separate from this script’s no-reseed guarantee.
6. Frontend, if operators want visual inspection. It is not a runtime dependency of the runner.

Do not introduce a second Matrix service for this demo. If Matrix or a controller must be provisioned or changed, that is an environment operation outside this runbook and needs the corresponding owner’s approval.

## Health endpoint

Inspect the endpoint after backend `/health` succeeds:

```text
GET /api/v1/agentteams-collaboration/health
```

| Field | Meaning |
|---|---|
| `enabled` | Whether the collaboration runner feature is enabled in this backend process. |
| `ready_for_demo` | Submission gate. It is true only when every required readiness condition below is met. |
| `reason` | Semicolon-separated non-secret reasons why the gate is blocked. |
| `matrix` | `ready` only after the Matrix gateway’s room check succeeds; otherwise disabled, not configured, or unavailable. |
| `room` | `ready` only after the configured room check succeeds; otherwise disabled, not configured, unchecked, or unavailable/detail. |
| `manager` | `configured` means only that `AGENTTEAMS_MANAGER_MXID` is present. It is **not** a remote Manager connection or liveness assertion. |
| `platform_team` | `ready` when any platform Team record exists. Health has no user-selected team; task creation still validates the selected team and leader. |
| `workers` | Stored AgentTeams Worker bindings with `agent_id`, resolved `matrix_user_id`, and `configured`, `missing_runtime_ref`, or `invalid_matrix_user_id` status. `configured` is not remote liveness. |

`ready_for_demo` requires: runner enabled, Matrix `ready`, room `ready`, Manager configured, a platform team, and at least two distinct configured Worker IDs with two distinct resolvable MXIDs. This readiness check intentionally never claims remote proof for Manager or Worker runtime processes.

## Demo caller boundary

All collaboration task routes except the public health check require `actor_no`. In this demo, the backend verifies that it names an active `HumanEmployee`, records that value in `TaskRun.owner_human_no` at creation, and permits list/detail/graph/events/messages/result/cancel/retry only to that stored creator. Creation also reads the selected Team server-side and requires `leader_agent_id` to equal its designated `leader_employee_id`; a submitted Team member cannot replace the leader. Existing task rows with no recorded owner are intentionally inaccessible through these routes because their creator cannot be verified.

This is **demo request isolation, not production authentication**: `actor_no` is selected by the demo UI and is not a credential. A formal deployment must have login/authentication middleware validate and inject the caller identity, then apply a durable Team ACL/role policy. Do not treat a freely supplied query parameter as proof of identity in production.

## Logs, tasks, and correlation

The demo script writes these files under the project:

- `backend/agentteams-backend.out.log` and `backend/agentteams-backend.err.log`
- `frontend/agentteams-frontend.out.log` and `frontend/agentteams-frontend.err.log` when the frontend is started

Use the persisted collaboration APIs or the AgentTeams page to correlate evidence:

| Identifier | Where it starts / how to follow it |
|---|---|
| Task ID | The root `TaskRun` ID returned by task creation. It has the `AT-` prefix and is the URL parameter for task, graph, event, message, and result endpoints. |
| Trace ID | The root creation code sets it to the Task ID; child nodes retain that trace ID. Use it to group a root and all descendants. |
| Matrix Event ID | Stored on durable `AgentMessage` records after successful outbound delivery or accepted inbound receipt. The UI’s Matrix message log displays it as “Matrix event ID”. |
| Event sequence | `AgentTaskEvent.event_seq` is append-only per root task and is the ordering evidence for the timeline. |

For an API investigation, use `/tasks/{task_id}`, `/tasks/{task_id}/graph`, `/tasks/{task_id}/events`, `/tasks/{task_id}/messages`, and `/tasks/{task_id}/result` under the collaboration API prefix. Avoid putting raw Matrix message bodies or `.env` contents in an incident ticket; use Task ID, Trace ID, event sequence, event type, and Matrix event ID instead.

## Troubleshooting

| Symptom | Check and safe response |
|---|---|
| Matrix login not ready | Confirm the required Matrix configuration names are present without printing values. Check backend error log and the existing Matrix account outside this repository. The health reason will say Matrix login did not succeed. |
| Room not ready | Confirm the configured account has already joined the configured room. Health only calls the room check after Matrix authentication configuration is usable. Do not create a replacement room from this demo script. |
| Manager does not begin delegation | The root task remains in `planning` until its coordinator sends a valid `task.delegate`; it eventually reaches the configured root task timeout. Verify the Manager’s room membership, Matrix sender mapping, and root Task/Trace IDs. A configured Manager field is not proof that it received the task. |
| Worker timeout | An assigned child that does not send `task.accepted` reaches `accept_timeout`; a root that exceeds the total task deadline reaches `task_timeout`. Inspect the worker mapping, room membership, assignment Matrix event ID, and timeline. The runner does not use a builtin Worker fallback. |
| Malformed or unsupported protocol | Inspect the persisted `protocol.invalid` event/message and correct the `DWP_EVENT` envelope. Invalid JSON, unknown types, and unknown versions never produce a task-status transition. |
| Policy denial for a tool request | Inspect the tool audit IDs and decision in the task’s tool audit panel. The platform returns a safe denied `tool.result`; correct permissions or seek the normal approval, rather than bypassing the Gateway. |
| Script reports a blocked preflight | Read only the named backend/frontend logs and endpoint status/reason. The script intentionally does not reveal configuration values. Fix the external prerequisite, rerun the script, then run the explicit smoke check. |
| Script refuses to start because `backend/dwp.db` is missing | Initialize or migrate the database only through a separately approved operation. Do not bypass the check or use this demo script to create data. |

## Tests versus real smoke

Normal backend unit tests force `DWP_AGENTTEAMS_COLLAB_ENABLED=0` in `backend/tests/conftest.py`, so they never start the real Matrix runner. Runner, protocol, API, and smoke-evidence tests use controlled fakes or stored evidence. A real Matrix verification is separate and operator-triggered:

```powershell
cd backend; .\.venv\Scripts\python.exe ..\scripts\agentteams_collaboration_smoke.py
```

That script does not start services. It first requires `ready_for_demo`, submits the fixed collaboration task, waits for a terminal result, and prints minimal evidence only. Run it only against an already-running, authorized environment.
