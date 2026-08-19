"""Sprint 8 AgentTeams 最小接入测试：网关单元 + 群聊任务路径 + 降级。"""

import os

import httpx

from app import models
from app.services.agentteams_gateway import AgentTeamsGateway, AgentTeamsUnavailableError
from app.services.group_chat import send_conversation_message

# 隔离本地 .env 的 Matrix token，避免影响单元测试（login 需走 MockTransport）
os.environ.pop("AGENTTEAMS_MATRIX_TOKEN", None)


def _seed_group(db):
    conv = models.Conversation(
        id="CONV-AT-1",
        kind="group",
        title="协作空间",
        owner_human_no="E10281",
        participants=[
            {"employee_no": "DT-E10281", "role": "organizer"},
            {"employee_no": "VE-0001", "role": "member"},
        ],
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


class FakeGatewayOK:
    def __init__(self):
        pass

    def send_message(self, room_id, text):
        return "evt-1"

    def poll_messages(self, room_id, since=None):
        return [{"sender": "@manager", "body": "【完成】入职准备已全部办妥：账号开通、工牌发放（AgentTeams 汇总）"}]

    @staticmethod
    def parse_completion(messages, request_keyword):
        return AgentTeamsGateway.parse_completion(messages, request_keyword)

    def close(self):
        pass


class FakeGatewayFail:
    def __init__(self):
        pass

    def send_message(self, room_id, text):
        raise AgentTeamsUnavailableError("AgentTeams 不可用")

    def close(self):
        pass


class FakeOrchestrator:
    def __init__(self):
        self.calls = []

    def create_conversation_task(self, db, **kwargs):
        self.calls.append(kwargs)
        return None


# ---------- 网关单元（Fake Matrix over httpx） ----------


def test_gateway_login_and_rooms(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"access_token": "tok-1"})
        if request.url.path.endswith("/joined_rooms"):
            return httpx.Response(200, json={"joined_rooms": ["!room:local"]})
        return httpx.Response(404)

    g = AgentTeamsGateway(base_url="http://matrix.test", user="admin", password="pw", timeout=2)
    monkeypatch.setattr(g, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    assert g.login() == "tok-1"
    assert g.joined_rooms() == ["!room:local"]
    g.close()


def test_gateway_login_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errcode": "M_FORBIDDEN"})

    g = AgentTeamsGateway(base_url="http://matrix.test", user="admin", password="bad", timeout=2)
    g._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        g.login()
        raised = False
    except AgentTeamsUnavailableError:
        raised = True
    assert raised
    g.close()


def test_gateway_send_poll_parse():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"access_token": "tok-1"})
        if request.method == "PUT":
            return httpx.Response(200, json={"event_id": "evt-x"})
        if "messages" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "chunk": [
                        {"type": "m.room.message", "sender": "@m", "content": {"msgtype": "m.text", "body": "进行中"}},
                        {"type": "m.room.message", "sender": "@m", "content": {"msgtype": "m.text", "body": "【完成】已处理任务"}},
                    ]
                },
            )
        return httpx.Response(404)

    g = AgentTeamsGateway(base_url="http://matrix.test", user="admin", password="pw", timeout=2)
    g._client = httpx.Client(transport=httpx.MockTransport(handler))
    g.send_message("!room:local", "任务")
    msgs = g.poll_messages("!room:local")
    assert len(msgs) == 2
    report = g.parse_completion(msgs, "任务")
    assert report is not None and "完成" in report
    g.close()


# ---------- 群聊任务路径 ----------


def test_group_chat_agentteams_path(db_session, monkeypatch):
    conv = _seed_group(db_session)
    monkeypatch.setattr("app.services.group_chat.AgentTeamsGateway", lambda: FakeGatewayOK())
    classifier = lambda db, **kw: True  # noqa: E731 强制判为任务

    send_conversation_message(
        db_session,
        conversation=conv,
        actor_no="E10281",
        content="帮王小明完成入职准备",
        classifier=classifier,
    )

    run = db_session.query(models.TaskRun).filter(models.TaskRun.conversation_id == conv.id).one()
    assert run.source == "agentteams"
    assert run.status == "completed"
    assert "入职准备" in run.summary
    # 汇报回传到对话
    msgs = db_session.query(models.ConversationMessage).filter(models.ConversationMessage.conversation_id == conv.id).all()
    assert any("【完成】" in m.content for m in msgs)
    # 审计记录 send + receive
    events = db_session.query(models.AuditEvent).filter(models.AuditEvent.plugin_id.like("agentteams:%")).all()
    assert {e.action for e in events} >= {"send", "receive"}


def test_group_chat_fallback_builtin(db_session, monkeypatch):
    conv = _seed_group(db_session)
    monkeypatch.setattr("app.services.group_chat.AgentTeamsGateway", lambda: FakeGatewayFail())
    classifier = lambda db, **kw: True  # noqa: E731
    fake_orch = FakeOrchestrator()

    send_conversation_message(
        db_session,
        conversation=conv,
        actor_no="E10281",
        content="帮王小明完成入职准备",
        classifier=classifier,
        orchestrator=fake_orch,
    )

    assert len(fake_orch.calls) == 1  # 降级到内置编排
    assert fake_orch.calls[0]["request"] == "帮王小明完成入职准备"
    assert db_session.query(models.TaskRun).filter(models.TaskRun.conversation_id == conv.id).count() == 0
