# 记忆插件（Memory Plugin）设计文档 v0.5

> 版本：v0.5（发散完整版）
> 作者：C（前端负责人）
> 日期：2026-08-18
> 状态：待与 A/B 老师对齐后定稿
> 变更：v0.4 → v0.5 大幅扩展——补全记忆完整生命周期（写入/存储/压缩/读取/消费）、附件记忆、LLM 自动生成用户画像、权限细化示例、记忆消费场景、API 草图。

---

## 1. 一句话定位

**记忆插件 = 平台的"长期记忆系统"**：为每个对象（真人、分身、虚拟员工、团队）持续记录、压缩、检索、并消费"发生过的事"，让 AI 不只"当下聪明"，还能"记得过去、认得熟人、查得到账"。

---

## 2. 设计原则

### 0. 一切即插件（核心理念 ⭐）

本项目继承 DeepSeek Harness 的核心理念：**一切即插件**——所有功能模块都是插件，彼此不紧密耦合，用户需要什么插件就"装"什么，即插即用。未来员工甚至能上传自己写的插件给数字分身使用。

对记忆插件的**具体要求**：

1. **记忆是"可选插件"，不是平台强制的核心**：
   - 记忆与知识库、RPA、Workflow 等平级，都是 `Plugin` 表里登记的一个插件（`type=memory`）；
   - 某个数字员工需要记忆能力 → 通过 `employee_plugin_grant` 给它"装"上；不需要 → 不装，不产生任何记忆。

2. **松耦合，不捆绑**：
   - Chat、Team、Harness 等模块**不直接 import 记忆代码**，而是通过统一的 Plugin Gateway 调用记忆；
   - 记忆插件可独立升级、替换、甚至移除，不影响其他模块。

3. **记忆内部也可插件化**：
   - 存储后端（SQLite → 向量库）、压缩器（LLM / 模板）、画像生成器，都做成可替换的组件（适配器模式）；
   - 未来换更好的实现，只需换一个"零件"。

4. **即插即用**：
   - 记忆插件有 `enable/disable` 开关，启用即开始记录，停用即停止；
   - 前端可直观看到"这个员工有没有装记忆插件"。

5. **与具体会话体系解耦**：
   - 记忆插件不绑定某一种"会话/对话"实现（无论旧 `ChatSession` 还是新 `Conversation`）；
   - 对话结束后，通过统一接口把该沉淀的对话内容写入记忆，两套会话体系都能对接；
   - 会话体系（如何展示/存储对话）与记忆体系（如何提炼/沉淀记忆）分层，互不硬依赖。

### 其余设计原则

1. **一切走治理链**：记忆读写都经 `Identity → Policy → Gateway → Memory Adapter → Store`，不绕过权限；
2. **该记的全记，该省的省**：决策永久、对话压缩、附件摘要，按价值分级存储；
3. **记忆要"用起来"**：不是死存，要能被 Chat 注入、被画像提炼、被前端检索；
4. **最小惊讶**：默认最小权限，涉密默认不可见，写操作默认审批；
5. **逐步演进**：先跑通基础闭环，再上压缩、画像、语义检索。

---

## 3. 现状盘点（基于最新 master）

### 3.1 项目进度（影响记忆插件的部分）

| 能力 | 现状 | 对记忆的意义 |
|---|---|---|
| 身份 + 策略 + 网关 + 审计 | ✅ | 记忆复用这条治理链 |
| 聊天编排 + 会话（ChatSession/ChatMessage） | ✅ | 对话记忆的写入来源 |
| 团队任务编排（team_orchestrator） | ✅ | 跨员工协作记忆挂靠 |
| Harness 接入（runtime_adapter.py + Docker） | ✅ | 记忆桥接 Harness 有样板 |
| 知识库 RAG（Qwen Embedding 向量检索） | ✅ | 未来记忆语义检索可借鉴 |
| 前端管理端（无记忆功能） | ✅ | 待加记忆标签页 |

### 3.2 记忆"三大原材料"与缺口

| 原材料 | 表 | 记什么 | 现状 |
|---|---|---|---|
| 对话记录 | `ChatMessage` | 每次聊了啥 | ✅ 有，未按人聚合、无压缩 |
| 操作+决策+结果 | `AuditEvent` | 谁、何时、调啥、决策、原因、结果 | ✅ 有（=决策记忆，天然可追溯） |
| 长期记忆 | `PersonalMemory`(v1) | 提炼记忆 | ✅ 最简陋版，已合并 master |

