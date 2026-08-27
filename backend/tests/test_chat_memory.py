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
from app.services.llm import LLMProvider
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


# ---- Round 2 Task B3: memory tool 开关配置 ----

TOOL_ENABLED_ENV = "DWP_MEMORY_TOOL_ENABLED"


def test_memory_tool_config_default_true(monkeypatch):
    """未设置时工具开关默认开启（与总开关独立）。"""
    monkeypatch.delenv(TOOL_ENABLED_ENV, raising=False)
    assert config.memory_tool_enabled() is True


def test_memory_tool_config_disabled(monkeypatch):
    monkeypatch.setenv(TOOL_ENABLED_ENV, "0")
    assert config.memory_tool_enabled() is False


def test_memory_tool_config_invalid_falls_back(monkeypatch):
    """非法值回退默认 True；关闭只认显式 0/false/no/off。"""
    monkeypatch.setenv(TOOL_ENABLED_ENV, "abc")
    assert config.memory_tool_enabled() is True


def test_memory_tool_independent_of_memory_enabled(monkeypatch):
    """工具开关独立于总开关：总开关关闭时工具开关仍可单独查询（组合逻辑由调用方负责）。"""
    monkeypatch.setenv("DWP_MEMORY_ENABLED", "0")
    monkeypatch.setenv(TOOL_ENABLED_ENV, "1")
    assert config.memory_enabled() is False
    assert config.memory_tool_enabled() is True


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


# ---- Task 5: direct_recall ----


class _FakeLLM(LLMProvider):
    """可编程假 LLM：按调用顺序返回预设响应，并记录调用历史。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        return self.script.pop(0)

    def tool_call(self, messages, tools):
        return self.chat(messages, tools)

    def structured_output(self, messages, schema):
        return {}


def _seed_old_memory(db_session, owner="VE-0001", session="S-OLD", ref="chat:S-OLD:assistant:2"):
    from app.services.memory_service import capture_turn

    assert capture_turn(
        db_session,
        owner_employee_no=owner,
        source_type="chat",
        source_session_id=session,
        source_ref=ref,
        user_text="张三的 IT 账号怎么样？",
        assistant_text="张三的 HR 材料已完成，IT 账号仍待开通。",
        trace_id="T-OLD",
    ) is not None


def test_memory_direct_recall_injects_and_keeps_db_original(db_session):
    from app.services.chat import ChatOrchestrator
    from app.services.llm import LLMResponse
    from app.services.session import history

    _seed_old_memory(db_session)

    fake = _FakeLLM([LLMResponse(content="HR 材料已完成，IT 账号仍待开通。")])
    result = ChatOrchestrator(fake).handle_message(
        db_session,
        employee_no="VE-0001",
        message="上次张三的账号处理好了吗？",
        session_id=None,
    )

    user_payload = fake.calls[0][-1]["content"]
    assert "【本地相关记忆】" in user_payload
    assert "上次张三的账号处理好了吗？" in user_payload
    stored = history(db_session, result.session_id)
    assert stored[0].content == "上次张三的账号处理好了吗？"
    assert "【本地相关记忆】" not in stored[0].content


def test_memory_direct_recall_no_hits_no_injection(db_session):
    from app.services.chat import ChatOrchestrator
    from app.services.llm import LLMResponse

    fake = _FakeLLM([LLMResponse(content="今天天气如何？")])
    result = ChatOrchestrator(fake).handle_message(
        db_session,
        employee_no="VE-0001",
        message="今天天气如何？",
        session_id=None,
    )

    user_payload = fake.calls[0][-1]["content"]
    assert user_payload == "今天天气如何？"
    assert "【本地相关记忆】" not in user_payload


def test_memory_direct_recall_disabled_no_injection(db_session, monkeypatch):
    from app.services.chat import ChatOrchestrator
    from app.services.llm import LLMResponse

    _seed_old_memory(db_session)
    monkeypatch.setenv("DWP_MEMORY_ENABLED", "0")

    fake = _FakeLLM([LLMResponse(content="HR 材料已完成。")])
    result = ChatOrchestrator(fake).handle_message(
        db_session,
        employee_no="VE-0001",
        message="上次张三的账号处理好了吗？",
        session_id=None,
    )

    user_payload = fake.calls[0][-1]["content"]
    assert user_payload == "上次张三的账号处理好了吗？"
    assert "【本地相关记忆】" not in user_payload


# ---- Task 6: direct_capture ----


def test_memory_direct_capture_after_successful_reply(db_session):
    from sqlalchemy import select

    from app import models
    from app.services.chat import ChatOrchestrator
    from app.services.llm import LLMResponse

    fake = _FakeLLM([LLMResponse(content="HR 材料已完成，IT 账号仍待开通。")])
    result = ChatOrchestrator(fake).handle_message(
        db_session,
        employee_no="VE-0001",
        message="张三的账号处理好了吗？",
        session_id=None,
    )

    assistant_msg = db_session.scalar(
        select(models.ChatMessage).where(
            models.ChatMessage.session_id == result.session_id,
            models.ChatMessage.role == "assistant",
        )
    )
    entries = list(
        db_session.scalars(
            select(models.MemoryEntry).where(
                models.MemoryEntry.subject_no == "VE-0001",
                models.MemoryEntry.kind == "conversation",
            )
        )
    )
    assert len(entries) == 1
    assert entries[0].source_type == "chat"
    assert entries[0].source_session_id == result.session_id
    assert entries[0].source_ref == f"chat:{result.session_id}:assistant:{assistant_msg.id}"
    assert "张三的账号处理好了吗？" in entries[0].content
    assert "HR 材料已完成，IT 账号仍待开通。" in entries[0].content


def test_memory_direct_capture_skipped_on_llm_failure(db_session, monkeypatch):
    from sqlalchemy import select

    from app import models
    from app.services.chat import ChatOrchestrator
    from app.services.llm import LLMUnavailableError

    def boom(messages, tools=None):
        raise LLMUnavailableError("模拟不可用")

    fake = _FakeLLM([])
    monkeypatch.setattr(fake, "chat", boom)
    result = ChatOrchestrator(fake).handle_message(
        db_session,
        employee_no="VE-0001",
        message="张三的账号处理好了吗？",
        session_id=None,
    )
    assert "LLM 暂不可用" in result.message
    entries = list(
        db_session.scalars(
            select(models.MemoryEntry).where(models.MemoryEntry.kind == "conversation")
        )
    )
    assert entries == []


def test_memory_direct_capture_once_after_tool_multi_round(db_session):
    from sqlalchemy import select

    from app import models
    from app.services.chat import ChatOrchestrator
    from app.services.llm import LLMResponse, ToolCall

    fake = _FakeLLM(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="tc-1",
                        name="search_knowledge",
                        arguments={"knowledge_base_id": "KB-ONBOARD", "query": "第一天做什么"},
                    )
                ],
            ),
            LLMResponse(content="第一天先到 HR 报到，签署合同。"),
        ]
    )
    result = ChatOrchestrator(fake).handle_message(
        db_session,
        employee_no="VE-0001",
        message="新员工第一天要做什么？",
        session_id=None,
    )
    assert "报到" in result.message
    entries = list(
        db_session.scalars(
            select(models.MemoryEntry).where(models.MemoryEntry.kind == "conversation")
        )
    )
    assert len(entries) == 1


def test_memory_read_failure_e2e_chat_still_answers(db_session, monkeypatch):
    """§17 #11 E2E：A 的 retrieve_for_prompt 异常时，chat 全链路仍正常回答且不注入记忆。"""
    from app.services import memory_runtime
    from app.services.chat import ChatOrchestrator
    from app.services.llm import LLMResponse

    def boom(db, **kwargs):
        raise RuntimeError("mock memory read failed")

    monkeypatch.setattr(memory_runtime, "retrieve_for_prompt", boom)

    fake = _FakeLLM([LLMResponse(content="HR 材料已完成，IT 账号仍待开通。")])
    result = ChatOrchestrator(fake).handle_message(
        db_session,
        employee_no="VE-0001",
        message="上次张三的账号处理好了吗？",
        session_id=None,
    )
    assert "HR 材料已完成" in result.message
    user_payload = fake.calls[0][-1]["content"]
    assert user_payload == "上次张三的账号处理好了吗？"
    assert "【本地相关记忆】" not in user_payload


