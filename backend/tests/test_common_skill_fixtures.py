"""本批通用 Skill 的 Mock Fixture 结构稳定性测试。

只验证 fixture 可解析、字段稳定、可重复，不涉及 Harness 执行 Skill，
也不涉及 ChatOrchestrator。
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "mock-data" / "skill-fixtures"


def _load_json(relative: str) -> dict:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


def test_collaboration_scenarios_parseable():
    data = _load_json("collaboration/collaboration-scenarios.json")
    scenarios = data["scenarios"]
    assert isinstance(scenarios, list) and scenarios
    statuses = {s["status"] for s in scenarios}
    assert {"success", "not_found", "unavailable", "denied", "blocked"} <= statuses


def test_recursion_blocked_has_repeated_chain():
    data = _load_json("collaboration/collaboration-scenarios.json")
    scenario = next(s for s in data["scenarios"] if s["scenario_id"] == "recursion_blocked")
    visited = scenario["visited_employee_ids"]
    assert len(visited) != len(set(visited))
    assert visited[0] == visited[-1]


def test_normal_document_non_empty():
    text = (FIXTURES / "documents" / "normal-document.md").read_text(encoding="utf-8")
    assert text.strip()


def test_empty_document_effectively_empty():
    text = (FIXTURES / "documents" / "empty-document.md").read_text(encoding="utf-8")
    assert text.strip() == ""


def test_conflict_documents_non_empty_and_different():
    a = (FIXTURES / "documents" / "conflict-document-a.md").read_text(encoding="utf-8")
    b = (FIXTURES / "documents" / "conflict-document-b.md").read_text(encoding="utf-8")
    assert a.strip() and b.strip()
    assert a != b


def test_work_records_parseable_and_cover_statuses():
    data = _load_json("work-records/work-records.json")
    records = data["records"]
    assert isinstance(records, list) and records
    statuses = {r["status"] for r in records}
    assert {"completed", "in_progress", "not_done", "research", "review", "issue_resolved"} <= statuses


def test_work_records_have_duplicate_scenario():
    data = _load_json("work-records/work-records.json")
    records = data["records"]
    assert any(r.get("duplicate_of") for r in records)
