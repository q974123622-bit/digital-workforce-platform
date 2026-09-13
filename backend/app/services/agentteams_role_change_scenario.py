"""Shared contract for the fixed, fictional AgentTeams role-change demo."""

ROLE_CHANGE_TEAM_ID = "TEAM-ONBOARD"
ROLE_CHANGE_LEADER_ID = "VE-0001"
ROLE_CHANGE_WORKER_IDS = frozenset({"VE-0002", "VE-0003", "VE-0005"})
ROLE_CHANGE_TITLE = "岗位调整评估"
ROLE_CHANGE_INSTRUCTION = "评估张明的岗位调整是否可以按期完成。"
ROLE_CHANGE_MATERIAL: dict[str, object] = {}
ROLE_CHANGE_SKILL_ID = "agentteams-role-change-readiness"
ROLE_CHANGE_EMPLOYEE_REF = "张明"
ROLE_CHANGE_APPROVAL_REF = "AUD-RC-001"

ROLE_CHANGE_FIRST_ROUND_CAPABILITIES = frozenset({
    "hr.role_change.read",
    "it.permission.plan",
})
ROLE_CHANGE_SECOND_ROUND_CAPABILITIES = frozenset({
    "audit.permission.review",
    "it.permission.activate",
})
