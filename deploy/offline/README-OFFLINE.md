# AI 员工平台：堡垒机离线一键部署包

这个目录可整体复制到另一台堡垒机运行。服务器不需要安装 Python、Node.js、pnpm、Nginx 或 npm 依赖；它们均已封装进 Docker 镜像。

## 运行前提

- Linux x86_64 / amd64
- Docker Engine 26+，Docker Compose v2.27+
- 当前用户能够执行 `docker info`
- Docker 数据目录至少 6 GiB 可用空间
- 堡垒机能够访问配置的模型网关
- 默认 Web 端口为 `8080`

> 本包需要挂载 `/var/run/docker.sock`，因为后端必须管理四个独立 Harness 实例。请只部署在受控内网服务器，勿向互联网暴露。

## 包含的服务

| 服务 | 容器/实例 | 用途 |
| --- | --- | --- |
| 前端 | Compose frontend | 聊天、通讯录、能力中心、管理后台 |
| 后端 | Compose backend | 登录、会话、SSE、策略、审计、工具网关 |
| AI员工平台 | `dwp-harness-ai-general` | 综合知识问答 |
| 投资分析AI员工 | `dwp-harness-ai-investment` | 证券与投行知识问答 |
| 张三数字分身 | `dwp-harness-dt-e10281` | 无直接知识库，必要时委派岗位员工 |
| 陈晓萌数字分身 | `dwp-harness-dt-e20999` | 个人分身问答与受限委派 |

当前不包含 AgentTeams、OpenSpec、企业微信正式接入和真实内部知识凭据。知识库默认是 Mock；改为 Internal 时失败会明确报错，不会静默回退。

## 一键启动

上传 ZIP 后执行：

```bash
unzip dwp-ai-employee-platform-offline-0.2.0-linux-amd64.zip -d dwp-ai-employee-platform
cd dwp-ai-employee-platform
sha256sum -c images/SHA256SUMS
chmod +x start.sh stop.sh status.sh backup.sh verify.sh
./start.sh
```

首次启动会要求输入模型 API Key、员工初始密码和管理员初始密码，并自动生成 Harness HMAC 签名密钥。这些值只写入本机 `secrets/`，权限为 `600`，不会显示在 Compose 配置或镜像里。张三、陈晓萌和管理员首次登录后都必须修改初始密码。

启动完成后访问：

```text
http://堡垒机IP:8080
```

更换端口：

```bash
export DWP_HTTP_PORT=18080
./start.sh
```

## 模型与知识库配置

首次启动会从 `config.env.example` 生成 `config.env`。默认配置：

```dotenv
DEEPSEEK_BASE_URL=http://api.llmxt.cisctest:8002/v1
DEEPSEEK_MODEL=ascend-deepseek-v4-flash
DWP_KB_MODE=mock
```

Coding Plan Key 只需替换 `secrets/llm_api_key`，并在 `config.env` 修改 Base URL 和模型名；Harness、Agent 和知识工具协议无需改变。

接内部知识引擎时，把 `DWP_KB_MODE` 改为 `internal`，再通过安全渠道填写 `config.env` 中的内部配置。不要把配置后的文件重新打包或上传 Git。

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
- 员工初始密码：`secrets/employee_initial_password`
- 管理员初始密码：`secrets/admin_initial_password`
- 插件制品和 MockMemory：`data/artifacts/`、`data/dwp.db`

发布 ZIP 不包含 `.env`、真实 Key、数据库、日志、内部地址、认证信息或本机工作区数据。
