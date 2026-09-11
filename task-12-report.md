# Task 12 recovery report

## Scope

Recovery was performed only in `D:\lincwc\Desktop\digital workforce platform-agentteams-collaboration`. Existing untracked reports for Tasks 8–11 and the original checkout were not modified or staged.

## Delivery audit

The reviewed delivery set is `README.md`, `backend/tests/test_agentteams_demo_script_contract.py`, `docs/agentteams/PROTOCOL.md`, `docs/agentteams/DEPLOYMENT.md`, `docs/agentteams/DEMO.md`, and `scripts/run_agentteams_demo.ps1`.

No content correction was required after source cross-check:

- `PROTOCOL.md` lists the exact twelve `MESSAGE_TYPES` implemented in `backend/app/services/agentteams_protocol.py`. Its sender table matches the `apply_message` inbound handlers: assignee for delegation, acceptance, progress, tool request, result, and failure; coordinator for summary; assignee or coordinator for agent messages; and platform-only outbound submit, assign, tool result, and cancel messages.
- `DEPLOYMENT.md` accurately states the health boundary in `backend/app/routers/agentteams_collaboration.py`: Manager is only `configured`, `platform_team` means that any Team exists, and readiness requires two distinct configured Worker IDs with two distinct resolvable Matrix IDs. It does not claim remote Manager or Worker liveness.
- `DEMO.md` uses the specified fixed onboarding task, keeps the Matrix smoke operator-triggered, and describes the failure path as an explicit timeout with no builtin-Team fallback.
- `run_agentteams_demo.ps1` contains no install, reset, Docker Compose, Docker start, or Matrix-creation action. It reads `.env`, Docker, and the controller only for preflight; enables collaboration only through the process environment; launches hidden backend/frontend processes with explicit directories and log files; waits for backend health; and prints only bounded health status/reason, URLs, PIDs, log locations, smoke guidance, and PID-specific stop guidance.
- The documentation states that the first demo uses the existing platform database and does not require Redis, Celery, or PostgreSQL. The normal test configuration disables the real collaboration runner.

## Verification before staging

1. `D:\lincwc\Desktop\digital workforce platform\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_agentteams_demo_script_contract.py -q`
   - Passed: `1 passed`.
   - Warnings: one pre-existing FastAPI/TestClient deprecation warning and one pytest cache write permission warning. The script was not executed.
2. `powershell -NoProfile -Command "[scriptblock]::Create((Get-Content -LiteralPath 'scripts/run_agentteams_demo.ps1' -Raw)) | Out-Null"`
   - Passed with exit code 0; parse only.
3. `D:\lincwc\Desktop\digital workforce platform\backend\.venv\Scripts\python.exe -m py_compile backend/tests/test_agentteams_demo_script_contract.py`
   - Initially blocked because the worktree denied creation of `backend/tests/__pycache__` (WinError 5). Re-ran with `PYTHONPYCACHEPREFIX` set to a writable temporary directory; passed with exit code 0 and did not write to the repository.
4. Markdown local-link check across `README.md` and the three AgentTeams documents
   - Passed for every local target except the intentionally documented implementation-plan dependency below.
5. `git diff --check`
   - Passed with no whitespace error. Git emitted only the informational README LF-to-CRLF warning.

## Intentional documentation dependency

`README.md` links to `docs/superpowers/plans/2026-08-31-agentteams-standalone-collaboration-system.md`. That plan is absent from this isolated worktree and is intentionally left as a link: the README explicitly says the original design and plan documents will enter the repository during the coordinator’s later integration step. The referenced design path, `docs/superpowers/specs/2026-08-28-agentteams-standalone-collaboration-system-design.md`, exists and is tracked here. This report must remain untracked and outside the Task 12 commit.

## Commit and final index check

The staged diff was checked again with `git diff --cached --check` after staging and before commit; it had no whitespace errors. The staged set contained exactly these six files and no task report:

1. `README.md`
2. `backend/tests/test_agentteams_demo_script_contract.py`
3. `docs/agentteams/DEMO.md`
4. `docs/agentteams/DEPLOYMENT.md`
5. `docs/agentteams/PROTOCOL.md`
6. `scripts/run_agentteams_demo.ps1`

Committed as `2eaf6c4 docs: add agentteams demo runbook` on branch `codex/agentteams-collaboration`. After the commit, Git reports only the pre-existing untracked Task 8–11 reports and this untracked Task 12 report; no tracked Task 12 artifact remains modified.

## Not performed

The startup script was never executed. No backend, frontend, Docker, Matrix, AgentTeams, or real smoke service was started, and no real Matrix smoke claim is made.

## Fix round 1: README link repair

