"""Durable execution records and SSE replay for workplace runs."""

from sqlalchemy import select

from app import models
from app.routers import workplace
from app.services import execution_events


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


def test_cleared_generation_hides_late_answer(client, db_session):
    conversation = _direct_conversation(db_session)
    old_generation = conversation.generation
    response = client.delete(f"/api/v1/conversations/{conversation.id}?actor_no=E10281")
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
