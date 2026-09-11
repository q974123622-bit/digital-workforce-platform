# AgentTeams collaboration demonstration

This script demonstrates one persisted, multi-Agent collaboration loop in two explicit modes. The default `local_demo` mode uses deterministic local Agent outputs and does not connect to Matrix. `-Strict` selects `agentteams_strict`, which uses the existing real Matrix path and never falls back to local execution. The page displays the mode reported by server health; it never infers connectivity in the browser.

| Mode | Command | Agent output | Message evidence |
|---|---|---|---|
| Local demo (default) | `.\scripts\run_agentteams_demo.ps1` | Deterministic onboarding Demo data | `Transport事件ID`; not Matrix evidence |
| Strict AgentTeams | `.\scripts\run_agentteams_demo.ps1 -Strict` | Existing external Manager/Workers | `Matrix事件ID` after real delivery |

The local mode does not require or use Docker, Matrix, credentials, an AgentTeams controller, or remote Workers; the dedicated script does not probe or output those values in local mode. It does require the existing initialized database and fixed seed records: `TEAM-ONBOARD`, Leader `VE-0001`, and Workers `VE-0002` / `VE-0003`. The script does not reset or seed the database. The page pins the local submission form to that team instead of relying on team-list order.

Local Agent business text is deterministic Demo output for one fixed onboarding script, not general multi-Agent reasoning. Task orchestration, protocol validation, task/message/event persistence, Gateway policy and audit, API routes, UI polling, task tree, timeline, and final-result projection all run through real platform code.

## Required offline prerequisite

Complete the offline checks before requesting access to a Matrix room or starting any live service:

```powershell
# Parse/contract check only; it does not execute the startup script.
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_agentteams_demo_script_contract.py -q
```

The normal backend test setup forces `DWP_AGENTTEAMS_COLLAB_ENABLED=0`, so unit tests do not start a runner. Review [DEPLOYMENT.md](DEPLOYMENT.md). For local mode, confirm the fixed seed records; for strict mode, also confirm the existing Matrix/AgentTeams environment, team bindings, Manager, and two Worker prerequisites with the environment owner. Do not submit until the collaboration health endpoint says the gate is ready.

## Start the selected mode

From the repository root, use the default credential-free path:

```powershell
.\scripts\run_agentteams_demo.ps1
```

Confirm the page badge says **本地Demo（未连接Matrix）**. To use a prepared real environment instead:

```powershell
.\scripts\run_agentteams_demo.ps1 -Strict
```

Confirm the page badge says **真实AgentTeams / Matrix**. A strict preflight failure is blocking and must not be represented as a successful local run.

## Fixed task

Open `http://127.0.0.1:4173/agentteams-collaboration` (or choose **Agent协作演示** in the sidebar). In local mode the form fixes `TEAM-ONBOARD`, title, instruction, and empty material; those fields are read-only because input does not drive general Agent reasoning. In strict mode, select the prepared team and enter the required task normally. The canonical local title is `新员工入职准备复核`, material is `{}`, and instruction is:

```text
请协作完成新员工入职准备复核：HR核查制度和材料，IT核查账号与服务状态，至少一次跨Agent澄清或继续委派，最后汇总完成项、风险和后续动作。
```

Click **提交协作任务**. The local API rejects any different title, instruction, or material with HTTP 409 and creates no task; strict mode remains free-form. Record the displayed Task ID and use it as the trace reference for the rest of this demonstration. Do not copy `.env` content, credentials, endpoint values, or raw Matrix protocol bodies into slides or tickets.

## Live sequence and evidence

The page polls persisted server state while a task is non-terminal. In local mode, the local transport emits deterministic protocol-valid Demo responses for the fixed Manager/Workers; these are not external runtime or Matrix evidence. In strict mode, the existing external Manager and Workers produce real Matrix protocol messages. The UI has no button to forge a delegation, acceptance, or tool result.

