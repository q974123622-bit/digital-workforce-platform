---
name: db-index-investigation
description: Use when investigating invalid, missing, or unused database indexes.
whenToUse: Inspect index validity and statement access patterns for scan regressions.
---

# Database Index Investigation

优先检查 `indexes` 与 `statements`，核对索引有效性、扫描次数和查询谓词；必要时用 `table_stats` 验证顺序扫描是否异常。索引存在不等于可用，不得忽略 invalid 状态。所有事实引用 `evidence_id`。
