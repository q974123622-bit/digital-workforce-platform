"""Run one real AgentTeams collaboration loop and print minimal safe evidence.

This script never starts the backend or Matrix.  It is intentionally an
operator-triggered check against an already-running, configured backend.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx


BASE_URL = os.environ.get("DWP_BASE_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")
TERMINAL_STATUSES = {"completed", "failed", "timed_out", "cancelled"}
FIXED_TASK = {
    "team_id": "TEAM-ONBOARD",
    "leader_agent_id": "DT-E10281",
    "title": "新员工入职准备复核",
    "instruction": (
        "请协作完成新员工入职准备复核：HR核查制度和材料，IT核查账号与服务状态，"
        "至少一次跨Agent澄清或继续委派，最后汇总完成项、风险和后续动作。"
    ),
    "material": {"employee": "待入职员工", "scenario": "smoke"},
}


def _empty_evidence(*missing_criteria: str) -> dict[str, Any]:
    return {
        "ok": False,
        "task_id": "",
        "participant_ids": [],
        "matrix_event_ids": {},
        "summary": "",
        "missing_criteria": list(missing_criteria),
    }


def _print_evidence(evidence: dict[str, Any]) -> None:
    """Do not print request payloads, health details, credentials, or message bodies."""
    printable = {
        key: evidence[key]
        for key in ("task_id", "participant_ids", "matrix_event_ids", "summary", "missing_criteria")
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))


def _get_json(client: httpx.Client, path: str) -> Any:
    response = client.get(f"{BASE_URL}{path}")
    response.raise_for_status()
    return response.json()


def _post_json(client: httpx.Client, path: str, payload: dict[str, Any]) -> Any:
    response = client.post(f"{BASE_URL}{path}", json=payload)
    response.raise_for_status()
    return response.json()


def _as_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _is_received_room_message(message: dict[str, Any]) -> bool:
    return (
        message.get("direction") == "inbound"
        and message.get("delivery_status") == "received"
        and bool(str(message.get("matrix_room_id") or "").strip())
        and bool(str(message.get("matrix_event_id") or "").strip())
    )


def _has_protocol_summary(message: dict[str, Any]) -> bool:
    if isinstance(message.get("body"), str) and message["body"].strip():
        return True
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return False
    summary = payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        return True
    protocol_payload = payload.get("payload")
    return (
        isinstance(protocol_payload, dict)
        and isinstance(protocol_payload.get("summary"), str)
        and bool(protocol_payload["summary"].strip())
    )


def wait_for_terminal(client: httpx.Client, task_id: str, timeout_seconds: int = 600) -> dict[str, Any]:
    """Poll persisted task state for no longer than the documented smoke window."""
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    while True:
        value = _get_json(client, f"/agentteams-collaboration/tasks/{task_id}")
        latest = value if isinstance(value, dict) else {}
        if latest.get("status") in TERMINAL_STATUSES or time.monotonic() >= deadline:
            return latest
        time.sleep(2)


def build_evidence(
    task_id: str,
    detail: dict[str, Any],
    events: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Check the persisted evidence required for a real, multi-Worker loop."""
    nodes = _as_list(detail.get("nodes"))
    children = [node for node in nodes if node.get("parent_task_id")]
    root = next((node for node in nodes if not node.get("parent_task_id")), {})
    manager_id = str(root.get("assignee_agent_id") or "").strip()
    child_assignees = {
        str(node.get("assignee_agent_id") or "").strip()
        for node in children
        if str(node.get("assignee_agent_id") or "").strip()
    }
    worker_assignees = child_assignees - {manager_id}
    worker_senders = {
        str(message.get("sender_agent_id") or "").strip()
        for message in messages
        if _is_received_room_message(message)
        and str(message.get("sender_agent_id") or "").strip() in worker_assignees
    }
    matrix_event_ids: dict[str, list[str]] = {}
    for sender in sorted(worker_senders):
        matrix_event_ids[sender] = sorted({
            str(message.get("matrix_event_id") or "").strip()
            for message in messages
            if _is_received_room_message(message)
            and str(message.get("sender_agent_id") or "").strip() == sender
        })
    manager_summaries = [
        message for message in messages
        if _is_received_room_message(message)
        and message.get("message_type") == "task.summary"
        and str(message.get("sender_agent_id") or "").strip() == manager_id
        and _has_protocol_summary(message)
    ]
    if manager_summaries:
        matrix_event_ids[manager_id] = sorted({
            str(message.get("matrix_event_id") or "").strip() for message in manager_summaries
        })

    missing = []
    if result.get("status") != "completed":
        missing.append("任务未以completed终态结束")
    summary = str(result.get("summary") or "").strip()
    if not summary:
        missing.append("缺少非空任务汇总")
    if len(children) < 2:
        missing.append("少于两个子任务节点")
    if len(worker_senders) < 2:
        missing.append("少于两个不同的子任务Worker发送者")
    if any(not matrix_event_ids[sender] for sender in worker_senders):
        missing.append("参与的Worker缺少Matrix事件ID")
    if not manager_summaries:
        missing.append("缺少Manager的入站task.summary Matrix证据")
    if not any(event.get("event_type") == "task.progress" for event in events):
        missing.append("缺少task.progress事件")
    has_agent_message = any(
        _is_received_room_message(message) and message.get("message_type") == "agent.message"
        for message in messages
    )
    has_nested_delegate = any(
        isinstance(node.get("depth"), int) and node["depth"] >= 2 for node in nodes
    )
    if not (has_agent_message or has_nested_delegate):
        missing.append("缺少agent.message或二级委派节点")

    return {
        "ok": not missing,
        "task_id": task_id,
        "participant_ids": sorted(worker_senders | ({manager_id} if manager_summaries else set())),
        "matrix_event_ids": matrix_event_ids,
        "summary": summary,
        "missing_criteria": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a configured AgentTeams collaboration smoke check without starting services."
    )
    parser.parse_args()
    with httpx.Client(timeout=20.0) as client:
        try:
            health = _get_json(client, "/agentteams-collaboration/health")
        except (httpx.HTTPError, TypeError, ValueError):
            _print_evidence(_empty_evidence("预检健康检查不可用"))
            return 2
        if not isinstance(health, dict):
            _print_evidence(_empty_evidence("预检健康检查响应无效"))
            return 2
        if health.get("ready_for_demo") is not True:
            _print_evidence(_empty_evidence("预检未就绪"))
            return 2

        try:
            task = _post_json(client, "/agentteams-collaboration/tasks", FIXED_TASK)
            task_id = str(task.get("id") or "").strip() if isinstance(task, dict) else ""
            if not task_id:
                _print_evidence(_empty_evidence("任务创建响应缺少任务ID"))
                return 1

            wait_for_terminal(client, task_id, timeout_seconds=600)
            detail = _get_json(client, f"/agentteams-collaboration/tasks/{task_id}")
            events = _get_json(client, f"/agentteams-collaboration/tasks/{task_id}/events")
            messages = _get_json(client, f"/agentteams-collaboration/tasks/{task_id}/messages")
            result = _get_json(client, f"/agentteams-collaboration/tasks/{task_id}/result")
        except (httpx.HTTPError, TypeError, ValueError):
            _print_evidence(_empty_evidence("运行期间后端响应无效"))
            return 1

    evidence = build_evidence(
        task_id,
        detail if isinstance(detail, dict) else {},
        _as_list(events),
        _as_list(messages),
        result if isinstance(result, dict) else {},
    )
    _print_evidence(evidence)
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
