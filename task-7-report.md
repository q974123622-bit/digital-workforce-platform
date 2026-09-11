# Task 7 report — Local Synapse AgentTeams launcher

## Scope

- Added the pinned local Synapse Compose deployment and an internal-only
  homeserver template.
- Added an idempotent five-account/private-room bootstrap that writes the
  Task 6 strict runtime contract atomically under `.agentteams-runtime/`.
- Added strict startup and worktree-proven PID-only shutdown scripts.

## Commit

- `ffa9dd6 feat: add local Synapse AgentTeams launcher`

## Safety decisions

- Synapse is fixed at `matrixdotorg/synapse:v1.117.0`, binds only
  `127.0.0.1:8008`, retains the `dwp-agentteams-synapse-data` named volume,
  disables open registration/federation, and is always addressed as compose
  project `dwp-agentteams`.
- The Synapse registration secret is a private bind-mounted file referenced by
  `registration_shared_secret_path`; it is not passed through Compose
  environment rendering. Runtime JSON/token material is atomically written in
  the ignored runtime directory and never printed.
- The start script refuses an already proven worktree-owned invocation,
  validates Docker/Synapse/DeepSeek, and records only processes it starts with
  PID, command, and worktree evidence. The stop script queries each recorded
  PID's command line and stops only matching worktree processes; it never uses
  broad `taskkill` or Python/Node process selection and retains the volume.

## TDD evidence

- RED: `backend/.venv/Scripts/python.exe -m pytest
  backend/tests/test_agentteams_real_demo_scripts.py -q` produced eight
  expected failures because all Task 7 files were absent.
- GREEN: Task 7 contracts plus Task 6 strict runtime-loader regression:
  `13 passed, 2 warnings`. The warnings are the existing Starlette/httpx
  deprecation and an unwritable pytest-cache directory.
- `bootstrap_agentteams_matrix.py` compiled from source without writing a
  bytecode file; both PowerShell scripts parsed successfully.
- `docker compose -p dwp-agentteams -f
  deploy/agentteams-matrix/docker-compose.yml config` succeeded without
  starting Docker containers. It confirmed loopback port binding, the pinned
  image, project/volume names, and private shared-secret mount. Real Docker,
  Synapse, and DeepSeek acceptance remain explicitly deferred to Task 10.

## Working-tree protection

The Task 7 preflight found substantial existing dirty/untracked local-demo,
transport, UI, documentation, and prior task-report content. Task 7 did not
edit any of those paths. Its only modified tracked file is the previously
clean `.gitignore`, where it adds the single `.agentteams-runtime/` rule.

## Fix round 1

- `c50cb94 fix: harden local Synapse launcher lifecycle`

- Added a minimal Synapse logging configuration and mounts it read-only at the
  configured `log_config` path, so a fresh named volume has a usable file.
- Replaced argument-string process creation with `ProcessStartInfo` and one
  `ArgumentList.Add` call per argument. Each PID record now contains the
  absolute executable, structured argv, working directory, worktree, process
  id, and UTC creation time; stop/rollback reject any executable, argv,
  command-line, workspace, or creation-time mismatch.
- Startup fails closed if the scoped Compose project already has Synapse
  running. Once this invocation attempts Compose startup, every later
  bootstrap/readiness/process failure writes a failed (empty) PID record,
  stops only verified records from this attempt, and tears down only its own
  Compose project without `-v`.
- Bootstrap now uses Task 6 `require_safe_path` guards for its runtime
  directory, secrets, runtime document, and unique temporary files; temporary
  cleanup checks file identity before deletion. Windows chmod remains
  best-effort, not a claim of cross-account ACL isolation.
- Added contract coverage for the log mount, ArgumentList/no-window launcher,
  owned Compose rollback, strict PID record fields/mismatch rejection,
  reparse-path refusal, and `-NoFrontend` Node/Vite avoidance.

## Fix round 2

- `4d17855 fix: support PowerShell 5.1 process launch`

- Added `scripts/agentteams_process_helpers.ps1`, which is safe to dot-source
  and has no launcher side effects. It uses `ProcessStartInfo.ArgumentList`
  where available and otherwise applies CommandLineToArgvW/CRT-compatible
  quoting to each fallback argument for Windows PowerShell 5.1.
- A real `powershell.exe` 5.1 contract creates a temporary Python argv-capture
  script and proves round-trip preservation of an empty argument, spaces,
  embedded quotes, quote-adjacent backslashes, and trailing backslashes. The
  observed helper mode is `UsedArgumentList=false`, proving the PS5.1 fallback
  path rather than only a simulated modern-runtime path.
- Regression contracts also retain the owned-Compose failure rollback check and
  verify the ArgumentList path with a mock object. No Docker service was
  started during this round.

## Fix round 3

- `8b0ed03 fix: isolate process helper strict mode`

- Removed the helper's top-level `Set-StrictMode` so dot-sourcing cannot alter
  the caller's language mode. The helper functions do not require a strict-mode
  side effect; any future function-specific strictness must remain scoped to
  that function.
- A real Windows PowerShell 5.1 regression explicitly sets StrictMode off,
  dot-sources the helper, and reads a previously undefined variable without an
  exception. The difficult argv round-trip regression remains green.
- The locally available PowerShell 7.6.4 branch was also exercised against a
  real `ProcessStartInfo`: it selected `ArgumentList` and preserved all four
  checked argv values. No Docker service was started.
