from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.agentteams_decisions import (
    AgentDecisionError,
    FinalSummary,
    ManagerDecision,
    PlannedSubtask,
    SkillSelection,
    WorkerDecision,
)
from app.services.agentteams_gateway import MatrixEvent, MatrixSyncBatch
from app.services.agentteams_protocol import AgentProtocolMessage, parse_protocol_lines, render_protocol_line
from app.services.agentteams_agent_runtime import AgentRuntime, AgentRuntimeConfig
from app.services.llm import LLMUnavailableError


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
        self.plan_contexts = []
        self.worker_contexts = []

    def select_skills(self, _context):
        return self.selections.pop(0)

    def plan(self, context):
        self.plan_contexts.append(context)
        return self.plans.pop(0)

    def decide_work(self, context):
        self.worker_contexts.append(context)
        return self.work.pop(0)


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
        return ManagerDecision(selected_skill_ids=["agentteams-onboarding-readiness"], action="summarize", summary=FinalSummary(summary="可按期入职", completed_items=["资料"], risks=[], unknowns=[], next_actions=[]))
    return ManagerDecision(selected_skill_ids=["agentteams-onboarding-readiness"], action="fail", failure_reason="证据不足")


def assign_payload():
    return {
        "title": "HR 核查", "instruction": "核验新员工A的完整HR入职材料，并报告缺口。",
        "client_task_key": "hr", "dependency_results": [], "parent_chain_agent_ids": ["VE-0001"],
        "team_capability_catalog": manager_payload()["team_capability_catalog"], "team_catalog_version": 1,
    }


