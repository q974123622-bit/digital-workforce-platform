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
