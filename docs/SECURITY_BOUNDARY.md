# 数据与权限边界

> 版本：v0.3（2026-08-17，Sprint 1.5 Architecture Freeze）
> 适用：券商内部 PoC 一周期。目标：仓库内无任何真实数据/凭据；实习生零真实数据接触；LLM 链路只含虚构数据；权限判定集中。
> 配套：`docs/ARCHITECTURE.md`、`docs/API_CONTRACT.md`（冻结版）、`docs/DEVELOPMENT_HANDOFF.md`。

## 1. 角色与数据边界

| 角色 | 可访问 | 禁止 | 本周职责 |
|---|---|---|---|
| A 正式员工（架构/总装） | 共享仓库全部代码 + Mock 数据；Secure Overlay（仓库外，仅 A/B 机器） | 未经批准将真实内容发送至 DeepSeek/Codex；将 secure 文件放入仓库 | 后端主链路、契约、LLM、编排、联调 |
| B 正式员工（安全/企业资源） | 共享仓库 + Secure Overlay + 真实系统接口形态（仅验证，不接数据） | 向实习生暴露真实端点/Key/知识库；本周接入真实数据链路 | Policy、Gateway、Sandbox、安全门禁 |
| C 实习生（前端） | 共享仓库 `frontend/` + `mock-data/` + 冻结的 API 契约 | Secure Overlay、真实端点、真实 Key、内部文档/截图 | 全部前端页面 |
| D 实习生（Mock/测试） | 共享仓库 `mock-data/` + `tests/` + mock adapters | 同上 | 种子数据、Mock 插件、测试、脚本 |

铁律：**两名实习生在开发全过程中只能接触仓库内文件与虚构数据。**

## 2. 数据分级

| 等级 | 示例 | 规则 |
|---|---|---|
| L1 普通 | 公开制度、FAQ、培训材料 | 数字分身与AI员工默认可读 |
| L2 内部 | 部门流程、内部文档、脱敏员工信息 | 按部门/岗位授权；正式员工分身可读指定 L2，实习生分身 Deny；VE 仅按 grant 授权 |
| L3 敏感 | 客户、交易、凭据、未脱敏数据 | 本仓库不存在真实 L3；读取一律 Deny。仅虚构 workflow/RPA 的特定执行动作可进入 Approval，批准后仍必须重新经过 Gateway 执行并留审计 |

## 3. 权限模型

**有效权限 = Subject 策略 ∩ 插件授权 ∩ 数据范围 ∩ 环境（Sandbox/网络）∩ 任务授权**

- 决策优先级：**Deny > Approval > Allow**。
- 默认拒绝：未显式授权的动作一律 Deny，并写入审计。
- 唯一评估入口：Policy Engine。前端、Runtime、插件内部不得做权限判断。
- 前端展示的「允许/拒绝」必须来自后端决策结果；前端不得自行解释权限。

## 4. 统一资源访问链（强制）

**业务模块不能直接访问知识库、数据库、Workflow 或 RPA。**

```text
Employee Identity
  → Policy Engine（唯一授权源，默认拒绝）
  → Plugin Gateway（唯一执行入口）
  → Adapter（Runtime / Knowledge / Workflow / RPA）
  → Enterprise Resource
```

违反该链路的直接访问视为安全违规：数据范围、插件授权、Sandbox 策略全部失效。Code Review 一票否决。

## 5. 数字对象边界

### 数字分身（Digital Twin）

- 权限 ⊆ 真人权限 ∩ 分身策略，永不超过真人。
- 演示对账：DT-E10281（正式员工）可读 L2 内部制度；DT-E20999（实习生）访问 L2 被 Deny。

### AI员工（Virtual Employee）

- 独立 VE 工号；必须绑定一名真人 Owner（无 Owner 不允许创建）。
- 权限来自 Owner/HR 策略 + 插件授权 + 数据范围，不自动继承平台能力。
- 演示对账：VE-0001 仅 HR L1/L2 + 指定插件（知识库、ADP）。

### Agent Team

- 成员权限相互独立；Leader 不自动继承成员权限。
- 审批由策略触发（`decision_mode=approval`）；批准只改变授权状态，不等于执行成功，原子任务必须携带服务端审批凭证重新经过 Policy/Gateway/Adapter。

### RPA

- 以插件或员工形态存在，只执行结构化任务；权限规则与插件一致，经 Plugin Gateway 调用。

## 6. LLM 与外部服务边界

1. **唯一出口**：LLMProvider 是唯一读取 `DEEPSEEK_API_KEY` 的代码点，其余模块无法接触 Key。
2. **SAFEMODE**：`DEMO_LLM_SAFEMODE=true`（默认开启）时，任何 `source != demo` 的 prompt 段将被拒绝发送并告警；工具结果、知识内容、对话消息均带来源标签。
3. **Harness 同规则**：其模型调用经同一环境约束；本周 Harness 输入内容必须为虚构数据。
4. **Codex 边界**：开发助手（Codex）不读取 Secure Overlay；Secure Overlay 不在共享仓库内，不在本周开发工作区。
5. **演示话术**：全程声明「全部为虚构数据 + P0-lite 模板化协作」，不得宣称已接入真实系统/真实知识库。
6. **Key 存放（Sprint 4）**：`DEEPSEEK_API_KEY` 仅存于本地 gitignored `backend/.env` 或进程环境变量，绝不提交 Git；`backend/.env.example` 只含占位名。模型名默认 `v4-flash`，官方接口不支持时以 `DEEPSEEK_MODEL` 覆盖为 deepseek-chat（见交接文档）。

