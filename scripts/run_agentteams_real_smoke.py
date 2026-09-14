"""Run one real strict-Matrix AgentTeams task and print safe persisted evidence.

The Matrix transport, DeepSeek decisions, platform database and Gateway calls
are real only when the already-running API reports strict readiness.  Business
facts returned by the onboarding Tools remain project demo data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.agentteams_local_scenario import LOCAL_DEMO_LEADER_ID, LOCAL_DEMO_TEAM_ID  # noqa: E402


BASE_URL = os.environ.get("DWP_BASE_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")
ACTOR_NO = os.environ.get("DWP_SMOKE_ACTOR_NO", "E10281")
FIXED_GOAL = "评估新员工A是否可以按期入职。"
TERMINAL = {"completed", "failed", "timed_out", "cancelled"}
DEMO_DATA_BOUNDARY = "业务事实来自项目内演示数据；Matrix、DeepSeek、平台状态与审计需由本次严格验收确认。"
FIXED_GAP_READINESS_DECISIONS = {"not_ready", "incomplete"}
ONBOARDING_AGENT_IDS_BY_ROLE = {
    "manager": "VE-0001",
    "hr": "VE-0002",
    "it": "VE-0003",
    "audit": "VE-0005",
    "hr_material": "VE-HR-MATERIAL",
    "hr_training": "VE-HR-TRAINING",
    "it_account": "VE-IT-ACCOUNT",
    "it_device": "VE-IT-DEVICE",
}
ONBOARDING_AGENT_IDS = frozenset(ONBOARDING_AGENT_IDS_BY_ROLE.values())


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _get(client: httpx.Client, path: str, *, actor: bool = False) -> Any:
    response = client.get(f"{BASE_URL}{path}", params={"actor_no": ACTOR_NO} if actor else None)
    response.raise_for_status()
    return response.json()


def _post(client: httpx.Client, path: str, payload: dict[str, Any]) -> Any:
    response = client.post(f"{BASE_URL}{path}", params={"actor_no": ACTOR_NO}, json=payload)
    response.raise_for_status()
    return response.json()


def _is_real_event_id(value: object) -> bool:
    return isinstance(value, str) and value.startswith("$") and len(value) > 1


def _event_seq(event: dict[str, Any]) -> int | None:
    value = event.get("event_seq")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _message_content(message: dict[str, Any]) -> dict[str, Any] | None:
    """Return the protocol body from either inbound envelopes or outbound payloads."""
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None
    nested = payload.get("payload")
    if payload.get("type") == message.get("message_type") and isinstance(nested, dict):
        return nested
    return payload


def _strict_matrix_base_ready(health: object) -> bool:
    return bool(
        isinstance(health, dict)
        and health.get("ready_for_demo") is True
        and health.get("mode") == "agentteams_strict"
        and health.get("transport") == "matrix"
        and health.get("configured_team_id") == LOCAL_DEMO_TEAM_ID
        and health.get("matrix") == "ready"
        and health.get("room") == "ready"
        and health.get("manager") == "ready"
        and health.get("platform_team") == "ready"
    )


def _all_onboarding_workers_ready(health: object) -> bool:
    if not isinstance(health, dict) or not isinstance(health.get("workers"), list):
        return False
    workers = _items(health["workers"])
    if len(workers) != len(health["workers"]) or len(workers) != len(ONBOARDING_AGENT_IDS):
        return False
    agent_ids = [worker.get("agent_id") for worker in workers]
    return set(agent_ids) == ONBOARDING_AGENT_IDS and len(set(agent_ids)) == len(agent_ids) and all(
        worker.get("status") == "ready" for worker in workers
    )


def _strict_onboarding_health_ready(health: object) -> bool:
    """Require the exact TEAM-ONBOARD infrastructure and eight live runtimes."""
    return _strict_matrix_base_ready(health) and _all_onboarding_workers_ready(health)


def _has_fixed_gap_readiness_decision(
    nodes: list[dict[str, Any]], events: list[dict[str, Any]], result: dict[str, Any]
) -> bool:
    """Require matching structured decisions for this fixed missing-evidence case."""
    roots = [node for node in nodes if node.get("depth") == 0 and node.get("parent_task_id") is None]
    if len(roots) != 1 or not isinstance(roots[0].get("id"), str) or not roots[0]["id"]:
        return False
    root_id = roots[0]["id"]
    event_decisions = [
        detail.get("readiness_decision")
        for event in events
        if event.get("event_type") == "task.summary"
        and event.get("task_id") == root_id
        and event.get("actor_agent_id") == "VE-0001"
        for detail in [event.get("detail")]
        if isinstance(detail, dict)
    ]
    result_decisions = [
        payload.get("readiness_decision")
        for node in _items(result.get("node_results"))
        if node.get("id") == root_id and node.get("depth") == 0
        for payload in [node.get("output_payload")]
        if isinstance(payload, dict)
    ]
    return (
        len(event_decisions) == 1
        and len(result_decisions) == 1
        and event_decisions[0] in FIXED_GAP_READINESS_DECISIONS
        and result_decisions[0] == event_decisions[0]
    )


def _has_parallel_root_worker_release(
    nodes: list[dict[str, Any]], events: list[dict[str, Any]]
) -> bool:
    roots = [node for node in nodes if node.get("depth") == 0 and node.get("parent_task_id") is None]
    if len(roots) != 1:
        return False
    root_id = roots[0].get("id")
    first_round_keys = {
        key
        for event in events
        if event.get("event_type") == "task.delegate"
        and event.get("task_id") == root_id
        and event.get("actor_agent_id") == "VE-0001"
        and isinstance(event.get("detail"), dict)
        and event["detail"].get("round") == 1
        for key in event["detail"].get("client_task_keys", [])
        if isinstance(key, str)
    }
    direct = [
        node for node in nodes
        if node.get("parent_task_id") == root_id
        and node.get("client_task_key") in first_round_keys
        and node.get("assignee_agent_id") in {"VE-0002", "VE-0003"}
    ]
    if len(direct) != 2 or {node.get("assignee_agent_id") for node in direct} != {"VE-0002", "VE-0003"}:
        return False
    assignment_seqs: list[int] = []
    completion_seqs: list[int] = []
    for node in direct:
        node_id = node.get("id")
        assignee = node.get("assignee_agent_id")
        assigned = [
            seq for event in events
            if event.get("event_type") == "task.assigned"
            and event.get("task_id") == node_id
            and event.get("actor_agent_id") == "VE-0001"
            and event.get("target_agent_id") == assignee
            for seq in [_event_seq(event)] if seq is not None
        ]
        completed = [
            seq for event in events
            if event.get("event_type") == "task.completed" and event.get("task_id") == node_id
            for seq in [_event_seq(event)] if seq is not None
        ]
        if not assigned or not completed:
            return False
        assignment_seqs.append(min(assigned))
        completion_seqs.append(min(completed))
    return max(assignment_seqs) < min(completion_seqs)


def _has_exact_first_round_plan(
    nodes: list[dict[str, Any]], dependency_edges: Any,
    events: list[dict[str, Any]], messages: list[dict[str, Any]],
) -> bool:
    roots = [node for node in nodes if node.get("depth") == 0 and node.get("parent_task_id") is None]
    if len(roots) != 1:
        return False
    root_id = roots[0].get("id")
    root_delegations = [
        event for event in events
        if event.get("event_type") == "task.delegate"
        and event.get("task_id") == root_id
        and event.get("actor_agent_id") == "VE-0001"
        and isinstance(event.get("detail"), dict)
        and event["detail"].get("round") == 1
    ]
    if len(root_delegations) != 1:
        return False
    detail = root_delegations[0].get("detail")
    client_task_keys = detail.get("client_task_keys") if isinstance(detail, dict) else None
    if (
        not isinstance(detail, dict)
        or not isinstance(client_task_keys, list)
        or len(client_task_keys) != 2
        or any(not isinstance(key, str) or not key for key in client_task_keys)
        or len(set(client_task_keys)) != 2
    ):
        return False
    direct = [
        node for node in nodes
        if node.get("parent_task_id") == root_id
        and node.get("client_task_key") in client_task_keys
    ]
    if (
        not isinstance(root_id, str) or not root_id
        or len(direct) != 2
        or {node.get("assignee_agent_id") for node in direct} != {"VE-0002", "VE-0003"}
        or any(node.get("required") is not True for node in direct)
    ):
        return False
    direct_ids = {node.get("id") for node in direct}
    direct_client_task_keys = [node.get("client_task_key") for node in direct]
    if (
        any(not isinstance(node_id, str) or not node_id for node_id in direct_ids)
        or set(direct_client_task_keys) != set(client_task_keys)
        or not isinstance(dependency_edges, list)
        or any(not isinstance(edge, dict) for edge in dependency_edges)
        or any(edge.get("task_id") in direct_ids for edge in dependency_edges)
    ):
        return False
    delegate_messages = [
        message for message in messages
        if message.get("message_type") == "task.delegate"
        and message.get("task_id") == root_id
        and message.get("sender_agent_id") == "VE-0001"
        and message.get("receiver_agent_id") == "platform"
        and message.get("direction") == "inbound"
        and isinstance(_message_content(message), dict)
        and _message_content(message).get("round") == 1
    ]
    if len(delegate_messages) != 1:
        return False
    delegate_payload = _message_content(delegate_messages[0])
    subtasks = delegate_payload.get("subtasks") if delegate_payload is not None else None
    if not isinstance(subtasks, list) or len(subtasks) != 2 or any(not isinstance(item, dict) for item in subtasks):
        return False
    observed: dict[str, tuple[Any, Any]] = {}
    for subtask in subtasks:
        key = subtask.get("client_task_key")
        if (
            key not in client_task_keys
            or subtask.get("required") is not True
            or subtask.get("depends_on") != []
            or key in observed
        ):
            return False
        observed[key] = (subtask.get("assignee_agent_id"), subtask.get("required_capability"))
    return set(observed) == set(client_task_keys) and set(observed.values()) == {
        ("VE-0002", "hr.onboarding.coordinate"),
        ("VE-0003", "it.onboarding.coordinate"),
    }


def _positive_audit_ids(value: Any) -> set[int] | None:
    if not isinstance(value, list) or not value:
        return None
    ids = {
        item for item in value
        if isinstance(item, int) and not isinstance(item, bool) and item > 0
    }
    return ids if len(ids) == len(value) else None


def _has_expected_onboarding_tool_evidence(
    nodes: list[dict[str, Any]], events: list[dict[str, Any]], messages: list[dict[str, Any]]
) -> bool:
    expected_by_agent = {
        "VE-HR-MATERIAL": ("agentteams.hr.lookup_materials", "agentteams-onboarding-materials", "hr.materials.read"),
        "VE-HR-TRAINING": ("agentteams.hr.lookup_training", "agentteams-onboarding-training", "hr.training.read"),
        "VE-IT-ACCOUNT": ("agentteams.it.lookup_accounts", "agentteams-it-accounts", "it.accounts.read"),
        "VE-IT-DEVICE": ("agentteams.it.lookup_devices", "agentteams-it-devices", "it.devices.read"),
        "VE-0005": ("agentteams.audit.lookup_evidence", "agentteams-onboarding-evidence", "audit.evidence.read"),
    }
    tool_nodes = [
        node for node in nodes
        if node.get("assignee_agent_id") in expected_by_agent
    ]
    if {node.get("assignee_agent_id") for node in tool_nodes} != set(expected_by_agent):
        return False
    expected_requests = {
        node.get("id"): (
            node.get("assignee_agent_id"),
            *expected_by_agent[node.get("assignee_agent_id")],
        )
        for node in tool_nodes
    }
    if (
        len(expected_requests) != len(tool_nodes)
        or any(not isinstance(task_id, str) or not task_id for task_id in expected_requests)
    ):
        return False
    for task_id, (sender, tool_name, plugin_id, capability) in expected_requests.items():
        requests = [
            message for message in messages
            if message.get("message_type") == "tool.request" and message.get("task_id") == task_id
        ]
        if len(requests) != 1:
            return False
        request, payload = requests[0], _message_content(requests[0])
        if payload is None:
            return False
        if not (
            request.get("sender_agent_id") == sender
            and request.get("receiver_agent_id") == "platform"
            and request.get("direction") == "inbound"
            and payload.get("tool_name") == tool_name
            and payload.get("plugin_id") == plugin_id
            and payload.get("action") == "execute"
            and payload.get("required_capability") == capability
        ):
            return False
    event_evidence: dict[str, tuple[int, set[int]]] = {}
    for task_id, (actor, *_contract) in expected_requests.items():
        successful = [
            event for event in events
            if event.get("event_type") == "tool.result"
            and event.get("task_id") == task_id
            and event.get("actor_agent_id") == actor
            and isinstance(event.get("detail"), dict)
            and event["detail"].get("decision") == "allow"
            and _event_seq(event) is not None
        ]
        if len(successful) != 1:
            return False
        ids = _positive_audit_ids(successful[0]["detail"].get("audit_ids"))
        if ids is None:
            return False
        event_evidence[task_id] = (_event_seq(successful[0]), ids)
    evidence_sets = [value[1] for value in event_evidence.values()]
    if len(set().union(*evidence_sets)) != sum(len(value) for value in evidence_sets):
        return False
    result_payloads: dict[str, dict[str, Any]] = {}
    for task_id, (receiver, *_contract) in expected_requests.items():
        results = [
            message for message in messages
            if message.get("message_type") == "tool.result"
            and message.get("task_id") == task_id
            and message.get("sender_agent_id") == "platform"
            and message.get("receiver_agent_id") == receiver
            and message.get("direction") == "outbound"
        ]
        if len(results) != 1:
            return False
        payload = _message_content(results[0])
        if payload is None:
            return False
        if payload.get("ok") is not True or payload.get("decision") != "allow":
            return False
        if _positive_audit_ids(payload.get("audit_ids")) != event_evidence[task_id][1]:
            return False
        result_payloads[task_id] = payload
    account_task_ids = [
        task_id for task_id, (agent_id, *_contract) in expected_requests.items()
        if agent_id == "VE-IT-ACCOUNT"
    ]
    if not any(
        isinstance(data := result_payloads[task_id].get("data"), dict)
        and isinstance(accounts := data.get("accounts"), list)
        and any(
            isinstance(item, dict)
            and item.get("evidence_id") == "IT-ACC-A-002"
            and item.get("status") == "missing"
            for item in accounts
        )
        for task_id in account_task_ids
    ):
        return False
    audit_nodes = [node for node in tool_nodes if node.get("assignee_agent_id") == "VE-0005"]
    if len(audit_nodes) != 1:
        return False
    audit = audit_nodes[0]
    audit_completed = [
        seq for event in events
        if event.get("event_type") == "task.completed" and event.get("task_id") == audit.get("id")
        for seq in [_event_seq(event)] if seq is not None
    ]
    return bool(audit_completed) and event_evidence[audit.get("id")][0] < min(audit_completed)


def _has_expected_onboarding_topology(
    nodes: list[dict[str, Any]], events: list[dict[str, Any]], messages: list[dict[str, Any]]
) -> bool:
    if len(nodes) < len(ONBOARDING_AGENT_IDS):
        return False
    by_agent: dict[str, dict[str, Any]] = {}
    for node in nodes:
        agent_id = node.get("assignee_agent_id")
        if agent_id not in ONBOARDING_AGENT_IDS:
            return False
        by_agent.setdefault(agent_id, node)
    if set(by_agent) != ONBOARDING_AGENT_IDS:
        return False

    root = by_agent["VE-0001"]
    if root.get("parent_task_id") is not None or root.get("depth") != 0 or root.get("coordinator_agent_id") != "VE-0001":
        return False
    root_id = root.get("id")
    if not isinstance(root_id, str) or not root_id:
        return False

    ownership = {
        "VE-0002": ("VE-0001", 1),
        "VE-0003": ("VE-0001", 1),
        "VE-0005": ("VE-0001", 1),
        "VE-HR-MATERIAL": ("VE-0002", 2),
        "VE-HR-TRAINING": ("VE-0002", 2),
        "VE-IT-ACCOUNT": ("VE-0003", 2),
        "VE-IT-DEVICE": ("VE-0003", 2),
    }
    for agent_id, (coordinator_id, depth) in ownership.items():
        node = by_agent[agent_id]
        parent_id = by_agent[coordinator_id].get("id")
        if (
            not isinstance(node.get("id"), str)
            or not node.get("id")
            or node.get("parent_task_id") != parent_id
            or node.get("coordinator_agent_id") != coordinator_id
            or node.get("depth") != depth
            or node.get("required") is not True
        ):
            return False

    delegate_specs = (
        ("VE-0001", 1, {"VE-0002": "hr.onboarding.coordinate", "VE-0003": "it.onboarding.coordinate"}),
        ("VE-0002", None, {"VE-HR-MATERIAL": "hr.materials.read", "VE-HR-TRAINING": "hr.training.read"}),
        ("VE-0003", None, {"VE-IT-ACCOUNT": "it.accounts.read", "VE-IT-DEVICE": "it.devices.read"}),
        ("VE-0001", 2, {"VE-0005": "audit.evidence.read"}),
    )
    delegate_seqs: dict[tuple[str, int | None], int] = {}
    for sender_id, round_number, expected in delegate_specs:
        parent_task_id = by_agent[sender_id].get("id")
        matching_events = [
            event for event in events
            if event.get("event_type") == "task.delegate"
            and event.get("task_id") == parent_task_id
            and event.get("actor_agent_id") == sender_id
            and isinstance(event.get("detail"), dict)
            and event["detail"].get("round") == round_number
            and _event_seq(event) is not None
        ]
        matching_messages = [
            message for message in messages
            if message.get("message_type") == "task.delegate"
            and message.get("task_id") == parent_task_id
            and message.get("sender_agent_id") == sender_id
            and message.get("receiver_agent_id") == "platform"
            and message.get("direction") == "inbound"
            and isinstance(_message_content(message), dict)
            and _message_content(message).get("round") == round_number
        ]
        if len(matching_events) != 1 or len(matching_messages) != 1:
            return False
        payload = _message_content(matching_messages[0])
        subtasks = payload.get("subtasks") if payload is not None else None
        if not isinstance(subtasks, list) or len(subtasks) != len(expected):
            return False
        observed: dict[str, str] = {}
        for subtask in subtasks:
            if (
                not isinstance(subtask, dict)
                or subtask.get("assignee_agent_id") not in expected
                or subtask.get("required") is not True
                or subtask.get("depends_on") != []
                or subtask.get("required_capability") != expected[subtask.get("assignee_agent_id")]
            ):
                return False
            agent_id = subtask["assignee_agent_id"]
            key = subtask.get("client_task_key")
            if agent_id in observed or not isinstance(key, str) or by_agent[agent_id].get("client_task_key") != key:
                return False
            observed[agent_id] = key
        if set(observed) != set(expected):
            return False
        event_keys = matching_events[0]["detail"].get("client_task_keys")
        if event_keys is not None and set(event_keys) != set(observed.values()):
            return False
        delegate_seqs[(sender_id, round_number)] = _event_seq(matching_events[0])

    completed: dict[str, int] = {}
    for agent_id in ownership:
        task_id = by_agent[agent_id].get("id")
        sequences = [
            seq for event in events
            if event.get("event_type") == "task.completed"
            and event.get("task_id") == task_id
            and event.get("actor_agent_id") == agent_id
            for seq in [_event_seq(event)] if seq is not None
        ]
        if len(sequences) != 1:
            return False
        completed[agent_id] = sequences[0]
    summaries = [
        seq for event in events
        if event.get("event_type") == "task.summary"
        and event.get("task_id") == root_id
        and event.get("actor_agent_id") == "VE-0001"
        for seq in [_event_seq(event)] if seq is not None
    ]
    if len(summaries) != 1:
        return False
    third_round_events = [
        event for event in events
        if event.get("event_type") == "task.delegate"
        and event.get("task_id") == root_id
        and event.get("actor_agent_id") == "VE-0001"
        and isinstance(event.get("detail"), dict)
        and event["detail"].get("round") == 3
        and _event_seq(event) is not None
    ]
    third_round_messages = [
        message for message in messages
        if message.get("message_type") == "task.delegate"
        and message.get("task_id") == root_id
        and message.get("sender_agent_id") == "VE-0001"
        and message.get("receiver_agent_id") == "platform"
        and message.get("direction") == "inbound"
        and isinstance(_message_content(message), dict)
        and _message_content(message).get("round") == 3
    ]
    if len(third_round_events) != 1 or len(third_round_messages) != 1:
        return False
    third_payload = _message_content(third_round_messages[0])
    third_subtasks = third_payload.get("subtasks") if third_payload is not None else None
    if not isinstance(third_subtasks, list) or not third_subtasks:
        return False
    follow_up_keys = {
        item.get("client_task_key") for item in third_subtasks
        if isinstance(item, dict)
    }
    follow_up_leads = {
        node.get("client_task_key"): node
        for node in nodes
        if node.get("parent_task_id") == root_id
        and node.get("client_task_key") in follow_up_keys
    }
    if len(follow_up_leads) != len(third_subtasks) or len(follow_up_keys) != len(third_subtasks):
        return False
    follow_up_completion_seqs: list[int] = []
    follow_up_delegate_seqs: list[int] = []
    expected_specialists = {
        "VE-0002": {"VE-HR-MATERIAL": "hr.materials.read", "VE-HR-TRAINING": "hr.training.read"},
        "VE-0003": {"VE-IT-ACCOUNT": "it.accounts.read", "VE-IT-DEVICE": "it.devices.read"},
    }
    expected_lead_capabilities = {
        "VE-0002": "hr.onboarding.coordinate",
        "VE-0003": "it.onboarding.coordinate",
    }
    for subtask in third_subtasks:
        lead = follow_up_leads.get(subtask.get("client_task_key"))
        if not isinstance(lead, dict):
            return False
        lead_id = lead.get("assignee_agent_id")
        if (
            lead_id not in expected_specialists
            or subtask.get("assignee_agent_id") != lead_id
            or subtask.get("required_capability") != expected_lead_capabilities[lead_id]
            or subtask.get("depends_on") != []
            or subtask.get("required") is not True
            or lead.get("coordinator_agent_id") != "VE-0001"
            or lead.get("depth") != 1
            or lead.get("required") is not True
        ):
            return False
        lead_task_id = lead.get("id")
        lead_delegates = [
            seq for event in events
            if event.get("event_type") == "task.delegate"
            and event.get("task_id") == lead_task_id
            and event.get("actor_agent_id") == lead_id
            for seq in [_event_seq(event)] if seq is not None
        ]
        lead_delegate_messages = [
            message for message in messages
            if message.get("message_type") == "task.delegate"
            and message.get("task_id") == lead_task_id
            and message.get("sender_agent_id") == lead_id
            and message.get("receiver_agent_id") == "platform"
            and message.get("direction") == "inbound"
            and isinstance(_message_content(message), dict)
        ]
        children = [node for node in nodes if node.get("parent_task_id") == lead_task_id]
        lead_payload = _message_content(lead_delegate_messages[0]) if len(lead_delegate_messages) == 1 else None
        lead_subtasks = lead_payload.get("subtasks") if lead_payload is not None else None
        if (
            len(lead_delegates) != 1
            or not isinstance(lead_subtasks, list)
            or {item.get("assignee_agent_id"): item.get("required_capability") for item in lead_subtasks if isinstance(item, dict)}
               != expected_specialists[lead_id]
            or len(children) != 2
            or {node.get("assignee_agent_id") for node in children} != set(expected_specialists[lead_id])
            or any(node.get("coordinator_agent_id") != lead_id or node.get("depth") != 2 or node.get("required") is not True for node in children)
        ):
            return False
        child_completions = [
            seq for child in children
            for event in events
            if event.get("event_type") == "task.completed" and event.get("task_id") == child.get("id")
            for seq in [_event_seq(event)] if seq is not None
        ]
        lead_completions = [
            seq for event in events
            if event.get("event_type") == "task.completed" and event.get("task_id") == lead_task_id
            for seq in [_event_seq(event)] if seq is not None
        ]
        if len(child_completions) != 2 or len(lead_completions) != 1 or max(child_completions) >= lead_completions[0]:
            return False
        follow_up_delegate_seqs.extend(lead_delegates)
        follow_up_completion_seqs.extend(lead_completions)
    return (
        delegate_seqs[("VE-0001", 1)]
        < min(delegate_seqs[("VE-0002", None)], delegate_seqs[("VE-0003", None)])
        and delegate_seqs[("VE-0002", None)]
        < min(completed["VE-HR-MATERIAL"], completed["VE-HR-TRAINING"])
        and max(completed["VE-HR-MATERIAL"], completed["VE-HR-TRAINING"])
        < completed["VE-0002"]
        and delegate_seqs[("VE-0003", None)]
        < min(completed["VE-IT-ACCOUNT"], completed["VE-IT-DEVICE"])
        and max(completed["VE-IT-ACCOUNT"], completed["VE-IT-DEVICE"])
        < completed["VE-0003"]
        and max(completed["VE-0002"], completed["VE-0003"])
        < delegate_seqs[("VE-0001", 2)]
        < completed["VE-0005"]
        < _event_seq(third_round_events[0])
        < min(follow_up_delegate_seqs)
        < max(follow_up_completion_seqs)
        < summaries[0]
    )


def build_evidence(
    task_id: str,
    health: dict[str, Any],
    detail: dict[str, Any],
    events: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Validate presentation-visible persisted evidence without printing bodies."""
    nodes = _items(detail.get("nodes"))
    dependency_edges = detail.get("dependency_edges")
    skill_ids = sorted({skill for event in events for skill in (event.get("detail", {}).get("selected_skill_ids", []) if isinstance(event.get("detail"), dict) else []) if isinstance(skill, str)})
    event_ids = sorted({str(message["matrix_event_id"]) for message in messages if _is_real_event_id(message.get("matrix_event_id"))})
    audit_ids = sorted({audit for event in events for audit in (event.get("detail", {}).get("audit_ids", []) if isinstance(event.get("detail"), dict) else []) if isinstance(audit, int) and audit > 0})
    participants = sorted({str(node.get("assignee_agent_id")) for node in nodes if node.get("assignee_agent_id")})
    depths = [node.get("depth") for node in nodes if isinstance(node.get("depth"), int)]
    rounds = [
        round_number
        for event in events
        if event.get("event_type") == "task.delegate"
        and isinstance(event.get("detail"), dict)
        for round_number in [event["detail"].get("round")]
        if isinstance(round_number, int) and not isinstance(round_number, bool) and round_number > 0
    ]
    missing: list[str] = []
    if not _strict_matrix_base_ready(health):
        missing.append("strict_matrix_health")
    if not _all_onboarding_workers_ready(health):
        missing.append("agent_heartbeats")
    if result.get("status") != "completed":
        missing.append("completed_result")
    if not str(result.get("summary") or "").strip():
        missing.append("final_summary")
    if not _has_fixed_gap_readiness_decision(nodes, events, result):
        missing.append("fixed_gap_readiness_decision")
    if set(participants) != ONBOARDING_AGENT_IDS:
        missing.append("expanded_onboarding_participants")
    if not any(depth >= 2 for depth in depths):
        missing.append("depth_two_delegation")
    if not _has_expected_onboarding_topology(nodes, events, messages):
        missing.append("onboarding_collaboration_topology")
    if not _has_exact_first_round_plan(nodes, dependency_edges, events, messages):
        missing.append("first_round_parallel_plan")
    if not _has_parallel_root_worker_release(nodes, events):
        missing.append("parallel_root_worker_release")
    if not _has_expected_onboarding_tool_evidence(nodes, events, messages):
        missing.append("onboarding_tool_evidence")
    if not skill_ids or "agentteams-onboarding-readiness" not in skill_ids:
        missing.append("selected_skills")
    if sorted(set(rounds)) != [1, 2, 3]:
        missing.append("delegation_round")
    if len(audit_ids) < 5:
        missing.append("governed_tool_audits")
    if len(event_ids) < 8:
        missing.append("real_matrix_event_ids")
    if not any(
        event.get("event_type") == "task.summary"
        and event.get("actor_agent_id") == "VE-0001"
        for event in events
    ):
        missing.append("manager_review")
    return {
        "ok": not missing,
        "task_id": task_id,
        "participant_ids": participants,
        "matrix_event_ids": event_ids,
        "selected_skill_ids": skill_ids,
        "audit_ids": audit_ids,
        "rounds": rounds,
        # The persisted final summary proves completion, but its arbitrary text
        # is never an operator-script output channel.  Reporting only this
        # whitelist boolean avoids credential-shaped data leaking through an
        # unexpected model or service response.
        "final_summary_present": bool(str(result.get("summary") or "").strip()),
        "data_boundary": DEMO_DATA_BOUNDARY,
        "missing_criteria": missing,
    }


