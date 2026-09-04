# AgentTeams Real Matrix LLM Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a locally runnable AgentTeams demo in which independent Manager, HR, IT, and Audit processes communicate through a real Synapse Matrix room, use DeepSeek for decisions, call governed demo Tools, and expose the whole task tree and evidence trail in the existing page.

**Architecture:** Keep the platform backend as the authoritative task state machine and Tool gateway. Add a shared project-Skill registry and one reusable Agent runtime; four Windows processes instantiate that runtime with separate Matrix accounts and role-specific prompts. Synapse transports protocol messages only, while the platform validates identity, team scope, dependencies, limits, and all state changes.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, Pydantic 2, httpx, pytest, React, TypeScript, Vitest, Docker Compose, Synapse Matrix, DeepSeek OpenAI-compatible API.

**Spec:** `docs/superpowers/specs/2026-09-04-agentteams-real-matrix-llm-agents-design.md`

## Global Constraints

- The standard input is exactly `评估新员工A是否可以按期入职。`; no fixed HR/IT/Audit sequence may be embedded in the page, launcher, or runtime.
- Real demo mode is `agentteams_strict`; it must never fall back to `local_demo` after Matrix, Agent, or DeepSeek failure.
- Matrix uses five identities: platform, VE-0001 Manager, VE-0002 HR, VE-0003 IT, and VE-0005 Audit.
- Business facts remain fictional local demo data; every returned fact must carry `source=demo`.
- Tool execution must pass through the existing Gateway and produce an AuditEvent.
- Maximum depth is 3, maximum children per round is 5, maximum Manager follow-up rounds is 3, maximum nodes per root is 20, and total timeout is 600 seconds.
- Key visible stages use a three-second delay in the real demo; tests inject a zero-second delay.
- Secrets are written only below the gitignored `.agentteams-runtime/` directory and are never printed or returned by an API.
- Do not alter or delete the existing `local_demo`; it remains the deterministic offline regression mode.

---

### Task 1: Project Skill Registry and Three-Layer Skills

**Files:**
- Create: `backend/app/services/agentteams_skills.py`
- Create: `.agents/skills/agentteams-manager-coordination/SKILL.md`
- Create: `.agents/skills/agentteams-onboarding-readiness/SKILL.md`
- Create: `.agents/skills/employee-collaboration/SKILL.md`
- Create: `.agents/skills/hr-onboarding-assistant/SKILL.md`
- Create: `.agents/skills/it-service-assistant/SKILL.md`
- Create: `.agents/skills/audit-evidence-review/SKILL.md`
- Test: `backend/tests/test_agentteams_skills.py`

**Interfaces:**
- Produces: `ProjectSkill(skill_id, name, description, when_to_use, content, path)`.
- Produces: `ProjectSkillRegistry.from_root(root: Path)`, `.catalog(allowed_ids: set[str] | None) -> list[dict[str, str]]`, and `.load(skill_ids: list[str]) -> list[ProjectSkill]`.
- Consumes: repository `.agents/skills/*/SKILL.md` files only; database personal Skills remain untouched.

- [ ] **Step 1: Write failing parsing and boundary tests**

```python
def test_registry_returns_summary_before_full_content(repo_root):
    registry = ProjectSkillRegistry.from_root(repo_root)
    item = next(x for x in registry.catalog() if x["skill_id"] == "agentteams-onboarding-readiness")
    assert set(item) == {"skill_id", "name", "description", "whenToUse"}
    assert "## 完成条件" not in str(item)
    assert "## 完成条件" in registry.load([item["skill_id"]])[0].content

def test_registry_rejects_path_traversal(repo_root):
    registry = ProjectSkillRegistry.from_root(repo_root)
    with pytest.raises(SkillRegistryError, match="unknown_skill"):
        registry.load(["../backend/.env"])
```

- [ ] **Step 2: Run the focused test and confirm failure**

Run: `python -m pytest backend/tests/test_agentteams_skills.py -q`

Expected: collection fails because `agentteams_skills` does not exist.

- [ ] **Step 3: Implement strict frontmatter parsing and immutable catalog entries**

