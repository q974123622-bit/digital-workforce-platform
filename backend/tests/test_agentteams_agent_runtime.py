from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from app.services.agentteams_decisions import (
    AgentDecisionEngine,
    AgentDecisionError,
    FinalSummary,
    ManagerDecision,
    PlannedSubtask,
    RecoveryPlanDecision,
    ResultReviewDecision,
    SkillSelection,
    WorkerDecision,
)
from app.services.agentteams_skills import ProjectSkillRegistry
from app.services.agentteams_gateway import MatrixEvent, MatrixSyncBatch
from app.services.agentteams_protocol import AgentProtocolMessage, parse_protocol_lines, render_protocol_line
from app.services.agentteams_agent_runtime import AgentRuntime, AgentRuntimeConfig
import app.services.agentteams_agent_runtime as runtime_module
from app.services.llm import LLMProvider, LLMUnavailableError


class FakeGateway:
    def __init__(self):
        self.events: list[MatrixEvent] = []
        self.sent: list[tuple[str, str, str]] = []
        self.fail_sends = False
        self.fail_on_send: int | None = None
        self.send_attempts = 0

    def sync_messages(self, since, room_ids):
        assert room_ids == {"!team:local"}
        return MatrixSyncBatch(events=list(self.events), next_cursor="cursor-1")

    def send_protocol_message(self, room_id, text, protocol_line):
        self.send_attempts += 1
        if self.fail_sends or self.fail_on_send == self.send_attempts:
            raise RuntimeError("network unavailable")
        self.sent.append((room_id, text, protocol_line))
        return f"$sent-{len(self.sent)}"

    def close(self):
        pass


class FakeEngine:
    def __init__(self):
        self.selections: list[SkillSelection] = []
        self.plans: list[ManagerDecision] = []
        self.work: list[WorkerDecision] = []
        self.reviews: list[ResultReviewDecision | Exception] = []
        self.recoveries: list[RecoveryPlanDecision | Exception] = []
        self.plan_contexts = []
        self.worker_contexts = []
        self.review_contexts = []
        self.recovery_contexts = []

    def select_skills(self, _context):
        return self.selections.pop(0)

    def plan(self, context):
        self.plan_contexts.append(context)
        return self.plans.pop(0)

    def decide_work(self, context):
        self.worker_contexts.append(context)
        return self.work.pop(0)

    def review_result(self, context):
        self.review_contexts.append(context)
        result = self.reviews.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def plan_recovery(self, context):
        self.recovery_contexts.append(context)
        result = self.recoveries.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


TEAM = {
    "VE-0002": {"capabilities": ["hr.onboarding.read"]},
    "VE-0003": {"capabilities": ["it.readiness.read"]},
    "VE-0005": {"capabilities": ["audit.evidence.read"]},
}
MXIDS = {
    "platform": "@platform:local",
    "VE-0001": "@manager:local",
    "VE-0002": "@hr:local",
    "VE-0003": "@it:local",
    "VE-0005": "@audit:local",
}


def _strict_onboarding_runtime_document() -> dict:
    """Build the CLI fixture from the same eight-role document as bootstrap."""
    import importlib.util

    bootstrap_path = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_agentteams_matrix.py"
    spec = importlib.util.spec_from_file_location("agent_runtime_cli_bootstrap_fixture", bootstrap_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    credentials = {
        name: {"mxid": f"@{name}:test", "token": f"{name}-token"}
        for name in module.ACCOUNT_NAMES
    }
    return module.build_runtime_configuration(
        credentials, "!team:local", matrix_url="https://matrix.test",
    )


def message(*, type, sender, receiver, task_id="task-1", parent_task_id=None, payload=None, key="key-1"):
    return AgentProtocolMessage(
        version="1.0", message_id=f"message-{key}", type=type,
        root_task_id="root-1", task_id=task_id, parent_task_id=parent_task_id,
        trace_id="trace-1", sender=sender, receiver=receiver,
        timestamp="2026-09-04T10:00:00+08:00", idempotency_key=key,
        payload=payload or {},
    )


def event(protocol, *, event_id="$event-1", sender=None, ts=1):
    return MatrixEvent(
        event_id=event_id, room_id="!team:local", sender=sender or MXIDS[protocol.sender],
        body=render_protocol_line(protocol), ts=ts,
    )


def sent_protocols(gateway):
    return [parse_protocol_lines(line)[0] for _, _, line in gateway.sent]


def runtime(tmp_path, role="manager", agent_id=None):
    agent_id = agent_id or {"manager": "VE-0001", "hr": "VE-0002", "it": "VE-0003", "audit": "VE-0005"}[role]
    gateway, engine = FakeGateway(), FakeEngine()
    pauses = []
    config = AgentRuntimeConfig(
        agent_id=agent_id, role=role, matrix_mxid=MXIDS[agent_id], matrix_token="never-persist",
        room_id="!team:local", trusted_sender_mxids=MXIDS, allowed_skill_ids={
            "agentteams-onboarding-readiness", "hr-onboarding-assistant", "it-service-assistant", "audit-evidence-review",
        }, team_catalog=TEAM, safe_tool_catalog=[{
            "name": "query_onboarding_employee", "plugin_id": "agentteams-onboarding-employee", "action": "read",
        }], state_dir=tmp_path / "state",
    )
    return AgentRuntime(config, gateway, engine, pause=pauses.append), gateway, engine, pauses


def test_fresh_real_runtime_can_establish_a_cursor_before_advertising_heartbeat(tmp_path):
    subject, _, _, _ = runtime(tmp_path)

    class InitialGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.initial_calls = []

        def initial_sync_cursor(self, room_ids):
            self.initial_calls.append(room_ids)
            return "current-room-end"

    gateway = InitialGateway()
    subject.gateway = gateway

    assert subject.initialize_matrix_cursor() is True
    assert gateway.initial_calls == [{"!team:local"}]
    persisted = json.loads(subject._state_path.read_text(encoding="utf-8"))
    assert persisted["cursor"] == "current-room-end"
    assert persisted["heartbeat_at"] is None

    assert subject.initialize_matrix_cursor() is False
    assert gateway.initial_calls == [{"!team:local"}]


def plan(action="delegate"):
    if action == "delegate":
        return ManagerDecision(
            selected_skill_ids=["agentteams-onboarding-readiness"], action="delegate",
            subtasks=[
                PlannedSubtask(client_task_key="hr", title="HR 核查", goal="核查资料", required_capability="hr.onboarding.read", assignee_agent_id="VE-0002", reason="HR证据"),
                PlannedSubtask(client_task_key="it", title="IT 核查", goal="核查账号", required_capability="it.readiness.read", assignee_agent_id="VE-0003", reason="IT证据"),
            ],
        )
    if action == "summarize":
        return ManagerDecision(selected_skill_ids=["agentteams-onboarding-readiness"], action="summarize", summary=FinalSummary(summary="可按期入职", readiness_decision="ready", completed_items=["资料"], risks=[], unknowns=[], next_actions=[]))
    return ManagerDecision(selected_skill_ids=["agentteams-onboarding-readiness"], action="fail", failure_reason="证据不足")


def reasoned(decision, text):
    decision._attach_reasoning(text)
    return decision


def assign_payload():
    return {
        "title": "HR 核查", "instruction": "核验新员工A的完整HR入职材料，并报告缺口。",
        "client_task_key": "hr", "dependency_results": [], "parent_chain_agent_ids": ["VE-0001"],
        "required_capability": "hr.onboarding.read",
        "team_capability_catalog": manager_payload()["team_capability_catalog"], "team_catalog_version": 1,
    }


def manager_payload(instruction="评估新员工A", version=1):
    return {
        "instruction": instruction,
        "team_capability_catalog": [{"agent_id": key, **value} for key, value in TEAM.items()],
        "team_catalog_version": version,
    }


@pytest.mark.parametrize(
    ("action", "expected_type"),
    [("accept", "task.result.accepted"), ("reject", "task.result.rejected")],
)
def test_coordinator_reviews_a_submitted_result(tmp_path, action, expected_type):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    expected_feedback = (
        "证据完整，可以验收" if action == "accept" else "请补充审批记录"
    )
    engine.reviews = [
        ResultReviewDecision(
            action=action,
            feedback=expected_feedback,
        )
    ]
    review_request = message(
        type="task.result.submitted",
        sender="platform",
        receiver="VE-0002",
        task_id="worker-task-1",
        parent_task_id="leader-task-1",
        payload={
            "title": "核查审批记录",
            "instruction": "核对审批证据",
            "assignee_agent_id": "VE-0003",
            "result": {"result": "已核查", "audit_ids": [101]},
        },
        key=f"review-{action}",
    )
    gateway.events = [event(review_request)]

    assert subject.run_once() == 1

    sent = sent_protocols(gateway)
    assert sent[-1].type == expected_type
    assert sent[-1].receiver == "platform"
    assert sent[-1].payload["feedback"] == expected_feedback
    assert engine.review_contexts[0].task == {
        "task_id": "worker-task-1",
        "title": "核查审批记录",
        "goal": "核对审批证据",
        "acceptance_criteria": ["核对审批证据"],
    }


def test_review_decision_failure_rejects_for_revision_instead_of_failing_worker_node(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.reviews = [AgentDecisionError("decision_failed")]
    gateway.events = [
        event(
            message(
                type="task.result.submitted",
                sender="platform",
                receiver="VE-0002",
                task_id="worker-task-1",
                parent_task_id="leader-task-1",
                payload={
                    "title": "核查审批记录",
                    "assignee_agent_id": "VE-0003",
                    "result": {"result": "候选结果"},
                },
                key="review-failed",
            )
        )
    ]

    assert subject.run_once() == 1

    response = sent_protocols(gateway)[-1]
    assert response.type == "task.result.rejected"
    assert response.payload == {
        "feedback": "验收未完成，请根据任务要求复核后重新提交",
        "reason_code": "decision_failed",
    }


def test_recovery_request_reaches_leader_replan_decision_path(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    replacement = PlannedSubtask(
        client_task_key="materials-retry",
        title="重新核查 HR 材料",
        goal="重新读取并核验入职材料证据。",
        required_capability="it.readiness.read",
        assignee_agent_id="VE-0003",
        depends_on=[],
        required=True,
        reason="原任务中断，需要重新取得证据。",
    )
    engine.reviews = [
        ResultReviewDecision(
            action="replan",
            feedback="原任务中断，重新派发材料核查。",
        )
    ]
    engine.recoveries = [RecoveryPlanDecision(
        action="replan",
        feedback="原任务中断，重新派发材料核查。",
        subtasks=[replacement],
    )]
    recovery_request = message(
        type="task.recovery.requested",
        sender="platform",
        receiver="VE-0002",
        task_id="worker-task-1",
        parent_task_id="leader-task-1",
        payload={
            "title": "核查 HR 入职材料",
            "instruction": "核验材料证据",
            "required_capability": "it.readiness.read",
            "assignee_agent_id": "VE-0003",
            "result_status": "INTERRUPTED",
            "result": {},
            "evidence": [],
            "current_dag": [],
            "team_capability_catalog": [{
                "agent_id": "VE-0003",
                "capabilities": ["it.readiness.read"],
            }],
            "team_relationship_graph": {
                "self": {"agent_id": "VE-0002", "role": "HR负责人", "level": "leader"},
                "reports_to": ["VE-0001"],
                "direct_reports": [{
                    "agent_id": "VE-0003",
                    "role": "IT专员",
                    "capabilities": ["it.readiness.read"],
                }],
            },
            "remaining_recovery_attempts": 2,
        },
        key="recovery-request",
    )
    gateway.events = [event(recovery_request)]

    assert subject.run_once() == 1

    sent = sent_protocols(gateway)
    assert sent[-1].type == "task.replan"
    assert sent[-1].payload["feedback"] == "原任务中断，重新派发材料核查。"
    assert sent[-1].payload["subtasks"][0]["client_task_key"] == "materials-retry"
    context = engine.review_contexts[0]
    assert context.result_status == "INTERRUPTED"
    assert not hasattr(context, "remaining_recovery_attempts")
    recovery_context = engine.recovery_contexts[0]
    assert recovery_context.remaining_recovery_attempts == 2
    assert recovery_context.direct_reports == [{
        "agent_id": "VE-0003",
        "capabilities": ["it.readiness.read"],
    }]


def test_recovery_decision_failure_reports_blocked_instead_of_failing_as_worker(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.reviews = [AgentDecisionError("decision_failed")]
    gateway.events = [event(message(
        type="task.recovery.requested",
        sender="platform",
        receiver="VE-0002",
        task_id="worker-task-1",
        parent_task_id="leader-task-1",
        payload={
            "title": "核查 HR 入职材料",
            "result_status": "INTERRUPTED",
            "remaining_recovery_attempts": 1,
        },
        key="recovery-decision-failed",
    ))]

    assert subject.run_once() == 1

    response = sent_protocols(gateway)[-1]
    assert response.type == "task.blocked"
    assert response.payload == {
        "feedback": "恢复决策未完成，已向上级报告阻塞",
        "reason_code": "decision_failed",
    }


def test_manager_selects_skill_then_delegates_without_invalid_root_ack(tmp_path):
    subject, gateway, engine, pauses = runtime(tmp_path)
    engine.selections = [
        SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"]),
        SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"]),
    ]
    engine.plans = [plan(), plan()]
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload()))]

    assert subject.run_once() == 1

    sent = sent_protocols(gateway)
    assert [item.type for item in sent] == ["agent.message", "task.delegate"]
    assert sent[0].payload == {"kind": "skill.selected", "selected_skill_ids": ["agentteams-onboarding-readiness"]}
    assert {item["assignee_agent_id"] for item in sent[1].payload["subtasks"]} == {"VE-0002", "VE-0003"}
    assert all("required_capability" in item for item in sent[1].payload["subtasks"])
    assert pauses == [0.0, 0.0]


def test_completed_matrix_batch_discards_event_ids_behind_advanced_cursor(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.selections = [
        SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])
    ]
    engine.plans = [plan()]
    gateway.events = [
        event(
            message(
                type="task.submit",
                sender="platform",
                receiver="VE-0001",
                payload=manager_payload(),
                key="current-submit",
            ),
            event_id="$current-submit",
        )
    ]

    assert subject.run_once() == 1

    assert subject._state["cursor"] == "cursor-1"
    assert subject._state["processed_event_ids"] == []
    assert "current-submit" in subject._state["processed_idempotency_keys"]


