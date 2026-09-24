# dsh 可插拔框架架构（三层解耦）

> 面向长期运维：把「变化频率不同」的三样东西拆到三个可独立替换的位置，
> 让 dsh 框架升级退化为**换目录 + 重建容器**，不再需要构建/传输/导入数百 MB 镜像。
>
> 落地日期：2026-09-17 ｜ 适用目录：`deploy/offline/`

---

## 0. 一句话结论

| 维度 | 改造前 | 改造后 |
| --- | --- | --- |
| 升级 dsh 版本 | 改 `Dockerfile.dsh` → `docker build`（约 8 分钟）→ `save` → 传输 638MB → `load` → 重建 7 个容器 | `ops/dsh-install-runtime.sh 0.1.5-rc.1` → `ops/dsh-switch.sh --all 0.1.5-rc.1` |
| 改一个插件开关 | 重建 638MB 镜像 + 全量重建容器 | `ops/profile-switch.sh <rev> --all`（只重建容器，秒级） |
| 回滚 | 找回旧镜像 tar / 旧 tag | `ops/dsh-rollback.sh`（改指针 + 重建，秒级） |
| 出错影响面 | 全量（7 个 harness 一起换） | 可先灰度 1 个AI员工验证 |
| 升级安全性 | 靠人肉读 diff | 静态契约门禁 + 真实执行预演 + 切换后容器内契约校验 |

---

## 1. 为什么需要三层

改造前的耦合（2026-09-17 实测）：

- harness 容器只挂载了 `dsh-home` 与 `workspace` 两个卷；
- **dsh 本体（`/usr/local/lib/node_modules/@deepseek-ai/dsh`）与平台 profile（`/opt/dwp/profile`）都烘在镜像里**；
- 于是「改一个插件禁用项」也要重建并重新分发 638MB 镜像。

而 `backend/app/services/runtime_manager.py` 只有两处硬编码引用 `/opt/dwp/profile`（`cp` profile 三件套），
**没有任何地方引用 dsh 的安装路径** —— 这给了我们一个关键自由度：
> 只要 `/opt/dwp/profile` 这个挂载点路径不变，后端业务代码就可以零改动。

---

## 2. 三层结构

```
┌─ L0 底座镜像  ${DWP_DSH_IMAGE}（默认 dwp-dsh:rc6，冻结）
│    只提供：node 22 运行时 + 一份「灾备兜底」的旧 dsh
│    永不随版本升级而重建
│
├─ L1 框架层    ./runtime/dsh-<version>/   →  容器内 /opt/dsh-runtime (ro)
│    多版本并存；通过 compose 的 PATH 前置解析生效，backend 无需感知
│
└─ L2 Profile 层 ./profile/<rev>/          →  容器内 /opt/dwp/profile (ro)
     插件开关 / 模型路由 / MCP 通道；与框架版本完全正交
```

### 2.1 L1 如何生效（关键机制）

`docker exec` **不继承** entrypoint 里的 `export`，但**继承 compose `environment:` 定义的容器环境变量**。
因此在 `compose.yaml` 的 `x-harness-common.environment` 里设置：

```yaml
PATH: /opt/dsh-runtime/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
```

后端 `runtime_adapter.py` 里的 `exec_in_container(name, ["dsh", "--profile", ...])`
就会命中 `/opt/dsh-runtime/bin/dsh`，**调用代码一行都不用改**。

`runtime/` 下的目录布局是 npm global prefix 布局：

```
runtime/dsh-0.1.5-rc.1/
├── bin/dsh -> ../lib/node_modules/@deepseek-ai/dsh/lib/bin.js   （相对软链，挂载后仍可解析）
├── lib/node_modules/@deepseek-ai/dsh/...                        （240 个包嵌套在包内，自包含）
└── MANIFEST.json                                                 （版本/来源/校验值）
```

### 2.2 必须 fail-fast

Docker 对**不存在的 bind 源会静默创建 root 属主空目录**。如果不显式断言，
`DWP_DSH_RUNTIME` 写错路径时容器照样「启动成功」，实际却回落到镜像内置的旧 dsh
—— 这正是「以为升级了其实没升级」的静默错配。因此 entrypoint 第一件事就是：

