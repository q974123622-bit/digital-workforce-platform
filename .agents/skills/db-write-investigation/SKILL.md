---
name: db-write-investigation
description: Use when investigating database write throughput, transaction frequency, and WAL pressure.
whenToUse: Inspect statements and table statistics for write amplification or unbatched writes.
---

# Database Write Investigation

优先检查 `statements` 与 `table_stats`，核对每次写入行数、事务频率、写入速率和 WAL 压力。锁、索引或清理信号只有在用于排除竞争原因时才查询。结论必须引用 Tool 返回的 `evidence_id`，不得从场景名称猜答案。