def test_manager_runtime_receives_platform_relationship_graph_in_each_planning_round(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    graph = {
        "self": {"agent_id": "VE-0001", "role": "Manager", "level": "manager"},
        "reports_to": [],
        "direct_reports": [
            {"agent_id": "VE-0002", "role": "HR负责人", "capabilities": ["hr.onboarding.read"]},
            {"agent_id": "VE-0003", "role": "IT负责人", "capabilities": ["it.readiness.read"]},
            {"agent_id": "VE-0005", "role": "审计专员", "capabilities": ["audit.evidence.read"]},
        ],
    }
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    engine.plans = [plan(), ManagerDecision(
        selected_skill_ids=["agentteams-onboarding-readiness"], action="delegate",
        subtasks=[PlannedSubtask(
            client_task_key="audit-review", title="审计复核", goal="复核部门证据",
            required_capability="audit.evidence.read", assignee_agent_id="VE-0005",
            reason="第二轮独立复核",
        )],
    )]
    submit = message(
        type="task.submit", sender="platform", receiver="VE-0001",
        payload={**manager_payload(), "team_relationship_graph": graph},
    )
    summary = message(
        type="task.summary", sender="platform", receiver="VE-0001", key="relationship-summary",
        payload={
            "request": "task.summary", "child_results": [{"result": "ok"}],
            "existing_child_task_keys": ["hr", "it"], "current_round": 1,
            "remaining_limits": {"rounds": 3, "nodes": 18, "children": 5},
            "team_capability_catalog": manager_payload()["team_capability_catalog"],
            "team_catalog_version": 2, "team_relationship_graph": graph,
        },
    )
    gateway.events = [event(submit, event_id="$relationship-submit", ts=1), event(summary, event_id="$relationship-summary", ts=2)]

    assert subject.run_once() == 2

    assert engine.plan_contexts[0].team_relationship_graph == graph
    assert engine.plan_contexts[1].team_relationship_graph == graph
    assert sent_protocols(gateway)[-1].payload["round"] == 2


def test_manager_planning_context_exposes_only_direct_team_leaders(tmp_path):
    subject, _, _, _ = runtime(tmp_path)
    graph = {
        "self": {"agent_id": "VE-0001", "role": "Manager", "level": "manager"},
        "reports_to": [],
        "direct_reports": [
            {"agent_id": "VE-0002", "role": "HR负责人", "capabilities": ["hr.onboarding.read"]},
            {"agent_id": "VE-0003", "role": "IT负责人", "capabilities": ["it.readiness.read"]},
        ],
    }
    catalog = {
        **TEAM,
        "VE-HR-MATERIAL": {"capabilities": ["hr.materials.read"]},
        "VE-IT-DEVICE": {"capabilities": ["it.devices.read"]},
    }

    context = subject._manager_context(
        goal="评估新员工A",
        selected=["agentteams-onboarding-readiness"],
        catalog=catalog,
        child_results=[],
        existing_child_task_keys=[],
        current_round=1,
        limits={},
        relationship_graph=graph,
    )

    assert {item["agent_id"] for item in context.team_capability_catalog} == {
        "VE-0002", "VE-0003",
    }


def test_manager_uses_non_thinking_engine_when_task_disables_thinking(tmp_path):
    subject, gateway, thinking_engine, pauses = runtime(tmp_path)
    non_thinking_engine = FakeEngine()
    non_thinking_engine.selections = [
        SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"]),
    ]
    non_thinking_engine.plans = [plan()]
    subject.non_thinking_decisions = non_thinking_engine
    gateway.events = [event(message(
        type="task.submit",
        sender="platform",
        receiver="VE-0001",
        payload={**manager_payload(), "thinking_enabled": False},
    ))]

    assert subject.run_once() == 1

    assert thinking_engine.selections == []
    assert [item.type for item in sent_protocols(gateway)] == ["agent.message", "task.delegate"]
    assert pauses == [0.0, 0.0]


def test_manager_persists_reasoning_before_the_delegate_action(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.selections = [reasoned(
        SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"]),
        "用户要求评估入职条件，应选择入职就绪 Skill。",
    )]
    engine.plans = [reasoned(
        plan(),
        "当前没有 HR 与 IT 的一手证据，应先并行派发核查任务。",
    )]
    gateway.events = [event(message(
        type="task.submit",
        sender="platform",
        receiver="VE-0001",
        payload=manager_payload(),
    ))]

    subject.run_once()

    sent = sent_protocols(gateway)
    assert [item.type for item in sent] == [
        "agent.message", "agent.message", "agent.message", "task.delegate",
    ]
    traces = [item.payload for item in sent if item.payload.get("kind") == "decision.trace"]
    assert traces == [
        {
            "kind": "decision.trace",
            "stage": "skill_selection",
            "iteration": 1,
            "reasoning_segments": ["用户要求评估入职条件，应选择入职就绪 Skill。"],
        },
        {
            "kind": "decision.trace",
            "stage": "decision",
            "iteration": 1,
            "action": "delegate",
            "reasoning_segments": ["当前没有 HR 与 IT 的一手证据，应先并行派发核查任务。"],
        },
    ]


def test_runtime_surfaces_persistent_llm_unavailability_as_fixed_failure(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    raw_provider_error = "Bearer secret-token provider temporarily unavailable"

    def unavailable(_context):
        raise LLMUnavailableError(raw_provider_error)

    engine.select_skills = unavailable
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload()))]

    assert subject.run_once() == 1

    failure = sent_protocols(gateway)[-1]
    assert failure.type == "task.failed"
    assert failure.payload == {"error_code": "llm_unavailable"}
    assert raw_provider_error not in gateway.sent[-1][2]


@pytest.mark.parametrize(
    "provider_code",
    [
        "network_error", "http_error", "rate_limited", "client_error", "server_error",
        "invalid_response_json", "invalid_structured_json",
    ],
)
def test_runtime_surfaces_safe_provider_failure_code(tmp_path, provider_code):
    subject, gateway, engine, _ = runtime(tmp_path)

    def unavailable(_context):
        raise LLMUnavailableError("provider detail must remain private", code=provider_code)

    engine.select_skills = unavailable
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload()))]

    assert subject.run_once() == 1

    failure = sent_protocols(gateway)[-1]
    assert failure.payload == {"error_code": provider_code}
    assert "provider detail must remain private" not in gateway.sent[-1][2]


