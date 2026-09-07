"""Task 9 contracts for the deterministic and real-only AgentTeams verifiers."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from app import models
from app.services.agentteams_agent_runtime import AgentRuntime, AgentRuntimeConfig
from app.services.agentteams_collaboration import AgentTeamsCollaborationService
from app.services.agentteams_decisions import AgentDecisionEngine, AgentDecisionError
from app.services.agentteams_gateway import MatrixEvent, MatrixSyncBatch
from app.services.agentteams_protocol import AgentProtocolMessage, render_protocol_line
from app.services.agentteams_runtime_config import MatrixIdentity, StrictRuntimeConfig
from app.services.agentteams_runner import AgentTeamsCollaborationRunner
from app.services.agentteams_skills import ProjectSkillRegistry
from app.services.llm import LLMProvider


ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_verifiers_have_explicit_safe_contracts():
    """Real acceptance helpers must never fall back to local-demo behaviour."""
    matrix = _load_script("verify_agentteams_matrix.py")
    smoke = _load_script("run_agentteams_real_smoke.py")

    assert matrix.main is not None
    assert matrix.verify_matrix_environment is not None
    assert smoke.main is not None
    assert smoke.build_evidence is not None
    assert smoke.FIXED_GOAL == "评估新员工A是否可以按期入职。"


def test_matrix_verifier_uses_five_real_format_events_without_exposing_tokens():
    matrix = _load_script("verify_agentteams_matrix.py")
    identities = {
        "platform": MatrixIdentity("@platform:matrix.test", "platform-secret"),
        "manager": MatrixIdentity("@manager:matrix.test", "manager-secret", "VE-0001", ("agentteams-onboarding-readiness",), ()),
        "hr": MatrixIdentity("@hr:matrix.test", "hr-secret", "VE-0002", ("hr-onboarding-assistant",), ()),
        "it": MatrixIdentity("@it:matrix.test", "it-secret", "VE-0003", ("it-service-assistant",), ()),
        "audit": MatrixIdentity("@audit:matrix.test", "audit-secret", "VE-0005", ("audit-evidence-review",), ()),
    }
    runtime = StrictRuntimeConfig("https://matrix.test", "!task9:matrix.test", identities["platform"], {key: identities[key] for key in ("manager", "hr", "it", "audit")}, "TEAM-ONBOARD", {})
    sent: list[str] = []

    class _Gateway:
        def __init__(self, identity): self.identity = identity
        def whoami(self): return self.identity.mxid
        def joined_members(self, _room): return {identity.mxid for identity in identities.values()}
        def send_message(self, _room, _body):
            event_id = f"$verify-{len(sent) + 1}"; sent.append(event_id); return event_id
        def sync_messages(self, _since, _rooms):
            return MatrixSyncBatch([MatrixEvent(event_id, "!task9:matrix.test", "@platform:matrix.test", "safe", 1) for event_id in sent], "next")
        def close(self): pass

    evidence = matrix.verify_matrix_environment(runtime, gateway_factory=lambda _runtime, identity: _Gateway(identity), pause=lambda _: None)
    assert evidence["ok"] is True
    assert set(evidence["event_ids"].values()) == set(sent)
    assert "secret" not in str(evidence).lower()


def test_matrix_verifier_advances_each_cursor_and_accumulates_split_ping_batches():
    """Dropping a per-account sync cursor loses pings when initial sync is state-limited."""
    matrix = _load_script("verify_agentteams_matrix.py")
    identities = {
        "platform": MatrixIdentity("@platform:matrix.test", "platform-secret"),
        "manager": MatrixIdentity("@manager:matrix.test", "manager-secret", "VE-0001", (), ()),
        "hr": MatrixIdentity("@hr:matrix.test", "hr-secret", "VE-0002", (), ()),
        "it": MatrixIdentity("@it:matrix.test", "it-secret", "VE-0003", (), ()),
        "audit": MatrixIdentity("@audit:matrix.test", "audit-secret", "VE-0005", (), ()),
    }
    runtime = StrictRuntimeConfig("https://matrix.test", "!task9:matrix.test", identities["platform"], {name: identities[name] for name in ("manager", "hr", "it", "audit")}, "TEAM-ONBOARD", {})
    sent: list[str] = []
    initial_cursors: dict[str, str] = {}
    incremental_cursors: dict[str, list[str]] = {name: [] for name in identities}

    class _LimitedGateway:
        def __init__(self, name, identity): self.name, self.identity, self.incremental = name, identity, 0
        def whoami(self): return self.identity.mxid
        def joined_members(self, _room): return {item.mxid for item in identities.values()}
        def send_message(self, _room, _body):
            event_id = f"$ping-{len(sent) + 1}"; sent.append(event_id); return event_id
        def sync_messages(self, since, _rooms):
            if since is None:
                cursor = f"{self.name}-cursor-0"; initial_cursors[self.name] = cursor
                return MatrixSyncBatch([MatrixEvent(f"$state-{self.name}", runtime.room_id, self.identity.mxid, "state", 0)], cursor)
            incremental_cursors[self.name].append(since)
            self.incremental += 1
            batch_ids = sent[:2] if self.incremental == 1 else sent[2:]
            return MatrixSyncBatch([MatrixEvent(event_id, runtime.room_id, self.identity.mxid, "ping", self.incremental) for event_id in batch_ids], f"{self.name}-cursor-{self.incremental}")
        def close(self): pass

    evidence = matrix.verify_matrix_environment(runtime, gateway_factory=lambda _runtime, identity: _LimitedGateway(next(name for name, item in identities.items() if item is identity), identity), pause=lambda _: None, sync_attempts=2)
    assert evidence["ok"] is True
    assert set(evidence["event_ids"].values()) == set(sent)
    assert set(initial_cursors) == set(identities)
    assert all(cursors == [f"{name}-cursor-0", f"{name}-cursor-1"] for name, cursors in incremental_cursors.items())
    assert "secret" not in str(evidence).lower()


def test_matrix_verifier_timeout_still_polls_every_account_after_cursor_snapshot():
    """Breaking after the first missing account hides per-account Matrix visibility failures."""
    matrix = _load_script("verify_agentteams_matrix.py")
    identities = {
        "platform": MatrixIdentity("@platform:matrix.test", "platform-secret"),
        "manager": MatrixIdentity("@manager:matrix.test", "manager-secret", "VE-0001", (), ()),
        "hr": MatrixIdentity("@hr:matrix.test", "hr-secret", "VE-0002", (), ()),
        "it": MatrixIdentity("@it:matrix.test", "it-secret", "VE-0003", (), ()),
        "audit": MatrixIdentity("@audit:matrix.test", "audit-secret", "VE-0005", (), ()),
    }
    runtime = StrictRuntimeConfig("https://matrix.test", "!task9:matrix.test", identities["platform"], {name: identities[name] for name in ("manager", "hr", "it", "audit")}, "TEAM-ONBOARD", {})
    polled: list[str] = []

    class _MissingGateway:
        def __init__(self, name, identity): self.name, self.identity = name, identity
        def whoami(self): return self.identity.mxid
        def joined_members(self, _room): return {item.mxid for item in identities.values()}
        def send_message(self, _room, _body): return f"$ping-{self.name}"
        def sync_messages(self, since, _rooms):
            if since is None: return MatrixSyncBatch([], f"{self.name}-cursor-0")
            polled.append(self.name); return MatrixSyncBatch([], f"{self.name}-cursor-1")
        def close(self): pass

    evidence = matrix.verify_matrix_environment(runtime, gateway_factory=lambda _runtime, identity: _MissingGateway(next(name for name, item in identities.items() if item is identity), identity), pause=lambda _: None, sync_attempts=1)
    assert evidence == {"ok": False, "accounts": [], "event_ids": {}, "reason": "matrix_sync_missing_verifier_events"}
    assert set(polled) == set(identities)


def test_real_smoke_evidence_rejects_non_strict_or_incomplete_persistence_without_bodies():
    smoke = _load_script("run_agentteams_real_smoke.py")
    detail = {"nodes": [
        {"assignee_agent_id": "VE-0001", "depth": 0},
        {"assignee_agent_id": "VE-0002", "depth": 1, "started_at": "2026-09-05T00:00:00", "finished_at": "2026-09-05T00:01:00"},
        {"assignee_agent_id": "VE-0003", "depth": 1, "started_at": "2026-09-05T00:00:30", "finished_at": "2026-09-05T00:01:30"},
        {"assignee_agent_id": "VE-0005", "depth": 2},
    ]}
    events = [
        {"event_type": "task.delegate", "detail": {"round": 1, "selected_skill_ids": ["agentteams-onboarding-readiness"]}},
        {"event_type": "tool.result", "detail": {"audit_ids": [11]}},
        {"event_type": "task.summarizing", "detail": {}},
    ]
    messages = [{"matrix_event_id": "$event-1", "body": "token=must-not-be-printed"}]
    health = {"ready_for_demo": False, "mode": "local_demo", "transport": "local", "workers": []}
    evidence = smoke.build_evidence("AT-test", health, detail, events, messages, {"status": "failed", "summary": ""})
    assert evidence["ok"] is False
    assert {"strict_matrix_health", "agent_heartbeats", "completed_result", "governed_tool_audits"}.issubset(evidence["missing_criteria"])
    assert "token" not in str(evidence).lower()


def _valid_real_smoke_evidence_inputs():
    health = {
        "ready_for_demo": True, "mode": "agentteams_strict", "transport": "matrix",
        "workers": [{"status": "ready"}] * 4,
    }
    detail = {"nodes": [
        {"id": "root", "client_task_key": "root", "parent_task_id": None, "assignee_agent_id": "VE-0001", "coordinator_agent_id": "VE-0001", "depth": 0},
        {"id": "hr", "client_task_key": "hr-check", "parent_task_id": "root", "assignee_agent_id": "VE-0002", "coordinator_agent_id": "VE-0001", "depth": 1, "required": True},
        {"id": "it", "client_task_key": "it-check", "parent_task_id": "root", "assignee_agent_id": "VE-0003", "coordinator_agent_id": "VE-0001", "depth": 1, "required": True},
        {"id": "audit", "client_task_key": "audit-check", "parent_task_id": "it", "assignee_agent_id": "VE-0005", "coordinator_agent_id": "VE-0003", "depth": 2, "required": True},
    ], "dependency_edges": []}
    events = [
        {"event_seq": 1, "event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0001", "detail": {"round": 1, "client_task_keys": ["hr-check", "it-check"], "selected_skill_ids": ["agentteams-onboarding-readiness"]}},
        {"event_seq": 2, "event_type": "task.assigned", "task_id": "hr", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0002", "detail": {"parent_task_id": "root"}},
        {"event_seq": 3, "event_type": "task.assigned", "task_id": "it", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0003", "detail": {"parent_task_id": "root"}},
        {"event_seq": 4, "event_type": "tool.result", "task_id": "hr", "actor_agent_id": "VE-0002", "target_agent_id": "VE-0002", "detail": {"decision": "allow", "audit_ids": [11]}},
        {"event_seq": 5, "event_type": "tool.result", "task_id": "it", "actor_agent_id": "VE-0003", "target_agent_id": "VE-0003", "detail": {"decision": "allow", "audit_ids": [12]}},
        {"event_seq": 6, "event_type": "task.delegate", "task_id": "it", "actor_agent_id": "VE-0003", "target_agent_id": "VE-0003", "detail": {}},
        {"event_seq": 7, "event_type": "task.completed", "task_id": "hr", "actor_agent_id": "VE-0002", "target_agent_id": "VE-0002", "detail": {}},
        {"event_seq": 8, "event_type": "tool.result", "task_id": "audit", "actor_agent_id": "VE-0005", "target_agent_id": "VE-0005", "detail": {"decision": "allow", "audit_ids": [13]}},
        {"event_seq": 9, "event_type": "task.completed", "task_id": "audit", "actor_agent_id": "VE-0005", "target_agent_id": "VE-0005", "detail": {}},
        {"event_seq": 10, "event_type": "task.completed", "task_id": "it", "actor_agent_id": "VE-0003", "target_agent_id": "VE-0003", "detail": {}},
        {"event_seq": 11, "event_type": "task.summary", "task_id": "root", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0001", "detail": {"readiness_decision": "incomplete"}},
    ]
    messages = [
        {"matrix_event_id": "$event-1", "message_type": "task.delegate", "task_id": "root", "sender_agent_id": "VE-0001", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"subtasks": [
            {"client_task_key": "hr-check", "assignee_agent_id": "VE-0002", "required_capability": "hr.onboarding.read", "depends_on": [], "required": True},
            {"client_task_key": "it-check", "assignee_agent_id": "VE-0003", "required_capability": "it.readiness.read", "depends_on": [], "required": True},
        ]}},
        {"matrix_event_id": "$event-2", "message_type": "tool.request", "task_id": "it", "sender_agent_id": "VE-0003", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"tool_name": "agentteams.it.lookup_readiness", "plugin_id": "agentteams-it-readiness", "action": "execute", "required_capability": "it.readiness.read"}},
        {"matrix_event_id": "$event-3", "message_type": "tool.result", "task_id": "it", "sender_agent_id": "platform", "receiver_agent_id": "VE-0003", "direction": "outbound", "payload": {"ok": True, "decision": "allow", "audit_ids": [12], "data": {"pending_items": [{"evidence_id": "IT-A-003", "status": "missing"}]}}},
        {"matrix_event_id": "$event-4", "message_type": "tool.request", "task_id": "audit", "sender_agent_id": "VE-0005", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"tool_name": "agentteams.audit.lookup_evidence", "plugin_id": "agentteams-onboarding-evidence", "action": "execute", "required_capability": "audit.evidence.read"}},
        {"matrix_event_id": "$event-5", "message_type": "tool.result", "task_id": "audit", "sender_agent_id": "platform", "receiver_agent_id": "VE-0005", "direction": "outbound", "payload": {"ok": True, "decision": "allow", "audit_ids": [13]}},
    ]
    result = {
        "status": "completed",
        "summary": "done",
        "node_results": [{"task_id": "root", "result": {"readiness_decision": "incomplete"}}],
    }
    return health, detail, events, messages, result


@pytest.mark.parametrize("readiness_decision", ["not_ready", "incomplete"])
def test_real_smoke_accepts_it_worker_delegated_audit_and_parallel_root_release(readiness_decision):
    smoke = _load_script("run_agentteams_real_smoke.py")
    health, detail, events, messages, result = _valid_real_smoke_evidence_inputs()
    events[-1]["detail"]["readiness_decision"] = readiness_decision
    result["node_results"][0]["result"]["readiness_decision"] = readiness_decision

    evidence = smoke.build_evidence(
        "AT-it-audit", health, detail, events, messages, result,
    )

    assert evidence["ok"] is True, evidence["missing_criteria"]
    assert "onboarding_collaboration_topology" not in evidence["missing_criteria"]
    assert "parallel_root_worker_release" not in evidence["missing_criteria"]


@pytest.mark.parametrize("source", ["summary-event", "result"])
def test_real_smoke_rejects_ready_decision_for_fixed_missing_evidence(source):
    smoke = _load_script("run_agentteams_real_smoke.py")
    health, detail, events, messages, result = deepcopy(_valid_real_smoke_evidence_inputs())
    if source == "summary-event":
        events[-1]["detail"]["readiness_decision"] = "ready"
    else:
        result["node_results"][0]["result"]["readiness_decision"] = "ready"

    evidence = smoke.build_evidence("AT-ready-misclassification", health, detail, events, messages, result)

    assert evidence["ok"] is False
    assert "fixed_gap_readiness_decision" in evidence["missing_criteria"]


@pytest.mark.parametrize("case", ["duplicate-delegate", "wrong-keys", "node-key-mismatch", "optional-worker", "dependency-edge"])
def test_real_smoke_rejects_false_positive_first_round_plans(case):
    smoke = _load_script("run_agentteams_real_smoke.py")
    health, detail, events, messages, result = deepcopy(_valid_real_smoke_evidence_inputs())
    if case == "duplicate-delegate":
        events.append({**events[0], "event_seq": 12})
    elif case == "wrong-keys":
        events[0]["detail"]["client_task_keys"] = ["hr-check", "other"]
    elif case == "node-key-mismatch":
        detail["nodes"][2]["client_task_key"] = "different-it-key"
    elif case == "optional-worker":
        detail["nodes"][1]["required"] = False
    else:
        detail["dependency_edges"] = [{"task_id": "it", "depends_on_task_id": "hr"}]

    evidence = smoke.build_evidence("AT-invalid-plan", health, detail, events, messages, result)

    assert evidence["ok"] is False
    assert "first_round_parallel_plan" in evidence["missing_criteria"]


@pytest.mark.parametrize("case", ["wrong-it-tool", "it-gap-absent", "wrong-audit-tool"])
def test_real_smoke_rejects_false_positive_tool_evidence(case):
    smoke = _load_script("run_agentteams_real_smoke.py")
    health, detail, events, messages, result = deepcopy(_valid_real_smoke_evidence_inputs())
    if case == "wrong-it-tool":
        messages[1]["payload"]["plugin_id"] = "agentteams-onboarding-employee"
    elif case == "it-gap-absent":
        messages[2]["payload"]["data"]["pending_items"][0]["status"] = "ready"
    else:
        messages[3]["payload"]["tool_name"] = "agentteams.it.lookup_readiness"

    evidence = smoke.build_evidence("AT-invalid-tools", health, detail, events, messages, result)

    assert evidence["ok"] is False
    assert "it_audit_tool_evidence" in evidence["missing_criteria"]


def test_real_smoke_rejects_root_workers_released_after_one_already_completed():
    smoke = _load_script("run_agentteams_real_smoke.py")
    health = {
        "ready_for_demo": True, "mode": "agentteams_strict", "transport": "matrix",
        "workers": [{"status": "ready"}] * 4,
    }
    detail = {"nodes": [
        {"id": "root", "parent_task_id": None, "assignee_agent_id": "VE-0001", "coordinator_agent_id": "VE-0001", "depth": 0},
        {"id": "hr", "parent_task_id": "root", "assignee_agent_id": "VE-0002", "coordinator_agent_id": "VE-0001", "depth": 1},
        {"id": "it", "parent_task_id": "root", "assignee_agent_id": "VE-0003", "coordinator_agent_id": "VE-0001", "depth": 1},
        {"id": "audit", "parent_task_id": "hr", "assignee_agent_id": "VE-0005", "coordinator_agent_id": "VE-0002", "depth": 2},
    ]}
    events = [
        {"event_seq": 1, "event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0001", "detail": {"round": 1, "selected_skill_ids": ["agentteams-onboarding-readiness"]}},
        {"event_seq": 2, "event_type": "task.assigned", "task_id": "hr", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0002", "detail": {"parent_task_id": "root"}},
        {"event_seq": 3, "event_type": "tool.result", "task_id": "hr", "actor_agent_id": "VE-0002", "target_agent_id": "VE-0002", "detail": {"decision": "allow", "audit_ids": [11]}},
        {"event_seq": 4, "event_type": "task.completed", "task_id": "hr", "actor_agent_id": "VE-0002", "target_agent_id": "VE-0002", "detail": {}},
        {"event_seq": 5, "event_type": "task.assigned", "task_id": "it", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0003", "detail": {"parent_task_id": "root"}},
        {"event_seq": 6, "event_type": "tool.result", "task_id": "it", "actor_agent_id": "VE-0003", "target_agent_id": "VE-0003", "detail": {"decision": "allow", "audit_ids": [12]}},
        {"event_seq": 7, "event_type": "task.delegate", "task_id": "it", "actor_agent_id": "VE-0003", "target_agent_id": "VE-0003", "detail": {}},
        {"event_seq": 8, "event_type": "tool.result", "task_id": "audit", "actor_agent_id": "VE-0005", "target_agent_id": "VE-0005", "detail": {"decision": "allow", "audit_ids": [13]}},
        {"event_seq": 9, "event_type": "task.completed", "task_id": "audit", "actor_agent_id": "VE-0005", "target_agent_id": "VE-0005", "detail": {}},
        {"event_seq": 10, "event_type": "task.completed", "task_id": "it", "actor_agent_id": "VE-0003", "target_agent_id": "VE-0003", "detail": {}},
        {"event_seq": 11, "event_type": "task.summary", "task_id": "root", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0001", "detail": {}},
    ]

    evidence = smoke.build_evidence(
        "AT-serial-release", health, detail, events,
        [{"matrix_event_id": f"$event-{index}"} for index in range(1, 5)],
        {"status": "completed", "summary": "done"},
    )

    assert evidence["ok"] is False
    assert "parallel_root_worker_release" in evidence["missing_criteria"]


@pytest.mark.parametrize(
    ("it_tool_seq", "it_delegate_seq", "audit_complete_seq", "hr_complete_seq", "it_complete_seq", "manager_summary_seq"),
    [
        (6, 5, 7, 8, 9, 10),
        (4, 5, 8, 6, 7, 9),
        (4, 5, 6, 8, 9, 7),
    ],
    ids=["tool-after-delegate", "audit-after-it", "manager-before-workers"],
)
def test_real_smoke_rejects_out_of_order_it_audit_chain(
    it_tool_seq, it_delegate_seq, audit_complete_seq, hr_complete_seq, it_complete_seq, manager_summary_seq,
):
    smoke = _load_script("run_agentteams_real_smoke.py")
    nodes = [
        {"id": "root", "parent_task_id": None, "assignee_agent_id": "VE-0001", "coordinator_agent_id": "VE-0001", "depth": 0},
        {"id": "hr", "parent_task_id": "root", "assignee_agent_id": "VE-0002", "coordinator_agent_id": "VE-0001", "depth": 1},
        {"id": "it", "parent_task_id": "root", "assignee_agent_id": "VE-0003", "coordinator_agent_id": "VE-0001", "depth": 1},
        {"id": "audit", "parent_task_id": "it", "assignee_agent_id": "VE-0005", "coordinator_agent_id": "VE-0003", "depth": 2},
    ]
    events = [
        {"event_seq": 1, "event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {"round": 1}},
        {"event_seq": it_tool_seq, "event_type": "tool.result", "task_id": "it", "actor_agent_id": "VE-0003", "detail": {"decision": "allow", "audit_ids": [12]}},
        {"event_seq": it_delegate_seq, "event_type": "task.delegate", "task_id": "it", "actor_agent_id": "VE-0003", "detail": {}},
        {"event_seq": audit_complete_seq, "event_type": "task.completed", "task_id": "audit", "actor_agent_id": "VE-0005", "detail": {}},
        {"event_seq": hr_complete_seq, "event_type": "task.completed", "task_id": "hr", "actor_agent_id": "VE-0002", "detail": {}},
        {"event_seq": it_complete_seq, "event_type": "task.completed", "task_id": "it", "actor_agent_id": "VE-0003", "detail": {}},
        {"event_seq": manager_summary_seq, "event_type": "task.summary", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {}},
    ]

    assert smoke._has_expected_onboarding_topology(nodes, events) is False


@pytest.mark.parametrize(
    ("audit_parent", "audit_coordinator", "delegate_task", "delegate_actor"),
    [
        ("hr", "VE-0002", "hr", "VE-0002"),
        ("it", "VE-0002", "it", "VE-0003"),
        ("it", "VE-0003", "hr", "VE-0003"),
        ("it", "VE-0003", "it", "VE-0002"),
    ],
    ids=["hr-parent", "wrong-coordinator", "wrong-task", "wrong-actor"],
)
def test_real_smoke_rejects_audit_delegation_not_owned_by_it_parent(
    audit_parent, audit_coordinator, delegate_task, delegate_actor,
):
    smoke = _load_script("run_agentteams_real_smoke.py")
    nodes = [
        {"id": "root", "parent_task_id": None, "assignee_agent_id": "VE-0001", "coordinator_agent_id": "VE-0001", "depth": 0},
        {"id": "hr", "parent_task_id": "root", "assignee_agent_id": "VE-0002", "coordinator_agent_id": "VE-0001", "depth": 1},
        {"id": "it", "parent_task_id": "root", "assignee_agent_id": "VE-0003", "coordinator_agent_id": "VE-0001", "depth": 1},
        {"id": "audit", "parent_task_id": audit_parent, "assignee_agent_id": "VE-0005", "coordinator_agent_id": audit_coordinator, "depth": 2},
    ]
    events = [
        {"event_seq": 1, "event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {"round": 1}},
        {"event_seq": 4, "event_type": "tool.result", "task_id": "it", "actor_agent_id": "VE-0003", "detail": {"decision": "allow", "audit_ids": [12]}},
        {"event_seq": 5, "event_type": "task.delegate", "task_id": delegate_task, "actor_agent_id": delegate_actor, "detail": {}},
        {"event_seq": 6, "event_type": "task.completed", "task_id": "audit", "actor_agent_id": "VE-0005", "detail": {}},
        {"event_seq": 7, "event_type": "task.completed", "task_id": "hr", "actor_agent_id": "VE-0002", "detail": {}},
        {"event_seq": 8, "event_type": "task.completed", "task_id": "it", "actor_agent_id": "VE-0003", "detail": {}},
        {"event_seq": 9, "event_type": "task.summary", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {}},
    ]

    assert smoke._has_expected_onboarding_topology(nodes, events) is False


def test_real_smoke_does_not_count_null_as_a_delegation_round():
    smoke = _load_script("run_agentteams_real_smoke.py")
    health = {
        "ready_for_demo": True,
        "mode": "agentteams_strict",
        "transport": "matrix",
        "workers": [{"status": "ready"}] * 4,
    }
    detail = {"nodes": [
        {"id": "root", "parent_task_id": None, "assignee_agent_id": "VE-0001", "coordinator_agent_id": "VE-0001", "depth": 0},
        {"id": "hr", "parent_task_id": "root", "assignee_agent_id": "VE-0002", "coordinator_agent_id": "VE-0001", "depth": 1, "started_at": "2026-09-05T00:00:00", "finished_at": "2026-09-05T00:01:00"},
        {"id": "it", "parent_task_id": "root", "assignee_agent_id": "VE-0003", "coordinator_agent_id": "VE-0001", "depth": 1, "started_at": "2026-09-05T00:00:30", "finished_at": "2026-09-05T00:01:30"},
        {"id": "audit", "parent_task_id": "hr", "assignee_agent_id": "VE-0005", "coordinator_agent_id": "VE-0002", "depth": 2},
    ]}
    events = [
        {"event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {"round": None, "selected_skill_ids": ["agentteams-onboarding-readiness"]}},
        {"event_type": "task.delegate", "task_id": "hr", "actor_agent_id": "VE-0002", "detail": {}},
        {"event_type": "tool.result", "detail": {"audit_ids": [11, 12, 13]}},
        {"event_type": "task.summary", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {}},
    ]

    evidence = smoke.build_evidence(
        "AT-null-round", health, detail, events,
        [{"matrix_event_id": f"$event-{index}"} for index in range(1, 5)],
        {"status": "completed", "summary": "done"},
    )

    assert evidence["ok"] is False
    assert evidence["rounds"] == []
    assert "delegation_round" in evidence["missing_criteria"]


def test_real_smoke_exits_safely_when_the_started_api_is_not_strict(monkeypatch, capsys):
    """The real-only script must reject a local API rather than silently demo it."""
    smoke = _load_script("run_agentteams_real_smoke.py")

    class _Client:
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    monkeypatch.setattr(smoke.httpx, "Client", lambda **_kwargs: _Client())
    monkeypatch.setattr(
        smoke,
        "_get",
        lambda *_args, **_kwargs: {
            "ready_for_demo": False,
            "mode": "local_demo",
            "transport": "local",
            "token": "must-not-leak",
        },
    )
    assert smoke.main([]) == 2
    output = capsys.readouterr().out
    assert "strict_matrix_not_ready" in output
    assert "must-not-leak" not in output


def test_real_smoke_timeout_returns_failure_without_printing_response_bodies(monkeypatch, capsys):
    """Polling a live but unfinished strict run has a bounded, redacted failure."""
    smoke = _load_script("run_agentteams_real_smoke.py")

    class _Client:
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    def fake_get(_client, path, *, actor=False):
        del actor
        if path.endswith("/health"):
            return {
                "ready_for_demo": True,
                "mode": "agentteams_strict",
                "transport": "matrix",
                "workers": [{"status": "ready"}] * 4,
            }
        if path.endswith("/events") or path.endswith("/messages"):
            return []
        if path.endswith("/result"):
            return {"status": "timed_out", "summary": "token=must-not-leak"}
        return {"status": "planning", "body": "token=must-not-leak"}

    monkeypatch.setattr(smoke.httpx, "Client", lambda **_kwargs: _Client())
    monkeypatch.setattr(smoke, "_get", fake_get)
    monkeypatch.setattr(smoke, "_post", lambda *_args, **_kwargs: {"id": "AT-timeout"})
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: pytest.fail("timeout must not sleep after deadline"))

    assert smoke.main(["--timeout-seconds", "1"]) == 1
    output = capsys.readouterr().out
    assert "completed_result" in output
    assert "must-not-leak" not in output


def test_matrix_verifier_invalid_config_has_safe_nonzero_exit(monkeypatch, capsys):
    matrix = _load_script("verify_agentteams_matrix.py")
    monkeypatch.setattr(matrix, "load_strict_runtime_config", lambda _path: (_ for _ in ()).throw(ValueError("token=must-not-leak")))

    assert matrix.main([]) == 2
    output = capsys.readouterr().out
    assert "invalid_runtime_config" in output
    assert "must-not-leak" not in output


def test_real_smoke_evidence_redacts_bearer_basic_and_json_credentials():
    """Changing the smoke output to pass through a service summary leaks credentials."""
    smoke = _load_script("run_agentteams_real_smoke.py")
    secrets = {
        "bearer-credential-42",
        "basic-credential-42",
        "json-token-42",
        "access-token-42",
        "password-42",
        "secret-42",
    }
    summary = (
        "authorization: Bearer bearer-credential-42; "
        "Authorization: Basic basic-credential-42; "
        '{"token":"json-token-42","access_token":"access-token-42",'
        '"password":"password-42","secret":"secret-42"}'
    )
    evidence = smoke.build_evidence(
        "AT-redaction",
        {"ready_for_demo": True, "mode": "agentteams_strict", "transport": "matrix", "workers": [{"status": "ready"}] * 4},
        {"nodes": [{"id": "root", "parent_task_id": None, "assignee_agent_id": "VE-0001", "coordinator_agent_id": "VE-0001", "depth": 0}, {"id": "hr", "parent_task_id": "root", "assignee_agent_id": "VE-0002", "coordinator_agent_id": "VE-0001", "depth": 1}, {"id": "it", "parent_task_id": "root", "assignee_agent_id": "VE-0003", "coordinator_agent_id": "VE-0001", "depth": 1}, {"id": "audit", "parent_task_id": "it", "assignee_agent_id": "VE-0005", "coordinator_agent_id": "VE-0003", "depth": 2}]},
        [
            {"event_seq": 1, "event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0001", "detail": {"round": 1, "selected_skill_ids": ["agentteams-onboarding-readiness"]}},
            {"event_seq": 2, "event_type": "task.assigned", "task_id": "hr", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0002", "detail": {}},
            {"event_seq": 3, "event_type": "task.assigned", "task_id": "it", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0003", "detail": {}},
            {"event_seq": 4, "event_type": "tool.result", "task_id": "hr", "actor_agent_id": "VE-0002", "detail": {"decision": "allow", "audit_ids": [11]}},
            {"event_seq": 5, "event_type": "tool.result", "task_id": "it", "actor_agent_id": "VE-0003", "detail": {"decision": "allow", "audit_ids": [12]}},
            {"event_seq": 6, "event_type": "task.delegate", "task_id": "it", "actor_agent_id": "VE-0003", "detail": {}},
            {"event_seq": 7, "event_type": "task.completed", "task_id": "hr", "actor_agent_id": "VE-0002", "detail": {}},
            {"event_seq": 8, "event_type": "tool.result", "task_id": "audit", "actor_agent_id": "VE-0005", "detail": {"decision": "allow", "audit_ids": [13]}},
            {"event_seq": 9, "event_type": "task.completed", "task_id": "audit", "actor_agent_id": "VE-0005", "detail": {}},
            {"event_seq": 10, "event_type": "task.completed", "task_id": "it", "actor_agent_id": "VE-0003", "detail": {}},
            {"event_seq": 11, "event_type": "task.summary", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {}},
        ],
        [{"matrix_event_id": "$event-1"}, {"matrix_event_id": "$event-2"}, {"matrix_event_id": "$event-3"}, {"matrix_event_id": "$event-4"}],
        {"status": "completed", "summary": summary},
    )

    rendered = json.dumps(evidence)
    assert all(secret not in rendered for secret in secrets)


def test_real_smoke_rejects_manager_direct_audit_even_with_an_unrelated_depth_two_node():
    smoke = _load_script("run_agentteams_real_smoke.py")
    health = {"ready_for_demo": True, "mode": "agentteams_strict", "transport": "matrix", "workers": [{"status": "ready"}] * 4}
    detail = {"nodes": [
        {"id": "root", "parent_task_id": None, "assignee_agent_id": "VE-0001", "coordinator_agent_id": "VE-0001", "depth": 0},
        {"id": "hr", "parent_task_id": "root", "assignee_agent_id": "VE-0002", "coordinator_agent_id": "VE-0001", "depth": 1, "started_at": "2026-09-05T00:00:00", "finished_at": "2026-09-05T00:01:00"},
        {"id": "it", "parent_task_id": "root", "assignee_agent_id": "VE-0003", "coordinator_agent_id": "VE-0001", "depth": 1, "started_at": "2026-09-05T00:00:30", "finished_at": "2026-09-05T00:01:30"},
        {"id": "audit", "parent_task_id": "root", "assignee_agent_id": "VE-0005", "coordinator_agent_id": "VE-0001", "depth": 1},
        {"id": "unrelated-depth-two", "parent_task_id": "hr", "assignee_agent_id": "VE-0003", "coordinator_agent_id": "VE-0002", "depth": 2},
    ]}
    events = [
        {"event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {"round": 1, "selected_skill_ids": ["agentteams-onboarding-readiness"]}},
        {"event_type": "task.delegate", "task_id": "hr", "actor_agent_id": "VE-0002", "detail": {"round": 1}},
        {"event_type": "tool.result", "detail": {"audit_ids": [11, 12, 13]}},
        {"event_type": "task.summarizing", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {}},
    ]
    messages = [{"matrix_event_id": f"$event-{index}"} for index in range(1, 5)]

    evidence = smoke.build_evidence("AT-wrong-topology", health, detail, events, messages, {"status": "completed", "summary": "done"})

    assert evidence["ok"] is False
    assert "onboarding_collaboration_topology" in evidence["missing_criteria"]


def test_platform_transport_serializes_assignment_as_platform_sender(db_session):
    """The Matrix account, not a logical coordinator, authenticates platform outbox work."""
    service = AgentTeamsCollaborationService()
    run = service.create_task(
        db_session, team_id="TEAM-ONBOARD", leader_agent_id="VE-0001",
        title="sender regression", instruction="评估新员工A是否可以按期入职。", material={},
    )
    outbound = models.AgentMessage(
        id="AM-task9-platform-sender", root_task_id=run.id, task_id=run.id,
        parent_task_id=None, message_type="task.assign", sender_agent_id="VE-0001",
        receiver_agent_id="VE-0002", body="assignment", payload={}, matrix_room_id="",
        matrix_event_id=None, idempotency_key="task9:assignment", direction="outbound",
        delivery_status="pending",
    )
    db_session.add(outbound)
    db_session.flush()
    runner = AgentTeamsCollaborationRunner(service=service, gateway=_MatrixBus().endpoint("@platform:matrix.test"), room_id="!task9:matrix.test")
    try:
        assert runner._outbound_protocol(db_session, outbound).sender == "platform"
    finally:
        runner.close()


def test_worker_accepts_platform_signed_assignment_but_rejects_non_parent_sender(tmp_path):
    """The signed transport sender may be platform; the business ancestry may not be forged."""
    mxids = {"platform": "@platform:matrix.test", "VE-0001": "@manager:matrix.test", "VE-0002": "@hr:matrix.test", "VE-0003": "@it:matrix.test"}
    config = AgentRuntimeConfig(
        agent_id="VE-0002", role="hr", matrix_mxid=mxids["VE-0002"], matrix_token="test-hr",
        room_id="!task9:matrix.test", trusted_sender_mxids=mxids,
        allowed_skill_ids={"employee-collaboration", "hr-onboarding-assistant"}, state_dir=tmp_path,
    )
    runtime = AgentRuntime(config, _MatrixBus().endpoint(mxids["VE-0002"]), None, pause=lambda _: None)
    payload = {"parent_chain_agent_ids": ["VE-0001"], "team_catalog_version": 1, "team_capability_catalog": [{"agent_id": "VE-0005", "capabilities": ["agentteams-onboarding-evidence"]}], "title": "HR核查", "instruction": "核查材料", "client_task_key": "hr-check"}

    platform_assignment = AgentProtocolMessage(
        version="1.0", message_id="MSG-platform", type="task.assign", root_task_id="root", task_id="hr",
        parent_task_id="root", trace_id="root", sender="platform", receiver="VE-0002",
        timestamp="2026-09-05T00:00:00+00:00", idempotency_key="platform-assignment", payload=payload,
    )
    forged_assignment = platform_assignment.model_copy(update={"sender": "VE-0003", "message_id": "MSG-forged", "idempotency_key": "forged-assignment"})

    assert runtime._new_worker_context(platform_assignment, ["hr-onboarding-assistant"])["parent_chain_agent_ids"] == ["VE-0001"]
    try:
        with pytest.raises(AgentDecisionError, match="invalid_parent_chain"):
            runtime._new_worker_context(forged_assignment, ["hr-onboarding-assistant"])
    finally:
        runtime.close()


def _activate_matrix_agent(db_session, agent_id, mxid):
    employee = db_session.get(models.DigitalEmployee, agent_id)
    employee.runtime_type, employee.runtime_ref, employee.status = "agentteams", mxid, "active"


def _required_hr_delegate(run):
    return AgentProtocolMessage(
        version="1.0", message_id="MSG-task9-delegate", type="task.delegate",
        root_task_id=run.id, task_id=run.id, parent_task_id=None, trace_id=run.trace_id,
        sender="VE-0001", receiver="platform", timestamp="2026-09-05T00:00:00+00:00",
        idempotency_key=f"{run.id}:delegate:hr", payload={
            "selected_skill_ids": ["agentteams-onboarding-readiness"],
            "subtasks": [{
                "client_task_key": "hr-required", "title": "HR必需核查",
                "instruction": "核查新员工A材料。", "assignee_agent_id": "VE-0002",
                "required_capability": "agentteams-onboarding-employee", "depends_on": [],
                "material": {}, "required": True,
            }],
        },
    )


def test_runner_records_new_matrix_event_but_deduplicates_reused_protocol_key(db_session, monkeypatch):
    """Removing protocol-key reservation would replay a new Matrix event into a second task tree."""
    monkeypatch.setenv("DWP_AGENTTEAMS_MATRIX_DOMAIN", "matrix.test")
    mxids = {"platform": "@platform:matrix.test", "manager": "@manager:matrix.test", "hr": "@hr:matrix.test"}
    _activate_matrix_agent(db_session, "VE-0001", mxids["manager"])
    _activate_matrix_agent(db_session, "VE-0002", mxids["hr"])
    service = AgentTeamsCollaborationService()
    run = service.create_task(db_session, team_id="TEAM-ONBOARD", leader_agent_id="VE-0001", title="idempotency", instruction="评估新员工A是否可以按期入职。", material={})
    run.owner_human_no = "E10281"
    db_session.commit()
    bus = _MatrixBus()
    runner = AgentTeamsCollaborationRunner(service=service, gateway=bus.endpoint(mxids["platform"]), room_id="!task9:matrix.test")
    message = _required_hr_delegate(run)
    protocol_line = render_protocol_line(message)
    try:
        bus.endpoint(mxids["manager"]).send_protocol_message("!task9:matrix.test", "delegate", protocol_line)
        runner.tick_once(db_session)
        db_session.commit()
        before = {
            "nodes": db_session.query(models.AgentTaskNode).filter_by(root_task_id=run.id).count(),
            "events": db_session.query(models.AgentTaskEvent).filter_by(root_task_id=run.id).count(),
            "audits": db_session.query(models.AuditEvent).count(),
        }
        bus.events.append(MatrixEvent("$task9-reused-protocol-key", "!task9:matrix.test", mxids["manager"], protocol_line, len(bus.events) + 1))
        runner.tick_once(db_session)
        db_session.commit()
    finally:
        runner.close()

    after = {
        "nodes": db_session.query(models.AgentTaskNode).filter_by(root_task_id=run.id).count(),
        "events": db_session.query(models.AgentTaskEvent).filter_by(root_task_id=run.id).count(),
        "audits": db_session.query(models.AuditEvent).count(),
    }
    assert after == before
    duplicate = db_session.query(models.AgentMessage).filter_by(matrix_event_id="$task9-reused-protocol-key").one()
    assert duplicate.message_type == "matrix.duplicate"


def test_required_worker_failure_fails_ancestor_root_and_deduplicates(db_session, monkeypatch):
    """Removing required-failure propagation would leave the root planning after an auditable worker failure."""
    monkeypatch.setenv("DWP_AGENTTEAMS_MATRIX_DOMAIN", "matrix.test")
    mxids = {"manager": "@manager:matrix.test", "hr": "@hr:matrix.test"}
    _activate_matrix_agent(db_session, "VE-0001", mxids["manager"])
    _activate_matrix_agent(db_session, "VE-0002", mxids["hr"])
    service = AgentTeamsCollaborationService()
    run = service.create_task(db_session, team_id="TEAM-ONBOARD", leader_agent_id="VE-0001", title="required failure", instruction="评估新员工A是否可以按期入职。", material={})
    run.owner_human_no = "E10281"
    service.apply_message(db_session, matrix_sender=mxids["manager"], message=_required_hr_delegate(run))
    child = db_session.query(models.AgentTaskNode).filter_by(root_task_id=run.id, client_task_key="hr-required").one()
    failure = AgentProtocolMessage(
        version="1.0", message_id="MSG-task9-failed", type="task.failed", root_task_id=run.id,
        task_id=child.id, parent_task_id=run.id, trace_id=run.trace_id, sender="VE-0002",
        receiver="platform", timestamp="2026-09-05T00:01:00+00:00", idempotency_key=f"{child.id}:failed",
        payload={"error_code": "tool_evidence_unavailable", "error_message": "governed evidence unavailable"},
    )

    service.apply_message(db_session, matrix_sender=mxids["hr"], message=failure)
    first_event_count = db_session.query(models.AgentTaskEvent).filter_by(root_task_id=run.id).count()
    assert service.apply_message(db_session, matrix_sender=mxids["hr"], message=failure) == []

    root = db_session.get(models.AgentTaskNode, run.id)
    assert child.status == "failed"
    assert root.status == "failed"
    assert run.status == "failed"
    assert db_session.query(models.AgentTaskEvent).filter_by(root_task_id=run.id).count() == first_event_count


class _ScriptedLLM(LLMProvider):
    """One role's deliberate model choices; Runtime/DB/Gateway stay real."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def structured_output(self, messages, schema):
        self.calls.append(messages)
        return self.outputs.pop(0)

    def chat(self, messages, tools=None):  # pragma: no cover - outside structured decision path
        raise AssertionError("runtime must use structured decisions")

    def tool_call(self, messages, tools):  # pragma: no cover - outside structured decision path
        raise AssertionError("runtime must use structured decisions")


