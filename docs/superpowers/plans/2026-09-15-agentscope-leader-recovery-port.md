# AgentScope AgentTeams Leader Recovery Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the upstream AgentScope AgentTeams Leader result gate and dynamic replanning loop so Worker failure or rejection returns to the Leader instead of immediately failing the Manager task.

**Architecture:** A source-derived, storage-independent taskflow core is vendored under Apache-2.0. A SQLAlchemy adapter maps that core onto the existing AgentTaskNode graph; the existing Matrix runtime carries authenticated recovery requests and complete Leader replan decisions.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Pydantic v2, Matrix protocol transport, pytest, React/TypeScript/Vitest.

**Spec:** `docs/superpowers/specs/2026-09-15-agentscope-leader-recovery-port-design.md`

## Global Constraints

- Pin imported behavior to AgentScope AgentTeams commit `e9cdf2e416ecaab129b9e85ac4a7a19dd018cb7e` under Apache-2.0.
- Keep SQLAlchemy tables as the only durable task state.
- Do not import Kubernetes, MinIO, CoPaw/QwenPaw lifecycle, or evaluation code.
- Preserve Manager to Leader to Worker authority and direct-report capability validation.
- Validate a complete replan before creating any replacement node.
- Preserve old result, failure, feedback, audit, and replacement history.
- Use TDD for every production behavior.

---

### Task 1: Source-derived taskflow core

**Files:**
- Create: `backend/app/vendor/agentscope_agentteams/__init__.py`
- Create: `backend/app/vendor/agentscope_agentteams/taskflow_core.py`
- Create: `backend/app/vendor/agentscope_agentteams/NOTICE.md`
- Create: `backend/tests/test_agentscope_taskflow_core.py`

**Interfaces:**
- Produces: `TaskResult`, `DagTask`, `result_terminal_status()`, `validate_dag()`, `ready_nodes()`.

- [ ] Write literal behavior tests adapted from upstream `copaw/tests/test_taskflow_tool.py` for result-state mapping, cycles, unknown dependencies, and accepted-only readiness.
- [ ] Run the new test file and verify import/behavior failures.
- [ ] Port the minimal upstream constants, dataclasses, and pure functions with attribution.
- [ ] Run the new test file and verify it passes.

### Task 2: SQLAlchemy taskflow adapter and evidence gate

**Files:**
- Create: `backend/app/services/agentteams_taskflow_adapter.py`
- Modify: `backend/app/services/agentteams_collaboration.py`
- Modify: `backend/tests/test_agentteams_collaboration.py`

**Interfaces:**
- Consumes: source-derived `TaskResult`, `DagTask`, and `ready_nodes()`.
- Produces: `DatabaseTaskflowAdapter.read_result()`, `current_dag()`, `validate_replan()`, `apply_replan()`.

- [ ] Write a failing test proving a submitted node exposes safe Tool evidence to its Leader review bundle.
- [ ] Write a failing test proving an invalid final task in a multi-node replan creates no nodes.
- [ ] Write a failing test proving accepted nodes alone unlock dependencies.
- [ ] Implement the adapter and connect the result bundle builder.
- [ ] Run the focused collaboration tests until green.

### Task 3: Leader recovery decision contract

**Files:**
- Modify: `backend/app/services/agentteams_decisions.py`
- Modify: `backend/app/services/agentteams_agent_runtime.py`
- Modify: `backend/tests/test_agentteams_decisions.py`
- Modify: `backend/tests/test_agentteams_agent_runtime.py`

**Interfaces:**
- Produces: `LeaderRecoveryDecision(action, feedback, tasks)` where action is `accept`, `replan`, or `stop_blocked`.
- Emits: `task.result.accepted`, `task.replan`, or `task.blocked`.

- [ ] Write failing tests for accept, complete-graph replan, invalid assignee rejection, and stop-blocked.
- [ ] Write a failing runtime test proving `task.recovery.requested` reaches the coordinator decision path.
- [ ] Port the upstream Leader result-gate language into the prompt/Skill context and implement the strict output model.
- [ ] Implement the runtime mapping and safe error-stage preservation.
- [ ] Run decision and runtime tests until green.

### Task 4: Failure routing and atomic replanning

**Files:**
- Modify: `backend/app/services/agentteams_collaboration.py`
- Modify: `backend/app/services/agentteams_runner.py`
- Modify: `backend/tests/test_agentteams_collaboration.py`
- Modify: `backend/tests/test_agentteams_runner.py`

**Interfaces:**
- Consumes: authenticated `task.replan` and `task.blocked` messages.
- Produces: immutable failed/revision nodes, replacement graph nodes, ready Matrix assignments, and bounded recovery events.

- [ ] Write a failing test proving required Worker failure does not immediately fail ancestors.
- [ ] Write a failing test proving the coordinator receives an `INTERRUPTED` recovery request.
- [ ] Write a failing test proving duplicate recovery messages are idempotent.
- [ ] Write a failing test proving only Leader `task.blocked` propagates failure upward.
- [ ] Implement source-faithful failure routing and atomic replan application.
- [ ] Run collaboration and runner tests until green.

### Task 5: Local demo and frontend visibility

**Files:**
- Modify: `backend/app/services/agentteams_local_demo.py`
- Modify: `backend/tests/test_agentteams_local_demo.py`
- Modify: `frontend/src/pages/agentteams/TaskTree.tsx`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

**Interfaces:**
- Consumes: recovery/replan events and immutable replacement links.
- Produces: deterministic local recovery flow and human-readable tree statuses.

- [ ] Write failing local-demo and frontend tests for Leader replanning, replacement history, and blocked escalation labels.
- [ ] Implement deterministic local decisions using the same taskflow adapter.
- [ ] Add compact status labels without changing the tree hierarchy or zoom behavior.
- [ ] Run local demo and frontend tests until green.

### Task 6: Integrated verification

**Files:**
- Modify if required by real evidence only: `scripts/run_agentteams_real_smoke.py`
- Modify: `backend/tests/test_agentteams_llm_collaboration_e2e.py`

**Interfaces:**
- Produces: one repeatable real Matrix recovery smoke and regression evidence.

- [ ] Add an E2E assertion for Worker rejection/failure to Leader replan to accepted replacement to Manager continuation.
- [ ] Run the focused backend suites with a workspace-local `--basetemp`.
- [ ] Run the AgentTeams frontend Vitest suite.
- [ ] Restart the real Matrix demo and execute one smoke task.
- [ ] Inspect final task status, graph, events, messages, Tool evidence, and revision links.
- [ ] Run `git diff --check` and inspect the final worktree status.