**缺口**：没有统一的"长期记忆"层（只有会话和审计），没有压缩、没有画像、没有附件、没有互通检索。

---

## 4. 核心概念：什么是"一条记忆"

一条记忆 = **一个对象 + 一个事件/事实 + 一组控制标签**。

拆成 7 个维度：

| 维度 | 字段 | 取值 | 回答 |
|---|---|---|---|
| 主体 | `subject_type`+`subject_no` | human/twin/virtual/team | 这是谁的记忆 |
| 类型 | `kind` | 见 §5 | 记的是什么 |
| 内容形式 | `content_type` | text/structured | 文字还是结构化 |
| 关联对方 | `related_subject_no` | 对方编号 | 这次和谁交互 |
| 可见性 | `visibility` | public/personal/shared/confidential | 谁能看 |
| 敏感等级 | `data_level` | L1/L2/L3 | 多敏感 |
| 生命周期 | `lifecycle` | active/summarized | 近期完整还是已摘要 |

---

## 5. 记忆的类型体系（kind 全枚举）

| kind | 记什么 | 保存策略 | 举例 |
|---|---|---|---|
| `conversation` | 对话内容 | 近期完整 → 过期摘要 | "王老师问入职流程" |
| `decision` | 让 AI 执行的操作及结果 | **永久完整，可追溯** | "调用了员工查询 MCP，返回成功" |
| `fact` | 稳定事实/偏好 | 长期，可更新 | "王老师偏好周五开会" |
| `attachment` | 上传的文件（提取摘要） | 文件存后台 + 摘要长期 | "上传了《入职指南.pdf》，摘要：..." |
| `summary` | 对话压缩后的精要 | 长期 | "7 月对话摘要：主要讨论项目排期" |
| `profile` | 用户画像 | 长期，LLM 定期更新 | "张三：架构部，偏好简洁直接" |
| `basic_info` | 基本信息 | 长期 | "姓名、部门、岗位" |

---

## 6. 记忆的完整生命周期 ⭐（核心，把功能串起来）

记忆不是"写进去就完事"，它有完整的一生：

```
① 写入（自动为主）
   ├─ 对话 → Chat Orchestrator 每次对话自动写入
   ├─ 决策 → Gateway 每次调用自动写入（来自 AuditEvent）
   ├─ 附件 → 上传时自动提取摘要写入
   └─ 事实/画像 → 系统定期提炼写入

② 存储
   ├─ 结构化字段存 SQLite（PoC）
   └─ 附件文件存后台文件目录

③ 压缩（自动，解决"量大"）
   ├─ conversation 满 30 天 → 触发
   ├─ LLM 把一段对话压缩成"精要摘要"（kind=summary）
   └─ 原始对话标记 summarized 或归档

④ 读取（按权限）
   ├─ 精确查询（主体/类型/对方/时间/等级）
   └─ 语义检索（远期，借鉴 RAG）

⑤ 消费（让记忆"用起来"）
   ├─ 前端查看（管理端记忆标签页）
   ├─ Chat 注入（对话时检索相关记忆，AI"记得过去"）
   ├─ 画像辅助（根据 profile 调整回复风格）
   └─ 决策追溯（查"上次让 AI 干了什么、结果如何"）
```

**这条"生命周期"是本设计的骨架**，后面每一节都是它的展开。

---

## 7. 时间分层与压缩（回答"量"的问题）

| 记忆类型 | 近期（30 天内） | 过期后 |
|---|---|---|
| conversation | 完整保存 | LLM 压缩成摘要，长期保存 |
| decision | 完整保存 | **永久完整，不压缩**（量小、可追溯） |
| attachment | 文件 + 摘要 | 摘要长期，文件按需清理 |
| fact / profile / basic_info | 长期保存 | 长期（可更新） |

**压缩机制**：
- 触发：自动（定时任务 / 每日批处理，PoC 可先手动触发）；
- 方法：LLM 把某个主体过去 30 天外的对话，提炼成 3~5 条精要摘要；
- 产物：`kind=summary` 的记录，标注覆盖的时间范围；
- 用途：摘要既能"回忆历史脉络"，又能作为生成用户画像的原料。

---

## 8. 附件记忆（新，回答"文件"的问题）

类似 QQ/微信聊天记录——能查到发过什么文件，且不必重读文件本身。

设计：

1. **上传时**：附件存后台文件目录；LLM 提取文字 → 生成摘要；
2. **记忆里存摘要**：`kind=attachment`，`content`=摘要，`file_ref`=文件路径；
3. **读取时**：AI 直接用摘要"回忆"，不需要重新读文件；
4. **需要原文时**：按 `file_ref` 取回文件。

