# 记忆插件（Memory Plugin）设计文档

> 版本：v0.1（草案，供讨论）
> 作者：C（前端负责人）
> 日期：2026-08-18
> 状态：待与 A 老师对齐后定稿

---

## 1. 背景

数字员工平台把"能力"统一抽象为**插件**，由「身份 → Policy Engine → Plugin Gateway → Adapter → 资源」这一条治理链统一管控。

老师把插件分为三类，目前进度如下：

| 插件类别 | 本质 | 机制 | 现状 |
|---|---|---|---|
| 技能插件（skill） | 流程/方法 | SKILL.md + 工具注册，运行时热插拔 | 🟡 进行中（`feature/common-skills-mock` 分支） |
| 知识库插件（knowledge） | 事实/资料 | 统一 ctx.kb 接口，多源异构融合 | 🟡 进行中（`codex/sprint5-mock-kb` 分支） |
| 记忆插件（memory） | 状态/历史 | **待设计** | 🔴 未启动（本档重点） |

本文档聚焦**记忆插件**的设计：它是什么、与另外两类的区别、数据模型、前端方案，以及需要老师拍板的开放问题。

---

## 2. 现状盘点（基于 master 当前代码）

- 当前 `Plugin.type` 已有 5 种：`knowledge / mcp / workflow / rpa / http`（见 `mock-data/seed.json`）。
- 授权模型 `employee_plugin_grant`：`employee_id + plugin_id + action + decision_mode`，动作目前是 `read / execute / search` 等，决策是 `allow / deny / approval`。
- 已存在但未被当作"记忆"使用的数据：
  - **短期会话**：`ChatSession` / `ChatMessage`（有）
  - **审批/审计**：`AuditEvent`（有，含 decision / reason / trace_id）
- 尚未建模的：长期事实、客户交互历史、跨员工协作记忆。

---

## 3. 核心认知：记忆与技能、知识库的本质区别

| 维度 | 技能（skill） | 知识库（knowledge） | 记忆（memory） |
|---|---|---|---|
| 本质 | 流程/方法 | 事实/资料 | 状态/历史 |
| 读写 | 只读 | 只读为主 | **读写，随时间累积** |
| 生命周期 | 静态 | 静态 | **动态增长** |
| 类比 | 菜谱（怎么做） | 词典（是什么） | 脑子里的记忆（经历过什么） |

**关键结论**：记忆的难点不在"存"，而在"**按身份安全地读写**"。记忆涉及隐私（客户历史）与权限（审批决策），必须走「身份 → Policy → Gateway」治理链，读和写都要鉴权。

---

## 4. 五种记忆类型

| 记忆类型 | 记什么 | 作用域 | 现状 |
|---|---|---|---|
| ① 短期会话记忆 short_term | 当前对话上下文 | 单次会话 | ✅ 已有（ChatSession/ChatMessage） |
| ② 长期事实记忆 long_term_fact | 用户偏好、稳定事实 | 单个员工 | ❌ 无 |
| ③ 客户交互历史 customer_history | 与某客户的历史往来 | 按客户 | ❌ 无 |
| ④ 审批决策记忆 approval_decision | 过去的允许/拒绝/审批 | 员工/全局 | ⚠️ AuditEvent 已存，未当记忆检索 |
| ⑤ 跨员工协作记忆 collaboration | 员工间共享上下文 | 团队/跨员工 | ❌ 无 |

---

## 5. 数据模型草案（前端类型，待与后端对齐）

> 注意：`shared-schema/types.ts` 属于"需 A 批准"的冻结文件，以下类型只是草案，最终以 A 定稿为准。

```typescript
// 记忆子类型
type MemoryKind =
  | 'short_term'        // 短期会话
  | 'long_term_fact'    // 长期事实
  | 'customer_history'  // 客户交互历史
  | 'approval_decision' // 审批决策
  | 'collaboration';    // 跨员工协作

// 记忆插件：在 Plugin 基础上，type = 'memory'，并声明支持哪些子类型
interface MemoryPlugin extends Plugin {
  type: 'memory';
  memory_kinds: MemoryKind[];
}

// 一条记忆记录
interface MemoryEntry {
  id: string;
  kind: MemoryKind;
  scope: 'employee' | 'customer' | 'team' | 'global'; // 作用域
  subject_id: string;   // 关联对象：员工号 / 客户号 / 团队号
  content: string;      // 内容
  created_at: string;
  updated_at: string;
}

// 记忆的读写动作（与普通插件的 read/execute 不同，记忆有"写"）
type MemoryAction = 'read' | 'write' | 'delete';
```

