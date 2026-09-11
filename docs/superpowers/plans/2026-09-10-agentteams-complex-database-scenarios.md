# AgentTeams Complex Database Scenarios Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three coordination-dependent database diagnosis scenarios and make new experiments run only those scenarios without changing historical results.

**Architecture:** Extend the immutable scenario catalog with coordination metadata and hidden secondary labels, extend the existing runner with one budgeted Manager review round, and extend deterministic scoring with coordination metrics. Preserve the five legacy fixtures for historical reads while changing only the frontend creation payload.

**Tech Stack:** Python, FastAPI, Pydantic, SQLAlchemy, pytest, React, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-10-agentteams-complex-database-scenarios-design.md`

## Global Constraints

- Existing benchmark records and legacy fixture IDs remain readable.
- New experiments submit only `compound_lock_index`, `conflicting_signals_followup`, and `budgeted_incident_triage`.
- All modes retain identical model, Tool, fixture, and budget configuration.
- Flash uses `thinking=false` and `reasoning_effort=low`.
- No automatic commit or push.

---

### Task 1: Complex fixture contract

**Files:**
- Modify: `backend/app/services/agentteams_database_scenarios.py`
- Modify: `backend/tests/test_agentteams_database_scenarios.py`
- Create: `mock-data/agentteams-benchmark/database/<new-scenario>/*.json`
- Modify: `mock-data/agentteams-benchmark/database/scenarios.jsonl`

**Interfaces:**
- Produces: `DatabaseScenario.complexity`, `DatabaseScenario.tool_budget`, `ExpectedDiagnosis.secondary_causes`, and `ExpectedDiagnosis.requires_followup_signals`.

- [x] Write failing catalog tests that expect eight readable scenarios and literal coordination metadata for the three new IDs.
- [x] Run the focused scenario tests and confirm failure because the new fixtures and fields do not exist.
- [x] Add the three complete five-signal fixture directories and strict schema fields while replacing the fixed catalog-size check with an explicit non-empty bounded check.
- [x] Run the focused scenario tests and confirm they pass.

### Task 2: Coordination-aware deterministic scoring

**Files:**
- Modify: `backend/app/services/agentteams_database_evaluation.py`
- Modify: `backend/tests/test_agentteams_database_evaluation.py`

**Interfaces:**
- Produces: `secondary_cause_hit`, `conflict_resolution_rate`, `effective_rework_rate`, and `budget_success_rate` on `DatabaseBenchmarkMetrics`.

- [x] Write failing literal-result tests for complex success, missing secondary cause, missing follow-up evidence, ineffective duplicate rework, and budget exhaustion.
- [x] Run the focused evaluator tests and confirm the new fields or gates are missing.
- [x] Implement complex-only success gates and the four coordination metrics without changing legacy scenario scoring.
- [x] Run the focused evaluator tests and confirm they pass.

### Task 3: One budgeted Manager review round

**Files:**
- Modify: `backend/app/services/agentteams_database_benchmark_runner.py`
- Modify: `backend/app/services/agentteams_database_benchmark_backend.py`
- Modify: `backend/tests/test_agentteams_database_benchmark_runner.py`
- Modify: `backend/tests/test_agentteams_database_benchmark_backend.py`

**Interfaces:**
- Produces: `ManagerReview` with zero or one authorized follow-up assignment; runner records a second-round Tool result and increments `rework_count` only for new evidence.

- [x] Write failing tests proving full/serial modes can perform required follow-up, uncoordinated mode cannot, and Tool/model budgets fail closed.
- [x] Run the focused runner/backend tests and confirm failure because review is absent.
- [x] Add the structured review provider call and reuse the existing collaboration/Gateway path for the follow-up task.
- [x] Pass observed evidence IDs and scenario budget facts into the evaluator.
- [x] Run the focused runner/backend tests and confirm they pass.

### Task 4: New-only experiment matrix and aggregation

**Files:**
- Modify: `backend/app/services/agentteams_database_benchmark.py`
- Modify: `backend/tests/test_agentteams_database_benchmark_service.py`
- Modify: `backend/tests/test_agentteams_database_benchmark_api.py`

**Interfaces:**
- Produces: aggregation for the four new metrics while preserving old experiment deserialization.

- [x] Write failing tests for 45 new runs and coordination metric aggregation.
- [x] Run focused service/API tests and confirm failure.
- [x] Add aggregation fields and preserve terminal-run resume behavior so old results are never re-executed.
- [x] Run focused service/API tests and confirm they pass.

### Task 5: Frontend new-scenario panel

**Files:**
- Modify: `frontend/src/pages/agentteams/DatabaseBenchmarkPanel.tsx`
- Modify: `frontend/src/pages/agentteams/DatabaseBenchmarkPanel.test.tsx`
- Modify: `shared-schema/types.ts`

**Interfaces:**
- Consumes: new summary metric fields.
- Produces: creation payload with only three complex scenario IDs and a results table that labels coordination metrics.

- [x] Write failing component tests asserting the three-ID payload, 45-run copy, and four coordination metrics.
- [x] Run the focused frontend test and confirm failure against the old five-ID payload.
- [x] Comment the five legacy scenario entries, add the three complex entries, and render the new metrics.
- [x] Run focused frontend tests and confirm they pass.

### Task 6: Regression and runtime verification

**Files:**
- Modify only if a failing regression identifies a scoped defect.

**Interfaces:**
- Produces: verified backend and frontend artifacts with no new experiment started automatically.

- [x] Run all database benchmark backend tests.
- [x] Run the full frontend test suite and production build.
- [x] Run `git diff --check` and inspect scoped changes.
- [x] Restart the existing project services without creating an experiment.
- [x] Verify backend health, collaboration readiness, and frontend HTTP 200.
