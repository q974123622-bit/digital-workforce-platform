# DeepSeek Harness（dsh）版本升级 SOP

> 适用：`deploy/offline/` ｜ 最后更新：2026-09-17
> 架构与设计原理见 **[DSH-PLUGGABLE-ARCHITECTURE.md](./DSH-PLUGGABLE-ARCHITECTURE.md)**。
>
> 本文是**操作手册**；升级不再需要重建/传输 638MB 镜像，改为「生成版本目录 → 门禁 → 预演 → 灰度切换 → 冒烟」。

---

## 0. 结论速览（2026-09-17 开发机实测）

| 项目 | 结论 |
| --- | --- |
| 唯一可用 npm 源 | `https://mirrors.huaweicloud.com/repository/npm/`（公网 npm 被 SNI 阻断） |
| 当前生产基线 | `0.1.0-rc.6`（底座镜像内置，可由镜像现场物化） |
| 本次升级目标 | `0.1.5-rc.1`（上游 `latest`） |
| 契约核对 | 合成树 88 条目 / 12 项禁用 / 无重复 id / 启用的 `tool-*` 为 `tool-result-pruner` + 5 个本地工具（见下） |
| 安全边界 | **无回退**；rc.1 新增 8 个框架内部条目，**没有新增模型可见工具** |
| 本地工具 | 2026-09-23 起有意放开 `tool-bash`/`tool-fs`/`tool-fs-search`/`tool-todo`/`tool-goal`（技能脚本可执行 + 本地干活），边界仍由 `workspace-write` + `/workspace` 约束 |
| 悬空 patch id | 无（`tool-subagent-report`、`tool-str-replace-editor` 两行已从 profile 删除；白名单保留仅为兼容旧 profile） |
| stdout 契约 | 未变（纯文本、无 ANSI、非 JSON、退出码 0） |
| 遥测 | `DSH_TELEMETRY_MODE=DISABLED`（堡垒机无外网；取值不是 `off`） |

---

## 1. 先分清两条升级粒度（最重要）

| 要改什么 | 走哪条路 | 耗时 |
| --- | --- | --- |
| **dsh 框架本体版本**（0.1.0-rc.6 → 0.1.5-rc.1） | `ops/dsh-install-runtime.sh` + `ops/dsh-switch.sh` | 分钟级（仅首次装包需 1 分钟） |
| **平台 Profile**（插件禁用项 / 模型参数 / MCP 通道） | `ops/profile-switch.sh` | 秒级 |
| **底座镜像**（node 大版本等） | 已冻结，**不要**为升级 dsh 而重建 | — |

> ⚠️ 历史上最容易踩的坑：`./rebuild.sh all` 曾包含 `harness-profile`，
> 一次全量重建会把框架**悄悄打回镜像内置的旧版本**。现已修正：`all` 只重建 backend + frontend。

---

## 2. 阶段 0：上游探测

```bash
cd deploy/offline
./probe-dsh-registry.sh                       # 一键探测容器内可达的 npm 源与版本线
./probe-dsh-registry.sh --image dwp-dsh:rc6   # 指定底座镜像
```

### 渠道判定表

| 探测结果 | 渠道 | 做法 |
| --- | --- | --- |
| 公网 npm 200 | A | 直接装 |
| 公网被阻断 + 国内源 OK | **A'（本环境）** | `ops/dsh-install-runtime.sh <ver> --registry <可用源>` |
| 全部 FAIL | C/D | 索取 `.tgz` 或上游镜像 tar |

注意：`registry.yarnpkg.com` 元数据可查，但其 `dist.tarball` 指回 `registry.npmjs.org`，
**只能查版本，不能装**。

---

## 3. 阶段 1：生成 runtime 版本目录

```bash
# 从内网源安装（默认华为云源）
./ops/dsh-install-runtime.sh 0.1.5-rc.1

# 从底座镜像物化（仅当版本与镜像内置版本一致，例如回滚目标 rc.6）
./ops/dsh-install-runtime.sh 0.1.0-rc.6 --from-image

# 重建已有目录
./ops/dsh-install-runtime.sh 0.1.5-rc.1 --force
```

产物：`runtime/dsh-<version>/{bin/dsh, lib/node_modules/..., MANIFEST.json}`

要点：
- 容器内以宿主机用户身份运行（`--user`），产物归属当前用户，堡垒机 `xyzqadmin` 可直接增删
- 必须用**底座镜像**做容器，保证 node ABI 与生产一致
- 目录布局是 npm global prefix 布局；`bin/dsh` 是**相对软链**，挂载到 `/opt/dsh-runtime` 后仍可解析

---

## 4. 阶段 2：门禁 + 预演