def test_runtime_suppresses_unknown_provider_failure_code(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)

    def unavailable(_context):
        raise LLMUnavailableError("token=provider-private", code="provider_private_reason")

    engine.select_skills = unavailable
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload()))]

    assert subject.run_once() == 1

    failure = sent_protocols(gateway)[-1]
    assert failure.payload == {"error_code": "runtime_error"}
    assert "provider-private" not in gateway.sent[-1][2]


def test_runtime_keeps_programming_errors_as_fixed_runtime_failure(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    raw_programming_error = "api_key=secret unexpected programming failure"

    def broken(_context):
        raise RuntimeError(raw_programming_error)

    engine.select_skills = broken
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload()))]

    assert subject.run_once() == 1

    failure = sent_protocols(gateway)[-1]
    assert failure.payload == {"error_code": "runtime_error"}
    assert raw_programming_error not in gateway.sent[-1][2]


def test_runtime_keeps_agent_decision_error_code(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)

    def invalid_decision(_context):
        raise AgentDecisionError("planning_failed")

    engine.select_skills = invalid_decision
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload()))]

    assert subject.run_once() == 1
    assert sent_protocols(gateway)[-1].payload == {"error_code": "planning_failed"}


def test_manager_summary_can_follow_up_or_finish(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.plans = [plan(), plan("summarize")]
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    submit = message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload())
    summary = message(type="task.summary", sender="platform", receiver="VE-0001", payload={"request": "task.summary", "child_results": [{"result": "ok"}], "existing_child_task_keys": ["hr-check", "it-check"], "current_round": 1, "remaining_limits": {"rounds": 3, "nodes": 18, "children": 5}, "team_capability_catalog": manager_payload()["team_capability_catalog"], "team_catalog_version": 2} , key="summary")
    gateway.events = [event(submit, event_id="$1", ts=1), event(summary, event_id="$2", ts=2)]

    subject.run_once()

    sent = sent_protocols(gateway)
    assert [item.type for item in sent] == ["agent.message", "task.delegate", "task.summary"]
    assert sent[-1].payload["summary"] == "可按期入职"
    assert sent[-1].payload["readiness_decision"] == "ready"
    assert engine.plan_contexts[-1].child_results == [{"result": "ok"}]
    assert engine.plan_contexts[-1].existing_child_task_keys == ["hr-check", "it-check"]


def test_manager_context_preserves_zero_remaining_rounds(tmp_path):
    """A zero remaining-round budget must reach Manager as zero, not become a new default budget."""
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.plans = [plan(), plan("summarize")]
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    submit = message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload())
    summary = message(
        type="task.summary", sender="platform", receiver="VE-0001", key="zero-rounds",
        payload={
            "request": "task.summary", "child_results": [{"result": "ok"}],
            "existing_child_task_keys": ["hr-check", "it-check"], "current_round": 3,
            "remaining_limits": {"rounds": 0, "nodes": 18, "children": 5},
            "team_capability_catalog": manager_payload()["team_capability_catalog"],
            "team_catalog_version": 2,
        },
    )
    gateway.events = [event(submit, event_id="$zero-submit", ts=1), event(summary, event_id="$zero-summary", ts=2)]

    subject.run_once()

    assert engine.plan_contexts[-1].remaining_rounds == 0