## 7. Sandbox 边界

- Sandbox 只做执行隔离：位置（remote-only / local）、网络（internet deny）、目录挂载（/workspace/{employee_id}）、资源与超时限制。
- **Sandbox 是执行隔离，不是权限定义来源**：授权顺序固定为先 Policy（读取 subject 绑定的 sandbox 配置，当前内嵌于 `digital_employee`：`location` / `internet` / `max_data_level` / `allowed_domains`），后启动 Sandbox；被拒请求不启动。
- 降级：Docker 不可用 → local 模式，仍写入 Sandbox 决策审计，UI 展示一致。
- Sprint 3 落地：`SandboxPolicy`（runtime_location / internet_access / filesystem_scope）+ MockExecutor + `POST /internal/sandbox/run`；remote_only 请求 local → POLICY-004 DENY；internet=deny 请求非 none 网络 → POLICY-003 DENY。

## 7.2 技能脚本执行边界（2026-09-22 变更，**需正式安全评审**）

背景：接入内网 SkillHub 技能市场后，技能包里的 `scripts/*.py` 是技能的实际执行体
（如 `python3 scripts/office/pack.py`）。为让「已授权技能」能真正跑起来，harness 容器
的执行能力被有意放开，边界随之变化，逐条列明：

- **放开了什么**：`profile/current/cordis.patch.yml` 中 5 项本地工具由禁用改为启用 ——
  `tool-bash`、`tool-fs`（2026-09-22，技能脚本执行）、`tool-fs-search`、`tool-todo`、`tool-goal`
  （2026-09-23，长任务自己找文件、自己记进度）；**其余 10 项工具保持禁用**。
  `DSH_PERMISSION_MODE` 由 `read-only` 改为 `workspace-write`。
  底座镜像由 `dwp-dsh:rc6` 换成 `dwp-dsh:rc6-py`（前者 + Python 3.11）。
  这 5 项都是**纯本地能力、无外部副作用**：不发起网络请求、不触碰平台能力、不改动容器外任何东西。
- **代价（必须知情）**：容器内**可以执行任意代码**。技能脚本来自内网 SkillHub 的第三方投稿，
  平台只做结构校验（禁可执行二进制/`.sh`、禁 secret 键、禁网络声明），**不做代码审计**。
- **仍然守住的红线**：
  1. 沙箱根 = 容器工作目录 = `/workspace`（compose `working_dir`），容器内其他路径不可写；
  2. 技能包只投放到 `/workspace/skills/<slug>/`，且只投放**已授权**（`agent_plugin_binding`
     三态齐备）的技能，撤回授权即回收（`runtime_manager.deploy_skills`）；
  3. 平台仍是唯一出网口：技能包内不得声明 `endpoint/url/network`，脚本对外发请求不改变
     平台侧知识/模型调用仍需 Policy 放行的事实；
  4. `ops/baseline/contract.json` 的 `enabled_tool_allowlist` 已登记这 5 项（+ 框架内部的
     `tool-result-pruner`），任何新增的启用态 `tool-*` 都会被门禁 `ops/dsh-preflight.sh` 拦下。
- **回退**：`DWP_DSH_IMAGE` 改回 `dwp-dsh:rc6`、profile 里 5 项工具改回 `disabled: true`、
  `DSH_PERMISSION_MODE` 改回 `read-only`，重跑 `ops/dsh-preflight.sh` 并同步 MANIFEST 与基线。
- **上线前必须完成**：①安全团队评审（重点是第三方技能脚本的准入与审计）；
  ②技能导入的白名单/人工复核流程；③容器资源与执行超时限制的确认。

## 7.5 Secret / Config 边界（Sprint 3）

- 所有内部 endpoint / Token / Credential 只能通过环境变量或 secure config 引用（`backend/app/services/config.py`，`DWP_*` 命名）。
- 禁止：写入 Git、写入 Prompt、写入日志。
- InternalKnowledgeAdapterStub 仅保留接口与配置结构；真实接入由正式员工在 Secure Overlay 受控环境完成。

## 7.6 员工自助凭据（会话内补全，2026-09-22 变更，**需正式安全评审**）

**背景**：部分外部 MCP 服务（如兴证e家员工服务）的鉴权是**按人签发、会过期**的用户级令牌
（`X-MCP-JWT`），平台无法预置，只能由员工本人提供。为此新增「工具缺参 → 执行轨迹气泡补全 →
提交后自动续跑」链路（`backend/app/services/tool_input.py` + `mcp_user_credential` 表 +
前端 `features/executions/InputRequestCard.tsx`）。

