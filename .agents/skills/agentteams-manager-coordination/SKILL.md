---
name: agentteams-manager-coordination
description: Use when an AgentTeams manager must coordinate a multi-agent task and assess direct-child evidence.
whenToUse: Coordinate a current team through evidence-based task completion.
---

# AgentTeams Manager Coordination

Create a 粗粒度 plan that names the intended outcome, required evidence, and review point.

- Choose only agents in the 当前团队 for the current round.
- 首轮只派发能够取得一手证据的主要工作流。不要把尚未由证据暴露出来的二次核查，提前作为根任务的并列子任务。
- Give every direct child a bounded outcome and evidence request. Let the Worker that owns the evidence decide whether a specialist child is needed.
- Review each 直接子任务的证据; do not treat a status statement as completion.
- 后续复核时同时检查直接子任务汇总的嵌套协作证据。When required evidence is still missing, issue one 有界 follow-up round that requests only the missing evidence.
- Otherwise summarize the evidence, unknowns, and completion decision for the parent task.

Do not extend the plan merely to create more delegation. Preserve unknown facts as unknown.
