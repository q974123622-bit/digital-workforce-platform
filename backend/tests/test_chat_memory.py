"""工作线 B 集成测试：直接聊天、记忆上下文、失败降级。

按 MEMORY_WORKLINE_B_AI_EXECUTION_PLAN 逐 Task 累积：
- Task 1: contract_stub 自测
- Task 2: memory_config
- Task 4: session_owner
- （Task 3 / 5 / 6 后续补充）
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import config
from app.services.session import get_or_create
from tests.memory_contract_stub import MemoryContractStub


def _hit(memory_id=101, content="张三的 IT 账号未开通"):
    """构造与 A 的 MemoryHit 字段一致的命中对象，便于 Task 9 无缝切换真实实现。"""
    return SimpleNamespace(
        memory_id=memory_id,
        content=content,
        created_at="2026-08-20",
        score=0.5,
        kind="conversation_turn",
    )


# ---- Task 1: contract stub 自测 ----


def test_contract_stub_returns_hits():
    hit = _hit()
    stub = MemoryContractStub(hits=[hit], context="【本地相关记忆】")
    assert stub.retrieve_for_prompt(owner_employee_no="EMP-A", query="张三") == [hit]
    assert stub.render_prompt_context([hit]) == "【本地相关记忆】"
    assert stub.capture_turn(owner_employee_no="EMP-A") == 9001


def test_contract_stub_empty_context_when_no_hits():
    stub = MemoryContractStub(hits=[], context="【本地相关记忆】")
    assert stub.retrieve_for_prompt() == []
    assert stub.render_prompt_context([]) == ""


def test_contract_stub_read_failure_raises():
    stub = MemoryContractStub(fail_read=True)
    with pytest.raises(RuntimeError):
        stub.retrieve_for_prompt()


def test_contract_stub_write_failure_raises():
    stub = MemoryContractStub(fail_write=True)
    with pytest.raises(RuntimeError):
        stub.capture_turn()


# ---- Task 2: memory_config ----


def test_memory_config_defaults(monkeypatch):
    for k in ("DWP_MEMORY_ENABLED", "DWP_MEMORY_MAX_ITEMS", "DWP_MEMORY_MAX_CHARS"):
        monkeypatch.delenv(k, raising=False)
    assert config.memory_enabled() is True
    assert config.memory_max_items() == 3
    assert config.memory_max_chars() == 1200


def test_memory_config_disabled(monkeypatch):
    monkeypatch.setenv("DWP_MEMORY_ENABLED", "0")
    assert config.memory_enabled() is False


def test_memory_config_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("DWP_MEMORY_MAX_ITEMS", "abc")
    monkeypatch.setenv("DWP_MEMORY_MAX_CHARS", "not-a-number")
    assert config.memory_max_items() == 3
    assert config.memory_max_chars() == 1200


def test_memory_config_negative_falls_back(monkeypatch):
    monkeypatch.setenv("DWP_MEMORY_MAX_ITEMS", "-1")
    monkeypatch.setenv("DWP_MEMORY_MAX_CHARS", "-5")
    assert config.memory_max_items() == 3
    assert config.memory_max_chars() == 1200


def test_memory_config_bounds(monkeypatch):
    monkeypatch.setenv("DWP_MEMORY_MAX_ITEMS", "10")
    monkeypatch.setenv("DWP_MEMORY_MAX_CHARS", "4000")
    assert config.memory_max_items() == 10
    assert config.memory_max_chars() == 4000


# ---- Task 4: session_owner ----


def test_session_owner_mismatch_rejected(db_session):
    session, created = get_or_create(db_session, None, "VE-0001")
    assert created is True
    with pytest.raises(HTTPException) as exc:
        get_or_create(db_session, session.session_id, "VE-0002")
    assert exc.value.status_code == 409


# ---- Task 3: memory_runtime ----


def test_memory_runtime_prepare_success(db_session):
    from app.services import memory_runtime
    from app.services.memory_service import capture_turn

    memory_id = capture_turn(
        db_session,
        owner_employee_no="VE-0001",
        source_type="chat",
        source_session_id="S-OLD",
        source_ref="chat:S-OLD:assistant:2",
        user_text="张三的 IT 账号怎么样？",
        assistant_text="张三的 HR 材料已完成，IT 账号仍待开通。",
        trace_id="T-1",
    )
    prepared = memory_runtime.prepare_memory_context(
        db_session,
        owner_employee_no="VE-0001",
        query="上次张三的账号处理好了吗？",
        current_session_id="S-NOW",
        trace_id="T-1",
    )
    assert prepared.text
    assert prepared.memory_ids == (memory_id,)
    assert prepared.chars == len(prepared.text)
    assert "【本地相关记忆】" in prepared.text
    assert "张三" in prepared.text


def test_memory_runtime_prepare_disabled_skips_a(db_session, monkeypatch):
    from app.services import memory_runtime

    called: list = []
    monkeypatch.setattr(
        memory_runtime, "retrieve_for_prompt", lambda **kw: called.append(("retrieve", kw)) or []
    )
    monkeypatch.setattr(
        memory_runtime, "render_prompt_context", lambda hits, **kw: called.append("render") or ""
    )
    monkeypatch.setenv("DWP_MEMORY_ENABLED", "0")

    prepared = memory_runtime.prepare_memory_context(
        db_session,
        owner_employee_no="VE-0001",
        query="q",
        current_session_id="S-NOW",
        trace_id="T-1",
    )
    assert prepared.text == ""
    assert prepared.memory_ids == ()
    assert called == []


def test_memory_runtime_prepare_read_error_degrades(db_session, monkeypatch):
    from app.services import memory_runtime

    def boom(**kwargs):
        raise RuntimeError("mock memory read failed")

    monkeypatch.setattr(memory_runtime, "retrieve_for_prompt", boom)
    prepared = memory_runtime.prepare_memory_context(
        db_session,
        owner_employee_no="VE-0001",
        query="q",
        current_session_id="S-NOW",
        trace_id="T-1",
    )
    assert prepared.text == ""
    assert prepared.memory_ids == ()


def test_memory_runtime_capture_success(db_session):
    from sqlalchemy import select

    from app import models
    from app.services import memory_runtime

    memory_id = memory_runtime.capture_turn_safely(
        db_session,
        owner_employee_no="VE-0001",
        source_type="chat",
        source_session_id="S-OLD",
        source_ref="chat:S-OLD:assistant:5",
        user_text="张三的账号处理好了吗？",
        assistant_text="IT 账号仍待开通。",
        trace_id="T-2",
    )
    assert isinstance(memory_id, int)
    events = list(
        db_session.scalars(
            select(models.AuditEvent).where(models.AuditEvent.action == "memory.capture")
        )
    )
    assert len(events) == 1
    assert events[0].decision == "allow"
    assert f"memory_id={memory_id}" in (events[0].result_summary or "")


def test_memory_runtime_capture_disabled_skips_a(db_session, monkeypatch):
    from app.services import memory_runtime

    called: list = []
    monkeypatch.setattr(memory_runtime, "capture_turn", lambda **kw: called.append(kw) or 9001)
    monkeypatch.setenv("DWP_MEMORY_ENABLED", "0")

    result = memory_runtime.capture_turn_safely(
        db_session,
        owner_employee_no="VE-0001",
        source_type="chat",
        source_session_id="S-OLD",
        source_ref="chat:S-OLD:assistant:6",
        user_text="u",
        assistant_text="a",
        trace_id="T-2",
    )
    assert result is None
    assert called == []


def test_memory_runtime_capture_write_error_degrades(db_session, monkeypatch):
    from app.services import memory_runtime

    def boom(**kwargs):
        raise RuntimeError("mock memory write failed")

    monkeypatch.setattr(memory_runtime, "capture_turn", boom)
    result = memory_runtime.capture_turn_safely(
        db_session,
        owner_employee_no="VE-0001",
        source_type="chat",
        source_session_id="S-OLD",
        source_ref="chat:S-OLD:assistant:7",
        user_text="u",
        assistant_text="a",
        trace_id="T-2",
    )
    assert result is None
