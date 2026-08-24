# Skill → Tool / Plugin 能力契约

> 本文描述每个通用 Skill 需要什么 Tool / Plugin 能力、概念输入输出、真实现状与 Mock Fixture 映射。
> 本文不是 HTTP API 契约：不定义 URL、HTTP Method、FastAPI Router、Pydantic 请求/响应、HTTP 状态码或数据库表，也不定义自定义机器可读 manifest。

## 状态定义

- **Existing**：当前平台真实已有能力。
- **Partial**：平台已有部分底层能力，但没有统一 Tool。
- **Required / Not Implemented**：Skill 未来运行需要，但当前尚未实现的 Runtime 能力。
- **Mock Fixture**：当前为 Demo 框架准备的虚构数据，不代表真实 Runtime 能力已接通。

## 总览

| Skill | Required Capability | Current Platform Status | Mock Fixture | Runtime Status |
|---|---|---|---|---|
| enterprise-knowledge | enterprise knowledge search（`search_knowledge`） | Existing (Demo) | `mock-data/kb/` + `MockKnowledgeAdapter` | 平台 ChatOrchestrator 当前真实可用；Harness Runtime 桥接未完成 |
| employee-collaboration | employee ask / delegate / handoff（`collaborate_employee`） | Existing (Demo) | `mock-data/skill-fixtures/collaboration/collaboration-scenarios.json` | Demo Tool 已接入（Mock Fixture） |
| employee-collaboration | employee search | Partial（平台 REST/DB 员工查询存在，无统一 employee search Tool） | — | Required / Not Implemented |
| employee-collaboration | 真实跨员工 Runtime | Not Implemented | — | Not Implemented |
| document-analysis | document read（`read_document`） | Existing (Demo) | `mock-data/skill-fixtures/documents/` | Demo Tool 已接入（Mock Fixture） |
| document-analysis | document search | Required / Not Implemented | — | Required / Not Implemented |
| document-analysis | 真实文件 Runtime | Not Implemented | — | Not Implemented |
| work-summary | work record query（`query_work_records`） | Existing (Demo) | `mock-data/skill-fixtures/work-records/work-records.json` | Demo Tool 已接入（Mock Fixture） |
| work-summary | 统一 session/message query Tool | Required / Not Implemented | — | Required / Not Implemented |
| work-summary | 统一 task query Tool | Required / Not Implemented | — | Required / Not Implemented |
| work-summary | 统一 audit/activity query Tool | Required / Not Implemented | — | Required / Not Implemented |
| work-summary | 真实工作记录 Runtime | Not Implemented | — | Not Implemented |

## enterprise-knowledge

**Skill**：`enterprise-knowledge`

**需要能力**：enterprise knowledge search

**当前平台真实已有**：`search_knowledge`

当前链路大致为：

```text
search_knowledge
  → Policy
  → Plugin Gateway
  → Knowledge Adapter
  → Mock Knowledge
```

**概念输入**

- `query`：必选，用户要查询的问题或关键词。
- `knowledge_base_id`：当 Runtime / Tool 需要时提供，用于指定目标知识库。

**概念输出**

- `query result` / `hits`：检索命中内容。
- `decision` / `status`：本次调用的决策或状态。
- `reason`：拒绝或失败时的原因。
- `source` / knowledge base 信息：存在时提供来源标签与知识库信息。

**需要表达的结果状态**

- Allow + Result
- Deny
- Approval Required
- Empty
- Error

> 契约不包含具体 Policy 编号；权限语义由 Policy Engine 决定。

**数据来源（Demo）**

- `mock-data/kb/`（虚构 Markdown / docx / xlsx / pdf）
- 默认 `DWP_KB_MODE=mock`：`MockKnowledgeAdapter`
- `DWP_KB_MODE=rag`：`RAGKnowledgeAdapter`（Demo 向量检索，无 Key 自动降级 Mock；非真实生产 RAG）

## employee-collaboration

**Skill**：`employee-collaboration`

**需要能力**

- employee search
- employee ask
- employee delegate
- employee handoff

**真实现状**

- 员工查询 REST / DB 能力存在。
- 单员工 chat 存在。
- 真正跨员工 ask / delegate / handoff 当前不存在。
- Harness Subagent 不能直接等同于企业数字员工。

已接入 Demo Tool：`collaborate_employee`（覆盖 ask / delegate / handoff），底层为 Mock Fixture，不代表真实跨员工 Runtime。

> 以下均为未来 Runtime 能力的概念输入输出，不代表这些 Tool 已经注册。

### employee search

**概念输入**

- 搜索条件：employee identifier、name、department、capability 等。

**概念输出**

- matching employees
- employee identity / basic metadata
- availability（如果 Runtime 未来提供）

### employee ask

**概念输入**

- target employee
- question / request
- collaboration context / trace（如 Runtime 支持）

**概念输出**

- status
- target employee
- response
- reason
- trace / source

### employee delegate

**概念输入**

- target employee
- subtask
- task context

**概念输出**

- status
- result
- reason

### employee handoff

只定义为「完整任务转交」语义，当前不实现。

