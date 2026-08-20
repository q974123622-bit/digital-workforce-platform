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
| L1 普通 | 公开制度、FAQ、培训材料 | 数字分身与虚拟员工默认可读 |
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

### 虚拟员工（Virtual Employee）

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

## 7.5 Secret / Config 边界（Sprint 3）

- 所有内部 endpoint / Token / Credential 只能通过环境变量或 secure config 引用（`backend/app/services/config.py`，`DWP_*` 命名）。
- 禁止：写入 Git、写入 Prompt、写入日志。
- InternalKnowledgeAdapterStub 仅保留接口与配置结构；真实接入由正式员工在 Secure Overlay 受控环境完成。

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
