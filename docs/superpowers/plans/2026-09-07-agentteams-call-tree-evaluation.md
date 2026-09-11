# AgentTeams Call Tree and Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render persisted AgentTeams work as a readable collaboration call tree and add persisted deterministic plus DeepSeek evaluation for terminal tasks.

**Architecture:** Preserve the existing task/message/event persistence and add a focused evaluation service plus one POST endpoint. Derive the UI tree from existing graph/messages and store one validated evaluation document as an append-only task event.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, DeepSeekProvider, React, TypeScript, Ant Design, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-07-agentteams-call-tree-evaluation-design.md`

## Global Constraints

- Do not add a database table or migration.
- Do not expose credentials or raw authentication data to the evaluator or API response.
- Do not alter AgentTeams task execution or Matrix protocol behavior.
- Do not commit or push this work.

---

### Task 1: Preserve truthful strict Worker health states

**Files:**
- Modify: `backend/app/routers/agentteams_collaboration.py`
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_agentteams_collaboration_api.py`

**Interfaces:**
- Consumes: `_strict_environment(db)` Worker state and `_normalise_health(raw_health)`.
- Produces: Worker status that distinguishes `ready`, `stale_heartbeat`, `unavailable`, and invalid configuration.

- [x] Add a failing API test where a valid MXID has no current heartbeat and assert it is not reported as `invalid_matrix_user_id`.
- [x] Run `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_agentteams_collaboration_api.py -q` and confirm the new test fails on the current normalizer.
- [x] Preserve strict Manager/Worker runtime statuses in `_normalise_health` and generate an Agent-specific readiness reason.
- [x] Re-run the API test and confirm it passes.

### Task 2: Implement deterministic rule evaluation

**Files:**
- Create: `backend/app/services/agentteams_evaluation.py`
- Create: `backend/tests/test_agentteams_evaluation.py`

**Interfaces:**
- Produces: `evaluate_rules(nodes, dependencies, messages, summary) -> RuleEvaluation` with a maximum score of 60 and bounded Chinese findings.

- [x] Add failing tests for completed parallel/serial/follow-up collaboration, missing audits, undelivered messages, and incomplete tasks.
- [x] Run `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_agentteams_evaluation.py -q` and confirm imports/expectations fail.
- [x] Implement the five deterministic dimensions and bounded evidence extraction.
- [x] Re-run the service tests and confirm they pass.

### Task 3: Add validated model evaluation and persistence API

**Files:**
- Modify: `backend/app/services/agentteams_evaluation.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/routers/agentteams_collaboration.py`
- Modify: `backend/tests/test_agentteams_evaluation.py`
- Modify: `backend/tests/test_agentteams_collaboration_api.py`

**Interfaces:**
- Produces: `AgentTeamsEvaluator.evaluate(...) -> dict` and `POST /tasks/{task_id}/evaluation`.
- Persists: one `task.evaluation.completed` event whose `detail` is the public evaluation document.

- [x] Add failing tests for schema-valid model scores, one validation retry, model-unavailable fallback, terminal-only access, owner access, cached replay, Demo provenance and partial-result retry.
- [x] Run the two target backend test files and confirm the new cases fail.
- [x] Implement the sanitized judge prompt, explicit JSON contract, Pydantic validation, fallback document, endpoint, partial/completed event persistence, in-process task lock and cached retrieval.
- [x] Re-run both backend test files and confirm they pass.

### Task 4: Add shared frontend evaluation contract and panel

**Files:**
- Modify: `shared-schema/types.ts`
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/pages/agentteams/EvaluationPanel.tsx`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.tsx`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

**Interfaces:**
- Consumes: the evaluation POST endpoint.
- Produces: rule/model/total scores, dimension reasons, deductions, loading and unavailable states.

- [x] Add failing component/page tests for terminal auto-evaluation, cached result rendering, unavailable model scoring and retry.
- [x] Run the target Vitest file and confirm the new tests fail.
- [x] Add TypeScript contracts, API method, evaluation state/loading, retry action and the right-side evaluation panel.
- [x] Re-run the target frontend tests and typecheck.

### Task 5: Replace relationship cards with a collaboration call tree

**Files:**
- Modify: `frontend/src/pages/agentteams/TaskTree.tsx`
- Modify: `frontend/src/index.css`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.tsx`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

**Interfaces:**
- Consumes: persisted nodes, rounds, dependencies, and messages.
- Produces: one root tree with parallel sibling branches, serial dependency connectors, and Chinese message leaves.

- [x] Add failing UI assertions for tree connectors, mixed parallel/serial layers, chronological message leaves, Chinese protocol labels, selectable Manager root and absence of the old relationship groups.
- [x] Run the target Vitest file and confirm the new assertions fail.
- [x] Implement the compact dependency-layer tree and remove `TaskRelationshipMap` from the page.
- [x] Re-run target tests and typecheck.

### Task 6: Verify code while leaving the live demo stopped

**Files:**
- Modify only if a code defect is reproduced: `scripts/run_agentteams_agent.py` or startup scripts and their existing tests.

**Interfaces:**
- Consumes: existing runtime config and role-specific keys from `backend/.env` without printing them.
- Produces: complete automated verification without claiming a live Matrix connection.

- [x] Respect the user's request not to keep the demonstration environment running.
- [x] Confirm Docker/Matrix live services are stopped and do not claim fresh live connectivity.
- [x] Run complete backend tests, complete frontend tests, typecheck, and `git diff --check`.
- [x] Review the final diff and report exact implemented and remaining boundaries without committing or pushing.
