# AgentTeams Local Demo Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the approved `local_demo` mode so the standalone collaboration page completes a persisted Manager → HR/IT Workers → nested delegation → tool audit → final summary loop without Matrix, Docker, or official AgentTeams.

**Architecture:** Keep `AgentTeamsCollaborationService`, protocol validation, task state, database tables, API projections, and UI polling unchanged as the source of truth. Add a transport factory with two explicit modes: `local_demo` uses a deterministic protocol-speaking local runtime; `agentteams_strict` uses the existing Matrix gateway and never falls back. The local runtime emits the same `DWP_EVENT` envelopes as external agents, so all transitions still pass through the existing service instead of writing task state directly.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, Pydantic, pytest, React, TypeScript, Vitest, PowerShell.

**Spec:** `docs/superpowers/specs/2026-08-28-agentteams-standalone-collaboration-system-design.md`

## Global Constraints

- Work only in `codex/agentteams-collaboration`; do not merge, push, or install official AgentTeams.
- Do not modify the original checkout database or `.env`; use the isolated worktree copy only.
- `local_demo` must be visibly labelled as deterministic local simulation and must never claim Matrix connectivity.
- `agentteams_strict` must preserve the existing Matrix readiness gate and must never silently fall back.
- Local responses must enter through the existing protocol parser and `AgentTeamsCollaborationService.apply_message`; the transport must not mutate task models directly.
- Tool requests must still pass through Policy → Gateway → Adapter/Runtime → Audit.
- Keep creator ownership isolation, team leader validation, idempotency, cancellation, timeout, and protocol rejection behavior intact.
- Do not commit generated databases, `.env`, logs, virtual environments, or temporary installer files.
- Do not create Git commits during this execution; review the complete uncommitted diff before the user decides how to integrate it.

---

### Task 1: Explicit transport mode and transport factory

**Files:**
- Create: `backend/app/services/agentteams_transport.py`
- Modify: `backend/app/services/config.py`
- Modify: `backend/app/services/agentteams_runner.py`
- Test: `backend/tests/test_agentteams_transport.py`
- Test: `backend/tests/test_agentteams_runner.py`

**Interfaces:**
- Produces: `AgentTeamsTransport` protocol with `send_protocol_message(room_id, text, protocol_line) -> str`, `sync_messages(since, allowed_room_ids) -> MatrixSyncBatch`, and `close() -> None`.
- Produces: `create_agentteams_transport(mode: str | None = None) -> AgentTeamsTransport`.
- Produces: `config.agentteams_mode() -> Literal["local_demo", "agentteams_strict"]` with a safe default of `agentteams_strict`; the demo script explicitly selects `local_demo`.
- Consumes: existing `AgentTeamsGateway`, `MatrixEvent`, and `MatrixSyncBatch` without changing strict-mode behavior.

- [ ] **Step 1: Write failing configuration and factory tests**

```python
def test_agentteams_mode_defaults_to_strict(monkeypatch):
    monkeypatch.delenv("DWP_AGENTTEAMS_MODE", raising=False)
    assert config.agentteams_mode() == "agentteams_strict"

def test_factory_selects_local_transport(monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    assert isinstance(create_agentteams_transport(), LocalDemoTransport)

def test_factory_keeps_matrix_transport_strict(monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    assert isinstance(create_agentteams_transport(), AgentTeamsGateway)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_agentteams_transport.py -q`

Expected: FAIL because the mode function, transport protocol, factory, and local implementation do not exist.

- [ ] **Step 3: Add the minimal mode parser and factory boundary**

```python
DWP_AGENTTEAMS_MODE = "DWP_AGENTTEAMS_MODE"

def agentteams_mode() -> str:
    value = os.environ.get(DWP_AGENTTEAMS_MODE, "agentteams_strict")
    return value if value in {"local_demo", "agentteams_strict"} else "agentteams_strict"
```

`AgentTeamsCollaborationRunner.__init__` must accept a general `transport` while retaining the current `gateway=` test compatibility alias. When neither is injected, it must call `create_agentteams_transport()`.

