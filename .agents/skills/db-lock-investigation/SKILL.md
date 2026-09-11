---
name: db-lock-investigation
description: Use when investigating blocking transactions and database lock wait chains.
whenToUse: Inspect locks and related statements to distinguish blocking from inherently slow queries.
---

# Database Lock Investigation

优先检查 `locks`，建立持有者、等待者、等待时长和事务年龄之间的链路，再用 `statements` 判断高延迟是否由等待造成。短暂且无长事务的等待只能作为干扰项。所有判断引用 `evidence_id`。