def _safe_empty(reason: str) -> dict[str, Any]:
    return {"ok": False, "task_id": "", "participant_ids": [], "matrix_event_ids": [], "selected_skill_ids": [], "audit_ids": [], "rounds": [], "final_summary_present": False, "data_boundary": DEMO_DATA_BOUNDARY, "missing_criteria": [reason]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one strict Matrix / DeepSeek AgentTeams smoke task")
    parser.add_argument("--goal", default=FIXED_GOAL)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args(argv)
    if args.goal != FIXED_GOAL or not 1 <= args.timeout_seconds <= 600:
        print(json.dumps(_safe_empty("invalid_real_smoke_arguments"), ensure_ascii=False))
        return 2
    loopback = urlsplit(BASE_URL).hostname in {"127.0.0.1", "localhost", "::1"}
    with httpx.Client(timeout=20.0, trust_env=not loopback) as client:
        try:
            health = _get(client, "/agentteams-collaboration/health")
            if not _strict_onboarding_health_ready(health):
                print(json.dumps(_safe_empty("strict_matrix_not_ready"), ensure_ascii=False))
                return 2
            task = _post(client, "/agentteams-collaboration/tasks", {"team_id": LOCAL_DEMO_TEAM_ID, "leader_agent_id": LOCAL_DEMO_LEADER_ID, "title": "新员工A按期入职评估", "instruction": FIXED_GOAL, "material": {}})
            task_id = str(task.get("id") or "") if isinstance(task, dict) else ""
            if not task_id:
                print(json.dumps(_safe_empty("task_id_missing"), ensure_ascii=False))
                return 1
            deadline = time.monotonic() + args.timeout_seconds
            detail: dict[str, Any] = {}
            while time.monotonic() < deadline:
                candidate = _get(client, f"/agentteams-collaboration/tasks/{task_id}", actor=True)
                detail = candidate if isinstance(candidate, dict) else {}
                if detail.get("status") in TERMINAL:
                    break
                time.sleep(2)
            graph = _get(client, f"/agentteams-collaboration/tasks/{task_id}/graph", actor=True)
            if isinstance(graph, dict):
                detail = {**detail, "dependency_edges": graph.get("dependency_edges")}
            events = _items(_get(client, f"/agentteams-collaboration/tasks/{task_id}/events", actor=True))
            messages = _items(_get(client, f"/agentteams-collaboration/tasks/{task_id}/messages", actor=True))
            result = _get(client, f"/agentteams-collaboration/tasks/{task_id}/result", actor=True)
        except (httpx.HTTPError, TypeError, ValueError):
            print(json.dumps(_safe_empty("safe_http_failure"), ensure_ascii=False))
            return 1
    evidence = build_evidence(task_id, health, detail, events, messages, result if isinstance(result, dict) else {})
    print(json.dumps(evidence, ensure_ascii=False))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