def test_worker_requests_tool_then_can_complete_only_after_real_tool_evidence(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [
        WorkerDecision(action="tool", tool_name="query_onboarding_employee", tool_arguments={"employee_ref": "新员工A"}),
        WorkerDecision(action="complete", result="资料齐全"),
    ]
    payload = assign_payload()
    for member in payload["team_capability_catalog"]:
        member["mxid"] = f"@{member['agent_id'].lower()}:must-not-reach-model"
        member["token"] = "must-not-reach-model"
    assignment = message(type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1", payload=payload)
    tool_result = message(type="tool.result", sender="platform", receiver="VE-0002", parent_task_id="root-1", payload={"ok": True, "audit_ids": [101], "data": {"source": "demo", "approval_ref": "AUD-RC-001", "key": "drop", "note": "api_key: exposed password=hidden"}, "team_capability_catalog": manager_payload()["team_capability_catalog"], "team_catalog_version": 2}, key="tool")
    gateway.events = [event(assignment, event_id="$assign", sender=MXIDS["VE-0001"], ts=1), event(tool_result, event_id="$tool", ts=2)]

    subject.run_once()

    sent = sent_protocols(gateway)
    assert [item.type for item in sent] == ["task.accepted", "task.progress", "agent.message", "tool.request", "task.result"]
    assert sent[3].receiver == "platform"
    assert sent[3].payload["plugin_id"] == "agentteams-onboarding-employee"
    assert sent[-1].payload["audit_ids"] == [101]
    assert sent[-1].payload["approval_ref"] == "AUD-RC-001"
    assert engine.worker_contexts[0].task["goal"] == "核验新员工A的完整HR入职材料，并报告缺口。"
    assert engine.worker_contexts[0].current_agent_id == "VE-0002"
    assert engine.worker_contexts[0].current_team_capability_catalog == [
        {"agent_id": "VE-0002", "capabilities": ["hr.onboarding.read"]},
        {"agent_id": "VE-0003", "capabilities": ["it.readiness.read"]},
        {"agent_id": "VE-0005", "capabilities": ["audit.evidence.read"]},
    ]
    assert "mxid" not in str(engine.worker_contexts[0].current_team_capability_catalog).lower()
    assert "token" not in str(engine.worker_contexts[0].current_team_capability_catalog).lower()
    saved = (tmp_path / "state" / "VE-0002.json").read_text(encoding="utf-8")
    assert "exposed" not in saved and "password" not in saved and '"key"' not in saved
    assert "101" in saved


def test_worker_rejects_an_identical_tool_request_after_successful_evidence(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    duplicate = WorkerDecision(
        action="tool",
        tool_name="query_onboarding_employee",
        tool_arguments={"employee_ref": "新员工A"},
    )
    engine.work = [
        duplicate,
        duplicate.model_copy(deep=True),
        WorkerDecision(action="complete", result="已使用首次工具证据完成核查"),
    ]
    assignment = message(
        type="task.assign", sender="VE-0001", receiver="VE-0002",
        parent_task_id="root-1", payload=assign_payload(),
    )
    tool_result = message(
        type="tool.result", sender="platform", receiver="VE-0002",
        parent_task_id="root-1",
        payload={
            "ok": True,
            "audit_ids": [101],
            "team_capability_catalog": manager_payload()["team_capability_catalog"],
            "team_catalog_version": 2,
        },
        key="tool-result-after-first-request",
    )
    gateway.events = [
        event(assignment, event_id="$assign", sender=MXIDS["VE-0001"], ts=1),
        event(tool_result, event_id="$tool-result", ts=2),
    ]

    subject.run_once()

    sent = sent_protocols(gateway)
    assert [item.type for item in sent].count("tool.request") == 1
    assert sent[-1].type == "task.result"
    assert any(
        item.get("kind") == "runtime.rejected"
        and item.get("reason") == "duplicate_tool_request"
        for item in engine.worker_contexts[-1].previous_results
    )


def test_department_lead_completes_from_authenticated_platform_summary_audit_ids(tmp_path):
    """Ignoring canonical summary audit IDs would fail a lead after its specialists finish."""
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [
        WorkerDecision(
            action="delegate",
            subtask=PlannedSubtask(
                client_task_key="audit",
                title="审计",
                goal="复核",
                required_capability="audit.evidence.read",
                assignee_agent_id="VE-0005",
                reason="取得直属子任务证据",
            ),
        ),
        WorkerDecision(action="complete", result="直属子任务均已完成"),
        WorkerDecision(action="complete", result="不应进入纠正重试"),
    ]
    assignment = message(
        type="task.assign",
        sender="VE-0001",
        receiver="VE-0002",
        parent_task_id="root-1",
        payload=assign_payload(),
    )
    summary = message(
        type="task.summary",
        sender="platform",
        receiver="VE-0002",
        parent_task_id="root-1",
        payload={
            "request": "task.summary",
            "audit_ids": [101, 202],
            "child_results": [
                {"client_task_key": "materials", "status": "completed", "result": {"audit_ids": [101]}},
                {"client_task_key": "training", "status": "completed", "result": {"audit_ids": [202]}},
            ],
            "team_capability_catalog": manager_payload()["team_capability_catalog"],
            "team_catalog_version": 2,
        },
        key="canonical-child-summary",
    )
    gateway.events = [
        event(assignment, event_id="$assign", sender=MXIDS["VE-0001"], ts=1),
        event(summary, event_id="$summary", sender=MXIDS["platform"], ts=2),
    ]

    assert subject.run_once() == 2

    result = sent_protocols(gateway)[-1]
    assert result.type == "task.result"
    assert result.payload["audit_ids"] == [101, 202]


def test_department_lead_keeps_latest_child_summary_when_full_envelope_exceeds_evidence_budget(tmp_path):
    """A large catalog/summary envelope must not erase the only completion evidence."""
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [
        WorkerDecision(
            action="delegate",
            subtask=PlannedSubtask(
                client_task_key="audit",
                title="审计",
                goal="复核",
                required_capability="audit.evidence.read",
                assignee_agent_id="VE-0005",
                reason="取得直属子任务证据",
            ),
        ),
        WorkerDecision(action="complete", result="已汇总直属专员证据"),
    ]
    assignment = message(
        type="task.assign",
        sender="VE-0001",
        receiver="VE-0002",
        parent_task_id="root-1",
        payload=assign_payload(),
    )
    large_result = {
        f"evidence_section_{index}": "证据内容" * 300
        for index in range(8)
    }
    summary_payload = {
        "request": "task.summary",
        "audit_ids": [101, 202],
        "child_results": [
            {"client_task_key": "materials", "status": "completed", "result": large_result},
            {"client_task_key": "training", "status": "completed", "result": large_result},
        ],
        "team_capability_catalog": manager_payload()["team_capability_catalog"],
        "team_catalog_version": 2,
        "remaining_limits": {"rounds": 3, "nodes": 20, "children": 5, "depth": 2},
    }
    summary = message(
        type="task.summary",
        sender="platform",
        receiver="VE-0002",
        parent_task_id="root-1",
        payload=summary_payload,
        key="large-canonical-child-summary",
    )
    gateway.events = [
        event(assignment, event_id="$assign", sender=MXIDS["VE-0001"], ts=1),
        event(summary, event_id="$summary", sender=MXIDS["platform"], ts=2),
    ]

    assert subject.run_once() == 2

    result = sent_protocols(gateway)[-1]
    assert result.type == "task.result"
    assert result.payload["audit_ids"] == [101, 202]
    latest_context = engine.worker_contexts[-1]
    assert latest_context.previous_results
    assert latest_context.previous_results[-1]["kind"] == "child.summary"
    assert len(json.dumps(latest_context.previous_results, ensure_ascii=False)) <= 4096


def test_department_lead_rejects_nested_or_textual_summary_audit_ids(tmp_path):
    """Recursively trusting model-visible child data would allow forged audit evidence."""
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [
        WorkerDecision(
            action="delegate",
            subtask=PlannedSubtask(
                client_task_key="audit",
                title="审计",
                goal="复核",
                required_capability="audit.evidence.read",
                assignee_agent_id="VE-0005",
                reason="取得直属子任务证据",
            ),
        ),
        WorkerDecision(action="complete", result="尝试伪造完成"),
        WorkerDecision(action="complete", result="仍然尝试伪造完成"),
    ]
    assignment = message(
        type="task.assign",
        sender="VE-0001",
        receiver="VE-0002",
        parent_task_id="root-1",
        payload=assign_payload(),
    )
    summary = message(
        type="task.summary",
        sender="platform",
        receiver="VE-0002",
        parent_task_id="root-1",
        payload={
            "request": "task.summary",
            "child_results": [
                {"audit_ids": [9001]},
                {"result": {"audit_ids": [9002]}},
                {"result": '{"audit_ids":[9003]}'},
            ],
            "note": "audit_ids=[9004]",
            "team_capability_catalog": manager_payload()["team_capability_catalog"],
            "team_catalog_version": 2,
        },
        key="forged-child-summary",
    )
    gateway.events = [
        event(assignment, event_id="$assign", sender=MXIDS["VE-0001"], ts=1),
        event(summary, event_id="$summary", sender=MXIDS["platform"], ts=2),
    ]

    assert subject.run_once() == 2

    failure = sent_protocols(gateway)[-1]
    assert failure.type == "task.failed"
    assert failure.payload["error_code"] == "completion_without_tool_evidence"


@pytest.mark.parametrize(
    "audit_ids",
    [
        pytest.param([{}], id="unhashable-object"),
        pytest.param([101, 101], id="duplicate"),
        pytest.param([True], id="boolean"),
        pytest.param([-1], id="negative"),
        pytest.param([2 ** 63], id="out-of-range"),
    ],
)
def test_department_lead_treats_malformed_summary_audit_ids_as_missing_evidence(
    tmp_path, audit_ids,
):
    """Malformed top-level audit IDs must use the fixed missing-evidence failure path."""
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [
        WorkerDecision(
            action="delegate",
            subtask=PlannedSubtask(
                client_task_key="audit",
                title="审计",
                goal="复核",
                required_capability="audit.evidence.read",
                assignee_agent_id="VE-0005",
                reason="取得直属子任务证据",
            ),
        ),
        WorkerDecision(action="complete", result="尝试使用非法证据完成"),
        WorkerDecision(action="complete", result="纠正后仍无有效证据"),
    ]
    assignment = message(
        type="task.assign",
        sender="VE-0001",
        receiver="VE-0002",
        parent_task_id="root-1",
        payload=assign_payload(),
    )
    summary = message(
        type="task.summary",
        sender="platform",
        receiver="VE-0002",
        parent_task_id="root-1",
        payload={
            "request": "task.summary",
            "audit_ids": audit_ids,
            "child_results": [],
            "team_capability_catalog": manager_payload()["team_capability_catalog"],
            "team_catalog_version": 2,
        },
        key=f"malformed-summary-{audit_ids!r}",
    )
    gateway.events = [
        event(assignment, event_id="$assign", sender=MXIDS["VE-0001"], ts=1),
        event(summary, event_id="$summary", sender=MXIDS["platform"], ts=2),
    ]

    assert subject.run_once() == 2

    failure = sent_protocols(gateway)[-1]
    assert failure.type == "task.failed"
    assert failure.payload["error_code"] == "completion_without_tool_evidence"


def test_worker_persists_each_reasoning_iteration_around_tool_evidence(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [
        reasoned(
            WorkerDecision(action="tool", tool_name="query_onboarding_employee", tool_arguments={}),
            "还没有员工材料证据，先调用查询工具。",
        ),
        reasoned(
            WorkerDecision(action="complete", result="资料齐全"),
            "工具已经返回成功审计记录，可以提交节点结果。",
        ),
    ]
    assignment = message(
        type="task.assign", sender="VE-0001", receiver="VE-0002",
        parent_task_id="root-1", payload=assign_payload(),
    )
    tool_result = message(
        type="tool.result", sender="platform", receiver="VE-0002",
        parent_task_id="root-1",
        payload={
            "ok": True, "audit_ids": [101],
            "team_capability_catalog": manager_payload()["team_capability_catalog"],
            "team_catalog_version": 2,
        },
        key="tool-trace",
    )
    gateway.events = [
        event(assignment, event_id="$assign-trace", sender=MXIDS["VE-0001"], ts=1),
        event(tool_result, event_id="$tool-trace", ts=2),
    ]

    subject.run_once()

    traces = [
        item.payload for item in sent_protocols(gateway)
        if item.payload.get("kind") == "decision.trace"
    ]
    assert [(item["iteration"], item["action"]) for item in traces] == [
        (1, "tool"), (2, "complete"),
    ]
    assert "还没有员工材料证据" in "".join(traces[0]["reasoning_segments"])
    assert "工具已经返回成功审计记录" in "".join(traces[1]["reasoning_segments"])


def test_worker_prompt_receives_tool_input_schema_and_exact_arguments_reach_tool_request(tmp_path):
    """Dropping the schema from the real Worker catalog must break this outbound behavior."""
    class CapturingLLM(LLMProvider):
        def __init__(self):
            self.outputs = [
                {"selected_skill_ids": ["hr-onboarding-assistant"]},
                {
                    "action": "tool",
                    "tool_name": "agentteams.hr.lookup_onboarding",
                    "tool_arguments": {"employee_ref": "新员工A"},
                },
            ]
            self.calls = []

        def structured_output(self, messages, schema):
            self.calls.append(messages)
            return self.outputs.pop(0)

        def chat(self, messages, tools=None):  # pragma: no cover - wrong boundary
            raise AssertionError("runtime decisions must use structured_output")

        def tool_call(self, messages, tools):  # pragma: no cover - wrong boundary
            raise AssertionError("runtime decisions must use structured_output")

    input_schema = {
        "type": "object",
        "required": ["employee_ref"],
        "properties": {
            "employee_ref": {"type": "string", "const": "新员工A", "maxLength": 16},
        },
        "additionalProperties": False,
    }
    gateway = FakeGateway()
    provider = CapturingLLM()
    decisions = AgentDecisionEngine(
        provider,
        ProjectSkillRegistry.from_root(Path(__file__).resolve().parents[2]),
    )
    config = AgentRuntimeConfig(
        agent_id="VE-0002",
        role="hr",
        matrix_mxid=MXIDS["VE-0002"],
        matrix_token="never-persist",
        room_id="!team:local",
        trusted_sender_mxids=MXIDS,
        allowed_skill_ids={"hr-onboarding-assistant"},
        team_catalog=TEAM,
        safe_tool_catalog=[{
            "name": "agentteams.hr.lookup_onboarding",
            "plugin_id": "agentteams-onboarding-employee",
            "action": "execute",
            "required_capability": "hr.onboarding.read",
            "input_schema": input_schema,
        }],
        state_dir=tmp_path / "state",
        pause_seconds=0,
    )
    assignment = message(
        type="task.assign",
        sender="VE-0001",
        receiver="VE-0002",
        parent_task_id="root-1",
        payload=assign_payload(),
    )
    gateway.events = [event(assignment, sender=MXIDS["VE-0001"])]

    assert AgentRuntime(config, gateway, decisions, pause=lambda _seconds: None).run_once() == 1

    worker_payload = next(
        json.loads(message["content"])
        for message in provider.calls[1]
        if '"safe_tool_catalog"' in message["content"]
    )
    assert worker_payload["safe_tool_catalog"][0]["input_schema"] == input_schema
    request = sent_protocols(gateway)[-1]
    assert request.type == "tool.request"
    assert request.payload["plugin_id"] == "agentteams-onboarding-employee"
    assert request.payload["params"] == {"employee_ref": "新员工A"}


def test_worker_refreshes_tool_result_catalog_before_delegating(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [
        WorkerDecision(action="tool", tool_name="query_onboarding_employee", tool_arguments={}),
        WorkerDecision(
            action="delegate",
            subtask=PlannedSubtask(
                client_task_key="audit", title="审计复核", goal="核验审计证据",
                required_capability="audit.evidence.read", assignee_agent_id="VE-0005", reason="缺少审计证据",
            ),
        ),
    ]
    assignment = message(
        type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1",
        payload=assign_payload(),
    )
    refreshed_catalog = [{
        "agent_id": "VE-0005", "capabilities": ["audit.evidence.read"],
    }]
    tool_result = message(
        type="tool.result", sender="platform", receiver="VE-0002", parent_task_id="root-1",
        payload={
            "ok": True, "audit_ids": [101], "team_capability_catalog": refreshed_catalog,
            "team_catalog_version": 2,
        },
        key="refreshed-tool",
    )
    gateway.events = [
        event(assignment, event_id="$assign", sender=MXIDS["VE-0001"], ts=1),
        event(tool_result, event_id="$tool", ts=2),
    ]

    assert subject.run_once() == 2

    assert sent_protocols(gateway)[-1].type == "task.delegate"
    assert engine.worker_contexts[-1].current_team_capability_catalog == refreshed_catalog
    assert subject._state["tasks"]["task-1"]["team_catalog_version"] == 2


@pytest.mark.parametrize("stale_version", [1, 0])
def test_worker_rejects_stale_or_invalid_tool_result_catalog(tmp_path, stale_version):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [
        WorkerDecision(action="tool", tool_name="query_onboarding_employee", tool_arguments={}),
        WorkerDecision(action="complete", result="must not use stale authority"),
    ]
    assignment_payload = assign_payload()
    assignment_payload["team_catalog_version"] = 1
    assignment = message(
        type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1",
        payload=assignment_payload,
    )
    tool_result = message(
        type="tool.result", sender="platform", receiver="VE-0002", parent_task_id="root-1",
        payload={
            "ok": True, "audit_ids": [101],
            "team_capability_catalog": manager_payload()["team_capability_catalog"],
            "team_catalog_version": stale_version,
        },
        key=f"stale-tool-{stale_version}",
    )
    gateway.events = [
        event(assignment, event_id="$assign", sender=MXIDS["VE-0001"], ts=1),
        event(tool_result, event_id="$tool", ts=2),
    ]

    assert subject.run_once() == 2

    assert sent_protocols(gateway)[-1].type == "task.failed"
    assert sent_protocols(gateway)[-1].payload == {"error_code": "invalid_team_catalog"}


def test_cli_loaded_tool_catalog_controls_runtime_tool_requests(tmp_path):
    from scripts.run_agentteams_agent import load_runtime_config

    input_schema = {
        "type": "object",
        "required": ["employee_ref"],
        "properties": {
            "employee_ref": {"type": "string", "const": "新员工A", "maxLength": 16},
        },
        "additionalProperties": False,
    }
    document = _strict_onboarding_runtime_document()
    document["agents"]["hr"]["safe_tool_catalog"] = [{
        "name": "agentteams.hr.lookup_onboarding",
        "plugin_id": "hr-employee-mcp",
        "action": "read",
        "required_capability": "hr.onboarding.read",
        "input_schema": input_schema,
    }]
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    config, _ = load_runtime_config("hr", path, env={"DEEPSEEK_API_KEY_HR": "x", "DWP_AGENTTEAMS_RUNTIME_CONFIG": str(path)})
    gateway, engine = FakeGateway(), FakeEngine(); engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [WorkerDecision(action="tool", tool_name="agentteams.hr.lookup_onboarding", tool_arguments={})]
    gateway.events = [event(
        message(type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1", payload=assign_payload()),
        sender=config.trusted_sender_mxids["VE-0001"],
    )]
    AgentRuntime(config, gateway, engine, pause=lambda _: None).run_once()
    request = [item for item in sent_protocols(gateway) if item.type == "tool.request"][-1]
    assert request.payload["tool_name"] == "agentteams.hr.lookup_onboarding"
    assert request.payload["required_capability"] == "hr.onboarding.read"
    gateway, engine = FakeGateway(), FakeEngine(); engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [WorkerDecision(action="tool", tool_name="not-listed", tool_arguments={})]
    gateway.events = [event(
        message(type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1", payload=assign_payload()),
        sender=config.trusted_sender_mxids["VE-0001"],
    )]
    AgentRuntime(config, gateway, engine, pause=lambda _: None).run_once()
    assert all(item.type != "tool.request" for item in sent_protocols(gateway))


def test_agentteams_cli_builds_thinking_and_non_thinking_providers_with_the_configured_model(monkeypatch):
    from scripts.run_agentteams_agent import build_decision_engines

    monkeypatch.setenv("AGENTTEAMS_WORKER_MODEL", "deepseek-v4-flash")

    thinking_engine, non_thinking_engine = build_decision_engines(
        "role-key", Path(__file__).resolve().parents[2]
    )

    assert thinking_engine.provider.model == "deepseek-v4-flash"
    assert non_thinking_engine.provider.model == "deepseek-v4-flash"
    assert thinking_engine.provider.thinking is True
    assert non_thinking_engine.provider.thinking is False


@pytest.mark.parametrize("decision, expected", [
    (WorkerDecision(action="ask", recipient_agent_id="VE-0003", message="请确认账号"), "agent.message"),
    (WorkerDecision(action="delegate", subtask=PlannedSubtask(client_task_key="audit", title="审计", goal="复核", required_capability="audit.evidence.read", assignee_agent_id="VE-0005", reason="审计证据")), "task.delegate"),
])
def test_worker_maps_ask_and_delegate_to_protocol(tmp_path, decision, expected):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [decision]
    gateway.events = [event(message(type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1", payload=assign_payload()), sender=MXIDS["VE-0001"])]

    subject.run_once()

    sent = sent_protocols(gateway)
    assert sent[-1].type == expected
    assert sent[-1].receiver == ("VE-0003" if expected == "agent.message" else "platform")
    if expected == "task.delegate":
        assert sent[-1].payload["subtasks"][0]["required_capability"] == "audit.evidence.read"


def test_department_lead_runtime_emits_parallel_batch_from_platform_relationship_context(tmp_path):
    gateway, engine = FakeGateway(), FakeEngine()
    trusted = {
        **MXIDS,
        "VE-HR-MATERIAL": "@hr-material:local",
        "VE-HR-TRAINING": "@hr-training:local",
    }
    subject = AgentRuntime(AgentRuntimeConfig(
        agent_id="VE-0002", role="hr", matrix_mxid=MXIDS["VE-0002"],
        matrix_token="never-persist", room_id="!team:local",
        trusted_sender_mxids=trusted,
        allowed_skill_ids={"hr-onboarding-assistant"},
        team_catalog={}, safe_tool_catalog=[], state_dir=tmp_path / "state",
    ), gateway, engine, pause=lambda _: None)
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [WorkerDecision(
        action="delegate",
        subtasks=[
            PlannedSubtask(
                client_task_key="hr-materials", title="核查入职材料",
                goal="核查新员工A的入职材料。", required_capability="hr.materials.read",
                assignee_agent_id="VE-HR-MATERIAL", reason="由材料专员取得一手证据。",
            ),
            PlannedSubtask(
                client_task_key="hr-training", title="核查入职培训",
                goal="核查新员工A的培训安排。", required_capability="hr.training.read",
                assignee_agent_id="VE-HR-TRAINING", reason="由培训专员取得一手证据。",
            ),
        ],
    )]
    payload = assign_payload()
    payload["required_capability"] = "hr.onboarding.coordinate"
    payload["team_capability_catalog"] = [
        {"agent_id": "VE-HR-MATERIAL", "capabilities": ["hr.materials.read"]},
        {"agent_id": "VE-HR-TRAINING", "capabilities": ["hr.training.read"]},
    ]
    payload["team_relationship_graph"] = {
        "self": {"agent_id": "VE-0002", "role": "HR负责人", "level": "leader"},
        "reports_to": ["VE-0001"],
        "direct_reports": [
            {"agent_id": "VE-HR-MATERIAL", "role": "HR材料专员", "capabilities": ["hr.materials.read"]},
            {"agent_id": "VE-HR-TRAINING", "role": "HR培训专员", "capabilities": ["hr.training.read"]},
        ],
    }
    gateway.events = [event(message(
        type="task.assign", sender="VE-0001", receiver="VE-0002",
        parent_task_id="root-1", payload=payload,
    ), sender=MXIDS["VE-0001"])]

    assert subject.run_once() == 1

    delegate = sent_protocols(gateway)[-1]
    assert delegate.type == "task.delegate"
    assert {item["assignee_agent_id"] for item in delegate.payload["subtasks"]} == {
        "VE-HR-MATERIAL", "VE-HR-TRAINING",
    }
    assert engine.worker_contexts[0].team_relationship_graph == payload["team_relationship_graph"]


def test_role_change_leader_forwards_verified_dependency_reference_to_worker(tmp_path):
    gateway, engine = FakeGateway(), FakeEngine()
    trusted = {**MXIDS, "VE-IT-DEVICE": "@it-device:local"}
    subject = AgentRuntime(AgentRuntimeConfig(
        agent_id="VE-0003", role="it", matrix_mxid=MXIDS["VE-0003"],
        matrix_token="never-persist", room_id="!team:local",
        trusted_sender_mxids=trusted,
        allowed_skill_ids={"it-service-assistant"},
        team_catalog={}, safe_tool_catalog=[], state_dir=tmp_path / "state",
    ), gateway, engine, pause=lambda _: None)
    engine.selections = [SkillSelection(selected_skill_ids=["it-service-assistant"])]
    engine.work = [WorkerDecision(
        action="delegate",
        subtasks=[PlannedSubtask(
            client_task_key="activate-role", title="执行最终权限激活",
            goal="使用已通过审计的审批编号激活权限。",
            required_capability="it.permission.activate",
            assignee_agent_id="VE-IT-DEVICE", reason="由执行专员完成激活。",
        )],
    )]
    payload = assign_payload()
    payload.update({
        "required_capability": "it.role_change.activate.coordinate",
        "material": {"employee_ref": "张明"},
        "dependency_results": [{
            "client_task_key": "audit-review", "status": "completed",
            "result": {"result": "审计通过", "approval_ref": "AUD-RC-001", "audit_ids": [1295]},
        }],
        "team_capability_catalog": [
            {"agent_id": "VE-IT-DEVICE", "capabilities": ["it.permission.activate"]},
        ],
        "team_relationship_graph": {
            "self": {"agent_id": "VE-0003", "role": "IT负责人", "level": "leader"},
            "reports_to": ["VE-0001"],
            "direct_reports": [{
                "agent_id": "VE-IT-DEVICE", "role": "IT执行专员",
                "capabilities": ["it.permission.activate"],
            }],
        },
    })
    gateway.events = [event(message(
        type="task.assign", sender="VE-0001", receiver="VE-0003",
        parent_task_id="root-1", payload=payload,
    ), sender=MXIDS["VE-0001"])]

    assert subject.run_once() == 1

    context = engine.worker_contexts[0]
    assert context.task["material"] == {"employee_ref": "张明"}
    delegate = sent_protocols(gateway)[-1]
    assert delegate.type == "task.delegate"
    assert delegate.payload["subtasks"][0]["material"] == {
        "employee_ref": "张明",
        "approval_ref": "AUD-RC-001",
    }


def test_spoof_not_for_me_and_parse_errors_are_safely_ignored(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    valid = message(type="task.submit", sender="platform", receiver="VE-0001", payload={"instruction": "x"})
    gateway.events = [
        event(valid, event_id="$spoof", sender="@evil:local"),
        event(message(type="task.submit", sender="platform", receiver="VE-0002", payload={"instruction": "x"}), event_id="$other"),
        MatrixEvent(event_id="$bad", room_id="!team:local", sender=MXIDS["platform"], body="DWP_EVENT {bad", ts=3),
    ]

    assert subject.run_once() == 0
    assert not gateway.sent
    state = json.loads((tmp_path / "state" / "VE-0001.json").read_text(encoding="utf-8"))
    assert state["security"]["spoofed_sender"] == 1
    assert state["security"]["parse_error"] == 1


def test_event_and_idempotency_duplicates_replay_nothing(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.selections = [
        SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"]),
        SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"]),
    ]
    engine.plans = [plan(), plan()]
    protocol = message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload("x"))
    gateway.events = [event(protocol, event_id="$same"), event(protocol, event_id="$different", ts=2)]

    assert subject.run_once() == 1
    assert len(gateway.sent) == 2


def test_restart_uses_atomic_state_without_secret_and_send_failure_does_not_advance(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    engine.plans = [plan()]
    protocol = message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload("x"))
    gateway.events = [event(protocol)]
    gateway.fail_sends = True
    assert subject.run_once() == 0
    assert json.loads((tmp_path / "state" / "VE-0001.json").read_text(encoding="utf-8"))["pending_outbox"]

    gateway.fail_sends = False
    assert subject.run_once() == 1
    saved = (tmp_path / "state" / "VE-0001.json").read_text(encoding="utf-8")
    assert "never-persist" not in saved
    assert '"cursor":"cursor-1"' in saved
    restarted, _, _, _ = runtime(tmp_path)
    assert restarted._state["cursor"] == "cursor-1"


def test_evidence_is_bounded_and_completion_without_tool_is_failed_once(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [WorkerDecision(action="complete", result="没有工具证据"), WorkerDecision(action="complete", result="仍然没有")]
    gateway.events = [event(message(type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1", payload=assign_payload()), sender=MXIDS["VE-0001"])]

    subject.run_once()

    sent = sent_protocols(gateway)
    assert sent[-1].type == "task.failed"
    assert sent[-1].payload["error_code"] == "completion_without_tool_evidence"
    assert len(engine.worker_contexts) == 2


def test_worker_evidence_keeps_a_bounded_safe_tail(tmp_path):
    subject, _, _, _ = runtime(tmp_path, role="hr")
    evidence = []
    for index in range(20):
        evidence = subject._append_evidence(evidence, {"kind": "tool.result", "value": {"audit_ids": [str(index)], "secret": "hidden"}})

    assert len(evidence) <= 8
    assert len(json.dumps(evidence, ensure_ascii=False)) <= 4096
    assert all("secret" not in item["value"] for item in evidence)


def test_cli_config_is_fail_closed_without_real_network(tmp_path):
    from scripts.run_agentteams_agent import load_runtime_config, resolve_role_api_key

    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"matrix_url": "http://x", "room_id": "!room", "agents": {"manager": {"agent_id": "VE-0001", "mxid": "@manager:x", "token": "token"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="deepseek_key_missing"):
        load_runtime_config("manager", path, env={})
    with pytest.raises(ValueError, match="invalid_runtime_config"):
        load_runtime_config("hr", path, env={"DEEPSEEK_API_KEY_HR": "x"})
    with pytest.raises(ValueError, match="deepseek_key_missing"):
        resolve_role_api_key("hr", {"DEEPSEEK_API_KEY": "legacy-shared"})
    assert resolve_role_api_key("audit", {"DEEPSEEK_API_KEY_AUDIT": "audit-only"}) == "audit-only"


def test_cli_accepts_a_role_specific_token_environment_reference(tmp_path):
    from scripts.run_agentteams_agent import load_runtime_config

    document = _strict_onboarding_runtime_document()
    document["agents"]["manager"].pop("token")
    document["agents"]["manager"]["token_env"] = "MANAGER_MATRIX_TOKEN"
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    config, _ = load_runtime_config("manager", path, env={"DEEPSEEK_API_KEY_MANAGER": "key", "MANAGER_MATRIX_TOKEN": "manager-only", "DWP_AGENTTEAMS_RUNTIME_CONFIG": str(path)})

    assert config.matrix_token == "manager-only"
    assert config.matrix_mxid == "@manager:test"


def test_role_is_behavior_only_and_two_team_identity_sets_are_valid(tmp_path):
    for agent_id, mxid in (("MGR-42", "@matrix-manager:local"), ("COORD-99", "@matrix-coordinator:local")):
        config = AgentRuntimeConfig(
            agent_id=agent_id, role="manager", matrix_mxid=mxid, matrix_token="manager-token",
            room_id="!room", trusted_sender_mxids={"platform": "@platform:local", agent_id: mxid},
            allowed_skill_ids={"agentteams-onboarding-readiness"}, state_dir=tmp_path / agent_id,
        )
        assert config.agent_id == agent_id


def test_cli_rejects_non_contract_team_identity_for_a_manager(tmp_path):
    from scripts.run_agentteams_agent import load_runtime_config

    agents = {
        "manager": {"agent_id": "COORD-99", "mxid": "@coord:matrix", "token": "manager-only", "allowed_skill_ids": ["agentteams-onboarding-readiness"], "safe_tool_catalog": []},
        "hr": {"agent_id": "HR-27", "mxid": "@hr:matrix", "token": "hr-only", "allowed_skill_ids": ["hr-onboarding-assistant"], "safe_tool_catalog": []},
        "it": {"agent_id": "TECH-12", "mxid": "@it:matrix", "token": "it-only", "allowed_skill_ids": ["it-service-assistant"], "safe_tool_catalog": []},
        "audit": {"agent_id": "AUD-88", "mxid": "@audit:matrix", "token": "audit-only", "allowed_skill_ids": ["audit-evidence-review"], "safe_tool_catalog": []},
    }
    path = tmp_path / "custom-runtime.json"
    path.write_text(json.dumps({"matrix_url": "http://matrix", "room_id": "!room", "team_id": "TEAM-CUSTOM", "platform": {"mxid": "@platform:matrix", "token": "platform-only"}, "team_catalog": {}, "agents": agents}), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid_runtime_config"):
        load_runtime_config("manager", path, env={"DEEPSEEK_API_KEY_MANAGER": "key", "DWP_AGENTTEAMS_RUNTIME_CONFIG": str(path)})


def test_state_redactor_removes_sensitive_keys_values_and_stack_text(tmp_path):
    subject, _, _, _ = runtime(tmp_path, role="hr")
    subject._state["tasks"] = {
        "task": {"previous_results": [{"authorization": "Bearer secret-value", "endpoint": "https://internal.example/a", "nested": {"api_key": "api-secret"}}], "error": "Traceback (most recent call last): File C:/private.py"}
    }
    subject._save_state()

    state = (tmp_path / "state" / "VE-0002.json").read_text(encoding="utf-8")
    for forbidden in ("secret-value", "internal.example", "api-secret", "Traceback", "authorization", "endpoint", "api_key"):
        assert forbidden not in state


def test_manager_requires_fresh_catalog_for_submit_and_follow_up(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload={"instruction": "x"}), event_id="$missing")]

    subject.run_once()

    assert sent_protocols(gateway)[0].type == "task.failed"
    assert not engine.plan_contexts

    subject, gateway, engine, _ = runtime(tmp_path / "fresh")
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    engine.plans = [plan()]
    submit = message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload(version=5))
    stale_summary = message(type="task.summary", sender="platform", receiver="VE-0001", payload={"request": "task.summary", "team_capability_catalog": manager_payload()["team_capability_catalog"], "team_catalog_version": 5}, key="stale")
    gateway.events = [event(submit, event_id="$submit", ts=1), event(stale_summary, event_id="$stale", ts=2)]

    subject.run_once()

    assert sent_protocols(gateway)[-1].type == "task.failed"


def test_worker_requires_full_parent_chain_and_rejects_self_or_ancestor_delegate(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    missing_chain = assign_payload()
    missing_chain.pop("parent_chain_agent_ids")
    gateway.events = [event(message(type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1", payload=missing_chain), sender=MXIDS["VE-0001"])]
    subject.run_once()
    assert sent_protocols(gateway)[0].type == "task.failed"

    subject, gateway, engine, _ = runtime(tmp_path / "cycle", role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [WorkerDecision(action="delegate", subtask=PlannedSubtask(client_task_key="loop", title="loop", goal="loop", required_capability="hr.onboarding.read", assignee_agent_id="VE-0002", reason="bad"))]
    cyclic = assign_payload()
    cyclic["parent_chain_agent_ids"] = ["VE-0001", "VE-0002", "VE-0005"]
    gateway.events = [event(message(type="task.assign", sender="VE-0005", receiver="VE-0002", parent_task_id="parent", payload=cyclic), sender=MXIDS["VE-0005"])]
    subject.run_once()
    assert sent_protocols(gateway)[-1].type == "task.failed"

    subject, gateway, engine, _ = runtime(tmp_path / "long-cycle", role="it")
    engine.selections = [SkillSelection(selected_skill_ids=["it-service-assistant"])]
    engine.work = [WorkerDecision(action="delegate", subtask=PlannedSubtask(client_task_key="loop-hr", title="loop", goal="loop", required_capability="hr.onboarding.read", assignee_agent_id="VE-0002", reason="bad"))]
    long_cycle = assign_payload()
    long_cycle["parent_chain_agent_ids"] = ["VE-0001", "VE-0002", "VE-0005"]
    gateway.events = [event(message(type="task.assign", sender="VE-0005", receiver="VE-0003", parent_task_id="audit", payload=long_cycle), sender=MXIDS["VE-0005"])]
    subject.run_once()
    assert sent_protocols(gateway)[-1].type == "task.failed"


def test_valid_protocol_lines_survive_bad_lines_in_same_matrix_event(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    engine.plans = [plan()]
    protocol = message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload())
    body = "ordinary text\nDWP_EVENT {not-json}\n" + render_protocol_line(protocol) + "\nDWP_EVENT {still-bad}"
    gateway.events = [MatrixEvent(event_id="$mixed", room_id="!team:local", sender=MXIDS["platform"], body=body, ts=1)]

    assert subject.run_once() == 1
    assert [item.type for item in sent_protocols(gateway)] == ["agent.message", "task.delegate"]
    assert subject._state["security"]["parse_error"] == 2


def test_partial_manager_send_retries_same_protocol_ids_without_cursor_advance(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    # If recovery reruns the model, these deliberately different second answers
    # would change the reply.  A durable pending outbox must never consume them.
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"]), SkillSelection(selected_skill_ids=["audit-evidence-review"])]
    engine.plans = [plan(), plan("summarize")]
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload()))]
    gateway.fail_on_send = 2

    assert subject.run_once() == 0
    first_attempt = sent_protocols(gateway)
    assert [item.type for item in first_attempt] == ["agent.message"]
    first_body = gateway.sent[0][2]
    assert subject._state["cursor"] is None

    gateway.fail_on_send = None
    assert subject.run_once() == 1
    resent = sent_protocols(gateway)[1:]
    assert resent[0].message_id == first_attempt[0].message_id
    assert resent[0].idempotency_key == first_attempt[0].idempotency_key
    assert gateway.sent[1][2] == first_body
    assert [item.type for item in resent] == ["agent.message", "task.delegate"]
    assert subject._state["cursor"] == "cursor-1"
    assert "key-1" in subject._state["processed_idempotency_keys"]
    assert len(engine.selections) == 1 and len(engine.plans) == 1


def test_pending_exception_retries_preserve_canonical_pending_without_replanning(
    tmp_path, monkeypatch,
):
    """Repeated legal pending-send exceptions remain observable without changing the envelope."""
    class Clock:
        tick = 0

        @classmethod
        def now(cls, _timezone):
            cls.tick += 1
            return datetime(2026, 9, 7, tzinfo=timezone.utc) + timedelta(seconds=cls.tick)

    subject, gateway, engine, _ = runtime(tmp_path)
    monkeypatch.setattr(runtime_module, "datetime", Clock)
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    engine.plans = [plan()]
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload()))]

    def raise_send_exception(*_args):
        gateway.send_attempts += 1
        raise RuntimeError("transport https://invalid.example/path token=placeholder")

    gateway.send_protocol_message = raise_send_exception

    assert subject.run_once() == 0
    canonical_pending = deepcopy(subject._state["pending_outbox"])
    assert subject._state["cursor"] is None
    assert subject.run_once() == 0
    assert subject.run_once() == 0

    assert gateway.send_attempts == 3
    assert subject._state["pending_outbox"] == canonical_pending
    assert subject._state["cursor"] is None
    assert subject._state["heartbeat_at"] == "2026-09-07T00:00:03+00:00"
    assert subject._state["security"] == {"pending_send_exception": 3}
    assert engine.selections == [] and engine.plans == []
    persisted = (tmp_path / "state" / "VE-0001.json").read_text(encoding="utf-8")
    assert "invalid.example" not in persisted
    assert "placeholder" not in persisted


def test_pending_empty_event_id_is_counted_without_recording_send_details(tmp_path):
    """A transport that returns no event ID is distinct from a raised send exception."""
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    engine.plans = [plan()]
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload()))]

    def return_empty_event_id(*_args):
        gateway.send_attempts += 1
        return ""

    gateway.send_protocol_message = return_empty_event_id

    assert subject.run_once() == 0
    assert subject.run_once() == 0

    assert gateway.send_attempts == 2
    assert subject._state["security"] == {"pending_empty_event_id": 2}
    persisted = (tmp_path / "state" / "VE-0001.json").read_text(encoding="utf-8")
    assert "exception" not in persisted.replace("pending_empty_event_id", "")
    assert "invalid.example" not in persisted


def test_pending_send_telemetry_never_evicts_a_near_limit_canonical_pending(tmp_path):
    """A counter may be dropped, but a valid replay envelope must remain durable."""
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    engine.plans = [plan()]
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload()))]
    gateway.fail_sends = True

    assert subject.run_once() == 0
    pending = subject._state["pending_outbox"]
    assert pending is not None
    subject._state["security"] = {}
    pending["trial_state"]["tasks"] = {"padding": ""}
    base_size = subject._serialized_size(subject._pending_recovery_state(pending, subject._state))
    pending["trial_state"]["tasks"]["padding"] = "x" * (16_383 - base_size)
    assert subject._serialized_size(subject._pending_recovery_state(pending, subject._state)) == 16_383
    canonical_pending = deepcopy(pending)

    assert subject.run_once() == 0

    assert subject._state["pending_outbox"] == canonical_pending
    persisted = json.loads((tmp_path / "state" / "VE-0001.json").read_text(encoding="utf-8"))
    assert persisted["pending_outbox"] == canonical_pending
    assert subject._state["cursor"] is None
    assert engine.selections == [] and engine.plans == []


def test_pending_send_counters_survive_successful_replay_and_restart(tmp_path):
    """Pending send telemetry remains durable after an old replay trial becomes current."""
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    engine.plans = [plan()]
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload()))]
    gateway.fail_sends = True

    assert subject.run_once() == 0
    assert subject.run_once() == 0
    assert subject.run_once() == 0
    assert subject._state["security"] == {"pending_send_exception": 3}

    gateway.fail_sends = False
    assert subject.run_once() == 1
    assert subject._state["security"] == {"pending_send_exception": 3}

    restarted = AgentRuntime(subject.config, FakeGateway(), FakeEngine(), pause=lambda _: None)
    assert restarted._state["security"] == {"pending_send_exception": 3}


def test_near_limit_state_preserves_restartable_pending_worker_assignment(tmp_path):
    seeded, _, _, _ = runtime(tmp_path, role="hr")
    seeded._state["tasks"] = {
        f"old-{index:02d}": {
            "kind": "manager",
            "goal": f"old-{index:02d}-" + ("x" * 500),
            "selected_skill_ids": ["agentteams-onboarding-readiness"],
            "team_catalog_version": 1,
        }
        for index in range(32)
    }
    seeded._state["processed_event_ids"] = [
        f"$historic-{index:02d}-" + ("y" * 130) for index in range(20)
    ]
    seeded._save_state()
    state_path = tmp_path / "state" / "VE-0002.json"
    assert 15_000 < state_path.stat().st_size <= 16_384

    gateway = FakeGateway()
    engine = FakeEngine()
    subject = AgentRuntime(seeded.config, gateway, engine, pause=lambda _: None)
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [WorkerDecision(
        action="tool", tool_name="query_onboarding_employee",
        tool_arguments={"employee_ref": "新员工A"},
    )]
    assignment = message(
        type="task.assign", sender="VE-0001", receiver="VE-0002", task_id="current",
        parent_task_id="root-1", key="current-assignment", payload=assign_payload(),
    )
    gateway.events = [event(assignment, event_id="$current", sender=MXIDS["VE-0001"])]
    gateway.fail_on_send = 1

    assert subject.run_once() == 0
    assert gateway.send_attempts == 1
    persisted_pending = json.loads(state_path.read_text(encoding="utf-8"))["pending_outbox"]
    assert persisted_pending is not None
    assert "current" in persisted_pending["trial_state"]["tasks"]
    assert state_path.stat().st_size <= 16_384

    replay_gateway = FakeGateway()
    replay_gateway.events = [event(assignment, event_id="$current", sender=MXIDS["VE-0001"])]
    replay_engine = FakeEngine()
    restarted = AgentRuntime(seeded.config, replay_gateway, replay_engine, pause=lambda _: None)

    assert restarted.run_once() == 1
    assert [item.type for item in sent_protocols(replay_gateway)] == [
        "task.accepted", "task.progress", "agent.message", "tool.request",
    ]
    assert restarted._state["cursor"] == "cursor-1"
    assert restarted._state["tasks"]["current"]["task"]["goal"] == assign_payload()["instruction"]
    assert replay_engine.selections == [] and replay_engine.work == []
    assert state_path.stat().st_size <= 16_384


def test_pending_fails_closed_instead_of_pruning_tracking_to_fit(tmp_path):
    seeded, _, _, _ = runtime(tmp_path, role="hr")
    event_ids = [f"$historic-event-{index:02d}-" + ("e" * 480) for index in range(15)]
    idempotency_keys = [f"historic-key-{index:02d}-" + ("i" * 480) for index in range(15)]
    seeded._state["processed_event_ids"] = event_ids
    seeded._state["processed_idempotency_keys"] = idempotency_keys
    seeded._save_state()
    state_path = tmp_path / "state" / "VE-0002.json"
    assert 14_000 < state_path.stat().st_size <= 16_384

    gateway = FakeGateway()
    engine = FakeEngine()
    subject = AgentRuntime(seeded.config, gateway, engine, pause=lambda _: None)
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [WorkerDecision(
        action="tool", tool_name="query_onboarding_employee",
        tool_arguments={"employee_ref": "新员工A"},
    )]
    assignment = message(
        type="task.assign", sender="VE-0001", receiver="VE-0002", task_id="current",
        parent_task_id="root-1", key="current-assignment", payload=assign_payload(),
    )
    gateway.events = [event(assignment, event_id="$current", sender=MXIDS["VE-0001"])]

    assert subject.run_once() == 0
    assert gateway.send_attempts == 0
    assert subject._state["security"]["outbound_rejected"] == 1
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["pending_outbox"] is None
    assert persisted["processed_event_ids"] == event_ids
    assert persisted["processed_idempotency_keys"] == idempotency_keys
    assert state_path.stat().st_size <= 16_384


def test_save_state_keeps_runtime_memory_identical_to_bounded_durable_state(tmp_path):
    """A long-lived Matrix process must not retain state that its restart would discard."""
    subject, _, _, _ = runtime(tmp_path, role="hr")
    subject._state["processed_event_ids"] = [
        f"$event-{index:03d}-" + ("e" * 180) for index in range(256)
    ]
    subject._state["processed_idempotency_keys"] = [
        f"key-{index:03d}-" + ("i" * 180) for index in range(256)
    ]

    subject._save_state()

    persisted = json.loads(
        (tmp_path / "state" / "VE-0002.json").read_text(encoding="utf-8")
    )
    assert subject._state == persisted
    assert len(subject._state["processed_event_ids"]) < 256
    assert len(subject._state["processed_idempotency_keys"]) < 256
    assert subject._serialized_size(subject._state) <= 16_384


def test_worker_rejects_stale_delegate_candidate_after_platform_catalog_refresh(tmp_path):
    """A later platform summary revokes the old assignment snapshot before delegation."""
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [
        WorkerDecision(action="ask", recipient_agent_id="VE-0001", message="need manager context"),
        WorkerDecision(action="delegate", subtask=PlannedSubtask(
            client_task_key="audit", title="audit", goal="review", required_capability="audit.evidence.read",
            assignee_agent_id="VE-0005", reason="old snapshot candidate",
        )),
    ]
    assignment = message(type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1", payload=assign_payload())
    refreshed_catalog = [item for item in manager_payload()["team_capability_catalog"] if item["agent_id"] != "VE-0005"]
    summary = message(
        type="task.summary", sender="platform", receiver="VE-0002", parent_task_id="root-1", key="fresh-summary",
        payload={"request": "task.summary", "team_capability_catalog": refreshed_catalog, "team_catalog_version": 2},
    )
    gateway.events = [event(assignment, event_id="$assign", ts=1), event(summary, event_id="$summary", ts=2)]

    assert subject.run_once() == 2
    assert [item.type for item in sent_protocols(gateway)] == ["task.accepted", "task.progress", "agent.message", "agent.message", "task.failed"]
    assert subject._state["tasks"]["task-1"]["team_catalog_version"] == 2
    assert {item["agent_id"] for item in engine.worker_contexts[0].current_team_capability_catalog} == {
        "VE-0002", "VE-0003", "VE-0005",
    }
    assert {item["agent_id"] for item in engine.worker_contexts[1].current_team_capability_catalog} == {
        "VE-0002", "VE-0003",
    }


def test_run_forever_waits_after_empty_poll_and_default_visible_pause_is_disabled(tmp_path):
    subject, gateway, _, _ = runtime(tmp_path)
    observed_waits = []

    class StopAfterOnePoll:
        checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks > 1

        def wait(self, seconds):
            observed_waits.append(seconds)

    subject.run_forever(StopAfterOnePoll())
    assert observed_waits == [1.0]
    assert subject.config.pause_seconds == 0.0


def test_empty_poll_persists_a_safe_utc_heartbeat(tmp_path):
    subject, _, _, _ = runtime(tmp_path, role="hr")

    assert subject.run_once() == 0

    persisted = json.loads((tmp_path / "state" / "VE-0002.json").read_text(encoding="utf-8"))
    assert persisted["heartbeat_at"].endswith("+00:00")
    assert "never-persist" not in json.dumps(persisted)


def test_runtime_config_rejects_agent_id_that_can_escape_the_state_directory(tmp_path):
    with pytest.raises(ValueError, match="invalid_runtime_config"):
        AgentRuntimeConfig(
            agent_id="../outside", role="manager", matrix_mxid="@manager:local",
            matrix_token="token", room_id="!team:local",
            trusted_sender_mxids={"platform": "@platform:local", "../outside": "@manager:local"},
            allowed_skill_ids={"agentteams-onboarding-readiness"}, state_dir=tmp_path / "state",
        )


@pytest.mark.parametrize(
    ("role", "agent_id", "skill_id"),
    [
        ("hr_material", "VE-HR-MATERIAL", "hr-material-specialist"),
        ("hr_training", "VE-HR-TRAINING", "hr-training-specialist"),
        ("it_account", "VE-IT-ACCOUNT", "it-account-specialist"),
        ("it_device", "VE-IT-DEVICE", "it-device-specialist"),
    ],
)
def test_runtime_config_assigns_each_specialist_its_own_professional_skill(
    tmp_path, role, agent_id, skill_id,
):
    mxid = f"@{role}:local"

    configured = AgentRuntimeConfig(
        agent_id=agent_id,
        role=role,
        matrix_mxid=mxid,
        matrix_token="specialist-token",
        room_id="!team:local",
        trusted_sender_mxids={"platform": "@platform:local", agent_id: mxid},
        allowed_skill_ids={skill_id},
        state_dir=tmp_path / role,
    )

    assert configured.professional_skill_ids == {skill_id}


def test_pending_replay_resumes_same_batch_without_skipping_later_events(tmp_path):
    """A failed B response may replay, but C must still be read from the old cursor once."""
    subject, gateway, engine, _ = runtime(tmp_path)
    subject._state["processed_event_ids"] = [f"$historic-{index}" for index in range(20)]
    subject._state["processed_idempotency_keys"] = [f"historic-{index}" for index in range(20)]
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])] * 3
    engine.plans = [plan("summarize")] * 3
    batch = [
        event(message(type="task.submit", sender="platform", receiver="VE-0001", task_id=task_id, key=task_id, payload=manager_payload()), event_id=f"${task_id}", ts=index)
        for index, task_id in enumerate(("A", "B", "C"), start=1)
    ]
    gateway.events = batch
    gateway.fail_on_send = 4  # A's two envelopes succeed; B's delegate fails.

    assert subject.run_once() == 1
    assert subject._state["cursor"] is None
    assert subject._state["pending_outbox"]["source"]["idempotency_key"] == "B"
    pending_trial = subject._state["pending_outbox"]["trial_state"]
    assert "$A" in pending_trial["processed_event_ids"]
    assert "A" in pending_trial["processed_idempotency_keys"]

    restarted_gateway = FakeGateway()
    restarted_gateway.events = batch
    restarted_engine = FakeEngine()
    restarted_engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    restarted_engine.plans = [plan("summarize")]
    restarted = AgentRuntime(subject.config, restarted_gateway, restarted_engine, pause=lambda _: None)

    assert restarted.run_once() == 2
    replayed = sent_protocols(restarted_gateway)
    assert [item.task_id for item in replayed] == ["B", "B", "C", "C"]
    assert restarted._state["cursor"] == "cursor-1"
    assert restarted_engine.selections == [] and restarted_engine.plans == []


def test_pending_replay_resumes_second_protocol_line_in_one_matrix_event(tmp_path):
    """Replaying line one must not mark the enclosing Matrix event completed."""
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])] * 2
    engine.plans = [plan("summarize")] * 2
    first = message(type="task.submit", sender="platform", receiver="VE-0001", task_id="one", key="one", payload=manager_payload())
    second = message(type="task.submit", sender="platform", receiver="VE-0001", task_id="two", key="two", payload=manager_payload())
    combined = MatrixEvent(
        event_id="$both", room_id="!team:local", sender=MXIDS["platform"],
        body="bad text\n" + render_protocol_line(first) + "\nDWP_EVENT {broken}\n" + render_protocol_line(second), ts=1,
    )
    gateway.events = [combined]
    gateway.fail_on_send = 2

    assert subject.run_once() == 0
    assert subject._state["cursor"] is None

    restarted_gateway = FakeGateway()
    restarted_gateway.events = [combined]
    restarted_engine = FakeEngine()
    restarted_engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    restarted_engine.plans = [plan("summarize")]
    restarted = AgentRuntime(subject.config, restarted_gateway, restarted_engine, pause=lambda _: None)

    assert restarted.run_once() == 2
    assert [item.task_id for item in sent_protocols(restarted_gateway)] == ["one", "one", "two", "two"]
    assert restarted._state["security"]["parse_error"] == 1


