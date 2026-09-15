# 目标测试堡垒机部署与测试指南

## 1. 适用环境

本离线包已按以下目标环境制作：

- Red Hat Enterprise Linux Server 7.9；
- Linux x86_64 / amd64；
- Docker Engine 26.1.4；
- Docker Compose v2.27.1；
- overlay2、cgroup v1；
- Docker 数据目录位于大容量 `/app` 文件系统；
- 原 `8080` 端口已被占用，因此本包默认使用 `18080`。

详细环境快照见：

```text
source/docs/TARGET_BASTION_ENVIRONMENT.md
```

## 2. 上传和校验

推荐将 ZIP 上传至：

```text
/app/dwp-ai-employee-platform-package/
```

执行：

```bash
cd /app/dwp-ai-employee-platform-package
sha256sum -c dwp-ai-employee-platform-offline-0.3.0-linux-amd64.zip.sha256
unzip dwp-ai-employee-platform-offline-0.3.0-linux-amd64.zip -d runtime
cd runtime
sha256sum -c images/SHA256SUMS
chmod +x start.sh stop.sh status.sh backup.sh verify.sh rebuild.sh
```

两个 SHA-256 校验都必须显示 `OK`。失败时不要继续运行，应重新传输发布包。

## 3. 配置模型网关

可以在启动前手工准备配置：

```bash
cp config.env.example config.env
chmod 600 config.env
vi config.env
```

修改以下三项：

```dotenv
DEEPSEEK_BASE_URL=http://实际内部模型网关:实际端口/v1
DEEPSEEK_MODEL=实际开放的模型名
DEEPSEEK_CHAT_COMPLETIONS_PATH=/chat/completions
```

最终请求地址为：

```text
{DEEPSEEK_BASE_URL}{DEEPSEEK_CHAT_COMPLETIONS_PATH}
```

因此模型网关的协议、地址、端口、路径前缀、接口相对路径和模型名都可以在目标机修改，不必重建镜像。也可以不提前创建 `config.env`，`start.sh` 会在发现占位配置时交互询问 Base URL 和模型名。API Key 不写入 `config.env`，首次启动时由 `start.sh` 安全读取并保存到本机 `secrets/llm_api_key`。

## 4. 首次一键启动

当前目标机推荐直接执行：

```bash
export DWP_HTTP_PORT=18080
./start.sh
```

脚本会：

1. 检查架构、Docker、Compose、Docker Socket 和磁盘空间；
2. 检查入口端口是否被其他服务占用；
3. 安全读取模型 Key 和三个账号各自的访问密钥；
4. 自动生成 Harness HMAC 签名密钥；
5. 导入前端、后端、前端离线 Builder 和 DSH Harness 镜像；
6. 启动平台服务和七个独立 Harness 实例；
7. 等待后端健康并检查七个 Harness Profile。

成功后访问：

```text
http://目标堡垒机地址:18080
```

`.env` 会保存选定端口，因此以后直接执行 `./start.sh` 仍会继续使用 `18080`。

## 5. 首轮测试模式

首轮保持 `config.env` 中：

```dotenv
DWP_KB_MODE=mock
DWP_MEMORY_MODE=mock
DWP_MCP_RUNTIME_MODE=mock
```

这种模式下：

- 模型请求使用真实内部模型网关；
- 知识内容来自本地 Mock 数据；
- 长期记忆写入本地 MockMemory；
- MCP 完成上传、审核、发布、授权和 Mock 运行验证，但不会访问真实 MCP 服务。

## 6. 基础验收

先执行：

```bash
./status.sh
./verify.sh
```

应确认：

- 前端和后端状态正常；
- `dwp-harness-ai-general` 正常；
- `dwp-harness-ai-investment` 正常；
- `dwp-harness-ai-research` 正常；
- `dwp-harness-ai-fund` 正常；
- `dwp-harness-ai-xyzq` 正常；
- `dwp-harness-dt-e10281` 正常；
- `dwp-harness-dt-e20999` 正常；
- 七个 Harness 都加载 `dwp-knowledge-agent-v2` Profile。

页面测试：

1. 输入张三访问密钥，确认系统直接识别身份并进入员工端；
2. 向“AI员工平台”询问 IT 服务或内规问题；
3. 确认执行过程以 SSE 显示，回答逐字输出；
4. 刷新页面，确认每次对话的执行轨迹仍然存在；
5. 向“投资分析AI员工”询问证券或投行业务问题；
6. 向“研究分析AI员工”询问研报或行业资料分析问题；
7. 确认岗位数字员工的知识库授权没有重叠；
8. 打开张三数字分身，确认其没有直接知识库，并可按 Agent 判断进行一次受控委派；
9. 使用陈晓萌账号确认无法读取张三的插件、对话和长期记忆；
10. 输入管理员访问密钥进入管理后台，检查插件审核、发布和授权流程。

