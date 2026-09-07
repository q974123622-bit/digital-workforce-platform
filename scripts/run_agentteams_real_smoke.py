"""Run one real strict-Matrix AgentTeams task and print safe persisted evidence.

The Matrix transport, DeepSeek decisions, platform database and Gateway calls
are real only when the already-running API reports strict readiness.  Employee
facts returned by the three onboarding Tools remain project demo data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

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


def _has_parallel_root_worker_release(
    nodes: list[dict[str, Any]], events: list[dict[str, Any]]
) -> bool:
    roots = [node for node in nodes if node.get("depth") == 0 and node.get("parent_task_id") is None]
    if len(roots) != 1:
        return False
    root_id = roots[0].get("id")
    direct = [
        node for node in nodes
        if node.get("parent_task_id") == root_id and node.get("assignee_agent_id") in {"VE-0002", "VE-0003"}
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
    direct = [node for node in nodes if node.get("parent_task_id") == root_id]
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
        or any(not isinstance(key, str) or not key for key in direct_client_task_keys)
        or len(set(direct_client_task_keys)) != len(direct_client_task_keys)
        or not isinstance(dependency_edges, list)
        or any(not isinstance(edge, dict) for edge in dependency_edges)
        or any(edge.get("task_id") in direct_ids for edge in dependency_edges)
    ):
        return False
    root_delegations = [
        event for event in events
        if event.get("event_type") == "task.delegate"
        and event.get("task_id") == root_id
        and event.get("actor_agent_id") == "VE-0001"
    ]
    if len(root_delegations) != 1:
        return False
    detail = root_delegations[0].get("detail")
    client_task_keys = detail.get("client_task_keys") if isinstance(detail, dict) else None
    if (
        not isinstance(detail, dict)
        or detail.get("round") != 1
        or not isinstance(client_task_keys, list)
        or len(client_task_keys) != 2
        or any(not isinstance(key, str) or not key for key in client_task_keys)
        or len(set(client_task_keys)) != 2
        or set(client_task_keys) != set(direct_client_task_keys)
    ):
        return False
    delegate_messages = [
        message for message in messages
        if message.get("message_type") == "task.delegate"
        and message.get("task_id") == root_id
        and message.get("sender_agent_id") == "VE-0001"
        and message.get("receiver_agent_id") == "platform"
        and message.get("direction") == "inbound"
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
        ("VE-0002", "hr.onboarding.read"),
        ("VE-0003", "it.readiness.read"),
    }


def _positive_audit_ids(value: Any) -> set[int] | None:
    if not isinstance(value, list) or not value:
        return None
    ids = {
        item for item in value
        if isinstance(item, int) and not isinstance(item, bool) and item > 0
    }
    return ids if len(ids) == len(value) else None


def _has_expected_it_audit_tool_evidence(
    nodes: list[dict[str, Any]], events: list[dict[str, Any]], messages: list[dict[str, Any]]
) -> bool:
    by_agent = {node.get("assignee_agent_id"): node for node in nodes}
    it, audit = by_agent.get("VE-0003"), by_agent.get("VE-0005")
    if not isinstance(it, dict) or not isinstance(audit, dict):
        return False
    expected_requests = {
        it.get("id"): ("VE-0003", "agentteams.it.lookup_readiness", "agentteams-it-readiness", "it.readiness.read"),
        audit.get("id"): ("VE-0005", "agentteams.audit.lookup_evidence", "agentteams-onboarding-evidence", "audit.evidence.read"),
    }
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
    for task_id, actor in ((it.get("id"), "VE-0003"), (audit.get("id"), "VE-0005")):
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
    it_ids, audit_ids = event_evidence[it.get("id")][1], event_evidence[audit.get("id")][1]
    if not it_ids.isdisjoint(audit_ids):
        return False
    result_payloads: dict[str, dict[str, Any]] = {}
    for task_id, receiver in ((it.get("id"), "VE-0003"), (audit.get("id"), "VE-0005")):
        results = [
            message for message in messages
            if message.get("message_type") == "tool.result"
            and message.get("task_id") == task_id
            and message.get("sender_agent_id") == "platform"
            and message.get("receiver_agent_id") == receiver
            and message.get("direction") == "outbound"
        ]
        if len(results) != 1 or not isinstance(results[0].get("payload"), dict):
            return False
        payload = results[0]["payload"]
        if payload.get("ok") is not True or payload.get("decision") != "allow":
            return False
        if _positive_audit_ids(payload.get("audit_ids")) != event_evidence[task_id][1]:
            return False
        result_payloads[task_id] = payload
    data = result_payloads[it.get("id")].get("data")
    pending = data.get("pending_items") if isinstance(data, dict) else None
    if not isinstance(pending, list) or not any(
        isinstance(item, dict) and item.get("evidence_id") == "IT-A-003" and item.get("status") == "missing"
        for item in pending
    ):
        return False
    audit_completed = [
        seq for event in events
        if event.get("event_type") == "task.completed" and event.get("task_id") == audit.get("id")
        for seq in [_event_seq(event)] if seq is not None
    ]
    return bool(audit_completed) and event_evidence[audit.get("id")][0] < min(audit_completed)


def _has_expected_onboarding_topology(
    nodes: list[dict[str, Any]], events: list[dict[str, Any]]
) -> bool:
    roots = [node for node in nodes if node.get("depth") == 0 and node.get("parent_task_id") is None]
    if (
        len(roots) != 1
        or roots[0].get("assignee_agent_id") != "VE-0001"
        or roots[0].get("coordinator_agent_id") != "VE-0001"
    ):
        return False
    root_id = roots[0].get("id")
    if not isinstance(root_id, str) or not root_id:
        return False
    direct = [node for node in nodes if node.get("parent_task_id") == root_id]
    if (
        len(direct) != 2
        or {node.get("assignee_agent_id") for node in direct} != {"VE-0002", "VE-0003"}
        or any(node.get("depth") != 1 or node.get("coordinator_agent_id") != "VE-0001" for node in direct)
    ):
        return False
    hr = next(node for node in direct if node.get("assignee_agent_id") == "VE-0002")
    it = next(node for node in direct if node.get("assignee_agent_id") == "VE-0003")
    audit_nodes = [node for node in nodes if node.get("assignee_agent_id") == "VE-0005"]
    depth_two = [node for node in nodes if node.get("depth") == 2]
    if len(audit_nodes) != 1 or len(depth_two) != 1 or depth_two[0].get("id") != audit_nodes[0].get("id"):
        return False
    audit = audit_nodes[0]
    if not (
        audit.get("parent_task_id") == it.get("id")
        and audit.get("depth") == 2
        and audit.get("coordinator_agent_id") == "VE-0003"
    ):
        return False
    root_delegations = [
        event for event in events
        if event.get("event_type") == "task.delegate"
        and event.get("task_id") == root_id
        and event.get("actor_agent_id") == "VE-0001"
        and isinstance(event.get("detail"), dict)
        and event["detail"].get("round") == 1
        and _event_seq(event) is not None
    ]
    it_delegate_seqs = [
        seq for event in events
        if event.get("event_type") == "task.delegate"
        and event.get("task_id") == it.get("id")
        and event.get("actor_agent_id") == "VE-0003"
        for seq in [_event_seq(event)] if seq is not None
    ]
    if not root_delegations or not it_delegate_seqs:
        return False
    it_delegate_seq = min(it_delegate_seqs)
    successful_it_tool_seqs = [
        seq for event in events
        if event.get("event_type") == "tool.result"
        and event.get("task_id") == it.get("id")
        and event.get("actor_agent_id") == "VE-0003"
        and isinstance(event.get("detail"), dict)
        and event["detail"].get("decision") == "allow"
        and isinstance(event["detail"].get("audit_ids"), list)
        and any(isinstance(audit_id, int) and not isinstance(audit_id, bool) and audit_id > 0 for audit_id in event["detail"].get("audit_ids", []))
        for seq in [_event_seq(event)] if seq is not None
    ]
    audit_completed = [
        seq for event in events
        if event.get("event_type") == "task.completed" and event.get("task_id") == audit.get("id")
        for seq in [_event_seq(event)] if seq is not None
    ]
    hr_completed = [
        seq for event in events
        if event.get("event_type") == "task.completed" and event.get("task_id") == hr.get("id")
        for seq in [_event_seq(event)] if seq is not None
    ]
    it_completed = [
        seq for event in events
        if event.get("event_type") == "task.completed" and event.get("task_id") == it.get("id")
        for seq in [_event_seq(event)] if seq is not None
    ]
    manager_summaries = [
        seq for event in events
        if event.get("event_type") == "task.summary"
        and event.get("task_id") == root_id
        and event.get("actor_agent_id") == "VE-0001"
        for seq in [_event_seq(event)] if seq is not None
    ]
    if not all((successful_it_tool_seqs, audit_completed, hr_completed, it_completed, manager_summaries)):
        return False
    ordered_worker_evidence = (
        min(successful_it_tool_seqs) < it_delegate_seq < min(audit_completed) < min(it_completed)
        and max(min(hr_completed), min(it_completed)) < min(manager_summaries)
    )
    it_delegated_audit = any(
        event.get("event_type") == "task.delegate"
        and event.get("task_id") == it.get("id")
        and event.get("actor_agent_id") == "VE-0003"
        for event in events
    )
    manager_reviewed = any(
        event.get("event_type") == "task.summary"
        and event.get("task_id") == root_id
        and event.get("actor_agent_id") == "VE-0001"
        for event in events
    )
    return it_delegated_audit and manager_reviewed and ordered_worker_evidence


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
    if not (health.get("ready_for_demo") is True and health.get("mode") == "agentteams_strict" and health.get("transport") == "matrix"):
        missing.append("strict_matrix_health")
    workers = health.get("workers")
    if not isinstance(workers, list) or len(workers) < 4 or any(item.get("status") != "ready" for item in _items(workers)):
        missing.append("agent_heartbeats")
    if result.get("status") != "completed":
        missing.append("completed_result")
    if not str(result.get("summary") or "").strip():
        missing.append("final_summary")
    if not {"VE-0001", "VE-0002", "VE-0003", "VE-0005"}.issubset(set(participants)):
        missing.append("manager_hr_it_audit_participants")
    if not any(depth >= 2 for depth in depths):
        missing.append("depth_two_delegation")
    if not _has_expected_onboarding_topology(nodes, events):
        missing.append("onboarding_collaboration_topology")
    if not _has_exact_first_round_plan(nodes, dependency_edges, events, messages):
        missing.append("first_round_parallel_plan")
    if not _has_parallel_root_worker_release(nodes, events):
        missing.append("parallel_root_worker_release")
    if not _has_expected_it_audit_tool_evidence(nodes, events, messages):
        missing.append("it_audit_tool_evidence")
    if not skill_ids or "agentteams-onboarding-readiness" not in skill_ids:
        missing.append("selected_skills")
    if not rounds:
        missing.append("delegation_round")
    if len(audit_ids) < 3:
        missing.append("governed_tool_audits")
    if len(event_ids) < 4:
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
    with httpx.Client(timeout=20.0) as client:
        try:
            health = _get(client, "/agentteams-collaboration/health")
            if not isinstance(health, dict) or not (health.get("ready_for_demo") is True and health.get("mode") == "agentteams_strict" and health.get("transport") == "matrix"):
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