```sh
[ -x /opt/dsh-runtime/bin/dsh ] || { echo "FATAL: ..." >&2; exit 1; }
[ -f /opt/dwp/profile/cordis.yml ] || { echo "FATAL: ..." >&2; exit 1; }
```

后端 `runtime_manager.ensure_container()` 也加了同样的运行时断言。

### 2.3 dsh-home 里的 bundle 软链（重要，但不需手工处理）

`harness/<ID>/dsh-home/profiles/node_modules/` 是一张 **2.1MB 的软链农场**，
每条软链指向绝对路径。实测结论：

- 用镜像内置 dsh 跑一次 → 软链指向 `/usr/local/lib/node_modules/...`
- 换到 `/opt/dsh-runtime` 挂载后再跑 → **dsh 自动重建软链农场**，全部指向 `/opt/dsh-runtime/...`

也就是说切换 runtime **不需要手工清理 dsh-home**；但必须校验它确实被改写了，
否则就会出现「CLI 是新版、bundle 还是旧版」的错配。
`ops/lib.sh` 的 `smoke_harness()` 就负责这件事（`dsh-base` 软链必须以 `/opt/dsh-runtime` 开头）。

---

## 3. 版本真相与目录布局

`deploy/offline/versions.env` 是**唯一版本真相**：

```ini
DWP_DSH_IMAGE=dwp-dsh:rc6                    # L0 底座（冻结）
DWP_DSH_RUNTIME=./runtime/dsh-0.1.0-rc.6     # L1 全局默认
DWP_DSH_RUNTIME_VERSION=0.1.0-rc.6
DWP_PROFILE=./profile/current                # L2 指针
DWP_PROFILE_REV=v1-20260910
DWP_DSH_TELEMETRY_MODE=DISABLED              # 堡垒机无外网，必须关闭
DWP_DSH_RUNTIME_AI_GENERAL=                  # per-employee 灰度覆盖（留空=沿用全局）
DWP_DSH_RUNTIME_AI_INVESTMENT=
... （共 7 个）
```

`start.sh` 与 `ops/dsh-switch.sh` 会把它**展开**进 `.env`（compose 读取），
展开时把 `./runtime/current` 这类软链指针解析成具体目录，避免 Docker 解析 bind 源软链产生歧义。

> `.env` 是生成物，**不要手工编辑其中的 `DWP_DSH_RUNTIME*`**；改 `versions.env` 或用 ops 脚本。

```
deploy/offline/
├── versions.env                      唯一版本真相
├── runtime/
│   ├── dsh-0.1.0-rc.6/               L1 旧版本（回滚目标；可由底座镜像现场物化）
│   ├── dsh-0.1.5-rc.1/               L1 新版本
│   ├── current -> dsh-0.1.0-rc.6     人可审计的指针（脚本以 versions.env 为准）
│   └── <ver>/MANIFEST.json           版本号 / 来源 registry / sha256 / node 版本 / 生成时间
│   └── <ver>/GATE.json               最近一次门禁结论
├── profile/
│   ├── current -> v1-20260910        L2 指针
│   ├── v1-20260910/                  cordis.yml / cordis.patch.yml / package.json / MANIFEST.json
│   └── MANIFEST.json                 版本索引（根目录，不挂载进容器）
└── ops/                              运维工具集（见第 4~6 节）
```

---

## 4. 升级门禁（两道）

### 4.1 静态契约门禁 —— `ops/dsh-preflight.sh`

纯离线、不联网、不调模型，几秒完成：

1. 校验 runtime 目录结构（`bin/dsh` 可执行、`MANIFEST.json` 版本自洽）
2. 用**可写 profile 副本 + 空 dsh-home** 起一次性容器跑 `dsh --dump-config`
   （必须用可写副本：dsh 会改写 `cordis.yml`，实测 3B → 223B）
3. `ops/dsh-dump-parse.js` 用 dsh 自带的 `js-yaml` 把合成树解析成 JSON
4. `ops/dsh-contract-check.js` 与 `ops/baseline/contract.json` 逐条比对
5. 与 `ops/baseline/dump-config-<version>.txt` 快照做 diff（同版本必须逐字节可复现）

