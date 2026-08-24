# adapters/ — Adapter 预留目录

> 状态：Sprint 1.5 冻结（2026-08-17）。本目录只放接口契约与注册说明，**不包含任何实现代码**。
> 约束：不允许直接访问知识库、数据库、Workflow 或 RPA；所有资源调用必须走统一链路：
> `Employee Identity → Policy Engine → Plugin Gateway → Employee Harness → Plugin Adapter Tool → Enterprise Resource`。

Skill 与 Plugin 统一使用 Capability Contract v1.0，但 Skill 是不可执行的 instruction；
只有 Plugin 可以进入上述执行链。Harness 负责员工级理解与工具计划，Adapter 是其受控业务工具；
正常或降级路径都只允许 Adapter 调用一次，Harness 文本不能作为业务成功回执。

## 目录用途

| 子目录/文件（规划） | 用途 | 契约位置 |
|---|---|---|
| `runtime/`（规划） | Runtime Adapter（harness / demo / openclaw-stub / agentteams-stub） | `docs/API_CONTRACT.md §6 Runtime Adapter Interface` |
| `knowledge/`（规划） | Knowledge Adapter（L1/L2 虚构知识库查询） | `docs/API_CONTRACT.md §7 Knowledge Adapter Interface` |
| `workflow/`（规划） | ADP Workflow / 审批流程 Adapter（Mock） | `docs/API_CONTRACT.md §7` |
| `rpa/`（规划） | RPA 报表机器人 Adapter（Mock） | `docs/API_CONTRACT.md §7` |
| `agentteams/`（规划） | AgentTeams 协作平台 Adapter 桩：本周不接入（需 K8s/Docker + Matrix 形态），Demo 口播「已预留接入位」 | `docs/ARCHITECTURE.md §2` |

## 本周（Sprint 1 / 1.5）不实现

- DeepSeek API 调用（LLMProvider 属 `backend/app/services/`，后续 Sprint 实现）
- 内部知识库接入（真实内容仅存在于工作区外 Secure Overlay，绝不入库）
- AgentTeams / DeepSeek Harness 真实调用（只预留 Adapter 桩位）

任何新增 Adapter 必须：
1. 实现冻结契约中的接口签名（`docs/API_CONTRACT.md`）；
2. 只通过 Plugin Gateway 被调用，不做权限判断；
3. 调用结果（允许/拒绝/审批）自动落审计（由 Gateway 负责）。
