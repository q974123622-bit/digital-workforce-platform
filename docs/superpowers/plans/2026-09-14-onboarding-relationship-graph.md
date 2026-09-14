# Onboarding Relationship Graph Implementation Plan

**Goal:** Make the onboarding Matrix demo create a real Manager -> department leads -> specialists task tree, with a model-driven Manager follow-up round.

**Architecture:** The strict runtime configuration is the topology authority. The platform projects a bounded per-agent relationship view into each protocol message, while the decision layer and collaboration service both enforce the same direct-report boundary. Existing role-change and database scenarios continue to operate when no onboarding relationship contract applies.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic v2, Matrix protocol runtime, pytest.

## Tasks

- [ ] Add failing decision and runtime-contract tests for bounded relationship context and batch Worker delegation.
- [ ] Extend strict Matrix bootstrap/runtime roles with HR and IT specialist identities and scoped capabilities.
- [ ] Project a per-agent relationship graph into task submit, assignment, and child-summary messages.
- [ ] Enforce direct-report delegation in both model-output validation and platform task creation.
- [ ] Add onboarding-only Manager and department-lead round contracts.
- [ ] Update onboarding Skills and startup scripts for the four specialist processes.
- [ ] Run targeted backend tests, full backend regression tests, and frontend regression/build checks.

## Constraints

- Do not change the database benchmark team or its evaluation scenarios.
- Do not change the role-change workflow contract.
- Do not forward private reasoning content, Matrix credentials, or raw room history as shared context.
- Preserve all pre-existing uncommitted changes in the worktree.