```bash
# 静态契约门禁（离线、不调模型，几秒）
./ops/dsh-preflight.sh dsh-0.1.5-rc.1
./ops/dsh-preflight.sh dsh-0.1.5-rc.1 --save-baseline   # 首次引入该版本时登记基线快照

# 真实执行预演（调模型，约 10-30 秒）
./ops/dsh-canary.sh dsh-0.1.5-rc.1
```

门禁会检查（详见架构文档第 4 节）：目录结构、合成树条目数、10 项必须禁用、
启用的 `tool-*` 白名单、7 个关键通道必须启用、模型绑定与 `config.env` 一致、
MCP 通道 config 键齐全、patch 悬空 id 白名单、**超时链不变量**（profile 的工具调用超时
容得下后端的委派等待 + 单轮执行）、同版本文档逐字节可复现。

预演会跑**两轮**：退出码 0、stdout 非空且体积合理、**无 ANSI 转义**、**无回车刷新残留**、
**非 JSON**、stderr 无致命关键字；其中工具场景还会断言产物真的落盘（`/workspace/notes.txt`）
且 stdout 没有混进进度行 / 待办标记 / 思考过程。

> 门禁必须用**可写 profile 副本**：`dsh --dump-config` 会改写 `cordis.yml`（实测 3B → 223B）。
> 门禁必须用**正规 YAML 解析器**（`ops/dsh-dump-parse.js`）：
> 早期 grep 启发式（`grep -A3 "- id: X"`）会把已禁用的 `tool-web`/`tool-todo`/`tool-ralph` 误报为启用。

---

## 5. 阶段 3：灰度 → 全量切换

```bash
# 1) 灰度：只切 AI-GENERAL（先让一个AI员工跑新版）
./ops/dsh-switch.sh AI-GENERAL dsh-0.1.5-rc.1

# 2) 在界面上向 AI-GENERAL 发起一次真实问答，确认结果正常
#    同时可向 AI-FUND（仍在旧版）发起一次，验证双版本并存

# 3) 全量
./ops/dsh-switch.sh --all dsh-0.1.5-rc.1

# 4) 状态与深度自检
./ops/dsh-status.sh
./verify.sh --deep
```

`dsh-switch.sh` 内部流程：门禁 → 预演 → 快照回退指针 → 改 `versions.env` 并展开 `.env`
→ 清理旧 bundle 软链农场 → 仅重建目标容器 → 冒烟（挂载源 / dsh 版本 / **容器内合成树契约校验**）。
**任一步失败会自动回滚。**

---

## 6. 阶段 4：回滚

```bash
./ops/dsh-rollback.sh --status   # 查看回退指针与上次切换记录
./ops/dsh-rollback.sh            # 恢复 versions.env + 重建受影响实例 + 冒烟
```

旧版本目录长期保留在 `runtime/` 下，回滚**不需要构建镜像、不需要网络**，秒级完成。

---

## 7. 阶段 5：打包与堡垒机交付

```bash
# 开发机
./package-bastion.sh                  # 默认只随包发布「无法从底座镜像物化」的版本
./package-bastion.sh --all-runtime    # 强制随包发布全部 runtime 版本
./package-bastion.sh --runtime dsh-0.1.0-rc.6   # 额外强制包含某个版本

# 堡垒机
cd /home/xyzqadmin/project/ai_employee/deploy/offline
./deploy.sh
```

- 打包时 `runtime/` 整体从 tar 排除，随后 `cp -a` 复制（因为 tar 的 `--exclude='*/node_modules'` 会误伤）
- 与镜像内置版本相同的 runtime（如 rc.6）不随包传输，由堡垒机 `deploy.sh` 阶段 2b 用
  `ops/dsh-install-runtime.sh <ver> --from-image` 现场物化（约 245MB，几秒）
- 堡垒机侧无需外网、无需导入新镜像

---

## 8. 契约核对清单（breaking change 高发点）

> 下面标 **[自动]** 的项已由 `ops/dsh-preflight.sh` 与 `ops/dsh-canary.sh` 自动覆盖；
> 标 **[人工]** 的项需要人判断或跑一次真实用例。

### 8.1 bundle 名 —— ✅ 已核对通过 [自动]

`package.json` 引用 `@deepseek-ai/dsh-base` / `@deepseek-ai/dsh-headless`。
在 `0.1.5-rc.1` 的包清单中**两者均存在**，bundle 名未变。

> 修正记录：旧版本文档曾写「bundle 名未变」但未给出核对方法，现已由合成树条目数断言替代
> （rc.6 82 条 / rc.1 88 条，均为非空且通过契约）。

### 8.2 插件 id 与 `disabled` 安全边界 —— ★ 最高优先级 [自动]