- [ ] **Step 4: Run focused runner/factory tests and verify GREEN**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_agentteams_transport.py tests/test_agentteams_runner.py -q`

- [ ] **Step 5: Review checkpoint**

Inspect the diff and verify strict mode still constructs the existing Matrix gateway and no production path silently selects local mode.

---

### Task 2: Deterministic protocol-speaking LocalDemoTransport

**Files:**
- Create: `backend/app/services/agentteams_local_demo.py`
- Modify: `backend/app/services/agentteams_transport.py`
- Test: `backend/tests/test_agentteams_local_demo.py`

**Interfaces:**
- Produces: `LocalDemoTransport`, a stateful in-process transport implementing `AgentTeamsTransport`.
- Consumes: `AgentProtocolMessage`, `parse_protocol_lines`, `render_protocol_line`, `MatrixEvent`, and `MatrixSyncBatch`.
- Behavior: each outbound protocol message is acknowledged once by idempotency key; local event IDs and cursors are monotonic; cancellation produces no new work.

- [ ] **Step 1: Write failing tests for queue, cursor, and idempotency**

```python
def test_submit_enqueues_manager_delegate():
    transport = LocalDemoTransport()
    event_id = transport.send_protocol_message("local-demo", "submit", render_protocol_line(submit))
    batch = transport.sync_messages(None, {"local-demo"})
    assert event_id.startswith("local-out-")
    assert [parse_event_type(e) for e in batch.events] == ["task.delegate"]
    assert batch.next_cursor == "local-1"

def test_retried_outbound_is_idempotent():
    transport = LocalDemoTransport()
    transport.send_protocol_message("local-demo", "submit", render_protocol_line(submit))
    transport.send_protocol_message("local-demo", "submit", render_protocol_line(submit))
    assert len(transport.sync_messages(None, {"local-demo"}).events) == 1
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_agentteams_local_demo.py -q`

Expected: FAIL because `LocalDemoTransport` does not exist.

- [ ] **Step 3: Implement the minimal deterministic scenario engine**

The local engine reacts only to validated outbound protocol envelopes:

```text
task.submit(root -> VE-0001)
  => VE-0001 task.delegate(hr-policy -> VE-0002, it-account -> VE-0003)

task.assign(hr-policy -> VE-0002)
  => task.accepted
  => task.progress(40)
  => agent.message(VE-0002 -> VE-0003)
  => task.delegate(hr-it-check -> VE-0003)

task.assign(it-account -> VE-0003)
  => task.accepted
  => task.progress(60)
  => tool.request(work-record-query/read -> platform)

task.assign(hr-it-check -> VE-0003)
  => task.accepted
  => task.progress(80)
  => task.result

tool.result(platform -> VE-0003)
  => task.result for it-account

task.summary request for HR node
  => VE-0002 task.summary with material check and IT clarification result

task.summary request for root
  => VE-0001 task.summary with completed items, risks, and next actions
```

Every response must use a valid `AgentProtocolMessage`, preserve root/task/parent/trace fields, and use deterministic Chinese summaries. Sender MXIDs must resolve to the existing demo runtime references (`dwp-ve-0001`, `dwp-ve-0002`, `dwp-ve-0003`) under the configured Matrix domain, even though no network call occurs.

- [ ] **Step 4: Add failure/edge tests**

Cover malformed outbound protocol, duplicate outbound idempotency keys, empty queue cursor advancement, unknown outbound types, and `task.cancel` not producing new local work.

- [ ] **Step 5: Run local transport tests and verify GREEN**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_agentteams_local_demo.py tests/test_agentteams_transport.py -q`

- [ ] **Step 6: Review checkpoint**

Verify that the local transport never imports SQLAlchemy models, never writes database state, never calls Matrix, and never labels a local event as remotely delivered.

---

### Task 3: Mode-aware health, lifecycle, API projection, and full backend loop

**Files:**
- Modify: `backend/app/routers/agentteams_collaboration.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/agentteams_runner.py`
- Test: `backend/tests/test_agentteams_collaboration_api.py`
- Test: `backend/tests/test_agentteams_local_demo_e2e.py`

**Interfaces:**
- Produces: health fields `mode`, `transport`, and `matrix` where local mode returns `mode="local_demo"`, `transport="local"`, `matrix="not_connected"`.
- Strict mode readiness remains Matrix/room/Manager/two configured Worker based.
- Local mode readiness requires the feature enabled, the selected platform seed team, its designated leader, and the two demo Workers; it does not require Matrix credentials or Docker.

- [ ] **Step 1: Write failing health-mode tests**