**边界约定**：

- **存储**：用户级凭据按 `(plugin_id, human_no)` 加密存储（复用 `mcp_credentials` 的
  HMAC-SHA256 流密码 + Encrypt-then-MAC），只存密文与末 4 位掩码；明文仅在调用瞬间于内存中
  组装请求头。**明文永不落库、不入对话正文、不入执行事件 payload、不入审计、不入 Prompt。**
- **隔离**：凭据按人隔离，只用于该员工自己发起的调用；他人不可读取、不可借用。
- **回显**：接口只回掩码（`****abcd`）；前端输入框为密码框、默认遮蔽。
- **续跑**：提交后以「原提问 + 补充说明」自动重跑一轮，补充说明只写「已提供并安全保存」，
  不携带明文（`prompt_shared.guard_outbound_prompt` 的凭据包含性校验继续兜底）。
- **过期**：平台解析 JWT 的 `exp` 判定过期；过期或缺失时重新弹气泡，不带着必然失败的令牌调用。
- **到期提醒**（2026-09-24 新增）：员工侧插件中心常驻提醒——页头汇总「有几个服务的令牌需要更新」，
  对应服务行内标注「x 天后过期」或「已过期」并给出到期时间；提前天数由
  `DWP_MCP_CREDENTIAL_WARN_DAYS` 控制（默认 7 天，下限 1）。提醒接口
  （`GET /my/mcp/credential-alerts`）**只回状态与到期时间，连掩码都不回**，
  且按人反查（企业级共享 MCP 上的按人令牌同样会被提醒）。
- **轮换 / 撤销**：员工重复提交即覆盖（轮换）；运维侧删除 `mcp_user_credential` 对应行即撤销。
  轮换 `DWP_MCP_CREDENTIAL_KEY` 会让已存凭据不可解密，需要员工重新补全。
- **审计**：`tool_input_request` 记录谁在什么时候为哪个工具补了哪些字段（只记掩码与字段名），
  审计事件 `input_submit` 同步留痕。
- **忽略**：员工可以忽略一条待补全（`POST .../input-requests/{id}/ignore`），记录落库为
  `cancelled` 并记 `resolved_by`/`resolved_at`，刷新后不再重复提示。忽略**只表示不再提示**，
  不删除任何已保存的凭据、也不改变执行事实。
- **运维凭据**：应用级 `X-MCP-API-KEY` 仍走 secrets 文件（`secrets/ehome_mcp_api_key`，600），
  绝不通过气泡向员工索要。
- **上线前必须完成**：①安全团队评审（重点是「员工在会话里粘贴凭据」这条新路径）；
  ②明确哪些 MCP 服务允许走该链路（白名单）；③令牌有效期与轮换策略的运维约定。

## 7.7 Harness 工具令牌有效期（2026-09-23 变更）

**背景**：dsh 调用平台 MCP（知识检索 / 附件 / 技能 / 委派）时携带一枚短期工具令牌
（`backend/app/services/harness_token.py`，HMAC-SHA256 签名，绑定员工、请求人、trace、委派深度）。
单轮执行上限从 300/360 秒提到 900/1200 秒后，原来 300 秒签发、600 秒硬上限的令牌会让
长任务跑到第 10 分钟后所有平台 MCP 调用 401。

- **改了什么**：令牌有效期改为按单轮上限推导（`max(role, twin) + 300` = 1500 秒），
  硬上限由 600 提到 3600 秒；数字收在 `backend/app/services/config.py` 一处。
- **边界不变**：令牌仍然是短期、带签名、按执行签发；不含任何明文凭据（凭据只在容器 entrypoint
  注入，令牌只带员工标识与 trace）；执行结束即失去意义；验签失败或过期一律拒绝。
- **回退**：改 `DWP_HARNESS_TOKEN_TTL_SECONDS`（下限自动取单轮上限），或恢复代码默认值。

## 8. 仓库卫生规则

- `.gitignore` 覆盖：`deepseek-harness/`、`higress/`、`.env`、`node_modules/`、`__pycache__/`、`*.db`、`*.log`、secure 路径。
- ✅ 已处理（2026-08-17）：`higress/tools/appservice_tokens.txt`（含 token 字样）已移出工作区，备份至 `E:\Desktop\CISC\external_tokens_backup\higress_tools_appservice_tokens.txt`。
- 禁止提交：真实 Token/Key/Endpoint、真实知识库内容、内部截图/日志。
- 所有种子数据与知识内容必须为虚构（`mock-data/`），字段结构可仿真，内容不得由真实内部数据脱敏复制。

## 9. 安全门禁（阻塞项，对应 PLANS.md P3-05）

- 仓库扫描：无真实 Token/数据/Endpoint。
- SAFEMODE 验证：构造非 demo 段被拒发的单测通过。
- 越权直呼插件：未授权插件返回 Deny 且入审计。
- 实习生环境：无 Secure Overlay 文件；C/D 提交内容不涉及真实来源。
