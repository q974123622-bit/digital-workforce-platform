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
