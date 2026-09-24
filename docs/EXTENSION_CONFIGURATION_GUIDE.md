# 测试环境配置与扩展接口指南

## 1. 可修改范围

离线包将配置和代码分为两层：

```text
目标机配置层
├── 模型 Base URL、端口和模型名
├── 模型 API Key Secret
├── 知识库地址、检索路径、身份头和 ID 映射
├── mem0 地址和认证
└── MCP Endpoint/Secret 引用

源码 Adapter 层
├── LLM Provider 协议适配
├── InternalKnowledgeAdapter 请求和响应适配
├── MemoryAdapter / InternalMemoryAdapter
└── MCP Adapter 与 Tool Gateway 扩展
```

地址和凭据不能写入源码。只有当目标服务协议、字段或认证方式不同于当前契约时才修改 Adapter。

## 2. 模型网关

目标机编辑根目录 `config.env`：

```dotenv
DEEPSEEK_BASE_URL=http://实际模型网关主机:实际端口/v1
DEEPSEEK_MODEL=实际启用的模型名
DEEPSEEK_CHAT_COMPLETIONS_PATH=/chat/completions
```

API Key 单独保存在：

```text
secrets/llm_api_key
```

当前 Provider 使用 OpenAI 兼容请求，最终地址为：

```text
POST {DEEPSEEK_BASE_URL}{DEEPSEEK_CHAT_COMPLETIONS_PATH}
```

所以 Base URL 可同时修改协议、主机、端口和前缀。只要新网关兼容 Chat Completions，更换网关和模型无需修改代码。若采用其他协议，只修改：

```text
source/backend/app/services/llm.py
```

业务路由、Harness、知识工具协议和前端接口保持不变。

## 3. 内部知识引擎

首次运行保持：

```dotenv
DWP_KB_MODE=mock
```

联调时切换：

```dotenv
DWP_KB_MODE=internal
DWP_INTERNAL_KB_BASE_URL=
DWP_INTERNAL_KB_RETRIEVAL_PATH=/实际检索路径
DWP_INTERNAL_KB_X_ORG=
DWP_INTERNAL_KB_X_TENANT=
DWP_INTERNAL_KB_X_USER=
DWP_INTERNAL_KB_AUTHORIZATION=
DWP_INTERNAL_KB_ID_MAP={}
```

正式链路固定为：

```text
Identity
→ Policy
→ Gateway
→ InternalKnowledgeAdapter
→ 内部知识引擎
→ Audit
```

如果请求字段或响应结构不同，修改 `internal_knowledge_adapter.py`；如果要增加另一种知识来源，实现 `KnowledgeAdapter` 并在 `knowledge_registry.py` 注册。不得在聊天路由或 Harness 中直接调用内部地址。

## 4. MCP 工具

MCP 属于 Plugin，当前本地默认：

```dotenv
DWP_MCP_RUNTIME_MODE=mock
```

发布包提供 `source/extensions/mcp-example/`，可以复制为新的 MCP 插件上传包。Manifest 只描述工具 Schema、逻辑 Endpoint 引用和 Secret 引用，不保存真实地址或凭据。

MCP 正式调用必须经过：

```text
Harness
→ MCP Tool Gateway
→ HMAC 身份
→ AgentPluginBinding
→ EmployeePluginGrant
→ Policy
→ MCP Adapter
→ External 或 Hosted MCP
→ Audit
```

测试环境接入新的 MCP 时，需要补充对应 Adapter/Runtime 实现和 MockTransport 测试；不能允许 Harness 直接访问 MCP 服务。`tools/list` 仅返回当前 Agent 已发布、已授权且已启用的工具。

### 4.1 盈米 External MCP（基金数据）接入示例

平台已内置标准 Streamable HTTP MCP Client（`services/external_mcp_client.py`）与盈米适配器
（`services/external_mcp_adapter.py`），可连接盈米 AI 开放平台（`YM-Stargate`）获取基金数据工具。

配置（`config.env` / `secrets`）：

```dotenv
DWP_QIEMAN_MCP_URL=https://stargate.yingmi.com/mcp/v2
# DWP_QIEMAN_MCP_API_KEY=   # 可选，优先级高于 secrets 文件
DWP_QIEMAN_MCP_TIMEOUT=20
```

`api_key` 属敏感凭据，写入 `secrets/qieman_api_key`（Compose 挂载为 `/run/secrets/qieman_api_key`），
不写入 `config.env`、不提交进 ZIP。

运行机制：

