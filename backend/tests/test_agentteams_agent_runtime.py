from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.agentteams_decisions import (
    FinalSummary,
    ManagerDecision,
    PlannedSubtask,
    SkillSelection,
    WorkerDecision,
)
from app.services.agentteams_gateway import MatrixEvent, MatrixSyncBatch
from app.services.agentteams_protocol import AgentProtocolMessage, parse_protocol_lines, render_protocol_line
from app.services.agentteams_agent_runtime import AgentRuntime, AgentRuntimeConfig


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


def test_manager_summary_can_follow_up_or_finish(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path)
    engine.plans = [plan(), plan("summarize")]
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    submit = message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload())
    summary = message(type="task.summary", sender="platform", receiver="VE-0001", payload={"request": "task.summary", "child_results": [{"result": "ok"}], "current_round": 1, "remaining_limits": {"rounds": 3, "nodes": 18, "children": 5}, "team_capability_catalog": manager_payload()["team_capability_catalog"], "team_catalog_version": 2} , key="summary")
    gateway.events = [event(submit, event_id="$1", ts=1), event(summary, event_id="$2", ts=2)]

    subject.run_once()

    sent = sent_protocols(gateway)
    assert [item.type for item in sent] == ["agent.message", "task.delegate", "task.summary"]
    assert sent[-1].payload["summary"] == "可按期入职"
    assert engine.plan_contexts[-1].child_results == [{"result": "ok"}]


def test_worker_requests_tool_then_can_complete_only_after_real_tool_evidence(tmp_path):
    subject, gateway, engine, _ = runtime(tmp_path, role="hr")
    engine.selections = [SkillSelection(selected_skill_ids=["hr-onboarding-assistant"])]
    engine.work = [
        WorkerDecision(action="tool", tool_name="query_onboarding_employee", tool_arguments={"employee_ref": "新员工A"}),
        WorkerDecision(action="complete", result="资料齐全"),
    ]
    assignment = message(type="task.assign", sender="VE-0001", receiver="VE-0002", parent_task_id="root-1", payload=assign_payload())
    tool_result = message(type="tool.result", sender="platform", receiver="VE-0002", parent_task_id="root-1", payload={"ok": True, "audit_ids": ["AUD-1"], "data": {"source": "demo"}}, key="tool")
    gateway.events = [event(assignment, event_id="$assign", sender=MXIDS["VE-0001"], ts=1), event(tool_result, event_id="$tool", ts=2)]

    subject.run_once()

    sent = sent_protocols(gateway)
    assert [item.type for item in sent] == ["task.accepted", "task.progress", "agent.message", "tool.request", "task.result"]
    assert sent[3].receiver == "platform"
    assert sent[3].payload["plugin_id"] == "agentteams-onboarding-employee"
    assert sent[-1].payload["audit_ids"] == ["AUD-1"]
    assert engine.worker_contexts[0].task["goal"] == "核验新员工A的完整HR入职材料，并报告缺口。"


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
    assert not (tmp_path / "state" / "VE-0001.json").exists()

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
    from scripts.run_agentteams_agent import load_runtime_config

    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"matrix_url": "http://x", "room_id": "!room", "agents": {"manager": {"agent_id": "VE-0001", "mxid": "@manager:x", "token": "token"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="deepseek_key_missing"):
        load_runtime_config("manager", path, env={})
    with pytest.raises(ValueError, match="invalid_runtime_config"):
        load_runtime_config("hr", path, env={"DEEPSEEK_API_KEY": "x"})


def test_cli_accepts_a_role_specific_token_environment_reference(tmp_path):
    from scripts.run_agentteams_agent import load_runtime_config

    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({
        "matrix_url": "http://matrix", "room_id": "!room", "platform": {"mxid": "@platform:matrix"}, "team_catalog": TEAM,
        "agents": {
            "manager": {"agent_id": "VE-0001", "mxid": "@manager:matrix", "token_env": "MANAGER_MATRIX_TOKEN"},
            "hr": {"agent_id": "VE-0002", "mxid": "@hr:matrix", "token": "hr-only"},
            "it": {"agent_id": "VE-0003", "mxid": "@it:matrix", "token": "it-only"},
            "audit": {"agent_id": "VE-0005", "mxid": "@audit:matrix", "token": "audit-only"},
        },
    }), encoding="utf-8")

    config, _ = load_runtime_config("manager", path, env={"DEEPSEEK_API_KEY": "key", "MANAGER_MATRIX_TOKEN": "manager-only"})

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
        "manager": {"agent_id": "COORD-99", "mxid": "@coord:matrix", "token": "manager-only"},
        "hr": {"agent_id": "HR-27", "mxid": "@hr:matrix", "token": "hr-only"},
        "it": {"agent_id": "TECH-12", "mxid": "@it:matrix", "token": "it-only"},
        "audit": {"agent_id": "AUD-88", "mxid": "@audit:matrix", "token": "audit-only"},
    }
    path = tmp_path / "custom-runtime.json"
    path.write_text(json.dumps({"matrix_url": "http://matrix", "room_id": "!room", "platform": {"mxid": "@platform:matrix"}, "team_catalog": {}, "agents": agents}), encoding="utf-8")

    config, _ = load_runtime_config("manager", path, env={"DEEPSEEK_API_KEY": "key"})

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
    engine.selections = [SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"]), SkillSelection(selected_skill_ids=["agentteams-onboarding-readiness"])]
    engine.plans = [plan(), plan()]
    gateway.events = [event(message(type="task.submit", sender="platform", receiver="VE-0001", payload=manager_payload()))]
    gateway.fail_on_send = 2

    assert subject.run_once() == 0
    first_attempt = sent_protocols(gateway)
    assert [item.type for item in first_attempt] == ["agent.message"]
    assert subject._state["cursor"] is None

    gateway.fail_on_send = None
    assert subject.run_once() == 1
    resent = sent_protocols(gateway)[1:]
    assert resent[0].message_id == first_attempt[0].message_id
    assert resent[0].idempotency_key == first_attempt[0].idempotency_key
    assert [item.type for item in resent] == ["agent.message", "task.delegate"]
    assert subject._state["cursor"] == "cursor-1"


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