> **为什么必须用正规解析器**：早期探测脚本用 `grep -A3 "- id: X"` 判断 `disabled`，
> 而 `disabled: true` 不一定紧跟在 `- id:` 之后 3 行内，导致把已禁用的
> `tool-web`/`tool-todo`/`tool-ralph` 误报为「启用」。解析器以顶层 `^- id:` +
> 条目内 `^  disabled: true` 为唯一口径，实测 88 条目 / 12 项禁用 / 无重复 id。

`contract.json` 只约束**版本无关的不变量**，因此上游新增工具不会误报：

| 规则 | 内容 |
| --- | --- |
| 必须禁用 | 10 个模型可见工具（`tool-web`/`tool-subagent*`/`tool-workflow`/`tool-ralph`/`tool-jobs`/`tool-pwsh`/`tool-skill`） |
| 启用的 `tool-*` 白名单 | 6 项：`tool-result-pruner`（框架内部，非模型可见）+ 5 个本地工具 `tool-bash`/`tool-fs`/`tool-fs-search`/`tool-todo`/`tool-goal` → **任何其他启用态工具都会被拦下** |
| 必须启用 | `dwp-platform-tools` / `approval` / `sandbox-policy` / `system-prompt` / `agent-default-model` / `llm-deepseek` / `session-telemetry-otel` |
| 模型绑定 | `agent-default-model.config.model` 必须等于 `config.env` 的 `DEEPSEEK_MODEL` |
| MCP 通道 | `dwp-platform-tools` 的 `name`/`transport`/`config.url`/`config.headers` 必须齐全 |
| 超时链不变量 | `toolCallTimeoutMs/1000 >= 委派等待 + max(岗位超时, 分身超时) + 缓冲`（否则委派会静默失败，见 `backend/app/services/config.py` 顶部） |
| patch 悬空白名单 | 只允许 `tool-subagent-report`、`tool-str-replace-editor`（当前 profile 已删掉这两行、无悬空；白名单保留仅为兼容旧 profile，超出即失败） |

### 4.2 真实执行预演 —— `ops/dsh-canary.sh`

一次性容器 + 真实模型凭据真跑**两轮**，校验平台依赖的 **stdout 契约**：

- 退出码 0
- stdout 非空、体积合理（1..65536 字节）
- **无 ANSI 转义序列**（平台按纯文本解析，转义序列会污染回答）
- **无回车刷新残留**（进度行原地刷新会变成一坨覆盖文本）
- **不是 JSON**
- stderr 无 `FATAL` / `Cannot find module` / `EACCES` / `CONTEXT_WINDOW` 等致命关键字

两个场景：

| 场景 | 任务 | 额外断言 |
| --- | --- | --- |
| `plain` | 纯文本问答，不调用工具 | 只跑上面的通用契约 |
| `tool` | 写 `notes.txt` → bash 读回 → 只回复文件内容 | ① 产物真的落盘（本地工具能干活）；② stdout 没有混进进度行 / 待办标记 / 思考过程 |

预演刻意把平台 MCP 指向一个不可达哑地址（任务也要求不调用平台工具），
因此它只验证「dsh 主链路 + 模型链路 + 本地工具 + stdout 契约」，不验证平台 MCP 工具面。

### 4.3 2026-09-23 的实测结论

| 版本 | 顶层条目 | 禁用项 | 悬空 patch id | stdout 契约 |
| --- | --- | --- | --- | --- |
| `0.1.0-rc.6`（原基线） | 82 | 19 | 无 | 92 字节纯文本 |
| `0.1.5-rc.1`（当前基线） | 88 | 12 | 无 | 纯文本（两轮产物见 `ops/baseline/canary-0.1.5-rc.1.*`） |

- rc.1 比 rc.6 **多出 8 个框架内部条目**（`storage*` / `session-*` / `web-fetch-http` / `plugin-package-inventory-deepseek` / `deepseek-llm-api-extensions`），**没有新增模型可见工具**
- 2026-09-23 起 profile 有意放开 5 个本地工具（技能脚本可执行 + 长任务自己找文件、自己记进度），
  同时删掉两行悬空 patch，**禁用项 15 → 12、悬空 warning 归零**
- **安全边界没有回退**：放开的都是纯本地能力，外部副作用仍为零；`tool-web`（无外网）/
  `tool-skill`（需联网装包）/`tool-jobs`（跨轮存活）/subagent 全家继续禁用