class _MatrixBus:
    """A strict Matrix-shaped event bus, deliberately not LocalDemoTransport."""

    mode = "agentteams_strict"

    def __init__(self):
        self.events: list[MatrixEvent] = []

    def endpoint(self, sender: str):
        return _MatrixEndpoint(self, sender)


class _MatrixEndpoint:
    def __init__(self, bus, sender):
        self.bus, self.sender, self.cursor = bus, sender, 0

    def send_protocol_message(self, room_id, text, protocol_line):
        index = len(self.bus.events) + 1
        event_id = f"$task9-{index}"
        self.bus.events.append(MatrixEvent(
            event_id=event_id, room_id=room_id, sender=self.sender,
            body=f"{text}\n{protocol_line}", ts=index,
        ))
        return event_id

    def sync_messages(self, since, room_ids):
        start = int(since or "0")
        visible = [event for event in self.bus.events[start:] if event.room_id in room_ids and event.sender != self.sender]
        self.cursor = len(self.bus.events)
        return MatrixSyncBatch(events=visible, next_cursor=str(self.cursor))

    def close(self):
        return None


def _decision_outputs():
    manager = [
        {"selected_skill_ids": ["agentteams-onboarding-readiness"]},
        {"selected_skill_ids": ["agentteams-onboarding-readiness"], "action": "delegate", "subtasks": [
            {"client_task_key": "hr-check", "title": "HR材料核查", "goal": "核查新员工A的材料。", "required_capability": "hr.onboarding.read", "assignee_agent_id": "VE-0002", "depends_on": [], "required": True, "reason": "需要HR原始材料。"},
            {"client_task_key": "it-check", "title": "IT就绪核查", "goal": "核查新员工A的账号和设备。", "required_capability": "it.readiness.read", "assignee_agent_id": "VE-0003", "depends_on": [], "required": True, "reason": "需要IT原始状态。"},
        ]},
        {"selected_skill_ids": ["agentteams-onboarding-readiness"], "action": "summarize", "summary": {"summary": "当前不能确认按期入职，IT-A-003 缺失仍需跟进。", "readiness_decision": "incomplete", "completed_items": ["HR、IT、审计均已核查"], "risks": ["IT-A-003 缺失"], "unknowns": [], "next_actions": ["补齐 IT-A-003"]}},
    ]
    hr = [
        {"selected_skill_ids": ["hr-onboarding-assistant"]},
        {"action": "tool", "tool_name": "agentteams.hr.lookup_onboarding", "tool_arguments": {"employee_ref": "新员工A"}},
        {"action": "complete", "result": "HR材料已完整核查。"},
    ]
    it = [
        {"selected_skill_ids": ["it-service-assistant"]},
        {"action": "tool", "tool_name": "agentteams.it.lookup_readiness", "tool_arguments": {"employee_ref": "新员工A"}},
        {"action": "delegate", "subtask": {"client_task_key": "audit-check", "title": "审计证据核查", "goal": "核查IT就绪缺口的审计依据。", "required_capability": "audit.evidence.read", "assignee_agent_id": "VE-0005", "depends_on": [], "required": True, "reason": "IT发现MFA缺口，需要独立审计复核。"}},
        {"action": "complete", "result": "IT缺口与审计复核已完成。"},
    ]
    audit = [
        {"selected_skill_ids": ["audit-evidence-review"]},
        {"action": "tool", "tool_name": "agentteams.audit.lookup_evidence", "tool_arguments": {"employee_ref": "新员工A"}},
        {"action": "complete", "result": "审计证据已复核，存在待归档项。"},
    ]
    return manager, hr, it, audit


