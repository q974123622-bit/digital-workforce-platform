# AI员工平台项目指令

本文件适用于整个仓库。参与本项目的 AI 代理在规划、实现、评审或生成 OpenSpec 工件前，必须完整阅读并遵守以下项目基线：

1. `docs/project/CONSTITUTION.md`：不可违反的工程与治理底线；
2. `docs/project/mission.md`：产品使命、参与者关系与当前 MVP；
3. `docs/project/tech-stack.md`：技术现状、默认路线、扩展边界与环境约束；
4. `docs/project/roadmap.md`：当前建设阶段、Feature 边界、依赖与验收条件。

## 工作要求

- 开始工作前，指出对应的 Roadmap Feature；若不属于现有 Feature，先说明新需求及其与当前路线的关系。
- 按项目宪法的风险规则使用 OpenSpec。涉及公共 API、数据、权限、外部系统、跨模块行为或部署模型时，实施前必须建立或更新相应 change；低风险工作不得为了形式完整而制造规范膨胀。
- 遵守 Feature 的允许修改范围、不修改范围、前置依赖和验收条件。发现必须跨界时，先报告影响，不得顺手扩大范围。
- 代码、文档和测试必须区分 `CURRENT`、`VERIFIED`、`DEFAULT`、`EXPERIMENTAL`、`TARGET` 与 `TBD`，不得把构想、本地验证或 Mock 描述为测试服务器已可用能力。
- 真实依赖不可用时必须显式失败，禁止使用 Mock、演示数据或虚构答案冒充真实链路成功。
- 外部能力必须遵守 Identity、Policy、Gateway、Adapter 和 Audit 边界，不得由渠道、前端、LLM 或单个 Agent 建立旁路。
- 若项目基线与代码现状或新的人类需求冲突，应指出冲突并请求人类决策，不得由 AI 自行修改或重新解释基线。

项目基线的修订必须遵守 `CONSTITUTION.md` 的人类批准规则。具体 Change 的 Proposal、Design、Specs 与 Tasks 不得复制整套基线，只需引用并落实与本次变更有关的约束。
