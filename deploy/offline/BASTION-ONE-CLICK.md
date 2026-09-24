# 堡垒机一键部署（离线 / 无外网）

从开发机打包，到堡垒机一条命令完成部署。适用目标环境：Kylin V10 / RHEL 7.9 等
x86_64 / Docker 26 + Compose v2 / 无公网。

---

## 0. 前置事实（本包已内置）

| 项 | 值 |
|---|---|
| 离线镜像 tag | `0.4.0-offline`（`dwp-backend` / `dwp-frontend` / `dwp-frontend-builder`）+ `dwp-dsh:rc6-py` |
| 模型网关 | `http://10.56.35.240:8002/v1`，模型 `ascend-qwen3.6-27b` |
| 内部知识引擎 | `http://10.57.180.65`（测试 REST 通道，库 751 / 756 / 755） |
| 入口端口 | `18080` |
| Harness 实例 | 7 个：AI-GENERAL / AI-INVESTMENT / AI-RESEARCH / AI-FUND / AI-XYZQ / DT-E10281 / DT-E20999 |
| 登录方式 | 登录 key（`DWP_ENABLE_LEGACY_PASSWORD_LOGIN=0`，密码登录已关闭） |

**目标环境不通的外部服务，本包已全部关闭，界面上不会出现**：

| 服务 | 端点 | 处置 |
|---|---|---|
| 盈米 AI 开放平台 MCP | `stargate.yingmi.com` | `DWP_QIEMAN_MCP_URL` 置空 |
| 兴业证券智能投顾 MCP | `developer.xyzq.cn` | `DWP_XYZQ_MCP_URL` 置空 |
| 兴证e家员工服务 MCP | `10.32.253.43` | `DWP_EHOME_MCP_URL` 置空 |
| SkillHub 技能市场 | `10.49.70.2` | `DWP_SKILLHUB_BASE_URL` 置空 + `DWP_SKILLHUB_ENABLED=0` |

前三个置空后，`seed.ensure_*_mcp()` 直接返回，**连插件行都不会建**，所以插件市场、
MCP 接入、运行能力核验三处都不会出现这些条目；SkillHub 关闭后管理端「技能市场」
页签不渲染。全部是「不显示」，不是「显示成不可用」。

> 必须**在首次启动前**改完：`seed` 只 insert 不 delete，已经建出来的插件行不会被清掉。
> 若以后防火墙放通了某条，把对应 URL 填回去、重建 backend 镜像即可恢复。

真实凭据预置在 `deploy/offline/secrets/`（权限 600），执行过程无任何交互式输入。

---

## 1. 开发机：打包

```bash
cd <仓库>/deploy/offline
./package-bastion.sh
```

可选环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `IMAGES_DIR` | `/root/digitalNPC/dwp-ai-employee-platform-offline-0.4.0-linux-amd64/images` | 离线镜像来源 |
| `OUT_ROOT` | `/root/digitalNPC/bastion-bundle` | 产物输出目录 |

产物：

```
/root/digitalNPC/bastion-bundle/install.sh                                       # 一键部署脚本（同时打进包里）
/root/digitalNPC/bastion-bundle/dwp-ai-employee-platform/                        # 解包后的源目录
/root/digitalNPC/bastion-bundle/dwp-ai-employee-platform-bastion-<时间戳>.tar.gz
/root/digitalNPC/bastion-bundle/dwp-ai-employee-platform-bastion-<时间戳>.tar.gz.sha256
```

打包脚本会：写入 8 个真实 secret、按上表关闭 4 个不可达的外部服务、原样复制离线镜像
（保持 `SHA256SUMS` 有效）、重建 7 个 Harness 空目录骨架，并排除 `.git` / `node_modules` /
`__pycache__` / `frontend/dist` / `data/` / `harness/` 运行时产物 / `backups/` / `.env`。

---

## 2. 上传

```bash
scp /root/digitalNPC/bastion-bundle/dwp-ai-employee-platform-bastion-*.tar.gz \
    /root/digitalNPC/bastion-bundle/install.sh \
    xyzqadmin@<堡垒机IP>:/tmp/
```

---

## 3. 堡垒机：一条命令

```bash
ssh xyzqadmin@<堡垒机IP>
bash /tmp/install.sh /tmp/dwp-ai-employee-platform-bastion-*.tar.gz
```

