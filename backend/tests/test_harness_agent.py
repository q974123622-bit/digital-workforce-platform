import json

from app import models
from app.services.harness_agent import run_agent
from app.services.runtime_adapter import RuntimeResult


def test_harness_agent_labels_internal_knowledge_card(db_session, monkeypatch):
    """委派链路的工具卡片也使用真实库名称和审计里的实际来源。"""

    class FakeRuntime:
        def __init__(self, **_kwargs):
            pass

        def run(self, **_kwargs):
            return RuntimeResult(mode="harness", ok=True, result="已根据真实知识库确认。")

    monkeypatch.setattr("app.services.harness_agent.DockerHarnessRuntimeAdapter", FakeRuntime)
    monkeypatch.setattr("app.services.harness_agent.runtime_manager.mark_busy", lambda *_args: None)
    monkeypatch.setattr("app.services.harness_agent.runtime_manager.mark_ready", lambda *_args: None)
    db_session.add(
        models.AuditEvent(
            trace_id="T-LABEL",
            employee_id="AI-GENERAL",
            plugin_id="KB-IT-SERVICE",
            knowledge_base_id="KB-IT-SERVICE",
            action="read",
            decision="allow",
            result_summary=json.dumps(
                {"source": "internal", "knowledge_base_id": "KB-IT-SERVICE", "hit_count": 1},
                ensure_ascii=False,
            ),
        )
    )
    db_session.commit()

    result = run_agent(
        db_session,
        employee_id="AI-GENERAL",
        requester_human_no="E10281",
        message="查询 VPN 连接方式。",
        trace_id="T-LABEL",
    )
    card = next(card for card in result.tool_cards if card.plugin_id == "KB-IT-SERVICE")
    assert card.name == "IT 服务知识库"
    assert card.source == "internal"


def test_harness_agent_keeps_delegated_knowledge_source(db_session, monkeypatch):
    """分身没有知识库授权、只能委派检索：来源卡片必须仍然挂在分身这条回复上。

    审计行的 employee_id 是被委派方（AI-INVESTMENT），若仍按「只有我自己」过滤，
    tool_cards 会全空，前端就看不到任何来源。同时不能被同一 trace 里其他成员的
    检索串台（群聊共用 trace_id）。
    """

    class FakeRuntime:
        def __init__(self, **_kwargs):
            pass

        def run(self, **_kwargs):
            return RuntimeResult(mode="harness", ok=True, result="已委派同事确认。")

    monkeypatch.setattr("app.services.harness_agent.DockerHarnessRuntimeAdapter", FakeRuntime)
    monkeypatch.setattr("app.services.harness_agent.runtime_manager.mark_busy", lambda *_args: None)
    monkeypatch.setattr("app.services.harness_agent.runtime_manager.mark_ready", lambda *_args: None)
    db_session.add(models.DelegationRun(
        id="D-DELEGATED", trace_id="T-DELEGATED", conversation_id="harness",
        requester_human_no="E10281", sender_employee_id="DT-E10281",
        recipient_employee_id="AI-INVESTMENT", action="delegate",
        goal="融资融券是啥", reason="Harness 自主委派", status="completed",
    ))
    db_session.add_all([
        models.AuditEvent(
            trace_id="T-DELEGATED", employee_id="AI-INVESTMENT", plugin_id="KB-SECURITIES",
            knowledge_base_id="KB-SECURITIES", action="read", decision="allow",
            result_summary=json.dumps({
                "source": "internal", "knowledge_base_id": "KB-SECURITIES", "hit_count": 3,
                "documents": [{"title": "融资融券业务问答"}, {"title": "交易规则要点"}],
            }, ensure_ascii=False),
        ),
        # 同一 trace 里其他成员的检索（群聊共用 trace_id）：不得挂到这条回复下。
        models.AuditEvent(
            trace_id="T-DELEGATED", employee_id="AI-FUND", plugin_id="KB-IT-SERVICE",
            knowledge_base_id="KB-IT-SERVICE", action="read", decision="allow",
            result_summary=json.dumps({
                "source": "internal", "knowledge_base_id": "KB-IT-SERVICE", "hit_count": 1,
                "documents": [{"title": "VPN 手册"}],
            }, ensure_ascii=False),
        ),
    ])
    db_session.commit()

    result = run_agent(
        db_session,
        employee_id="DT-E10281",
        requester_human_no="E10281",
        message="融资融券是啥",
        trace_id="T-DELEGATED",
    )
    card = next(card for card in result.tool_cards if card.knowledge_base_id == "KB-SECURITIES")
    assert card.name == "证券业务知识库"
    assert card.agent_employee_id == "AI-INVESTMENT"
    assert card.agent_employee_name == "投资分析AI员工"
    assert card.hit_count == 3
    assert card.citation is not None
    assert card.citation["document_title"] == "融资融券业务问答"
    assert card.citation["document_titles"] == ["融资融券业务问答", "交易规则要点"]
    assert card.citation["agent_employee_id"] == "AI-INVESTMENT"
    assert card.citation["preview_available"] is False
    assert [item.knowledge_base_id for item in result.tool_cards] == ["KB-SECURITIES"]