```python
@dataclass(frozen=True)
class ProjectSkill:
    skill_id: str
    name: str
    description: str
    when_to_use: str
    content: str
    path: Path

class ProjectSkillRegistry:
    @classmethod
    def from_root(cls, root: Path) -> "ProjectSkillRegistry":
        skills = parse_skill_directories(root / ".agents" / "skills")
        return cls(skills)

    def catalog(self, allowed_ids: set[str] | None = None) -> list[dict[str, str]]:
        selected = self._filter(allowed_ids)
        return [skill.catalog_entry() for skill in selected]

    def load(self, skill_ids: list[str]) -> list[ProjectSkill]:
        return [self._require(skill_id) for skill_id in skill_ids]
```

The parser requires exactly one skill directory level, `name`, `description`, and `whenToUse`, rejects duplicate IDs, and sorts the catalog by skill ID for deterministic prompts.

- [ ] **Step 4: Write the six Skill files with explicit responsibilities**

Manager coordination says: coarse plan, choose only current-team agents, review direct-child evidence, issue a bounded follow-up round only when evidence is missing, otherwise summarize. Onboarding readiness defines evidence categories and completion criteria without Agent IDs. Worker collaboration distinguishes Tool, Ask, Delegate, and Handoff and forbids delegation to the parent chain. The HR, IT, and Audit execution Skills require Tool evidence and mark unknown data as unknown.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest backend/tests/test_agentteams_skills.py -q`

Expected: all Skill parsing and content-boundary assertions pass.

Commit: `feat: add AgentTeams project skill registry`

---

### Task 2: Typed DeepSeek Decisions and Prompt Builders

**Files:**
- Create: `backend/app/services/agentteams_decisions.py`
- Test: `backend/tests/test_agentteams_decisions.py`

**Interfaces:**
- Consumes: `LLMProvider.structured_output(messages, schema)` and `ProjectSkillRegistry`.
- Produces: Pydantic types `SkillSelection`, `PlannedSubtask`, `ManagerDecision`, `WorkerDecision`, `FinalSummary`.
- Produces: `AgentDecisionEngine.select_skills(context: SkillSelectionContext) -> SkillSelection`, `.plan(context: ManagerContext) -> ManagerDecision`, `.decide_work(context: WorkerContext) -> WorkerDecision`, and `.summarize(context: ManagerContext) -> FinalSummary`.

- [ ] **Step 1: Write failing schema, prompt-safety, and one-retry tests**

```python
def test_manager_plan_is_validated_and_retried_once(fake_llm, registry):
    fake_llm.outputs = [{"subtasks": [{"bad": True}]}, VALID_MANAGER_PLAN]
    decision = AgentDecisionEngine(fake_llm, registry).plan(MANAGER_CONTEXT)
    assert decision.subtasks[0].required_capability == "hr.onboarding.read"
    assert len(fake_llm.calls) == 2
    assert "validation_errors" in fake_llm.calls[1][-1]["content"]

def test_all_llm_prompt_segments_are_demo_sourced(fake_llm, registry):
    AgentDecisionEngine(fake_llm, registry).select_skills(SKILL_CONTEXT)
    assert all(message["source"] == "demo" for message in fake_llm.calls[0])
```

- [ ] **Step 2: Confirm the tests fail**

Run: `python -m pytest backend/tests/test_agentteams_decisions.py -q`

Expected: import error for `agentteams_decisions`.

- [ ] **Step 3: Implement typed decisions and bounded retry**

```python
class PlannedSubtask(BaseModel):
    client_task_key: str
    title: str
    goal: str
    required_capability: str
    assignee_agent_id: str
    depends_on: list[str] = Field(default_factory=list)
    required: bool = True
    reason: str

class ManagerDecision(BaseModel):
    selected_skill_ids: list[str]
    action: Literal["delegate", "summarize", "fail"]
    subtasks: list[PlannedSubtask] = Field(default_factory=list)
    summary: FinalSummary | None = None
```

`AgentDecisionEngine` calls DeepSeek once, validates with Pydantic, sends only validation errors for one retry, then raises `AgentDecisionError("planning_failed")`. The Python layer also rejects unknown selected Skill IDs before full Skill content is loaded.

- [ ] **Step 4: Encode role prompts as data, not scenario scripts**

The Manager prompt receives the user goal, Manager runtime Skill, selected scenario Skill, current-team capability catalog, completed direct-child results, current round, and numeric limits. The Worker prompt receives its task, professional Skill, safe Tool catalog, dependency results, parent-chain Agent IDs, and previous Tool/message results. Neither prompt contains fixed VE assignments or a fixed onboarding answer.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest backend/tests/test_agentteams_decisions.py -q`