就这一条。脚本会依次做：Docker / Compose / 架构预检 → 选安装目录 → 解包 →
`deploy/offline/deploy.sh`（8 个阶段，见下）。

安装目录默认 **`/app/dwp-ai-employee-platform`**（`/app` 不可写时回落
`$HOME/dwp-ai-employee-platform`）。`/home` 往往只有 10G 左右，落在 `/app` 这类大盘更稳。

想指定目录或端口：

```bash
bash /tmp/install.sh /tmp/pkg.tar.gz /app/dwp --port 18081
```

### 不想用 install.sh 也可以

```bash
mkdir -p /app/dwp-ai-employee-platform
tar -xzf /tmp/dwp-ai-employee-platform-bastion-*.tar.gz \
    -C /app/dwp-ai-employee-platform --strip-components=1
cd /app/dwp-ai-employee-platform/deploy/offline
chmod +x *.sh
./deploy.sh
```

所有脚本都位置无关（内部 `cd "$(dirname "$0")"`），`rebuild.sh` 需要的
`DWP_SOURCE_ROOT=../..` 由 `deploy.sh` 自动导出，无需手工指定。

### `deploy.sh` 的可选参数

| 参数 | 用途 |
|---|---|
| `--port 18080` | 更换入口端口 |
| `--skip-rebuild` | 跳过源码重建，直接用 0.4.0 镜像内 2026-09-10 的旧源码（不建议） |
| `--no-probe` | 跳过最后的端到端探测 |

### `deploy.sh` 的 8 个阶段

| 阶段 | 内容 |
|---|---|
| 0/8 | 预检：docker / compose v2 / socket / amd64 / Docker 数据盘 ≥6GiB / 端口 / 8 个 secret / `config.env` 关键项 |
| 1/8 | 建目录与权限：`secrets data/artifacts data/attachments backups` + **7 个** `harness/<ID>/{dsh-home,workspace}`，`umask 077` |
| 2/8 | `sha256sum -c images/SHA256SUMS` → `docker load`（镜像已存在则跳过）；必要时从底座镜像物化 dsh runtime |
| 3/8 | 自动探测镜像 tag（优先 `0.4.0-offline`，排除 `rollback-*`）→ `export DWP_IMAGE_TAG` |
| 4/8 | `export DWP_HTTP_PORT` → `./start.sh`（首次启动，生成 `.env` 并拉起全部容器） |
| 5/8 | `export DWP_SOURCE_ROOT=../..` → `./rebuild.sh all`（backend / frontend） |
| 6/8 | `./verify.sh` |
| 7/8 | 端到端探测：容器内真实调模型网关 + 真实调内部知识引擎三个库 |
| 8/8 | 打印访问地址、3 个登录 key、镜像列表 |

**顺序不可调换**：`rebuild.sh` 内部会调 `docker compose up`，而 `.env`（含 `DWP_UID` /
`DWP_DOCKER_GID` / `DWP_IMAGE_TAG`）只有 `start.sh` 才会生成。

---

## 4. 预期输出与通过判据

```
===== 0/8 预检 =====              端口 18080 空闲 / 8 个 secret 齐全
                                  未接入兴证e家员工服务 MCP（端点已置空，界面不出现该插件）
                                  未接入 SkillHub（已关闭），管理端不显示「技能市场」页签
===== 1/8 初始化目录与权限 =====    7 个 Harness 工作区（含 AI-XYZQ）
===== 2/8 校验并导入离线镜像 =====  SHA256 校验通过
===== 3/8 确定镜像 tag =====        DWP_IMAGE_TAG=0.4.0-offline
===== 4/8 首次启动 =====            启动完成：前端、后端和 7 个 Harness 实例均已运行
===== 5/8 用当前源码重建 =====      backend / frontend 都重建
===== 6/8 基础自检 =====            全部基础自检通过
===== 7/8 端到端探测 =====
  --- 模型网关 ---
  模型 HTTP 200 | 含 choices: True
  --- 内部知识引擎 ---
  OK   KB-IT-SERVICE             hits=5 | ['办公IT服务目录.docx', ...]
  OK   KB-SECURITIES             hits=5 | ['融资融券业务QA.xlsx_Sheet1', ...]
  OK   KB-INVESTMENT-BANKING     hits=5 | ['07第二部分-投行业务承揽常见问题.docx', ...]
===== 8/8 完成 =====
  访问地址 : http://<堡垒机IP>:18080
```

