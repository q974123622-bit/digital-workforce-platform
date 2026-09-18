# AgentTeams Strict Three-Level Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every new AgentTeams task use a real Manager → Leader → Worker hierarchy for both TEAM-ONBOARD and TEAM-DB-BENCH while preserving reviewed results, Matrix transport, Tool Gateway, permissions, and historical tasks.

**Architecture:** Introduce one fail-closed hierarchy contract shared by runtime configuration, decision validation, collaboration persistence, and the database runner. Migrate both checked-in teams to explicit leaders and leaf workers, then let scenario Skills describe required department capabilities without bypassing leaders. Keep the frontend data-driven and render only persisted parent-child nodes.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, Pydantic v2, pytest, Matrix client runtime, React, TypeScript, Vitest, Vite.

**Spec:** `docs/superpowers/specs/2026-09-15-agentteams-strict-three-level-hierarchy-design.md`

## Global Constraints

- Every new AgentTeams task path must be `mission → team_assignment → worker_task`; no new flat Manager → Worker path is accepted.
- Manager delegates only to direct Leaders, Leader delegates only to direct Workers, and Worker cannot delegate.
- Validate an entire delegation batch before creating any task node.
- Scenario Skills declare business capabilities and dependencies, not permission to bypass the hierarchy.
- Existing historical tasks remain readable and are not rewritten.
- Worker results require Leader acceptance; Leader summaries require Manager acceptance.
- Business Tool calls remain behind the existing three-layer permission resolver and Gateway.
- Do not restore the frontend evaluation panel.
- Do not expose Matrix tokens or model API keys in logs, API payloads, tests, or documentation.
- Do not commit or push without explicit user instruction.

---

### Task 1: Shared fail-closed team hierarchy contract

**Files:**
- Create: `backend/app/services/agentteams_hierarchy.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/database.py`
- Create: `backend/tests/test_agentteams_hierarchy.py`
- Modify: `backend/tests/test_database_schema_compatibility.py`

**Interfaces:**
- Consumes: `manager_id: str`, `member_ids: Collection[str]`, and the normalized relationship mapping `{agent_id: {role, reports_to, direct_reports}}` generated from persisted `TeamMember.role` and `TeamMember.reports_to_employee_id`.
- Produces: nullable `TeamMember.reports_to_employee_id`, `TeamHierarchy.from_relationships(...)`, `level_of(agent_id)`, `direct_reports_of(agent_id)`, `validate_delegation(delegator_id, assignee_ids)`, and `project_for(agent_id, capability_catalog)`.

- [ ] **Step 1: Write a failing valid-topology test**

```python
def test_builds_manager_leader_worker_levels():
    hierarchy = TeamHierarchy.from_relationships(
        manager_id="M",
        member_ids={"M", "L", "W"},
        relationships={
            "M": {"role": "Manager", "reports_to": [], "direct_reports": ["L"]},
            "L": {"role": "Leader", "reports_to": ["M"], "direct_reports": ["W"]},
            "W": {"role": "Worker", "reports_to": ["L"], "direct_reports": []},
        },
    )
    assert hierarchy.level_of("M") == "manager"
    assert hierarchy.level_of("L") == "leader"
    assert hierarchy.level_of("W") == "worker"
```

- [ ] **Step 2: Run `python -m pytest backend/tests/test_agentteams_hierarchy.py -q` and verify RED because the module does not exist.**
- [ ] **Step 3: Implement immutable normalized topology construction with exact member-set, reciprocal-edge, depth, non-empty Leader children, leaf Worker, unique-parent, and cycle validation.**
- [ ] **Step 4: Add failing parameterized tests for missing members, Manager → Worker, empty Leader, Worker children, multiple parents, reciprocal mismatch, and cycles; verify each fails with `invalid_team_hierarchy`.**
- [ ] **Step 5: Implement `validate_delegation` so invalid edges raise `manager_must_delegate_to_leader`, `leader_must_delegate_to_worker`, or `worker_cannot_delegate`.**
- [ ] **Step 6: Add and pass projection tests proving a model receives only self, parent, direct reports, roles, and bounded capability lists.**
- [ ] **Step 7: Add a failing SQLite compatibility test, then add the nullable `team_member.reports_to_employee_id` column and migration without rewriting existing rows.**
- [ ] **Step 8: Run the focused hierarchy and schema tests and `git diff --check`; leave changes uncommitted.**