def test_harness_agent_own_search_is_not_labelled_as_delegated(db_session, monkeypatch):
    """自己查的卡片不标「由 XX 检索」，避免误导用户。"""

    class FakeRuntime:
        def __init__(self, **_kwargs):
            pass

        def run(self, **_kwargs):
            return RuntimeResult(mode="harness", ok=True, result="已确认。")

    monkeypatch.setattr("app.services.harness_agent.DockerHarnessRuntimeAdapter", FakeRuntime)
    monkeypatch.setattr("app.services.harness_agent.runtime_manager.mark_busy", lambda *_args: None)
    monkeypatch.setattr("app.services.harness_agent.runtime_manager.mark_ready", lambda *_args: None)
    db_session.add(models.AuditEvent(
        trace_id="T-OWN", employee_id="AI-INVESTMENT", plugin_id="KB-SECURITIES",
        knowledge_base_id="KB-SECURITIES", action="read", decision="allow",
        result_summary=json.dumps({"source": "internal", "knowledge_base_id": "KB-SECURITIES", "hit_count": 2}),
    ))
    db_session.commit()

    result = run_agent(
        db_session,
        employee_id="AI-INVESTMENT",
        requester_human_no="E10281",
        message="融资融券是啥",
        trace_id="T-OWN",
    )
    card = next(card for card in result.tool_cards if card.knowledge_base_id == "KB-SECURITIES")
    assert card.agent_employee_id is None
    assert card.agent_employee_name is None


def test_harness_token_round_trips_conversation_id():
    from app.services.harness_token import issue_token, verify_token

    token = issue_token(
        employee_id="DT-E10281", requester_human_no="E10281", trace_id="T-CONV",
        depth=0, goal="帮我确认 IT 制度", conversation_id="CONV-TOKEN",
    )
    assert verify_token(token).conversation_id == "CONV-TOKEN"

    # 旧令牌没有 conversation_id claim → 默认空串，向后兼容
    legacy = issue_token(
        employee_id="DT-E10281", requester_human_no="E10281", trace_id="T-OLD", depth=0,
    )
    assert verify_token(legacy).conversation_id == ""


def test_agent_delegate_hands_conversation_history_to_target(db_session, monkeypatch):
    """委派链路带上下文：被委派方拿到该会话的最近对话，而不是只看到一句被转述的问题。"""
    from app.routers import internal
    from app.services.chat import ChatResult
    from app.services.harness_token import issue_token, verify_token

    db_session.add(models.Conversation(
        id="CONV-DELEGATE", kind="group", title="", owner_human_no="E10281",
        participants=[
            {"employee_no": "DT-E10281", "role": "organizer"},
            {"employee_no": "AI-GENERAL", "role": "member"},
        ],
    ))
    db_session.add_all([
        models.ConversationMessage(
            conversation_id="CONV-DELEGATE", participant_no="E10281", participant_name="张三",
            role="user", content="帮我确认 IT 制度", seq=1, generation=0,
        ),
        models.ConversationMessage(
            conversation_id="CONV-DELEGATE", participant_no="DT-E10281",
            participant_name="张三的数字分身", role="assistant", content="我来协调。",
            seq=2, generation=0,
        ),
    ])
    db_session.commit()

    captured: dict = {}

    def fake_run_agent(db, **kwargs):
        captured.update(kwargs)
        return ChatResult(session_id="H-1", trace_id=kwargs["trace_id"], message="已确认。")

    monkeypatch.setattr("app.services.harness_agent.run_agent", fake_run_agent)

    claims = verify_token(issue_token(
        employee_id="DT-E10281", requester_human_no="E10281", trace_id="T-DELEGATE",
        depth=0, goal="帮我确认 IT 制度", conversation_id="CONV-DELEGATE",
    ))
    result = internal._agent_delegate(
        db_session, claims,
        internal.AgentDelegateToolIn(target_agent_id="AI-GENERAL", question="IT 制度怎么走？"),
    )

    assert result["answer"] == "已确认。"
    assert captured["conversation_id"] == "CONV-DELEGATE"
    assert [m["content"] for m in captured["history"]] == ["帮我确认 IT 制度", "我来协调。"]
    assert captured["history"][1]["speaker"] == "张三的数字分身"
