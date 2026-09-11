"""Sprint 4 Chat + LLM Provider 测试（FakeLLM，不依赖真实 DeepSeek）。"""

from app.services.chat import ChatOrchestrator, ChatResult, ToolCard
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
    """场景 A：AI员工平台问 IT 服务 → 工具经 Gateway Allow → 正常回答。"""
    fake = FakeLLM(_tool_script("KB-IT-SERVICE", "VPN 怎么连", "根据 IT 服务知识库，VPN 连接流程如下…"))
    orchestrator = ChatOrchestrator(fake)
    result = orchestrator.handle_message(
        db_session,
        employee_no="AI-GENERAL",
        message="查询一下内部制度。",
        session_id=None,
    )
    assert result.message == "根据 IT 服务知识库，VPN 连接流程如下…"
    assert len(result.tool_cards) == 1
    assert result.tool_cards[0].decision == "allow"
    assert result.tool_cards[0].name == "IT 服务知识库"
    assert result.tool_cards[0].source == "demo"
    assert result.policy_denied is None
    # 工具结果确实来自 Gateway（虚构内容，带知识库名称和 source=demo 标签）
    tool_msgs = [m for m in fake.calls[1] if m["role"] == "tool"]
    assert any("知识库：IT 服务知识库" in m["content"] and "source=demo" in m["content"] for m in tool_msgs)


def test_chat_orchestrator_labels_internal_knowledge_base(db_session, monkeypatch):
    """真实知识引擎检索时，卡片和工具消息必须显示真实库名与 source=internal。"""
    monkeypatch.setenv("DWP_KB_MODE", "internal")

    def fake_search_knowledge(*_args, **_kwargs):
        return {
            "ok": True,
            "decision": "allow",
            "policy_id": "POLICY-001",
            "data": {
                "source": "internal",
                "knowledge_base_id": "KB-IT-SERVICE",
                "query": "VPN 怎么连",
                "hits": [{"title": "VPN 接入指引", "snippet": "使用企业 VPN 客户端连接。"}],
            },
        }

    monkeypatch.setattr("app.services.chat.search_knowledge", fake_search_knowledge)
    fake = FakeLLM(_tool_script("KB-IT-SERVICE", "VPN 怎么连", "已根据真实知识库确认 VPN 连接方式。"))
    result = ChatOrchestrator(fake).handle_message(
        db_session,
        employee_no="AI-GENERAL",
        message="查询一下 VPN 连接方式。",
        session_id=None,
    )
    assert result.tool_cards[0].name == "IT 服务知识库"
    assert result.tool_cards[0].source == "internal"
    tool_message = next(m for m in fake.calls[1] if m["role"] == "tool")["content"]
    assert "知识库：IT 服务知识库" in tool_message
    assert "source=internal" in tool_message
    assert "source=demo" not in tool_message
    assert "Authorization" not in tool_message
    assert "DWP_INTERNAL_KB_" not in tool_message


def test_chat_endpoint_returns_internal_source_label(client, monkeypatch):
    """Chat API 必须保留真实库名称和 source 字段，供前端渲染来源标签。"""
    result = ChatResult(
        session_id="S-SOURCE",
        trace_id="T-SOURCE",
        message="已确认。",
        tool_cards=[
            ToolCard(
                plugin_id="knowledge:KB-IT-SERVICE",
                name="IT 服务知识库",
                decision="allow",
                source="internal",
                policy_id="POLICY-001",
            )
        ],
    )
    monkeypatch.setattr("app.routers.employees.run_agent", lambda *_args, **_kwargs: result)
    response = client.post("/api/v1/employees/AI-GENERAL/chat", json={"message": "查询 VPN。"})
    assert response.status_code == 200
    assert response.json()["tool_cards"][0] == {
        "plugin_id": "knowledge:KB-IT-SERVICE",
        "name": "IT 服务知识库",
        "decision": "allow",
        "source": "internal",
        "policy_id": "POLICY-001",
        "reason": None,
    }


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


def test_chat_guard_blocks_query_answer_without_tool(db_session):
    fake = FakeLLM([
        LLMResponse(content="金融科技库里可能有区块链和风控。"),
        LLMResponse(content="金融科技库里可能有区块链和风控。"),
    ])
    result = ChatOrchestrator(fake).handle_message(
        db_session, employee_no="DT-E20999", message="查询一下金融科技知识库", session_id=None,
    )
    assert result.tool_cards == []
    assert "无法确认" in result.message
    assert "区块链" not in result.message
    assert len(fake.calls) == 2


def test_system_prompt_hides_inaccessible_kb_names(db_session):
    fake = FakeLLM([LLMResponse(content="好的")])
    ChatOrchestrator(fake).handle_message(
        db_session, employee_no="DT-E20999", message="你好", session_id=None,
    )
    system_prompt = fake.calls[0][0]["content"]
    assert "公共知识（L1）" not in system_prompt
    assert "示例客户敏感信息库" not in system_prompt
    assert "IT 服务知识库" not in system_prompt


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
