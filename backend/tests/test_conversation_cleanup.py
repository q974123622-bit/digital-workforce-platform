"""会话清空 / 删除卡片的物理清除与可见性语义。"""

from sqlalchemy import select

from app import models
from app.services import execution_events
from app.services.group_chat import append_user_message


def _login(client):
    assert client.post("/api/v1/auth/key-login", json={"access_key": "test-key-e10281"}).status_code == 200


def _direct_conversation(client) -> str:
    response = client.post("/api/v1/conversations", json={
        "actor_no": "E10281", "kind": "direct", "participant_employee_nos": ["AI-GENERAL"],
    })
    assert response.status_code == 201
    return response.json()["id"]


def _seed_content(db, client, conversation_id: str, *, attachment: bool = False) -> str | None:
    """写入消息 / 协作任务 / 执行轨迹 / 记忆摘要，返回附件 id。"""
    conversation = db.get(models.Conversation, conversation_id)
    message = append_user_message(
        db, conversation=conversation, actor_no="E10281", content="请查询 IT 服务流程",
    )
    db.add(models.TaskRun(
        id="TR-TEST-1", team_id="TEAM-TEST", conversation_id=conversation_id,
        trigger_message_seq=message.seq, request="测试协作任务",
    ))
    execution = execution_events.create_execution(
        db, conversation_id=conversation_id, trigger_message_seq=message.seq,
        primary_employee_id="AI-GENERAL", conversation_generation=conversation.generation,
    )
    db.add(models.ConversationMemoryState(
        conversation_id=conversation_id, compacted_through_seq=message.seq,
        rolling_summary="历史摘要",
    ))
    db.commit()
    assert execution.id
    attachment_id = None
    if attachment:
        uploaded = client.post(
            f"/api/v1/conversations/{conversation_id}/attachments",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert uploaded.status_code == 200
        attachment_id = uploaded.json()["attachment_id"]
    return attachment_id


def _assert_content_gone(db, conversation_id: str) -> None:
    db.expire_all()
    assert db.scalars(select(models.ConversationMessage).where(
        models.ConversationMessage.conversation_id == conversation_id)).all() == []
    assert db.scalars(select(models.TaskRun).where(
        models.TaskRun.conversation_id == conversation_id)).all() == []
    assert db.scalars(select(models.AgentExecution).where(
        models.AgentExecution.conversation_id == conversation_id)).all() == []
    assert db.scalars(select(models.ConversationAttachment).where(
        models.ConversationAttachment.conversation_id == conversation_id)).all() == []
    assert db.get(models.ConversationMemoryState, conversation_id) is None


def test_clear_purges_every_table_and_disk(client, db_session, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    _login(client)
    conversation_id = _direct_conversation(client)
    _seed_content(db_session, client, conversation_id, attachment=True)
    # 清空后执行事件也不得残留。
    execution_ids = list(db_session.scalars(select(models.AgentExecution.id).where(
        models.AgentExecution.conversation_id == conversation_id)).all())
    assert execution_ids

    response = client.post(f"/api/v1/conversations/{conversation_id}/clear?actor_no=E10281")
    assert response.status_code == 200

    _assert_content_gone(db_session, conversation_id)
    assert db_session.scalars(select(models.AgentExecutionEvent).where(
        models.AgentExecutionEvent.execution_id.in_(execution_ids))).all() == []
    assert db_session.scalars(select(models.AttachmentChunk)).all() == []
    assert db_session.scalars(select(models.AttachmentProcessingJob)).all() == []
    assert not (tmp_path / "E10281" / conversation_id).exists()
    # 卡片保留，详情仍可访问且为空。
    detail = client.get(f"/api/v1/conversations/{conversation_id}")
    assert detail.status_code == 200
    assert detail.json()["messages"] == []


def test_delete_hides_card_and_direct_revives_same_conversation(client, db_session, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    _login(client)
    conversation_id = _direct_conversation(client)
    _seed_content(db_session, client, conversation_id)

    response = client.delete(f"/api/v1/conversations/{conversation_id}?actor_no=E10281")
    assert response.status_code == 200

    # 卡片从列表消失，详情与附件接口均返回不存在。
    listed = client.get("/api/v1/conversations?actor_no=E10281").json()
    assert conversation_id not in [item["id"] for item in listed]
    assert client.get(f"/api/v1/conversations/{conversation_id}").status_code == 404
    assert client.get(f"/api/v1/conversations/{conversation_id}/attachments").status_code == 404
    _assert_content_gone(db_session, conversation_id)

    # 重新对同一员工发起私聊：复用原会话（编号不增长），且是干净的空会话。
    revived = client.post("/api/v1/conversations", json={
        "actor_no": "E10281", "kind": "direct", "participant_employee_nos": ["AI-GENERAL"],
    })
    assert revived.status_code == 201
    assert revived.json()["id"] == conversation_id
    assert revived.json()["messages"] == []
    assert conversation_id in [
        item["id"] for item in client.get("/api/v1/conversations?actor_no=E10281").json()
    ]


def test_group_delete_does_not_revive(client, db_session):
    _login(client)
    created = client.post("/api/v1/conversations", json={
        "actor_no": "E10281", "kind": "group", "title": "协作空间",
        "participant_employee_nos": ["AI-GENERAL"],
    })
    assert created.status_code == 201
    first_id = created.json()["id"]
    assert client.delete(f"/api/v1/conversations/{first_id}?actor_no=E10281").status_code == 200

    again = client.post("/api/v1/conversations", json={
        "actor_no": "E10281", "kind": "group", "title": "协作空间",
        "participant_employee_nos": ["AI-GENERAL"],
    })
    assert again.status_code == 201
    assert again.json()["id"] != first_id


def test_deleted_conversation_rejects_new_runs(client, db_session):
    _login(client)
    conversation_id = _direct_conversation(client)
    assert client.delete(f"/api/v1/conversations/{conversation_id}?actor_no=E10281").status_code == 200
    response = client.post(f"/api/v1/conversations/{conversation_id}/runs", json={
        "actor_no": "E10281", "content": "已删除会话不应接受新消息",
    })
    assert response.status_code == 404
