"""工作线 B：职场/群聊记忆编排测试。

覆盖成功沉淀、多数字员工隔离、成员降级不沉淀、重复写入幂等。
读取侧由 ChatOrchestrator 统一负责（见 test_chat_memory.py），
本文件只验证 group_chat 在 assistant ConversationMessage 落库后的写入侧。
"""

from sqlalchemy import select

from app import models
from app.services.group_chat import send_conversation_message, send_group_message
from app.services.llm import LLMProvider, LLMResponse, LLMUnavailableError


class _FakeLLM(LLMProvider):
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


class _FlakyProvider(LLMProvider):
    """第 1 次调用抛 LLM_UNAVAILABLE，之后正常（模拟单个成员降级）。"""

    def __init__(self, responses):
        self.calls = 0
        self.responses = list(responses)

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            raise LLMUnavailableError("mock key missing")
        return self.responses.pop(0)

    def tool_call(self, messages, tools):
        return self.chat(messages, tools)

    def structured_output(self, messages, schema):
        return {}


def _group_conversation(db_session, conversation_id="CONV-TEST-1"):
    conv = models.Conversation(
        id=conversation_id,
        kind="group",
        title="测试协作",
        owner_human_no="E10281",
        participants=[
            {"employee_no": "DT-E10281", "role": "organizer"},
            {"employee_no": "VE-0001", "role": "member"},
        ],
    )
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)
    return conv


def _direct_conversation(db_session, conversation_id="CONV-DIRECT"):
    conv = models.Conversation(
        id=conversation_id,
        kind="direct",
        title="",
        owner_human_no="E10281",
        participants=[{"employee_no": "DT-E10281", "role": "member"}],
    )
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)
    return conv


def _conversation_memories(db_session, owner=None):
    q = select(models.MemoryEntry).where(models.MemoryEntry.source_type == "conversation")
    if owner is not None:
        q = q.where(models.MemoryEntry.subject_no == owner)
    return list(db_session.scalars(q))


def test_group_memory_direct_conversation_captures_turn(db_session):
    """职场私聊：assistant ConversationMessage 落库后沉淀，owner 为实际回答者。"""
    conv = _direct_conversation(db_session)
    fake = _FakeLLM([LLMResponse(content="HR 材料已完成，IT 账号仍待开通。")])
    send_conversation_message(
        db_session,
        conversation=conv,
        actor_no="E10281",
        content="张三的账号处理好了吗？",
        provider=fake,
    )

    memories = _conversation_memories(db_session)
    assert len(memories) == 1
    entry = memories[0]
    assert entry.subject_no == "DT-E10281"
    assert entry.source_session_id == "CONV-DIRECT"
    assert entry.source_ref.startswith("conversation:CONV-DIRECT:assistant:")
    assert "张三的账号处理好了吗？" in entry.content
    assert "HR 材料已完成，IT 账号仍待开通。" in entry.content


def test_group_memory_multi_employee_isolated(db_session):
    """群聊：两个数字员工各自沉淀，owner 不同、幂等键各自独立。"""
    conv = _group_conversation(db_session)
    fake = _FakeLLM(
        [LLMResponse(content="我先整理入职材料。"), LLMResponse(content="IT 账号当天开通。")]
    )
    send_group_message(
        db_session,
        conversation=conv,
        actor_no="E10281",
        content="帮我准备入职",
        provider=fake,
    )

    memories = _conversation_memories(db_session)
    assert len(memories) == 2
    assert {m.subject_no for m in memories} == {"DT-E10281", "VE-0001"}
    refs = [m.source_ref for m in memories]
    assert len(set(refs)) == 2
    contents = "".join(m.content for m in memories)
    assert "我先整理入职材料。" in contents
    assert "IT 账号当天开通。" in contents


def test_group_memory_degraded_member_not_captured(db_session):
    """成员 LLM 失败走降级文案时，不沉淀该成员的问答记忆。"""
    conv = _group_conversation(db_session)
    flaky = _FlakyProvider([LLMResponse(content="IT 账号当天开通。")])
    send_group_message(
        db_session,
        conversation=conv,
        actor_no="E10281",
        content="帮我准备入职",
        provider=flaky,
    )

    msgs = list(
        db_session.scalars(
            select(models.ConversationMessage).where(
                models.ConversationMessage.conversation_id == conv.id
            )
        )
    )
    degraded = [m for m in msgs if "暂时无法响应" in m.content]
    assert len(degraded) == 1
    memories = _conversation_memories(db_session)
    assert len(memories) == 1
    assert memories[0].subject_no == "VE-0001"