Expected: typed parsing, retry count, demo-source, and unknown-Skill tests pass.

Commit: `feat: add typed AgentTeams LLM decisions`

---

### Task 3: State Machine Limits, Blocked Dependencies, and Follow-Up Rounds

**Files:**
- Modify: `backend/app/services/agentteams_collaboration.py`
- Modify: `backend/app/services/config.py`
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_agentteams_collaboration.py`
- Test: `backend/tests/test_agentteams_collaboration_api.py`

**Interfaces:**
- Consumes: existing `AgentProtocolMessage` and persisted node/message/event tables.
- Produces: `_release_ready_dependents(db, completed_node)`, `_safe_child_results(db, parent_id)`, and `_team_capability_catalog(db, team_id)`.
- Produces: task graph fields `current_round`, `selected_skill_ids`, and blocked node status using existing JSON payload/event storage; no database migration.

- [ ] **Step 1: Write failing dependency, limit, and follow-up tests**

```python
def test_dependent_child_is_not_assigned_until_predecessor_completes(db, service):
    root = prepared_root(db, service)
    service.apply_message(db, message("task.delegate", root.id, subtasks=DEPENDENT_PLAN))
    assert child(db, "it-check").status == "blocked"
    assert outbound(db, child(db, "it-check").id, "task.assign") is None
    complete(db, child(db, "hr-check"))
    assert child(db, "it-check").status == "assigned"
    assert outbound(db, child(db, "it-check").id, "task.assign").payload["dependency_results"]

def test_manager_can_delegate_follow_up_from_summarizing(db, service):
    root = summarizing_root(db, service, round_no=1)
    service.apply_message(db, valid_follow_up_delegate(root.id))
    assert root.status == "waiting_children"
    assert root.input_payload["coordination"]["round"] == 2
```

- [ ] **Step 2: Confirm current behavior fails the tests**

Run: `python -m pytest backend/tests/test_agentteams_collaboration.py -q -k "blocked or follow_up or node_limit or cycle"`

Expected: dependent nodes are currently assigned immediately and summarizing cannot transition back to waiting children.

- [ ] **Step 3: Implement atomic validation before state mutation**

Update `ALLOWED` with `blocked -> assigned` and `summarizing -> waiting_children`. Extend service construction with `max_rounds=3` and `max_nodes=20`. Validate team membership, runtime binding, capability match, parent-chain cycles, semantic duplicate key, depth, round, and total-node count before adding children or outbox rows.

- [ ] **Step 4: Delay dependency assignment and pass safe evidence**

Children with no dependencies start as `assigned` and receive `task.assign`. Children with dependencies start as `blocked` and receive only `task.blocked` event. Completion calls `_release_ready_dependents`; when all dependencies are completed, it changes the node to `assigned`, appends `task.unblocked`, and queues one assignment containing redacted `dependency_results`.

- [ ] **Step 5: Include direct-child results in summary requests**

`_maybe_request_parent_summary` sets the parent to `summarizing` only after every required direct child reaches a permitted terminal state and queues `task.summary` with `child_results`, `current_round`, and remaining limits. Required child failure propagates a root failure; optional failure remains visible but does not block summary.

- [ ] **Step 6: Run focused and API tests, then commit**

Run: `python -m pytest backend/tests/test_agentteams_collaboration.py backend/tests/test_agentteams_collaboration_api.py -q`

Expected: existing protocol tests plus blocked/release/follow-up/limit cases pass.

Commit: `feat: support adaptive AgentTeams task rounds`

---

### Task 4: Governed Onboarding Demo Tools and Audit Worker Seed

**Files:**
- Create: `mock-data/agentteams/onboarding-a.json`
- Modify: `backend/app/services/adapters.py`
- Modify: `mock-data/seed.json`
- Test: `backend/tests/test_agentteams_demo_tools.py`
- Test: `backend/tests/test_seed.py`

**Interfaces:**
- Produces Plugin IDs `agentteams-onboarding-employee`, `agentteams-it-readiness`, and `agentteams-onboarding-evidence`.
- Produces endpoints `mock://agentteams/onboarding-employee`, `mock://agentteams/it-readiness`, and `mock://agentteams/onboarding-evidence`.
- Consumes: existing `gateway.invoke_plugin`; no Agent runtime reads Fixture files directly.

- [ ] **Step 1: Write failing Tool governance tests**

