---
name: db-vacuum-investigation
description: Use when investigating autovacuum lag, dead tuples, and table bloat.
whenToUse: Inspect vacuum and table statistics for cleanup pressure.
---

# Database Vacuum Investigation

优先检查 `vacuum` 与 `table_stats`，比较死元组、存活元组、最近自动清理时间、表大小和扫描数量。不能仅因查询慢就断言表膨胀。最终结论保留相关 `evidence_id`。
