# Task 9 report — deterministic integration and real Matrix verification

## Scope

- Added a deterministic full-state-machine integration test driven by scripted
  LLM outputs while retaining the real Manager/Worker Runtime, protocol,
  collaboration service, database, Gateway-shaped strict transport, governed
  Tool policy, and audit paths.
- The one submitted goal is `评估新员工A是否可以按期入职。`.  The Manager discovers
  and loads the onboarding Skill, delegates HR and IT concurrently, HR creates
  an Audit subtask at depth two, all three governed onboarding Tools return
  positive audit IDs, and the Manager performs the final review/summary with a
  risk and a next action.
- Added strict-only operational scripts.  The Matrix verifier proves platform
  plus four agents via `whoami`, exact private-room membership, five sent `$`
  event IDs, and sync visibility.  The real smoke runner refuses local mode,
  starts the fixed goal only against a ready strict API, polls persisted state,
  and emits redacted evidence required by the UI.

## Production compatibility fixes

- Strict Runner `task.assign` envelopes are sent by `platform` (the real Matrix
  account) while persisted business sender/coordinator fields retain their
  Manager or Worker identities.
- Worker assignment validation accepts the trusted platform signer as well as
  legitimate ancestry, but still rejects self, repeated, and broken parent
  chains.
- A completed child now requests its parent summary from the parent assignee,
  which owns the Worker Runtime context.  That parent returns a result; the
  platform subsequently requests the root Manager summary.

## TDD and verification evidence

- Valid RED: strict Runner serialized a logical Manager sender for a platform
  Matrix outbox assignment; the focused sender assertion failed before the
  strict-envelope repair.
- Valid RED: trusted platform-signed Worker assignment was rejected as an
  invalid parent chain before the narrow trusted-signer predicate repair.
- Valid RED: the full fake-LLM integration run exposed the wrong child-summary
  recipient; it reached the expected Manager final summary after repair.
- Valid RED: the smoke timeout contract showed a response summary containing a
  token-like value in script output.  Output now redacts sensitive values.
- Focused regression: `83 passed, 2 warnings` across the deterministic E2E,
  local-demo E2E, Runner, and Runtime suites.  The warnings are the existing
  Starlette/httpx deprecation and Windows pytest-cache directory warning.
- Expanded backend run: `146 passed, 1 failed, 3 warnings` before its first
  failure.  The failure is the pre-existing demo-process-tree contract calling
  `taskkill.exe /T /F`, which this Windows sandbox denies with `Access denied`;
  it is outside the Task 9 strict paths and no source was changed to mask it.

## Acceptance boundary

The deterministic test uses fake LLM decisions and an in-memory
Matrix-shaped `$`-event transport; it is not Synapse or DeepSeek acceptance.
The scripts are intentionally real-only and did not start Synapse, call
DeepSeek, or print credentials in this task.  Business facts from the three
onboarding Tools are explicitly project demo data; Matrix, DeepSeek, platform
state, and audit evidence require a later live strict acceptance run.

## Dirty-worktree protection

The pre-existing untracked `task-9-report.md` contains a separate frontend
report and was not overwritten.  The pre-existing local-demo transport/E2E
implementation received one unstaged compatibility hunk: non-root summary
requests return `task.result`, while root requests return `task.summary`,
matching the real Runtime state machine.  It is regression evidence only and
is excluded from the Task 9 commit; the strict production fixes, new test, and
verification scripts remain independently commit-safe.

## Review remediation

- The real smoke script now emits only structured, whitelisted evidence.  It
  validates that a final summary exists but reports `final_summary_present`
  instead of any summary text, so Bearer/Basic credentials and JSON credential
  fields cannot leak through an arbitrary service response.
- The strict test suite now asserts persisted `$` Matrix IDs, injects a new
  Matrix event that reuses a real protocol idempotency key and verifies no task
  node/event/audit state is replayed, and exercises a required worker
  `task.failed` message.  The failure contract is the current service behavior:
  the failed required child, its root ancestor, and the TaskRun become failed;
  a duplicate failure idempotency key adds no state events.
- Clean-export correction: strict E2E fixtures use the committed Runner
  `gateway=` seam, not the uncommitted local-demo `transport=` extension.
  The deterministic Matrix bus is therefore injected through the same strict
  Runner interface available in the clean Task 9 commit.

## Matrix incremental-sync remediation

- A live Synapse verification exposed a pagination boundary: an initial room
  sync can be timeline-limited to membership/state events and therefore omit
  verifier pings that were sent afterwards.  The verifier now snapshots a
  per-account cursor before sending the five pings, then advances every
  account through incremental syncs and accumulates visible event IDs across
  attempts.  It does not stop polling later accounts when an earlier account
  is still missing an ID.
- Valid RED: the new split-batch fake transport returned state-only initial
  syncs and two later ping batches.  Before the repair, the success assertion
  failed and the timeout case showed that no incremental cursors were used.
  After the repair, the verifier contract tests passed (`3 passed`) and the
  full deterministic E2E file passed (`14 passed`).
- Current-worktree focused regression passed `88 passed, 2 warnings`, which
  includes excluded pre-existing local-demo regression files.  The strict
  clean export at the remediation commit passed `14 passed, 1 warning` for the
  deterministic E2E and `81 passed, 1 warning` for the strict E2E/Runner/Runtime
  focus.  Those runs used an isolated checkout and the committed `gateway=`
  seam only.