**通用状态**

- success
- not_found
- unavailable
- denied
- blocked
- error

**Mock Fixture**

- `mock-data/skill-fixtures/collaboration/collaboration-scenarios.json`

> 这些 Fixture 只是 Demo 场景，不代表真实跨员工 Runtime 已经存在。

## document-analysis

**Skill**：`document-analysis`

**需要能力**：document read / document search

**真实现状**

- 平台没有通用 document / file Tool。
- Knowledge Adapter 只能读取现有 Mock KB markdown。
- Harness 自身文件 Tool 属于 Harness Runtime 内部能力，不能直接声明为平台数字员工 Tool。

已接入 Demo Tool：`read_document`，只读取 `mock-data/skill-fixtures/documents/`，不代表通用文档 Tool 已实现。

**概念输入**

- `document identifier` / `reference`：必选，定位目标文档。
- `query`：可选。
- analysis 范围：可选。

**概念输出**

- content
- document metadata（如果存在）
- status
- reason

**状态**

- success
- empty
- not_found
- denied
- error

**Mock Fixture**

- `mock-data/skill-fixtures/documents/`
  - `normal-document.md`
  - `empty-document.md`
  - `conflict-document-a.md`
  - `conflict-document-b.md`

> 当前 Fixture 用于定义和测试 Skill 场景，不表示通用文档 Tool 已经实现。

## work-summary

**Skill**：`work-summary`

**需要能力**

- work record query
- session / message query
- task query
- audit / activity query

**真实现状**

- `ChatSession` / `ChatMessage` 存在。
- `AuditEvent` 存在。
- `TaskRun` 模型存在，但编排 / API 不完整。
- 没有统一 work-record Tool。

已接入 Demo Tool：`query_work_records`，只查询 `mock-data/skill-fixtures/work-records/work-records.json`，不代表真实工作记录 Runtime 已接入。

**概念输入**

- employee / session / task 标识（视 Runtime 提供能力）。
- time range。
- status / type 过滤（如支持）。

**概念输出**

- work records
- record type
- factual status
- timestamp / source
- task / session / trace 关联（存在时）

**状态语义**

- `success` + `records` 非空：正常结果。
- `success` + `records=[]`：调用成功但没有匹配工作记录（正式约定；Adapter 不返回 `status=empty`）。
- `denied`：权限拒绝。
- `error`：工具执行失败。

**Mock Fixture**

- `mock-data/skill-fixtures/work-records/work-records.json`

> work-summary Skill 必须保留 `completed`、`in_progress`、`not_done`、`research`、`review`、`issue_resolved` 等事实状态，不能由 Tool 层统一改写成「完成」。

## Workflow Tool 映射

| Tool | Workflow Plugin | 调用的子 Plugin / 知识库 | 状态 |
|---|---|---|---|
| compare_regulations | regulation-compare-workflow | search_knowledge(KB-REG-EXTERNAL)、search_knowledge(KB-REG-INTERNAL) | Existing (Demo) |
| review_document_compliance | document-compliance-workflow | document-read、search_knowledge(KB-REG-EXTERNAL)、search_knowledge(KB-REG-INTERNAL) | Existing (Demo) |
| handle_it_support | it-support-workflow | search_knowledge(KB-IT-SERVICE)、employee-search、collaborate_employee | Existing (Demo) |
| assist_with_employee | employee-assist-workflow | employee-search、collaborate_employee | Existing (Demo) |
| prepare_work_report | report-export-workflow | work-record-query、rpa-report（Approval Required） | Existing (Demo) |
| analyze_policy_change | policy-change-impact-workflow | document-read、search_knowledge(KB-REG-EXTERNAL)、search_knowledge(KB-REG-INTERNAL)、search_knowledge(KB-SECURITIES)、employee-search、collaborate_employee（可选） | Existing (Demo) |

所有 Workflow 子调用均重新经过 Plugin Gateway + Policy + Audit；Workflow 只返回结构化结果，最终总结由 Skill + LLM 完成。

完整 Plugin 目录见 `docs/PLUGIN_CATALOG.md`。
# Domain Skill Tool Contracts

| Skill | Tool / workflow tool | Plugin / sources |
|---|---|---|
| hr-policy-assistant | search_knowledge | KB-HR-POLICY |
| hr-onboarding-assistant | assist_hr_onboarding | hr-onboarding-workflow |
| hr-transfer-assistant | review_hr_transfer | hr-transfer-review-workflow |
| it-service-assistant | search_knowledge, query_it_service_status | KB-IT-SERVICE, it-service-status |
| it-incident-triage | triage_it_incident | it-incident-triage-workflow |
| it-change-assistant | query_it_service_status, search_knowledge | it-service-status, KB-IT-SERVICE |
| audit-policy-check | search_knowledge | KB-AUDIT-PROCEDURE, KB-REG-INTERNAL, KB-REG-EXTERNAL |
| audit-trace-review | query_audit_events | audit-event-query |
| audit-evidence-review | review_audit_evidence | audit-evidence-review-workflow |

All workflow children re-enter Plugin Gateway; only policy-governed results may be presented.
