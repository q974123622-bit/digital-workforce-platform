# Task 6 report — Strict Matrix AgentTeams readiness

## RED → GREEN evidence

- Runtime heartbeat and state-file traversal tests: RED `2 failed`; GREEN `2 passed`.
- Strict runtime-config validation: RED `4 failed` (module absent); GREEN `4 passed`.
- Matrix Gateway `whoami` / `joined_members`: RED `2 failed`; GREEN `2 passed`.
- Strict five-identity health API: RED `1 failed`; GREEN `1 passed`.
- Durable strict event-ID validation: RED `1 failed`; GREEN `1 passed`.

## Implementation

- Added a bounded, regular-file-only strict runtime configuration reader for
  `.agentteams-runtime/runtime.json` (or `DWP_AGENTTEAMS_RUNTIME_CONFIG`). It
  rejects symlinks, over-large files, missing credentials, and duplicate
  Matrix token/MXID/Agent identity values. Only an explicit public identity
  view is exposed.
- Added Matrix account `whoami()` and room `joined_members()` helpers with
  safe HTTP-error mapping and idempotent client close.
- Strict health verifies the platform plus Manager/HR/IT/Audit credentials,
  common room membership, current TEAM-ONBOARD membership and runtime binding,
  and four fresh state heartbeats. It reports safe MXIDs/statuses/timestamps
  only. Missing/mismatched/old evidence fails closed; local-demo makes no
  Matrix/config request.
- Every Agent runtime poll atomically persists a UTC `heartbeat_at`; Agent IDs
  are constrained before they can form a state filename.
- Strict runner delivery accepts only non-empty `$...` Matrix event IDs.
  Explicit local-demo retains its synthetic `local-out-...` transport ID.
- Updated legacy strict delegate fixtures to provide the actual VE-0002 grant
  `hr-employee-mcp`; local transport restart fixtures explicitly select
  `local_demo`.

## Verification

```text
backend/.venv/Scripts/python.exe -m pytest \
  backend/tests/test_agentteams_runner.py \
  backend/tests/test_agentteams_collaboration_api.py -q
67 passed, 2 warnings

backend/.venv/Scripts/python.exe -m pytest \
  backend/tests/test_agentteams_agent_runtime.py \
  backend/tests/test_agentteams_gateway.py \
  backend/tests/test_agentteams_strict_runtime_config.py \
  backend/tests/test_agentteams_local_demo.py \
  backend/tests/test_agentteams_transport.py -q
57 passed, 2 warnings

backend/.venv/Scripts/python.exe -m pytest \
  backend/tests/test_agentteams_collaboration_smoke_contract.py \
  backend/tests/test_agentteams_runner.py \
  backend/tests/test_agentteams_collaboration_api.py \
  backend/tests/test_agentteams_agent_runtime.py \
  backend/tests/test_agentteams_gateway.py \
  backend/tests/test_agentteams_strict_runtime_config.py \
  backend/tests/test_agentteams_local_demo.py \
  backend/tests/test_agentteams_transport.py -q
128 passed, 2 warnings
```

Warnings are the existing Starlette/httpx deprecation notice and an unwritable
pytest cache directory.

## Working-tree protection

Before any Task6 edit, saved byte-for-byte copies, binary diffs and SHA-256
manifest for the pre-existing dirty runner/router/schema/API/runner-test files
under `tmp/task6-before/`. Task6 staging must be constructed from the
before-to-after delta only; the local-demo and other pre-existing hunks are not
part of this task.

The five original dirty files were staged from synthetic `D = HEAD +
(before -> after Task6 delta)` blobs under `tmp/task6-synthetic/`; the merge
was automatic for runner and schema, while the router/API/runner-test overlap
with pre-existing local-demo insertions was manually resolved in the temporary
copies. `git diff --cached --check` passed and the cached diff contains no
pre-existing local-scenario imports, constants, or transport/UI hunks.

## Known failure

An expanded `backend/tests -q -x` run reached `143 passed`. The former
environment-only gateway-close smoke test was updated to the Task6 runtime.json
contract and now verifies that all five constructed account clients close when
membership lookup raises. The current first failure is the separate Windows
process-tree probe: `taskkill.exe` returns `Access denied` in this sandbox.

## Review fix round 1

- Strict runtime state now authenticates `{agent_id,mxid,heartbeat_at}` with an
  HMAC derived from that agent's Matrix token. The key is never persisted;
  same-OS-account readers of the runtime token remain inside the deployment
  trust boundary, but arbitrary state JSON cannot forge liveness.
- Runtime config has one loader for health and the role CLI, requires a
  configured team id, rejects portable-path and Windows-device agent ids, and
  uses the configuration directory's `state/` folder for both paths.
- Review RED began with the missing heartbeat signing API; focused GREEN was
  `83 passed`. Windows reparse-point behavior is guarded where the platform
  exposes reparse metadata; normal file/symlink paths are covered directly.
