# AgentTeams Manager → Team Leader Implementation Plan

> **For Codex:** Use test-driven development and execute each task in order. Do not commit or push unless the user asks.

**Goal:** Add explicit Manager/Leader/Worker node roles and a coordinator-controlled result acceptance gate without replacing the existing durable task tree, authorization, outbox, or capability gateway.

**Architecture:** Keep one SQL task graph. Treat `coordinator_agent_id` as the reviewer and `assignee_agent_id` as the submitter. A result becomes a review request; acceptance is the only transition that releases dependencies. Rejection creates a linked revision node and rewires downstream dependencies to the latest revision.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest, React/TypeScript, Vitest.

## Task 1: Protocol and state-machine contract

**Files:**
- Modify: `backend/app/services/agentteams_protocol.py`
- Modify: `backend/app/services/agentteams_collaboration.py`
- Test: `backend/tests/test_agentteams_protocol.py`
- Test: `backend/tests/test_agentteams_collaboration.py`

1. Add failing protocol tests for `task.result.submitted`, `task.result.accepted`, and `task.result.rejected`.
2. Add failing service tests proving submission does not complete/unblock, only the coordinator can review, and acceptance releases dependants.
3. Extend protocol literals and `ALLOWED` transitions minimally.
4. Implement review-request outbox and acceptance handling.
5. Run the two target test files.

## Task 2: Rejection and immutable revision history

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/database.py`
- Modify: `backend/app/services/agentteams_collaboration.py`
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_agentteams_collaboration.py`
- Test: `backend/tests/test_database_schema_compatibility.py`

1. Add failing tests for feedback validation, revision linkage, dependency rewiring, latest-child summary gating, and idempotent replay.
2. Add `node_kind` and `revision_of_task_id` with SQLite compatibility migration.
3. Create revision nodes transactionally and preserve the rejected node/output.
4. Ensure summaries and dependency results use accepted latest revisions.
5. Run target tests.

## Task 3: Runtime and local transport review responses

**Files:**
- Modify: `backend/app/services/agentteams_decisions.py`
- Modify: `backend/app/services/agentteams_agent_runtime.py`
- Modify: `backend/app/services/agentteams_local_demo.py`
- Test: `backend/tests/test_agentteams_decisions.py`
- Test: `backend/tests/test_agentteams_agent_runtime.py`
- Test: `backend/tests/test_agentteams_local_demo.py`

1. Add a strict `ResultReviewDecision` schema and bounded review context.
2. Add failing runtime tests for consuming a submitted-result request and emitting accept/reject messages.
3. Route review decisions through the same structured-output validation and reasoning-event path.
4. Make the deterministic local transport accept valid demo results.
5. Run the three target test files.

## Task 4: API and UI visibility

**Files:**
- Modify: `shared-schema/types.ts`
- Modify: `frontend/src/pages/agentteams/TaskTree.tsx`
- Modify: `frontend/src/pages/agentteams/NodeProcessDetails.tsx`
- Test: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

1. Add new statuses, `node_kind`, and `revision_of_task_id` to shared types.
2. Add failing UI assertions for awaiting review, rejected revision, and role labels.
3. Render the new states and links without changing the page layout.
4. Run the focused frontend test.

## Task 5: End-to-end regression and documentation

**Files:**
- Modify: `docs/agentteams/PROTOCOL.md`
- Modify: `docs/agentteams/DEMO.md`
- Test: affected `backend/tests/test_agentteams_*`

1. Update protocol/state documentation with the acceptance boundary.
2. Run focused backend suites and the frontend focused suite.
3. Run `git diff --check` and inspect `git status`.
4. Report exact passing counts and any environment-only limitations; leave changes uncommitted.