def _strict_test_runtime_config(mxids):
    schema = {
        "type": "object", "required": ["employee_ref"],
        "properties": {"employee_ref": {"type": "string", "const": "新员工A", "maxLength": 16}},
        "additionalProperties": False,
    }
    return {
        "matrix_url": "http://matrix.test", "room_id": "!task9:matrix.test", "team_id": "TEAM-ONBOARD",
        "platform": {"mxid": mxids["platform"], "token": "test-platform"},
        "agents": {
            "manager": {"agent_id": "VE-0001", "mxid": mxids["VE-0001"], "token": "test-manager", "allowed_skill_ids": ["agentteams-onboarding-readiness"], "safe_tool_catalog": []},
            "hr": {"agent_id": "VE-0002", "mxid": mxids["VE-0002"], "token": "test-hr", "allowed_skill_ids": ["hr-onboarding-assistant"], "safe_tool_catalog": [{"name": "agentteams.hr.lookup_onboarding", "plugin_id": "agentteams-onboarding-employee", "action": "execute", "required_capability": "hr.onboarding.read", "input_schema": schema}]},
            "it": {"agent_id": "VE-0003", "mxid": mxids["VE-0003"], "token": "test-it", "allowed_skill_ids": ["it-service-assistant"], "safe_tool_catalog": [{"name": "agentteams.it.lookup_readiness", "plugin_id": "agentteams-it-readiness", "action": "execute", "required_capability": "it.readiness.read", "input_schema": schema}]},
            "audit": {"agent_id": "VE-0005", "mxid": mxids["VE-0005"], "token": "test-audit", "allowed_skill_ids": ["audit-evidence-review"], "safe_tool_catalog": [{"name": "agentteams.audit.lookup_evidence", "plugin_id": "agentteams-onboarding-evidence", "action": "execute", "required_capability": "audit.evidence.read", "input_schema": schema}]},
        },
        "team_catalog": {
            "VE-0001": {"capabilities": []},
            "VE-0002": {"capabilities": ["hr.onboarding.read"]},
            "VE-0003": {"capabilities": ["it.readiness.read"]},
            "VE-0005": {"capabilities": ["audit.evidence.read"]},
        },
    }


