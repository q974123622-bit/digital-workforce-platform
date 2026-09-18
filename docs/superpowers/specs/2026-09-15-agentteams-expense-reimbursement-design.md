# AgentTeams 财务报销协作场景设计

## 目标

在现有数字员工平台中增加“差旅报销协同审核”演示。用户可以从任务标题旁的预设菜单回填标题与任务说明，也可以继续自由编辑。真实 Matrix 运行时使用同一个根 Manager，按 `Manager → Leader → Worker` 完成财务核验、独立审计和模拟付款制单。

## 范围

- 保留 `TEAM-ONBOARD` 技术标识和现有入职、岗位调整场景。
- 前端增加任务预设菜单，不改变任务创建 API。
- 根 Manager 增加 `agentteams-expense-reimbursement` 场景 Skill。
- 新增财务 Leader `VE-FIN-LEAD`。
- 新增发票、预算和付款三个 Worker：`VE-FIN-INVOICE`、`VE-FIN-BUDGET`、`VE-FIN-PAYMENT`。
- 现有审计 Leader `VE-0005` 增加报销审计协调能力。
- 现有审计 Worker `VE-AUDIT-REVIEW` 增加报销合规 Skill 和 Tool。
- 所有外部动作使用本地确定性 Adapter，不连接真实财务或支付系统。

## 协作树

```text
Manager VE-0001
├─ 财务 Leader VE-FIN-LEAD
│  ├─ 发票核验 Worker VE-FIN-INVOICE
│  ├─ 预算核验 Worker VE-FIN-BUDGET
│  └─ 付款制单 Worker VE-FIN-PAYMENT
└─ 审计 Leader VE-0005
   └─ 报销合规 Worker VE-AUDIT-REVIEW
```

发票和预算核验可以并行。审计分支独立复核报销合规性。付款制单必须引用财务与审计已经返回的批准依据；产生的付款单是模拟记录。

## 固定演示数据

- 报销单：`EXP-2026-0915-001`
- 申请人：李明
- 事由：上海客户现场差旅
- 申请金额：`4860.00` 元
- 预算上限：`5000.00` 元
- 发票及行程证据完整，无重复报销。
- 审计批准编号：`AUD-EXP-001`
- 最终模拟付款单：`PAY-EXP-001`

固定数据保证演示可以稳定完成，但所有结果仍必须通过授权 Tool 获取并带回审计编号，不能只靠 Prompt 猜测。

## 专属 Skill

- `agentteams-expense-reimbursement`：Manager 识别报销场景并向财务、审计 Leader 委派。
- `finance-expense-coordination`：财务 Leader 拆分发票、预算和付款任务并验收。
- `expense-invoice-verification`：发票 Worker 核验发票、行程和金额。
- `expense-budget-verification`：预算 Worker 核验预算余额与标准。
- `expense-payment-preparation`：付款 Worker 在批准依据齐全后生成模拟付款单。
- `audit-expense-review`：审计 Worker 独立检查重复报销、合规条件并返回批准编号。

## Tool 契约

所有 Tool 使用相同的受控输入 `expense_id=EXP-2026-0915-001`。付款 Tool 额外要求 `approval_ref=AUD-EXP-001`。

- `agentteams-expense-claim`：读取报销单、发票和行程证据。
- `agentteams-expense-budget`：读取预算与费用标准。
- `agentteams-expense-audit`：返回独立合规复核结果。
- `agentteams-expense-payment`：生成模拟付款单。

每个 Tool 同时具有团队授权和对应数字员工个体授权，Gateway 继续执行统一权限检查。

## 前端交互

任务标题输入框右侧增加“选择预设”菜单：

- 新员工入职评估
- 财务报销协同审核

选择预设会同时覆盖标题和任务说明，之后仍允许手工编辑。自由输入流程保持不变。

## 运行与兼容

- Matrix 账号、角色启动列表、严格运行配置和健康检查扩展到新增四个财务角色。
- 所有角色保持 `thinking=false` 默认值和 `reasoning_effort=low`。
- 现有入职、岗位调整、数据库评测定义不删除。
- 现有任务 API、任务树和审计事件结构不改变。

## 验收

- 前端预设正确回填并允许继续编辑。
- 严格运行配置包含新增角色、关系、Skill 和 Tool，且不会泄露凭据。
- Manager 规划上下文只看到财务 Leader 和审计 Leader，不直接看到财务 Worker。
- 财务 Leader 只能向三个直属财务 Worker 派发。
- 审计 Leader 只能向其直属审计 Worker 派发。
- Tool 参数必须通过严格 Schema；付款 Tool 缺少审计批准编号时拒绝。
- 当前入职相关测试继续通过。
- 新财务场景通过本地契约测试和真实 Matrix smoke。