def test_group_memory_capture_idempotent_on_same_seq(db_session):
    """异步/重试复用同一 assistant seq 时，不生成重复记忆。"""
    from app.services import memory_runtime

    ref = "conversation:CONV-TEST-1:assistant:3"
    first = memory_runtime.capture_turn_safely(
        db_session,
        owner_employee_no="DT-E10281",
        source_type="conversation",
        source_session_id="CONV-TEST-1",
        source_ref=ref,
        user_text="q",
        assistant_text="a",
        trace_id="T",
    )
    second = memory_runtime.capture_turn_safely(
        db_session,
        owner_employee_no="DT-E10281",
        source_type="conversation",
        source_session_id="CONV-TEST-1",
        source_ref=ref,
        user_text="q",
        assistant_text="a",
        trace_id="T",
    )
    assert first == second
    assert len(_conversation_memories(db_session)) == 1


# ---- Task 8: 复用规则与审计可观测性 ----


def test_workplace_router_reuses_shared_pipeline():
    """企业微信（若接入）复用职场会话链路；当前无独立 WeCom 服务模块。"""
    import pkgutil

    from app import services
    from app.routers import workplace

    assert workplace.process_conversation_async is not None
    module_names = [m.name for m in pkgutil.iter_modules(services.__path__)]
    assert not any("wecom" in name.lower() for name in module_names)


def test_memory_audit_read_metadata_only(db_session):
    """读取审计只记录命中数量、IDs 和字符数，不复制记忆正文。"""
    from app.services.chat import ChatOrchestrator
    from app.services.llm import LLMResponse
    from app.services.memory_service import capture_turn

    capture_turn(
        db_session,
        owner_employee_no="DT-E10281",
        source_type="chat",
        source_session_id="S-OLD",
        source_ref="chat:S-OLD:assistant:2",
        user_text="张三的 IT 账号怎么样？",
        assistant_text="HR 材料已完成，IT 账号仍待开通。",
        trace_id="T-OLD",
    )
    fake = _FakeLLM([LLMResponse(content="HR 材料已完成。")])
    ChatOrchestrator(fake).handle_message(
        db_session,
        employee_no="DT-E10281",
        message="上次张三的账号处理好了吗？",
        session_id=None,
    )

    reads = list(
        db_session.scalars(
            select(models.AuditEvent).where(models.AuditEvent.action == "memory.read_auto")
        )
    )
    assert reads
    for event in reads:
        summary = event.result_summary or ""
        assert "hits=" in summary
        assert "ids=" in summary
        assert "chars=" in summary
        assert "HR 材料已完成" not in summary
        assert "张三" not in summary


def test_memory_audit_capture_metadata_only(db_session):
    """写入审计只记录 memory_id 与 source_ref，不复制记忆正文。"""
    conv = _direct_conversation(db_session)
    fake = _FakeLLM([LLMResponse(content="HR 材料已完成，IT 账号仍待开通。")])
    send_conversation_message(
        db_session,
        conversation=conv,
        actor_no="E10281",
        content="张三的账号处理好了吗？",
        provider=fake,
    )

    captures = list(
        db_session.scalars(
            select(models.AuditEvent).where(
                models.AuditEvent.action == "memory.capture",
                models.AuditEvent.decision == "allow",
            )
        )
    )
    assert captures
    for event in captures:
        summary = event.result_summary or ""
        assert "memory_id=" in summary
        assert "source_ref=" in summary
        assert "HR 材料已完成" not in summary
        assert "张三的账号处理好了吗？" not in summary


def test_group_memory_workplace_cross_conversation_recall(db_session):
    """§17 #15 E2E：职场私聊会话 A 沉淀记忆，新会话 B 同一数字员工可自动召回。"""
    from app.services.llm import LLMResponse

    # 会话 A：DT-E10281 沉淀一条职场私聊记忆
    conv_a = _direct_conversation(db_session, conversation_id="CONV-RECALL-A")
    fake_a = _FakeLLM([LLMResponse(content="HR 材料已完成，IT 账号仍待开通。")])
    send_conversation_message(
        db_session,
        conversation=conv_a,
        actor_no="E10281",
        content="张三的账号处理好了吗？",
        provider=fake_a,
    )
    assert len(_conversation_memories(db_session, owner="DT-E10281")) == 1

    # 会话 B：新会话，同一数字员工提问，应自动召回会话 A 的记忆
    conv_b = _direct_conversation(db_session, conversation_id="CONV-RECALL-B")
    fake_b = _FakeLLM([LLMResponse(content="HR 材料已完成，IT 账号仍待开通。")])
    send_conversation_message(
        db_session,
        conversation=conv_b,
        actor_no="E10281",
        content="上次张三的账号处理好了吗？",
        provider=fake_b,
    )
    # DT-E10281 收到的最后一条 user 消息应包含自动检索的旧记忆
    user_payload = fake_b.calls[0][-1]["content"]
    assert "【本地相关记忆】" in user_payload
    assert "HR 材料已完成" in user_payload