def test_fake_llm_drives_real_runtime_runner_gateway_policy_audit_state_machine(db_session, monkeypatch, tmp_path):
    """A complete adaptive run must not rely on the local-demo fixed transport."""
    monkeypatch.setenv("DWP_AGENTTEAMS_MATRIX_DOMAIN", "matrix.test")
    mxids = {"platform": "@platform:matrix.test", "VE-0001": "@manager:matrix.test", "VE-0002": "@hr:matrix.test", "VE-0003": "@it:matrix.test", "VE-0005": "@audit:matrix.test"}
    strict_config_path = tmp_path / "strict-runtime.json"
    strict_config_path.write_text(json.dumps(_strict_test_runtime_config(mxids)), encoding="utf-8")
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(strict_config_path))
    for agent_id, mxid in mxids.items():
        if agent_id == "platform":
            continue
        employee = db_session.get(models.DigitalEmployee, agent_id)
        employee.runtime_type, employee.runtime_ref, employee.status = "agentteams", mxid, "active"
    db_session.flush()

    bus = _MatrixBus()
    service = AgentTeamsCollaborationService()
    capability_catalog = {
        item["agent_id"]: set(item["capabilities"])
        for item in service._team_capability_catalog(db_session, "TEAM-ONBOARD")
    }
    assert {"hr.onboarding.read"}.issubset(capability_catalog["VE-0002"])
    assert {"it.readiness.read"}.issubset(capability_catalog["VE-0003"])
    assert {"audit.evidence.read"}.issubset(capability_catalog["VE-0005"])
    run = service.create_task(db_session, team_id="TEAM-ONBOARD", leader_agent_id="VE-0001", title="新员工A按期入职评估", instruction="评估新员工A是否可以按期入职。", material={})
    run.owner_human_no = "E10281"
    db_session.commit()
    runner = AgentTeamsCollaborationRunner(service=service, gateway=bus.endpoint(mxids["platform"]), room_id="!task9:matrix.test")
    assert runner.transport_mode == "agentteams_strict"
    registry = ProjectSkillRegistry.from_root(ROOT)
    manager_out, hr_out, it_out, audit_out = _decision_outputs()
    configs = [
        ("VE-0001", "manager", {"agentteams-manager-coordination", "agentteams-onboarding-readiness"}, [], manager_out),
        ("VE-0002", "hr", {"employee-collaboration", "hr-onboarding-assistant"}, [{"name": "agentteams.hr.lookup_onboarding", "plugin_id": "agentteams-onboarding-employee", "action": "execute", "required_capability": "hr.onboarding.read"}], hr_out),
        ("VE-0003", "it", {"employee-collaboration", "it-service-assistant"}, [{"name": "agentteams.it.lookup_readiness", "plugin_id": "agentteams-it-readiness", "action": "execute", "required_capability": "it.readiness.read"}], it_out),
        ("VE-0005", "audit", {"employee-collaboration", "audit-evidence-review"}, [{"name": "agentteams.audit.lookup_evidence", "plugin_id": "agentteams-onboarding-evidence", "action": "execute", "required_capability": "audit.evidence.read"}], audit_out),
    ]
    runtimes = []
    trusted = dict(mxids)
    for agent_id, role, skills, tools, outputs in configs:
        runtime_config = AgentRuntimeConfig(agent_id=agent_id, role=role, matrix_mxid=mxids[agent_id], matrix_token=f"test-{role}", room_id="!task9:matrix.test", trusted_sender_mxids=trusted, allowed_skill_ids=skills, safe_tool_catalog=tools, state_dir=tmp_path / role, pause_seconds=0)
        runtimes.append(AgentRuntime(runtime_config, bus.endpoint(mxids[agent_id]), AgentDecisionEngine(_ScriptedLLM(outputs), registry), pause=lambda _: None))

    handled_by_agent = {runtime.config.agent_id: 0 for runtime in runtimes}
    try:
        for _ in range(40):
            runner.tick_once(db_session)
            db_session.commit()
            # All currently released Workers get a turn before the next platform receive.
            for runtime in runtimes:
                handled_by_agent[runtime.config.agent_id] += runtime.run_once()
            db_session.commit()
            db_session.refresh(run)
            if run.status in {"completed", "failed", "timed_out", "cancelled"}:
                break
    finally:
        runner.close()
        for runtime in runtimes:
            runtime.close()

    nodes = db_session.query(models.AgentTaskNode).filter_by(root_task_id=run.id).all()
    by_key = {node.client_task_key: node for node in nodes}
    assert run.status == "completed", (
        [(event.event_type, event.actor_agent_id, event.summary, event.detail.get("error") if isinstance(event.detail, dict) else event.detail)
         for event in db_session.query(models.AgentTaskEvent).filter_by(root_task_id=run.id).all()
         if event.event_type in {"task.failed", "protocol.rejected", "tool.failed", "tool.denied"}],
        handled_by_agent,
    )
    assert by_key["hr-check"].parent_task_id == run.id
    assert by_key["it-check"].parent_task_id == run.id
    root_children = [node for node in nodes if node.parent_task_id == run.id]
    assert {node.assignee_agent_id for node in root_children} == {"VE-0002", "VE-0003"}
    assert len(root_children) == 2
    assert by_key["audit-check"].parent_task_id == by_key["it-check"].id
    assert by_key["audit-check"].depth == 2
    assert by_key["audit-check"].coordinator_agent_id == "VE-0003"
    # Both independent first-round assignments were persisted before either
    # root Worker completed, proving neither was dependency-blocked.
    assignments = [event for event in db_session.query(models.AgentTaskEvent).filter_by(root_task_id=run.id).all() if event.event_type == "task.assigned"]
    assert {event.target_agent_id for event in assignments} >= {"VE-0002", "VE-0003"}
    events = db_session.query(models.AgentTaskEvent).filter_by(root_task_id=run.id).all()
    direct_ids = {by_key["hr-check"].id, by_key["it-check"].id}
    direct_assignments = [event.event_seq for event in assignments if event.task_id in direct_ids]
    direct_completions = [event.event_seq for event in events if event.event_type == "task.completed" and event.task_id in direct_ids]
    assert len(direct_assignments) == 2 and len(direct_completions) == 2
    assert max(direct_assignments) < min(direct_completions)
    assert {"task.delegate", "tool.result", "task.summarizing", "task.summary"} <= {event.event_type for event in events}
    assert any(
        event.event_type == "task.delegate"
        and event.task_id == by_key["it-check"].id
        and event.actor_agent_id == "VE-0003"
        for event in events
    )
    manager_summary_events = [
        event for event in events
        if event.event_type == "task.summary"
        and event.task_id == run.id
        and event.actor_agent_id == "VE-0001"
    ]
    assert len(manager_summary_events) == 1
    assert manager_summary_events[0].detail["readiness_decision"] == "incomplete"
    assert {"agentteams-onboarding-readiness", "hr-onboarding-assistant", "it-service-assistant", "audit-evidence-review"}.issubset({skill for event in events for skill in (event.detail.get("selected_skill_ids", []) if isinstance(event.detail, dict) else [])})
    tool_events = [event for event in events if event.event_type == "tool.result"]
    assert len(tool_events) == 3
    audit_ids = [audit for event in tool_events for audit in event.detail["audit_ids"]]
    assert all(isinstance(audit, int) and audit > 0 for audit in audit_ids)
    assert db_session.query(models.AuditEvent).filter(models.AuditEvent.id.in_(audit_ids)).count() == len(audit_ids)
    messages = db_session.query(models.AgentMessage).filter_by(root_task_id=run.id).all()
    assert all(not str(message.matrix_event_id or "").startswith("local-") for message in messages)
    assert any(str(message.matrix_event_id or "").startswith("$") for message in messages)

    smoke = _load_script("run_agentteams_real_smoke.py")
    graph = service.build_graph(db_session, run.id)
    detail = graph
    evidence = smoke.build_evidence(
        run.id,
        {"ready_for_demo": True, "mode": "agentteams_strict", "transport": "matrix", "workers": [{"status": "ready"}] * 4},
        detail,
        [
            {
                "event_type": event.event_type,
                "event_seq": event.event_seq,
                "task_id": event.task_id,
                "actor_agent_id": event.actor_agent_id,
                "target_agent_id": event.target_agent_id,
                "detail": dict(event.detail or {}),
            }
            for event in events
        ],
            [
                {
                    "matrix_event_id": message.matrix_event_id,
                    "message_type": message.message_type,
                    "task_id": message.task_id,
                    "sender_agent_id": message.sender_agent_id,
                    "receiver_agent_id": message.receiver_agent_id,
                    "direction": message.direction,
                    "payload": dict(message.payload or {}),
                }
                for message in messages
            ],
        service.build_result(db_session, run.id),
    )
    assert evidence["ok"] is True, evidence["missing_criteria"]
    assert evidence["rounds"] == [1]
    worker_delegate = next(event for event in events if event.event_type == "task.delegate" and event.task_id == by_key["it-check"].id)
    assert "round" not in worker_delegate.detail

    # Replay does not reapply already-recorded Matrix protocol effects.
    completed_count = len([event for event in events if event.event_type == "task.completed"])
    runner.tick_once(db_session); db_session.commit()
    assert len([event for event in db_session.query(models.AgentTaskEvent).filter_by(root_task_id=run.id).all() if event.event_type == "task.completed"]) == completed_count
