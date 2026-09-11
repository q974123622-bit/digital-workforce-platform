# AgentTeams collaboration deployment and operations

This runbook starts the platform-side collaboration runner in one of two explicit modes. It does **not** create Matrix, AgentTeams, Workers, containers, or database data. It requires an initialized `backend/dwp.db` and disables automatic seed recovery, so it cannot delete or reseed platform data.

| Mode | Selection | External requirements | Failure behavior |
|---|---|---|---|
| `local_demo` | dedicated demo script default | does not require/use Matrix credentials or remote services; script does not probe/output them | fixed local seed readiness is blocking |
| `agentteams_strict` | pass `-Strict` | existing Matrix room, Manager, Workers, and credentials | Matrix readiness is blocking; no local fallback |

Local Agent business outputs are deterministic Demo data. Both modes execute the real platform orchestration, protocol validation, database persistence, Gateway permission/audit path, API, and UI. The page labels local event identifiers `Transport事件ID` and strict identifiers `Matrix事件ID`.

## Prerequisites

Both modes require the existing Python/frontend dependencies, free ports, and an initialized database. Local mode additionally requires the fixed existing records `TEAM-ONBOARD`, Leader `VE-0001`, and Workers `VE-0002` / `VE-0003`; it does not create them and the UI does not depend on team-list order.

The following external prerequisites apply only to `agentteams_strict`:

Before starting the local platform process, confirm all of the following outside this repository:

- An existing Matrix homeserver, a usable collaboration room, and the AgentTeams controller that serves that environment.
- A Manager plus at least two Worker identities that have already joined the intended room. The platform health endpoint checks the room using its configured Matrix account, but it does not remotely prove that Manager or Worker runtimes are alive.
- A team already exists in the platform database. Its leader and at least two AgentTeams digital employees must have distinct, resolvable Matrix runtime references. The Worker records must be `runtime_type=agentteams`; health reports these bindings as `configured`, not as remotely connected.
- An initialized local SQLite database already exists at `backend/dwp.db`. The demo script blocks before startup when this file is absent: database initialization is a separate, explicitly approved operation and is never performed by the script.
- The repository has an existing `backend/.venv/Scripts/python.exe` with the project dependencies and frontend dependencies already installed. The startup script never installs dependencies.
- Ports 8000 and, unless `-NoFrontend` is used, 4173 are free. The script refuses to stop or replace a process using either port. This demo uses 4173 because the target Windows host reserves a system range containing 5173; the script does not change system port rules.

The controller check is intentionally read-only: if Docker is available, the script inspects the configured controller container. It never runs Docker Compose, creates a container, starts a service, or resets platform data.

## Required configuration names

`DWP_AGENTTEAMS_MODE` is non-secret and accepts `local_demo` or `agentteams_strict`. Normal application configuration is fail-closed (`agentteams_strict` when not overridden); only the dedicated demo script opts its own process and inherited children into `local_demo`. Local transport does not require or use Matrix credentials, and the script does not probe or output them. This does not change the application-wide configuration loading boundary. The remaining Matrix names below apply to strict mode.

Set deployment configuration through the normal environment or the ignored `backend/.env`; never commit values. The platform code requires the following names for a live Matrix-and-room readiness check:

| Purpose | Names |
|---|---|
| Transport selection | `DWP_AGENTTEAMS_MODE` — process-scoped `local_demo` by default; `agentteams_strict` with `-Strict`. |
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

For a prepared real Matrix environment, run:

```powershell
.\scripts\run_agentteams_demo.ps1 -Strict
```

The script performs only these checks/actions:

