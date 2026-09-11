# Database benchmark attribution

本目录是基于 MultiAgentBench / MARBLE `database` 场景思想改编的本地确定性评测夹具，不包含或运行 MARBLE runtime，也不代表官方榜单分数。

- Upstream: https://github.com/ulab-uiuc/MARBLE
- Source dataset: `multiagentbench/database/database_main.jsonl`
- Upstream license: MIT License
- Preserved license notice: `UPSTREAM_LICENSE.txt`
- Local changes: 将 PostgreSQL 运维问题压缩为五类可审计只读 JSON 信号，并重新编写中文任务、证据和标签。

本目录不包含真实客户数据、生产数据库快照或凭据。
