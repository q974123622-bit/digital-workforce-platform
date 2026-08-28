"""Round 2 B1：Plugin Gateway memory 分支 + search_memory 入口测试。

覆盖 §7 测试矩阵中与 Gateway 直接相关的项：
- allow / empty 命中语义
- parameter_error（空 query / 非法 limit）
- deny（无 grant → 403 + 审计）
- error（检索异常 → error 态 + 审计）
- owner 隔离（owner_employee_no 由 Gateway 注入，不接受模型参数）
- data_level 过滤（hit.data_level > subject.max_data_level 不发送）
- limit 封顶与默认值
- 审计只记元数据（hits/ids/chars），不记正文
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import models
from app.services import gateway
from app.services.memory_service import capture_turn, retrieve_for_prompt


def _seed_memory(db, owner, session_id, ref, user_text, assistant_text="好的，我会记住。"):
    memory_id = capture_turn(
        db,
        owner_employee_no=owner,
        source_type="chat",
        source_session_id=session_id,
        source_ref=ref,
        user_text=user_text,
        assistant_text=assistant_text,
    )
    assert memory_id is not None
    return memory_id


def _audit(db, trace_id):
    return db.scalar(
        select(models.AuditEvent).where(
            models.AuditEvent.trace_id == trace_id,
            models.AuditEvent.plugin_id == "agent-memory",
            models.AuditEvent.action == "search",
        )
    )


# ---- allow / empty ----


def test_search_memory_allow_returns_hits_and_metadata_only_audit(db_session):
    memory_id = _seed_memory(db_session, "VE-0001", "S-GW-A", "chat:S-GW-A:a:1", "张三的 IT 账号还没有开通")

    result = gateway.search_memory(
        db_session,
        employee_id="VE-0001",
        query="张三 IT 账号",
        current_session_id="S-GW-B",
        trace_id="T-GW-ALLOW",
    )

    assert result["ok"] is True
    assert result["decision"] == "allow"
    assert memory_id in result["data"]["memory_ids"]
    assert "【本地相关记忆】" in result["data"]["text"]
    assert "账号" in result["data"]["text"]
    assert all("content" not in hit for hit in result["data"]["hits"])

    audit = _audit(db_session, "T-GW-ALLOW")
    assert audit is not None
    assert audit.decision == "allow"
    assert "hits=" in audit.result_summary
    assert "ids=" in audit.result_summary
    assert "chars=" in audit.result_summary
    assert "张三的 IT 账号还没有开通" not in (audit.result_summary or "")


def test_search_memory_empty_returns_empty_decision(db_session):
    result = gateway.search_memory(
        db_session,
        employee_id="VE-0001",
        query="完全不存在的关键词xyz",
        current_session_id="S-GW-B",
        trace_id="T-GW-EMPTY",
    )

    assert result["ok"] is True
    assert result["decision"] == "empty"
    assert result["data"]["text"] == ""
    assert result["data"]["memory_ids"] == []


# ---- parameter_error ----


@pytest.mark.parametrize("query", ["", "   ", None])
def test_search_memory_empty_query_returns_parameter_error(db_session, query):
    result = gateway.search_memory(
        db_session,
        employee_id="VE-0001",
        query=query,
        current_session_id="S-GW-B",
        trace_id="T-GW-PE-Q",
    )
    assert result["ok"] is False
    assert result["decision"] == "parameter_error"
    assert result["error"] == "query_required"


@pytest.mark.parametrize("limit", [0, -1, 3.5, "x", True])
def test_search_memory_invalid_limit_returns_parameter_error(db_session, limit):
    result = gateway.search_memory(
        db_session,
        employee_id="VE-0001",
        query="张三",
        current_session_id="S-GW-B",
        trace_id="T-GW-PE-L",
        limit=limit,
    )
    assert result["ok"] is False
    assert result["decision"] == "parameter_error"
    assert result["error"] == "limit_invalid"


# ---- limit cap / default ----


def test_search_memory_limit_over_10_is_capped(db_session, monkeypatch):
    captured = {}

    def fake_retrieve(db, *, owner_employee_no, query, current_session_id, limit=3, max_chars=1200):
        captured["limit"] = limit
        return []

    monkeypatch.setattr("app.services.gateway.retrieve_for_prompt", fake_retrieve)
    gateway.search_memory(
        db_session,
        employee_id="VE-0001",
        query="张三",
        current_session_id="S-GW-B",
        trace_id="T-GW-CAP",
        limit=11,
    )
    assert captured["limit"] == 10


def test_search_memory_default_limit_is_3(db_session, monkeypatch):
    captured = {}

    def fake_retrieve(db, *, owner_employee_no, query, current_session_id, limit=3, max_chars=1200):
        captured["limit"] = limit
        return []

    monkeypatch.setattr("app.services.gateway.retrieve_for_prompt", fake_retrieve)
    gateway.search_memory(
        db_session,
        employee_id="VE-0001",
        query="张三",
        current_session_id="S-GW-B",
        trace_id="T-GW-DEF",
    )
    assert captured["limit"] == 3


# ---- deny ----


def test_search_memory_deny_without_grant_raises_403(db_session):
    with pytest.raises(HTTPException) as exc_info:
        gateway.search_memory(
            db_session,
            employee_id="VE-0003",
            query="张三",
            current_session_id="S-GW-B",
            trace_id="T-GW-DENY",
        )
    assert exc_info.value.status_code == 403

    audit = _audit(db_session, "T-GW-DENY")
    assert audit is not None
    assert audit.decision == "deny"


# ---- error ----


def test_search_memory_error_state_on_retrieve_exception(db_session, monkeypatch):
    def boom(db, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.services.gateway.retrieve_for_prompt", boom)
    result = gateway.search_memory(
        db_session,
        employee_id="VE-0001",
        query="张三",
        current_session_id="S-GW-B",
        trace_id="T-GW-ERR",
    )

    assert result["ok"] is False
    assert result["decision"] == "error"
    assert "db down" in (result.get("error") or "")

    audit = _audit(db_session, "T-GW-ERR")
    assert audit is not None
    assert audit.decision == "error"


# ---- owner isolation ----


def test_search_memory_owner_is_injected_and_isolated(db_session):
    _seed_memory(db_session, "VE-0001", "S-ISO-A", "chat:S-ISO-A:a:1", "张三的 IT 账号还没有开通")

    result = gateway.search_memory(
        db_session,
        employee_id="VE-0002",
        query="张三 IT 账号",
        current_session_id="S-ISO-B",
        trace_id="T-GW-ISO",
    )

    assert result["decision"] == "empty"
    assert result["data"]["memory_ids"] == []


# ---- data_level filter ----


def test_search_memory_filters_hits_above_subject_max_data_level(db_session):
    memory_id = _seed_memory(db_session, "DT-E20999", "S-LV-A", "chat:S-LV-A:a:1", "张三的 IT 账号还没有开通")

    # 直接检索能命中（L2 记忆确实存在），证明下面 gateway 的 empty 是数据级别过滤所致
    direct_hits = retrieve_for_prompt(
        db_session,
        owner_employee_no="DT-E20999",
        query="张三 IT 账号",
        current_session_id="S-LV-B",
    )
    assert any(h.memory_id == memory_id for h in direct_hits)
    assert any(h.data_level == "L2" for h in direct_hits)

    result = gateway.search_memory(
        db_session,
        employee_id="DT-E20999",
        query="张三 IT 账号",
        current_session_id="S-LV-B",
        trace_id="T-GW-LV",
    )

    assert result["decision"] == "empty"
    assert result["data"]["text"] == ""
    assert memory_id not in result["data"]["memory_ids"]
