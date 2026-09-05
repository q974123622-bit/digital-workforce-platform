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
