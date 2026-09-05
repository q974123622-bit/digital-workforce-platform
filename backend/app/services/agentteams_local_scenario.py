"""Canonical inputs for the deterministic local AgentTeams onboarding demo."""

LOCAL_DEMO_TEAM_ID = "TEAM-ONBOARD"
LOCAL_DEMO_LEADER_ID = "VE-0001"
LOCAL_DEMO_WORKER_IDS = frozenset({"VE-0002", "VE-0003"})
LOCAL_DEMO_TITLE = "新员工入职准备复核"
LOCAL_DEMO_INSTRUCTION = (
    "请协作完成新员工入职准备复核：HR核查制度和材料，IT核查账号与服务状态，"
    "至少一次跨Agent澄清或继续委派，最后汇总完成项、风险和后续动作。"
)
LOCAL_DEMO_MATERIAL: dict[str, object] = {}
