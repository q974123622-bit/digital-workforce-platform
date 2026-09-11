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