**设计要点**：

1. 记忆动作需要**读/写分离**：知识库只读，但记忆要写入，所以授权模型要多一个 `write` 动作（并默认走审批）。
2. 记忆按 `scope + subject_id` 隔离：客户历史只能按客户查，员工长期事实只能按员工查，防止串读。
3. 记忆的内容要分级（data_level），与现有 `L1/L2/L3` 对齐——客户历史、审批决策大概率属于敏感级。

---

## 6. 治理链（与现有体系保持一致）

记忆的读写也必须走统一治理链，不能绕过：

```text
Employee Identity
  → Policy Engine（读/写分别鉴权，默认拒绝）
  → Plugin Gateway（唯一执行入口）
  → Memory Adapter（读写记忆存储）
  → Memory Store（数据落盘/检索）
```

- 读记忆：`action=read`，按 scope + data_level 鉴权；
- 写记忆：`action=write`，默认 `approval`（敏感记忆写入需审批）；
- 每次读写落一条 `AuditEvent`（trace_id + employee_id + decision + reason）。

---

## 7. 前端页面方案（C 的交付范围）

### 7.1 插件中心（`Plugins.tsx`）
- 在 `components/tags.tsx` 的 `PLUGIN_TYPE_META` 中新增 `memory` 类型（图标 + 颜色 + 中文名"记忆"）；
- 记忆插件的卡片上额外展示它支持的**子类型标签**（短期/长期/客户/审批/协作）。

### 7.2 员工详情页（`EmployeeDetail.tsx`）
- 在现有 Tabs 中新增一个「**记忆**」标签页，展示该员工的相关记忆：
  - 长期事实（偏好等）；
  - 审批决策历史（从 AuditEvent 聚合）；
  - 客户交互历史（按客户分组）；
  - （短期会话已在聊天页体现，可只放摘要）。

### 7.3 插件授权（对应 T1-08 的"插件配置"难点）
- 授权表单支持记忆的**读/写分离**：`read` 可勾选 allow/deny，`write` 默认 approval；
- 安全配置页补充记忆相关的数据范围（哪些 scope 可读写）。

---

## 8. 本周 PoC 范围建议

短期会话、审批决策已有数据底座，建议本周**新增演示**优先做价值最直观的两类：

1. **长期事实记忆**：虚拟员工"记住"用户偏好（如"王老师偏好周五开会"），下次回答时主动使用；
2. **客户交互历史**：演示"查询某客户的历史往来"，并体现权限隔离（实习生查客户历史 → Deny）。

其余（跨员工协作记忆等）列为 P1，后续 Sprint 再做。

---

## 9. 待与 A 老师对齐的问题清单

1. **建模方式**：记忆是往 `Plugin.type` 里加一个 `memory`（与 knowledge 平级），还是像 skill 一样独立建模（单独的表/概念）？
2. **存储归属**：记忆的"存/取"后端（数据库 + 检索）由谁负责——B 老师做，还是 A 老师做，还是先由前端用 Mock 顶？
3. **本周范围**：PoC 先做哪几种子记忆？是否按第 8 节建议（长期事实 + 客户历史）？
4. **读写权限**：记忆的 `write` 动作是否默认走 approval？`delete` 是否本周需要？
5. **检索方式**：记忆按 `scope + subject_id` 精确查（简单、够 Demo），还是需要语义检索（复杂、后续）？
6. **冻结文件**：本方案涉及修改 `shared-schema/types.ts`（需 A 批准），是否等 A 定稿数据模型后再动？
7. **与 skill 的关系**：common-skills 分支里有 `work-summary`（工作总结）技能，它需要读"工作记录"，这和记忆插件的"审批决策/协作记忆"是否重叠？如何避免重复建模？

---

## 10. 备注

- 本方案只涉及前端展示/配置 + 概念建模，不接真实记忆存储；真实数据/权限由正式员工（A/B）在受控环境实现。
- 所有演示数据必须虚构，符合 `docs/SECURITY_BOUNDARY.md` 的边界要求。
