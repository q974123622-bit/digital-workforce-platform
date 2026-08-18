# 通用 Skill 约定

- 项目级 Skill 位置：`.agents/skills/`
- Skill 格式复用 DeepSeek Harness 的 `SKILL.md`（YAML frontmatter + Markdown 正文）
- Skill 不负责员工权限，不固化正式/实习、L1/L2、部门、策略编号等差异
- Skill 通过当前 Runtime 实际可用的 Tool 工作，只负责「怎么查询、怎么处理结果」
- 身份 → Policy Engine → Plugin Gateway 仍是唯一治理链

## 当前通用 Skill

| Skill | 主要用途 | 依赖的 Runtime 能力 | 当前状态 |
|---|---|---|---|
| enterprise-knowledge | 企业知识查询 | knowledge search Tool | Skill 完成，Runtime 桥接待后续 |
| employee-collaboration | 员工协作 | employee search/chat/delegate Tool | Skill+Mock 完成，真实跨员工执行待后续 |
| document-analysis | 文档分析 | document/file Tool | Skill+Mock 完成，真实文件 Tool 待后续 |
| work-summary | 工作总结 | work record/session/task Tool（可选） | Skill+Mock 完成 |

Skill 资产完成 ≠ 真实 Runtime 能力已经接通。

详细的 Skill → Tool / Plugin 能力 → Mock Fixture 映射关系见：[docs/SKILL_TOOL_CONTRACTS.md](SKILL_TOOL_CONTRACTS.md)
