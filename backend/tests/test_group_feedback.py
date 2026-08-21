"""Sprint 9 群聊逐人反馈测试：房间消息回传会话 + subtasks 状态。"""

import re

from app import models
from app.services.group_chat import _try_agentteams_task, send_conversation_message


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


def test_group_feedback_writes_per_worker_messages(db_session, monkeypatch):
    conv = _seed_group(db_session)
    monkeypatch.setattr("app.services.group_chat.AgentTeamsGateway", lambda: FakeGatewayFeedback())
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
    assert run.source == "agentteams"
    # 逐人反馈进入会话消息
    msgs = (
        db_session.query(models.ConversationMessage)
        .filter(models.ConversationMessage.conversation_id == conv.id)
        .all()
    )
    participants = {m.participant_no for m in msgs}
    assert "VE-0002" in participants and "VE-0003" in participants
    assert any("IT 部分完成" in m.content for m in msgs)
    assert any("反馈结束" in m.content for m in msgs)
    # subtasks 反映成员状态
    subs = {s.get("worker_no"): s for s in run.subtasks}
    assert subs.get("VE-0003", {}).get("collaboration_status") == "reported"
    assert "反馈结束" in subs.get("VE-0003", {}).get("collaboration_messages", [""])[0]
    assert subs.get("VE-0002", {}).get("collaboration_status") == "acknowledged"


def test_feedback_dedup_across_restart(db_session, monkeypatch):
    """持久化去重：同一房间事件即使再次 poll 也不重复回传（重启后同样生效）。"""
    conv = _seed_group(db_session)
    leader = db_session.get(models.DigitalEmployee, "DT-E10281")

    class FakeOnce:
        def __init__(self):
            self.calls = 0
            self.task_id = ""

        def send_message(self, room_id, text):
            self.task_id = re.search(r"id=(T-[A-Z0-9-]+)", text).group(1)
            return "evt-send"

        def poll_messages(self, room_id, since=None):
            self.calls += 1
            return [
                {
                    "sender": "@dwp-ve-0003:matrix-local.agentteams.io:18080",
                    "body": f"id={self.task_id} IT 部分完成",
                    "event_id": "$evt-xyz-001",
                    "ts": 9999999999999,
                }
            ]

        def parse_completion(self, messages, request_keyword, since_ts=None, task_id=None, exclude_senders=None):
            return f"TASK_COMPLETED id={self.task_id} IT 协作部分已完成"

        def close(self):
            pass

    fake = FakeOnce()
    monkeypatch.setattr("app.services.group_chat.AgentTeamsGateway", lambda: fake)

    run = _try_agentteams_task(
        db_session,
        conversation=conv,
        leader=leader,
        actor_no="E10281",
        request="请 IT 助理执行任务",
        trigger_seq=None,
    )
    assert run is not None
    first_count = db_session.query(models.ConversationMessage).count()
    assert first_count >= 2  # 用户消息 + 一条 IT 反馈

    # 第二次轮询同一事件（模拟重启后的重复拉取）：不应新增消息
    fake.calls = 0
    _try_agentteams_task(
        db_session,
        conversation=conv,
        leader=leader,
        actor_no="E10281",
        request="请 IT 助理执行任务2",
        trigger_seq=None,
    )
    second_count = db_session.query(models.ConversationMessage).count()
    assert second_count == first_count + 1  # 仅新增"任务2"的用户消息
