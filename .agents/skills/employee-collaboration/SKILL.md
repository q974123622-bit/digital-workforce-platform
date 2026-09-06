---
name: employee-collaboration
description: Use when an employee agent must choose between Tool, Ask, Delegate, Complete, or Fail during collaboration.
whenToUse: Select the smallest collaboration action that can produce needed evidence.
---

# Employee Collaboration

Choose the action that matches the missing information or ownership boundary.

- **Tool** records an external lookup or operation result as evidence.
- **Ask** requests clarification from a responsible participant.
- **Delegate** assigns a bounded child outcome to an eligible current-team agent selected from the fresh 能力目录 by the required capability.
- **Complete** reports a bounded result only after the required evidence is present.
- **Fail** reports the unresolved constraint and the evidence gathered so far.

Use your own authorized Tool first when the missing fact is within your responsibility. If that evidence exposes a specific gap outside your ownership, Delegate only that gap and 等待子任务结果 before completing. Do not invent an identity or capability, and do not duplicate a child whose result is already present.

当自己的 Tool 证据已经明确某个特定证据缺口，且当前能力目录存在独立复核能力时，Delegate 仅覆盖该缺口的有界子任务，并把已有 Tool 证据带入任务目标或原因。若 previous results 已有同一缺口的子结果，必须不得重复委派；改用已有 Tool 证据与子结果作出 Complete 或 Fail。

Never Delegate to yourself or the 父链, or use delegation to return work to a parent. Report unknown information as 未知 and include the evidence gathered so far.
