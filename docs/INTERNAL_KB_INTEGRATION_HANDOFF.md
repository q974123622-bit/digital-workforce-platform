# 内部知识检索接入交接手册

本文面向接手内部知识引擎对接、增加知识库或增加 AI 员工的开发同事。当前实现是只读检索，不包含知识库创建、上传、修改或删除。

## 1. 当前已经实现

- `DWP_KB_MODE=internal` 时启用真实内部知识检索；其他模式不会意外访问内部服务。
- 一次检索指定一个平台知识库 ID，由 Adapter 映射为当前环境的内部知识库数字 ID。
- 所有业务调用仍经过 `Identity -> Policy -> Plugin Gateway -> KnowledgeAdapter -> Audit`。
- 发往内部知识引擎的检索固定携带 `enable_filters=true`。
- 内部结果统一为 `hits[{title, snippet, score}]`；审计仅记录来源、平台知识库 ID 和命中数，不记录问题、文档名或正文。
- 已验证 `KB-IT-SERVICE` 可以映射到内部的“IT 服务知识库”并完成真实只读检索。

当前未实现：

- AI 员工与具体知识库的多对多授权表。
- 单次请求同时检索多个内部知识库及跨库结果重排。
- 火山“检索应用”的 API 对接。

目前权限授权仍以 `knowledge-l1`、`knowledge-l2`、`knowledge-l3` 插件级别为主。拥有 `knowledge-l2` 授权不等于已经实现了对某个具体 L2 知识库的独立授权。增加正式 AI 员工前，应先完成第 8 节的权限模型决策。

## 2. 关键代码入口

| 文件 | 作用 |
|---|---|
| `backend/app/services/internal_knowledge_adapter.py` | 调用内部只读 Chunk Retrieval API，转换返回结构 |
| `backend/app/services/knowledge_adapter.py` | 根据 `DWP_KB_MODE` 选择 Mock、本地 RAG 或内部 Adapter |
| `backend/app/services/config.py` | 读取内部端点、身份字段、认证值和知识库映射 |
| `backend/app/services/gateway.py` | Policy 后执行 Adapter，并写安全审计摘要 |
| `backend/app/services/knowledge_registry.py` | 解析平台知识库并选择数据等级插件 |
| `backend/app/services/chat.py` | 给模型提供可用知识库清单并执行 `search_knowledge` 工具 |
| `mock-data/seed.json` | Demo AI 员工、插件授权和平台知识库登记 |
| `backend/tests/test_internal_knowledge_adapter.py` | 内部 Adapter、错误处理、Gateway 和审计的离线测试 |
| `tools/internal_kb_probe.py` | 独立只读连通性探测，不经过平台业务链路 |

业务模块不得直接调用内部 HTTP 接口或直接实例化 Adapter。平台内统一调用：

```python
gateway.search_knowledge(
    db,
    employee_id="<digital-employee-id>",
    knowledge_base_id="<platform-kb-id>",
    query="<question>",
    trace_id="<trace-id>",
)
```

对应的服务间 HTTP 入口是 `POST /internal/knowledge/search`。

## 3. 本地配置

从 `backend/.env.example` 建立本地 `backend/.env`。真实值只能由环境负责人通过受控渠道提供，不得写入 Git、聊天、工单正文或截图。

```dotenv
DWP_KB_MODE=internal
DWP_INTERNAL_KB_BASE_URL=<service-root-without-api-path>
DWP_INTERNAL_KB_X_ORG=<org>
DWP_INTERNAL_KB_X_TENANT=<tenant>
DWP_INTERNAL_KB_X_USER=<user>
DWP_INTERNAL_KB_AUTHORIZATION=<authorization-value>
DWP_INTERNAL_KB_ID_MAP={"<platform-kb-id>":<internal-kb-id>}
```

`DWP_INTERNAL_KB_BASE_URL` 只能填写服务根地址。Adapter 会追加：

```text
/marketing_agent/api/v2/rag/chunk/retrieval
```

映射示例只表达结构，不代表各环境的真实 ID：

```dotenv
DWP_INTERNAL_KB_ID_MAP={"KB-IT-SERVICE":123,"KB-REG-INTERNAL":456}
```

同事需要从目标环境重新查询并确认内部 ID，不能照搬其他租户或环境的数字 ID。

## 4. 先做只读连通性探测

探测工具独立于正式 Adapter，适合确认网络、租户身份和知识库可见性。将同一组连接信息放入 Git 忽略的 `tools/.env`，不要提交该文件。

```powershell
# 在仓库根目录列出匹配的知识库
powershell -ExecutionPolicy Bypass -File .\tools\run_internal_kb_probe.ps1 list -Keyword "IT服务"

# 确认目标 ID 后做一次只读检索
powershell -ExecutionPolicy Bypass -File .\tools\run_internal_kb_probe.ps1 retrieve `
  -KbId <internal-kb-id> `
  -Question "VPN 怎么申请？"
```

预期：列表能看到目标知识库；检索返回相关片段和分数。不要把真实响应内容保存到仓库。

## 5. 启动平台并验证正式链路

Windows PowerShell：

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.seed --reset
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开终端，从仓库根目录启动前端：

```powershell
pnpm install --frozen-lockfile
pnpm --filter @dwp/frontend dev
```

若公司网络不能访问公共 npm registry，请使用公司批准的镜像。不要把个人代理凭据写入仓库。

