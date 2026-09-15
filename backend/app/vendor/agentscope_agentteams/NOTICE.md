# AgentScope AgentTeams source notice

This directory contains a storage-independent source port from:

- Project: AgentScope AgentTeams
- Repository: https://github.com/agentscope-ai/AgentTeams
- Commit: `e9cdf2e416ecaab129b9e85ac4a7a19dd018cb7e`
- License: Apache License, Version 2.0

Imported or mechanically adapted source behavior:

- `copaw/src/copaw_worker/task.py`
  - `TaskflowError`
  - result status constants
  - `DagTask`
  - `TaskResult`
  - `is_effective_result`
  - the terminal mapping from `accept_task_result`
  - `validate_dag`
  - accepted-dependency readiness from `ready_nodes`

The upstream file-system store and CoPaw/QwenPaw runtime integration are not
included. Digital Workforce Platform supplies an SQLAlchemy persistence adapter
and its existing Matrix transport.
