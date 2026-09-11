# Task 8 Synapse volume ownership fix

## Scope and protection

- Base **B**: `b32ea839af0cf0f2fb49a913e122247098db64c8`.
- Original targeted state **O** matched B before this change. All unrelated dirty and untracked work remains outside the temporary index.
- Candidate **A** contains only:
  - `deploy/agentteams-matrix/docker-compose.yml`
  - `scripts/start_agentteams_real_demo.ps1`
  - `backend/tests/test_agentteams_real_demo_scripts.py`
- Temporary index manifest: `C:\Users\lincw\.codex\visualizations\2026\09\03\01a067a4-34f3-7893-aa7b-610fae11b076\task8-synapse-audit\manifest.json`.

## TDD evidence

- RED: the new focused contract run initially had two failures: no `synapse-volume-init` service and no cleanup helper that preserved the original error.
- First real-container attempt exposed a Compose command-form issue, not a read-only mount failure: the initializer logged `chown: missing operand` because shell-form `command` was split when combined with the explicit `/bin/sh -ec` entrypoint.
- RED again: the contract required a single command argument for that entrypoint and failed against the shell-form configuration.
- GREEN: `backend/tests/test_agentteams_real_demo_scripts.py -q` passed **18 tests** after using a one-element exec command array.

## Implementation and real runtime evidence

- `synapse-volume-init` runs the pinned Synapse image as `0:0`, mounts only the project named volume, gives `/data` ownership to `991:991`, and applies mode `750`. It does not use `777` and does not delete the volume.
- Synapse waits for `synapse-volume-init` to complete successfully. The initializer has no read-only bind mounts, so the read-only configuration files mounted by Synapse are not touched.
- Startup cleanup remains project-scoped (`-p dwp-agentteams -f ... down`) and catches cleanup failures so that the startup exception is not replaced.
- Actual Docker check started only `synapse-volume-init` and `synapse`; no Agent processes were started. The initializer exited with code 0, Synapse reached `Up`, and `http://127.0.0.1:8008/_matrix/client/versions` returned **200**. Cleanup used project-scoped `docker compose down` without `-v`.

## Commit and clean-tree verification

- Commit: `5a3c781 fix: initialize Synapse volume ownership`.
- A ZIP clean export of that commit passed the same focused script contract suite: **18 passed** (one pre-existing FastAPI deprecation warning), and `docker compose config --quiet` succeeded from the export.