| Stage | Local deterministic response / strict external response | UI action | Expected durable/UI evidence |
|---|---|---|---|
| 1. Submit | Local: local transport accepts `task.submit`. Strict: Matrix delivers it to the external Manager. | Select the new task in **协作任务记录**. | Root starts in `planning`; delivery is labelled Transport event ID (local) or Matrix event ID (strict). |
| 2. Delegate | Local: deterministic Manager response delegates HR and IT children. Strict: external Manager sends the same valid `task.delegate`. | Keep the root selected; wait for refresh. | Both children appear in `assigned`; delegation/waiting/assigned events persist. |
| 3. Accept | Local: deterministic HR and IT responses accept their children. Strict: external Workers send `task.accepted`. | Select each child. | Each node becomes `accepted`; worker/task correlation is visible. |
| 4. Progress | Local: deterministic Workers emit bounded integer progress. Strict: external Workers send real progress messages. | Observe progress and **事件时间线**. | Nodes become `running`; percentages and monotonically ordered `task.progress` events persist. |
| 5. A2A clarification | Local: deterministic local Worker response emits the fixed `agent.message`. Strict: one external Worker messages the other over Matrix. | Select the relevant child in the message log. | Sender/receiver persist; the message alone does not change task status. |
| 6. Nested continuation | Local: deterministic HR response delegates and completes the fixed depth-2 continuation. Strict: external HR/IT Workers perform the valid nested delegate/accept/progress/result/summary exchange. | Select HR, then the depth-2 child. | The nested node and waiting/summarizing sequence persist. |
| 7. Tool audit | Local: deterministic Worker issues the fixed request through the real Gateway. Strict: external Worker issues an authorized request over Matrix. | Open **工具审计** for that Worker. | Real policy decision, audit IDs, and safe `tool.result` persist in both modes. |
| 8. Worker results | Local: deterministic children return fixed Demo results. Strict: external Workers return their actual results. | Inspect each node. | Result nodes reach `completed`/100% and save server-controlled results. |
| 9. Root summary | Local: deterministic Manager response returns a fixed Demo summary. Strict: external Manager summarizes completed items, risks, and actions. | Inspect **最终汇总** on the root. | Root completes; summary and summarizing/summary events persist with the mode-appropriate ID label. |

The UI deliberately renders only persisted task nodes, events, messages, result summaries, and tool audit fields. It is therefore valid evidence of platform persistence and correlation. A local `Transport事件ID` is not a Matrix event ID and is not a substitute for a real strict-mode Matrix smoke proof.

## Independent smoke evidence

For strict mode only, after the UI sequence has been accepted by the environment owner, run the separate, operator-triggered smoke check against the already-running backend:

```powershell
cd backend; .\.venv\Scripts\python.exe ..\scripts\agentteams_collaboration_smoke.py
```

It submits its fixed code-defined onboarding scenario as actor `E10281` with canonical Leader `VE-0001` and prints only minimal JSON evidence: Task ID, participating Worker/Manager IDs, Matrix event IDs, final summary, and missing criteria. It requires `ready_for_demo=true`, `mode=agentteams_strict`, and `transport=matrix`; it does not start the backend, frontend, Matrix, or AgentTeams services.

For safe evidence collection, retain only:

- the Task ID and Trace ID;
- the ordered event types/sequences and non-sensitive node statuses;
- participant IDs and Matrix event IDs;
- safe tool decision/audit IDs and the final summary; and
- relevant line ranges from the backend/frontend log files, after checking they contain no secrets.

Do not screenshot or paste `backend/.env`, raw login responses, credentials, private endpoints, full tool request parameters, or unsanitized Matrix body text.

## Strict-only failure demonstration: no fallback

This external Worker outage exercise applies only to `agentteams_strict`. Local deterministic transport is not evidence of an external Worker failure and must not be used for this exercise.

Use a separate task and one of these environment-approved faults:

1. Stop one configured Worker before it accepts its assigned child; or
2. Assign a child to a configured-but-unavailable Worker identity that the external environment owner prepared for this test.

Submit the same fixed task, select the affected child, and wait for the configured acceptance deadline. Expected evidence is an explicit `task.timed_out` event with `accept_timeout` for the unaccepted child. If the root cannot complete because its required child timed out, it later reaches the configured root `task_timeout`. The tree and timeline must show the timeout state and the Worker/task correlation.

The expected result is **not** successful completion and **not** an invisible handoff to the platform’s builtin Team path. Do not use the UI retry button to mask the failure before collecting the timeout evidence. Restore the external Worker only after the failure evidence has been recorded, then create a new task for a recovery demonstration.

## Strict real acceptance walkthrough — one task only