---

## 5. 灰度切换与回滚

### 5.1 切换（`ops/dsh-switch.sh`）

```
./ops/dsh-switch.sh AI-GENERAL dsh-0.1.5-rc.1   # 灰度：只切 1 个AI员工
./ops/dsh-switch.sh --all dsh-0.1.5-rc.1        # 全量
```

流程（任一步失败自动回滚）：

1. **门禁** —— 复用 `runtime/<name>/GATE.json`，缺失或 `--force` 时现场跑 preflight
2. **预演** —— `dsh-canary.sh`
3. **记录回退指针** —— 快照 `versions.env` → `ops/state/versions.env.previous`，
   并记录本次影响的服务列表 → `ops/state/last-switch.env`
4. **改版本真相** —— 灰度写该员工的 `DWP_DSH_RUNTIME_<ID>`；全量写 `DWP_DSH_RUNTIME` 并清空全部覆盖键
5. **仅重建目标容器** —— `docker compose up -d --no-deps --force-recreate <services>`
6. **冒烟** —— 挂载源 / dsh 版本 / bundle 软链 / **容器内合成树契约校验**（把解析器与契约
   `docker cp` 进真实容器再跑一遍，是「实际生效」的最强证明）

### 5.2 回滚（`ops/dsh-rollback.sh`）

```bash
./ops/dsh-rollback.sh --status   # 查看回退指针
./ops/dsh-rollback.sh            # 恢复 versions.env + 重建受影响实例 + 冒烟
```

旧版本目录长期保留在 `runtime/` 下，回滚**不需要构建镜像、不需要网络**，秒级完成。

### 5.3 状态可见（`ops/dsh-status.sh`）

一条命令列出：全局版本真相、可用 runtime 版本（含体积与门禁状态）、
每个 harness 实例实际生效的 dsh 版本 / 挂载源 / bundle 软链指向、最近一次切换记录。

---

## 6. Profile 层（L2）

Profile 真源从 `docker/dsh-profile/`（已标记 DEPRECATED）迁移到 `deploy/offline/profile/<rev>/`。

```bash
./ops/profile-switch.sh --list                            # 列出可用版本
./ops/profile-switch.sh --new-rev v2-20261001             # 从 docker/dsh-profile 建新版本
./ops/profile-switch.sh v2-20261001 --all                 # 切换
./ops/profile-switch.sh --reindex                         # 重建 MANIFEST（改了 profile 内容后）
```

校验口径：
- `cordis.patch.yml` / `package.json` → 字节级 sha256
- `cordis.yml` → **语义比对**（去注释后必须是 `[]`），因为 dsh 会在文件头追加注释

`./rebuild.sh harness-profile` 已改为委托本脚本；`./rebuild.sh all` **不再包含 harness-profile**
（这是历史上最危险的隐式回退点：一次 `rebuild.sh all` 会把框架悄悄打回镜像内置的旧版本）。

---

## 7. 离线交付

`package-bastion.sh` 的 runtime 处理：

- tar 阶段的 `--exclude='*/node_modules'` 会**连带排掉 `runtime/*/lib/node_modules`**，
  因此 `runtime/` 整体从 tar 排除，随后单独 `cp -a` 复制（与既有 `images/` 做法一致）。
- **默认只随包发布「无法从底座镜像物化」的版本**：与镜像内置版本相同的 runtime
  （如 `dsh-0.1.0-rc.6`）由堡垒机侧 `deploy.sh` 用 `ops/dsh-install-runtime.sh <ver> --from-image`
  现场生成（约 245MB，几秒完成），显著减小传输包体积。
- 需要强制随包发布时用 `--runtime <dir-name>` 或 `--all-runtime`。

堡垒机侧 `deploy.sh` 的新增环节：

- 阶段 0：三层就位性预检（缺 runtime 只 warn，不直接失败）
- 阶段 2b：镜像导入后，对缺失的 runtime 目录执行 `--from-image` 物化，仍缺失才失败
- 阶段 5：`./rebuild.sh all` 只重建 backend/frontend
- 阶段 8：打印三层版本与 ops 常用命令

---

## 8. 硬约束与已知坑

