# AgentTeams collaboration live demonstration

This script demonstrates one persisted, multi-Agent collaboration loop. It does not use local mock task generation, does not claim that the frontend is proof of connection, and does not replace an unavailable Worker with the builtin orchestrator.

## Required offline prerequisite

Complete the offline checks before requesting access to a Matrix room or starting any live service:

```powershell
# Parse/contract check only; it does not execute the startup script.
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_agentteams_demo_script_contract.py -q
```

The normal backend test setup forces `DWP_AGENTTEAMS_COLLAB_ENABLED=0`, so unit tests do not start a real Matrix runner. Review [DEPLOYMENT.md](DEPLOYMENT.md) and confirm the existing Matrix/AgentTeams environment, team bindings, Manager, and two Worker prerequisites with the environment owner. Do not begin the live sequence until the collaboration health endpoint says the submission gate is ready.

## Fixed task

Open `http://127.0.0.1:5173/agentteams-collaboration` (or choose **Agent协作演示** in the sidebar). In **提交协作任务**, select the prepared team, enter the title `新员工入职准备复核`, leave supplemental material empty or use an empty JSON object, and enter this task description exactly:

```text
让主Agent完成新员工入职准备复核：
1. HR Agent核查制度和材料；
2. IT Agent核查账号与服务状态；
3. 其中一个Agent向另一个Agent发起一次澄清或继续委派；
4. 主Agent汇总完成项、风险和后续动作。
```

Click **提交协作任务**. Record the displayed Task ID and use it as the trace reference for the rest of this demonstration. Do not copy `.env` content, credentials, endpoint values, or raw Matrix protocol bodies into slides or tickets.

## Live sequence and evidence

The page polls persisted server state while a task is non-terminal. Operators observe the page; the existing Manager and Workers produce their Matrix protocol messages. The UI has no button to forge a delegation, acceptance, or tool result.

| Stage | Required actor action | UI action | Expected durable/UI evidence |
|---|---|---|---|
| 1. Submit | Platform creates the root and queues `task.submit` to the selected leader. | Select the new task in **协作任务记录**. | Root node starts in `planning`; its title, Task ID, coordinator, and assignee appear. After delivery, **Matrix 消息日志** has a `task.submit` record with a Matrix event ID. |
| 2. Delegate | Manager sends one valid `task.delegate` with two required children: HR materials/policy and IT account/service. | Keep the root selected; wait for the automatic refresh. | Task tree adds both child nodes in `assigned`; timeline includes `task.delegate`, `task.waiting_children`, and `task.assigned`; message log shows the persisted assignments. |
| 3. Accept | HR and IT Workers each send `task.accepted` for their own child. | Select each child in the tree. | Each node status becomes `accepted`; timeline identifies the correct worker and target task. |
| 4. Progress | Each Worker sends at least one `task.progress` with an integer progress value. | Observe the selected child’s progress bar and **事件时间线**. | Status becomes `running` on first progress, percentage changes, and a `task.progress` event appears with a monotonically increasing event sequence. |
| 5. A2A clarification | One Worker sends an `agent.message` to the other Worker asking a scoped clarification. | In **Matrix 消息日志**, select the relevant child task. | A protocol `agent.message` is visible with sender and receiver; the task status does not change merely because of this message. |
| 6. Nested continuation | The HR Worker, while its node is running, sends a valid `task.delegate` for a narrow follow-up child assigned to the other configured Worker. The follow-up returns `task.accepted`, `task.progress`, and `task.result`; HR then answers the platform’s requested `task.summary`. | Select the HR node, then the new depth-2 child. | Tree contains a depth-2 node, the parent temporarily shows `waiting_children`/`summarizing`, and the timeline records its nested delegation and summary sequence. This is the continuation alternative required by the fixed task; it can coexist with the A2A clarification above. |
| 7. Tool audit | A Worker sends one allowed `tool.request` to `platform` for a read-only or otherwise authorized action. | Select that Worker node and open **工具审计**. | The UI shows the platform’s `tool.result`, policy decision, and audit IDs. A denial is valid evidence too; it must be shown as a safe denied result, not retried around policy. |
| 8. Worker results | HR, IT, and any required nested child send valid `task.result` messages. | Inspect each node in the tree. | Each result node reaches `completed`, has 100% progress, and shows that a server-side controlled result was saved. |
| 9. Root summary | Once all required root children are complete, the platform requests `task.summary`; Manager returns the root `task.summary` with completed items, risks, and next actions. | Select the root after it becomes terminal and inspect **最终汇总**. | Root status is `completed`; final summary is non-empty; timeline shows `task.summarizing` and `task.summary`; Matrix log contains Manager’s inbound summary with a Matrix event ID. |

The UI deliberately renders only persisted task nodes, events, messages, result summaries, and tool audit fields. It is therefore valid evidence of platform persistence and correlation, but not a substitute for a real Matrix smoke proof.

## Independent smoke evidence

After the UI sequence has been accepted by the environment owner, run the separate, operator-triggered smoke check against the already-running backend:

```powershell
cd backend; .\.venv\Scripts\python.exe ..\scripts\agentteams_collaboration_smoke.py
```

It submits its fixed code-defined onboarding scenario and prints only minimal JSON evidence: Task ID, participating Worker/Manager IDs, Matrix event IDs, final summary, and missing criteria. It first requires `ready_for_demo`; it does not start the backend, frontend, Matrix, or AgentTeams services.

For safe evidence collection, retain only:

- the Task ID and Trace ID;
- the ordered event types/sequences and non-sensitive node statuses;
- participant IDs and Matrix event IDs;
- safe tool decision/audit IDs and the final summary; and
- relevant line ranges from the backend/frontend log files, after checking they contain no secrets.

Do not screenshot or paste `backend/.env`, raw login responses, credentials, private endpoints, full tool request parameters, or unsanitized Matrix body text.

## Failure demonstration: no fallback

Use a separate task and one of these environment-approved faults:

1. Stop one configured Worker before it accepts its assigned child; or
2. Assign a child to a configured-but-unavailable Worker identity that the external environment owner prepared for this test.

Submit the same fixed task, select the affected child, and wait for the configured acceptance deadline. Expected evidence is an explicit `task.timed_out` event with `accept_timeout` for the unaccepted child. If the root cannot complete because its required child timed out, it later reaches the configured root `task_timeout`. The tree and timeline must show the timeout state and the Worker/task correlation.

The expected result is **not** successful completion and **not** an invisible handoff to the platform’s builtin Team path. Do not use the UI retry button to mask the failure before collecting the timeout evidence. Restore the external Worker only after the failure evidence has been recorded, then create a new task for a recovery demonstration.
