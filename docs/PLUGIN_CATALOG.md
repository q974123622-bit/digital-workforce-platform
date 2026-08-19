# 数字员工平台 Plugin Catalog

> 本文描述数字员工 Demo 平台的 Plugin 体系。Skill 与平台 Plugin 不是同一个对象：Skill 是 DeepSeek Harness 的指令资产（`.agents/skills/*/SKILL.md`），平台 Plugin 是数字员工业务插件体系（`plugin` 表 + Plugin Gateway + Adapter + WorkflowEngine）。两者通过 ChatOrchestrator 的 Tool 层对接。

## 1. 插件分类

| 分类 | 说明 | 当前对象 |
|---|---|---|
| Skill | Harness 兼容的可复用任务指令包，只规定“何时、如何使用能力”，不判权限 | `.agents/skills/*` |
| Atomic Tool Plugin | 一个明确原子能力（查询目录/搜索/读取），无编排 | `knowledge-catalog` / `employee-search` / `document-catalog` 等 |
| Workflow Plugin | 编排多个子 Plugin，子调用必须再次经过 Plugin Gateway + Policy + Audit | `workflow://*` 五个 Workflow |
| Knowledge Plugin | 知识库检索（Mock / RAG） | `knowledge-l1` / `knowledge-l2` |
| MCP Plugin | Mock 的 MCP 形态原子能力 | `hr-employee-mcp` 等 |
| RPA Plugin | 需审批的敏感执行能力 | `rpa-report` |

## 2. 当前 Plugin 列表（共 17 个）

| plugin_id | type | Tool | data_level | endpoint | 用途 | 状态 |
|---|---|---|---|---|---|---|
| knowledge-l1 | knowledge | search_knowledge | L1 | mock://kb/l1 | 公开制度/FAQ | active |
| knowledge-l2 | knowledge | search_knowledge | L2 | mock://kb/l2 | 内部流程 | active |
| hr-employee-mcp | mcp | — | L2 | mock://mcp/hr-employee | 员工查询 Mock | active |
| employee-collaboration | mcp | collaborate_employee | L2 | mock://collaboration/employee | 员工协作 | active |
| document-read | mcp | read_document | L2 | mock://document/read | 读取虚构文档 | active |
| work-record-query | mcp | query_work_records | L2 | mock://work/records | 工作记录查询 | active |
| knowledge-catalog | mcp | list_knowledge_bases | L1 | mock://knowledge/catalog | 知识库目录 | active |
| employee-search | mcp | search_employee | L1 | mock://employee/search | 员工目录搜索 | active |
| document-catalog | mcp | list_documents | L2 | mock://document/catalog | 文档目录 | active |
| adp-onboarding | workflow | — | L2 | mock://adp/onboarding | ADP 入职流程 Mock（遗留 workflow 类型） | active |
| regulation-compare-workflow | workflow | compare_regulations | L1 | workflow://regulation/compare | 外部监管 + 内部制度对比 | active |
| document-compliance-workflow | workflow | review_document_compliance | L2 | workflow://document/compliance | 文档 + 合规依据收集 | active |
| it-support-workflow | workflow | handle_it_support | L1 | workflow://it/support | IT 知识 + 可选升级协作 | active |
| employee-assist-workflow | workflow | assist_with_employee | L1 | workflow://employee/assist | 查员工 + 协作询问 | active |
| report-export-workflow | workflow | prepare_work_report | L2 | workflow://report/export | 工作记录 + RPA 报表（需审批） | active |
| rpa-report | rpa | — | L3 | mock://rpa/report | 报表机器人（敏感，Approval） | active |
| internet-search | http | — | L1 | mock://http/internet-search | 公网搜索 Mock（演示禁网策略） | active |

## 3. Workflow 调用图（文字链路）

```text
regulation-compare-workflow
  └─ search_knowledge(KB-REG-EXTERNAL) → search_knowledge(KB-REG-INTERNAL)

document-compliance-workflow
  └─ document-read → search_knowledge(KB-REG-EXTERNAL) → search_knowledge(KB-REG-INTERNAL)

it-support-workflow
  └─ search_knowledge(KB-IT-SERVICE)
     └─ [escalate=true] employee-search → collaborate_employee

employee-assist-workflow
  └─ employee-search → collaborate_employee

report-export-workflow
  └─ query_work_records → rpa-report（Approval Required，不自动执行）
```

所有子调用都通过 Plugin Gateway 重新执行 Policy 评估并落审计，Workflow 不能直接调用 Adapter。

## 4. DSH 关系说明

- `.agents/skills/` 是真实 Harness Skill 资产，已通过 `dsh-skill` + `dsh-skill-filesystem` 验证可发现加载。
- 平台 `plugin` 表、Plugin Gateway、Adapter、WorkflowEngine 是数字员工 Demo 平台的业务插件体系。
- 平台现有 `RuntimeAdapter`（Noop / Harness headless / Docker，默认 demo 模式）与 `TeamTaskOrchestrator`，是 Sprint 5 的 Runtime / 团队协作能力，独立于本目录的 Skill Demo Tool 与业务 Workflow。
- 当前 `Harness Runtime → 平台 Plugin Gateway` 的完整桥接仍未打通；不要把平台 Plugin 描述为已通过 `ctx.plugin(...)` 加载的 DSH Cordis Plugin。