1. Checks the exact `backend/.venv/Scripts/python.exe` path and an existing `backend/dwp.db`. A missing database blocks startup; the script does not initialize a database or seed data.
2. Sets `DWP_AGENTTEAMS_COLLAB_ENABLED=1`, `DWP_SKIP_AUTO_SEED=1`, and the selected `DWP_AGENTTEAMS_MODE` only in its process environment and inherited child processes. The backend therefore retains its normal schema compatibility path but skips `seed_if_empty()`.
3. In default local mode, does not require/use/probe/output Matrix credentials and skips the script's `.env`, Docker, and controller diagnostics. Under `-Strict` only, it reads whether `backend/.env` exists, checks Docker availability, and, when Docker is reachable, inspects the configured controller container. These are advisory, read-only diagnostics. The script does not print configuration values.
4. Checks port 8000 and, unless `-NoFrontend` is used, port 4173 before starting either service. It then starts FastAPI and Vite as hidden child processes with explicit working directories, waits for bounded HTTP readiness on `127.0.0.1:4173`, and treats a failed/early-exited frontend as startup failure. Standard output and error go directly to project log files.
5. Waits for `/health`, then requests `/api/v1/agentteams-collaboration/health` and prints only a health status and reason. Its real collaboration preflight is determined only by that endpoint’s `ready_for_demo` result, not by `.env`, Docker, or controller diagnostic presence.

It prints URLs, launched PIDs, log locations, and exact process-tree stop commands. The frontend command stops the launched wrapper and its Vite child tree. If a later startup step fails, the script invokes the same exact-PID tree cleanup for only the children it created. It prints the independent real-Matrix smoke command only under `-Strict`. Never broadly terminate all Python, Node, or Docker processes.

If collaboration health is not ready, the script prints a blocking warning. Under strict mode, a missing `.env`, unavailable Docker, or an unconfirmed controller is advisory only; process environment configuration can still yield a ready collaboration health result. The backend or UI being available is not a claim that AgentTeams is connected. The frontend is UI-only: it renders persisted server state and cannot independently prove Matrix connectivity.

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
| `mode` | Server-selected `local_demo` or `agentteams_strict`; this drives the page badge. |
| `transport` | `local` or `matrix`. |
| `enabled` | Whether the collaboration runner feature is enabled in this backend process. |
| `ready_for_demo` | Submission gate. It is true only when every required readiness condition below is met. |
| `reason` | Semicolon-separated non-secret reasons why the gate is blocked. |
| `matrix` | `not_connected` in local mode; in strict mode, `ready` only after the Matrix gateway check succeeds. |
| `room` | `ready` only after the configured room check succeeds; otherwise disabled, not configured, unchecked, or unavailable/detail. |
| `manager` | Local: fixed Leader `VE-0001` exists and is active. Strict: `configured` means only that `AGENTTEAMS_MANAGER_MXID` is present, not remote liveness. |
| `platform_team` | Local: fixed `TEAM-ONBOARD` has the canonical Leader/members. Strict: `ready` means some platform Team exists; create still validates the selected team and leader. |
| `workers` | Local: fixed `VE-0002`/`VE-0003` records and bindings are validated for the deterministic script. Strict: statuses describe stored/resolved Matrix bindings; `configured` is not remote liveness. |

In local mode, `ready_for_demo` requires the runner plus the fixed seed team, designated Leader, and two demo Workers; Matrix and room readiness are not required. In strict mode it requires Matrix `ready`, room `ready`, Manager configured, a platform team, and at least two distinct configured Worker IDs with distinct resolvable MXIDs. The strict check intentionally never claims remote liveness for Manager or Worker runtimes.

Local submission is intentionally not general-purpose: the API accepts only canonical `TEAM-ONBOARD` / `VE-0001`, title `新员工入职准备复核`, the documented fixed instruction, and material `{}`. Any difference returns HTTP 409 before a task is created. Strict mode keeps free-form title, instruction, and material.

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
| Transport / Matrix Event ID | Stored on durable `AgentMessage` records. Local IDs prove only local transport processing and render as `Transport事件ID`; strict IDs render as `Matrix事件ID`. |
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