平台在 `cordis.patch.yml` 禁用 10 项。**命名规律未变**：插件 id = 包名去掉 `dsh-` 前缀。

2026-09-23 实测结论（用 `ops/dsh-dump-parse.js` 解析合成树）：

| 版本 | 顶层条目 | `disabled: true` | 启用的 `tool-*` |
| --- | --- | --- | --- |
| `0.1.0-rc.6` | 82 | 19 | `tool-result-pruner` |
| `0.1.5-rc.1` | 88 | 12 | `tool-result-pruner` + `tool-bash` / `tool-fs` / `tool-fs-search` / `tool-todo` / `tool-goal` |

- 后 5 项是 2026-09-23 有意放开的本地工具（技能脚本可执行 + 长任务自己找文件、自己记进度），
  都是纯本地能力、无外部副作用，边界由 `DSH_PERMISSION_MODE=workspace-write` + `working_dir=/workspace` 约束
- 旧文档担心的 `dsh-tool-bash-persistent` / `dsh-tool-pwsh-persistent` / `dsh-tool-cordis` /
  `dsh-tool-present` / `dsh-tool-ask-user` / `dsh-tool-call-timeout-policy` —— **实测均不在 rc.1 的合成树中**
- 契约里「启用的 `tool-*` 必须落在白名单内」是**版本无关的强不变量**：
  上游一旦新增任何启用态工具，门禁立即失败（白名单见 `contract.json` 的 `enabled_tool_allowlist`）

**[人工]** 仍建议每轮升级跑一次「让模型尝试使用被禁工具」的用例，确认被拒。

### 8.3 模型参数结论需重测 [人工]

`cordis.patch.yml` 的注释结论是针对 rc.6 实测的：

- [x] `reasoningEffort: low` 仍被网关接受（网关合法集 xhigh/medium/low，dsh 只发 off/low/high/max）
- [x] `maxTokens` 由 16384 提到 **65536**：dsh 默认 256000 会撑爆 262144 窗口（→ `CONTEXT_WINDOW_EXCEEDED`），
  16K 对长任务又太紧；64K 是折中值。**2026-09-23 canary 两轮均未出现 `context_window`**，
  结论以 `ops/baseline/canary-0.1.5-rc.1.{plain,tool}.err.txt` 为准。

用一个 >6K tokens 的 prompt 验证。

### 8.4 stdout 契约 —— ★ [自动]

平台把 dsh 的 stdout **原样当答案**返回，未做任何解析：

```189:194:backend/app/services/runtime_adapter.py
        if proc.returncode != 0:
            return RuntimeResult(mode="harness", ok=False, result=(proc.stderr or proc.stdout or "").strip()[:500])
        output = proc.stdout.strip()
        if not output:
            return RuntimeResult(mode="harness", ok=False, result="Harness 未返回内容")
        return RuntimeResult(mode="harness", ok=True, result=output)
```

`ops/dsh-canary.sh` 已自动断言：纯文本、无 ANSI、无回车刷新残留、非 JSON、退出码 0。
实测 rc.1 输出 79~131 字节纯文本、stderr 为空。

工具场景（2026-09-23 新增）额外断言：`/workspace/notes.txt` 真的落盘且内容为 `hello-dwp`，
stdout 不得命中进度行（`- [ ]` / `[x]`）、`⏺` / `✻` / `⏵` / `✔`、`Thinking`、`Todo`、
`tokens used` 等泄漏关键字 —— 平台把 stdout 原样当答案，混进任何一条都是可见的污染。
两轮产物落 `ops/baseline/canary-<version>.{plain,tool}.{out,err}.txt` 供人工复核。

### 8.5 环境变量与 CLI 参数 [自动]

- `DSH_HOME`（容器内 `/dsh-home`）、`DSH_PERMISSION_MODE=workspace-write`、`--profile <name>`、
  `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` 语义均未变
- 预演脚本会实际带上这些变量跑通两轮（纯文本 + 真跑本地工具），等价于自动核对

### 8.6 进程路径（影响「停止执行」）—— ✅ 已改造

改造后 dsh 的进程路径随 runtime 挂载变化：

| 布局 | 进程 cmdline |
| --- | --- |
| runtime 挂载（正常） | `node /opt/dsh-runtime/bin/dsh --profile ...` |
| 灾备（镜像内置） | `node /usr/local/bin/dsh --profile ...` |

`runtime_manager.terminate_task()` 已改为同时覆盖两种布局：

```sh
pkill -9 -f '[d]sh --profile' 2>/dev/null || true
pkill -9 -f '[d]sh.*bin/dsh' 2>/dev/null || true
```

（`[d]` 是为了让模式不匹配 pkill 自己父 shell 的 cmdline，避免自杀。）