def manager_payload(instruction="评估新员工A", version=1):
    return {
        "instruction": instruction,
        "team_capability_catalog": [{"agent_id": key, **value} for key, value in TEAM.items()],
        "team_catalog_version": version,
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
    assert pauses == [3.0, 3.0]


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
    assert engine.plan_contexts[-1].child_results == [{"result": "ok"}]
    assert engine.plan_contexts[-1].existing_child_task_keys == ["hr-check", "it-check"]


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
    tool_result = message(type="tool.result", sender="platform", receiver="VE-0002", parent_task_id="root-1", payload={"ok": True, "audit_ids": [101], "data": {"source": "demo", "key": "drop", "note": "api_key: exposed password=hidden"}, "team_capability_catalog": manager_payload()["team_capability_catalog"], "team_catalog_version": 2}, key="tool")
    gateway.events = [event(assignment, event_id="$assign", sender=MXIDS["VE-0001"], ts=1), event(tool_result, event_id="$tool", ts=2)]

    subject.run_once()

    sent = sent_protocols(gateway)
    assert [item.type for item in sent] == ["task.accepted", "task.progress", "agent.message", "tool.request", "task.result"]
    assert sent[3].receiver == "platform"
    assert sent[3].payload["plugin_id"] == "agentteams-onboarding-employee"
    assert sent[-1].payload["audit_ids"] == [101]
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
    agents = {}
    for role, agent_id, mxid, skill in (("manager", "VE-0001", MXIDS["VE-0001"], "agentteams-onboarding-readiness"), ("hr", "VE-0002", MXIDS["VE-0002"], "hr-onboarding-assistant"), ("it", "VE-0003", MXIDS["VE-0003"], "it-service-assistant"), ("audit", "VE-0005", MXIDS["VE-0005"], "audit-evidence-review")):
        agents[role] = {"agent_id": agent_id, "mxid": mxid, "token": role + "-token", "allowed_skill_ids": [skill], "safe_tool_catalog": ([{"name": "agentteams.hr.lookup_onboarding", "plugin_id": "hr-employee-mcp", "action": "read", "required_capability": "hr.onboarding.read"}] if role == "hr" else [])}
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"matrix_url": "https://matrix.test", "room_id": "!team:local", "team_id": "TEAM-X", "platform": {"mxid": MXIDS["platform"], "token": "platform-token"}, "agents": agents, "team_catalog": TEAM}), encoding="utf-8")
    config, _ = load_runtime_config("hr", path, env={"DEEPSEEK_API_KEY_HR": "x", "DWP_AGENTTEAMS_RUNTIME_CONFIG": str(path)})
    gateway, engine = FakeGateway(), FakeEngine(); engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [WorkerDecision(action="tool", tool_name="agentteams.hr.lookup_onboarding", tool_arguments={})]
    gateway.events = [event(message(type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1", payload=assign_payload()), sender=MXIDS["VE-0001"])]
    AgentRuntime(config, gateway, engine, pause=lambda _: None).run_once()
    request = [item for item in sent_protocols(gateway) if item.type == "tool.request"][-1]
    assert request.payload["tool_name"] == "agentteams.hr.lookup_onboarding"
    assert request.payload["required_capability"] == "hr.onboarding.read"
    gateway, engine = FakeGateway(), FakeEngine(); engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [WorkerDecision(action="tool", tool_name="not-listed", tool_arguments={})]
    gateway.events = [event(message(type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1", payload=assign_payload()), sender=MXIDS["VE-0001"])]
    AgentRuntime(config, gateway, engine, pause=lambda _: None).run_once()
    assert all(item.type != "tool.request" for item in sent_protocols(gateway))


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

    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({
        "matrix_url": "http://matrix", "room_id": "!room", "team_id": "TEAM-ONBOARD", "platform": {"mxid": "@platform:matrix", "token": "platform-only"}, "team_catalog": TEAM,
        "agents": {
                "manager": {"agent_id": "VE-0001", "mxid": "@manager:matrix", "token_env": "MANAGER_MATRIX_TOKEN", "allowed_skill_ids": ["agentteams-onboarding-readiness"], "safe_tool_catalog": []},
                "hr": {"agent_id": "VE-0002", "mxid": "@hr:matrix", "token": "hr-only", "allowed_skill_ids": ["hr-onboarding-assistant"], "safe_tool_catalog": []},
                "it": {"agent_id": "VE-0003", "mxid": "@it:matrix", "token": "it-only", "allowed_skill_ids": ["it-service-assistant"], "safe_tool_catalog": []},
                "audit": {"agent_id": "VE-0005", "mxid": "@audit:matrix", "token": "audit-only", "allowed_skill_ids": ["audit-evidence-review"], "safe_tool_catalog": []},
        },
    }), encoding="utf-8")

    config, _ = load_runtime_config("manager", path, env={"DEEPSEEK_API_KEY_MANAGER": "key", "MANAGER_MATRIX_TOKEN": "manager-only", "DWP_AGENTTEAMS_RUNTIME_CONFIG": str(path)})

    assert config.matrix_token == "manager-only"
    assert config.matrix_mxid == "@manager:matrix"


def test_role_is_behavior_only_and_two_team_identity_sets_are_valid(tmp_path):
    for agent_id, mxid in (("MGR-42", "@matrix-manager:local"), ("COORD-99", "@matrix-coordinator:local")):
        config = AgentRuntimeConfig(
            agent_id=agent_id, role="manager", matrix_mxid=mxid, matrix_token="manager-token",
            room_id="!room", trusted_sender_mxids={"platform": "@platform:local", agent_id: mxid},
            allowed_skill_ids={"agentteams-onboarding-readiness"}, state_dir=tmp_path / agent_id,
        )
        assert config.agent_id == agent_id


def test_cli_accepts_non_demo_team_identity_for_a_manager(tmp_path):
    from scripts.run_agentteams_agent import load_runtime_config

    agents = {
        "manager": {"agent_id": "COORD-99", "mxid": "@coord:matrix", "token": "manager-only", "allowed_skill_ids": ["agentteams-onboarding-readiness"], "safe_tool_catalog": []},
        "hr": {"agent_id": "HR-27", "mxid": "@hr:matrix", "token": "hr-only", "allowed_skill_ids": ["hr-onboarding-assistant"], "safe_tool_catalog": []},
        "it": {"agent_id": "TECH-12", "mxid": "@it:matrix", "token": "it-only", "allowed_skill_ids": ["it-service-assistant"], "safe_tool_catalog": []},
        "audit": {"agent_id": "AUD-88", "mxid": "@audit:matrix", "token": "audit-only", "allowed_skill_ids": ["audit-evidence-review"], "safe_tool_catalog": []},
    }
    path = tmp_path / "custom-runtime.json"
    path.write_text(json.dumps({"matrix_url": "http://matrix", "room_id": "!room", "team_id": "TEAM-CUSTOM", "platform": {"mxid": "@platform:matrix", "token": "platform-only"}, "team_catalog": {}, "agents": agents}), encoding="utf-8")

    config, _ = load_runtime_config("manager", path, env={"DEEPSEEK_API_KEY_MANAGER": "key", "DWP_AGENTTEAMS_RUNTIME_CONFIG": str(path)})

    assert config.agent_id == "COORD-99"


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


def test_run_forever_waits_after_empty_poll_and_default_pause_is_three_seconds(tmp_path):
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
    assert subject.config.pause_seconds == 3.0


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


def test_pending_replay_resumes_same_batch_without_skipping_later_events(tmp_path):
    """A failed B response may replay, but C must still be read from the old cursor once."""
    subject, gateway, engine, _ = runtime(tmp_path)
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