```python
def test_local_demo_health_is_ready_without_matrix(client, monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_COLLAB_ENABLED", "1")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    body = client.get("/api/v1/agentteams-collaboration/health").json()
    assert body["ready_for_demo"] is True
    assert body["mode"] == "local_demo"
    assert body["transport"] == "local"
    assert body["matrix"] == "not_connected"

def test_strict_health_still_blocks_without_matrix(client, monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    assert client.get("/api/v1/agentteams-collaboration/health").json()["ready_for_demo"] is False
```

- [ ] **Step 2: Run health tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_agentteams_collaboration_api.py -q`

- [ ] **Step 3: Implement mode-aware health and lifecycle selection**

Local readiness must be derived from database Team/TeamMember/DigitalEmployee records. Do not reuse Matrix runtime liveness wording for local mode. Keep `source="agentteams"` for compatibility but return the explicit transport mode in health and page context.

- [ ] **Step 4: Write the full-loop failing test**

Create a root task through the API/service, construct the normal runner via the local factory, tick until terminal with a bounded maximum, then assert:

```python
assert run.status == "completed"
assert graph has root + HR + IT + nested IT node
assert events include task.delegate, task.accepted, task.progress, agent.message,
    task.summarizing, task.summary, and a tool result/denial audit event
assert messages include inbound and outbound local transport event IDs
assert final summary contains completed items, risk, and next action
```

- [ ] **Step 5: Run the test and verify RED before completing integration**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_agentteams_local_demo_e2e.py -q`

- [ ] **Step 6: Implement only the integration needed to pass**

Preserve caller ownership, server-designated leader validation, cancellation, timeout, and strict-mode error behavior.

- [ ] **Step 7: Run backend AgentTeams suites and verify GREEN**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_agentteams_protocol.py tests/test_agentteams_collaboration.py tests/test_agentteams_runner.py tests/test_agentteams_collaboration_api.py tests/test_agentteams_local_demo.py tests/test_agentteams_local_demo_e2e.py -q`

- [ ] **Step 8: Review checkpoint**

Review the full backend diff for direct state mutation, identity bypass, accidental Matrix fallback, unbounded loops, secrets, and non-deterministic ordering.

---

### Task 4: Demo script, visible UI boundary, docs, and live isolated verification

**Files:**
- Modify: `scripts/run_agentteams_demo.ps1`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.tsx`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`
- Modify: `docs/agentteams/DEMO.md`
- Modify: `docs/agentteams/DEPLOYMENT.md`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_agentteams_demo_script_contract.py`

**Interfaces:**
- Default script behavior: process-scoped `DWP_AGENTTEAMS_MODE=local_demo`; no Docker, Matrix, credential, database reset, or seed creation.
- Optional `-Strict`: process-scoped `DWP_AGENTTEAMS_MODE=agentteams_strict` and the existing Matrix readiness behavior.
- UI badge: `本地Demo（未连接Matrix）` or `真实AgentTeams / Matrix` based only on server health.
- Message event label: local mode uses `Transport事件ID`; strict mode uses `Matrix事件ID`.

- [ ] **Step 1: Write failing script and UI tests**

Assert that the default script sets local mode, `-Strict` selects strict mode, and the page renders the correct mode badge without claiming Matrix is connected.

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_agentteams_demo_script_contract.py -q`

Run: `pnpm --dir frontend test -- --run src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

- [ ] **Step 3: Implement script/UI/docs changes**

Remove official AgentTeams container checks from the default local path. Keep them advisory/read-only only under `-Strict`. Documentation must show both modes and must state that local Agent outputs are deterministic Demo data while orchestration, protocol validation, persistence, Gateway policy/audit, API, and UI are real platform code.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the two commands from Step 2 again.

- [ ] **Step 5: Run full verification**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
cd ..
pnpm --dir frontend test -- --run
pnpm --dir frontend build
```

- [ ] **Step 6: Start the isolated demo**

Run `./scripts/run_agentteams_demo.ps1`, verify backend health, collaboration health, and the page. Submit the fixed onboarding scenario and collect Task ID, node count/depth, ordered event types, local transport event IDs, tool audit decision, and final summary without exposing `.env` values.

- [ ] **Step 7: Final review checkpoint**

Review the complete uncommitted diff against the spec. Confirm no generated files are tracked and report baseline-only test failures separately from regressions.
