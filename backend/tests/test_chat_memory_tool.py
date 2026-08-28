"""Round 2 B4：search_memory 工具编排与治理测试（chat.py 层）。

覆盖 §7 测试矩阵 1-11（Gateway 层细节已在 test_gateway_memory.py 覆盖）：
- 工具可见性（配置开关 + 员工授权动态暴露）
- 无 grant 员工强行构造调用 → Gateway 403 deny
- 审计只记元数据（hits/ids/chars，不记正文）
- 防递归（工具轮只新增 1 条最终问答记忆）
- 员工隔离（owner 由 Gateway 注入）
- 参数语义（空 query / 非法 limit → parameter_error；limit 封顶与默认值）
- 调用治理（同轮第 3 次 → 上限提示；规范化 query 去重 → duplicate_query）
- error 态不阻断聊天
"""

import pytest
from sqlalchemy import func, select

from app import models
from app.services.chat import ChatOrchestrator
from app.services.llm import LLMProvider, LLMResponse, ToolCall
from app.services.memory_service import capture_turn


class _RecordingLLM(LLMProvider):
    """记录 tools 与 messages 的可编程假 LLM。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.tools_seen = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        self.tools_seen.append(tools)
        return self.script.pop(0)

    def tool_call(self, messages, tools):
        return self.chat(messages, tools)

    def structured_output(self, messages, schema):
        return {}


def _memory_tool_call(call_id, query, limit=None):
    arguments = {"query": query}
    if limit is not None:
        arguments["limit"] = limit
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name="search_memory", arguments=arguments)],
    )


def _tool_names(tools):
    return [t["function"]["name"] for t in (tools or [])]


def _tool_messages(chat_calls, round_index):
    return [m for m in chat_calls[round_index] if m["role"] == "tool"]


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


# ---- 工具可见性（测试 1/2/3/4） ----


def test_memory_tool_visible_when_enabled_and_granted(db_session):
    fake = _RecordingLLM([LLMResponse(content="好的")])
    ChatOrchestrator(fake).handle_message(db_session, employee_no="VE-0001", message="你好", session_id=None)
    names = _tool_names(fake.tools_seen[0])
    assert "search_memory" in names
    assert "search_knowledge" in names


def test_memory_tool_hidden_when_tool_switch_off(db_session, monkeypatch):
    monkeypatch.setenv("DWP_MEMORY_TOOL_ENABLED", "false")
    fake = _RecordingLLM([LLMResponse(content="好的")])
    ChatOrchestrator(fake).handle_message(db_session, employee_no="VE-0001", message="你好", session_id=None)
    names = _tool_names(fake.tools_seen[0])
    assert "search_memory" not in names
    assert "search_knowledge" in names


def test_memory_tool_hidden_when_memory_switch_off(db_session, monkeypatch):
    monkeypatch.setenv("DWP_MEMORY_ENABLED", "false")
    fake = _RecordingLLM([LLMResponse(content="好的")])
    ChatOrchestrator(fake).handle_message(db_session, employee_no="VE-0001", message="你好", session_id=None)
    names = _tool_names(fake.tools_seen[0])
    assert "search_memory" not in names


def test_memory_tool_hidden_without_grant(db_session):
    fake = _RecordingLLM([LLMResponse(content="好的")])
    ChatOrchestrator(fake).handle_message(db_session, employee_no="VE-0003", message="你好", session_id=None)
    names = _tool_names(fake.tools_seen[0])
    assert "search_memory" not in names


def test_memory_tool_forced_call_denies_without_grant(db_session):
    fake = _RecordingLLM([
        _memory_tool_call("m1", "张三"),
        LLMResponse(content="我无法查询。"),
    ])
    result = ChatOrchestrator(fake).handle_message(
        db_session, employee_no="VE-0003", message="查一下", session_id=None
    )
    assert result.policy_denied is not None
    assert result.policy_denied.decision == "deny"
    tool_msgs = _tool_messages(fake.calls, 1)
    assert any("无权检索历史记忆" in m["content"] for m in tool_msgs)


# ---- 审计（测试 5） ----


def test_memory_tool_call_writes_metadata_only_audit(db_session):
    _seed_memory(db_session, "VE-0001", "S-B4-A", "chat:S-B4-A:a:1", "张三的 IT 账号还没有开通")
    fake = _RecordingLLM([
        _memory_tool_call("m1", "张三 IT 账号"),
        LLMResponse(content="根据记忆，张三的账号还没开通。"),
    ])
    ChatOrchestrator(fake).handle_message(
        db_session, employee_no="VE-0001", message="查一下张三的账号", session_id=None
    )

    audit = db_session.scalar(
        select(models.AuditEvent).where(
            models.AuditEvent.plugin_id == "agent-memory",
            models.AuditEvent.action == "search",
            models.AuditEvent.decision == "allow",
        )
    )
    assert audit is not None
    assert "hits=" in (audit.result_summary or "")
    assert "ids=" in (audit.result_summary or "")
    assert "chars=" in (audit.result_summary or "")
    assert "张三的 IT 账号还没有开通" not in (audit.result_summary or "")


# ---- 防递归（测试 6） ----


def test_memory_tool_turn_adds_only_one_memory_entry(db_session):
    before = db_session.scalar(select(func.count()).select_from(models.MemoryEntry)) or 0
    fake = _RecordingLLM([
        _memory_tool_call("m1", "入职安排"),
        LLMResponse(content="这是最终回答。"),
    ])
    ChatOrchestrator(fake).handle_message(
        db_session, employee_no="VE-0001", message="问一下入职安排", session_id=None
    )
    after = db_session.scalar(select(func.count()).select_from(models.MemoryEntry)) or 0
    assert after - before == 1


# ---- 员工隔离（测试 7） ----


def test_memory_tool_isolates_owner(db_session):
    _seed_memory(db_session, "VE-0001", "S-ISO-A", "chat:S-ISO-A:a:1", "张三的 IT 账号还没有开通")
    fake = _RecordingLLM([
        _memory_tool_call("m1", "张三 IT 账号"),
        LLMResponse(content="没有查到。"),
    ])
    ChatOrchestrator(fake).handle_message(
        db_session, employee_no="VE-0002", message="查张三的账号", session_id=None
    )
    tool_msgs = _tool_messages(fake.calls, 1)
    assert any("没有找到相关记忆" in m["content"] for m in tool_msgs)


# ---- 参数语义（测试 8） ----


def test_memory_tool_empty_query_returns_parameter_error(db_session):
    fake = _RecordingLLM([
        _memory_tool_call("m1", "   "),
        LLMResponse(content="好的。"),
    ])
    result = ChatOrchestrator(fake).handle_message(
        db_session, employee_no="VE-0001", message="查一下", session_id=None
    )
    assert result.tool_cards[0].decision == "parameter_error"
    tool_msgs = _tool_messages(fake.calls, 1)
    assert any("参数无效" in m["content"] for m in tool_msgs)


@pytest.mark.parametrize("limit", [0, -1])
def test_memory_tool_invalid_limit_returns_parameter_error(db_session, limit):
    fake = _RecordingLLM([
        _memory_tool_call("m1", "张三", limit=limit),
        LLMResponse(content="好的。"),
    ])
    result = ChatOrchestrator(fake).handle_message(
        db_session, employee_no="VE-0001", message="查一下", session_id=None
    )
    assert result.tool_cards[0].decision == "parameter_error"
    tool_msgs = _tool_messages(fake.calls, 1)
    assert any("参数无效" in m["content"] for m in tool_msgs)


def test_memory_tool_limit_over_10_capped_via_gateway(db_session, monkeypatch):
    captured = {}

    def fake_retrieve(db, *, owner_employee_no, query, current_session_id, limit=3, max_chars=1200):
        captured["limit"] = limit
        return []

    monkeypatch.setattr("app.services.gateway.retrieve_for_prompt", fake_retrieve)
    fake = _RecordingLLM([
        _memory_tool_call("m1", "张三", limit=11),
        LLMResponse(content="好的。"),
    ])
    ChatOrchestrator(fake).handle_message(
        db_session, employee_no="VE-0001", message="查一下", session_id=None
    )
    assert captured["limit"] == 10


def test_memory_tool_default_limit_is_3(db_session, monkeypatch):
    captured = {}

    def fake_retrieve(db, *, owner_employee_no, query, current_session_id, limit=3, max_chars=1200):
        captured["limit"] = limit
        return []

    monkeypatch.setattr("app.services.gateway.retrieve_for_prompt", fake_retrieve)
    fake = _RecordingLLM([
        _memory_tool_call("m1", "张三"),
        LLMResponse(content="好的。"),
    ])
    ChatOrchestrator(fake).handle_message(
        db_session, employee_no="VE-0001", message="查一下", session_id=None
    )
    assert captured["limit"] == 3


# ---- 调用治理（测试 9） ----


def test_memory_tool_third_call_returns_limit_and_no_retrieval(db_session, monkeypatch):
    retrieve_calls = []

    def fake_retrieve(db, *, owner_employee_no, query, current_session_id, limit=3, max_chars=1200):
        retrieve_calls.append(query)
        return []

    monkeypatch.setattr("app.services.gateway.retrieve_for_prompt", fake_retrieve)
    fake = _RecordingLLM([
        _memory_tool_call("m1", "问题一"),
        _memory_tool_call("m2", "问题二"),
        _memory_tool_call("m3", "问题三"),
    ])
    result = ChatOrchestrator(fake).handle_message(
        db_session, employee_no="VE-0001", message="查一下", session_id=None
    )

    assert len(retrieve_calls) == 2
    assert result.tool_cards[2].decision == "limit"
    assert result.message == "工具调用次数过多，请重试。"


def test_memory_tool_duplicate_query_returns_duplicate_and_no_retrieval(db_session, monkeypatch):
    retrieve_calls = []

    def fake_retrieve(db, *, owner_employee_no, query, current_session_id, limit=3, max_chars=1200):
        retrieve_calls.append(query)
        return []

    monkeypatch.setattr("app.services.gateway.retrieve_for_prompt", fake_retrieve)
    fake = _RecordingLLM([
        _memory_tool_call("m1", "张三 IT 账号"),
        _memory_tool_call("m2", " 张三 it 账号 "),
        LLMResponse(content="最终回答。"),
    ])
    result = ChatOrchestrator(fake).handle_message(
        db_session, employee_no="VE-0001", message="查一下", session_id=None
    )

    assert len(retrieve_calls) == 1
    assert result.tool_cards[1].decision == "duplicate_query"
    tool_msgs = _tool_messages(fake.calls, 2)
    assert any("该关键词本轮已检索过" in m["content"] for m in tool_msgs)
    assert result.message == "最终回答。"


# ---- error 态不阻断聊天（测试 10） ----


def test_memory_tool_error_state_does_not_break_chat(db_session, monkeypatch):
    def boom(db, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.services.gateway.retrieve_for_prompt", boom)
    fake = _RecordingLLM([
        _memory_tool_call("m1", "张三"),
        LLMResponse(content="好的，我基于当前会话回答。"),
    ])
    result = ChatOrchestrator(fake).handle_message(
        db_session, employee_no="VE-0001", message="查一下", session_id=None
    )

    assert result.tool_cards[0].decision == "error"
    tool_msgs = _tool_messages(fake.calls, 1)
    assert any("记忆检索失败" in m["content"] for m in tool_msgs)
    assert result.message == "好的，我基于当前会话回答。"