The earlier intentional-dependency note is superseded for this branch. Task 12 requires links to the design, protocol, deployment, and demo documents; it does not require an implementation-plan link. Removed the nonexistent `docs/superpowers/plans/2026-08-31-agentteams-standalone-collaboration-system.md` README link and the adjacent text that implied that the plan would later enter this branch. Preserved the existing tracked design link.

Verification after the repair: the local Markdown-link check across `README.md`, `docs/agentteams/PROTOCOL.md`, `docs/agentteams/DEPLOYMENT.md`, and `docs/agentteams/DEMO.md` reported `all local targets exist` with no exemption. `git diff --check` reported no whitespace error; Git printed only its informational README LF-to-CRLF warning. The demo script was not executed and no service or smoke run occurred.

Committed the one-file repair as `63c367c docs: fix agentteams runbook links`. The staged set contained only `README.md`; the report remains untracked.

## Fix round 2: prevent demo startup reseed

### Root cause and RED evidence

`backend/app/main.py` previously called `seed_if_empty()` unconditionally from its FastAPI lifespan. `backend/app/seed.py` invokes destructive `seed_data()` whenever either DigitalEmployee or Skill data is incomplete, and `seed_data()` deletes multiple platform tables before reloading fixtures. Because `run_agentteams_demo.ps1` starts `app.main`, a demo start could trigger that destructive recovery path.

Tests were written before production changes and run with:

```text
D:\lincwc\Desktop\digital workforce platform\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_main_lifespan_seed_safety.py backend/tests/test_agentteams_demo_script_contract.py -q -p no:cacheprovider
```

The RED result was `3 failed, 1 passed`: the configuration getter was absent, the lifecycle still called `seed_if_empty()` with `DWP_SKIP_AUTO_SEED=1`, and the script lacked the new safety contract.

### Minimal repair

- Added the non-secret `DWP_SKIP_AUTO_SEED` setting and `config.skip_auto_seed()`. Only literal `1` enables it.
- Kept `Base.metadata.create_all()` and `ensure_schema_compatibility()` unchanged in the normal lifespan, but calls `seed_if_empty()` only when the skip setting is not enabled. Ordinary application startup therefore retains the prior seed behavior.
- The demo script sets `DWP_SKIP_AUTO_SEED=1` only in its process and inherited child processes. Before launching FastAPI, it requires an existing `backend/dwp.db` and explicitly refuses to initialize a database or seed data.
- `.env` presence is now advisory. The real demo preflight derives only from the backend-returned collaboration `ready_for_demo` value; a ready health result is not blocked merely because `.env`, Docker, or controller diagnostics are absent.
- Deployment text now states the precise database boundary and schema-compatibility behavior. Protocol documentation now marks `parent_task_id` and `payload` as optional/defaulted, matching the Pydantic model.

### GREEN and static checks

The same targeted test command then reported `4 passed, 1 warning`; the remaining warning is the project FastAPI/TestClient deprecation warning. The lifecycle tests mock `Base.metadata.create_all`, `ensure_schema_compatibility`, and `seed_if_empty`, and force collaboration disabled, so they do not write a real database, start the real Runner, or use Matrix/network access.

`py_compile` for the modified Python files passed with bytecode redirected to a writable temporary cache. PowerShell parse-only validation passed without executing the script. Markdown local-link checking reported `all local targets exist`, and `git diff --check` reported no whitespace errors apart from informational LF-to-CRLF notices.

Committed the scoped repair as `715c0b4 fix: prevent demo startup reseed`. The commit contains only `backend/app/main.py`, `backend/app/services/config.py`, `backend/tests/test_main_lifespan_seed_safety.py`, `backend/tests/test_agentteams_demo_script_contract.py`, `scripts/run_agentteams_demo.ps1`, `docs/agentteams/DEPLOYMENT.md`, and `docs/agentteams/PROTOCOL.md`. This report remains untracked.

## Fix round 3: protocol parent scope and pure preflight contract

### Root causes and RED evidence

The Pydantic protocol model defaults omitted `parent_task_id` to `None`, but collaboration context validation then requires that value to equal the persisted node parent. Omission is therefore accepted only for a root node; a child envelope with omitted or `null` parent ID fails `invalid_parent_task`. The previous protocol table did not make the child requirement explicit.

The demo script also computed real readiness directly from a local boolean. Although that boolean came from collaboration health, there was no pure callable contract proving that a ready synthetic health object remains ready when `.env` is absent, and the script could not be safely dot-sourced for such a test.

Tests were added before implementation and initially reported `2 failed, 1 passed`: the documentation lacked the root/child condition and the script lacked the pure helper and dot-source guard.

### Repair and GREEN evidence

