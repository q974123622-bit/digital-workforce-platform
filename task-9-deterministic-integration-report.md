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
