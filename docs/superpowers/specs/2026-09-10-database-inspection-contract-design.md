# Database Inspection Contract Design

## Scope

Unify only the `TEAM-DB-BENCH` database-inspection request contract. Do not alter generic AgentTeams assignments, other tools, `TEAM-ONBOARD`, existing task history, or the five legacy scenarios.

## Single source of truth

The database scenario module owns one contract for `scenario_id + signal + filters + limit`. It defines all signal names, per-signal filter fields, whether empty filters are allowed, value bounds, and the public JSON Schema representation. Runtime validation and model-facing schemas are derived from this contract.

`DatabaseInspectionFilters` forbids extra fields. A complete request validator checks that the scenario exists, supports the requested signal, and that every filter is permitted for that signal. Empty filters are accepted only when the signal contract explicitly allows an unfiltered inspection.

## Model boundary

Database-only `SingleAgentDecision`, `ManagerAssignment`, and `ManagerReview` use the strict filter type. The Manager and Single Agent receive the generated public contract. Their actual structured-output schemas are tested. Provider compatibility is preserved: this project uses `response_format=json_object`, not DeepSeek strict Tool Calls, so runtime Pydantic validation remains authoritative and unsupported JSON-Schema keywords are not relied on for safety.

The entire Manager plan is parsed and validated before any child task is created or delegated. A follow-up assignment is likewise validated before graph mutation. Invalid planning and single-agent decisions retain distinct stage-specific failure codes.

## Runtime and persistence

Gateway and Adapter validation reuse the same complete request validator. Gateway policy and authorization remain independent defense layers. Benchmark resource synchronization updates only reserved database benchmark identities, grants, team, and plugin. It upgrades an existing plugin schema, is idempotent, and does not reset data. The persisted schema and the runtime/model schema are compared in tests after canonical JSON normalization.

## Acceptance

Negative tests cover unknown filter names, known-but-disallowed fields, null/empty/wrong-type/boundary values, unsupported scenario-signal pairs, and a plan whose final assignment is invalid. Positive tests cover allowed filtered and unfiltered requests across planning, review, Single Agent, Gateway, Adapter, and resource synchronization.

After automated tests, run a 9-run smoke (`3 new scenarios x 3 configurations x 1 repeat`). Require terminal tasks, actual expected Tool inspections, valid audited evidence, correct scenario evaluation, `invalid=0`, and `database_inspection_failed=0`. Only then run the 45-run evaluation (`3 x 3 x 5`) with the same contract fingerprint and fixtures. Preserve all first-attempt and retry records.