### Task 2: Collaboration service enforces hierarchy before persistence

**Files:**
- Modify: `backend/app/services/agentteams_collaboration.py`
- Modify: `backend/app/services/agentteams_protocol.py`
- Test: `backend/tests/test_agentteams_collaboration.py`
- Test: `backend/tests/test_agentteams_protocol.py`

**Interfaces:**
- Consumes: `TeamHierarchy` from Task 1 and the existing `task.delegate` payload.
- Produces: `_team_hierarchy(...) -> TeamHierarchy` loaded from persisted team members; delegation creates `team_assignment` only below `mission` and `worker_task` only below `team_assignment`; strict Matrix topology must equal the persisted topology.

- [ ] **Step 1: Add a failing service test where a Manager batch contains one valid Leader and one Worker; assert `manager_must_delegate_to_leader` and zero child nodes.**
- [ ] **Step 2: Run the focused test and verify RED because the valid child is currently persisted or the Worker is accepted.**
- [ ] **Step 3: Resolve and validate the complete hierarchy and complete subtask batch before entering the node-creation loop.**
- [ ] **Step 4: Add a failing test where a Leader targets a Worker from another Leader; assert `leader_must_delegate_to_worker` and no new nodes.**
- [ ] **Step 5: Add a failing test where a Worker emits `task.delegate`; assert `worker_cannot_delegate`.**
- [ ] **Step 6: Replace inferred flat compatibility with strict `mission → team_assignment → worker_task` node-kind assignment while retaining legacy graph reads.**
- [ ] **Step 7: Add regression tests proving accepted Worker results unlock Leader summarization and accepted Leader summaries unlock Manager summarization exactly once.**
- [ ] **Step 8: Run `python -m pytest backend/tests/test_agentteams_collaboration.py backend/tests/test_agentteams_protocol.py -q`; leave changes uncommitted.**

### Task 3: Decision engine and runtime respect the same levels

**Files:**
- Modify: `backend/app/services/agentteams_decisions.py`
- Modify: `backend/app/services/agentteams_agent_runtime.py`
- Modify: `.agents/skills/agentteams-manager-coordination/SKILL.md`
- Test: `backend/tests/test_agentteams_decisions.py`
- Test: `backend/tests/test_agentteams_agent_runtime.py`

**Interfaces:**
- Consumes: projected relationship graph and current node kind.
- Produces: Manager plans containing Leader assignees only; Leader work decisions containing direct Worker assignees only; Worker decisions with no delegation action.

- [ ] **Step 1: Add a failing Manager-decision test whose model output assigns a leaf Worker; assert `manager_must_delegate_to_leader`.**
- [ ] **Step 2: Add a failing Leader-decision test whose output assigns another department's Worker; assert `leader_must_delegate_to_worker`.**
- [ ] **Step 3: Add a failing Worker-decision test whose output delegates; assert `worker_cannot_delegate`.**
- [ ] **Step 4: Pass hierarchy level and projected direct-report capabilities into `ManagerContext` and `WorkerContext`, and validate the full output batch through Task 1's contract.**
- [ ] **Step 5: Update prompts so Manager produces department assignments, Leader decomposes work, and Worker chooses among only its authorized Tools.**
- [ ] **Step 6: Preserve structured retry, public reasoning trace, review decisions, thinking-mode toggle, and bounded contexts.**
- [ ] **Step 7: Run `python -m pytest backend/tests/test_agentteams_decisions.py backend/tests/test_agentteams_agent_runtime.py -q`; leave changes uncommitted.**

