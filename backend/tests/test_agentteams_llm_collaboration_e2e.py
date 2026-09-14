"""Task 9 contracts for the deterministic and real-only AgentTeams verifiers."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from app import models, schemas
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


def test_matrix_verifier_uses_nine_real_format_events_without_exposing_tokens():
    matrix = _load_script("verify_agentteams_matrix.py")
    identities = {
        "platform": MatrixIdentity("@platform:matrix.test", "platform-secret"),
        "manager": MatrixIdentity("@manager:matrix.test", "manager-secret", "VE-0001", ("agentteams-onboarding-readiness",), ()),
        "hr": MatrixIdentity("@hr:matrix.test", "hr-secret", "VE-0002", ("hr-onboarding-assistant",), ()),
        "it": MatrixIdentity("@it:matrix.test", "it-secret", "VE-0003", ("it-service-assistant",), ()),
        "audit": MatrixIdentity("@audit:matrix.test", "audit-secret", "VE-0005", ("audit-evidence-review",), ()),
        "hr_material": MatrixIdentity("@hr-material:matrix.test", "hr-material-secret", "VE-HR-MATERIAL", (), ()),
        "hr_training": MatrixIdentity("@hr-training:matrix.test", "hr-training-secret", "VE-HR-TRAINING", (), ()),
        "it_account": MatrixIdentity("@it-account:matrix.test", "it-account-secret", "VE-IT-ACCOUNT", (), ()),
        "it_device": MatrixIdentity("@it-device:matrix.test", "it-device-secret", "VE-IT-DEVICE", (), ()),
    }
    runtime = StrictRuntimeConfig("https://matrix.test", "!task9:matrix.test", identities["platform"], {key: value for key, value in identities.items() if key != "platform"}, "TEAM-ONBOARD", {})
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
        "hr_material": MatrixIdentity("@hr-material:matrix.test", "hr-material-secret", "VE-HR-MATERIAL", (), ()),
        "hr_training": MatrixIdentity("@hr-training:matrix.test", "hr-training-secret", "VE-HR-TRAINING", (), ()),
        "it_account": MatrixIdentity("@it-account:matrix.test", "it-account-secret", "VE-IT-ACCOUNT", (), ()),
        "it_device": MatrixIdentity("@it-device:matrix.test", "it-device-secret", "VE-IT-DEVICE", (), ()),
    }
    runtime = StrictRuntimeConfig("https://matrix.test", "!task9:matrix.test", identities["platform"], {name: value for name, value in identities.items() if name != "platform"}, "TEAM-ONBOARD", {})
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
        "hr_material": MatrixIdentity("@hr-material:matrix.test", "hr-material-secret", "VE-HR-MATERIAL", (), ()),
        "hr_training": MatrixIdentity("@hr-training:matrix.test", "hr-training-secret", "VE-HR-TRAINING", (), ()),
        "it_account": MatrixIdentity("@it-account:matrix.test", "it-account-secret", "VE-IT-ACCOUNT", (), ()),
        "it_device": MatrixIdentity("@it-device:matrix.test", "it-device-secret", "VE-IT-DEVICE", (), ()),
    }
    runtime = StrictRuntimeConfig("https://matrix.test", "!task9:matrix.test", identities["platform"], {name: value for name, value in identities.items() if name != "platform"}, "TEAM-ONBOARD", {})
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
        "configured_team_id": "TEAM-ONBOARD", "matrix": "ready", "room": "ready",
        "manager": "ready", "platform_team": "ready",
        "workers": [
            {"agent_id": agent_id, "status": "ready"}
            for agent_id in (
                "VE-0001", "VE-0002", "VE-0003", "VE-0005",
                "VE-HR-MATERIAL", "VE-HR-TRAINING", "VE-IT-ACCOUNT", "VE-IT-DEVICE",
            )
        ],
    }
    detail = {"nodes": [
        {"id": "root", "client_task_key": "root", "parent_task_id": None, "assignee_agent_id": "VE-0001", "coordinator_agent_id": "VE-0001", "depth": 0},
        {"id": "hr", "client_task_key": "hr-check", "parent_task_id": "root", "assignee_agent_id": "VE-0002", "coordinator_agent_id": "VE-0001", "depth": 1, "required": True},
        {"id": "it", "client_task_key": "it-check", "parent_task_id": "root", "assignee_agent_id": "VE-0003", "coordinator_agent_id": "VE-0001", "depth": 1, "required": True},
        {"id": "hr-material", "client_task_key": "hr-material", "parent_task_id": "hr", "assignee_agent_id": "VE-HR-MATERIAL", "coordinator_agent_id": "VE-0002", "depth": 2, "required": True},
        {"id": "hr-training", "client_task_key": "hr-training", "parent_task_id": "hr", "assignee_agent_id": "VE-HR-TRAINING", "coordinator_agent_id": "VE-0002", "depth": 2, "required": True},
        {"id": "it-account", "client_task_key": "it-account", "parent_task_id": "it", "assignee_agent_id": "VE-IT-ACCOUNT", "coordinator_agent_id": "VE-0003", "depth": 2, "required": True},
        {"id": "it-device", "client_task_key": "it-device", "parent_task_id": "it", "assignee_agent_id": "VE-IT-DEVICE", "coordinator_agent_id": "VE-0003", "depth": 2, "required": True},
        {"id": "audit", "client_task_key": "audit-check", "parent_task_id": "root", "assignee_agent_id": "VE-0005", "coordinator_agent_id": "VE-0001", "depth": 1, "required": True},
    ], "dependency_edges": []}
    events = [
        {"event_seq": 1, "event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0001", "detail": {"round": 1, "client_task_keys": ["hr-check", "it-check"], "selected_skill_ids": ["agentteams-onboarding-readiness"]}},
        {"event_seq": 2, "event_type": "task.assigned", "task_id": "hr", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0002", "detail": {"parent_task_id": "root"}},
        {"event_seq": 3, "event_type": "task.assigned", "task_id": "it", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0003", "detail": {"parent_task_id": "root"}},
        {"event_seq": 4, "event_type": "task.delegate", "task_id": "hr", "actor_agent_id": "VE-0002", "detail": {"client_task_keys": ["hr-material", "hr-training"]}},
        {"event_seq": 5, "event_type": "task.delegate", "task_id": "it", "actor_agent_id": "VE-0003", "detail": {"client_task_keys": ["it-account", "it-device"]}},
        {"event_seq": 6, "event_type": "tool.result", "task_id": "hr-material", "actor_agent_id": "VE-HR-MATERIAL", "detail": {"decision": "allow", "audit_ids": [11]}},
        {"event_seq": 7, "event_type": "task.completed", "task_id": "hr-material", "actor_agent_id": "VE-HR-MATERIAL", "detail": {}},
        {"event_seq": 8, "event_type": "tool.result", "task_id": "hr-training", "actor_agent_id": "VE-HR-TRAINING", "detail": {"decision": "allow", "audit_ids": [12]}},
        {"event_seq": 9, "event_type": "task.completed", "task_id": "hr-training", "actor_agent_id": "VE-HR-TRAINING", "detail": {}},
        {"event_seq": 10, "event_type": "tool.result", "task_id": "it-account", "actor_agent_id": "VE-IT-ACCOUNT", "detail": {"decision": "allow", "audit_ids": [13]}},
        {"event_seq": 11, "event_type": "task.completed", "task_id": "it-account", "actor_agent_id": "VE-IT-ACCOUNT", "detail": {}},
        {"event_seq": 12, "event_type": "tool.result", "task_id": "it-device", "actor_agent_id": "VE-IT-DEVICE", "detail": {"decision": "allow", "audit_ids": [14]}},
        {"event_seq": 13, "event_type": "task.completed", "task_id": "it-device", "actor_agent_id": "VE-IT-DEVICE", "detail": {}},
        {"event_seq": 14, "event_type": "task.completed", "task_id": "hr", "actor_agent_id": "VE-0002", "detail": {}},
        {"event_seq": 15, "event_type": "task.completed", "task_id": "it", "actor_agent_id": "VE-0003", "detail": {}},
        {"event_seq": 16, "event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {"round": 2, "client_task_keys": ["audit-check"], "selected_skill_ids": ["agentteams-onboarding-readiness"]}},
        {"event_seq": 17, "event_type": "tool.result", "task_id": "audit", "actor_agent_id": "VE-0005", "detail": {"decision": "allow", "audit_ids": [15]}},
        {"event_seq": 18, "event_type": "task.completed", "task_id": "audit", "actor_agent_id": "VE-0005", "detail": {}},
        {"event_seq": 19, "event_type": "task.summary", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {"readiness_decision": "incomplete"}},
    ]
    reviewers = {
        "hr": ("VE-0001", "VE-0002"),
        "it": ("VE-0001", "VE-0003"),
        "hr-material": ("VE-0002", "VE-HR-MATERIAL"),
        "hr-training": ("VE-0002", "VE-HR-TRAINING"),
        "it-account": ("VE-0003", "VE-IT-ACCOUNT"),
        "it-device": ("VE-0003", "VE-IT-DEVICE"),
        "audit": ("VE-0001", "VE-0005"),
    }
    reviewed_events = []
    for event in events:
        if event.get("event_type") == "task.completed" and event.get("task_id") in reviewers:
            reviewer, assignee = reviewers[event["task_id"]]
            reviewed_events.append({
                "event_type": "task.result.accepted",
                "task_id": event["task_id"],
                "actor_agent_id": reviewer,
                "target_agent_id": assignee,
                "detail": {"feedback": "验收通过"},
            })
            event["actor_agent_id"] = reviewer
            event["target_agent_id"] = assignee
        reviewed_events.append(event)
    events = reviewed_events
    for event_seq, event in enumerate(events, start=1):
        event["event_seq"] = event_seq
    messages = [
        {"matrix_event_id": "$delegate-root-1", "message_type": "task.delegate", "task_id": "root", "sender_agent_id": "VE-0001", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"round": 1, "subtasks": [
            {"client_task_key": "hr-check", "assignee_agent_id": "VE-0002", "required_capability": "hr.onboarding.coordinate", "depends_on": [], "required": True},
            {"client_task_key": "it-check", "assignee_agent_id": "VE-0003", "required_capability": "it.onboarding.coordinate", "depends_on": [], "required": True},
        ]}},
        {"matrix_event_id": "$delegate-hr", "message_type": "task.delegate", "task_id": "hr", "sender_agent_id": "VE-0002", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"subtasks": [
            {"client_task_key": "hr-material", "assignee_agent_id": "VE-HR-MATERIAL", "required_capability": "hr.materials.read", "depends_on": [], "required": True},
            {"client_task_key": "hr-training", "assignee_agent_id": "VE-HR-TRAINING", "required_capability": "hr.training.read", "depends_on": [], "required": True},
        ]}},
        {"matrix_event_id": "$delegate-it", "message_type": "task.delegate", "task_id": "it", "sender_agent_id": "VE-0003", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"subtasks": [
            {"client_task_key": "it-account", "assignee_agent_id": "VE-IT-ACCOUNT", "required_capability": "it.accounts.read", "depends_on": [], "required": True},
            {"client_task_key": "it-device", "assignee_agent_id": "VE-IT-DEVICE", "required_capability": "it.devices.read", "depends_on": [], "required": True},
        ]}},
        {"matrix_event_id": "$delegate-root-2", "message_type": "task.delegate", "task_id": "root", "sender_agent_id": "VE-0001", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"round": 2, "subtasks": [
            {"client_task_key": "audit-check", "assignee_agent_id": "VE-0005", "required_capability": "audit.evidence.read", "depends_on": [], "required": True},
        ]}},
    ]
    tools = {
        "hr-material": ("VE-HR-MATERIAL", "agentteams.hr.lookup_materials", "agentteams-onboarding-materials", "hr.materials.read", 11, {}),
        "hr-training": ("VE-HR-TRAINING", "agentteams.hr.lookup_training", "agentteams-onboarding-training", "hr.training.read", 12, {}),
        "it-account": ("VE-IT-ACCOUNT", "agentteams.it.lookup_accounts", "agentteams-it-accounts", "it.accounts.read", 13, {"accounts": [{"evidence_id": "IT-ACC-A-002", "status": "missing"}]}),
        "it-device": ("VE-IT-DEVICE", "agentteams.it.lookup_devices", "agentteams-it-devices", "it.devices.read", 14, {}),
        "audit": ("VE-0005", "agentteams.audit.lookup_evidence", "agentteams-onboarding-evidence", "audit.evidence.read", 15, {}),
    }
    for task_id, (agent_id, tool_name, plugin_id, capability, audit_id, data) in tools.items():
        messages.extend([
            {"matrix_event_id": f"$request-{task_id}", "message_type": "tool.request", "task_id": task_id, "sender_agent_id": agent_id, "receiver_agent_id": "platform", "direction": "inbound", "payload": {"tool_name": tool_name, "plugin_id": plugin_id, "action": "execute", "required_capability": capability}},
            {"matrix_event_id": f"$result-{task_id}", "message_type": "tool.result", "task_id": task_id, "sender_agent_id": "platform", "receiver_agent_id": agent_id, "direction": "outbound", "payload": {"ok": True, "decision": "allow", "audit_ids": [audit_id], "data": data}},
        ])
    result = {
        "status": "completed",
        "summary": "done",
        "node_results": [{"id": "root", "depth": 0, "output_payload": {"readiness_decision": "incomplete"}}],
    }
    return health, detail, events, messages, result


@pytest.mark.parametrize(
    ("base_url", "trust_env"),
    [("http://127.0.0.1:8000/api/v1", False), ("https://dwp.example/api/v1", True)],
)
def test_real_smoke_only_bypasses_process_proxy_for_loopback_backend(
    monkeypatch, capsys, base_url, trust_env,
):
    smoke = _load_script("run_agentteams_real_smoke.py")
    captured = {}

    class Client:
        def __init__(self, **kwargs): captured.update(kwargs)
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    smoke.BASE_URL = base_url
    monkeypatch.setattr(smoke.httpx, "Client", Client)
    monkeypatch.setattr(smoke, "_get", lambda *_args, **_kwargs: {})

    assert smoke.main([]) == 2
    capsys.readouterr()
    assert captured["trust_env"] is trust_env


@pytest.mark.parametrize("readiness_decision", ["not_ready", "incomplete"])
def test_real_smoke_accepts_expanded_departments_then_manager_audit(readiness_decision):
    smoke = _load_script("run_agentteams_real_smoke.py")
    health, detail, events, messages, result = _valid_real_smoke_evidence_inputs()
    events[-1]["detail"]["readiness_decision"] = readiness_decision
    result["node_results"][0]["output_payload"]["readiness_decision"] = readiness_decision

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
        result["node_results"][0]["output_payload"]["readiness_decision"] = "ready"

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


@pytest.mark.parametrize("case", ["wrong-account-tool", "account-gap-absent", "wrong-audit-tool"])
def test_real_smoke_rejects_false_positive_tool_evidence(case):
    smoke = _load_script("run_agentteams_real_smoke.py")
    health, detail, events, messages, result = deepcopy(_valid_real_smoke_evidence_inputs())
    account_request = next(message for message in messages if message.get("task_id") == "it-account" and message.get("message_type") == "tool.request")
    account_result = next(message for message in messages if message.get("task_id") == "it-account" and message.get("message_type") == "tool.result")
    audit_request = next(message for message in messages if message.get("task_id") == "audit" and message.get("message_type") == "tool.request")
    if case == "wrong-account-tool":
        account_request["payload"]["plugin_id"] = "agentteams-onboarding-employee"
    elif case == "account-gap-absent":
        account_result["payload"]["data"]["accounts"][0]["status"] = "ready"
    else:
        audit_request["payload"]["tool_name"] = "agentteams.it.lookup_devices"

    evidence = smoke.build_evidence("AT-invalid-tools", health, detail, events, messages, result)

    assert evidence["ok"] is False
    assert "onboarding_tool_evidence" in evidence["missing_criteria"]


def test_real_smoke_rejects_root_workers_released_after_one_already_completed():
    smoke = _load_script("run_agentteams_real_smoke.py")
    health, detail, events, messages, result = deepcopy(_valid_real_smoke_evidence_inputs())
    first_root_completion = min(
        event["event_seq"]
        for event in events
        if event.get("event_type") == "task.completed"
        and event.get("task_id") in {"hr", "it"}
    )
    next(
        event for event in events
        if event.get("event_type") == "task.assigned"
        and event.get("task_id") == "it"
    )["event_seq"] = first_root_completion + 1

    evidence = smoke.build_evidence(
        "AT-serial-release", health, detail, events, messages, result,
    )

    assert evidence["ok"] is False
    assert "parallel_root_worker_release" in evidence["missing_criteria"]


@pytest.mark.parametrize("case", ["specialist-before-lead-delegate", "specialist-after-lead", "audit-round-before-leads", "summary-before-audit"])
def test_real_smoke_rejects_out_of_order_expanded_tree(case):
    smoke = _load_script("run_agentteams_real_smoke.py")
    _health, detail, events, messages, _result = deepcopy(_valid_real_smoke_evidence_inputs())
    if case == "specialist-before-lead-delegate":
        next(event for event in events if event.get("task_id") == "hr-material" and event.get("event_type") == "task.completed")["event_seq"] = 3
    elif case == "specialist-after-lead":
        hr_completion = next(
            event["event_seq"] for event in events
            if event.get("task_id") == "hr"
            and event.get("event_type") == "task.completed"
        )
        next(event for event in events if event.get("task_id") == "hr-training" and event.get("event_type") == "task.completed")["event_seq"] = hr_completion + 1
    elif case == "audit-round-before-leads":
        next(event for event in events if event.get("task_id") == "root" and event.get("detail", {}).get("round") == 2)["event_seq"] = 13
    else:
        next(event for event in events if event.get("event_type") == "task.summary")["event_seq"] = 17

    assert smoke._has_expected_onboarding_topology(detail["nodes"], events, messages) is False


def test_real_smoke_rejects_audit_delegated_by_it_instead_of_manager():
    smoke = _load_script("run_agentteams_real_smoke.py")
    _health, detail, events, messages, _result = deepcopy(_valid_real_smoke_evidence_inputs())
    audit = next(node for node in detail["nodes"] if node.get("assignee_agent_id") == "VE-0005")
    audit["parent_task_id"] = "it"
    audit["coordinator_agent_id"] = "VE-0003"

    assert smoke._has_expected_onboarding_topology(detail["nodes"], events, messages) is False


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
            return deepcopy(_valid_real_smoke_evidence_inputs()[0])
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


def test_real_smoke_rejects_expanded_tree_missing_one_specialist():
    smoke = _load_script("run_agentteams_real_smoke.py")
    health, detail, events, messages, result = deepcopy(_valid_real_smoke_evidence_inputs())
    detail["nodes"] = [
        node for node in detail["nodes"]
        if node.get("assignee_agent_id") != "VE-HR-TRAINING"
    ]

    evidence = smoke.build_evidence("AT-wrong-topology", health, detail, events, messages, result)

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
            {"client_task_key": "hr-check", "title": "HR协调核查", "goal": "协调核查新员工A的材料与培训。", "required_capability": "hr.onboarding.coordinate", "assignee_agent_id": "VE-0002", "depends_on": [], "required": True, "reason": "需要HR负责人协调专业核查。"},
            {"client_task_key": "it-check", "title": "IT协调核查", "goal": "协调核查新员工A的账号和设备。", "required_capability": "it.onboarding.coordinate", "assignee_agent_id": "VE-0003", "depends_on": [], "required": True, "reason": "需要IT负责人协调专业核查。"},
        ]},
        {"action": "accept", "feedback": "HR负责人结果证据完整，验收通过。"},
        {"action": "accept", "feedback": "IT负责人结果证据完整，验收通过。"},
        {"selected_skill_ids": ["agentteams-onboarding-readiness"], "action": "delegate", "subtasks": [
            {"client_task_key": "audit-check", "title": "独立审计核查", "goal": "复核HR和IT返回的入职证据。", "required_capability": "audit.evidence.read", "assignee_agent_id": "VE-0005", "depends_on": [], "required": True, "reason": "部门核查完成后需要独立审计。"},
        ]},
        {"action": "accept", "feedback": "独立审计结果可追溯，验收通过。"},
        {"selected_skill_ids": ["agentteams-onboarding-readiness"], "action": "summarize", "summary": {"summary": "当前不能确认按期入职，IT账号证据缺失仍需跟进。", "readiness_decision": "incomplete", "completed_items": ["HR、IT、审计均已核查"], "risks": ["多因素认证证据缺失"], "unknowns": [], "next_actions": ["补齐IT账号证据"]}},
    ]
    hr = [
        {"selected_skill_ids": ["hr-onboarding-assistant"]},
        {"action": "delegate", "subtasks": [
            {"client_task_key": "hr-material", "title": "HR材料核查", "goal": "核查新员工A的入职材料。", "required_capability": "hr.materials.read", "assignee_agent_id": "VE-HR-MATERIAL", "depends_on": [], "required": True, "reason": "由材料专员核查。"},
            {"client_task_key": "hr-training", "title": "HR培训核查", "goal": "核查新员工A的培训安排。", "required_capability": "hr.training.read", "assignee_agent_id": "VE-HR-TRAINING", "depends_on": [], "required": True, "reason": "由培训专员核查。"},
        ]},
        {"action": "accept", "feedback": "材料核查结果完整，验收通过。"},
        {"action": "accept", "feedback": "培训核查结果完整，验收通过。"},
        {"action": "complete", "result": "HR材料与培训已完成专业核查。"},
    ]
    it = [
        {"selected_skill_ids": ["it-service-assistant"]},
        {"action": "delegate", "subtasks": [
            {"client_task_key": "it-account", "title": "IT账号核查", "goal": "核查新员工A的账号。", "required_capability": "it.accounts.read", "assignee_agent_id": "VE-IT-ACCOUNT", "depends_on": [], "required": True, "reason": "由账号专员核查。"},
            {"client_task_key": "it-device", "title": "IT设备核查", "goal": "核查新员工A的设备。", "required_capability": "it.devices.read", "assignee_agent_id": "VE-IT-DEVICE", "depends_on": [], "required": True, "reason": "由设备专员核查。"},
        ]},
        {"action": "accept", "feedback": "账号核查结果完整，验收通过。"},
        {"action": "accept", "feedback": "设备核查结果完整，验收通过。"},
        {"action": "complete", "result": "IT账号与设备已完成专业核查。"},
    ]
    audit = [
        {"selected_skill_ids": ["audit-evidence-review"]},
        {"action": "tool", "tool_name": "agentteams.audit.lookup_evidence", "tool_arguments": {"employee_ref": "新员工A"}},
        {"action": "complete", "result": "审计证据已复核，存在待归档项。"},
    ]
    specialists = {
        "hr_material": [
            {"selected_skill_ids": ["hr-material-specialist"]},
            {"action": "tool", "tool_name": "agentteams.hr.lookup_materials", "tool_arguments": {"employee_ref": "新员工A"}},
            {"action": "complete", "result": "HR材料已核查。"},
        ],
        "hr_training": [
            {"selected_skill_ids": ["hr-training-specialist"]},
            {"action": "tool", "tool_name": "agentteams.hr.lookup_training", "tool_arguments": {"employee_ref": "新员工A"}},
            {"action": "complete", "result": "HR培训已核查。"},
        ],
        "it_account": [
            {"selected_skill_ids": ["it-account-specialist"]},
            {"action": "tool", "tool_name": "agentteams.it.lookup_accounts", "tool_arguments": {"employee_ref": "新员工A"}},
            {"action": "complete", "result": "IT账号已核查，存在认证缺口。"},
        ],
        "it_device": [
            {"selected_skill_ids": ["it-device-specialist"]},
            {"action": "tool", "tool_name": "agentteams.it.lookup_devices", "tool_arguments": {"employee_ref": "新员工A"}},
            {"action": "complete", "result": "IT设备已核查。"},
        ],
    }
    return {"manager": manager, "hr": hr, "it": it, "audit": audit, **specialists}


def _strict_test_runtime_config(mxids):
    schema = {
        "type": "object", "required": ["employee_ref"],
        "properties": {"employee_ref": {"type": "string", "const": "新员工A", "maxLength": 16}},
        "additionalProperties": False,
    }
    role_contracts = {
        "manager": ("VE-0001", "agentteams-onboarding-readiness", None),
        "hr": ("VE-0002", "hr-onboarding-assistant", ("agentteams.hr.coordinate_onboarding", "agentteams-onboarding-employee", "hr.onboarding.coordinate")),
        "it": ("VE-0003", "it-service-assistant", ("agentteams.it.coordinate_onboarding", "agentteams-it-readiness", "it.onboarding.coordinate")),
        "audit": ("VE-0005", "audit-evidence-review", ("agentteams.audit.lookup_evidence", "agentteams-onboarding-evidence", "audit.evidence.read")),
        "hr_material": ("VE-HR-MATERIAL", "hr-material-specialist", ("agentteams.hr.lookup_materials", "agentteams-onboarding-materials", "hr.materials.read")),
        "hr_training": ("VE-HR-TRAINING", "hr-training-specialist", ("agentteams.hr.lookup_training", "agentteams-onboarding-training", "hr.training.read")),
        "it_account": ("VE-IT-ACCOUNT", "it-account-specialist", ("agentteams.it.lookup_accounts", "agentteams-it-accounts", "it.accounts.read")),
        "it_device": ("VE-IT-DEVICE", "it-device-specialist", ("agentteams.it.lookup_devices", "agentteams-it-devices", "it.devices.read")),
    }
    agents = {}
    team_catalog = {}
    for role, (agent_id, skill_id, tool) in role_contracts.items():
        agents[role] = {
            "agent_id": agent_id, "mxid": mxids[agent_id], "token": f"test-{role}",
            "allowed_skill_ids": [skill_id],
            "safe_tool_catalog": [] if tool is None else [{
                "name": tool[0], "plugin_id": tool[1], "action": "execute",
                "required_capability": tool[2], "input_schema": schema,
            }],
        }
        team_catalog[agent_id] = {"capabilities": [] if tool is None else [tool[2]]}
    return {
        "matrix_url": "http://matrix.test", "room_id": "!task9:matrix.test", "team_id": "TEAM-ONBOARD",
        "platform": {"mxid": mxids["platform"], "token": "test-platform"},
        "agents": agents,
        "team_catalog": team_catalog,
        "team_relationships": {
            "VE-0001": {"role": "Manager", "reports_to": [], "direct_reports": ["VE-0002", "VE-0003", "VE-0005"]},
            "VE-0002": {"role": "HR负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-HR-MATERIAL", "VE-HR-TRAINING"]},
            "VE-0003": {"role": "IT负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-IT-ACCOUNT", "VE-IT-DEVICE"]},
            "VE-0005": {"role": "审计负责人", "reports_to": ["VE-0001"], "direct_reports": []},
            "VE-HR-MATERIAL": {"role": "HR材料专员", "reports_to": ["VE-0002"], "direct_reports": []},
            "VE-HR-TRAINING": {"role": "HR培训专员", "reports_to": ["VE-0002"], "direct_reports": []},
            "VE-IT-ACCOUNT": {"role": "IT账号专员", "reports_to": ["VE-0003"], "direct_reports": []},
            "VE-IT-DEVICE": {"role": "IT设备专员", "reports_to": ["VE-0003"], "direct_reports": []},
        },
    }


def test_fake_llm_drives_real_runtime_runner_gateway_policy_audit_state_machine(db_session, monkeypatch, tmp_path):
    """A complete adaptive run must not rely on the local-demo fixed transport."""
    monkeypatch.setenv("DWP_AGENTTEAMS_MATRIX_DOMAIN", "matrix.test")
    mxids = {
        "platform": "@platform:matrix.test", "VE-0001": "@manager:matrix.test",
        "VE-0002": "@hr:matrix.test", "VE-0003": "@it:matrix.test",
        "VE-0005": "@audit:matrix.test", "VE-HR-MATERIAL": "@hr-material:matrix.test",
        "VE-HR-TRAINING": "@hr-training:matrix.test", "VE-IT-ACCOUNT": "@it-account:matrix.test",
        "VE-IT-DEVICE": "@it-device:matrix.test",
    }
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
    assert {"hr.onboarding.coordinate"}.issubset(capability_catalog["VE-0002"])
    assert {"it.onboarding.coordinate"}.issubset(capability_catalog["VE-0003"])
    assert {"audit.evidence.read"}.issubset(capability_catalog["VE-0005"])
    assert capability_catalog["VE-HR-MATERIAL"] == {"hr.materials.read"}
    assert capability_catalog["VE-HR-TRAINING"] == {"hr.training.read"}
    assert capability_catalog["VE-IT-ACCOUNT"] == {"it.accounts.read"}
    assert capability_catalog["VE-IT-DEVICE"] == {"it.devices.read"}
    run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title="新员工A按期入职评估",
        instruction="评估新员工A是否可以按期入职。",
        material={},
        owner_human_no="E10281",
    )
    db_session.commit()
    runner = AgentTeamsCollaborationRunner(service=service, gateway=bus.endpoint(mxids["platform"]), room_id="!task9:matrix.test")
    assert runner.transport_mode == "agentteams_strict"
    registry = ProjectSkillRegistry.from_root(ROOT)
    outputs_by_role = _decision_outputs()
    configs = [
        ("VE-0001", "manager", {"agentteams-manager-coordination", "agentteams-onboarding-readiness"}, [], outputs_by_role["manager"]),
        ("VE-0002", "hr", {"employee-collaboration", "hr-onboarding-assistant"}, [{"name": "agentteams.hr.coordinate_onboarding", "plugin_id": "agentteams-onboarding-employee", "action": "execute", "required_capability": "hr.onboarding.coordinate"}], outputs_by_role["hr"]),
        ("VE-0003", "it", {"employee-collaboration", "it-service-assistant"}, [{"name": "agentteams.it.coordinate_onboarding", "plugin_id": "agentteams-it-readiness", "action": "execute", "required_capability": "it.onboarding.coordinate"}], outputs_by_role["it"]),
        ("VE-0005", "audit", {"employee-collaboration", "audit-evidence-review"}, [{"name": "agentteams.audit.lookup_evidence", "plugin_id": "agentteams-onboarding-evidence", "action": "execute", "required_capability": "audit.evidence.read"}], outputs_by_role["audit"]),
        ("VE-HR-MATERIAL", "hr_material", {"employee-collaboration", "hr-material-specialist"}, [{"name": "agentteams.hr.lookup_materials", "plugin_id": "agentteams-onboarding-materials", "action": "execute", "required_capability": "hr.materials.read"}], outputs_by_role["hr_material"]),
        ("VE-HR-TRAINING", "hr_training", {"employee-collaboration", "hr-training-specialist"}, [{"name": "agentteams.hr.lookup_training", "plugin_id": "agentteams-onboarding-training", "action": "execute", "required_capability": "hr.training.read"}], outputs_by_role["hr_training"]),
        ("VE-IT-ACCOUNT", "it_account", {"employee-collaboration", "it-account-specialist"}, [{"name": "agentteams.it.lookup_accounts", "plugin_id": "agentteams-it-accounts", "action": "execute", "required_capability": "it.accounts.read"}], outputs_by_role["it_account"]),
        ("VE-IT-DEVICE", "it_device", {"employee-collaboration", "it-device-specialist"}, [{"name": "agentteams.it.lookup_devices", "plugin_id": "agentteams-it-devices", "action": "execute", "required_capability": "it.devices.read"}], outputs_by_role["it_device"]),
    ]
    runtimes = []
    trusted = dict(mxids)
    for agent_id, role, skills, tools, outputs in configs:
        runtime_config = AgentRuntimeConfig(agent_id=agent_id, role=role, matrix_mxid=mxids[agent_id], matrix_token=f"test-{role}", room_id="!task9:matrix.test", trusted_sender_mxids=trusted, allowed_skill_ids=skills, safe_tool_catalog=tools, state_dir=tmp_path / role, pause_seconds=0)
        runtimes.append(AgentRuntime(runtime_config, bus.endpoint(mxids[agent_id]), AgentDecisionEngine(_ScriptedLLM(outputs), registry), pause=lambda _: None))

    handled_by_agent = {runtime.config.agent_id: 0 for runtime in runtimes}
    try:
        for _ in range(80):
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
        [(event.event_type, event.actor_agent_id, event.summary, event.detail)
         for event in db_session.query(models.AgentTaskEvent).filter_by(root_task_id=run.id).all()
         if event.event_type in {"task.failed", "protocol.rejected", "tool.failed", "tool.denied"}],
        handled_by_agent,
        [(node.client_task_key, node.status) for node in nodes],
        [
            (
                item.task_id,
                item.message_type,
                item.sender_agent_id,
                item.receiver_agent_id,
                item.payload,
            )
            for item in db_session.query(models.AgentMessage).filter_by(
                root_task_id=run.id
            ).all()
            if item.message_type.startswith("task.result")
        ],
    )
    assert by_key["hr-check"].parent_task_id == run.id
    assert by_key["it-check"].parent_task_id == run.id
    root_children = [node for node in nodes if node.parent_task_id == run.id]
    assert {node.assignee_agent_id for node in root_children} == {"VE-0002", "VE-0003", "VE-0005"}
    assert len(root_children) == 3
    assert {node.assignee_agent_id for node in nodes if node.parent_task_id == by_key["hr-check"].id} == {"VE-HR-MATERIAL", "VE-HR-TRAINING"}
    assert {node.assignee_agent_id for node in nodes if node.parent_task_id == by_key["it-check"].id} == {"VE-IT-ACCOUNT", "VE-IT-DEVICE"}
    assert by_key["audit-check"].parent_task_id == run.id
    assert by_key["audit-check"].depth == 1
    assert by_key["audit-check"].coordinator_agent_id == "VE-0001"
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
    assert all(any(
        event.event_type == "task.delegate"
        and event.task_id == by_key[lead_key].id
        and event.actor_agent_id == lead_id
        for event in events
    ) for lead_key, lead_id in (("hr-check", "VE-0002"), ("it-check", "VE-0003")))
    manager_summary_events = [
        event for event in events
        if event.event_type == "task.summary"
        and event.task_id == run.id
        and event.actor_agent_id == "VE-0001"
    ]
    assert len(manager_summary_events) == 1
    assert manager_summary_events[0].detail["readiness_decision"] == "incomplete"
    assert {
        "agentteams-onboarding-readiness", "hr-onboarding-assistant", "it-service-assistant",
        "audit-evidence-review", "hr-material-specialist", "hr-training-specialist",
        "it-account-specialist", "it-device-specialist",
    }.issubset({skill for event in events for skill in (event.detail.get("selected_skill_ids", []) if isinstance(event.detail, dict) else [])})
    tool_events = [event for event in events if event.event_type == "tool.result"]
    assert len(tool_events) == 5
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
        _valid_real_smoke_evidence_inputs()[0],
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
            {
                "task_id": run.id,
                "status": by_key["root"].status,
                "summary": run.summary,
                "node_results": [
                    schemas.AgentTaskNodeOut.model_validate(node).model_dump(mode="json")
                    for node in nodes
                ],
            },
        )
    assert evidence["ok"] is True, evidence["missing_criteria"]
    assert evidence["rounds"] == [1, 2]
    worker_delegates = [event for event in events if event.event_type == "task.delegate" and event.task_id in {by_key["hr-check"].id, by_key["it-check"].id}]
    assert len(worker_delegates) == 2
    assert all("round" not in event.detail for event in worker_delegates)

    # Replay does not reapply already-recorded Matrix protocol effects.
    completed_count = len([event for event in events if event.event_type == "task.completed"])
    runner.tick_once(db_session); db_session.commit()
    assert len([event for event in db_session.query(models.AgentTaskEvent).filter_by(root_task_id=run.id).all() if event.event_type == "task.completed"]) == completed_count
