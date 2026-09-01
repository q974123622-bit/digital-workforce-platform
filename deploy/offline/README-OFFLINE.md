# 数字员工平台 Demo：测试环境离线部署

本部署包适用于：

- Red Hat Enterprise Linux 7.9（或兼容发行版）
- x86_64 / amd64
- Docker Engine 26.x
- Docker Compose v2.27+
- 服务器不能访问公网，但可以访问公司内部大模型网关

服务器不需要安装 Python、Node.js、pnpm、Nginx，也不需要执行在线依赖安装。依赖已经包含在离线 Docker 镜像中。

## 安全要求

测试 API Key 不包含在镜像、代码和压缩包中。首次启动时脚本会静默读取 Key，并保存到当前部署目录的 `secrets/llm_api_key`：

- 文件权限为 `600`；
- 不进入 Shell 命令历史；
- 不出现在 `docker inspect` 的 Compose 环境变量中；
- 不得上传 Git、发送聊天、截图或写入镜像。

已经在聊天、工单、截图或 Shell 命令行暴露过的 Key 必须先作废并重新申请。

## 1. 上传文件

通过堡垒机/FileZilla 将整个 ZIP 上传到服务器，例如：

```text
/home/xyzqadmin/0825/
```

 进入终端：

```bash
cd /home/xyzqadmin/0825
unzip dwp-demo-offline-0.1.0-linux-amd64.zip -d dwp-demo
cd dwp-demo
```

如果没有 `unzip`，可以在 Windows 上先解压，再把解压后的整个目录上传。

## 2. 校验镜像包

```bash
sha256sum -c images/SHA256SUMS
```

应该显示：

```text
dwp-demo-images-linux-amd64.tar: OK
```

## 3. 一键启动

```bash
chmod +x start.sh stop.sh status.sh backup.sh
./start.sh
```

脚本会自动：

1. 检查 CPU 架构、Docker 和 Compose；
2. 检查 Docker 数据目录至少还有 2 GiB 可用空间；
3. 记录当前用户 UID/GID，保证数据库和备份文件归当前用户所有；
4. 静默读取新的测试 API Key；
5. 导入离线镜像并启动前后端；
6. 等待健康检查；
7. 从后端容器调用 `ascend-deepseek-v4-flash`，验证容器到内部网关的网络。

默认访问地址：

```text
http://服务器IP:8080
```

如果服务器安全策略只允许指定端口，可在启动前设置：

```bash
export DWP_HTTP_PORT=18080
./start.sh
```

还需要由运维开放“访问者所在网段 → 测试服务器对应端口”。不要直接暴露到互联网。

## 4. 运维命令

查看状态和最近日志：

```bash
./status.sh
```

停止服务但保留数据：

```bash
./stop.sh
```

备份 SQLite 数据库：

```bash
./backup.sh
```

备份位于 `backups/`。数据库持久化在 `data/dwp.db`，重新启动不会丢失。

## 5. 更换 Key

删除旧 Key 文件后重新启动：

```bash
rm -f secrets/llm_api_key
./start.sh
```

删除动作只针对当前部署目录下的密钥文件。旧 Key 如果已经泄露，仍须由平台管理员在服务端撤销。

## 6. 故障定位

### 宿主机能调用模型，容器不能调用

这通常是 Docker 网桥的 DNS、NAT 或防火墙策略导致。执行：

```bash
docker compose exec backend getent hosts api.llmxt.cisctest
docker compose exec backend python -c "import socket; print(socket.gethostbyname('api.llmxt.cisctest'))"
```

需要运维确认 Docker 网桥网段可以访问 `api.llmxt.cisctest:8002`。

### 页面打不开

```bash
docker compose ps
curl -i http://127.0.0.1:8080/health
```

本机能访问而办公电脑不能访问，说明测试服务器入口端口或访问网段尚未放通。

### 查看后端日志

```bash
docker compose logs --tail=200 backend
```

日志不得粘贴 API Key。当前应用正常情况下不会输出 Key。

## 7. 当前 MVP 运行模式

离线包默认采用稳定演示配置：

```text
模型：ascend-deepseek-v4-flash
知识库：mock
Harness：关闭
AgentTeams：builtin
```

这一步用于先跑通“浏览器 → 平台 → 公司模型网关”的真实闭环。DeepSeek Harness、AgentTeams、企业微信和内部 MCP 应在基础闭环验收后逐项启用，不应一次性全部上线。
