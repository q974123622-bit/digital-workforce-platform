"""企业微信智能机器人长连接逻辑测试。

覆盖：staff ID -> 分身路由、fixed/twin 双维路由、消息处理返回回答文本、
DWP_WECOM_BOTS 配置解析（含 legacy 回退），以及 WebSocket 客户端的
订阅/回复/心跳/消息分发的 JSON 协议结构（明文，无加解密）。
"""

import asyncio
import json

import pytest

from app import models
from app.services import config
from app.services.wecom_bot import WeComBotClient


class _FakeWS:
    """记录 send 内容的最小 WebSocket 桩，便于校验协议 JSON。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))


def _seed_wecom_binding(db_session, external_user_id: str = "staff-001") -> None:
    db_session.add(
        models.DirectoryBinding(
            provider="wecom",
            corp_id="bot-id",
            external_user_id=external_user_id,
            human_employee_no="E10281",
            status="active",
        )
    )
    db_session.commit()


def _client() -> WeComBotClient:
    return WeComBotClient(bot_id="bot-id", secret="secret", ws_url="wss://example.invalid")


def _spec(employee_no: str = "AI-INVESTMENT", alias: str = "investment", mode: str = "fixed") -> config.WeComBotSpec:
    return config.WeComBotSpec(
        bot_id="bot-id", secret="secret", name="投资分析AI员工",
        alias=alias, mode=mode, employee_no=employee_no if mode == "fixed" else None,
    )


def _msg_data(content: str = "你好") -> dict:
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-123"},
        "body": {
            "msgtype": "text",
            "text": {"content": content},
            "from": {"userid": "staff-001"},
        },
    }


# ---- DWP_WECOM_BOTS 配置解析 ----


def test_wecom_bots_parses_multi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DWP_WECOM_BOTS", json.dumps([
        {"bot_id": "b-general", "secret": "s1", "name": "AI员工平台", "alias": "general",
         "mode": "fixed", "employee_no": "AI-GENERAL"},
        {"bot_id": "b-twin", "secret": "s2", "name": "数字分身", "alias": "twin", "mode": "twin"},
    ]))
    bots = config.wecom_bots()
    assert len(bots) == 2
    assert bots[0].mode == "fixed" and bots[0].employee_no == "AI-GENERAL"
    assert bots[0].alias == "general"
    assert bots[1].mode == "twin" and bots[1].employee_no is None


def test_wecom_bots_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DWP_WECOM_BOTS", raising=False)
    monkeypatch.setenv("DWP_WECOM_BOT_ID", "legacy-bot")
    monkeypatch.setenv("DWP_WECOM_BOT_SECRET", "legacy-secret")
    bots = config.wecom_bots()
    assert len(bots) == 1
    assert bots[0].bot_id == "legacy-bot"
    assert bots[0].mode == "twin"
    assert config.wecom_bot_configured() is True


def test_wecom_bots_skips_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DWP_WECOM_BOTS", json.dumps([
        {"bot_id": "b-general", "secret": "", "name": "AI员工平台", "alias": "general",
         "mode": "fixed", "employee_no": "AI-GENERAL"},
        {"bot_id": "b-invest", "secret": "real", "name": "投资分析AI员工", "alias": "investment",
         "mode": "fixed", "employee_no": "AI-INVESTMENT"},
    ]))
    monkeypatch.delenv("DWP_WECOM_BOT_ID", raising=False)
    monkeypatch.delenv("DWP_WECOM_BOT_SECRET", raising=False)
    bots = config.wecom_bots()
    assert [b.alias for b in bots] == ["investment"]


# ---- 双维路由与消息处理 ----


def test_route_to_twin(db_session) -> None:
    from app.services.wecom_adapter import route_to_twin

    _seed_wecom_binding(db_session)
    human, twin = route_to_twin(db_session, "staff-001")
    assert human.employee_no == "E10281"
    assert twin.employee_no == "DT-E10281"


def test_resolve_target_fixed(db_session) -> None:
    from app.services.wecom_adapter import resolve_target

    _seed_wecom_binding(db_session)
    human, employee = resolve_target(db_session, _spec(employee_no="AI-INVESTMENT"), "staff-001")
    assert human.employee_no == "E10281"
    assert employee.employee_no == "AI-INVESTMENT"
    assert employee.type == "virtual"


def test_resolve_target_fixed_unbound(db_session) -> None:
    from fastapi import HTTPException

    from app.services.wecom_adapter import resolve_target

    with pytest.raises(HTTPException) as exc:
        resolve_target(db_session, _spec(), "unknown-staff")
    assert exc.value.status_code == 404


def test_resolve_target_twin_uses_owner(db_session) -> None:
    from app.services.wecom_adapter import resolve_target

    _seed_wecom_binding(db_session)
    # 数字分身 bot（mode=twin）应路由到发起人自己的分身。
    human, employee = resolve_target(db_session, _spec(mode="twin"), "staff-001")
    assert human.employee_no == "E10281"
    assert employee.employee_no == "DT-E10281"


def test_handle_bot_message_fixed(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app.services.wecom_adapter import handle_bot_message

    _seed_wecom_binding(db_session)
    seen: dict = {}
    monkeypatch.setattr(
        "app.services.wecom_adapter._answer",
        lambda db, employee_id, requester_human_no, content, trace_id=None: seen.update(
            employee_id=employee_id, requester=requester_human_no
        ) or SimpleNamespace(message="投资分析结果", tool_cards=[]),
    )
    answer = handle_bot_message("staff-001", "这只股票怎么样", _spec())
    assert answer == "投资分析结果"
    assert seen["employee_id"] == "AI-INVESTMENT"
    assert seen["requester"] == "E10281"


def test_handle_bot_message_unbound(db_session) -> None:
    from app.services.wecom_adapter import handle_bot_message

    result = handle_bot_message("unknown-staff", "你好")
    assert result.startswith("抱歉，暂时无法处理")


def test_start_execution_conversation_includes_alias(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.services.wecom_adapter import start_execution

    _seed_wecom_binding(db_session)
    monkeypatch.setattr(
        "app.services.wecom_adapter._answer",
        lambda db, employee_id, requester_human_no, content, trace_id=None: SimpleNamespace(
            message="ok", tool_cards=[],
        ),
    )
    ctx = start_execution("staff-001", "你好", _spec(alias="investment"))
    assert ctx.employee_id == "AI-INVESTMENT"
    row = db_session.get(models.AgentExecution, ctx.execution_id)
    assert row.conversation_id.startswith("WECOM-investment-E10281-")


def test_run_execution_emits_events(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.services import execution_events
    from app.services.wecom_adapter import BotExecutionContext, run_execution

    _seed_wecom_binding(db_session)
    execution = execution_events.create_execution(
        db_session, conversation_id="WECOM-TEST", trigger_message_seq=1,
        primary_employee_id="DT-E10281",
    )
    ctx = BotExecutionContext(
        execution_id=execution.id, trace_id=execution.trace_id,
        employee_id="DT-E10281", requester_human_no="E10281",
        employee_name="架构部张三",
    )
    monkeypatch.setattr(
        "app.services.wecom_adapter._answer",
        lambda db, employee_id, requester_human_no, content, trace_id=None: SimpleNamespace(
            message="最终回答", tool_cards=[],
        ),
    )
    assert run_execution(ctx, "你好") == "最终回答"

    check = SessionLocal()
    try:
        row = check.get(models.AgentExecution, execution.id)
        assert row.status == "completed"
        events = check.scalars(select(models.AgentExecutionEvent).where(
            models.AgentExecutionEvent.execution_id == execution.id,
        ).order_by(models.AgentExecutionEvent.event_seq)).all()
        types = [event.event_type for event in events]
        assert "agent_started" in types
        assert "answer_done" in types
    finally:
        check.close()


# ---- WebSocket 客户端协议结构 ----


def test_subscribe_payload() -> None:
    ws = _FakeWS()
    asyncio.run(_client()._subscribe(ws))
    assert len(ws.sent) == 1
    msg = ws.sent[0]
    assert msg["cmd"] == "aibot_subscribe"
    assert msg["body"] == {"bot_id": "bot-id", "secret": "secret"}
    assert msg["headers"]["req_id"]


def test_respond_payload() -> None:
    ws = _FakeWS()
    asyncio.run(_client()._respond(ws, "req-123", "最终回答"))
    assert ws.sent == [{
        "cmd": "aibot_respond_msg",
        "headers": {"req_id": "req-123"},
        "body": {"msgtype": "markdown", "markdown": {"content": "最终回答"}},
    }]


def test_heartbeat_sends_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.wecom_bot as bot_mod

    monkeypatch.setattr(bot_mod, "HEARTBEAT_INTERVAL", 0)
    ws = _FakeWS()
    client = _client()

    async def run() -> None:
        task = asyncio.create_task(client._heartbeat(ws))
        while not ws.sent:
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert ws.sent[0]["cmd"] == "ping"


def test_on_message_dry_run_passes_spec(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.wecom_bot.config.wecom_dry_run", lambda: True)
    calls: list = []
    monkeypatch.setattr(
        "app.services.wecom_bot.handle_bot_message",
        lambda from_user, content, bot=None: calls.append((from_user, content, bot)) or "回答",
    )
    ws = _FakeWS()
    client = WeComBotClient(spec=_spec(employee_no="AI-INVESTMENT"), ws_url="wss://example.invalid")
    asyncio.run(client._on_message(ws, _msg_data()))
    assert len(calls) == 1
    assert calls[0][0] == "staff-001"
    assert calls[0][2] is not None and calls[0][2].alias == "investment"
    assert ws.sent == []  # dry-run 下不真发


def test_on_message_streams_execution(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.wecom_adapter import BotExecutionContext

    monkeypatch.setattr("app.services.wecom_bot.config.wecom_dry_run", lambda: False)
    monkeypatch.setattr(
        "app.services.wecom_bot.start_execution",
        lambda from_user, content, bot=None: BotExecutionContext(
            execution_id="EX-TEST", trace_id="T-TEST", employee_id="DT-E10281",
            requester_human_no="E10281", employee_name="架构部张三",
        ),
    )
    monkeypatch.setattr(
        "app.services.wecom_bot.run_execution",
        lambda ctx, content: "最终回答",
    )
    monkeypatch.setattr(
        "app.services.wecom_bot._poll_events",
        lambda execution_id, after_seq: [],
    )
    ws = _FakeWS()
    asyncio.run(_client()._on_message(ws, _msg_data()))
    assert ws.sent
    assert all(m["body"]["msgtype"] == "stream" for m in ws.sent)
    assert ws.sent[-1]["body"]["stream"]["finish"] is True
    assert "最终回答" in ws.sent[-1]["body"]["stream"]["content"]
    assert all(m["headers"]["req_id"] == "req-123" for m in ws.sent)


def test_on_message_ignores_empty_content(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.wecom_bot.config.wecom_dry_run", lambda: False)
    monkeypatch.setattr(
        "app.services.wecom_bot.handle_bot_message",
        lambda from_user, content, bot=None: pytest.fail("不应处理空消息"),
    )
    ws = _FakeWS()
    asyncio.run(_client()._on_message(ws, _msg_data(content="   ")))
    assert ws.sent == []


def test_on_event_disconnected(caplog) -> None:
    client = _client()
    client._on_event({"body": {"event": {"eventtype": "disconnected_event"}}})
    assert "disconnected_event" in caplog.text
