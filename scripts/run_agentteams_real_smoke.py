"""Run one real strict-Matrix AgentTeams task and print safe persisted evidence.

The Matrix transport, DeepSeek decisions, platform database and Gateway calls
are real only when the already-running API reports strict readiness.  Employee
facts returned by the three onboarding Tools remain project demo data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(?:api[_-]?key|token|password|authorization|credential)\s*[:=]\s*\S+"
)


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


def _safe_summary(value: object) -> str:
    """Keep the presentation summary useful without exposing accidental secrets."""
    return _SENSITIVE_VALUE.sub("[REDACTED]", str(value or "")).strip()


def _overlap(nodes: list[dict[str, Any]]) -> bool:
    windows: list[tuple[str, str]] = []
    for node in nodes:
        if node.get("assignee_agent_id") in {"VE-0002", "VE-0003"}:
            started, finished = node.get("started_at"), node.get("finished_at")
            if isinstance(started, str) and isinstance(finished, str):
                windows.append((started, finished))
    return len(windows) >= 2 and max(start for start, _ in windows) <= min(end for _, end in windows)


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
    skill_ids = sorted({skill for event in events for skill in (event.get("detail", {}).get("selected_skill_ids", []) if isinstance(event.get("detail"), dict) else []) if isinstance(skill, str)})
    event_ids = sorted({str(message["matrix_event_id"]) for message in messages if _is_real_event_id(message.get("matrix_event_id"))})
    audit_ids = sorted({audit for event in events for audit in (event.get("detail", {}).get("audit_ids", []) if isinstance(event.get("detail"), dict) else []) if isinstance(audit, int) and audit > 0})
    participants = sorted({str(node.get("assignee_agent_id")) for node in nodes if node.get("assignee_agent_id")})
    depths = [node.get("depth") for node in nodes if isinstance(node.get("depth"), int)]
    rounds = [event.get("detail", {}).get("round") for event in events if event.get("event_type") == "task.delegate" and isinstance(event.get("detail"), dict)]
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
    if not _overlap(nodes):
        missing.append("hr_it_overlap")
    if not skill_ids or "agentteams-onboarding-readiness" not in skill_ids:
        missing.append("selected_skills")
    if not rounds:
        missing.append("delegation_round")
    if len(audit_ids) < 3:
        missing.append("governed_tool_audits")
    if len(event_ids) < 4:
        missing.append("real_matrix_event_ids")
    if not any(event.get("event_type") == "task.summarizing" for event in events):
        missing.append("manager_review")
    return {
        "ok": not missing,
        "task_id": task_id,
        "participant_ids": participants,
        "matrix_event_ids": event_ids,
        "selected_skill_ids": skill_ids,
        "audit_ids": audit_ids,
        "rounds": rounds,
        "summary": _safe_summary(result.get("summary")),
        "data_boundary": DEMO_DATA_BOUNDARY,
        "missing_criteria": missing,
    }


def _safe_empty(reason: str) -> dict[str, Any]:
    return {"ok": False, "task_id": "", "participant_ids": [], "matrix_event_ids": [], "selected_skill_ids": [], "audit_ids": [], "rounds": [], "summary": "", "data_boundary": DEMO_DATA_BOUNDARY, "missing_criteria": [reason]}


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