### 8.7 会话格式 [人工]

`harness/*/dsh-home/sessions/*.jsonl.zstd` 是 dsh 私有格式。
`0.1.5-rc.1` 中存在 `dsh-session-format-v0-to-v1` / `v1-to-v2` / `v2-to-v3` 三个迁移插件
→ **跨版本升级会自动迁移，但不可逆**。

- [ ] 升级前备份 `harness/*/dsh-home/sessions/`
- [ ] 若启动报会话解析错误，清空该目录（历史对话已存平台 SQLite，不影响平台侧记录）

### 8.8 bundle 软链农场 [自动]

`harness/<ID>/dsh-home/profiles/node_modules/` 是 dsh 生成的**派生状态**（软链指向绝对路径）。

- 切换版本时 `dsh-switch.sh` / `dsh-rollback.sh` 会**自动清理**，由 dsh 按新 runtime 重建
- 实测（2026-09-17）：即使该软链仍指向旧路径，**合成树逐字节一致**，
  说明合成树由 runtime 自身的 bundle 决定；因此冒烟里它只做提示，不做硬失败
- 若将来 Profile 的 `package.json` 新增 `dependencies`，需重新评估这条结论

---

## 附录 A：升级检查清单（打印用）

```bash
cat <<'EOF'
[ ] 1. ./probe-dsh-registry.sh                      确认 npm 源可达 + 目标版本存在
[ ] 2. ./ops/dsh-install-runtime.sh <ver>           生成 runtime/dsh-<ver>/
[ ] 3. ./ops/dsh-preflight.sh dsh-<ver>             静态契约门禁通过
[ ] 4. ./ops/dsh-preflight.sh dsh-<ver> --save-baseline   登记基线快照
[ ] 5. ./ops/dsh-canary.sh dsh-<ver>                真实执行预演通过（两轮：纯文本 + 真跑本地工具）
[ ] 6. 备份 harness/*/dsh-home/sessions/
[ ] 7. ./ops/dsh-switch.sh AI-GENERAL dsh-<ver>     灰度切换
[ ] 8. 界面里向 AI-GENERAL 与一个旧版员工各发一次问答
[ ] 9. ./ops/dsh-switch.sh --all dsh-<ver>          全量切换
[ ] 10. ./ops/dsh-status.sh && ./verify.sh --deep
[ ] 11. ./package-bastion.sh                        打离线包
[ ] 12. 堡垒机 ./deploy.sh
[ ] 13. 记录到附录 B
EOF
```

---

## 附录 B：升级记录

| 日期 | 框架版本 | Profile 版本 | 方式 | 结论 | 执行人 |
| --- | --- | --- | --- | --- | --- |
| 2026-08-13 | `0.1.0-rc.6` | `v1-20260910` | 重建镜像（旧路径） | 基线 | — |
| 2026-09-17 | `0.1.5-rc.1` | `v1-20260910` | **runtime 目录切换**（三层可插拔） | 门禁 + 预演通过；灰度验证后全量 | — |

---

## 附录 C：探测原始数据（2026-09-17，开发机）

```text
# 容器内 registry 配置
registry = https://registry.npmjs.org/
node v22.23.2 / npm 10.9.8

# DNS
registry.npmjs.org       -> 解析正常
mirrors.huaweicloud.com  -> 解析正常
www.baidu.com            -> 解析正常

# HTTPS 连通性
https://registry.npmjs.org/     ERR ECONNRESET（SNI 阻断）
https://registry.npmmirror.com/ ERR ECONNRESET
https://www.baidu.com/          HTTP 200

# 可用源
https://mirrors.huaweicloud.com/repository/npm/   OK
  npm install -g @deepseek-ai/dsh@0.1.5-rc.1 -> added 522 packages in 1m

# 上游版本线（截至 2026-09-15）
0.1.0-rc.6 (2026-08-13) -> rc.7 -> rc.8 -> 0.1.1-rc.1/rc.2 -> 0.1.2-rc.1
  -> 0.1.5-rc.1 (latest, 2026-09-10) -> 0.1.5-rc.2 (next) -> 0.1.6-alpha.1 (alpha)

# 安装布局（0.1.5-rc.1）
/usr/local/lib/node_modules/@deepseek-ai/dsh/            CLI 本体（很薄）
  node_modules/@deepseek-ai/                            240 个包嵌套在此（自包含）
/usr/local/bin/dsh -> ../lib/node_modules/@deepseek-ai/dsh/lib/bin.js

# CLI 能力
有 --patch / --dump-config / --dump-default-config / --no-color
无 --output-format
新增 dsh web / dsh plugin（plugin 转发 pnpm，本环境未装 pnpm）
```