| # | 约束 | 原因 |
| --- | --- | --- |
| 1 | **`/opt/dwp/profile` 挂载点路径不可改** | `runtime_manager.py` 两处硬编码 `cp /opt/dwp/profile/...` |
| 2 | entrypoint 必须 fail-fast | Docker 对不存在的 bind 源静默建空目录 → 会「以为升级了其实没升级」 |
| 3 | Compose `<<:` 是**浅合并** | 服务自带 `volumes:` 会整体替换 anchor 的，所以 7 个服务各自显式列全 4 条挂载 |
| 4 | 每个服务用独立的 `DWP_DSH_RUNTIME_<ID>` 变量 | 避免嵌套默认值插值（`${A:-${B}}` 在 compose 里不可靠） |
| 5 | `--dump-config` 会改写 profile 目录 | 门禁必须用可写副本，不能对 `:ro` 挂载跑 |
| 6 | `dsh --dump-config` 会重建 dsh-home 的 bundle 软链 | 切换后必须校验软链指向，防止 CLI/bundle 版本错配 |
| 7 | `rebuild.sh` **不读 `.env`** | 必须显式 `export DWP_IMAGE_TAG` / `DWP_SOURCE_ROOT=../..` |
| 8 | 必须先 `./start.sh` 再 `./rebuild.sh` | rebuild 内部的 `docker compose up` 依赖 start.sh 生成的 `.env` |
| 9 | `terminate_task` 的 pkill 模式 | runtime 挂载后进程路径变为 `/opt/dsh-runtime/bin/dsh`，模式已改为 `[d]sh --profile` + `[d]sh.*bin/dsh`（`[d]` 避免 pkill 命中自身父 shell） |
| 10 | 日志安全 | ops 日志写 `ops/logs/`（umask 077）；**不得**输出 `docker compose config` 的完整环境变量（含 Authorization / API Key） |
| 11 | 遥测 | 堡垒机无外网，`DSH_TELEMETRY_MODE=DISABLED`（取值来自 `dsh-http-proxy`，**不是** `off`） |
| 12 | RHEL 7 的 bash 4.2 | `set -u` 下空数组 `"${arr[@]}"` 会报 unbound；脚本里用 `${arr[@]+"${arr[@]}"}` 或先判长度 |

---

## 9. 把同一模式套用到其他组件

三层解耦的通用形态是：

> **不可变的运行时底座（L0） + 可换目录的组件本体（L1） + 可换目录的配置/策略（L2）**，
> 三者都通过挂载点契约与调用方解耦，调用方只认固定路径/环境变量。

要在别的组件上复用，只需要满足三个条件：

1. 组件本体在容器里的路径可以通过**挂载点**替换（不是编译进镜像）
2. 调用方只依赖**固定的路径或环境变量**（如 `PATH`、`/opt/dwp/profile`），不依赖具体版本
3. 有一个**可离线执行的等价性判据**（如 `--dump-config` 合成树），能把「升级是否安全」变成可自动化的比对

然后照抄 `ops/` 的四件套即可：`install-*`（生成版本目录）、`*-preflight`（静态门禁）、
`*-canary`（真实预演）、`*-switch` / `*-rollback`（灰度切换与秒级回滚）。

---

## 10. 速查

```bash
cd deploy/offline

# 状态
./ops/dsh-status.sh
./ops/dsh-status.sh --verbose
./verify.sh --deep

# 新增一个 dsh 版本（开发机，内网源）
./ops/dsh-install-runtime.sh 0.1.5-rc.1
./ops/dsh-install-runtime.sh 0.1.0-rc.6 --from-image      # 从底座镜像物化

# 门禁 + 预演
./ops/dsh-preflight.sh dsh-0.1.5-rc.1
./ops/dsh-preflight.sh dsh-0.1.5-rc.1 --save-baseline     # 首次引入该版本时登记基线
./ops/dsh-canary.sh dsh-0.1.5-rc.1

# 切换 / 回滚
./ops/dsh-switch.sh AI-GENERAL dsh-0.1.5-rc.1
./ops/dsh-switch.sh --all dsh-0.1.5-rc.1
./ops/dsh-rollback.sh
./ops/dsh-rollback.sh --status

# Profile
./ops/profile-switch.sh --list
./ops/profile-switch.sh --new-rev v2-20261001
./ops/profile-switch.sh v2-20261001 --all
```
