"""Sprint 8 AgentTeams 最小接入测试：网关单元 + 群聊任务路径 + 降级。"""

import os
import re

import httpx

from app import models
from app.services.agentteams_gateway import AgentTeamsGateway, AgentTeamsUnavailableError
from app.services.group_chat import send_conversation_message
from app.services.llm import LLMProvider, LLMResponse
from app.services.runtime_adapter import RuntimeResult
from app.services.team_orchestrator import TeamTaskOrchestrator

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
        self.sent: list[str] = []

    def send_message(self, room_id, text):
        self.sent.append(text)
        return "evt-1"

    def poll_messages(self, room_id, since=None):
        task_id = re.search(r"id=(T-[A-Z0-9-]+)", self.sent[-1]).group(1)
        return [
            {
                "sender": "@manager",
                "body": f"TASK_COMPLETED id={task_id} 入职准备协作计划已确认（AgentTeams 汇总）",
                "ts": 9999999999999,
            }
        ]

    @staticmethod
    def parse_completion(messages, request_keyword, since_ts=None, task_id=None, exclude_senders=None):
        return AgentTeamsGateway.parse_completion(
            messages,
            request_keyword,
            since_ts=since_ts,
            task_id=task_id,
            exclude_senders=exclude_senders,
        )

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


class HybridLLM(LLMProvider):
    def structured_output(self, messages, schema):
        return {
            "subtasks": [
                {
                    "worker_id": "VE-0001",
                    "summary": "执行入职准备",
                    "plugin_ids": ["adp-onboarding"],
                }
            ]
        }

    def chat(self, messages, tools=None):
        return LLMResponse(content="AgentTeams 协作完成，Harness 执行完成。")

    def tool_call(self, messages, tools):
        return self.chat(messages, tools)


class RecordingHarness:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return RuntimeResult(mode="harness", ok=True, result="Harness 已执行数字员工子任务")


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


def test_parse_completion_prefers_task_id():
    msgs = [
        {"sender": "@m", "body": "【完成】上一个任务已办妥（旧任务回执）", "ts": 1111111111111},
        {
            "sender": "@m",
            "body": "✅ 已收到平台任务 id=T-20260820-ABC123：赵仁杰入职（新任务回执）",
            "ts": 2222222222222,
        },
    ]
    # ACK 不能被当作完成。
    assert AgentTeamsGateway.parse_completion(
        msgs, "赵仁杰", since_ts=1000000000000, task_id="T-20260820-ABC123"
    ) is None
    completed = {
        "sender": "@m",
        "body": "TASK_COMPLETED id=T-20260820-ABC123 赵仁杰入职协作已完成",
        "ts": 2222222222223,
    }
    assert AgentTeamsGateway.parse_completion(
        [*msgs, completed], "赵仁杰", since_ts=1000000000000, task_id="T-20260820-ABC123"
    ) == completed["body"]


def test_parse_completion_keeps_full_long_report():
    report = "TASK_COMPLETED id=T-LONG 最终汇报：" + ("完整协作内容。" * 120) + "汇报结束"
    parsed = AgentTeamsGateway.parse_completion(
        [{"sender": "@manager", "body": report, "ts": 100}],
        "最终汇报",
        since_ts=1,
        task_id="T-LONG",
    )
    assert len(report) > 500
    assert parsed == report


def test_parse_completion_excludes_own_task_message():
    """平台自己发送的任务消息（含 task_id）不得被当作回执。"""
    own = {
        "sender": "@platform-bot:matrix-local.agentteams.io:18080",
        "body": "@manager [平台任务 id=T-20260820-DA1E19] 请求者=张三(E10281) 请求=赵仁杰入职",
        "ts": 9999999999999,
    }
    reply = {
        "sender": "@manager:matrix-local.agentteams.io:18080",
        "body": "TASK_COMPLETED id=T-20260820-DA1E19：赵仁杰入职协作已完成",
        "ts": 9999999999998,
    }
    report = AgentTeamsGateway.parse_completion(
        [own, reply],
        "赵仁杰",
        since_ts=1000000000000,
        task_id="T-20260820-DA1E19",
        exclude_senders={"@platform-bot:matrix-local.agentteams.io:18080"},
    )
    assert report == reply["body"]


# ---------- 群聊任务路径 ----------


def test_group_chat_agentteams_path(db_session, monkeypatch):
    conv = _seed_group(db_session)
    monkeypatch.setenv("AGENTTEAMS_ROOM_ID", "!room:test")
    fake = FakeGatewayOK()
    monkeypatch.setattr("app.services.group_chat.AgentTeamsGateway", lambda: fake)
    monkeypatch.setattr("app.services.group_chat.config.team_backend_mode", lambda: "auto")
    classifier = lambda db, **kw: True  # noqa: E731 强制判为任务
    harness = RecordingHarness()
    orchestrator = TeamTaskOrchestrator(HybridLLM(), runtime=harness)

    send_conversation_message(
        db_session,
        conversation=conv,
        actor_no="E10281",
        content="帮王小明完成入职准备",
        classifier=classifier,
        orchestrator=orchestrator,
    )

    run = db_session.query(models.TaskRun).filter(models.TaskRun.conversation_id == conv.id).one()
    assert run.source == "agentteams"
    assert run.status == "completed"
    assert "Harness" in run.summary
    assert harness.calls and harness.calls[0]["employee_id"] == "VE-0001"
    assert run.subtasks[0]["runtime_mode"] == "harness"
    assert run.subtasks[0]["tool_name"] == "入职流程 Workflow"
    assert "流程：入职流程 Workflow" in run.subtasks[0]["result"]
    assert "AgentTeams 汇总" in harness.calls[0]["context"].collaboration_summary
    # 汇报回传到对话
    msgs = db_session.query(models.ConversationMessage).filter(models.ConversationMessage.conversation_id == conv.id).all()
    assert any("TASK_COMPLETED" in m.content for m in msgs)
    # 审计记录 send + receive
    events = db_session.query(models.AuditEvent).filter(models.AuditEvent.plugin_id.like("agentteams:%")).all()
    assert {e.action for e in events} >= {"send", "receive"}
    # 结构化任务消息：携带 task_id / 请求者
    assert fake.sent and "id=T-" in fake.sent[0]
    assert "请求者=张三(E10281)" in fake.sent[0]


def test_group_chat_fallback_builtin(db_session, monkeypatch):
    conv = _seed_group(db_session)
    monkeypatch.setenv("AGENTTEAMS_ROOM_ID", "!room:test")
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