```python
def test_agentteams_tool_returns_demo_source_and_writes_audit(db):
    result = invoke_plugin(db, subject_for("VE-0002"), "agentteams-onboarding-employee", {"employee_ref": "新员工A"})
    assert result.output["source"] == "demo"
    assert result.output["employee_ref"] == "新员工A"
    assert latest_audit(db).action == "plugin.invoke"

def test_it_worker_cannot_call_hr_private_tool(db):
    with pytest.raises(HTTPException) as error:
        invoke_plugin(db, subject_for("VE-0003"), "agentteams-onboarding-employee", {"employee_ref": "新员工A"})
    assert error.value.status_code == 403
```

- [ ] **Step 2: Confirm tests fail before registration**

Run: `python -m pytest backend/tests/test_agentteams_demo_tools.py -q`

Expected: plugin or adapter endpoint is unknown.

- [ ] **Step 3: Add one immutable fictional evidence Fixture**

The JSON contains three separate views keyed by employee reference: HR materials, IT account/device readiness, and audit evidence references. It includes one meaningful gap so the final result contains a real model-derived risk rather than an all-green fixed sentence. No secret, real employee, or production endpoint is present.

- [ ] **Step 4: Register adapters, plugins, grants, policies, and VE-0005**

Add VE-0005 with Matrix runtime reference `audit-worker`, add it to `TEAM-ONBOARD`, and grant each worker only its domain Plugin. Adapter handlers validate `employee_ref == "新员工A"`, read only the named Fixture section, and return a bounded structure with `source=demo` and evidence IDs.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest backend/tests/test_agentteams_demo_tools.py backend/tests/test_seed.py -q`

Expected: allowed calls return demo evidence and audit IDs; cross-domain calls are denied and audited.

Commit: `feat: add governed AgentTeams onboarding demo tools`

---

### Task 5: Independent Matrix Agent Runtime

**Files:**
- Create: `backend/app/services/agentteams_agent_runtime.py`
- Create: `scripts/run_agentteams_agent.py`
- Test: `backend/tests/test_agentteams_agent_runtime.py`

**Interfaces:**
- Consumes: `AgentTeamsGateway`, `AgentDecisionEngine`, project Skills, one Agent identity/token, room ID, role, and platform HTTP base URL.
- Produces: `AgentRuntimeConfig`, `AgentRuntime.run_once() -> int`, and `AgentRuntime.run_forever(stop_event)`.
- Produces: protocol responses only through the Agent's own Matrix token.

- [ ] **Step 1: Write failing Manager and Worker runtime tests using fake Matrix and LLM**

```python
def test_manager_converts_submit_into_model_selected_delegate(runtime):
    runtime.gateway.events = [matrix_event_for(TASK_SUBMIT_TO_MANAGER)]
    runtime.run_once()
    sent = protocol_messages(runtime.gateway.sent)
    assert [m.type for m in sent] == ["agent.message", "task.delegate"]
    assert sent[0].payload["kind"] == "skill.selected"
    assert {s["assignee_agent_id"] for s in sent[1].payload["subtasks"]} == {"VE-0002", "VE-0003"}

def test_worker_requests_tool_before_claiming_result(runtime):
    runtime.gateway.events = [matrix_event_for(TASK_ASSIGN_TO_HR)]
    runtime.run_once()
    assert protocol_messages(runtime.gateway.sent)[-1].type == "tool.request"