好处：文件相关记忆也"精要提炼"，AI 用到时直接回忆，快且省。

---

## 9. 记忆互通与检索（回答"跨对象"的问题）

**目标**：用户和不同 AI 聊的记忆互通；虚拟员工多方记忆按人隔离。

设计：

| 查询意图 | 过滤条件 |
|---|---|
| 王老师的全部记忆（和所有 AI） | `subject_type=human & subject_no=E10021` |
| 王老师和入职助手的记忆 | 上面 + `related_subject_no=VE-0001` |
| 王老师的决策记录 | `kind=decision` |
| HR 助手和某人的记忆 | `subject_type=virtual & subject_no=VE-0002 & related_subject_no=E` |

- 先做**精确过滤**（PoC 够用）；
- 语义检索（"帮我找找我上次聊到项目排期的那段"）后续借鉴 RAG。

---

## 10. 虚拟员工的多方记忆与权限 ⭐

虚拟员工（HR 助手 VE-0002）和 ABCD 四人交流，记忆如何管：

| 场景 | 规则 |
|---|---|
| A 来聊 | 查自己和 VE-0002 的记忆（related=A） |
| 第五人 E（普通） | 只能查自己和 VE-0002 的记忆 |
| E 是领导（权限高） | 可调取 VE-0002 与**所有人**的记忆（Policy 放行） |
| 跨部门查 | 默认拒绝，除非 Policy 授权 |

**权限模型（细化示例）**：

| visibility | 谁能看 | 示例 |
|---|---|---|
| `public` | 所有人 | 基本信息、公开制度 |
| `personal` | 本人 + Owner | 个人对话、偏好 |
| `shared` | 同部门/团队/授权范围 | 团队协作记录 |
| `confidential` | 管理员 / 领导 | 涉密决策、客户数据 |

> PoC 先简单实现（一个固定 admin + 领导按 department 判断），具体权限体系等老师设计后对齐。

---

## 11. 用户画像（LLM 自动生成 + 共享）⭐

**目标**：像"找熟人办业务"——AI 认得你、记得你的风格，回复更贴心。

设计：

1. **自动生成**：LLM 定期（或按触发）从该用户的 `conversation`/`summary`/`fact`/`decision` 提炼出画像，存 `kind=profile`；
2. **画像内容**：沟通风格（简洁/详细）、偏好（时间、格式）、习惯、关注领域、决策倾向；
3. **共享分级**：
   - 不敏感画像（沟通风格、偏好）→ `visibility=shared`，同步给相关虚拟员工，让它们都"认得"这个用户；
   - 涉密画像（如敏感偏好）→ `visibility=confidential`，不公开；
4. **消费**：Chat Orchestrator 在回复前检索该用户画像，注入上下文，调整回复风格。

> 实现难度较高，先规划，PoC 阶段可先做"LLM 生成静态画像 + 简单注入"。

---

## 12. 记忆的消费场景（记忆要"用起来"）

| 场景 | 记忆怎么被用 |
|---|---|
| 用户问"上次那个文档说了啥" | 从附件摘要回忆，不重读文件 |
| 用户问"我上次让你查的客户是谁" | 从 decision 记忆追溯 |
| 用户问"我们上个月聊过什么" | 从 summary 记忆回忆 |
| AI 回复时 | 检索画像，调整风格 |
| 管理端查看 | 记忆标签页展示时间线 |
| 审计 | 从 decision 记忆查"谁让 AI 干了什么" |

---

## 13. 前端形态

| 阶段 | 形态 | 记忆体现 |
|---|---|---|
| 现在（管理端，A 已不维护） | 管理界面 | 员工/虚拟员工详情页加「记忆」标签页（C 来做） |
| 未来（用户端，A 在做） | 类似企业微信/DSH 聊天窗口 | 打开窗口自动加载记忆，跨对话检索 |

---

## 14. DSH 兼容性

- 记忆是平台层 HTTP 接口，运行时无关；
- A 老师已实现 `runtime_adapter.py`（`RuntimeAdapter.run` + `DockerHarnessRuntimeAdapter`），记忆桥接 Harness 照此封装；
- Harness 内部会话记忆（`.dsh/sessions`）负责短期单次对话，平台记忆负责长期跨对话带权限。

---

## 15. 目标数据模型

