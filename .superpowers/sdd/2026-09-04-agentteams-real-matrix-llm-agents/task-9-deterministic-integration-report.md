# Task 9 report copy — deterministic integration and real Matrix verification

Canonical implementation report: [`task-9-deterministic-integration-report.md`](../../../task-9-deterministic-integration-report.md).

Task 9 adds deterministic fake-LLM coverage over the real AgentTeams Runtime,
protocol, service/database, strict Gateway-shaped transport, Tool policy, and
audit chain.  It proves onboarding Skill selection/loading, concurrent HR and
IT delegation, HR-to-Audit depth-two delegation, three governed Tool/audit
results, Manager review, final risk/action summary, task graph edges, positive
audit IDs, and idempotent replay.

The two operator scripts are strict-only: the Matrix verifier checks all five
accounts, exact private membership, sent `$` IDs, and sync visibility; the
smoke runner refuses local mode and reports redacted persisted acceptance
evidence.  Live Synapse/DeepSeek/Matrix execution remains Task 10 acceptance,
not a claim of this deterministic test.

Review remediation keeps smoke output strictly whitelisted: it proves a final
summary exists without outputting summary text.  The test suite additionally
proves strict `$` IDs, new-event/protocol-key deduplication without state replay,
and required-child failure propagation/deduplication under the existing service
contract.

The clean export uses the committed strict Runner `gateway=` injection seam;
it does not depend on the later untracked local-demo `transport=` extension.

## Matrix incremental-sync remediation

A live Synapse verification showed that initial room sync can be limited to
state/member timeline entries and omit the later verifier pings.  The verifier
therefore takes one cursor per account before sending its five pings, performs
incremental sync from every cursor on every attempt, advances those cursors,
and accumulates seen `$` IDs across batches.  It never breaks out before all
five accounts have been polled.  The fake contract test covers a state-only
initial sync, split later batches, cursor advancement, all-account timeout
polling, and redacted evidence.  It was validly RED before the source change;
the focused verifier contract then passed `3 passed` and the full deterministic
E2E file passed `14 passed`.  The isolated clean export passed `14 passed` for
that file and `81 passed` for strict E2E/Runner/Runtime, proving it uses no
uncommitted local-demo transport files.
