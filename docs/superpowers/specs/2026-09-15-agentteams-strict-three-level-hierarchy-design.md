# AgentTeams 全局严格三层协作设计

## 目标

将所有 AgentTeams 团队统一为真实的 `Manager → Leader → Worker` 三层协作。Manager 只负责选择团队、向 Leader 下达部门任务、验收 Leader 汇总和决定后续轮次；Leader 负责向直属 Worker 拆分任务并验收结果；Worker 负责执行业务和调用 Tool。前端只显示数据库中真实存在的任务节点，不合成层级。

本设计覆盖当前 `TEAM-ONBOARD`、`TEAM-DB-BENCH` 以及以后新增的 AgentTeams 团队。历史任务不迁移、不改写；新规则只作用于新建任务。

## 核心不变量

每个可运行团队必须具有且仅具有以下拓扑：

```text
Manager
  └─ 一个或多个 Leader
       └─ 一个或多个 Worker
```

- Manager 必须至少有一个直属 Leader。
- 每个 Leader 必须至少有一个直属 Worker。
- Worker 不能有直属下属。
- Manager 只能委派给直属 Leader。
- Leader 只能委派给自己的直属 Worker。
- Worker 不能委派。
- 一个成员不能同时属于多个层级，不能形成环，`reports_to` 与 `direct_reports` 必须双向一致。
- 一批委派必须先完成整批验证，再以同一事务创建节点，不能留下半截任务树。
- Prompt 中的关系图只帮助模型决策；服务端关系图和身份校验才是权限边界。

不再支持“Manager 的直属成员没有下属时自动当作 Worker”的扁平兼容。旧任务仍可读取和展示，但不能以旧结构创建新任务。

## 统一团队拓扑契约

数据库是团队拓扑的事实来源。保留 `Team.leader_employee_id` 作为 Manager ID，并为 `TeamMember` 增加 `reports_to_employee_id`；`TeamMember.role` 在新任务中严格使用 `manager | leader | worker`。新增独立的团队拓扑验证与投影模块，接收这些团队成员和 Manager ID，生成不可变的标准视图：

```python
TeamHierarchy(
    manager_id: str,
    leaders: dict[str, tuple[str, ...]],
    parent_by_agent: dict[str, str],
    level_by_agent: dict[str, Literal["manager", "leader", "worker"]],
)
```

该模块负责：

- 校验成员全集、层级、无环、双向关系和非空直属关系。
- 判断某次 `delegator → assignee` 是否符合层级。
- 为 Manager 或 Leader 生成只包含自身、上级和直属下属的最小关系图。
- 为健康检查返回不含凭据的错误原因。

运行配置加载、协作服务、Agent Runtime、数据库 Benchmark Runner 和测试 fixture 都调用这一个契约，不能分别实现各自的层级判断。

Matrix `team_relationships` 必须与数据库拓扑规范化后完全一致；Matrix 配置不是团队授权或上下级关系的事实来源。

## 派发与验收流程

### Manager

1. 接收用户目标并选择 Team。
2. 只从团队直属 Leader 中选择接收人。
3. 创建 `team_assignment` 节点。
4. 等待 Leader 的已验收汇总。
5. 根据已有汇总决定继续向 Leader 发起下一轮，或生成最终汇总。

Manager 不直接为 Worker 选择 Tool，也不能把任务派给 Worker。

### Leader

1. 收到本部门 `team_assignment`。
2. 获得自身身份、上级、直属 Worker、各 Worker 能力和已经完成的依赖结果。
3. 使用大模型将部门任务拆分为一个或多个 `worker_task`。
4. 只能选择直属 Worker，且请求能力必须属于该 Worker。
5. 验收 Worker 提交；不合格时走现有 revision 机制。
6. 所有必要结果验收完成后形成部门汇总，提交给 Manager 验收。

### Worker

1. 仅接收 `worker_task`。
2. 在分配能力范围和三层插件权限范围内选择并调用 Tool。
3. 提交证据和业务结果。
4. 不允许继续委派。

本次不改变已有 Tool Gateway、三层插件权限解析和 Worker Tool 调用方式；只收紧任务委派边界。

## 场景与编排规则分离

场景 Skill 只声明：

- 需要哪些部门能力；
- 哪些结果存在先后依赖；
- 完成条件和证据要求。

场景 Skill 不再直接指定叶子 Worker，也不再允许 Manager 绕过 Leader。通用协作引擎根据团队拓扑把部门能力交给 Leader，Leader 再结合自己的直属 Worker 能力进行拆分。

## TEAM-ONBOARD 迁移

团队固定为：

```text
VE-0001 Manager
├─ VE-0002 HR Leader
│  ├─ VE-HR-MATERIAL HR材料专员
│  └─ VE-HR-TRAINING HR培训/生效专员
├─ VE-0003 IT Leader
│  ├─ VE-IT-ACCOUNT IT账号专员
│  └─ VE-IT-DEVICE IT设备/权限专员
└─ VE-0005 审计 Leader
   └─ VE-AUDIT-REVIEW 审计专员
```

“新员工入职”和“岗位调整”共享该团队拓扑，但使用不同 Skill 和 Tool：

