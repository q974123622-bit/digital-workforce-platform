"""Sprint 9 群聊逐人反馈测试：房间消息回传会话 + subtasks 状态。"""

import re

from app import models
from app.services.group_chat import send_conversation_message


def _seed_group(db):
    conv = models.Conversation(
        id="CONV-FB-1",
        kind="group",
        title="协作空间",
        owner_human_no="E10281",
        participants=[
            {"employee_no": "DT-E10281", "role": "organizer"},
            {"employee_no": "VE-0001", "role": "member"},
            {"employee_no": "VE-0002", "role": "member"},
            {"employee_no": "VE-0003", "role": "member"},
        ],
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


class FakeGatewayFeedback:
    def __init__(self):
        self.task_id = ""

    def send_message(self, room_id, text):
        self.task_id = re.search(r"id=(T-[A-Z0-9-]+)", text).group(1)
        return "evt-send"

    def poll_messages(self, room_id, since=None):
        return [
            {
                "sender": "@dwp-ve-0002:matrix-local.agentteams.io:18080",
                "body": f"id={self.task_id} 收到，我来确认入职制度与材料",
                "event_id": "$evt-fb-002",
                "ts": 9999999999999,
            },
            {
                "sender": "@dwp-ve-0003:matrix-local.agentteams.io:18080",
                "body": f"id={self.task_id} IT 部分完成，账号清单已交付；" + ("完整反馈。" * 80) + "反馈结束",
                "event_id": "$evt-fb-003",
                "ts": 9999999999999,
            },
            {
                "sender": "@manager:matrix-local.agentteams.io:18080",
                "body": f"TASK_COMPLETED id={self.task_id} 协作计划已确认（AgentTeams 汇总）",
                "event_id": "$evt-fb-000",
                "ts": 9999999999999,
            },
        ]

    @staticmethod
    def parse_completion(messages, request_keyword, since_ts=None, task_id=None, exclude_senders=None):
        from app.services.agentteams_gateway import AgentTeamsGateway

        return AgentTeamsGateway.parse_completion(
            messages,
            request_keyword,
            since_ts=since_ts,
            task_id=task_id,
            exclude_senders=exclude_senders,
        )

    def close(self):
        pass


def test_group_feedback_is_not_polled_when_agentteams_is_disabled(db_session, monkeypatch):
    conv = _seed_group(db_session)
    fake = FakeGatewayFeedback()
    monkeypatch.setattr("app.services.group_chat.AgentTeamsGateway", lambda: fake)
    monkeypatch.setattr("app.services.group_chat.config.team_backend_mode", lambda: "auto")
    classifier = lambda db, **kw: True  # noqa: E731

    send_conversation_message(
        db_session,
        conversation=conv,
        actor_no="E10281",
        content="帮王小明完成入职准备",
        classifier=classifier,
    )

    run = db_session.query(models.TaskRun).filter(models.TaskRun.conversation_id == conv.id).one()
    assert run.source == "builtin"
    assert fake.task_id == ""
    # 旧房间反馈不得再写入会话。
    msgs = (
        db_session.query(models.ConversationMessage)
        .filter(models.ConversationMessage.conversation_id == conv.id)
        .all()
    )
    assert not any("IT 部分完成" in m.content or "反馈结束" in m.content for m in msgs)
