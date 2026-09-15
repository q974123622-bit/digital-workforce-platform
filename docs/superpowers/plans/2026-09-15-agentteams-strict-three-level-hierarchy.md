# AgentTeams 严格三层协作实施计划

1. 为团队成员增加上级关系和结构能力字段，并提供旧 SQLite 兼容迁移。
2. 建立统一层级解析器，校验单一 Manager、Leader/Worker 关系、环路和派发边界。
3. 改造协作服务，使根任务、Leader 任务和 Worker 任务分别持久化为 `mission`、`team_assignment`、`worker_task`。
4. 把关系图和当前能力目录传给 Manager、Leader 运行时；整批计划校验通过后才写入任务图。
5. 扩展 TEAM-ONBOARD 与岗位调整的 Matrix 身份、启动脚本和运行配置；TEAM-DB-BENCH 保持独立进程内 runner，但使用同一套三层团队契约。
6. 让本地 Demo 和数据库基准执行真实 Leader 二次派发、Worker 结果验收和 Leader 汇总。
7. 更新前端递归任务树标签和人员名称，移除协作页评测区域。
8. 将默认 Manager 追加轮次提高到五次，并保留其他资源上限。
9. 运行核心 E2E、全后端、前端测试和生产构建；修正所有仍假设旧扁平团队的契约。