## 7. 接入内部知识引擎

修改 `config.env`：

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

然后重建后端容器配置：

```bash
docker compose up -d --no-deps --force-recreate backend
./verify.sh
```

如果只是地址、路径、身份头或知识库映射变化，不需要重新构建镜像。如果内部协议或响应结构不同，修改：

```text
source/backend/app/services/internal_knowledge_adapter.py
```

再执行：

```bash
./rebuild.sh backend
./verify.sh
```

正式知识检索必须继续经过 `Identity → Policy → Gateway → InternalKnowledgeAdapter → Audit`，且 `enable_filters=true`。Internal 模式失败时不会静默回退 Mock。

## 8. 接入 MCP 工具

扩展模板位于：

```text
source/extensions/mcp-example/
```

复制模板，修改 `plugin.yaml` 中的插件名、分类、工具输入输出 Schema、`endpoint_ref` 和 `credential_ref`。ZIP 中不得包含真实地址和认证信息。

当前正式调用边界固定为：

```text
Harness
→ MCP Tool Gateway
→ HMAC 身份校验
→ 插件绑定与员工授权
→ Policy
→ MCP Adapter
→ External 或 Hosted MCP
→ Audit
```

真实 MCP Runtime 尚需在测试环境按实际协议补充 Adapter。修改区域主要是：

```text
source/backend/app/services/plugin_governance.py
source/backend/app/services/gateway.py
source/backend/app/services/runtime_adapter.py
source/extensions/
```

完成代码修改后执行 `./rebuild.sh backend`。Harness 不得绕过平台网关直接调用外部 MCP。

## 9. 接入内部 mem0

接口就绪后修改：

```dotenv
DWP_MEMORY_MODE=internal
DWP_INTERNAL_MEM0_BASE_URL=
DWP_INTERNAL_MEM0_AUTHORIZATION=
```

若协议不同，修改：

```text
source/backend/app/services/memory_adapter.py
```

记忆命名空间必须保持 `tenant_id + requester_human_no + agent_id`，防止员工和 Agent 之间相互污染。

## 10. 现场修改和离线重建

发布包保留完整、经过清理的 `source/`。以下配置变化不需要构建镜像：

- 模型网关地址、端口、路径和模型名；
- Mock/Internal 知识模式切换；
- 知识库地址、检索路径、身份头和 ID 映射；
- Mock/Internal Memory 模式切换；
- MCP 模式和非敏感引用配置。

源码变化使用：

```bash
./backup.sh
./rebuild.sh backend
./rebuild.sh frontend
./rebuild.sh harness-profile
./verify.sh
```

现有依赖已经包含在离线镜像中。新增 Python 或 npm 依赖时，需要公司内部软件源或重新制作包含依赖的 Builder 镜像。

## 11. 停止、备份和排障

```bash
./status.sh
./backup.sh
./stop.sh
```

排障时仅查看必要日志：

```bash
docker compose logs --tail=200 backend
docker compose logs --tail=100 frontend
```

不得把 `config.env`、`.env`、`secrets/`、数据库或包含个人数据的日志发到公共渠道。

## 12. 会话文件分析验收

附件默认保存在 `data/attachments/`，由 Compose 通过 `DWP_ATTACHMENT_ROOT=/data/attachments` 提供给后端；无需在 Harness 容器内挂载原始文件，Harness 只能经平台 Tool Gateway 读取本轮授权片段。

1. 在协作会话输入区点击回形针，分别上传 `.docx`、文本型 `.pdf` 和 `.xlsx`。
2. 等待附件状态变为“可供分析”，输入“提炼主要结论并标注来源位置”后发送。
3. 展开执行轨迹，确认出现附件接收、检索或读取步骤，回答引用的页码/Sheet 来自实际工具结果。
4. 上传 `.doc` 或 `.xls`，应明确显示“需要转换”；上传扫描 PDF 应显示“需要 OCR”，不得当作空文件回答。
5. 刷新页面确认消息附件卡片仍存在；清空会话后，旧附件不得重新出现在当前 generation。

旧 Office 转换和扫描 PDF OCR 通过后续测试环境 MCP 接入；未配置对应插件时系统明确停在待处理状态，不访问公共转换服务。
