# AI员工平台：堡垒机离线一键部署包

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

当前不包含 AgentTeams、OpenSpec 和真实内部知识凭据；企业微信智能机器人接入默认关闭，配置与联调见 `WECOM-INTEGRATION.md`。知识检索只走内部知识引擎（测试 REST / 生产 VeRAG 双通道），配置缺失或数据源不可达时会明确报错，不会静默回退到演示数据。

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

知识检索统一走内部知识引擎，且有**两条通道并存**：

- 测试环境走 REST：在 `config.env` 填写 `DWP_INTERNAL_KB_*`；
- 生产环境走 VeRAG MCP：填写 `DWP_VERAG_MCP_URL` / `DWP_VERAG_MCP_TENANT`，并把 Bearer token 写入 `secrets/verag_mcp_token`。

平台侧每个知识库登记为一个语义化英文 ID，行上同时保存两个环境的远端库 ID，运行期按「优先生产 VeRAG → 只通测试用 REST → 都不可达」自动选路，AI员工授权只配一次、换环境不改。

`DWP_KB_SOURCE_PROBE_ENABLED=1` 会按 `DWP_KB_SOURCE_PROBE_INTERVAL_SECONDS` 定时探测各数据源连通性；不可达的知识库会从AI员工工具清单里摘除，管理端知识资源页保留目录但置灰，并支持一键重测。交付/部署前可先跑一次自检：

```bash
docker compose exec backend python -m tools.check_knowledge_sources
```

不要把配置后的文件重新打包或上传 Git。

模型网关的协议、主机、端口、路径前缀、Chat Completions 相对路径和模型名均可在目标机修改。当前模型接口要求 OpenAI Chat Completions 兼容协议；协议本身不兼容时再修改 `source/backend/app/services/llm.py`，无需改变业务接口。

## 技能市场（内网 SkillHub）

平台把「能力获取」统一成三个市场：**MCP 市场 / 技能市场 / 知识库市场**。三个市场里的资源对所有人可见，员工要用需先提交申请、管理员审批通过后才真正生效。技能市场的货架来自内网 SkillHub：

```dotenv
DWP_SKILLHUB_BASE_URL=http://10.49.70.2   # 留空 = 未接入，管理端页签会明确提示
DWP_SKILLHUB_ENABLED=1
DWP_SKILLHUB_TIMEOUT=30
DWP_SKILLHUB_CACHE_TTL=60
```

管理端「插件」→「技能市场」页签可浏览、搜索、按分类筛选 SkillHub 的全部技能，勾选后批量导入平台货架，也可以直接点「一键导入全部」把目录里还没导入的技能一次灌完；导入即发布，导入记录（成功 / 跳过 / 失败原因）可在同一页签的「导入记录」抽屉查看。首次灌库也可以在容器内用 CLI：

```bash
docker compose exec backend python -m tools.skillhub_sync --dry-run           # 先看目录（含分类与精选标记）
docker compose exec backend python -m tools.skillhub_sync --category 办公效率  # 按分类导入
docker compose exec backend python -m tools.skillhub_sync --resume            # 失败重跑（已导入自动跳过）
docker compose exec backend python -m tools.skillhub_sync --resume --no-intro # 跳过 AI 补简介，省模型额度
```

**批量导入是后台任务**：接口立即返回批次号，页面轮询进度条。原因很实际 —— nginx 代理 `/api/` 的读超时是 300 秒，而一百多个技能光下载加转换就要好几分钟，同步做必然被掐断。中途切页签不影响导入，结果都会记进「导入记录」。

**分类与简介是平台侧自建的**：SkillHub 的列表与详情接口 `category` 字段全部为 null，分类清单却返回 19 类，两边对不上；唯一的线索是 `tags`（而且是字符串形态的 JSON 数组，混有 `null` 与残缺值）。因此平台在导入时做两件事：把 `tags` 与 19 类词表取交集得到业务分类，`推荐/精选/热门` 映射为推荐位；对**不含中文或超过 200 字**的简介调模型补一段中文摘要（失败回落原文首句，原文另存在版本的 `manifest['description']` 里，不会丢）。`tags` 为空的技能拿不到分类，由管理端「技能治理」页签人工标注。

管理端「插件」→「技能治理」页签是导入之后的持续运营入口：行内改业务分类、数据等级、推荐位，下架与恢复上架，以及对单条技能「重生成简介」。注意数据等级改的是授权判定实际读取的字段；下架会停用全部授权，恢复上架**不会**自动恢复授权。

已导入过的技能可以事后单独回填简介或分类：

```bash
docker compose exec backend python -m tools.skillhub_intro --dry-run              # 看哪些需要补简介
docker compose exec backend python -m tools.skillhub_intro --slug docx --slug pdf # 指定技能补简介
docker compose exec backend python -m tools.skillhub_intro --fill-category        # 只补分类，不改简介
docker compose exec backend python -m tools.skillhub_sync --refresh-meta          # 按目录 tags 重算分类与推荐位
```

`--fill-category` 走的是同一套模型调用，但**只写业务分类、不动简介** —— 中文简介本身已经很好的技能用这个补分类，避免把好简介重写坏。`--refresh-meta` 只补「还没有分类」的行，不会覆盖管理端人工标注的结果。

员工侧「技能市场」支持按来源（SkillHub 导入 / 平台共享 / 本人）、业务分类、数据等级筛选与关键字检索，带分页；精选技能在货架顶部单独成组，卡片简介统一折叠三行，完整内容在详情抽屉里看。

SkillHub 的包结构与平台要求不一致（它是 `<slug>/SKILL.md` + `scripts/**`，平台要求根目录 `plugin.yaml` + `SKILL.md`），平台会在导入时自动**打平目录并生成 `plugin.yaml`**，`SKILL.md` 与 `scripts/**` 完整保留。

技能包（含脚本）会入库并展开到 `data/artifacts/skills/<slug>/`，员工被授权后自动投放到其运行容器 `harness/<员工>/workspace/skills/<slug>/`（容器内即 `/workspace/skills/<slug>/`），AI员工按 SKILL.md 的说明在容器内执行脚本。撤回授权即回收。

因此 harness 底座镜像必须带 Python：本包使用 `dwp-dsh:rc6-py`（在 `dwp-dsh:rc6` 之上补齐 Python 3.11 与系统库，构建脚本见 `source/docker/Dockerfile.dsh-runtime`）。`./start.sh` 与 `./verify.sh` 都会自检容器内 `python3` 是否存在；若回退成 `dwp-dsh:rc6`，技能仍能安装与展示，但脚本无法执行。

**安全边界**：放开容器执行能力是刻意的取舍，详见 `docs/SECURITY_BOUNDARY.md` 第 7.2 节，正式上线前需走一次安全评审。

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
5. 在插件治理页「知识库独占分配」核对各AI员工的知识库两两不重叠（一人可多库），张三为 0 个知识库。

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