- 工具动态发现（`tools/list`，进程级缓存 5 分钟），不静态声明 Schema，适配盈米 `listChanged=true`；
- 对外工具名统一加 `qieman__` 前缀，避免未来多 MCP 源同名冲突；
- `agent_tools_mcp` 的 `tools/list` 仅对已授权员工（`EmployeePluginGrant` + `AgentPluginBinding`）追加盈米工具；
- seed 时仅当配置了 `DWP_QIEMAN_MCP_URL` 才注册 `qieman-fund-mcp` 插件，并授权 AI-INVESTMENT / AI-RESEARCH / AI-FUND。

连通性验证：

```bash
docker compose exec backend python -c "from app.services.external_mcp_adapter import QiemanMcpAdapter; print('tools:', len(QiemanMcpAdapter().list_tools(force=True)))"
```

### 4.2 兴证e家员工服务 External MCP（内网，双鉴权头）

`services/external_mcp_adapter.py` 内置 `ehome_adapter`（插件 `xyzq-ehome-mcp`，工具前缀
`ehome__`），连接兴证e家员工服务（会议室 / 餐卡 / 考勤休假 / 通讯录 / 资讯 / OA 送审等
查询类工具，共 17 个）。

配置（`config.env` / `secrets`）：

```dotenv
DWP_EHOME_MCP_URL=http://10.32.253.43/ehome/mcp/claw
# DWP_EHOME_MCP_API_KEY=   # 可选，优先级高于 secrets 文件
DWP_EHOME_MCP_TIMEOUT=20
```

**双鉴权头**：

| 头 | 级别 | 来源 |
|---|---|---|
| `X-MCP-API-KEY` | 应用级、静态、必需 | `secrets/ehome_mcp_api_key`（运维预置） |
| `X-MCP-JWT` | 用户级、按人签发、会过期 | **员工本人在会话内的「需要补充信息」气泡里粘贴一次** |

用户级令牌的边界：按 `(plugin_id, human_no)` 加密存储（复用 `mcp_credentials` 的
HMAC-SHA256 流密码 + Encrypt-then-MAC），只回掩码；明文不落库、不进对话正文、不进执行事件、
不进审计、不进 Prompt；平台解析 `exp` 判定过期，缺失/过期时重新弹气泡。到期前还会在
**员工侧插件中心**常驻提醒（页头汇总 + 服务行内标注剩余天数与到期时间，提前天数由
`DWP_MCP_CREDENTIAL_WARN_DAYS` 控制，默认 7 天），提醒接口只回状态与到期时间。详见
`docs/SECURITY_BOUNDARY.md` 的「7.6 员工自助凭据」。

运行机制：

- `tools/list` 只用应用级 key 完成握手，不要求员工先提供令牌；
- `tools/call` 前先做**事前预检**（缺用户级凭据/已过期 → 直接产出「待补全」，不发无效请求），
  调用时按人注入两个头；服务端返回缺参文本时走**事后识别**（`Missing required header`、
  `缺少请求参数`、`keyword 必填`）同样转成待补全；
- 命中后落 `tool_input_request` 记录并 emit `input_required` 事件，前端在执行轨迹里弹出输入
  气泡；用户填完提交 → 凭据加密存到本人名下 → 以「原提问 + 补充说明」**自动续跑一轮**；
- seed 时仅当 `config.ehome_mcp_configured()`（URL + 应用级 key 均非空）才注册插件，并授权
  AI-XYZQ / DT-E10281 / DT-E20999。

连通性验证（只校验应用级鉴权，不需要用户令牌）：

```bash
docker compose exec backend python -m tools.check_ehome_mcp
```

## 5. 内部 mem0

首次运行：

```dotenv
DWP_MEMORY_MODE=mock
```

接口就绪后：

```dotenv
DWP_MEMORY_MODE=internal
DWP_INTERNAL_MEM0_BASE_URL=
DWP_INTERNAL_MEM0_AUTHORIZATION=
```

如果内部 mem0 的路径或数据结构与当前草案不同，只修改 `memory_adapter.py`。命名空间必须继续保持：

```text
tenant_id + requester_human_no + agent_id
```

不得破坏用户和 Agent 之间的记忆隔离。

## 6. 修改后的最低验证

```bash
./backup.sh
./rebuild.sh backend       # 修改 Adapter 时
./rebuild.sh frontend      # 修改前端时
./verify.sh
./status.sh
```

知识库 Adapter 修改还需运行专项测试，测试必须使用 MockTransport。真实联调验收必须确认返回 `source=internal`，并在安全审计中确认知识库 ID、命中数和决策，不记录问题、知识片段、内部地址或认证信息。