def test_completed_protocol_progress_is_durable_before_its_matrix_event_finishes(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    engine.plans = [plan("summarize")]
    protocol = message(type="task.submit", sender="platform", receiver="VE-0001", task_id="first", key="first", payload=manager_payload())
    matrix_event = event(protocol, event_id="$combined")
    trial = json.loads(json.dumps(subject._state))
    subject._active_pending = {"event_id": "$combined", "trial": trial, "source": None}

    assert subject._process_protocol_message(protocol, matrix_event, trial) == 1
    saved = json.loads((tmp_path / "state" / "VE-0001.json").read_text(encoding="utf-8"))
    assert "first" in saved["processed_idempotency_keys"]
    assert "$combined" not in saved["processed_event_ids"]
    assert saved["cursor"] is None


def test_corrupt_pending_is_dropped_without_sending_or_advancing_cursor(tmp_path):
    subject, gateway, _, _ = runtime(tmp_path)
    subject._state["pending_outbox"] = {
        "version": 1, "event_id": "$bad", "source_idempotency_key": "bad", "next_cursor": "cursor-1",
        "trial_state": {"cursor": None},
        "messages": [message(type="task.summary", sender="VE-0002", receiver="platform", payload={"summary": "bad"}).model_dump(mode="json")],
    }

    assert subject.run_once() == 0
    assert gateway.sent == []
    assert subject._state["cursor"] is None
    assert subject._state["pending_outbox"] is None
    assert subject._state["security"]["pending_corrupt"] == 1


def test_canonical_pending_envelope_redacts_model_text_before_first_or_replay_send(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    secret = "Bearer very-secret-token api_key: exposed https://secret.example/path"
    engine.work = [WorkerDecision(action="ask", recipient_agent_id="VE-0001", message=secret)]
    gateway.events = [event(message(type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1", payload=assign_payload()))]
    gateway.fail_on_send = 2

    assert subject.run_once() == 0
    first_bodies = [line for _, _, line in gateway.sent]
    assert all("very-secret-token" not in line and "secret.example" not in line for line in first_bodies)

    gateway.fail_on_send = None
    assert subject.run_once() == 1
    all_bodies = [line for _, _, line in gateway.sent]
    assert all("very-secret-token" not in line and "secret.example" not in line for line in all_bodies)


def test_secret_canonical_envelope_is_byte_identical_on_first_send_and_replay(tmp_path):
    subject, gateway, _, _ = runtime(tmp_path)
    source = message(type="task.submit", sender="platform", receiver="VE-0001", key="source", payload=manager_payload())
    source_event = event(source, event_id="$source")
    trial = json.loads(json.dumps(subject._state))
    subject._active_pending = {"event_id": "$source", "trial": trial, "source": subject._pending_source(source_event, source)}
    secret = "Bearer very-secret-token api_key=exposed https://secret.example/path"
    replies = [
        subject._reply(source, "agent.message", "platform", {"kind": "peer.ask", "text": secret}, 0),
        subject._reply(source, "task.failed", "platform", {"error_code": "decision_failed"}, 1),
    ]
    gateway.fail_on_send = 2

    assert subject._send_responses(replies) is False
    first_body = gateway.sent[0][2]
    assert "very-secret-token" not in first_body and "secret.example" not in first_body

    replay_gateway = FakeGateway()
    replay = AgentRuntime(subject.config, replay_gateway, FakeEngine(), pause=lambda _: None)
    assert replay.run_once() == 1
    assert replay_gateway.sent[0][2] == first_body
    assert all("very-secret-token" not in line and "secret.example" not in line for _, _, line in replay_gateway.sent)
