# 堡垒机源码修改与离线重建

发布包同时包含可运行镜像和 `source/` 源码。修改配置不需要重建镜像；修改 Python、前端或 Harness Profile 后，可以使用根目录的 `rebuild.sh` 离线覆盖对应镜像。

## 不需要重建的修改

编辑 `config.env` 后重建后端容器即可：

```bash
docker compose up -d --no-deps --force-recreate backend
```

适用于：

- 模型网关协议仍为 OpenAI Chat Completions，仅修改地址、端口或模型名；
- Mock/Internal 知识库模式切换；
- 内部知识引擎地址、检索路径、身份头和知识库 ID 映射；
- Mock/Internal mem0 模式切换；
- MCP Runtime 模式和非敏感引用配置。

模型 Key 位于 `secrets/llm_api_key`。修改后应执行：

```bash
chmod 600 secrets/llm_api_key
docker compose up -d --no-deps --force-recreate backend
```

## 后端 Adapter 修改

主要扩展位置：

```text
source/backend/app/services/internal_knowledge_adapter.py
source/backend/app/services/knowledge_adapter.py
source/backend/app/services/memory_adapter.py
source/backend/app/services/plugin_governance.py
source/backend/app/services/gateway.py
source/backend/app/services/llm.py
```

修改后：

```bash
./rebuild.sh backend
./verify.sh
```

该流程从已安装依赖的后端镜像派生新镜像，不访问公网。它适用于 Python 业务代码、Adapter 和 Mock 数据修改。增加新的 Python 依赖时，需要通过公司内部 PyPI 或单独制作包含依赖的新基础镜像。

## 前端修改

修改 `source/frontend/` 或 `source/shared-schema/` 后：

```bash
./rebuild.sh frontend
./verify.sh
```

发布包包含 `dwp-frontend-builder` 镜像和现有依赖，因此修改已有前端代码无需访问公网。增加新的 npm 依赖时，需要公司内部 npm 源或重新制作前端 Builder 镜像。

## Harness Profile 修改

修改 `source/docker/dsh-profile/` 后：

```bash
./rebuild.sh harness-profile
./verify.sh
```

不得通过 Profile 恢复 Bash、任意文件写入、通用 Subagent、Workflow 或未经 Gateway 约束的网络工具。

## 回滚

`rebuild.sh` 会在覆盖前给旧镜像增加 `rollback-时间戳` 标签。需要回滚时，先查看：

```bash
docker images 'dwp-*' --format 'table {{.Repository}}\t{{.Tag}}\t{{.CreatedAt}}'
```

将目标回滚镜像重新标记为当前标签，再执行 `docker compose up -d --force-recreate`。回滚前先运行 `./backup.sh`。

## 安全边界

- 不要把配置后的 `config.env`、`.env`、`secrets/`、`data/` 和 `backups/` 复制回源码或提交 Git。
- 自动化测试不得访问真实内部知识引擎；使用 `httpx.MockTransport`。
- 真实内部接口联调必须经过 Identity、Policy、Gateway、Adapter 和 Audit 链路。
- Internal 模式失败时明确报错，不允许静默降级到 Mock 或旧 ChatOrchestrator。
