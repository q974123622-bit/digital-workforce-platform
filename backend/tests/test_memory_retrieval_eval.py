"""A5 固定样例：本地长期记忆检索质量评测。"""

import json
from datetime import datetime
from pathlib import Path
from time import perf_counter

import pytest

from app import models
from app.services.memory_service import render_prompt_context, retrieve_for_prompt


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "memory_retrieval_cases.json"


def _load_cases() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


def test_memory_retrieval_fixture_defines_a_repeatable_quality_contract():
    assert FIXTURE_PATH.exists(), "A5 固定检索样例尚未提供"

    cases = _load_cases()
    assert 20 <= len(cases) <= 30
    assert {"relevant", "irrelevant", "isolation", "preference", "data_level"} <= {
        tag for case in cases for tag in case["tags"]
    }
    for case in cases:
        assert {"id", "tags", "entries", "request", "expect"} <= case.keys()
        assert {"owner_employee_no", "query", "current_session_id", "limit", "max_chars"} <= case["request"].keys()
        assert {"must_hit", "must_not_hit", "expect_empty", "expected_data_levels"} <= case["expect"].keys()


@pytest.mark.skipif(not FIXTURE_PATH.exists(), reason="A5 固定检索样例尚未提供")
def test_memory_retrieval_fixed_cases_meet_expected_results_and_report_metrics(db_session, capsys):
    cases = _load_cases()
    totals = {
        "cases": len(cases),
        "required_hits": 0,
        "forbidden_hits": 0,
        "empty_cases": 0,
        "empty_cases_correct": 0,
        "latency_ms_total": 0.0,
        "prompt_chars_total": 0,
        "returned_hits": 0,
    }

    for case in cases:
        entries: list[models.MemoryEntry] = []
        for raw in case["entries"]:
            entry = models.MemoryEntry(
                subject_type=raw.get("subject_type", "virtual"),
                subject_no=raw["owner_employee_no"],
                kind=raw.get("kind", "conversation"),
                content=raw["content"],
                content_type="text",
                source_type=raw.get("source_type", "chat"),
                source_session_id=raw.get("source_session_id"),
                source_ref=raw["source_ref"],
                visibility="personal",
                data_level=raw.get("data_level", "L2"),
                lifecycle=raw.get("lifecycle", "active"),
                created_at=datetime.fromisoformat(raw["created_at"]),
            )
            db_session.add(entry)
            entries.append(entry)
        db_session.commit()

        request = case["request"]
        started = perf_counter()
        hits = retrieve_for_prompt(db_session, **request)
        elapsed_ms = (perf_counter() - started) * 1000
        context = render_prompt_context(hits, max_chars=request["max_chars"])
        hit_by_id = {hit.memory_id: hit for hit in hits}
        hit_refs = {entry.source_ref for entry in entries if entry.id in hit_by_id}
        hit_levels = {entry.source_ref: hit_by_id[entry.id].data_level for entry in entries if entry.id in hit_by_id}
        expected = case["expect"]

        required = set(expected["must_hit"])
        forbidden = set(expected["must_not_hit"])
        totals["required_hits"] += len(required)
        totals["forbidden_hits"] += len(hit_refs & forbidden)
        totals["latency_ms_total"] += elapsed_ms
        totals["prompt_chars_total"] += len(context)
        totals["returned_hits"] += len(hits)
        if expected["expect_empty"]:
            totals["empty_cases"] += 1
            totals["empty_cases_correct"] += int(not hits)

        assert required <= hit_refs, case["id"]
        assert not (hit_refs & forbidden), case["id"]
        assert bool(hits) is not expected["expect_empty"], case["id"]
        assert len(hits) <= request["limit"], case["id"]
        assert len(context) <= request["max_chars"], case["id"]
        for source_ref, data_level in expected["expected_data_levels"].items():
            assert hit_levels.get(source_ref) == data_level, case["id"]

    total_hits = totals["returned_hits"]
    metrics = {
        **totals,
        "required_hit_rate": 1.0,
        "false_hit_rate": totals["forbidden_hits"] / total_hits if total_hits else 0.0,
        "error_memory_rate": totals["forbidden_hits"] / total_hits if total_hits else 0.0,
        "empty_accuracy": totals["empty_cases_correct"] / totals["empty_cases"],
        "avg_latency_ms": round(totals["latency_ms_total"] / totals["cases"], 3),
        "avg_prompt_chars": round(totals["prompt_chars_total"] / totals["cases"], 3),
    }
    print("memory_retrieval_eval=" + json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    assert metrics["false_hit_rate"] == 0.0
    assert metrics["empty_accuracy"] == 1.0
