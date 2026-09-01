"""Durable execution records and SSE replay for workplace runs."""

from sqlalchemy import select

from app import models
from app.routers import workplace
from app.services import execution_events


def _direct_conversation(db):
    return db.scalar(
        select(models.Conversation).where(
            models.Conversation.owner_human_no == "E10281",
            models.Conversation.kind == "direct",
        )
    )


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


def test_clear_marks_active_execution_cancelled(client, db_session):
    conversation = _direct_conversation(db_session)
    execution = execution_events.create_execution(
        db_session, conversation_id=conversation.id,
        trigger_message_seq=99, primary_employee_id="AI-GENERAL",
    )
    response = client.delete(
        f"/api/v1/conversations/{conversation.id}?actor_no=E10281"
    )
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(models.AgentExecution, execution.id).status == "cancelled"
