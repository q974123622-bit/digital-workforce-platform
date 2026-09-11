# Task 10 Fix Report

## Scope

- Production change: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.tsx`
- Tests: `frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`
- Base commit: `3292503bf42dffdd4d7ae6df87eda101702378c8`

## RED evidence

The initial focused UI test for the server-provided `configured` status failed against the old page. It expected `Manager已配置/待实测`, but the rendered page contained `Manager未就绪：configured` and counted configured workers as zero ready workers. This was the existing partial test preparation and was kept as a substantive assertion.

The deterministic A/B delayed-selection test also failed against the old page: after selecting B and resolving A's five delayed detail responses, the assertion could not find `B 节点`. The old `loadTask` applied A's task, graph, events, messages, result, selected node, task list, loading state, and error state without verifying that A was still selected.

The first invocation using the requested frontend test wrapper was blocked before tests by Vite's temporary-config write (`EPERM`); the focused tests were then rerun with the approved test execution permission.

## GREEN verification

All commands below were run from this worktree with the approved Vite temporary-config permission where needed.

1. `pnpm --dir frontend exec vitest run src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx --reporter=verbose --silent -t "treats configured Manager and workers"`
   - PASS: 1 passed, 14 skipped; duration 18.18s.
2. `pnpm --dir frontend exec vitest run src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx --reporter=verbose --silent -t "keeps the current task details"`
   - PASS: 1 passed, 14 skipped; duration 19.50s.
3. `pnpm --dir frontend exec vitest run src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx --reporter=verbose --silent -t "stops polling after a historical task is already terminal"`
   - PASS: 1 passed, 14 skipped; duration 19.25s.
4. `pnpm --dir frontend exec vitest run src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx --reporter=verbose --silent -t "polls a running root task at one-second intervals"`
   - PASS: 1 passed, 14 skipped; duration 20.34s.
5. `pnpm --dir frontend typecheck`
   - PASS: `tsc --noEmit` exited successfully.
6. `pnpm --dir frontend build`
   - PASS: `tsc --noEmit && vite build`; 4,216 modules transformed and production assets emitted. The only output was Vite's existing advisory that the main minified chunk is larger than 500 kB.

The plan's literal wrapper command, `pnpm --dir frontend test -- AgentTeamsCollaborationPage.test.tsx`, was also run. Its script forwarded the extra `--` as `vitest run "--" "AgentTeamsCollaborationPage.test.tsx"`, which started the broader frontend suite rather than filtering to this file. The output showed unrelated frontend test files passing before the runner output window ended; the four direct focused page commands above are the conclusive page-level verification.

## Existing cancellation-test failure isolated

The repository's cancellation test was not modified in this fix. I verified its base version with:

`git show 3292503:frontend/src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx`

The base test is `shows the backend error when cancelling a running task fails`; it creates a running `AT-ROOT`, waits for a button named `/取消任务/`, then expects `取消任务失败：任务已被其他操作者终止`. The working-tree diff contains no hunk inside that test; its only pre-existing fixture difference is the health-status setup, which is not used to determine the task-action button.

For an equivalent isolated reproduction, I temporarily restored the page's exact `3292503` detail-loading and polling implementation (while keeping the unchanged cancellation test body) and ran:

`pnpm --dir frontend exec vitest run src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx --reporter=verbose --silent -t "cancelling a running task"`

It failed identically before and after the stale-response guard: `Unable to find role=\"button\" and name /取消任务/` at the test's `findByRole` call. The retry test passed in the same targeted invocation. The final source restores the Task 10 guard after this isolation. This failure is therefore recorded for review rather than attributed to the configured-status or stale-response changes.

## Implementation notes

- `ready_for_demo` remains the only submission gate.
- `configured` is rendered as `已配置/待实测`; it does not claim Matrix or AgentTeams runtime connectivity.
- A monotonically increasing selection version guards every asynchronous detail success, error, and loading-state update.
- Polling only runs when the loaded task ID matches the currently selected ID; it continues for a selected running task and stops for terminal tasks.

## Commit

`4134d8a fix: harden collaboration page state` — created after the final staged diff check. This report remains intentionally untracked.

## Fix round 2 — stale action state

### RED evidence

This round started from `4134d8ad40113228aca927bb798ef13c6093530f`.

1. The direct mixed-worker UI test initially failed because the page rendered only `Worker 已配置/待实测：1/3`; it did not show the two workers already reported as `ready`.  The missing expected text was `Worker已就绪：2/3；已配置/待实测：1/3`.
2. The new deferred retry UI test rendered A's real retry button, clicked it, selected B, then resolved A's delayed retry response.  Its final assertion failed because `B 的最终汇总` disappeared: the old success handler unconditionally replaced B with A.
3. The new deferred cancellation UI test followed the same real-button sequence and failed at the final `B 的最终汇总` assertion after A's delayed cancellation response resolved.

The test router was tightened while diagnosing the fixtures: an unmatched request or an undefined response now throws instead of silently returning a fake empty response.  The RED failures above therefore use real rendered UI actions and deterministic deferred responses, not API-only mock assertions.

The separately known cancellation-error test remains unmodified.  Its existing timing failure is recorded in the first-round isolation section above.  The new cancellation test explicitly waits for A's rendered task detail before clicking its real button, so it does not depend on that unrelated setup timing.

### GREEN verification

All following commands passed from this worktree after the minimal production change:

1. `pnpm --dir frontend exec vitest run src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx --reporter=verbose --silent -t "shows both ready and configured Worker counts"`
   - PASS: 1 passed, 17 skipped; duration 15.61s.
2. `pnpm --dir frontend exec vitest run src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx --reporter=verbose --silent -t "does not let a deferred cancel"`
   - PASS: 1 passed, 17 skipped; duration 18.42s.
3. `pnpm --dir frontend exec vitest run src/pages/agentteams/AgentTeamsCollaborationPage.test.tsx --reporter=verbose --silent -t "does not let a deferred retry"`
   - PASS: 1 passed, 17 skipped; duration 19.65s.
4. `pnpm --dir frontend typecheck`
   - PASS: `tsc --noEmit` exited successfully; duration 13.6s.
5. `pnpm --dir frontend build`
   - PASS: `tsc --noEmit && vite build`; 4,216 modules transformed and assets emitted; duration 27.8s.  The only output was Vite's pre-existing advisory about a chunk larger than 500 kB.

### Implementation notes

- Each cancel or retry captures the current selection version and updates task/detail/graph/result/action-error state only if that same task is still current when its request settles.
- Action loading is task-scoped through a set of acting task IDs.  A stale completion for A therefore cannot clear or change the visual loading state of an action on B.
- The mixed worker summary shows both backend-supported categories.  `configured` remains labelled `已配置/待实测`; it is not represented as a live Matrix or AgentTeams connection.
- Submission behavior remains unchanged: `ready_for_demo` is still the sole gate.

### Commit round 2

`986d386 fix: guard stale collaboration actions` — created after the staged diff was verified to contain only the collaboration page and its test file.  This report remains intentionally untracked.
