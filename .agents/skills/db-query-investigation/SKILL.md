---
name: db-query-investigation
description: Use when investigating resource-heavy analytical queries and temporary data pressure.
whenToUse: Inspect statements and table statistics for oversized scans, CPU, reads, and temporary files.
---

# Database Query Investigation

优先检查 `statements` 与 `table_stats`，关注单次读取行数、执行时间、临时文件、CPU 和读吞吐。锁信号可用于排除竞争，但短等待不得取代持续资源证据。结论必须引用 `evidence_id`。
