"""DeepSeek-backed decisions for the database benchmark without answer leakage."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from . import config
from .agentteams_database_benchmark_runner import (
    ExpertReport,
    LeaderReport,
    ManagerPlan,
    ManagerReview,
    SingleAgentDecision,
    manager_team_catalog,
)
from .agentteams_database_evaluation import DiagnosisResult
from .agentteams_database_scenarios import (
    DatabaseScenario,
    DatabaseScenarioCatalog,
    database_inspection_contract,
)
from .agentteams_skills import ProjectSkillRegistry
from .llm import DeepSeekProvider, LLMProvider


_KEY_ENVIRONMENTS = {
    "manager": "DEEPSEEK_API_KEY_MANAGER",
    "db_write": "DEEPSEEK_API_KEY_DB_WRITE",
    "db_lock": "DEEPSEEK_API_KEY_DB_LOCK",
    "db_vacuum": "DEEPSEEK_API_KEY_DB_VACUUM",
    "db_index": "DEEPSEEK_API_KEY_DB_INDEX",
    "db_query": "DEEPSEEK_API_KEY_DB_QUERY",
}
_ROLE_SKILLS = {
    "db_write": "db-write-investigation",
    "db_lock": "db-lock-investigation",
    "db_vacuum": "db-vacuum-investigation",
    "db_index": "db-index-investigation",
    "db_query": "db-query-investigation",
}
DATABASE_BENCHMARK_REQUEST_TIMEOUT_SECONDS = 90.0
DATABASE_BENCHMARK_PROMPT_CONTRACT = "database-diagnosis-taxonomy-v1"


class DeepSeekDatabaseBenchmarkBackend:
    def __init__(self, *, providers: Mapping[str, LLMProvider], project_root: Path) -> None:
        required = {"single_agent", "manager", *_ROLE_SKILLS}
        if set(providers) != required:
            raise ValueError("invalid_database_benchmark_providers")
        self.providers = dict(providers)
        self.skills = ProjectSkillRegistry.from_root(project_root)
        self.root_cause_taxonomy = DatabaseScenarioCatalog.from_root(
            project_root,
        ).root_cause_taxonomy()
        self.model_name = str(getattr(providers["manager"], "model", "configured-model"))
        self.model_settings = {
            "thinking": False,
            "reasoning_effort": "low",
            "structured_output": "json_object",
            "request_timeout_seconds": DATABASE_BENCHMARK_REQUEST_TIMEOUT_SECONDS,
            "prompt_contract": DATABASE_BENCHMARK_PROMPT_CONTRACT,
        }

    def _scenario_payload(
        self,
        scenario: DatabaseScenario,
        *,
        include_taxonomy: bool = False,
    ) -> dict[str, Any]:
        payload = scenario.model_dump(
            mode="json",
            exclude={"expected", "directory"},
        )
        if scenario.complexity == "legacy":
            payload.pop("complexity", None)
            payload.pop("tool_budget", None)
        if include_taxonomy:
            payload["candidate_root_causes"] = list(self.root_cause_taxonomy)
        return payload

    def _diagnosis_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        diagnosis = schema.get("$defs", {}).get("DiagnosisResult", schema)
        properties = diagnosis["properties"]
        properties["root_cause_id"]["enum"] = list(self.root_cause_taxonomy)
        properties["top_candidates"]["items"]["enum"] = list(self.root_cause_taxonomy)
        return schema

    @staticmethod
    def _messages(instruction: str, payload: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": instruction, "source": "demo"},
            {
                "role": "user",
                "content": json.dumps(
                    {**payload, "output_schema": schema},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "source": "demo",
            },
        ]

    def _call(self, role: str, instruction: str, payload: dict[str, Any], schema: dict[str, Any]) -> dict:
        response = self.providers[role].structured_output_with_trace(
            self._messages(instruction, payload, schema),
            schema,
        )
        return response.data

    def single_decide(self, *, scenario: DatabaseScenario, observations: list[dict], allowed_signals: tuple[str, ...]) -> dict:
        skill = self.skills.load(["agentteams-database-diagnosis"])[0]
        return self._call(
            "single_agent",
            "你是 Single Agent 数据库诊断基线。不得委派或询问其他 Agent。"
            "只能返回一次 Tool 决策或最终诊断；严格遵守以下项目 Skill。\n" + skill.content,
            {
                "scenario": self._scenario_payload(scenario, include_taxonomy=True),
                "allowed_signals": list(allowed_signals),
                "inspection_contract": database_inspection_contract([scenario.id]),
                "observations": observations,
            },
            self._diagnosis_schema(SingleAgentDecision.model_json_schema()),
        )

    def expert_report(self, *, role: str, scenario: DatabaseScenario, observation: dict) -> dict:
        skill_id = _ROLE_SKILLS.get(role)
        if skill_id is None:
            raise ValueError("invalid_database_expert_role")
        skill = self.skills.load([skill_id])[0]
        schema = ExpertReport.model_json_schema()
        schema["properties"]["role"] = {"type": "string", "const": role}
        return self._call(
            role,
            "你是数据库 AgentTeams 中的专业 Worker。只根据给定 Tool 观测形成报告，"
            f"不猜测未观察事实，不读取隐藏答案。结构化字段 role 必须精确填写为 {role}。\n"
            + skill.content,
            {
                "scenario": self._scenario_payload(scenario),
                "required_role": role,
                "observation": observation,
            },
            schema,
        )

    def leader_summarize(
        self,
        *,
        leader_agent_id: str,
        scenario: DatabaseScenario,
        reports: list[dict],
    ) -> dict:
        schema = LeaderReport.model_json_schema()
        schema["properties"]["leader_agent_id"] = {
            "type": "string",
            "const": leader_agent_id,
        }
        return self._call(
            "manager",
            "你是数据库 AgentTeams 的领域 Leader。只汇总直属 Worker 已提交且通过验收的报告，"
            "不得新增证据、读取隐藏答案或替其他领域作结论。",
            {
                "scenario": self._scenario_payload(scenario),
                "leader_agent_id": leader_agent_id,
                "worker_reports": reports,
            },
            schema,
        )

    def manager_plan(
        self,
        *,
        scenario: DatabaseScenario,
        initial_assignment_count: int,
        reserved_followup_calls: int,
    ) -> dict:
        skill = self.skills.load(["agentteams-database-diagnosis"])[0]
        schema = ManagerPlan.model_json_schema()
        assignments_schema = schema["properties"]["assignments"]
        assignments_schema["minItems"] = initial_assignment_count
        assignments_schema["maxItems"] = initial_assignment_count
        return self._call(
            "manager",
            "你是数据库 AgentTeams Manager。根据公开场景和受限团队能力目录规划第一轮并行调查。"
            "旧场景必须覆盖每个 Worker；coordination 场景必须在 tool_budget 内预留一次可能的复核调用，"
            f"本轮必须恰好分配 {initial_assignment_count} 个 Worker，"
            f"并为后续复核预留 {reserved_followup_calls} 次 Tool 调用；"
            "每个被选 Worker 最多分配一次且只能选择其 allowed_signals；"
            "不得请求隐藏答案、任意 SQL、额外 Agent 或目录路径。\n" + skill.content,
            {
                "scenario": self._scenario_payload(scenario),
                "team_catalog": manager_team_catalog(),
                "inspection_contract": database_inspection_contract([scenario.id]),
                "planning_constraints": {
                    "initial_assignment_count": initial_assignment_count,
                    "reserved_followup_calls": reserved_followup_calls,
                },
            },
            schema,
        )

    def manager_review(
        self,
        *,
        scenario: DatabaseScenario,
        reports: list[dict],
        observed_signals: list[str],
        remaining_tool_calls: int,
    ) -> dict:
        return self._call(
            "manager",
            "你是数据库 AgentTeams Manager，正在复核第一轮报告。若证据冲突或不足且仍有预算，"
            "只追加一个已授权的定向调查，可用受限 filters 缩小检查范围；"
            "不得原样重复已有查询，否则结束调查。不得读取隐藏答案。",
            {
                "scenario": self._scenario_payload(scenario),
                "worker_reports": reports,
                "observed_signals": observed_signals,
                "remaining_tool_calls": remaining_tool_calls,
                "team_catalog": manager_team_catalog(),
                "inspection_contract": database_inspection_contract([scenario.id]),
            },
            ManagerReview.model_json_schema(),
        )

    def manager_diagnose(self, *, scenario: DatabaseScenario, reports: list[dict]) -> dict:
        skill = self.skills.load(["agentteams-database-diagnosis"])[0]
        return self._call(
            "manager",
            "你是数据库 AgentTeams Manager。只汇总 Worker 的审计证据；"
            "不得新增证据或读取隐藏答案。\n" + skill.content,
            {
                "scenario": self._scenario_payload(scenario, include_taxonomy=True),
                "worker_reports": reports,
            },
            self._diagnosis_schema(DiagnosisResult.model_json_schema()),
        )

    def uncoordinated_diagnose(self, *, scenario: DatabaseScenario, reports: list[dict]) -> dict:
        return self._call(
            "manager",
            "你是无协调消融组的机械汇总器。不得跨报告解决冲突、补充推理或追加调查；"
            "只能根据各 Worker 明确写出的发现和证据，选择被直接支持最多的候选根因并拼接结论。",
            {
                "scenario": self._scenario_payload(scenario, include_taxonomy=True),
                "worker_reports": reports,
            },
            self._diagnosis_schema(DiagnosisResult.model_json_schema()),
        )


def build_deepseek_database_backend(
    *,
    project_root: Path,
    environ: Mapping[str, str] | None = None,
    provider_factory: Callable[..., LLMProvider] = DeepSeekProvider,
) -> DeepSeekDatabaseBenchmarkBackend:
    env = os.environ if environ is None else environ
    keys: dict[str, str] = {}
    for role, variable in _KEY_ENVIRONMENTS.items():
        value = env.get(variable, "")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("database_benchmark_key_missing")
        keys[role] = value.strip()
    model = config.agentteams_worker_model()
    role_providers = {
        role: provider_factory(
                api_key=api_key,
                model=model,
                thinking=False,
                reasoning_effort="low",
                request_timeout=DATABASE_BENCHMARK_REQUEST_TIMEOUT_SECONDS,
        )
        for role, api_key in keys.items()
    }
    return DeepSeekDatabaseBenchmarkBackend(
        providers={
            "single_agent": role_providers["manager"],
            **role_providers,
        },
        project_root=project_root,
    )