### Task 4: Migrate TEAM-ONBOARD and both business scenarios

**Files:**
- Modify: `scripts/bootstrap_agentteams_matrix.py`
- Modify: `scripts/start_agentteams_real_demo.ps1`
- Modify: `backend/app/services/agentteams_runtime_config.py`
- Modify: `backend/app/services/agentteams_role_change_scenario.py`
- Modify: `backend/app/seed.py`
- Modify: `mock-data/seed.json`
- Modify: `.agents/skills/agentteams-onboarding-readiness/SKILL.md`
- Modify: `.agents/skills/agentteams-role-change-readiness/SKILL.md`
- Modify: `.agents/skills/hr-role-change-assistant/SKILL.md`
- Modify: `.agents/skills/it-role-change-assistant/SKILL.md`
- Create: `.agents/skills/audit-role-change-worker/SKILL.md`
- Test: `backend/tests/test_agentteams_strict_runtime_config.py`
- Test: `backend/tests/test_agentteams_specialist_seed.py`
- Test: `backend/tests/test_agentteams_role_change_demo.py`
- Test: `backend/tests/test_agentteams_onboarding_operational_contract.py`
- Test: `backend/tests/test_agentteams_real_demo_scripts.py`

**Interfaces:**
- Consumes: strict hierarchy contract from Task 1.
- Produces: nine TEAM-ONBOARD identities including `VE-AUDIT-REVIEW`; reusable HR/IT Workers with scenario-specific capabilities; department-level Manager contracts.

- [ ] **Step 1: Add failing runtime and seed tests asserting the exact nine-member hierarchy and `VE-0005 → VE-AUDIT-REVIEW`.**
- [ ] **Step 2: Add failing role-change tests asserting Manager assigns only `VE-0002`, `VE-0003`, and when required `VE-0005`, while each Leader creates its own Worker tasks.**
- [ ] **Step 3: Add the audit Worker identity, Matrix bootstrap entry, startup role, team member, runtime relationship, professional Skill, and Worker Tool authorization.**
- [ ] **Step 4: Move leaf business Tool capabilities from HR/IT/Audit Leaders to the appropriate Workers while retaining Leader-only coordination capabilities.**
- [ ] **Step 5: Rewrite onboarding and role-change scenario contracts to request department outcomes and business dependencies without naming leaf assignees.**
- [ ] **Step 6: Preserve role-change ordering: IT plan evidence precedes audit review; audit `approval_ref` precedes final activation.**
- [ ] **Step 7: Make seed/runtime synchronization idempotent and additive for managed resources; add a double-sync test.**
- [ ] **Step 8: Run the five focused test files; leave changes uncommitted.**

### Task 5: Migrate TEAM-DB-BENCH to two real Leaders

**Files:**
- Modify: `backend/app/services/agentteams_runtime_config.py`
- Modify: `backend/app/services/agentteams_database_benchmark_runner.py`
- Modify: `backend/app/services/agentteams_database_benchmark_backend.py`
- Modify: `backend/app/seed.py`
- Modify: `mock-data/seed.json`
- Modify: `.agents/skills/agentteams-database-diagnosis/SKILL.md`
- Create: `.agents/skills/database-operations-leader/SKILL.md`
- Create: `.agents/skills/database-performance-leader/SKILL.md`
- Test: `backend/tests/test_agentteams_database_benchmark_runner.py`
- Test: `backend/tests/test_agentteams_database_benchmark_backend.py`
- Test: `backend/tests/test_agentteams_database_benchmark_service.py`
- Test: `backend/tests/test_agentteams_strict_runtime_config.py`
- Test: `backend/tests/test_seed.py`

**Interfaces:**
- Consumes: two Leader IDs `DB-OPS-LEAD`, `DB-PERF-LEAD` and existing five expert IDs.
- Produces: Manager department plan, Leader expert plan, Worker reports, Leader summaries, and unchanged final database diagnosis schema.

