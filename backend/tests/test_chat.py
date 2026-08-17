"""Sprint 4 Chat + LLM Provider 测试（FakeLLM，不依赖真实 DeepSeek）。"""

from app.services.chat import ChatOrchestrator
from app.services.llm import LLMProvider, LLMResponse, LLMUnavailableError, ToolCall


class FakeLLM(LLMProvider):
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


def _tool_script(kb_id, query, final_answer):
    return [
        LLMResponse(
            content="",
            tool_calls=[ToolCall(id="tc-1", name="search_knowledge", arguments={"knowledge_base_id": kb_id, "query": query})],
        ),
        LLMResponse(content=final_answer),
    ]


def test_chat_orchestrator_tool_allow_path(db_session):
    """场景 A：正式分身问内部制度 → 工具经 Gateway Allow → 正常回答。"""
    fake = FakeLLM(_tool_script("KB-INTERNAL", "内部制度", "根据内部制度库，入职流程如下…"))
    orchestrator = ChatOrchestrator(fake)
    result = orchestrator.handle_message(
        db_session,
        employee_no="DT-E10281",
        message="查询一下内部制度。",
        session_id=None,
    )
    assert result.message == "根据内部制度库，入职流程如下…"
    assert len(result.tool_cards) == 1
    assert result.tool_cards[0].decision == "allow"
    assert result.policy_denied is None
    # 工具结果确实来自 Gateway（虚构内容，带 source=demo 标签）
    tool_msgs = [m for m in fake.calls[1] if m["role"] == "tool"]
    assert any("source=demo" in m["content"] for m in tool_msgs)


def test_chat_endpoint_intern_deny(db_session):
    """场景 B：实习生分身同样问题 → Policy DENY → 权限不足提示 + Deny 卡片。"""
    fake = FakeLLM(_tool_script("KB-INTERNAL", "内部制度", "当前身份无权访问该知识库。"))
    orchestrator = ChatOrchestrator(fake)
    result = orchestrator.handle_message(
        db_session,
        employee_no="DT-E20999",
        message="查询一下内部制度。",
        session_id=None,
    )
    assert result.policy_denied is not None
    assert result.policy_denied.decision == "deny"
    assert result.policy_denied.policy_id == "POLICY-002"
    assert result.message == "当前身份无权访问该知识库。"


def test_chat_endpoint_virtual_onboarding_allow(db_session):
    """VE-0001 只允许公共 + 入职 Demo 知识库 → KB-ONBOARD Allow。"""
    fake = FakeLLM(_tool_script("KB-ONBOARD", "第一天做什么", "第一天先到 HR 报到，签署合同并领取工牌。"))
    orchestrator = ChatOrchestrator(fake)
    result = orchestrator.handle_message(
        db_session,
        employee_no="VE-0001",
        message="新员工第一天要做什么？",
        session_id=None,
    )
    assert result.tool_cards and result.tool_cards[0].decision == "allow"
    assert "报到" in result.message


def test_chat_saves_session_history(client, db_session):
    fake = FakeLLM(
        [
            LLMResponse(content="第一轮回答"),
            LLMResponse(content="第二轮回答"),
        ]
    )
    orchestrator = ChatOrchestrator(fake)
    first = orchestrator.handle_message(db_session, employee_no="DT-E10281", message="你好", session_id=None)
    second = orchestrator.handle_message(db_session, employee_no="DT-E10281", message="继续", session_id=first.session_id)
    assert second.session_id == first.session_id
    msgs = client.get(f"/api/v1/chat/sessions/{first.session_id}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]


def test_llm_safemode_rejects_non_demo():
    from app.services.llm import _assert_safemode

    good = [{"role": "user", "content": "x", "source": "demo"}]
    _assert_safemode(good)
    bad = [{"role": "user", "content": "真实内容", "source": "internal"}]
    try:
        _assert_safemode(bad)
        raised = False
    except LLMUnavailableError:
        raised = True
    assert raised


def test_deepseek_provider_requires_env_key():
    import os

    from app.services.llm import DeepSeekProvider

    old = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        provider = DeepSeekProvider(api_key=None, base_url="https://example.invalid", model="test")
        try:
            provider.chat([{"role": "user", "content": "hi", "source": "demo"}])
            raised = False
        except LLMUnavailableError as exc:
            raised = True
            assert "未配置" in str(exc)
        assert raised
    finally:
        if old is not None:
            os.environ["DEEPSEEK_API_KEY"] = old