def test_memory_write_failure_e2e_reply_still_returns(db_session, monkeypatch):
    """§17 #12 E2E：A 的 capture_turn 异常时，回答仍正常返回，不阻断聊天。"""
    from app.services import memory_runtime
    from app.services.chat import ChatOrchestrator
    from app.services.llm import LLMResponse

    def boom(db, **kwargs):
        raise RuntimeError("mock memory write failed")

    monkeypatch.setattr(memory_runtime, "capture_turn", boom)

    fake = _FakeLLM([LLMResponse(content="HR 材料已完成，IT 账号仍待开通。")])
    result = ChatOrchestrator(fake).handle_message(
        db_session,
        employee_no="VE-0001",
        message="上次张三的账号处理好了吗？",
        session_id=None,
    )
    assert "HR 材料已完成" in result.message


def test_memory_direct_capture_idempotent_on_same_source_ref(db_session):
    from sqlalchemy import select

    from app import models
    from app.services import memory_runtime

    ref = "chat:S-1:assistant:2"
    first = memory_runtime.capture_turn_safely(
        db_session,
        owner_employee_no="VE-0001",
        source_type="chat",
        source_session_id="S-1",
        source_ref=ref,
        user_text="q",
        assistant_text="a",
        trace_id="T",
    )
    second = memory_runtime.capture_turn_safely(
        db_session,
        owner_employee_no="VE-0001",
        source_type="chat",
        source_session_id="S-1",
        source_ref=ref,
        user_text="q",
        assistant_text="a",
        trace_id="T",
    )
    assert first == second
    entries = list(
        db_session.scalars(select(models.MemoryEntry).where(models.MemoryEntry.source_ref == ref))
    )
    assert len(entries) == 1


# ---- Task 9: 真实 A 实现联调 ----


def test_memory_runtime_uses_real_a_contract():
    """联调：memory_runtime 直接绑定 A 的真实 memory_service 实现，不再依赖行为假设。"""
    from app.services import memory_runtime, memory_service

    assert memory_runtime.capture_turn is memory_service.capture_turn
    assert memory_runtime.retrieve_for_prompt is memory_service.retrieve_for_prompt
    assert memory_runtime.render_prompt_context is memory_service.render_prompt_context