- [ ] **Step 1: Add failing topology tests for `VE-0001 → {DB-OPS-LEAD, DB-PERF-LEAD}` and the exact five leaf experts.**
- [ ] **Step 2: Add a failing runner test proving first-level nodes are exactly two `team_assignment` nodes and database checks are `worker_task` grandchildren.**
- [ ] **Step 3: Add Leader identities and team memberships; grant database coordination Skills to Leaders and keep the inspection Tool only on Workers.**
- [ ] **Step 4: Split the existing Manager plan into department assignments and Leader plans, preserving the scenario's signal/filter contract and whole-batch validation.**
- [ ] **Step 5: Preserve budgets by counting only successful Worker inspection calls; include Leader LLM calls in iteration and critical-path metrics.**
- [ ] **Step 6: Preserve full/serial/uncoordinated modes, with uncoordinated disabling cross-Leader review rather than bypassing hierarchy.**
- [ ] **Step 7: Add tests proving invalid signal/filter pairs fail before any Worker node or Tool request is created.**
- [ ] **Step 8: Run the five focused test files; leave changes uncommitted.**

### Task 6: Frontend renders complete persisted hierarchy

**Files:**
- Modify: `frontend/src/pages/agentteams/TaskTree.tsx`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.tsx`
- Test: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

**Interfaces:**
- Consumes: API nodes with `parent_task_id` and `node_kind`.
- Produces: scrollable, uncropped Manager/Leader/Worker tree; legacy flat-tree label; no evaluation panel.

- [ ] **Step 1: Add a failing UI test with one Manager, three Leaders, and five Workers; assert all nine cards and role labels are visible.**
- [ ] **Step 2: Add a failing layout assertion for a wide tree container that permits horizontal overflow instead of clipping cards.**
- [ ] **Step 3: Render levels only from persisted parent links and node kinds; do not synthesize missing Leader nodes.**
- [ ] **Step 4: Label historical direct `mission → worker_task` paths as historical structure without blocking their display.**
- [ ] **Step 5: Assert the removed evaluation panel heading and retry control remain absent.**
- [ ] **Step 6: Run `npm test -- --run src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx` and `npm run build`; leave changes uncommitted.**

### Task 7: End-to-end verification and documentation

**Files:**
- Modify: `docs/agentteams/PROTOCOL.md`
- Modify: `docs/agentteams/DEMO.md`
- Modify: `docs/agentteams/DEPLOYMENT.md`
- Test: affected `backend/tests/test_agentteams_*`

**Interfaces:**
- Consumes: all preceding tasks.
- Produces: verified local Matrix demo, documented topology contract, and exact handoff evidence.

- [ ] **Step 1: Document the strict topology, role responsibilities, error codes, audit acceptance chain, team rosters, and historical-task compatibility.**
- [ ] **Step 2: Run focused backend suites for hierarchy, collaboration, decisions, runtime, onboarding, role-change, database runner, seed, and scripts.**
- [ ] **Step 3: Run the complete backend suite with `python -m pytest backend/tests -q` and record exact pass/skip/fail counts.**
- [ ] **Step 4: Run the complete frontend suite with `npm test`, then `npm run build`.**
- [ ] **Step 5: Restart the strict Matrix demo and verify health reports Matrix connected, room connected, Manager ready, all Leaders ready, all Workers ready, and hierarchy valid.**
- [ ] **Step 6: Submit one new onboarding task and one new role-change task; verify final status, all persisted levels, Tool evidence, Worker → Leader acceptance, Leader → Manager acceptance, and final summary.**
- [ ] **Step 7: Run one database smoke scenario only; verify two Leader nodes, actual expert Tool evidence, valid final result, and no replay of prior evaluation runs.**
- [ ] **Step 8: Run `git diff --check`, inspect `git status --short`, and report unrelated pre-existing files separately; leave all feature changes uncommitted.**
