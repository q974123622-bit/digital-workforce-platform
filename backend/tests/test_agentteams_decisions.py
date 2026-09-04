from __future__ import annotations

from pathlib import Path

import pytest

from app.services.agentteams_decisions import (
    AgentDecisionEngine,
    AgentDecisionError,
    ManagerContext,
    SkillSelectionContext,
    WorkerContext,
)
from app.services.agentteams_skills import ProjectSkillRegistry
from app.services.llm import LLMProvider


VALID_MANAGER_PLAN = {
    "selected_skill_ids": ["agentteams-onboarding-readiness"],
    "action": "delegate",
    "subtasks": [
        {
            "client_task_key": "hr-check",
            "title": "核查 HR 入职材料",
            "goal": "确认人员档案和确认记录的证据。",
            "required_capability": "hr.onboarding.read",
            "assignee_agent_id": "VE-0002",
            "depends_on": [],
            "required": True,
            "reason": "需要 HR 负责的原始证据。",
        }
    ],
}


class FakeLLM(LLMProvider):
    def __init__(self):
        self.outputs: list[dict] = []
        self.calls: list[list[dict]] = []

    def structured_output(self, messages, schema):
        self.calls.append(messages)
        return self.outputs.pop(0)

    def chat(self, messages, tools=None):  # pragma: no cover - decision boundary only
        raise AssertionError("decision engine must use structured_output")

    def tool_call(self, messages, tools):  # pragma: no cover - decision boundary only
        raise AssertionError("decision engine must use structured_output")


@pytest.fixture()
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture()
def registry() -> ProjectSkillRegistry:
    return ProjectSkillRegistry.from_root(Path(__file__).resolve().parents[2])


@pytest.fixture()
def skill_context() -> SkillSelectionContext:
    return SkillSelectionContext(
        user_goal="评估一名员工是否具备按期入职条件。",
        runtime_skill_id="agentteams-manager-coordination",
        allowed_skill_ids={"agentteams-onboarding-readiness"},
    )


@pytest.fixture()
def manager_context() -> ManagerContext:
    return ManagerContext(
        user_goal="评估一名员工是否具备按期入职条件。",
        manager_skill_id="agentteams-manager-coordination",
        selected_skill_ids=["agentteams-onboarding-readiness"],
        team_capability_catalog=[
            {
                "agent_id": "VE-0002",
                "capabilities": ["hr.onboarding.read"],
                "tool_summaries": ["query_onboarding_employee"],
            }
        ],
        child_results=[],
        current_round=1,
        max_rounds=3,
        max_children=5,
        max_nodes=20,
    )


def test_manager_plan_is_validated_and_retried_once(fake_llm, registry, manager_context):
    """Rejecting an incomplete plan must cause one corrective model round."""
    fake_llm.outputs = [{"subtasks": [{"bad": True}]}, VALID_MANAGER_PLAN]

    decision = AgentDecisionEngine(fake_llm, registry).plan(manager_context)

    assert decision.subtasks[0].required_capability == "hr.onboarding.read"
    assert len(fake_llm.calls) == 2
    retry = fake_llm.calls[1][-1]
    assert retry["source"] == "demo"
    assert "validation_errors" in retry["content"]
    assert "bad" in retry["content"]


def test_skill_selection_only_receives_catalog_summaries(fake_llm, registry, skill_context):
    """Changing a Skill body must not make it into the catalog-selection call."""
    fake_llm.outputs = [{"selected_skill_ids": ["agentteams-onboarding-readiness"]}]

    selection = AgentDecisionEngine(fake_llm, registry).select_skills(skill_context)

    assert selection.selected_skill_ids == ["agentteams-onboarding-readiness"]
    assert all(message["source"] == "demo" for message in fake_llm.calls[0])
    prompt = "\n".join(message["content"] for message in fake_llm.calls[0])
    assert "agentteams-onboarding-readiness" in prompt
    assert "## 完成条件" not in prompt


def test_unknown_skill_is_rejected_before_full_skill_content_is_loaded(
    fake_llm, registry, skill_context,
):
    """An invented ID must not cause a registry load, even during a retry."""
    fake_llm.outputs = [
        {"selected_skill_ids": ["invented-skill"]},
        {"selected_skill_ids": ["still-invented"]},
    ]

    with pytest.raises(AgentDecisionError, match="skill_selection_failed"):
        AgentDecisionEngine(fake_llm, registry).select_skills(skill_context)

    assert len(fake_llm.calls) == 2
    assert "unknown_skill" in fake_llm.calls[1][-1]["content"]
    assert all(
        "## 完成条件" not in message["content"]
        for call in fake_llm.calls
        for message in call
    )


def test_worker_rejects_incompatible_action_fields_once_then_parses_valid_action(
    fake_llm, registry,
):
    """A Tool action cannot also carry an Ask payload into the runtime."""
    fake_llm.outputs = [
        {
            "action": "tool",
            "tool_name": "query_onboarding_employee",
            "message": "this is not an ask",
        },
        {"action": "complete", "result": "已取得可追溯的 HR 证据。"},
    ]
    context = WorkerContext(
        task={"goal": "核查 HR 入职材料", "required_capability": "hr.onboarding.read"},
        professional_skill_ids=["hr-onboarding-assistant"],
        safe_tool_catalog=[{"name": "query_onboarding_employee", "description": "查询演示材料"}],
        dependency_results=[],
        parent_chain_agent_ids=["VE-0001"],
        previous_results=[],
    )

    decision = AgentDecisionEngine(fake_llm, registry).decide_work(context)

    assert decision.action == "complete"
    assert decision.result == "已取得可追溯的 HR 证据。"
    assert len(fake_llm.calls) == 2
    assert "validation_errors" in fake_llm.calls[1][-1]["content"]


def test_second_invalid_manager_plan_raises_safe_planning_error(
    fake_llm, registry, manager_context,
):
    """Two invalid plans must stop before a downstream task graph is created."""
    fake_llm.outputs = [{"action": "delegate"}, {"action": "delegate"}]

    with pytest.raises(AgentDecisionError, match="planning_failed"):
        AgentDecisionEngine(fake_llm, registry).plan(manager_context)
