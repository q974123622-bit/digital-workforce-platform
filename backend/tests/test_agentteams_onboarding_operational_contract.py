"""Operational contracts for the expanded strict Matrix onboarding team."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.services.agentteams_runtime_config import MatrixIdentity, StrictRuntimeConfig


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_AGENTS = {
    "manager": "VE-0001",
    "hr": "VE-0002",
    "it": "VE-0003",
    "audit": "VE-0005",
    "hr_material": "VE-HR-MATERIAL",
    "hr_training": "VE-HR-TRAINING",
    "it_account": "VE-IT-ACCOUNT",
    "it_device": "VE-IT-DEVICE",
}


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_runtime_document() -> dict:
    return {
        "matrix_url": "http://matrix.test",
        "room_id": "!team:matrix.test",
        "team_id": "TEAM-ONBOARD",
        "platform": {"mxid": "@platform:matrix.test", "token": "platform-token"},
        "agents": {
            role: {
                "agent_id": agent_id,
                "mxid": f"@{role}:matrix.test",
                "token": f"{role}-token",
                "allowed_skill_ids": [f"{role}-skill"],
                "safe_tool_catalog": [],
            }
            for role, agent_id in list(EXPECTED_AGENTS.items())[:4]
        },
        "team_catalog": {},
    }


def test_strict_parser_rejects_legacy_four_role_onboarding_document(tmp_path):
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(_legacy_runtime_document()), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid_runtime_config"):
        load_strict_runtime_config(path, environ={})


def test_matrix_verifier_rejects_legacy_four_role_onboarding_before_network_io():
    verifier = _load_script("verify_agentteams_matrix.py")
    document = _legacy_runtime_document()
    identities = {
        name: MatrixIdentity(
            value["mxid"], value["token"], value.get("agent_id"), (), (),
        )
        for name, value in {"platform": document["platform"], **document["agents"]}.items()
    }
    runtime = StrictRuntimeConfig(
        document["matrix_url"], document["room_id"], identities["platform"],
        {name: identities[name] for name in document["agents"]}, "TEAM-ONBOARD", {},
    )

    evidence = verifier.verify_matrix_environment(
        runtime,
        gateway_factory=lambda *_args: pytest.fail("legacy contract must fail before Matrix I/O"),
        pause=lambda _seconds: None,
    )

    assert evidence == {
        "ok": False,
        "accounts": [],
        "event_ids": {},
        "reason": "invalid_runtime_identities",
    }


def _strict_health(*, roles=EXPECTED_AGENTS) -> dict:
    return {
        "ready_for_demo": True,
        "mode": "agentteams_strict",
        "transport": "matrix",
        "configured_team_id": "TEAM-ONBOARD",
        "matrix": "ready",
        "room": "ready",
        "manager": "ready",
        "platform_team": "ready",
        "workers": [
            {"agent_id": agent_id, "status": "ready"}
            for agent_id in roles.values()
        ],
    }


def test_real_smoke_health_preflight_requires_all_eight_named_agents():
    smoke = _load_script("run_agentteams_real_smoke.py")

    assert smoke._strict_onboarding_health_ready(_strict_health()) is True
    assert smoke._strict_onboarding_health_ready(
        _strict_health(roles=dict(list(EXPECTED_AGENTS.items())[:4]))
    ) is False


def _expanded_tree():
    nodes = [
        {"id": "root", "parent_task_id": None, "assignee_agent_id": "VE-0001", "coordinator_agent_id": "VE-0001", "depth": 0},
        {"id": "hr", "client_task_key": "hr", "parent_task_id": "root", "assignee_agent_id": "VE-0002", "coordinator_agent_id": "VE-0001", "depth": 1, "required": True},
        {"id": "it", "client_task_key": "it", "parent_task_id": "root", "assignee_agent_id": "VE-0003", "coordinator_agent_id": "VE-0001", "depth": 1, "required": True},
        {"id": "hr-material", "client_task_key": "hr-material", "parent_task_id": "hr", "assignee_agent_id": "VE-HR-MATERIAL", "coordinator_agent_id": "VE-0002", "depth": 2, "required": True},
        {"id": "hr-training", "client_task_key": "hr-training", "parent_task_id": "hr", "assignee_agent_id": "VE-HR-TRAINING", "coordinator_agent_id": "VE-0002", "depth": 2, "required": True},
        {"id": "it-account", "client_task_key": "it-account", "parent_task_id": "it", "assignee_agent_id": "VE-IT-ACCOUNT", "coordinator_agent_id": "VE-0003", "depth": 2, "required": True},
        {"id": "it-device", "client_task_key": "it-device", "parent_task_id": "it", "assignee_agent_id": "VE-IT-DEVICE", "coordinator_agent_id": "VE-0003", "depth": 2, "required": True},
        {"id": "audit", "client_task_key": "audit", "parent_task_id": "root", "assignee_agent_id": "VE-0005", "coordinator_agent_id": "VE-0001", "depth": 1, "required": True},
        {"id": "it-gap", "client_task_key": "it-gap", "parent_task_id": "root", "assignee_agent_id": "VE-0003", "coordinator_agent_id": "VE-0001", "depth": 1, "required": True},
        {"id": "it-account-recheck", "client_task_key": "it-account-recheck", "parent_task_id": "it-gap", "assignee_agent_id": "VE-IT-ACCOUNT", "coordinator_agent_id": "VE-0003", "depth": 2, "required": True},
        {"id": "it-device-recheck", "client_task_key": "it-device-recheck", "parent_task_id": "it-gap", "assignee_agent_id": "VE-IT-DEVICE", "coordinator_agent_id": "VE-0003", "depth": 2, "required": True},
    ]
    events = [
        {"event_seq": 1, "event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {"round": 1, "client_task_keys": ["hr", "it"]}},
        {"event_seq": 2, "event_type": "task.assigned", "task_id": "hr", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0002"},
        {"event_seq": 3, "event_type": "task.assigned", "task_id": "it", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0003"},
        {"event_seq": 4, "event_type": "task.delegate", "task_id": "hr", "actor_agent_id": "VE-0002", "detail": {}},
        {"event_seq": 5, "event_type": "task.delegate", "task_id": "it", "actor_agent_id": "VE-0003", "detail": {}},
        {"event_seq": 6, "event_type": "task.completed", "task_id": "hr-material", "actor_agent_id": "VE-HR-MATERIAL"},
        {"event_seq": 7, "event_type": "task.completed", "task_id": "hr-training", "actor_agent_id": "VE-HR-TRAINING"},
        {"event_seq": 8, "event_type": "task.completed", "task_id": "it-account", "actor_agent_id": "VE-IT-ACCOUNT"},
        {"event_seq": 9, "event_type": "task.completed", "task_id": "it-device", "actor_agent_id": "VE-IT-DEVICE"},
        {"event_seq": 10, "event_type": "task.completed", "task_id": "hr", "actor_agent_id": "VE-0002"},
        {"event_seq": 11, "event_type": "task.completed", "task_id": "it", "actor_agent_id": "VE-0003"},
        {"event_seq": 12, "event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {"round": 2, "client_task_keys": ["audit"]}},
        {"event_seq": 13, "event_type": "task.assigned", "task_id": "audit", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0005"},
        {"event_seq": 14, "event_type": "task.completed", "task_id": "audit", "actor_agent_id": "VE-0005"},
        {"event_seq": 15, "event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {"round": 3, "client_task_keys": ["it-gap"]}},
        {"event_seq": 16, "event_type": "task.assigned", "task_id": "it-gap", "actor_agent_id": "VE-0001", "target_agent_id": "VE-0003"},
        {"event_seq": 17, "event_type": "task.delegate", "task_id": "it-gap", "actor_agent_id": "VE-0003", "detail": {}},
        {"event_seq": 18, "event_type": "task.completed", "task_id": "it-account-recheck", "actor_agent_id": "VE-IT-ACCOUNT"},
        {"event_seq": 19, "event_type": "task.completed", "task_id": "it-device-recheck", "actor_agent_id": "VE-IT-DEVICE"},
        {"event_seq": 20, "event_type": "task.completed", "task_id": "it-gap", "actor_agent_id": "VE-0003"},
        {"event_seq": 21, "event_type": "task.summary", "task_id": "root", "actor_agent_id": "VE-0001"},
    ]
    messages = [
        {"message_type": "task.delegate", "task_id": "root", "sender_agent_id": "VE-0001", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"round": 1, "subtasks": [
            {"client_task_key": "hr", "assignee_agent_id": "VE-0002", "required_capability": "hr.onboarding.coordinate", "depends_on": [], "required": True},
            {"client_task_key": "it", "assignee_agent_id": "VE-0003", "required_capability": "it.onboarding.coordinate", "depends_on": [], "required": True},
        ]}},
        {"message_type": "task.delegate", "task_id": "hr", "sender_agent_id": "VE-0002", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"subtasks": [
            {"client_task_key": "hr-material", "assignee_agent_id": "VE-HR-MATERIAL", "required_capability": "hr.materials.read", "depends_on": [], "required": True},
            {"client_task_key": "hr-training", "assignee_agent_id": "VE-HR-TRAINING", "required_capability": "hr.training.read", "depends_on": [], "required": True},
        ]}},
        {"message_type": "task.delegate", "task_id": "it", "sender_agent_id": "VE-0003", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"subtasks": [
            {"client_task_key": "it-account", "assignee_agent_id": "VE-IT-ACCOUNT", "required_capability": "it.accounts.read", "depends_on": [], "required": True},
            {"client_task_key": "it-device", "assignee_agent_id": "VE-IT-DEVICE", "required_capability": "it.devices.read", "depends_on": [], "required": True},
        ]}},
        {"message_type": "task.delegate", "task_id": "root", "sender_agent_id": "VE-0001", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"round": 2, "subtasks": [
            {"client_task_key": "audit", "assignee_agent_id": "VE-0005", "required_capability": "audit.evidence.read", "depends_on": [], "required": True},
        ]}},
        {"message_type": "task.delegate", "task_id": "root", "sender_agent_id": "VE-0001", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"round": 3, "subtasks": [
            {"client_task_key": "it-gap", "assignee_agent_id": "VE-0003", "required_capability": "it.onboarding.coordinate", "depends_on": [], "required": True},
        ]}},
        {"message_type": "task.delegate", "task_id": "it-gap", "sender_agent_id": "VE-0003", "receiver_agent_id": "platform", "direction": "inbound", "payload": {"subtasks": [
            {"client_task_key": "it-account-recheck", "assignee_agent_id": "VE-IT-ACCOUNT", "required_capability": "it.accounts.read", "depends_on": [], "required": True},
            {"client_task_key": "it-device-recheck", "assignee_agent_id": "VE-IT-DEVICE", "required_capability": "it.devices.read", "depends_on": [], "required": True},
        ]}},
    ]
    return nodes, events, messages


def test_real_smoke_accepts_expanded_tree_with_audit_and_targeted_third_round():
    smoke = _load_script("run_agentteams_real_smoke.py")
    nodes, events, messages = _expanded_tree()

    assert smoke._has_expected_onboarding_topology(nodes, events, messages) is True


@pytest.mark.parametrize(
    ("node_id", "new_parent", "new_coordinator"),
    [
        ("audit", "it", "VE-0003"),
        ("hr-training", "root", "VE-0001"),
        ("it-device", "hr", "VE-0002"),
    ],
)
def test_real_smoke_rejects_expanded_tree_with_wrong_ownership(
    node_id, new_parent, new_coordinator,
):
    smoke = _load_script("run_agentteams_real_smoke.py")
    nodes, events, messages = _expanded_tree()
    node = next(item for item in nodes if item["id"] == node_id)
    node["parent_task_id"] = new_parent
    node["coordinator_agent_id"] = new_coordinator

    assert smoke._has_expected_onboarding_topology(nodes, events, messages) is False
