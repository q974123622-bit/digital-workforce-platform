# AgentTeams 财务报销协作场景 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second, editable finance reimbursement preset and a real Matrix `Manager → Leader → Worker` finance workflow without changing the existing task API.

**Architecture:** Extend the single supported `TEAM-ONBOARD` strict runtime with one finance Leader and three finance Workers, while reusing the audit branch. Add bounded deterministic finance adapters and generate their schemas, runtime catalogs, grants, and Matrix identities from the same checked-in definitions.

**Tech Stack:** React, TypeScript, Ant Design, FastAPI, SQLAlchemy, Pydantic, Python Matrix runtime, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-15-agentteams-expense-reimbursement-design.md`

## Global Constraints

- Keep `TEAM-ONBOARD` and all existing onboarding and role-change behavior.
- Do not connect to a real finance or payment system.
- Keep the existing task create API contract.
- Default to non-thinking mode with `reasoning_effort=low`.
- Manager sees Leaders only; Leaders see direct reports only.
- Tool execution remains behind Policy and Gateway authorization.

---

### Task 1: Frontend task presets

**Files:**
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.tsx`
- Test: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

**Interfaces:**
- Consumes: existing `title` and `instruction` state.
- Produces: a preset dropdown that writes both values and leaves both inputs editable.

- [ ] Add failing Vitest coverage for preset selection, replacement and subsequent manual editing.
- [ ] Run the focused test and confirm failure.
- [ ] Add the title-adjacent Ant Design dropdown and checked-in preset map.
- [ ] Run the focused test and confirm success.

### Task 2: Finance Tool contracts and deterministic adapters

**Files:**
- Create: `mock-data/agentteams/expense-li-ming.json`
- Modify: `backend/app/services/agentteams_runtime_config.py`
- Modify: `backend/app/services/adapters.py`
- Modify: `mock-data/seed.json`
- Test: `backend/tests/test_agentteams_demo_tools.py`

**Interfaces:**
- Produces: `expense_ref_input_schema()`, `expense_payment_input_schema()` and four `mock://agentteams/expense/*` adapters.
- Contract: claim ID `EXP-2026-0915-001`, approval `AUD-EXP-001`, payment `PAY-EXP-001`.

- [ ] Add failing adapter and schema tests, including rejection of unknown fields and missing approval.
- [ ] Run focused tests and confirm failure.
- [ ] Implement schemas, fixture-backed adapters, plugins and grants.
- [ ] Run focused tests and confirm success.

### Task 3: Finance Agent identities, hierarchy and Skills

**Files:**
- Create: `.agents/skills/agentteams-expense-reimbursement/SKILL.md`
- Create: `.agents/skills/finance-expense-coordination/SKILL.md`
- Create: `.agents/skills/expense-invoice-verification/SKILL.md`
- Create: `.agents/skills/expense-budget-verification/SKILL.md`
- Create: `.agents/skills/expense-payment-preparation/SKILL.md`
- Create: `.agents/skills/audit-expense-review/SKILL.md`
- Modify: `scripts/bootstrap_agentteams_matrix.py`
- Modify: `scripts/run_agentteams_agent.py`
- Modify: `scripts/start_agentteams_real_demo.ps1`
- Modify: `backend/app/services/agentteams_runtime_config.py`
- Modify: `backend/app/seed.py`
- Modify: `mock-data/seed.json`
- Test: `backend/tests/test_agentteams_real_demo_scripts.py`
- Test: `backend/tests/test_agentteams_agent_runtime.py`
- Test: `backend/tests/test_agentteams_collaboration_api.py`

**Interfaces:**
- Produces roles `finance`, `finance_invoice`, `finance_budget`, `finance_payment` and agent IDs `VE-FIN-LEAD`, `VE-FIN-INVOICE`, `VE-FIN-BUDGET`, `VE-FIN-PAYMENT`.
- Adds finance Leader to Manager direct reports and finance Workers only to that Leader.

- [ ] Add failing tests for exact roles, relationships, skills, Tool catalogs and health counts.
- [ ] Run focused tests and confirm failure.
- [ ] Add runtime roles, Matrix identities, seed employees, team membership and permissions.
- [ ] Add six focused Skill documents with explicit delegation and evidence rules.
- [ ] Run focused tests and confirm success.

### Task 4: Scenario routing and full verification

**Files:**
- Modify: `scripts/run_agentteams_real_smoke.py`
- Test: `backend/tests/test_agentteams_agent_runtime.py`
- Test: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

**Interfaces:**
- Consumes: finance preset text and runtime catalogs.
- Produces: Manager selection of `agentteams-expense-reimbursement` and a complete finance/audit result chain.

- [ ] Add a failing contract test proving the Manager can select the finance Skill without exposing Workers.
- [ ] Add smoke assertions for finance/audit evidence and the simulated payment result.
- [ ] Run all affected backend and frontend tests.
- [ ] Run TypeScript production build and `git diff --check`.
- [ ] Restart the strict Matrix demo, verify all identities are ready, and execute one finance task to completion.