That script does not start services. It first requires `ready_for_demo=true`, `mode=agentteams_strict`, and `transport=matrix`; it submits the fixed collaboration task as actor `E10281` with canonical Leader `VE-0001`, waits for a terminal result, and prints minimal evidence only. Run it only against an already-running, authorized environment.

## Strict real runtime topology and secret boundary

`agentteams_strict` is four separate Windows Agent processes plus the platform
services and one Docker-hosted Synapse.  It is not the default local demo and
does not silently fall back to it.

| Component | Host / owner | Starts it | Responsibility | Secret boundary |
|---|---|---|---|---|
| Synapse | Docker Compose project `dwp-agentteams` | `scripts/start_agentteams_real_demo.ps1` | Private Matrix homeserver on loopback; holds no platform task state. | Registration secret is mounted read-only from ignored `.agentteams-runtime/`; never print it. |
| Bootstrap | Windows Python | Start script invokes `scripts/bootstrap_agentteams_matrix.py` | Prepares secret, creates/loads five accounts and one private room, atomically writes strict runtime config. | `runtime.json` is ignored under `.agentteams-runtime/`; it contains runtime credentials and must not be copied. |
| Backend + Runner | Windows Python / FastAPI | Start script | Authoritative TaskRun/tree/message/event state, identity checks, Gateway/Policy/Audit, strict Matrix outbox/inbox. | Matrix/DeepSeek configuration is process environment or ignored `backend/.env`; health and APIs expose only safe status/evidence. |
| Manager, HR, IT, Audit | Four Windows Python processes | Start script calls `scripts/run_agentteams_agent.py --role manager|hr|it|audit --config ...` | Each owns one Matrix identity, cursor/state file, Skill/LLM decisions, and protocol replies. | Per-Agent Matrix tokens are read from ignored runtime config; DeepSeek key is inherited privately, never logged. |
| Frontend | Windows Node/Vite | Start script unless `-NoFrontend` | Reads persisted API evidence; does not send Matrix protocol messages or certify connectivity. | No Matrix or DeepSeek secret belongs in browser configuration. |

The Compose file pins `matrixdotorg/synapse:v1.117.0`, binds local Synapse to
`127.0.0.1:8008`, and mounts the homeserver/log templates plus the ignored
registration-secret path.  Element documents both the official Docker Hub image
and `ghcr.io/element-hq/synapse` in its [Synapse installation
guide](https://element-hq.github.io/synapse/latest/setup/installation.html).
Use that source documentation when an image pull fails; do not replace it with
an unreviewed mirror.

### Strict startup / stop order

1. Confirm Docker Engine, the existing backend virtual environment, Node/Vite
   if required, and a non-empty local DeepSeek configuration.  The script checks
   presence without printing the value.
2. Set only process-local `DWP_AGENTTEAMS_MODE=agentteams_strict` and
   `DWP_AGENTTEAMS_RUNTIME_CONFIG=.agentteams-runtime/runtime.json`.
3. Prepare the Synapse registration secret; bring up the scoped Compose project;
   wait for loopback Synapse; bootstrap five accounts/private room/runtime JSON.
4. Start backend, then the four named Agent processes, then frontend; retain only
   created PID metadata under ignored `.agentteams-runtime/pids.json`.
5. Use `verify_agentteams_matrix.py` before a real smoke task.  A `whoami`, room,
   send, or sync failure is a nonzero failure and remains strict.
6. Stop only through `scripts/stop_agentteams_real_demo.ps1`.  It verifies each
   recorded PID/worktree and runs `docker compose -p dwp-agentteams ... down`
   without removing the persistent Synapse volume.  Never kill all Python,
   Node, Docker, or Matrix processes.

Current live acceptance status is recorded in
[REAL_MATRIX_ACCEPTANCE.md](REAL_MATRIX_ACCEPTANCE.md).  At this revision the
Docker engine prerequisite exists, but the official Synapse image pull has not
completed; this is not a strict deployment PASS.
