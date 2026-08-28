"""Round 2 B5：复用 A5 固定样例，经 Plugin Gateway `search_memory` 端到端指标评测。

与 A5（纯 `retrieve_for_prompt` 评测）的区别：B5 走完整 Gateway 链路
（Identity → Policy → 注入 owner/会话 → 数据级别过滤 → 渲染 → 审计），
记录端到端工具调用延迟、注入字符数、工具调用次数、串读率与错误记忆率，
并输出与 Round 1 自动检索核心（直接 `retrieve_for_prompt`）的延迟对比。
"""

import json
from datetime import datetime
from pathlib import Path
from time import perf_counter

import pytest
from sqlalchemy import select

from app import models
from app.services import gateway
from app.services.memory_service import retrieve_for_prompt

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "memory_retrieval_cases.json"
_LEVEL_RANK = {"L1": 1, "L2": 2, "L3": 3}


def _load_cases() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


def _ensure_employee(db, owner: str, max_level: str) -> None:
    """评测用数字员工：保证存在、等级覆盖用例数据级别、具备 agent-memory/search 授权。"""
    emp = db.get(models.DigitalEmployee, owner)
    if emp is None:
        emp = models.DigitalEmployee(
            employee_no=owner,
            name=f"评测员工 {owner}",
            type="virtual",
            owner_human_no="E10021",
            department="评测",
            max_data_level=max_level,
        )
        db.add(emp)
    grant = db.scalar(
        select(models.EmployeePluginGrant).where(
            models.EmployeePluginGrant.employee_id == owner,
            models.EmployeePluginGrant.plugin_id == "agent-memory",
            models.EmployeePluginGrant.action == "search",
        )
    )
    if grant is None:
        db.add(
            models.EmployeePluginGrant(
                employee_id=owner,
                plugin_id="agent-memory",
                action="search",
                decision_mode="allow",
            )
        )
    db.commit()


@pytest.mark.skipif(not FIXTURE_PATH.exists(), reason="A5 固定检索样例尚未提供")
def test_memory_gateway_e2e_metrics(db_session, monkeypatch, capsys):
    cases = _load_cases()
    totals = {
        "cases": len(cases),
        "required_hits": 0,
        "forbidden_hits": 0,
        "cross_owner_hits": 0,
        "empty_cases": 0,
        "empty_cases_correct": 0,
        "tool_latency_ms_total": 0.0,
        "direct_latency_ms_total": 0.0,
        "injected_chars_total": 0,
        "returned_hits": 0,
        "tool_calls": 0,
    }

    for case in cases:
        entries: list[models.MemoryEntry] = []
        max_level = "L1"
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
            if _LEVEL_RANK.get(entry.data_level, 0) > _LEVEL_RANK.get(max_level, 0):
                max_level = entry.data_level
        db_session.commit()

        request = case["request"]
        owner = request["owner_employee_no"]
        _ensure_employee(db_session, owner, max_level)
        # 用例级字符预算：Gateway 读取 config.memory_max_chars()，按用例覆盖
        monkeypatch.setattr("app.services.config.memory_max_chars", lambda: request["max_chars"])

        # Round 1 自动检索核心（直接 retrieve_for_prompt）作为对比基线
        started = perf_counter()
        retrieve_for_prompt(
            db_session,
            owner_employee_no=owner,
            query=request["query"],
            current_session_id=request["current_session_id"],
            limit=request["limit"],
            max_chars=request["max_chars"],
        )
        totals["direct_latency_ms_total"] += (perf_counter() - started) * 1000

        # Round 2 工具链路：完整 Gateway search_memory
        started = perf_counter()
        result = gateway.search_memory(
            db_session,
            employee_id=owner,
            query=request["query"],
            limit=request["limit"],
            current_session_id=request["current_session_id"],
            trace_id=f"T-B5-{case['id']}",
        )
        totals["tool_latency_ms_total"] += (perf_counter() - started) * 1000

        assert result["ok"] is True, case["id"]
        assert result["decision"] in ("allow", "empty"), case["id"]
        totals["tool_calls"] += 1

        data = result["data"] or {}
        text = data.get("text") or ""
        totals["injected_chars_total"] += len(text)
        totals["returned_hits"] += len(data.get("memory_ids") or [])
        if case["expect"]["expect_empty"]:
            totals["empty_cases"] += 1
            totals["empty_cases_correct"] += int(result["decision"] == "empty")

        hit_ids = set(data.get("memory_ids") or [])
        hit_refs = {e.source_ref for e in entries if e.id in hit_ids}
        hit_by_id = {h["memory_id"]: h for h in (data.get("hits") or [])}
        hit_levels = {e.source_ref: hit_by_id[e.id]["data_level"] for e in entries if e.id in hit_by_id}
        expected = case["expect"]
        required = set(expected["must_hit"])
        totals["required_hits"] += len(required)
        totals["forbidden_hits"] += len(hit_refs & set(expected["must_not_hit"]))
        totals["cross_owner_hits"] += sum(
            1 for e in entries if e.id in hit_ids and e.subject_no != owner
        )

        assert required <= hit_refs, case["id"]
        assert not (hit_refs & set(expected["must_not_hit"])), case["id"]
        assert bool(hit_ids) is not expected["expect_empty"], case["id"]
        assert len(hit_ids) <= request["limit"], case["id"]
        assert len(text) <= request["max_chars"], case["id"]
        for source_ref, data_level in expected["expected_data_levels"].items():
            assert hit_levels.get(source_ref) == data_level, case["id"]

    total_hits = totals["returned_hits"]
    metrics = {
        **totals,
        "required_hit_rate": 1.0,
        "false_hit_rate": totals["forbidden_hits"] / total_hits if total_hits else 0.0,
        "cross_owner_leak_rate": totals["cross_owner_hits"] / total_hits if total_hits else 0.0,
        "error_memory_rate": totals["forbidden_hits"] / total_hits if total_hits else 0.0,
        "empty_accuracy": totals["empty_cases_correct"] / totals["empty_cases"],
        "avg_tool_latency_ms": round(totals["tool_latency_ms_total"] / totals["cases"], 3),
        "avg_direct_latency_ms": round(totals["direct_latency_ms_total"] / totals["cases"], 3),
        "avg_injected_chars": round(totals["injected_chars_total"] / totals["cases"], 3),
    }
    print("memory_gateway_eval=" + json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    assert metrics["required_hit_rate"] == 1.0
    assert metrics["false_hit_rate"] == 0.0
    assert metrics["cross_owner_leak_rate"] == 0.0
    assert metrics["empty_accuracy"] == 1.0