```python
class MemoryEntry(Base):
    __tablename__ = "memory_entry"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 主体
    subject_type: Mapped[str]          # human | twin | virtual | team
    subject_no: Mapped[str]            # E10281 / DT-E10281 / VE-0001 / TEAM-ONBOARD
    # 类型 + 内容
    kind: Mapped[str]                  # conversation|decision|fact|attachment|summary|profile|basic_info
    content: Mapped[str]               # 文字 / 摘要 / structured JSON
    content_type: Mapped[str]          # text | structured
    # 关联
    related_subject_no: Mapped[str | None]
    trace_id: Mapped[str | None]
    file_ref: Mapped[str | None]       # 附件路径（kind=attachment 时）
    # 控制
    visibility: Mapped[str]            # public | personal | shared | confidential
    data_level: Mapped[str]            # L1 | L2 | L3
    lifecycle: Mapped[str]             # active | summarized
    # 时间
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
```

> 前端类型同步到 `shared-schema/types.ts`（需 A 批准）。

---

## 16. API 草图

| 方法/路径 | 用途 |
|---|---|
| `POST /api/v1/memory` | 写入记忆（内部自动/手动） |
| `GET /api/v1/memory` | 查询（subject_type/subject_no/kind/related/时间/等级 多条件过滤） |
| `GET /api/v1/memory/{id}` | 单条记忆 |
| `POST /api/v1/memory/summarize` | 触发压缩（对话→摘要） |
| `GET /api/v1/memory/{subject_no}/profile` | 查/生成用户画像 |
| `POST /api/v1/memory/attachments` | 上传附件（存文件+提取摘要） |
| `GET /api/v1/memory/attachments/{id}` | 取回附件 |

---

## 17. 分步演进计划（每步有测试）

| 步骤 | 做什么 | 测试验证 |
|---|---|---|
| Step 0 ✅ | v1 PersonalMemory + 合并 master | 81 测试全绿 |
| Step 1 ✅ | 基础写/读/隔离 | 3 测试 |
| Step 2 ✅ | 升级 MemoryEntry：7 组维度字段 | 5 测试 |
| Step 3 ✅ | 读记忆按 visibility 鉴权（4 档） | 8 测试 |
| Step 4 | 记忆互通查询（多条件过滤 + 场景验证） | 跨 AI 历史、虚拟员工隔离 |
| Step 5 | 前端：详情页「记忆」标签页 | 页面渲染 |
| Step 6 | 压缩（对话→LLM 摘要，decision 永久） | 压缩后查摘要 |
| Step 7 | 附件记忆（上传→LLM 摘要→检索） | 附件摘要能查到 |
| Step 8 | 用户画像（LLM 生成 + 注入） | 画像生成 |
| Step 9 | Harness/RuntimeAdapter 桥接 | 模拟 Harness 调记忆 |

---

## 18. 待与老师确认清单（持续维护）

> 开发过程中冒出来的、需要老师拍板的点，都记在这里。等开发差不多了，一起找老师对齐。

### 已实现 PoC 版，待老师确认正式设计

1. **记忆表命名**：已把 `PersonalMemory` 升级为 `MemoryEntry`（7 维度），需老师确认这个演进 OK。
2. **管理员定义**：`memory_permission.py` 里 `ADMIN_HUMAN_NOS` 写死 `{"E10021"}`（王老师做演示），需确认正式的管理员/角色体系。
3. **visibility 四档规则**：`shared` 目前简化成"owner 可见"，需确认 shared 到底覆盖谁（同部门？团队？授权范围？）。
4. **"领导/管理员"如何表达**：目前用固定名单，需确认是用 employment_type / department / 固定名单 / 独立角色表？

### 待定的设计决策

5. **压缩定时**：30 天整、每日批处理？LLM 摘要的 prompt 谁来定？
6. **附件存储**：PoC 存本地目录，还是对象存储？附件大小上限？
7. **画像共享范围**：非敏感画像同步给哪些虚拟员工？（全部？同部门？）
8. **决策聚合**：读 AuditEvent 聚合，接口形态（单独查询还是并入 /memory）？
9. **会话标题总结**：✅ 已实现「LLM 自动总结 + 无密钥降级截断」（`chat.py:_summarize_title`）。有 DeepSeek 密钥时 AI 总结成标题，无密钥时用第一条消息截断。后续可优化 prompt 或换成其他模型。

---

## 19. 备注

- 只做概念建模 + 平台层接口 + 前端展示，不接真实敏感数据；
- 演示数据虚构，符合 `docs/SECURITY_BOUNDARY.md`；
- 记忆写默认最小权限，敏感读写需审批。
