# 记忆插件（Memory Plugin）设计文档 v0.2

> 版本：v0.2（全面版）
> 作者：C（前端负责人）
> 日期：2026-08-18
> 状态：待与 A/B 老师对齐后定稿
> 变更：v0.1 → v0.2 补全「4 维度模型、三大原材料、权限分级、虚拟员工全流程、DSH 兼容、分步计划」

---

## 1. 一句话定位

**记忆插件 = 让数字员工平台"记住发生过的事"，并且每条记忆都有"是谁的、是什么、谁能看、多敏感"四个属性，读写都走统一治理链。**

---

## 2. 现状盘点（基于当前代码）

项目里其实已经有"记忆的原材料"，只是散落在三个地方、没被统一当"记忆"管理：

| 原材料 | 存储表 | 记了什么 | 现状 |
|---|---|---|---|
| 对话记录 | `ChatMessage` | 每次聊了啥（用户/助手/工具） | ✅ 有，未按"谁"聚合 |
| 操作 + 决策 + 结果 | `AuditEvent` | 谁、何时、调了啥、允许/拒绝/审批、原因、结果摘要 | ✅ 有（就是"操作记忆+决策记忆"） |
| 个人记忆 | `PersonalMemory`（本次新建） | 提炼后的长期记忆 | ✅ 刚建，最简陋 |

**结论**：操作记忆、决策记忆、决策结果记忆，`AuditEvent` 已经全记着了（`decision` 字段 + `result_summary` 字段），我们**不必重造**，只需把它们当"记忆"统一查询 + 加权限。真正要新做的是"长期记忆"的建模与分级。

---

## 3. 核心模型：一条记忆的 4 个维度

任何一条记忆，都同时打上 4 个标签（回答 4 个问题）：

| 维度 | 字段 | 取值 | 回答 |
|---|---|---|---|
| **主体** | `subject_type` + `subject_no` | `human`(真人) / `virtual`(虚拟员工) / `team`(团队) | 这是**谁**的记忆 |
| **类型** | `kind` | `basic_info` / `conversation` / `operation` / `decision` / `fact` / `customer_history` | 记的是**什么** |
| **可见性** | `visibility` | `public`(公开) / `personal`(本人可查) / `confidential`(涉密，仅管理员) | **谁能看** |
| **数据等级** | `data_level` | `L1` / `L2` / `L3` | 内容**多敏感** |

> **关键设计**：`visibility`（谁能看）和 `data_level`（多敏感）是两个**独立**维度，要分开。比如"客户历史"既是 `confidential`（仅管理员）又是 `L3`（敏感）。

---

## 4. 记忆分级（对应"用户记忆分级"需求）

| 记忆内容 | kind | visibility | data_level | 谁能看 |
|---|---|---|---|---|
| 基本信息（姓名、部门） | basic_info | public | L1 | 所有人 |
| 对话记录 | conversation | personal | L2 | 本人 |
| 操作记录（AI 帮做了什么） | operation | personal | L2 | 本人 + Owner |
| 决策及结果 | decision | confidential | L3 | 管理员 |
| 事实/偏好（长期提炼） | fact | personal | L2 | 本人 |
| 客户交互历史 | customer_history | confidential | L3 | 管理员 |

**"干活时调用更高级记忆辅助"**：虚拟员工帮用户干活时，按当前任务授权，可临时调取该用户的 `fact`/`decision` 记忆辅助，但 `confidential` 级仍需 Policy 放行。

---

## 5. 虚拟员工的全流程记忆

虚拟员工的记忆，主体是 `virtual`，覆盖它的一生：

| 虚拟员工要记的 | kind | 举例 |
|---|---|---|
| 和谁对过话 | conversation | VE-0001 和王老师聊过入职 |
| 帮谁做过什么 | operation | VE-0001 帮王小明查了员工信息 |
| 决策及结果 | decision | VE-0001 调 RPA 被要求审批 → 批准 → 结果 |
| 这些记忆分权限 | visibility | 公开 / 本人 / 涉密 |

**好消息**：上述"和谁对话、帮谁做什么、决策结果"`AuditEvent` 已经记录（`trace_id` + `employee_id` + `action` + `decision` + `result_summary`）。要做的是把它们**按 `employee_id` 聚合、加 visibility 分级**，而不是重造。

---

## 6. DeepSeek Harness 兼容性设计

**结论：可兼容，因为记忆插件是平台层的 HTTP 接口，不绑定任何对话引擎。**

- 现在的记忆接口是 `POST/GET /memory`（平台 FastAPI 后端），运行时无关；
- 未来用户通过 DeepSeek Harness 对话时，Harness 内部会话记忆（存在 `.dsh/sessions`）负责"短期、单次对话"；
- 平台的记忆（`PersonalMemory`/`MemoryEntry`）负责"长期、跨对话、带权限"；
- 桥接方式：给 Harness 加一个"记忆工具"（类似项目里已有的 `search_knowledge` 知识库工具），背后调用平台 `/memory` 接口；
- 这正符合技术路线里的"Runtime Adapter"思路：Harness 通过 Adapter 调用平台能力，平台记忆独立于任何 Runtime。