检查地址：

- 后端健康检查：`http://127.0.0.1:8000/health`
- OpenAPI：`http://127.0.0.1:8000/docs`
- 前端：`http://127.0.0.1:5173/`

可直接验证服务间入口：

```powershell
$body = @{
  employee_id = "DT-E10281"
  knowledge_base_id = "KB-IT-SERVICE"
  query = "VPN 怎么申请？"
  trace_id = "HANDOFF-INTERNAL-001"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/internal/knowledge/search" `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

预期响应同时满足：

```text
ok=true
decision=allow
data.source=internal
data.knowledge_base_id=KB-IT-SERVICE
data.hits 至少包含一个结果（测试问题应确保库内有相关内容）
```

## 6. 如何证明不是 Mock

不要仅根据回答内容判断。使用本次调用的 `trace_id` 查询审计：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/audit?trace_id=HANDOFF-INTERNAL-001"
```

内部检索的 `result_summary` 应类似：

```json
{"source":"internal","knowledge_base_id":"KB-IT-SERVICE","hit_count":5}
```

判据：

- `source=internal`：真实内部 Adapter。
- `decision=allow`：平台 Policy 放行。
- `knowledge_base_id`：本次使用的平台知识库。
- `hit_count`：内部检索返回的片段数量。

审计中不应出现问题正文、文档名、命中片段或 Authorization。

## 7. 增加一个内部知识库

例如新增“内规”知识库：

1. 用只读 `list` 探测确认目标环境中的知识库名称和内部 ID。
2. 在 `mock-data/seed.json` 的 `knowledge_bases` 中登记稳定的平台 ID，例如 `KB-REG-INTERNAL`，并设置正确的 `data_level`、员工类型和部门范围。
3. 在部署环境的 `DWP_INTERNAL_KB_ID_MAP` 中增加平台 ID 到内部 ID 的映射。
4. 重建本地种子数据；生产环境应使用迁移或管理接口，不应执行 Demo reset。
5. 调用 `/internal/knowledge/search` 验证响应 `source=internal`。
6. 用同一 `trace_id` 查询审计，确认知识库、决策和命中数正确且没有内容泄漏。
7. 增加离线测试，使用 `httpx.MockTransport`，不得让自动化测试访问真实内部服务。

只增加环境映射但不登记平台知识库，会返回“知识库资源不存在”；只登记平台知识库但不增加映射，会返回“未配置平台知识库映射”。

## 8. 增加 AI 员工或多知识库权限前必须确认

产品或权限负责人需要给出明确矩阵：

| AI 员工 | 可访问平台知识库 | 动作 | 数据等级 | 是否需审批 |
|---|---|---|---|---|
| `<employee-id>` | `KB-IT-SERVICE` | `read` | `L2` | 否 |
| `<employee-id>` | `KB-REG-INTERNAL` | `read` | `L2` | 否 |

当前 `employee_plugin_grant` 只授权到 `knowledge-l2` 这类插件，不包含 `knowledge_base_id`。要实现“一个 AI 员工仅能访问指定的 IT、内规、外规知识库”，推荐新增独立的员工-知识库授权关系，并在以下两个位置统一使用：

1. `accessible_knowledge_bases()` 生成模型可见的知识库清单时过滤。
2. `search_knowledge()` 执行检索前再次强制校验，避免模型或调用方伪造知识库 ID。

多知识库问题还需要选择一种行为：

- 路由后单库检索：先判断问题相关知识库，再调用一个库。
- 授权库并行检索：逐库做 Policy 校验和内部调用，合并、去重、重排后再交给模型。
- 火山检索应用：只有拿到并验证对应 API 文档后才能采用，当前接入文档只确认单个 `kb_id` 的 Chunk Retrieval。

在具体知识库授权落地前，不要把插件等级授权描述成知识库级隔离。

## 9. 自动化验证

所有内部 Adapter 测试均为离线测试，不需要真实凭据：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests\test_internal_knowledge_adapter.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
```

前端：

```powershell
cd ..
pnpm --filter @dwp/frontend test
pnpm --filter @dwp/frontend build
```

## 10. 上传前安全检查

```powershell
git status --short
git diff --check
git diff -- backend/.env tools/.env
git check-ignore backend/.env tools/.env
```

上传前必须确认：

- `backend/.env` 和 `tools/.env` 均被 Git 忽略且未进入暂存区。
- 没有真实 Authorization、Token、内部基础地址和真实响应内容。
- 没有内部系统截图、终端日志或数据库文件。
- `backend/.env.example` 和 `tools/internal_kb_probe.env.example` 只有空占位符。
- 测试通过后再提交；不要提交 `node_modules`、`.venv`、`dist`、SQLite 或缓存文件。

## 11. 交接时需要拿到的答案

1. 每个环境的内部服务根地址、租户身份和认证信息由谁通过什么 Secret 系统提供？
2. 平台知识库 ID 与内部知识库 ID 的权威映射由谁维护？不同环境是否不同？
3. 每个 AI 员工具体允许访问哪些知识库？拒绝和审批规则是什么？
4. 一个问题涉及多个授权知识库时，采用单库路由、并行检索还是火山检索应用？
5. 内部接口的超时、限流、认证过期和 SLA 要求是什么？
6. 生产数据库如何迁移知识库和授权数据，谁有发布权限？
7. 验收问题集和期望来源由哪个业务负责人确认？
