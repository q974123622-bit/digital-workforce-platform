"""MCP 工具缺参/缺鉴权的识别、待补全记录与「提交后自动续跑」离线单测。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app import models
from app.routers import workplace
from app.services import execution_events, mcp_credentials, tool_input

USER_HEADER = "X-MCP-JWT"
APP_HEADER = "X-MCP-API-KEY"
PLUGIN_ID = "xyzq-ehome-mcp"


class _FakeAdapter:
    """最小适配器替身：只需要 ``plugin_id`` 与 ``required_inputs``。"""

    def __init__(self, plugin_id: str = PLUGIN_ID, entries: list[dict] | None = None, label: str = "兴证e家"):
        self.plugin_id = plugin_id
        self.source_label = label
        self._entries = entries

    def required_inputs(self):
        if self._entries is None:
            return []
        return self._entries


def _entries(*, exclude_tools: list[str] | None = None) -> list[dict]:
    return [
        {"key": APP_HEADER, "label": "应用密钥", "kind": "secret", "source": "app_secret", "required": True},
        {
            "key": USER_HEADER, "label": "用户令牌", "kind": "secret", "source": "user_secret",
            "required": True, "hint": "从 App 复制", "exclude_tools": exclude_tools or [],
        },
    ]


@pytest.fixture(autouse=True)
def _credential_key(monkeypatch):
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_KEY", "unit-test-key-material")
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_KEY_FILE", "/nonexistent/mcp_credential_key")


def _direct_conversation(db) -> models.Conversation:
    conv = db.scalar(select(models.Conversation).where(
        models.Conversation.owner_human_no == "E10281",
        models.Conversation.kind == "direct",
    ))
    if conv is None:
        conv = models.Conversation(
            id="CONV-INPUT-DIRECT", kind="direct", title="", owner_human_no="E10281",
            participants=[{"employee_no": "DT-E10281", "role": "member"}],
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)
    return conv


def _finished_execution(db, conv, *, seq: int = 11) -> models.AgentExecution:
    execution = execution_events.create_execution(
        db, conversation_id=conv.id, trigger_message_seq=seq, primary_employee_id="DT-E10281",
    )
    execution_events.complete(
        db, execution.id, actor_employee_id="DT-E10281",
        message_id=1, trace_id=execution.trace_id, tool_cards=[],
    )
    return execution


def _pending_request(db, conv, execution, *, reason: str = tool_input.REASON_MISSING) -> models.ToolInputRequest:
    return tool_input.create_request(
        db, conversation_id=conv.id, execution_id=execution.id, trace_id=execution.trace_id,
        plugin_id=PLUGIN_ID, tool_name="getEmpCardBalance",
        fields=[{
            "key": USER_HEADER, "label": "用户令牌", "kind": "secret", "required": True,
            "hint": "从 App 复制", "source": "user_secret", "masked": "", "reason": reason,
        }],
        reason=reason,
    )


# -- 事前预检 -----------------------------------------------------------------


def test_detect_missing_input_without_declaration_is_empty(db_session):
    assert tool_input.detect_missing_input(
        db_session, _FakeAdapter(entries=None), "E10281", "getPhone", {},
    ) == []


def test_detect_missing_input_reports_missing_and_expired(db_session):
    adapter = _FakeAdapter(entries=_entries())
    fields = tool_input.detect_missing_input(db_session, adapter, "E10281", "getEmpCardBalance", {})
    assert [field["key"] for field in fields] == [USER_HEADER]
    assert fields[0]["reason"] == tool_input.REASON_MISSING

    mcp_credentials.save_user_credential(
        db_session, plugin_id=PLUGIN_ID, human_no="E10281", header_name=USER_HEADER,
        secret="fresh-token", updated_by="E10281",
    )
    db_session.commit()
    assert tool_input.detect_missing_input(db_session, adapter, "E10281", "getEmpCardBalance", {}) == []

    mcp_credentials.save_user_credential(
        db_session, plugin_id=PLUGIN_ID, human_no="E10281", header_name=USER_HEADER,
        secret="stale-token", updated_by="E10281", expires_at=datetime.now() - timedelta(minutes=5),
    )
    db_session.commit()
    expired = tool_input.detect_missing_input(db_session, adapter, "E10281", "getEmpCardBalance", {})
    assert expired[0]["reason"] == tool_input.REASON_EXPIRED
    assert expired[0]["masked"].endswith("oken")


def test_detect_missing_input_skips_public_tools(db_session):
    adapter = _FakeAdapter(entries=_entries(exclude_tools=["getPhone", "getEmail"]))
    assert tool_input.detect_missing_input(db_session, adapter, "E10281", "getPhone", {}) == []
    assert tool_input.detect_missing_input(db_session, adapter, "E10281", "getEmpCardBalance", {}) != []


def test_build_auth_headers_merges_app_and_user_secret(db_session):
    adapter = _FakeAdapter(entries=_entries())
    mcp_credentials.save_credential(
        db_session, plugin_id=PLUGIN_ID, header_name=APP_HEADER, secret="app-secret",
        updated_by="seed",
    )
    mcp_credentials.save_user_credential(
        db_session, plugin_id=PLUGIN_ID, human_no="E10281", header_name=USER_HEADER,
        secret="user-token", updated_by="E10281",
    )
    db_session.commit()
    assert tool_input.build_auth_headers(db_session, adapter, "E10281") == {
        APP_HEADER: "app-secret", USER_HEADER: "user-token",
    }
    # 没有用户级凭据时不带上该头（由预检负责拦下）
    assert tool_input.build_auth_headers(db_session, adapter, "E20999") == {APP_HEADER: "app-secret"}
    # 未声明任何必填输入 → 走静态客户端路径
    assert tool_input.build_auth_headers(db_session, _FakeAdapter(entries=None), "E10281") is None


# -- 事后识别 -----------------------------------------------------------------


def test_detect_missing_from_text_matches_declared_header():
    adapter = _FakeAdapter(entries=_entries())
    fields = tool_input.detect_missing_from_text("Missing required header 'X-MCP-JWT'", adapter)
    assert [field["key"] for field in fields] == [USER_HEADER]
    assert fields[0]["kind"] == "secret"
    assert fields[0]["reason"] == tool_input.REASON_TOOL_ERROR


def test_detect_missing_from_text_maps_undeclared_param_to_text():
    adapter = _FakeAdapter(entries=_entries())
    fields = tool_input.detect_missing_from_text("缺少请求参数: keyword", adapter)
    assert [field["key"] for field in fields] == ["keyword"]
    assert fields[0]["kind"] == "text"


def test_detect_missing_from_text_maps_java_null_argument():
    """兴证e家真实返回：schema 未标注的必填参数缺失时抛 Java 空指针文案。"""
    adapter = _FakeAdapter(entries=_entries())
    text = 'Cannot invoke "String.split(String)" because "keyword" is null'
    fields = tool_input.detect_missing_from_text(text, adapter)
    assert [field["key"] for field in fields] == ["keyword"]
    assert fields[0]["kind"] == "text"


def test_detect_missing_from_text_handles_keyword_required_phrasing():
    adapter = _FakeAdapter(entries=_entries())
    assert [f["key"] for f in tool_input.detect_missing_from_text("keyword 是必填项", adapter)] == ["keyword"]
    # 无信号时不要臆造字段
    assert tool_input.detect_missing_from_text("服务暂时不可用", adapter) == []
    assert tool_input.detect_missing_from_text("", adapter) == []


# -- 记录与掩码 ---------------------------------------------------------------


def test_create_request_reuses_pending_row_for_same_fields(db_session):
    conv = _direct_conversation(db_session)
    execution = _finished_execution(db_session, conv)
    first = _pending_request(db_session, conv, execution)
    second = _pending_request(db_session, conv, execution)
    db_session.commit()
    assert first.id == second.id
    assert len(db_session.scalars(select(models.ToolInputRequest).where(
        models.ToolInputRequest.execution_id == execution.id,
    )).all()) == 1


def test_field_needs_value_and_masked_values():
    assert tool_input.field_needs_value({"required": True, "masked": "", "reason": "missing"}) is True
    assert tool_input.field_needs_value({"required": True, "masked": "****abcd", "reason": "missing"}) is False
    assert tool_input.field_needs_value({"required": True, "masked": "****abcd", "reason": "expired"}) is True
    assert tool_input.field_needs_value({"required": False, "masked": ""}) is False

    fields = [
        {"key": USER_HEADER, "kind": "secret"},
        {"key": "keyword", "kind": "text"},
    ]
    masked = tool_input.masked_values(fields, {USER_HEADER: "super-secret-token", "keyword": "报销"})
    assert masked[USER_HEADER] == mcp_credentials.mask("super-secret-token")
    assert "super-secret-token" not in json.dumps(masked, ensure_ascii=False)
    assert masked["keyword"] == "报销"


def test_build_resume_content_never_embeds_secret():
    fields = [
        {"key": USER_HEADER, "label": "用户令牌", "kind": "secret"},
        {"key": "keyword", "label": "关键字", "kind": "text"},
    ]
    content = tool_input.build_resume_content(
        "查我的餐卡余额", fields, {USER_HEADER: "super-secret-token", "keyword": "报销"},
    )
    assert content.startswith("查我的餐卡余额")
    assert "super-secret-token" not in content
    assert "已提供并安全保存" in content
    assert "报销" in content


# -- 提交接口 -----------------------------------------------------------------


def test_submit_input_saves_credential_and_resumes(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conv = _direct_conversation(db_session)
    execution = _finished_execution(db_session, conv)
    request = _pending_request(db_session, conv, execution)
    db_session.commit()

    token = "jwt-value-from-employee"
    response = client.post(
        f"/api/v1/conversations/{conv.id}/runs/{execution.id}/input",
        json={"actor_no": "E10281", "request_id": request.id, "values": {USER_HEADER: token}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_id"] != execution.id

    # 凭据加密存到本人名下，明文不落库
    assert mcp_credentials.load_user_credential(
        db_session, plugin_id=PLUGIN_ID, human_no="E10281",
    ) == token
    row = db_session.get(models.McpUserCredential, (PLUGIN_ID, "E10281"))
    assert row is not None and token not in row.secret_ciphertext

    # 待补全记录标记已提交，且 submitted_values 只有掩码
    db_session.refresh(request)
    assert request.status == tool_input.STATUS_SUBMITTED
    assert request.resolved_by == "E10281"
    assert request.submitted_values[USER_HEADER] == mcp_credentials.mask(token)
    assert token not in json.dumps(request.submitted_values, ensure_ascii=False)

    # 续跑消息带上原提问与「已提供」说明，但绝不带明文凭据
    new_execution = db_session.get(models.AgentExecution, body["execution_id"])
    assert new_execution is not None
    message = db_session.scalar(select(models.ConversationMessage).where(
        models.ConversationMessage.conversation_id == conv.id,
        models.ConversationMessage.seq == new_execution.trigger_message_seq,
    ))
    assert message is not None
    assert "补充信息" in message.content
    assert token not in message.content


def test_submit_input_rejects_unknown_or_non_pending(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conv = _direct_conversation(db_session)
    execution = _finished_execution(db_session, conv)
    request = _pending_request(db_session, conv, execution)
    db_session.commit()

    missing = client.post(
        f"/api/v1/conversations/{conv.id}/runs/{execution.id}/input",
        json={"actor_no": "E10281", "request_id": "TI-NOPE", "values": {USER_HEADER: "x"}},
    )
    assert missing.status_code == 404

    request.status = tool_input.STATUS_SUBMITTED
    db_session.commit()
    again = client.post(
        f"/api/v1/conversations/{conv.id}/runs/{execution.id}/input",
        json={"actor_no": "E10281", "request_id": request.id, "values": {USER_HEADER: "x"}},
    )
    assert again.status_code == 409

    other = client.post(
        f"/api/v1/conversations/{conv.id}/runs/{execution.id}/input",
        json={"actor_no": "E20999", "request_id": request.id, "values": {USER_HEADER: "x"}},
    )
    assert other.status_code == 403


def test_submit_input_requires_required_values(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conv = _direct_conversation(db_session)
    execution = _finished_execution(db_session, conv)
    request = _pending_request(db_session, conv, execution)
    db_session.commit()
    response = client.post(
        f"/api/v1/conversations/{conv.id}/runs/{execution.id}/input",
        json={"actor_no": "E10281", "request_id": request.id, "values": {}},
    )
    assert response.status_code == 422
    assert USER_HEADER in response.json()["error"]["message"]


def test_submit_input_conflicts_while_run_is_active(client, db_session, monkeypatch):
    """上一轮仍在执行时透传 409（retryable），但凭据已保存、记录保持 pending 可重试。"""
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conv = _direct_conversation(db_session)
    execution = execution_events.create_execution(
        db_session, conversation_id=conv.id, trigger_message_seq=21, primary_employee_id="DT-E10281",
    )
    request = _pending_request(db_session, conv, execution)
    db_session.commit()

    response = client.post(
        f"/api/v1/conversations/{conv.id}/runs/{execution.id}/input",
        json={"actor_no": "E10281", "request_id": request.id, "values": {USER_HEADER: "tok"}},
    )
    assert response.status_code == 409
    assert response.json()["error"]["detail"]["retryable"] is True
    db_session.refresh(request)
    assert request.status == tool_input.STATUS_PENDING
    assert mcp_credentials.load_user_credential(
        db_session, plugin_id=PLUGIN_ID, human_no="E10281",
    ) == "tok"


def test_ignore_input_persists_cancelled(client, db_session, monkeypatch):
    """忽略必须落库：只在前端本地收起的话，刷新页面气泡会重新冒出来。"""
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conv = _direct_conversation(db_session)
    execution = _finished_execution(db_session, conv)
    request = _pending_request(db_session, conv, execution)
    db_session.commit()

    url = f"/api/v1/conversations/{conv.id}/runs/{execution.id}/input-requests/{request.id}/ignore"
    response = client.post(url, json={"actor_no": "E10281"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == tool_input.STATUS_CANCELLED

    db_session.refresh(request)
    assert request.status == tool_input.STATUS_CANCELLED
    assert request.resolved_by == "E10281"
    assert request.resolved_at is not None
    # 忽略只是不再提示，不产生任何凭据、也不动原记录里的字段
    assert mcp_credentials.load_user_credential(
        db_session, plugin_id=PLUGIN_ID, human_no="E10281",
    ) == ""

    # 会话级列表里它已不是 pending（前端不会再强制弹出）
    rows = client.get(f"/api/v1/conversations/{conv.id}/input-requests").json()
    assert [row["status"] for row in rows] == [tool_input.STATUS_CANCELLED]

    assert client.post(url, json={"actor_no": "E10281"}).status_code == 409
    assert client.post(url, json={"actor_no": "E20999"}).status_code == 403
    assert client.post(
        f"/api/v1/conversations/{conv.id}/runs/{execution.id}/input-requests/TI-NOPE/ignore",
        json={"actor_no": "E10281"},
    ).status_code == 404


def test_input_request_listing_endpoints(client, db_session, monkeypatch):
    monkeypatch.setattr(workplace, "process_conversation_async", lambda *args: None)
    conv = _direct_conversation(db_session)
    execution = _finished_execution(db_session, conv)
    request = _pending_request(db_session, conv, execution)
    db_session.commit()

    by_conversation = client.get(f"/api/v1/conversations/{conv.id}/input-requests")
    assert by_conversation.status_code == 200
    rows = by_conversation.json()
    assert [row["id"] for row in rows] == [request.id]
    assert rows[0]["fields"][0]["key"] == USER_HEADER
    # 只回字段名与掩码，不含任何明文
    assert "super-secret" not in by_conversation.text

    by_run = client.get(f"/api/v1/conversations/{conv.id}/runs/{execution.id}/input-requests")
    assert by_run.status_code == 200
    assert [row["id"] for row in by_run.json()] == [request.id]

    unknown = client.get(f"/api/v1/conversations/{conv.id}/runs/EX-NOPE/input-requests")
    assert unknown.status_code == 404