```

- [ ] **Step 2: Confirm tests fail because runtime is absent**

Run: `python -m pytest backend/tests/test_agentteams_agent_runtime.py -q`

Expected: import error for `agentteams_agent_runtime`.

- [ ] **Step 3: Implement role-neutral receive, filter, dedupe, and cursor handling**

`run_once` syncs the configured room, parses `DWP_EVENT` lines, ignores messages not addressed to its logical Agent ID, validates the platform or peer sender MXID map, deduplicates by Matrix Event ID and protocol idempotency key, processes messages in timestamp/event order, sends responses, then atomically writes the next sync cursor to `.agentteams-runtime/state/<agent-id>.json`.

- [ ] **Step 4: Implement Manager behavior with actual model decisions**

On `task.submit`, Manager sends accepted/progress, selects scenario Skills, logs `skill.selected`, and asks `AgentDecisionEngine.plan`. On `task.summary` request, Manager reviews child results and either sends a follow-up `task.delegate`, a final `task.summary`, or `task.failed`. It never reads the platform database.

- [ ] **Step 5: Implement Worker behavior and adaptive collaboration**

On `task.assign`, Worker accepts, selects execution Skills, and asks `decide_work`. Actions map to protocol messages: Tool to `tool.request`, Ask to `agent.message`, Delegate to `task.delegate`, Complete to `task.result`, and Fail to `task.failed`. A Tool result or child-summary request re-enters decision with accumulated evidence. Completion without required Tool evidence is rejected locally.

- [ ] **Step 6: Add bounded retry and observable pacing**

DeepSeek calls retry once. Each visible phase invokes an injected `pause(seconds)`; production uses the configured three seconds and tests pass `lambda _: None`. Runtime errors become `task.failed` with safe error codes and no stack trace or credential.

- [ ] **Step 7: Run tests and commit**

Run: `python -m pytest backend/tests/test_agentteams_agent_runtime.py -q`

Expected: Manager planning, Worker Tool, Worker delegation, summary/follow-up, sender validation, restart cursor, and duplicate event tests pass.

Commit: `feat: run independent Matrix LLM agents`

---

### Task 6: Platform Runner Protocol Integration

**Files:**
- Modify: `backend/app/services/agentteams_runner.py`
- Modify: `backend/app/services/agentteams_gateway.py`
- Modify: `backend/app/routers/agentteams_collaboration.py`
- Test: `backend/tests/test_agentteams_runner.py`
- Test: `backend/tests/test_agentteams_collaboration_api.py`

**Interfaces:**
- Consumes: outbound messages from Task 3 and inbound protocol messages from Task 5.
- Produces: Matrix event IDs, strict readiness for all five accounts, and health payload with per-Agent process heartbeats.

- [ ] **Step 1: Write failing strict-readiness and child-result tests**

```python
def test_strict_health_requires_platform_manager_and_three_workers(client):
    health = client.get("/api/v1/agentteams-collaboration/health").json()
    assert {x["agent_id"] for x in health["workers"]} == {"VE-0001", "VE-0002", "VE-0003", "VE-0005"}
    assert health["ready_for_demo"] is False

def test_runner_records_real_matrix_event_id(db, runner):
    runner.process_once()
    assert db.scalar(select(AgentMessage).where(AgentMessage.direction == "outbound")).matrix_event_id.startswith("$")
```

- [ ] **Step 2: Confirm current health only covers existing workers**

Run: `python -m pytest backend/tests/test_agentteams_runner.py backend/tests/test_agentteams_collaboration_api.py -q -k "health or event_id or summary"`

- [ ] **Step 3: Add gateway account helpers without exposing tokens**

Add `whoami()` and `joined_members(room_id)` methods. Health returns MXIDs and readiness booleans only; it never returns Matrix passwords or tokens.

- [ ] **Step 4: Persist protocol evidence and Tool audit references**

Runner keeps the existing durable outbox behavior, stores each returned Matrix Event ID, rejects sender/MXID mismatches, and ensures `tool.result` payload contains the existing Gateway audit ID plus a redacted output. Protocol rejection never changes the node terminal state.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest backend/tests/test_agentteams_runner.py backend/tests/test_agentteams_collaboration_api.py -q`

Expected: all runner/API tests pass, including strict no-fallback behavior.

Commit: `feat: enforce strict Matrix AgentTeams readiness`

---

### Task 7: Local Synapse Bootstrap and Safe Process Lifecycle

**Files:**
- Create: `deploy/agentteams-matrix/docker-compose.yml`
- Create: `deploy/agentteams-matrix/homeserver.template.yaml`
- Create: `scripts/bootstrap_agentteams_matrix.py`
- Create: `scripts/start_agentteams_real_demo.ps1`
- Create: `scripts/stop_agentteams_real_demo.ps1`
- Modify: `.gitignore`
- Test: `backend/tests/test_agentteams_real_demo_scripts.py`

**Interfaces:**
- Produces: `.agentteams-runtime/runtime.json` containing non-committed local Matrix URL, room ID, MXIDs, and tokens.
- Consumes: Docker Desktop, the pinned Synapse image, local Python/Node runtimes, and `backend/.env` DeepSeek configuration.

- [ ] **Step 1: Write failing script contract tests**

