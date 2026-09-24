"""Durable execution records and SSE replay for workplace runs."""

import pytest
from sqlalchemy import select

from app import models
from app.routers import workplace
from app.services import execution_events, memory_service


def _direct_conversation(db):
    conv = db.scalar(
        select(models.Conversation).where(
            models.Conversation.owner_human_no == "E10281",
            models.Conversation.kind == "direct",
        )
    )
    if conv is None:
        conv = models.Conversation(
            id="CONV-TEST-DIRECT", kind="direct", title="", owner_human_no="E10281",
            participants=[{"employee_no": "DT-E10281", "role": "member"}],
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)
    return conv


def test_run_endpoint_creates_one_active_execution(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    payload = {"actor_no": "E10281", "content": "请查询 IT 服务流程"}

    started = client.post(f"/api/v1/conversations/{conversation.id}/runs", json=payload)
    assert started.status_code == 200
    body = started.json()
    assert body["execution_id"].startswith("EX-")
    assert body["trigger_message_seq"] > 0

    duplicate = client.post(f"/api/v1/conversations/{conversation.id}/runs", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["detail"]["retryable"] is True


def test_sse_streams_safe_progress_and_unicode_deltas(client, db_session, monkeypatch):
    monkeypatch.setenv("DWP_STREAM_CHAR_DELAY_MS", "0")
    conversation = _direct_conversation(db_session)
    execution = execution_events.create_execution(
        db_session,
        conversation_id=conversation.id,
        trigger_message_seq=77,
        primary_employee_id="AI-GENERAL",
    )
    execution_events.emit(
        db_session, execution.id,
        event_type="knowledge_completed", stage="knowledge_search", status="running",
        actor_employee_id="AI-GENERAL", knowledge_base_id="KB-IT-SERVICE",
        hit_count=2, title="IT 服务知识库检索完成", detail="共找到 2 条可用资料",
    )
    execution_events.emit_answer_chunks(
        db_session, execution.id, actor_employee_id="AI-GENERAL", answer="甲乙",
    )
    execution_events.complete(
        db_session, execution.id, actor_employee_id="AI-GENERAL",
        message_id=123, trace_id=execution.trace_id, tool_cards=[],
    )

    response = client.get(
        f"/api/v1/conversations/{conversation.id}/runs/{execution.id}/events"
    )
    assert response.status_code == 200
    text = response.text
    assert "event: progress" in text
    assert '"knowledge_base_id": "KB-IT-SERVICE"' in text
    assert '"delta": "甲"' in text
    assert '"delta": "乙"' in text
    assert "event: answer_done" in text
    for forbidden in ("Authorization", "TASK_ENVELOPE", "内部响应", "请查询"):
        assert forbidden not in text

    latest = client.get(f"/api/v1/conversations/{conversation.id}/runs/latest")
    assert latest.status_code == 200
    assert latest.json()["execution"]["status"] == "completed"
    assert any(event["title"] == "IT 服务知识库检索完成" for event in latest.json()["events"])
    history = client.get(f"/api/v1/conversations/{conversation.id}/runs/history")
    assert history.status_code == 200
    assert [item["execution"]["id"] for item in history.json()] == [execution.id]


def test_sse_resumes_inside_persisted_answer_chunk(client, db_session, monkeypatch):
    monkeypatch.setenv("DWP_STREAM_CHAR_DELAY_MS", "0")
    conversation = _direct_conversation(db_session)
    execution = execution_events.create_execution(
        db_session, conversation_id=conversation.id,
        trigger_message_seq=88, primary_employee_id="AI-GENERAL",
    )
    execution_events.emit_answer_chunks(
        db_session, execution.id, actor_employee_id="AI-GENERAL", answer="甲乙",
    )
    chunk = db_session.scalar(
        select(models.AgentExecutionEvent).where(
            models.AgentExecutionEvent.execution_id == execution.id,
            models.AgentExecutionEvent.event_type == "answer_chunk",
        )
    )
    execution_events.complete(
        db_session, execution.id, actor_employee_id="AI-GENERAL",
        message_id=124, trace_id=execution.trace_id, tool_cards=[],
    )

    response = client.get(
        f"/api/v1/conversations/{conversation.id}/runs/{execution.id}/events",
        headers={"Last-Event-ID": f"{chunk.event_seq}.0"},
    )
    assert '"delta": "甲"' not in response.text
    assert '"delta": "乙"' in response.text


def test_clear_removes_active_execution(client, db_session):
    conversation = _direct_conversation(db_session)
    execution = execution_events.create_execution(
        db_session, conversation_id=conversation.id,
        trigger_message_seq=99, primary_employee_id="AI-GENERAL",
    )
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/clear?actor_no=E10281"
    )
    assert response.status_code == 200
    db_session.expire_all()
    # 物理清除：执行行与进度事件都必须被删除。
    assert db_session.get(models.AgentExecution, execution.id) is None
    assert db_session.scalar(
        select(models.AgentExecutionEvent).where(
            models.AgentExecutionEvent.execution_id == execution.id,
        )
    ) is None


def test_cleared_generation_hides_late_answer(client, db_session):
    conversation = _direct_conversation(db_session)
    old_generation = conversation.generation
    response = client.post(f"/api/v1/conversations/{conversation.id}/clear?actor_no=E10281")
    assert response.status_code == 200
    db_session.expire_all()
    conversation = db_session.get(models.Conversation, conversation.id)
    assert conversation.generation == old_generation + 1
    db_session.add(models.ConversationMessage(
        conversation_id=conversation.id, participant_no="AI-GENERAL",
        participant_name="AI员工平台", role="assistant", content="迟到答案",
        seq=1, generation=old_generation,
    ))
    db_session.commit()
    visible = client.get(f"/api/v1/conversations/{conversation.id}")
    assert visible.status_code == 200
    assert visible.json()["messages"] == []


def test_retry_uses_original_prompt_and_records_lineage(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    started = client.post(
        f"/api/v1/conversations/{conversation.id}/runs",
        json={"actor_no": "E10281", "content": "原始问题"},
    ).json()
    execution = db_session.get(models.AgentExecution, started["execution_id"])
    execution_events.fail(db_session, execution.id, RuntimeError("test"))

    retried = client.post(f"/api/v1/conversations/{conversation.id}/runs/{execution.id}/retry")
    assert retried.status_code == 200
    body = retried.json()
    replacement = db_session.get(models.AgentExecution, body["execution_id"])
    assert replacement.retry_of_execution_id == execution.id
    messages = client.get(f"/api/v1/conversations/{conversation.id}").json()["messages"]
    assert messages[-1]["content"] == "原始问题"


def test_legacy_messages_endpoint_uses_run_pipeline(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages",
        json={"actor_no": "E10281", "content": "兼容问题"},
    )
    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    execution_id = response.headers["X-DWP-Execution-ID"]
    assert db_session.get(models.AgentExecution, execution_id) is not None


def test_cancel_run_marks_cancelled_and_frees_conversation(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    started = client.post(
        f"/api/v1/conversations/{conversation.id}/runs",
        json={"actor_no": "E10281", "content": "请查询 IT 服务流程"},
    ).json()

    cancelled = client.post(
        f"/api/v1/conversations/{conversation.id}/runs/{started['execution_id']}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    # 重复点击幂等：不报错、语义不变
    repeated = client.post(
        f"/api/v1/conversations/{conversation.id}/runs/{started['execution_id']}/cancel"
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "cancelled"

    db_session.expire_all()
    assert db_session.get(models.AgentExecution, started["execution_id"]).status == "cancelled"
    # 活跃执行已释放：同会话可以立刻发下一条（否则会撞唯一索引 409）
    assert client.post(
        f"/api/v1/conversations/{conversation.id}/runs",
        json={"actor_no": "E10281", "content": "第二个问题"},
    ).status_code == 200


def test_cancel_unknown_execution_returns_404(client, db_session):
    conversation = _direct_conversation(db_session)
    response = client.post(f"/api/v1/conversations/{conversation.id}/runs/EX-NOPE/cancel")
    assert response.status_code == 404


def test_cancelled_execution_stream_emits_cancelled_and_drops_pending_text(client, db_session, monkeypatch):
    monkeypatch.setenv("DWP_STREAM_CHAR_DELAY_MS", "0")
    conversation = _direct_conversation(db_session)
    execution = execution_events.create_execution(
        db_session, conversation_id=conversation.id,
        trigger_message_seq=200, primary_employee_id="AI-GENERAL",
    )
    execution_events.emit_answer_chunks(
        db_session, execution.id, actor_employee_id="AI-GENERAL", answer="甲乙",
    )
    execution_events.cancel_execution(db_session, execution.id)

    response = client.get(
        f"/api/v1/conversations/{conversation.id}/runs/{execution.id}/events"
    )
    assert response.status_code == 200
    # 前端必须收到终态事件，否则刷新/重连时会一直停在 running
    assert "event: cancelled" in response.text
    # 已停止：剩余流式文字不再下发
    assert '"delta": "乙"' not in response.text


def test_terminal_execution_is_not_reopened_by_late_events(client, db_session):
    conversation = _direct_conversation(db_session)
    execution = execution_events.create_execution(
        db_session, conversation_id=conversation.id,
        trigger_message_seq=201, primary_employee_id="AI-GENERAL",
    )
    execution_events.complete(
        db_session, execution.id, actor_employee_id="AI-GENERAL",
        message_id=900, trace_id=execution.trace_id, tool_cards=[],
    )
    # 完成瞬间仍在飞的工具回调：事件照常落轨迹，但不得把终态翻回 running
    execution_events.emit(
        db_session, execution.id, event_type="knowledge_completed", stage="knowledge_search",
        status="running", actor_employee_id="AI-GENERAL", title="迟到的检索完成",
    )
    db_session.expire_all()
    assert db_session.get(models.AgentExecution, execution.id).status == "completed"
    latest = client.get(f"/api/v1/conversations/{conversation.id}/runs/latest")
    assert latest.json()["execution"]["status"] == "completed"


def test_cancel_survives_late_worker_completion(client, db_session):
    """取消后 worker 迟到的收尾不得把状态改回 completed。"""
    conversation = _direct_conversation(db_session)
    execution = execution_events.create_execution(
        db_session, conversation_id=conversation.id,
        trigger_message_seq=300, primary_employee_id="AI-GENERAL",
    )
    execution_events.cancel_execution(db_session, execution.id)
    execution_events.complete(
        db_session, execution.id, actor_employee_id="AI-GENERAL",
        message_id=901, trace_id=execution.trace_id, tool_cards=[],
    )
    db_session.expire_all()
    assert db_session.get(models.AgentExecution, execution.id).status == "cancelled"


def test_cancelled_execution_cannot_be_revived_by_stale_worker_session(db_session):
    """worker 会话持有取消前的旧 ORM 对象时也不能把取消翻回去（实机踩过的真 bug）。

    取消状态必须以库为准：只读 `db.get()` 的内存状态时，worker 的下一个事件会把
    cancelled 写回 running，收尾时还会把状态写成 completed 并把答复落进会话。
    """
    from app.database import SessionLocal

    conversation = _direct_conversation(db_session)
    execution = execution_events.create_execution(
        db_session, conversation_id=conversation.id,
        trigger_message_seq=301, primary_employee_id="AI-GENERAL",
    )
    worker = SessionLocal()
    try:
        stale = worker.get(models.AgentExecution, execution.id)
        assert stale.status in ("queued", "running")
        execution_events.cancel_execution(db_session, execution.id)
        # 旧对象还停留在取消前的状态
        assert stale.status != "cancelled"
        with pytest.raises(RuntimeError):
            execution_events.emit(
                worker, execution.id, event_type="progress", stage="knowledge_search",
                status="running", actor_employee_id="AI-GENERAL", title="迟到的检索进度",
            )
        # 收尾同样必须被拦下（不再抛异常，静默跳过）
        execution_events.complete(
            worker, execution.id, actor_employee_id="AI-GENERAL",
            message_id=902, trace_id=execution.trace_id, tool_cards=[],
        )
    finally:
        worker.close()
    db_session.expire_all()
    assert db_session.get(models.AgentExecution, execution.id).status == "cancelled"


def test_restart_recovery_fails_active_execution(db_session):
    conversation = _direct_conversation(db_session)
    execution = execution_events.create_execution(
        db_session, conversation_id=conversation.id, trigger_message_seq=100,
        primary_employee_id="AI-GENERAL", conversation_generation=conversation.generation,
    )
    assert execution_events.fail_orphaned_executions(db_session) == 1
    db_session.expire_all()
    recovered = db_session.get(models.AgentExecution, execution.id)
    assert recovered.status == "failed"
    assert recovered.error_code == "AGENT_PROCESS_RESTARTED"
    assert recovered.retryable is True


# ---- 就地编辑最新一轮提问 + 就地重跑 ----


def _start_run(client, conversation, content: str) -> dict:
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/runs",
        json={"actor_no": "E10281", "content": content},
    )
    assert response.status_code == 200
    return response.json()


def test_rerun_message_edits_latest_turn_in_place(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    started = _start_run(client, conversation, "原始问题")
    seq = started["trigger_message_seq"]
    execution_events.cancel_execution(db_session, started["execution_id"])

    rerun = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/{seq}/runs",
        json={"actor_no": "E10281", "content": "改过的问题"},
    )
    assert rerun.status_code == 200
    body = rerun.json()
    # 就地重跑：复用同一个 seq，不追加新的用户消息
    assert body["trigger_message_seq"] == seq
    messages = client.get(f"/api/v1/conversations/{conversation.id}").json()["messages"]
    assert [message["seq"] for message in messages] == [seq]
    assert messages[0]["content"] == "改过的问题"

    replacement = db_session.get(models.AgentExecution, body["execution_id"])
    assert replacement.retry_of_execution_id == started["execution_id"]
    # 同一轮挂两条执行：前端据此渲染「查看上一次执行」
    history = client.get(f"/api/v1/conversations/{conversation.id}/runs/history").json()
    assert [item["execution"]["trigger_message_seq"] for item in history] == [seq, seq]


def test_rerun_message_without_content_keeps_original_text(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    started = _start_run(client, conversation, "原始问题")
    execution_events.cancel_execution(db_session, started["execution_id"])

    rerun = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/{started['trigger_message_seq']}/runs",
        json={"actor_no": "E10281"},
    )
    assert rerun.status_code == 200
    messages = client.get(f"/api/v1/conversations/{conversation.id}").json()["messages"]
    assert [message["content"] for message in messages] == ["原始问题"]


def test_rerun_message_supersedes_existing_reply(client, db_session, monkeypatch):
    """已有回复的最新一轮照样能改：旧回复软作废（保留可回看），不物理删除。"""
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    started = _start_run(client, conversation, "原始问题")
    seq = started["trigger_message_seq"]
    execution_events.cancel_execution(db_session, started["execution_id"])
    db_session.add(models.ConversationMessage(
        conversation_id=conversation.id, participant_no="DT-E10281", participant_name="我的分身",
        role="assistant", content="已回复", seq=seq + 1, generation=conversation.generation,
    ))
    db_session.commit()

    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/{seq}/runs",
        json={"actor_no": "E10281", "content": "改过的问题"},
    )
    assert response.status_code == 200
    db_session.expire_all()
    messages = client.get(f"/api/v1/conversations/{conversation.id}").json()["messages"]
    # 提问就地改写、旧回复留在原位，但被标记作废
    assert [message["seq"] for message in messages] == [seq, seq + 1]
    assert messages[0]["content"] == "改过的问题"
    assert messages[1]["content"] == "已回复"
    assert messages[1]["superseded_at"] is not None
    assert messages[0]["superseded_at"] is None


def test_superseded_reply_leaves_model_context(client, db_session, monkeypatch):
    """作废的旧答案不再进入模型上下文，否则模型会同时看到两条矛盾答案。"""
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    started = _start_run(client, conversation, "原始问题")
    seq = started["trigger_message_seq"]
    execution_events.cancel_execution(db_session, started["execution_id"])
    db_session.add(models.ConversationMessage(
        conversation_id=conversation.id, participant_no="DT-E10281", participant_name="我的分身",
        role="assistant", content="旧答案", seq=seq + 1, generation=conversation.generation,
    ))
    db_session.commit()

    rerun = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/{seq}/runs",
        json={"actor_no": "E10281", "content": "改过的问题"},
    )
    assert rerun.status_code == 200
    # 重跑完成后落新答案
    db_session.add(models.ConversationMessage(
        conversation_id=conversation.id, participant_no="DT-E10281", participant_name="我的分身",
        role="assistant", content="新答案", seq=seq + 2, generation=conversation.generation,
    ))
    db_session.commit()
    db_session.expire_all()

    history = memory_service.conversation_history(db_session, conversation.id, conversation.generation)
    assert [item["content"] for item in history] == ["改过的问题", "新答案"]
    # 界面仍能拿到旧答案（折叠展示）
    assert [message["seq"] for message in client.get(
        f"/api/v1/conversations/{conversation.id}"
    ).json()["messages"]] == [seq, seq + 1, seq + 2]


def test_edit_message_rejects_turn_with_live_reply(client, db_session, monkeypatch):
    """只改文字不重跑：已有有效回复时仍拒绝，答案不能挂在没问过的问题下面。"""
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    started = _start_run(client, conversation, "原始问题")
    seq = started["trigger_message_seq"]
    execution_events.cancel_execution(db_session, started["execution_id"])
    db_session.add(models.ConversationMessage(
        conversation_id=conversation.id, participant_no="DT-E10281", participant_name="我的分身",
        role="assistant", content="已回复", seq=seq + 1, generation=conversation.generation,
    ))
    db_session.commit()

    response = client.patch(
        f"/api/v1/conversations/{conversation.id}/messages/{seq}",
        json={"actor_no": "E10281", "content": "只改文字"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["message"] == "该轮已收到回复，请用「保存并重跑」"


def test_rerun_message_rejects_non_latest_turn(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    first = _start_run(client, conversation, "第一个问题")
    execution_events.cancel_execution(db_session, first["execution_id"])
    second = _start_run(client, conversation, "第二个问题")
    execution_events.cancel_execution(db_session, second["execution_id"])

    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/{first['trigger_message_seq']}/runs",
        json={"actor_no": "E10281", "content": "改第一个问题"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["message"] == "只能编辑最新一轮提问"


def test_rerun_message_keeps_bound_attachments(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    started = _start_run(client, conversation, "带附件的问题")
    seq = started["trigger_message_seq"]
    execution_events.cancel_execution(db_session, started["execution_id"])
    db_session.add(models.ConversationAttachment(
        id="ATT-TEST-1", conversation_id=conversation.id, owner_human_no="E10281",
        generation=conversation.generation, message_seq=seq, filename="notes.txt",
        file_type="txt", mime_type="text/plain", size=5, sha256="x",
        storage_path="/tmp/notes.txt", status="ready", chunk_count=1,
    ))
    message = db_session.scalar(select(models.ConversationMessage).where(
        models.ConversationMessage.conversation_id == conversation.id,
        models.ConversationMessage.seq == seq,
    ))
    message.attachment_ids = ["ATT-TEST-1"]
    db_session.commit()

    rerun = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/{seq}/runs",
        json={"actor_no": "E10281", "content": "带附件的问题（改）"},
    )
    # 附件在首次发送时已绑定，就地重跑必须跳过重新绑定（否则 bind_to_message 会 409）
    assert rerun.status_code == 200
    db_session.expire_all()
    messages = client.get(f"/api/v1/conversations/{conversation.id}").json()["messages"]
    assert messages[0]["attachment_ids"] == ["ATT-TEST-1"]
    assert db_session.get(models.ConversationAttachment, "ATT-TEST-1").message_seq == seq
    events = db_session.scalars(select(models.AgentExecutionEvent).where(
        models.AgentExecutionEvent.execution_id == rerun.json()["execution_id"],
    )).all()
    assert any(event.event_type == "attachment_received" for event in events)


def test_edit_and_rerun_blocked_while_execution_active(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    started = _start_run(client, conversation, "正在执行的问题")
    seq = started["trigger_message_seq"]

    edited = client.patch(
        f"/api/v1/conversations/{conversation.id}/messages/{seq}",
        json={"actor_no": "E10281", "content": "执行中改文字"},
    )
    assert edited.status_code == 409
    rerun = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/{seq}/runs",
        json={"actor_no": "E10281", "content": "执行中重跑"},
    )
    assert rerun.status_code == 409


def test_edit_message_updates_content_and_audits(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    started = _start_run(client, conversation, "原始问题")
    seq = started["trigger_message_seq"]
    execution_events.cancel_execution(db_session, started["execution_id"])

    response = client.patch(
        f"/api/v1/conversations/{conversation.id}/messages/{seq}",
        json={"actor_no": "E10281", "content": "  改过的问题  "},
    )
    assert response.status_code == 200
    assert response.json()["content"] == "改过的问题"
    # 只改文字：不产生新的执行
    assert len(db_session.scalars(select(models.AgentExecution).where(
        models.AgentExecution.conversation_id == conversation.id,
    )).all()) == 1
    assert db_session.scalar(select(models.AuditEvent).where(
        models.AuditEvent.action == "conversation_message_edit",
    )) is not None


def test_edit_message_rejects_empty_content_and_foreign_actor(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conversation = _direct_conversation(db_session)
    started = _start_run(client, conversation, "原始问题")
    seq = started["trigger_message_seq"]
    execution_events.cancel_execution(db_session, started["execution_id"])

    empty = client.patch(
        f"/api/v1/conversations/{conversation.id}/messages/{seq}",
        json={"actor_no": "E10281", "content": "   "},
    )
    assert empty.status_code == 400
    foreign = client.patch(
        f"/api/v1/conversations/{conversation.id}/messages/{seq}",
        json={"actor_no": "E20999", "content": "越权修改"},
    )
    assert foreign.status_code == 403
    unknown = client.patch(
        f"/api/v1/conversations/{conversation.id}/messages/9999",
        json={"actor_no": "E10281", "content": "不存在"},
    )
    assert unknown.status_code == 404