- `PROTOCOL.md` now states that root envelopes may omit `parent_task_id` or send `null`, while child envelopes must explicitly include the exact persisted parent ID; missing, `null`, and mismatches are `invalid_parent_task`. The child wire example and strict validation text carry the same rule. `payload` remains documented as optional with default `{}`, matching the model.
- `run_agentteams_demo.ps1` now defines `Test-CollaborationPreflightReady`, which returns true only for a non-null health object whose `ready_for_demo` is literal `$true`. `$realPreflightReady` calls that helper.
- A guard immediately after the function definitions returns when the script is dot-sourced. The behavior test launches a temporary PowerShell child with process-only execution-policy bypass, dot-sources the script, checks synthetic ready=true and ready=false health objects, and verifies its fixed output. The guard returns before `.env` existence checks, database checks, process launch, port checks, Docker calls, or HTTP calls.

Final targeted verification reported `6 passed, 1 warning`; the warning is the existing FastAPI/TestClient deprecation. `py_compile` used a temporary cache and passed. PowerShell parser validation passed without executing the script; Markdown local-link checking reported `all local targets exist`; and `git diff --check` found no whitespace errors other than informational LF-to-CRLF notices. No service, Matrix, network, or startup script execution occurred.

Committed the scoped repair as `de81040 fix: clarify agentteams preflight contract`. The commit contains only `scripts/run_agentteams_demo.ps1`, `backend/tests/test_agentteams_demo_script_contract.py`, `backend/tests/test_agentteams_protocol_document_contract.py`, and `docs/agentteams/PROTOCOL.md`. This report remains untracked.

## Final review repair: rejected Matrix envelopes must not pin sync

### Root cause and RED evidence

`AgentTeamsCollaborationRunner._receive_new()` processes a Matrix sync batch in one savepoint. Before this repair, a syntactically valid `DWP_EVENT` whose collaboration validation raised `CollaborationConflict` (for example `unknown_root_task`, `matrix_sender_mismatch`, or `invalid_transition`) escaped from `_process_event()`. The receive savepoint then rolled back without saving its cursor. When invoked through `run_forever()`, that exception also rolled back a successfully marked outbound `sent` record from the preceding send phase, permitting duplicate remote delivery on the next tick.

The added attack test intentionally places one rejected event before one valid event and an already-pending outbound submit. It asserts the rejected event is durable and audit-visible, the valid event is processed, the cursor reaches `s2`, the root remains unchanged, and the outbound submit remains `sent` with its Matrix event ID. It covers all three rejection classes above. The pre-repair RED command was:

```text
D:\lincwc\Desktop\digital workforce platform\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_agentteams_runner.py -q -p no:cacheprovider
```

Its result was `3 failed, 33 passed, 1 warning`; the three failures escaped as the expected collaboration-conflict strings. No Matrix client, Runner loop, service process, or network operation was started by these tests.

### Minimal repair and safety boundary

The Runner now wraps only `service.apply_message()` for each parsed protocol message in a nested savepoint. A `CollaborationConflict` rolls back that message's partial ORM work, produces a bounded `protocol.rejected` inbound audit row plus append-only task event, and continues processing the batch. It therefore cannot apply an invalid state transition. Other exceptions remain unhandled at this level: they keep the existing whole-receive-batch rollback and non-advancing cursor behavior.

`PROTOCOL.md` now distinguishes the two cases: malformed envelopes remain `protocol.invalid`; syntactically valid business rejections are `protocol.rejected`, non-state-changing, and skippable. Technical receive failures still retain the old no-cursor-advance semantics.

### GREEN and static verification

1. Re-running `backend/tests/test_agentteams_runner.py` immediately after the repair reported `36 passed, 1 warning`.
2. During expanded verification, another concurrent task changed the cancel-route signature to require `actor_no`. That unrelated tracked change causes the pre-existing `test_runner_suppresses_stale_submit_after_task_is_cancelled` to call the old direct function signature and fail. Without changing that Task 10 route or test, the isolated Runner command `-k 'not suppresses_stale_submit_after_task_is_cancelled'` reported `35 passed, 1 deselected, 1 warning`.
3. `backend/tests/test_agentteams_collaboration.py`, `backend/tests/test_agentteams_protocol.py`, and `backend/tests/test_agentteams_protocol_document_contract.py` reported `23 passed, 1 warning`.
4. `py_compile backend/app/services/agentteams_runner.py backend/tests/test_agentteams_runner.py` passed with bytecode redirected to a writable temporary cache.
5. Local Markdown-link checking across the README and three AgentTeams documents reported `Markdown local links: all resolved`; `git diff --check` reported no whitespace errors (only informational LF-to-CRLF notices).

No startup script, backend/frontend service, Docker, Matrix server/client, or real network smoke was executed. This report remains untracked and outside the final repair commit.

Committed this scoped repair as `66d77c1 fix: skip rejected agentteams events`. The commit contains exactly `backend/app/services/agentteams_runner.py`, `backend/tests/test_agentteams_runner.py`, and `docs/agentteams/PROTOCOL.md`.
