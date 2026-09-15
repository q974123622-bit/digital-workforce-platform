# AI 员工平台：堡垒机离线一键部署包

这个目录可整体复制到另一台堡垒机运行。服务器不需要安装 Python、Node.js、pnpm、Nginx 或 npm 依赖；它们均已封装进 Docker 镜像。发布包同时保留经过清理的 `source/` 源码和离线重建工具，便于在测试环境修改知识库、MCP、mem0、模型适配和前端代码。

## 运行前提

- Linux x86_64 / amd64
- Docker Engine 26+，Docker Compose v2.27+
- 当前用户能够执行 `docker info`
- Docker 数据目录至少 6 GiB 可用空间
- 堡垒机能够访问配置的模型网关
- 针对当前目标堡垒机默认 Web 端口为 `18080`，可通过 `DWP_HTTP_PORT` 修改

> 本包需要挂载 `/var/run/docker.sock`，因为后端必须管理五个独立 Harness 实例。请只部署在受控内网服务器，勿向互联网暴露。

## 包含的服务

| 服务 | 容器/实例 | 用途 |
| --- | --- | --- |
| 前端 | Compose frontend | 聊天、通讯录、能力中心、管理后台 |
| 后端 | Compose backend | 登录、会话、SSE、策略、审计、工具网关 |
| AI员工平台 | `dwp-harness-ai-general` | 综合知识问答 |
| 投资分析AI员工 | `dwp-harness-ai-investment` | 证券与投行知识问答 |
| 研究分析AI员工 | `dwp-harness-ai-research` | 证券/投行研报资料检索与分析 |
| 张三数字分身 | `dwp-harness-dt-e10281` | 无直接知识库，必要时委派岗位员工 |
| 陈晓萌数字分身 | `dwp-harness-dt-e20999` | 个人分身问答与受限委派 |

当前不包含 AgentTeams、OpenSpec 和真实内部知识凭据；企业微信智能机器人接入默认关闭，配置与联调见 `WECOM-INTEGRATION.md`。知识库默认是 Mock；改为 Internal 时失败会明确报错，不会静默回退。

会话执行、清空恢复、SSE 重连与 mem0 同步的当前行为见 `source/docs/STABILITY_AND_RUNTIME_RECOVERY.md`。当前团队后端固定为 `DWP_TEAM_BACKEND=builtin`；旧 AgentTeams 配置不会进入运行路径。

## 一键启动

上传 ZIP 后执行：

```bash
unzip dwp-ai-employee-platform-offline-0.3.0-linux-amd64.zip -d dwp-ai-employee-platform
cd dwp-ai-employee-platform
sha256sum -c images/SHA256SUMS
chmod +x start.sh stop.sh status.sh backup.sh verify.sh
./start.sh
```

首次启动会要求输入模型网关 Base URL、模型名、模型 API Key，以及张三、陈晓萌和管理员各自的访问密钥，并自动生成 Harness HMAC 签名密钥。Base URL 和模型名写入权限为 `600` 的本机 `config.env`；Secret 只写入本机 `secrets/`，不会进入镜像。登录页只需输入访问密钥，系统会直接识别身份。

启动完成后访问：

```text
http://堡垒机IP:18080
```

更换端口：

```bash
export DWP_HTTP_PORT=18080
./start.sh
```

## 模型与知识库配置

首次启动会从 `config.env.example` 生成 `config.env`。默认配置：

```dotenv
DEEPSEEK_BASE_URL=http://实际模型网关主机:实际端口/v1
DEEPSEEK_MODEL=实际启用的模型名
DEEPSEEK_CHAT_COMPLETIONS_PATH=/chat/completions
```

Coding Plan Key 只需替换 `secrets/llm_api_key`，并在 `config.env` 修改 Base URL 和模型名；Harness、Agent 和知识工具协议无需改变。

知识检索统一走内部知识引擎：通过安全渠道在 `config.env` 填写 `DWP_INTERNAL_KB_*` 内部配置即可。不要把配置后的文件重新打包或上传 Git。

模型网关的协议、主机、端口、路径前缀、Chat Completions 相对路径和模型名均可在目标机修改。当前模型接口要求 OpenAI Chat Completions 兼容协议；协议本身不兼容时再修改 `source/backend/app/services/llm.py`，无需改变业务接口。

## 包结构与现场修改

```text
├── images/                  # 可直接导入的运行镜像和前端 Builder 镜像
├── source/                  # 清理后的完整应用源码，不含 .git、Secret 和运行数据
├── config.env.example       # 模型、知识库、mem0、MCP 非敏感配置模板
├── secrets/                 # 首次启动时在目标机生成或填写
├── data/                    # 运行数据库和插件制品
├── harness/                 # 四个独立 Harness 工作区
├── rebuild.sh               # 后端、前端和 Harness Profile 离线重建
├── REBUILD-ON-BASTION.md    # 现场修改和回滚说明
├── DEPLOYMENT-AND-TEST.md    # 目标堡垒机部署与测试步骤
└── SOURCE-REVISION.txt      # 源码基线提交和打包信息
```

只修改地址、端口、模型名和 Adapter 配置时无需重建镜像。修改源码后的操作见 `REBUILD-ON-BASTION.md`：

```bash
./rebuild.sh backend
./rebuild.sh frontend
./rebuild.sh harness-profile
```

详细扩展边界见 `source/docs/EXTENSION_CONFIGURATION_GUIDE.md`。MCP 上传包模板位于 `source/extensions/mcp-example/`。

## 自检与运维

```bash
./verify.sh       # 校验前端、后端和四个 Harness Profile
./status.sh       # 状态与最近日志
./backup.sh       # SQLite 在线备份到 backups/
./stop.sh         # 停止服务，保留数据和员工工作区
```

完整业务验收建议：

1. 登录后分别打开“AI员工平台”和“投资分析AI员工”。
2. 各发起一个所属知识库问题，确认出现持续保存的执行轨迹和逐字回答。
3. 向张三数字分身询问制度问题，确认其自身无知识库，并按 Agent 判断委派岗位员工。
4. 刷新页面，确认执行轨迹和回答仍存在。
5. 在能力中心核对两个岗位员工知识库不重叠，张三为 0 个知识库。

## 数据与安全

- 数据库：`data/dwp.db`
- 备份：`backups/`
- 员工工作区：`harness/<员工ID>/`
- 模型 Key：`secrets/llm_api_key`
- Harness 签名密钥：`secrets/harness_signing_secret`
- 张三访问密钥：`secrets/login_key_e10281`
- 陈晓萌访问密钥：`secrets/login_key_e20999`
- 管理员访问密钥：`secrets/login_key_admin`
- 插件制品和 MockMemory：`data/artifacts/`、`data/dwp.db`
- 会话附件与解析结果：`data/attachments/`、`data/dwp.db`

发布 ZIP 不包含 `.env`、真实 Key、数据库、日志、内部地址、认证信息或本机工作区数据。