This walkthrough is for `agentteams_strict` after the acceptance preflight in
[REAL_MATRIX_ACCEPTANCE.md](REAL_MATRIX_ACCEPTANCE.md) succeeds.  It does not
claim that the current environment has passed: at authoring time the Synapse
image pull is incomplete.  Use exactly this one-line task and no fixed
role-by-role answer text:

```text
评估新员工A是否可以按期入职。
```

The browser/user is `E10281`; submit to the prepared strict `TEAM-ONBOARD`
Leader `VE-0001`.  The observed delegation is a model/runtime decision, not a
UI script.  The three Tool facts are fictional project demo data, whereas
Matrix event IDs, heartbeats, TaskRun persistence, policy/audit rows, and
DeepSeek decision activity are the strict acceptance evidence.

| Step | Actor | Input | Action | Output | Persisted state | Next trigger | Evidence / code location |
|---:|---|---|---|---|---|---|---|
| 1 | User / platform | One-line goal | HTTP task creation validates actor/team/leader and writes root plus `task.submit` outbox. | Task ID / trace ID. | Root `planning`; outbound message pending/sent. | Runner posts to Matrix. | Task API; `agentteams_collaboration.py:create_task`; `agentteams_runner.py:_send_pending`. |
| 2 | Platform Runner / Matrix | Persisted submit | Platform account sends strict `DWP_EVENT` to Manager. | `$` Matrix event ID. | Message records physical delivery; root remains planning. | Manager `run_once`. | `agentteams_runner.py:_outbound_protocol`; `agentteams_agent_runtime.py:run_once`. |
| 3 | Manager | Goal, current-team catalog summaries | Selects/loads onboarding Skill and uses DeepSeek structured plan. | `task.delegate` for independent HR and IT checks; selected Skill event. | Root `waiting_children`; HR/IT child nodes `assigned`. | Platform sends assignments. | `agentteams_agent_runtime.py:_manager_submit`; decision/Skill registry; timeline selected Skill and round. |
| 4 | HR and IT Workers | Their own assignment, parent chain, safe Tool catalog | Each accepts and progresses; no Worker may claim another identity. | `task.accepted`, `task.progress`. | Nodes enter `accepted` then `running`; event sequence grows. | Worker decision actions. | Runtime sender check / `_new_worker_context`; service `_authorize`. |
| 5 | HR / IT | Missing evidence or readiness question | HR may create the bounded Audit child; IT continues independently. | Depth-two `task.delegate` and Audit assignment. | HR waits for child; Audit node is depth 2. | Audit Worker receives assignment. | Service delegation/limits; task tree parent edge and `depth=2`. |
| 6 | HR, IT, Audit | Domain Tool request | Each sends an allowed `tool.request` to platform. Platform applies Gateway policy and calls only its scoped adapter. | Safe `tool.result` with positive audit IDs. | AuditEvents and Tool timeline entries persist; no raw credential/body appears. | Worker resumes from tool evidence. | `_apply_tool_request`; Gateway/Policy/Audit; Tool panel. |
| 7 | Workers / platform | Tool and child evidence | IT/Audit complete; HR combines child result and completes. | `task.result` messages. | Completed nodes progress to 100; root waits until all required children terminal. | Platform asks root summary. | `_apply_result`; `_maybe_request_parent_summary`; tree/timeline. |
| 8 | Manager | Direct-child results, round, limits | Reviews evidence and writes final risk/action summary, or bounded follow-up if evidence is missing. | Root `task.summary`. | Root and TaskRun `completed`; summary is persisted. | Real smoke validates final record. | Runtime manager summary path; `_apply_summary`; final-result API. |
| 9 | Operator verifier | Running strict services | Runs independent Matrix verifier then strict smoke runner. | Safe account/event/task/audit evidence only. | No test-only state is fabricated. | Record PASS or blocker. | `scripts/verify_agentteams_matrix.py`; `scripts/run_agentteams_real_smoke.py`. |

Use the Task ID, event sequence/type, selected Skill IDs, delegation round,
participant IDs, `$` event IDs, and numeric audit IDs as evidence.  Do not
capture raw Matrix bodies, runtime JSON, environment files, credentials, or
unredacted model/service text.

For this real path, the operator command is:

```powershell
backend\.venv\Scripts\python.exe .\scripts\run_agentteams_real_smoke.py
```

It refuses non-strict health, does not downgrade to local mode, has a maximum
600-second poll bound, and emits whitelisted evidence including
`final_summary_present` rather than arbitrary summary text.
