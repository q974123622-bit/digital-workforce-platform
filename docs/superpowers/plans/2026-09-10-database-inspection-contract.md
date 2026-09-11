# Database Inspection Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate cross-layer database inspection contract drift and prevent invalid plans from mutating the task graph.

**Architecture:** A database-only shared contract generates runtime validation and public/model schemas. All database planning and execution entry points consume it, while Gateway authorization remains unchanged.

**Tech Stack:** Python 3, Pydantic v2, FastAPI, SQLAlchemy, pytest, DeepSeek JSON structured output

**Spec:** `docs/superpowers/specs/2026-09-10-database-inspection-contract-design.md`

## Global Constraints

- Scope is limited to `TEAM-DB-BENCH` database inspection requests.
- Do not reset data, remove historical runs, run legacy scenarios, or change `TEAM-ONBOARD`.
- Tests precede production changes and must fail for the intended missing behavior.
- Runtime validation is authoritative; model Schema and persisted plugin Schema derive from the same contract.

---

### Task 1: Shared complete-request contract

**Files:**
- Modify: `backend/app/services/agentteams_database_scenarios.py`
- Test: `backend/tests/test_agentteams_database_scenarios.py`

- [x] Add failing table-driven tests for unknown, cross-signal, empty, null, type, length, and unsupported scenario-signal inputs.
- [x] Run the focused tests and confirm failures identify missing strict and combination validation.
- [x] Implement strict filters and complete-request validation from one signal contract.
- [x] Run the focused tests and confirm they pass.

### Task 2: Model-facing planning contract and atomicity

**Files:**
- Modify: `backend/app/services/agentteams_database_benchmark_runner.py`
- Modify: `backend/app/services/agentteams_database_benchmark_backend.py`
- Test: `backend/tests/test_agentteams_database_benchmark_runner.py`
- Test: `backend/tests/test_agentteams_database_benchmark_backend.py`

- [x] Add failing tests for real Manager/Single Agent schemas and for an invalid final assignment causing no child creation/delegation.
- [x] Run focused tests and confirm the open nested filters schema and missing combination validation cause failure.
- [x] Use the shared strict contract in all database-only model decisions and expose the generated contract to actual requests.
- [x] Run focused tests and confirm initial plans and follow-ups validate before graph mutation.

### Task 3: Gateway, Adapter, and persisted plugin contract

**Files:**
- Modify: `backend/app/services/adapters.py`
- Modify: `backend/app/seed.py`
- Modify: `mock-data/seed.json`
- Test: `backend/tests/test_agentteams_database_tool.py`
- Test: `backend/tests/test_seed.py`

- [x] Add failing tests proving all validation entrances agree and repeated sync upgrades only the target plugin idempotently.
- [x] Run focused tests and confirm the persisted schema is stale or permissive.
- [x] Reuse the complete request validator and generate the persisted plugin schema from the shared contract.
- [x] Run focused tests twice and compare normalized persisted and generated schemas.

### Task 4: Regression and real evaluation

**Files:**
- Test: all relevant database benchmark and collaboration tests

- [x] Run the complete relevant backend test set and inspect all failures.
- [x] Restart only the project backend so runtime code and cached resources refresh.
- [x] Run the 9-run smoke for the three new scenarios and verify terminal state, Tool audit evidence, conclusions, and zero unexpected invalid/inspection failures.
- [x] If smoke passes, run the 45-run evaluation for the same three scenarios and report every failed/invalid record without deleting history.
