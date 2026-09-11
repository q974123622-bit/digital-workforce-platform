# Real Matrix / DeepSeek acceptance record

This record is the acceptance gate for the strict AgentTeams path.  It is not a
local-demo checklist: a deterministic local transport identifier, a fake LLM
test, or a browser page is not evidence that Synapse, Matrix accounts, or
DeepSeek have run.

## Current status — blocked, not PASS

| Check | Observed evidence | Status |
|---|---|---|
| Docker Desktop engine | Docker Desktop socket repair was observed; Engine reports `29.5.2`. | Available prerequisite only |
| Official Synapse source | The project pins `matrixdotorg/synapse:v1.117.0`. Element's official installation guide lists both the Docker Hub image and `ghcr.io/element-hq/synapse`. | Source verified, not run |
| Image pull | The first Docker Hub pull ended with `unexpected EOF`; a later pull had no recorded completion progress. Docker Hub and GHCR manifest/index lineage was compared (`070236…` index and `4c3d…` amd64 in the captured inspection). | Blocked / incomplete |
| Five Matrix identities | No live `whoami`, private-room membership, sent-event, or sync evidence has been captured. | Not run |
| DeepSeek strict collaboration | No completed strict smoke task has been captured. | Not run |

The image source statement above follows Element's [official Synapse
installation guide](https://element-hq.github.io/synapse/latest/setup/installation.html),
which documents the official Docker Hub and GHCR images.  It does not authorize
substituting an unverified mirror or treating a matching manifest as a running
container.

## Commands to run after the pull is demonstrably complete

Run from the repository root in an authorized local environment.  Do not paste
the runtime JSON, token, password, authorization header, raw login response, or
raw Matrix body into a terminal transcript or ticket.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_agentteams_real_demo.ps1
```

Expected prerequisite evidence: Synapse is reachable, backend and frontend are
healthy, and four separately launched Windows Agent processes have recorded
heartbeats.  A startup error is a failure/blocked record; it must not select
`local_demo` as a substitute.

```powershell
backend\.venv\Scripts\python.exe .\scripts\verify_agentteams_matrix.py
```

Expected PASS evidence: exactly five distinct identities (platform, Manager,
HR, IT, Audit), exact private-room membership, five newly sent `$` Matrix event
IDs, and sync visibility of every one.  The verifier outputs account labels,
event IDs, and a reason code only; a nonzero exit is a failed acceptance.

```powershell
backend\.venv\Scripts\python.exe .\scripts\run_agentteams_real_smoke.py
```

Expected PASS evidence: the strict health gate, four ready worker heartbeats,
completed task, Manager/HR/IT/Audit participants, depth-two delegation, HR/IT
overlap, selected Skills, delegation round, three governed Tool audit IDs,
strict `$` event IDs, Manager review, and `final_summary_present=true`.  Tool
business facts remain fictional project data; Matrix, platform persistence,
and DeepSeek activity are the acceptance subjects.

## Automated evidence recorded separately

| Command family | Result | What it proves | What it does not prove |
|---|---:|---|---|
| Clean-export Task 9 E2E | `12 passed` | Deterministic Runtime/Runner/service/policy/audit state-machine wiring | Synapse, real Matrix, or DeepSeek |
| Clean-export strict focused tests | `79 passed` | Committed strict Runner/Runtime/E2E contracts | A running Docker container or five accounts |
| Dirty-worktree focused tests | `86 passed` | Current workspace, including predecessor local regressions | Clean-commit acceptance or real services |

Record a real run by appending date/time, command exit code, safe account/event
IDs, task ID, audit-ID count, and any blocker.  Do not change this document to
PASS until all three commands succeed against the same live strict environment.
