from io import BytesIO

from openpyxl import Workbook

from app import models
from app.services import attachments, execution_events
from app.services.group_chat import append_user_message
from app.services.harness_token import issue_token


def _login_and_conversation(client):
    assert client.post("/api/v1/auth/key-login", json={"access_key": "test-key-e10281"}).status_code == 200
    response = client.post("/api/v1/conversations", json={
        "actor_no": "E10281", "kind": "direct", "participant_employee_nos": ["AI-GENERAL"],
    })
    assert response.status_code == 201
    return response.json()["id"]


def test_markdown_upload_is_parsed_and_listed(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/attachments",
        files={"file": ("制度说明.md", "# 请假制度\n\n年假需要提前提交申请。".encode(), "text/markdown")},
    )
    assert response.status_code == 200
    attachment_id = response.json()["attachment_id"]
    rows = client.get(f"/api/v1/conversations/{conversation_id}/attachments").json()
    row = next(item for item in rows if item["attachment_id"] == attachment_id)
    assert row["status"] == "ready"
    assert row["chunk_count"] == 1


def test_xlsx_parser_preserves_sheet_and_range_access(client, db_session, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "预算"
    sheet.append(["部门", "金额"])
    sheet.append(["研发", 120])
    payload = BytesIO()
    workbook.save(payload)
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/attachments",
        files={"file": ("预算.xlsx", payload.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    attachment_id = response.json()["attachment_id"]
    conversation = db_session.get(models.Conversation, conversation_id)
    message = append_user_message(db_session, conversation=conversation, actor_no="E10281", content="分析预算")
    attachments.bind_to_message(
        db_session, conversation=conversation, owner_human_no="E10281",
        message_seq=message.seq, attachment_ids=[attachment_id],
    )
    message.attachment_ids = [attachment_id]
    db_session.commit()
    execution = execution_events.create_execution(
        db_session, conversation_id=conversation_id, trigger_message_seq=message.seq,
        primary_employee_id="AI-GENERAL", conversation_generation=conversation.generation,
    )
    result = attachments.read_spreadsheet_range(
        db_session, trace_id=execution.trace_id, requester="E10281", employee_id="AI-GENERAL",
        attachment_id=attachment_id, sheet="预算", cell_range="A1:B2",
    )
    assert result["values"] == [["部门", "金额"], ["研发", 120]]
    token = issue_token(
        employee_id="AI-GENERAL", requester_human_no="E10281",
        trace_id=execution.trace_id, depth=0,
    )
    listed = client.post(
        "/internal/agent-tools/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ).json()["result"]["tools"]
    names = {tool["name"] for tool in listed}
    assert {"list_attachments", "search_attachment", "read_attachment", "read_spreadsheet_range"} <= names


def test_legacy_office_requires_conversion_plugin(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/attachments",
        files={"file": ("旧制度.doc", b"legacy-office", "application/msword")},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "conversion_required"
    assert response.json()["error_code"] == "CONVERSION_REQUIRED"


def test_clear_conversation_invalidates_attachments(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    uploaded = client.post(
        f"/api/v1/conversations/{conversation_id}/attachments",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    ).json()
    response = client.delete(f"/api/v1/conversations/{conversation_id}?actor_no=E10281")
    assert response.status_code == 200
    detail = client.get(
        f"/api/v1/conversations/{conversation_id}/attachments/{uploaded['attachment_id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "deleted"
    assert client.get(f"/api/v1/conversations/{conversation_id}/attachments").json() == []


def test_rejects_spoofed_pdf_and_oversized_file(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    spoofed = client.post(
        f"/api/v1/conversations/{conversation_id}/attachments",
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
    )
    assert spoofed.status_code == 422
    oversized = client.post(
        f"/api/v1/conversations/{conversation_id}/attachments",
        files={"file": ("huge.txt", b"x" * (attachments.MAX_FILE_SIZE + 1), "text/plain")},
    )
    assert oversized.status_code == 413