```python
def test_start_script_launches_four_named_agent_processes():
    text = START_SCRIPT.read_text(encoding="utf-8")
    for role in ("manager", "hr", "it", "audit"):
        assert f"--role {role}" in text

def test_stop_script_targets_only_recorded_pids_and_named_compose_project():
    text = STOP_SCRIPT.read_text(encoding="utf-8")
    assert "Get-Process python" not in text
    assert ".agentteams-runtime\\pids.json" in text
```

- [ ] **Step 2: Confirm files are absent**

Run: `python -m pytest backend/tests/test_agentteams_real_demo_scripts.py -q`

Expected: file-not-found assertions fail.

- [ ] **Step 3: Add pinned Synapse Compose configuration**

Use `matrixdotorg/synapse:v1.117.0`, expose Synapse on `127.0.0.1:8008`, use a named volume, disable federation and open registration, and scope the Compose project name to `dwp-agentteams`. Bootstrap uses the Synapse admin registration API/shared secret to create exactly five local accounts, create one private room, invite/join all accounts, and verify membership.

- [ ] **Step 4: Write runtime configuration atomically and privately**

`bootstrap_agentteams_matrix.py` writes a temporary JSON file then renames it to `.agentteams-runtime/runtime.json`. It prints only account names, room ID, and success status. `.gitignore` excludes `.agentteams-runtime/`.

- [ ] **Step 5: Implement start and stop orchestration**

Start checks Docker/Synapse, bootstraps accounts, launches backend, four separately named Agent processes, and frontend, then records only the created PIDs. Stop resolves each recorded PID, verifies its command line belongs to this worktree, stops those processes, and runs `docker compose -p dwp-agentteams down` without removing the persistent volume.

- [ ] **Step 6: Run contract tests and commit**

Run: `python -m pytest backend/tests/test_agentteams_real_demo_scripts.py -q`

Expected: safe lifecycle, secret redaction, five-account, and pinned-image assertions pass.

Commit: `feat: add local Synapse AgentTeams launcher`

---

### Task 8: Observable API and Frontend Demonstration

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/routers/agentteams_collaboration.py`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.tsx`
- Modify: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`
- Modify: `frontend/src/pages/agentteams/TaskTree.tsx`
- Modify: `frontend/src/pages/agentteams/TimelinePanel.tsx`

**Interfaces:**
- Consumes: task graph/events/messages/result/health APIs.
- Produces: visible runtime mode, round, selected Skills, blocked/dependency state, Tool/Policy/Audit evidence, Matrix Event IDs, and final risk/actions.

- [ ] **Step 1: Write failing UI assertions for real evidence**

```tsx
it('shows selected skills, tool audit and matrix event evidence', async () => {
  mockPageApi(realTraceRoutes());
  renderPage();
  expect(await screen.findByText('agentteams-onboarding-readiness')).toBeInTheDocument();
  expect(screen.getByText('第 2 轮')).toBeInTheDocument();
  expect(screen.getByText('审计ID：AUD-101')).toBeInTheDocument();
  expect(screen.getByText('Matrix事件：$event-101')).toBeInTheDocument();
});
```

- [ ] **Step 2: Confirm the current page lacks these fields**

Run: `npm test -- --run frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

Expected: selected Skill/round/audit evidence assertions fail.

- [ ] **Step 3: Extend API DTOs using persisted event details**

Add optional typed fields so old local-demo rows still render. Do not synthesize Matrix Event IDs or readiness. Return `null`/empty state when evidence is absent.

- [ ] **Step 4: Render a slow readable trace without client-side fake progress**

The form default is the one-line goal. The page polls server state, highlights newly received events, and shows tags for real Matrix, DeepSeek, Demo Tool data, blocked dependencies, selected Skills, audit IDs, and Matrix Event IDs. It never advances a node on a timer; pacing is performed by real Agent processes.

- [ ] **Step 5: Run frontend tests and build, then commit**