**兼容性保障**：记忆读写统一走「身份 → Policy → Gateway → Memory Adapter → Memory Store」，无论前端、FastAPI 后端、还是 Harness/OpenClaw 调用，都走同一条链，天然一致。

---

## 7. 目标数据模型

### 7.1 后端表（目标，由当前 `PersonalMemory` 演进而来）

```python
class MemoryEntry(Base):
    __tablename__ = "memory_entry"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 主体：这是谁的记忆
    subject_type: Mapped[str]          # human | virtual | team
    subject_no: Mapped[str]            # E10281 / VE-0001 / TEAM-ONBOARD
    # 类型
    kind: Mapped[str]                  # basic_info | conversation | operation | decision | fact | customer_history
    # 可见性 + 数据等级
    visibility: Mapped[str]            # public | personal | confidential
    data_level: Mapped[str]            # L1 | L2 | L3
    # 关联与溯源
    related_subject_no: Mapped[str | None]  # 和谁对话/操作产生的（可选）
    trace_id: Mapped[str | None]       # 关联的审计/操作
    # 内容
    content: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
```

### 7.2 前端类型（草案，需 A 批准后进 shared-schema）

```typescript
type MemorySubjectType = 'human' | 'virtual' | 'team';
type MemoryKind = 'basic_info' | 'conversation' | 'operation' | 'decision' | 'fact' | 'customer_history';
type MemoryVisibility = 'public' | 'personal' | 'confidential';

interface MemoryEntry {
  id: number;
  subject_type: MemorySubjectType;
  subject_no: string;
  kind: MemoryKind;
  visibility: MemoryVisibility;
  data_level: 'L1' | 'L2' | 'L3';
  related_subject_no: string | null;
  trace_id: string | null;
  content: string;
  created_at: string;
  updated_at: string;
}
```

---

## 8. 权限模型

记忆读写走统一治理链：`Identity → Policy → Gateway → Memory Adapter → Memory Store`。

| 操作 | 规则 |
|---|---|
| 读 `public` | 任何人允许 |
| 读 `personal` | 仅本人（subject 本人）或 Owner |
| 读 `confidential` | 仅管理员（Policy 放行） |
| 写 `fact`（偏好） | 本人写入，默认 allow |
| 写 `operation`/`decision` | 系统自动写（来自 AuditEvent），不开放人工写 |
| 每次读写 | 落一条 `AuditEvent`（trace_id + decision + reason） |

---

## 9. 分步演进计划（每步都有测试，能快）

| 步骤 | 做什么 | 测试验证 |
|---|---|---|
| **Step 1** ✅ | PersonalMemory 表 + 写/读接口 | 已完成：seed / 写读 / 隔离（3 测试） |
| **Step 2** | 升级为 MemoryEntry：加 `subject_type/subject_no/kind/visibility/data_level/trace_id` 字段 | 写不同主体/类型/可见性的记忆 |
| **Step 3** | 读记忆按 `visibility` 鉴权（public/personal/confidential） | 不同身份读，验证隔离 |
| **Step 4** | 统一记忆查询：聚合 conversation(ChatMessage) + operation/decision(AuditEvent) + fact(MemoryEntry) | 查 VE-0001 完整记忆链 |
| **Step 5** | 前端：员工/虚拟员工详情页加「记忆」标签页 | 页面渲染 |
| **Step 6** | 封装 Runtime Adapter（供 Harness 调用记忆） | 模拟 Harness 调用记忆 |

**建议节奏**：Step 2、3 是地基（数据模型 + 权限），优先做扎实；Step 4 开始有"看得见"的价值；Step 5 回到前端主场；Step 6 与 A/B 的 Harness 集成对齐后做。

---

## 10. 待与老师对齐的问题

1. **表命名与演进**：是否把 `PersonalMemory` 正式升级为 `MemoryEntry`？还是保留原名只加字段？
2. **操作/决策记忆**：`AuditEvent` 已记录操作+决策，是"统一查询时聚合读取"，还是要"复制一份到 MemoryEntry"？（建议聚合读取，避免双写）
3. **visibility 鉴权**：`confidential`（涉密）到底谁能看？"管理员"在 PoC 里如何表达（一个固定 admin 身份？）？
4. **数据分级**：`data_level` 与现有 L1/L2/L3 对齐，记忆是否需要独立的 L3 敏感记忆演示场景？
5. **检索方式**：Step 4 的"统一记忆查询"先做精确查询（按 subject + kind + 时间），语义检索后续再说？
6. **Harness 集成**：Step 6 的 Runtime Adapter 由谁做（C 做接口封装，A/B 做 Harness 侧工具）？

---

## 11. 备注

- 本方案只涉及概念建模 + 平台层接口 + 前端展示，不接真实敏感数据；
- 所有演示数据虚构，符合 `docs/SECURITY_BOUNDARY.md` 边界；
- 记忆写操作默认最小权限，敏感记忆读写需审批。