- 新员工入职：HR 两名 Worker 分别检查材料、培训；IT 两名 Worker 分别检查账号、设备；审计 Worker 复核证据。
- 岗位调整：HR 两名 Worker 分别检查审批材料、目标岗位与生效日期；IT 两名 Worker 分别检查账号/撤销权限、规划新增权限并在审计通过后执行激活；审计 Worker 复核最小权限和职责分离。
- Manager 首先向 HR/IT Leader 派发；需要审计时向审计 Leader 派发，并保留 IT 激活依赖审计批准的业务约束。

新增 `VE-AUDIT-REVIEW` 真实数字员工、Matrix 身份、团队成员、关系和授权。Leader 本身只获得协调 Skill，不保留可由 Worker 执行的业务 Tool；业务 Tool 转移或补充到对应 Worker。

## TEAM-DB-BENCH 迁移

数据库团队固定为：

```text
VE-0001 Manager
├─ DB-OPS-LEAD 运行诊断 Leader
│  ├─ DB-WRITE-01 写入专家
│  ├─ DB-LOCK-01 锁专家
│  └─ DB-VACUUM-01 Vacuum 专家
└─ DB-PERF-LEAD 性能诊断 Leader
   ├─ DB-INDEX-01 索引专家
   └─ DB-QUERY-01 查询专家
```

Manager 的计划先生成两个部门级 assignment；Leader 再把具体 signal 检查分派给直属数据库专家。已有检查预算仍按 Worker 的实际数据库 Tool 调用计数，Leader 的规划和汇总不消耗检查预算，但计入模型迭代和关键路径。完整、串行和无协调配置仍保留；“无协调”只关闭跨 Leader 协调与复核，不允许破坏三层身份边界。

数据库检查 Tool 仅授予五个 Worker。两个 Leader 只具有数据库诊断协调 Skill，不直接调用数据库检查 Tool。

## 运行配置与就绪状态

- 所有严格运行配置都必须提供完整 `team_relationships`，不再允许数据库团队省略。
- 每个运行角色都具有唯一 Matrix 账号、Agent ID 和允许的 Skill/Tool 目录。
- 启动前校验完整拓扑；任一 Leader 无 Worker、Worker 有下属、成员缺失或关系不一致时，环境显示未就绪并返回具体、脱敏的错误码。
- 启动脚本按所选团队启动全部相关身份；健康检查分别报告 Matrix、房间、Manager、Leader、Worker 和拓扑状态。
- 不在日志、API 或测试快照中输出 Token、API Key。

## 数据与兼容性

- 更新 seed 中的数字员工、团队成员（含 `role` 和 `reports_to_employee_id`）、个人插件授权和团队插件授权。
- SQLite 兼容迁移只增加可空的 `team_member.reports_to_employee_id` 列；旧团队在同步前不能创建新 AgentTeams 任务，但历史任务读取不受影响。
- 同步操作保持幂等，只更新本功能管理的 seed 资源，不全量重置数据库。
- 已有用户自定义授权和历史任务不删除。
- 已创建的扁平任务按原 `node_kind` 展示和完成；不重新挂接父节点。
- 新任务创建时，如果团队仍是旧扁平结构，拒绝启动并报告 `invalid_team_hierarchy`。

## 错误与安全边界

错误至少区分：

- `invalid_team_hierarchy`：团队拓扑整体不合法。
- `manager_must_delegate_to_leader`：Manager 目标不是直属 Leader。
- `leader_must_delegate_to_worker`：Leader 目标不是直属 Worker。
- `worker_cannot_delegate`：Worker 尝试派发。
- `assignment_capability_mismatch`：接收人不具备请求能力。

模型输出在 Manager/Leader 决策层先验证整批计划；协作服务收到协议消息后使用相同规则再次防御性验证。任何入口失败都不创建部分节点。

## 前端

前端不增加层级选择，也不制造虚拟卡片：

- `mission` 显示为 Manager 任务。
- `team_assignment` 显示为 Leader 任务。
- `worker_task` 显示为 Worker 任务。
- 连接线完全根据 `parent_task_id` 生成。
- 大树允许横向滚动或自适应宽度，卡片正文不被裁切。
- 历史扁平任务继续可读，并标记为历史结构，不误称为三层协作。

## 验收标准

1. 任意新 AgentTeams 任务的非根路径都严格为 `mission → team_assignment → worker_task`，后续轮次仍遵守相同关系。
2. Manager 直接派给 Worker、Leader 跨部门派发、Worker 再派发均被拒绝，且整批无节点落库。
3. 新员工入职和岗位调整都产生 HR、IT、审计 Leader 及其真实 Worker 节点；岗位调整保留审计批准后才能激活的依赖。
4. 数据库团队产生两个 Leader 和五个专家的真实节点，检查预算只统计 Worker Tool 调用。
5. Worker 结果须经 Leader 验收；Leader 汇总须经 Manager 验收；驳回修订链继续有效。
6. 严格 Matrix 健康检查显示完整团队和合法拓扑，所有运行身份就绪。
7. 前端完整显示真实三层树，无评测面板，无卡片裁切。
8. 后端目标测试、全部 AgentTeams 回归、前端测试和生产构建通过。

## 非目标

- 不重写 Matrix Transport、Outbox、任务 DAG、Gateway 或插件权限系统。
- 不恢复已经移除的前端评测面板。
- 不修改或重放历史任务。
- 不让 Leader 替 Worker 调用业务 Tool。
- 不在本次引入可视化团队编辑器或前端层级选择器。