Run: `npm test -- --run frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

Run: `npm run build`

Expected: UI tests and TypeScript production build pass.

Commit: `feat: expose AgentTeams collaboration evidence in UI`

---

### Task 9: Deterministic Integration and Real Matrix Tests

**Files:**
- Create: `backend/tests/test_agentteams_llm_collaboration_e2e.py`
- Create: `scripts/verify_agentteams_matrix.py`
- Create: `scripts/run_agentteams_real_smoke.py`
- Modify: `backend/tests/test_agentteams_local_demo_e2e.py`

**Interfaces:**
- Consumes: all previous task interfaces.
- Produces: one deterministic integration test and two explicit real-environment verifiers.

- [ ] **Step 1: Write the deterministic full-state-machine test**

The fake LLM must select onboarding Skill, delegate HR and IT in parallel, make HR delegate an Audit child, execute all three governed Tools, trigger Manager review, and produce a final risk/action summary. Assert task parent edges, overlap of HR/IT running timestamps, one depth-2 node, audit IDs, idempotency, and no fixed local-demo transport calls.

- [ ] **Step 2: Run it red before the final glue changes**

Run: `python -m pytest backend/tests/test_agentteams_llm_collaboration_e2e.py -q`

Expected: at least one missing wiring assertion fails.

- [ ] **Step 3: Implement Matrix environment verifier**

`verify_agentteams_matrix.py` loads `.agentteams-runtime/runtime.json`, calls `whoami` for all five tokens, verifies all joined the same room, sends one unique ping per account, syncs until all five real Event IDs are observed, and exits nonzero on any mismatch.

- [ ] **Step 4: Implement explicit real DeepSeek smoke runner**

`run_agentteams_real_smoke.py` refuses non-strict mode, submits the one-line task over HTTP, polls for at most 600 seconds, streams only safe event summaries, and validates: four Agent participants, at least two overlapping workers, one recursive delegation, at least one Tool audit ID, real Matrix Event IDs, selected Skills, and a completed Manager summary.

- [ ] **Step 5: Run all deterministic tests and commit**

Run: `python -m pytest backend/tests/test_agentteams*.py -q`

Expected: all deterministic AgentTeams tests pass without Docker or DeepSeek.

Commit: `test: cover adaptive AgentTeams collaboration end to end`

---

### Task 10: Real Environment Acceptance and Documentation

**Files:**
- Modify: `docs/agentteams/PROTOCOL.md`
- Modify: `docs/agentteams/DEPLOYMENT.md`
- Modify: `docs/agentteams/DEMO.md`
- Create: `docs/agentteams/REAL_MATRIX_ACCEPTANCE.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: verified commands and captured safe evidence from Task 9.
- Produces: architecture, protocol, startup, troubleshooting, and one complete demonstration walkthrough.

- [ ] **Step 1: Start the real environment**

Run: `powershell -ExecutionPolicy Bypass -File scripts/start_agentteams_real_demo.ps1`

Expected: Synapse, backend, four Agent processes, and frontend become healthy; the script prints no token or password.

- [ ] **Step 2: Verify Matrix independently**

Run: `python scripts/verify_agentteams_matrix.py`

Expected: five distinct MXIDs, one room, and five distinct real Matrix Event IDs.

- [ ] **Step 3: Run the real DeepSeek demonstration**

Run: `python scripts/run_agentteams_real_smoke.py --goal "评估新员工A是否可以按期入职。"`

Expected: terminal status `completed`; output includes participant IDs, task depth, Tool audit count, and safe summary but no credential.

- [ ] **Step 4: Run complete backend and frontend regression**

Run: `python -m pytest backend/tests -q`

Run: `npm test -- --run`

Run: `npm run build`

Expected: all suites pass.

- [ ] **Step 5: Write evidence-backed documentation**

Protocol docs define each message's sender, receiver, required payload, state transition, idempotency behavior, and failure response. Deployment docs distinguish Docker Synapse from Windows Agent processes and explain secret locations. Demo docs use one chronological task and, for every step, state actor, input, action, output, state change, next trigger, and code location. Acceptance records exact commands, pass/fail output, Matrix Event ID shapes, and the boundary that Tool facts are fictional.

- [ ] **Step 6: Stop only this demo and commit documentation**

Run: `powershell -ExecutionPolicy Bypass -File scripts/stop_agentteams_real_demo.ps1`

Expected: only recorded worktree processes and the `dwp-agentteams` Compose project stop; persistent Synapse data remains.

Commit: `docs: add real AgentTeams demo runbook and evidence`

---

## Final Review Gates

- [ ] A requirements reviewer checks every completion criterion in the approved spec against code and test evidence.
- [ ] A code-quality reviewer checks each task's diff for security, race, idempotency, transaction, and maintainability issues.
- [ ] Fix each accepted finding with a focused failing test before changing production code.
- [ ] Run `git diff --check`, the complete backend suite, the complete frontend suite, and the real smoke flow again.
- [ ] Keep the branch independent; do not merge into main or push unless the user explicitly requests it.
