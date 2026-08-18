"""三个 Demo Tool 的接入测试：collaborate_employee / read_document / query_work_records。

覆盖 Tool → Plugin Gateway → Mock Adapter → Fixture 调用链，以及 ChatOrchestrator
的 Tool dispatch 级测试（用 FakeLLM 产生 tool_call）。
"""

from app.services import gateway
from app.services.chat import ChatOrchestrator
from app.services.llm import LLMProvider, LLMResponse, ToolCall


class FakeLLM(LLMProvider):
    """可编程假 LLM：按调用顺序返回预设响应，并记录调用历史。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        return self.script.pop(0)

    def tool_call(self, messages, tools):
        return self.chat(messages, tools)

    def structured_output(self, messages, schema):
        return {}


def _collab(db, target, action, trace_id="T-DEMO-COLLAB"):
    return gateway.invoke_plugin(
        db,
        employee_id="DT-E10281",
        plugin_id="employee-collaboration",
        action="execute",
        params={"target_employee_id": target, "action": action, "request": "demo request"},
        trace_id=trace_id,
    )


# ---- collaborate_employee：Adapter + Gateway 结果 ----


def test_collaborate_success(db_session):
    result = _collab(db_session, "VE-0002", "ask")
    assert result["ok"] is True
    assert result["decision"] == "allow"
    assert result["data"]["status"] == "success"


def test_collaborate_not_found(db_session):
    result = _collab(db_session, "VE-9999", "ask")
    assert result["data"]["status"] == "not_found"


def test_collaborate_unavailable(db_session):
    result = _collab(db_session, "VE-0001", "delegate")
    assert result["data"]["status"] == "unavailable"


def test_collaborate_blocked(db_session):
    result = _collab(db_session, "VE-0002", "handoff")
    assert result["data"]["status"] == "blocked"


# ---- read_document：Adapter 结果 + 目录穿越防护 ----


def _read_document(db, document_name, trace_id="T-DEMO-DOC"):
    return gateway.invoke_plugin(
        db,
        employee_id="DT-E10281",
        plugin_id="document-read",
        action="read",
        params={"document_name": document_name},
        trace_id=trace_id,
    )


def test_read_document_normal(db_session):
    result = _read_document(db_session, "normal-document.md")
    assert result["ok"] is True
    assert result["data"]["status"] == "success"
    assert "虚构" in result["data"]["content"]


def test_read_document_empty(db_session):
    result = _read_document(db_session, "empty-document.md")
    assert result["data"]["status"] == "empty"
    assert result["data"]["content"] == ""


def test_read_document_missing(db_session):
    result = _read_document(db_session, "does-not-exist.md")
    assert result["data"]["status"] == "not_found"
    assert result["data"]["content"] is None


def test_read_document_traversal_blocked(db_session):
    result = _read_document(db_session, "../seed.json")
    assert result["data"]["status"] == "error"
    assert result["data"]["content"] is None


# ---- query_work_records：查询与过滤 ----


def _query_work_records(db, params=None, trace_id="T-DEMO-WORK"):
    return gateway.invoke_plugin(
        db,
        employee_id="DT-E10281",
        plugin_id="work-record-query",
        action="read",
        params={"employee_id": "DT-E10281", **(params or {})},
        trace_id=trace_id,
    )


def test_query_work_records_all(db_session):
    result = _query_work_records(db_session)
    assert result["ok"] is True
    assert result["data"]["status"] == "success"
    assert len(result["data"]["records"]) >= 8


def test_query_work_records_filter_completed(db_session):
    result = _query_work_records(db_session, {"status": "completed"})
    records = result["data"]["records"]
    assert records
    assert all(r["status"] == "completed" for r in records)


def test_query_work_records_empty(db_session):
    result = _query_work_records(db_session, {"status": "not-a-real-status"})
    assert result["data"]["records"] == []


# ---- ChatOrchestrator Tool dispatch 级测试 ----


def test_chat_orchestrator_dispatch_collaborate(db_session):
    script = [
        LLMResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id="tc-collab",
                    name="collaborate_employee",
                    arguments={"target_employee_id": "VE-0002", "action": "ask", "request": "请说明入职制度"},
                )
            ],
        ),
        LLMResponse(content="已获得协作结果"),
    ]
    orchestrator = ChatOrchestrator(FakeLLM(script))
    result = orchestrator.handle_message(db_session, employee_no="DT-E10281", message="帮我协作", session_id=None)
    assert result.tool_cards and result.tool_cards[0].decision == "allow"
    assert result.tool_cards[0].plugin_id == "employee-collaboration"


def test_chat_orchestrator_dispatch_read_document(db_session):
    script = [
        LLMResponse(
            content="",
            tool_calls=[
                ToolCall(id="tc-doc", name="read_document", arguments={"document_name": "normal-document.md"})
            ],
        ),
        LLMResponse(content="文档内容已读取"),
    ]
    orchestrator = ChatOrchestrator(FakeLLM(script))
    result = orchestrator.handle_message(db_session, employee_no="DT-E10281", message="读文档", session_id=None)
    assert result.tool_cards and result.tool_cards[0].decision == "allow"
    assert result.tool_cards[0].plugin_id == "document-read"


def test_chat_orchestrator_dispatch_query_work_records(db_session):
    script = [
        LLMResponse(
            content="",
            tool_calls=[
                ToolCall(id="tc-work", name="query_work_records", arguments={})
            ],
        ),
        LLMResponse(content="工作记录已查询"),
    ]
    orchestrator = ChatOrchestrator(FakeLLM(script))
    result = orchestrator.handle_message(db_session, employee_no="DT-E10281", message="查工作记录", session_id=None)
    assert result.tool_cards and result.tool_cards[0].decision == "allow"
    assert result.tool_cards[0].plugin_id == "work-record-query"


def test_query_work_records_ignores_llm_employee_id(db_session):
    """安全回归：LLM tool_call 参数中伪造的 employee_id 必须被当前 subject 覆盖。"""
    script = [
        LLMResponse(
            content="",
            tool_calls=[
                ToolCall(id="tc-work-spoof", name="query_work_records", arguments={"employee_id": "VE-9999"})
            ],
        ),
        LLMResponse(content="工作记录已查询"),
    ]
    orchestrator = ChatOrchestrator(FakeLLM(script))
    result = orchestrator.handle_message(db_session, employee_no="DT-E10281", message="查工作记录", session_id=None)
    assert result.tool_cards and result.tool_cards[0].decision == "allow"
    # 工具结果必须来自当前 subject（DT-E10281），而不是伪造的 VE-9999
    tool_msgs = [m for m in orchestrator.provider.calls[1] if m["role"] == "tool"]
    assert any("DT-E10281" in m["content"] for m in tool_msgs)
    assert all("VE-9999" not in m["content"] for m in tool_msgs)


def test_collaborate_request_echoed_in_response(db_session):
    result = gateway.invoke_plugin(
        db_session,
        employee_id="DT-E10281",
        plugin_id="employee-collaboration",
        action="execute",
        params={"target_employee_id": "VE-0002", "action": "ask", "request": "这是本次唯一测试问题ABC"},
        trace_id="T-COLLAB-RUNTIME-ABC",
    )
    assert result["data"]["status"] == "success"
    assert result["data"]["request"] == "这是本次唯一测试问题ABC"
    assert "这是本次唯一测试问题ABC" in result["data"]["response"]


def test_collaborate_trace_id_from_invocation(db_session):
    result = gateway.invoke_plugin(
        db_session,
        employee_id="DT-E10281",
        plugin_id="employee-collaboration",
        action="execute",
        params={"target_employee_id": "VE-0002", "action": "ask", "request": "x"},
        trace_id="T-COLLAB-RUNTIME-ABC",
    )
    assert result["data"]["trace_id"] == "T-COLLAB-RUNTIME-ABC"


def test_collaborate_source_employee_id_from_subject(db_session):
    result = gateway.invoke_plugin(
        db_session,
        employee_id="DT-E10281",
        plugin_id="employee-collaboration",
        action="execute",
        params={"target_employee_id": "VE-0002", "action": "ask", "request": "x"},
        trace_id="T-COLLAB-SRC-001",
    )
    assert result["data"]["source_employee_id"] == "DT-E10281"


def test_collaborate_source_employee_id_not_forgeable(db_session):
    script = [
        LLMResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id="tc-collab-spoof",
                    name="collaborate_employee",
                    arguments={
                        "target_employee_id": "VE-0002",
                        "action": "ask",
                        "request": "x",
                        "source_employee_id": "VE-9999",
                    },
                )
            ],
        ),
        LLMResponse(content="协作完成"),
    ]
    orchestrator = ChatOrchestrator(FakeLLM(script))
    result = orchestrator.handle_message(db_session, employee_no="DT-E10281", message="协作", session_id=None)
    assert result.tool_cards and result.tool_cards[0].decision == "allow"
    tool_msgs = [m for m in orchestrator.provider.calls[1] if m["role"] == "tool"]
    assert any("DT-E10281" in m["content"] for m in tool_msgs)
    assert all("VE-9999" not in m["content"] for m in tool_msgs)