**通过判据**：第 7 步模型 `HTTP 200` 且 `含 choices: True`；知识库三行均为 `OK ... hits=N`（N > 0）。

浏览器打开 `http://<堡垒机IP>:18080` 验收。

---

## 5. 登录 key

| 身份 | key |
|---|---|
| 张三 | 见 `deploy/offline/secrets/login_key_e10281` |
| 陈晓萌 | 见 `deploy/offline/secrets/login_key_e20999` |
| 管理员 | 见 `deploy/offline/secrets/login_key_admin` |

明文同时存在于 `deploy/offline/secrets/login_key_*`。
`config.env` 中 `DWP_LOGIN_KEY_*` 为空且 `DWP_ENABLE_LEGACY_PASSWORD_LOGIN=0`，因此登录 key 的唯一来源就是这些 secret 文件。

---

## 6. 故障对照表

| 现象 | 原因 | 处理 |
|---|---|---|
| `端口 18080 被其他服务占用` | 端口冲突 | `./deploy.sh --port 18081` |
| `镜像 tar 校验失败` | 传输损坏 | 重新上传；`scp -C` 或分块传输 |
| `dwp-frontend-builder:0.4.0-offline 不存在` | 镜像未导入 | 确认 `images/` 下 tar 存在且 `docker load` 成功 |
| `start.sh` 报 `后端启动失败` | 见其自动打出的 120 行日志 | 多为 `secrets/llm_api_key` 为空或 `config.env` 缺项 |
| 模型探测 401 | `secrets/llm_api_key` 写错 | 用正确的模型网关 key 重写该文件（值见 `deploy/offline/secrets/llm_api_key`） |
| 模型探测 400 | 模型名不对 | `config.env` 的 `DEEPSEEK_MODEL` 应为 `ascend-qwen3.6-27b` |
| 知识库探测 403 | 租户头 / 授权头不对 | 比对 `DWP_INTERNAL_KB_X_ORG` / `X_TENANT` / `X_USER` / `AUTHORIZATION` |
| 知识库探测 `未配置平台知识库映射` | `DWP_INTERNAL_KB_ID_MAP` 缺失 | 确认含 `{"KB-IT-SERVICE":751,"KB-SECURITIES":756,"KB-INVESTMENT-BANKING":755}` |
| 报 `Harness 容器不存在` | compose 未预建该 harness | 重跑 `./deploy.sh`（第 4 步会拉起全部容器） |
| 前端 build 被 OOM 杀掉 | 内存偏小（<7G）且 swap 不足 | 改在开发机 `./rebuild.sh frontend` 出镜像后传过去，或用 `--skip-rebuild` |
| `docker compose exec` 读不到 `DEEPSEEK_API_KEY` | `docker exec` 不继承 entrypoint 导出的变量 | 探测脚本改为直接读 `/run/secrets/llm_api_key`（本包已如此实现） |

### 想重新接回某个外部服务

改 `deploy/offline/config.env` 里对应的 URL（兴证e家还要确认 `secrets/ehome_mcp_api_key` 非空），
然后重建 backend 镜像让 seed 生效：

```bash
export DWP_IMAGE_TAG=0.4.0-offline
export DWP_SOURCE_ROOT=../..
./rebuild.sh backend
```

---

## 7. 日常运维命令

```bash
cd <安装目录>/deploy/offline
./status.sh     # 查看容器状态
./verify.sh     # 基础自检
./backup.sh     # 备份
./stop.sh       # 停止

# 改完源码后重建单个组件
export DWP_IMAGE_TAG=0.4.0-offline
export DWP_SOURCE_ROOT=../..
./rebuild.sh backend|frontend|harness-profile|all
```

注意：

- `rebuild.sh` **不读 `.env`**，必须显式 `export DWP_IMAGE_TAG`，否则回落 `0.3.0-offline` 导致镜像找不到。
- 每次 rebuild 会先打 `dwp-*:rollback-<时间戳>` 备份 tag。
- 前端产物必须烘进镜像（compose 的 `frontend` 服务没有 bind mount）。
- 修改 `docker/dsh-profile/` 后必须 `./rebuild.sh harness-profile`。
- 不要执行 `docker compose config` 并落盘/外发，它会打印 `env_file` 解析后的全部凭据。
