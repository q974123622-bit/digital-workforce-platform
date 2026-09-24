"""VeRAG MCP 知识适配器测试（离线，httpx.MockTransport）。

覆盖：正常命中、两层 JSON 容错、鉴权头透传、超时/鉴权失败/空结果的错误映射。
"""

import json

import httpx
import pytest

from app.services.external_mcp_client import ExternalMcpClient
from app.services.verag_mcp_knowledge_adapter import VeragKnowledgeError, VeragMcpKnowledgeAdapter

URL = "http://verag.example/mcp"
AUTH_HEADERS = {"Authorization": "Bearer test-token", "X-Tenant": "6"}


def _sse(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        text="data: " + json.dumps(payload),
        headers={"content-type": "text/event-stream"},
    )


def _tool_result(payload: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
    }


def _handler(retrieval_payload, seen: list[dict] | None = None, *, fail: Exception | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append({"headers": dict(request.headers), "body": json.loads(request.content)})
        if fail is not None:
            raise fail
        body = json.loads(request.content)
        method = body.get("method")
        if method == "initialize":
            return _sse({
                "jsonrpc": "2.0", "id": body["id"],
                "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "VeRAG MCP"}},
            })
        if method == "notifications/initialized":
            return _sse({"jsonrpc": "2.0", "result": {}})
        if method == "tools/call":
            return _sse(_tool_result(retrieval_payload))
        return _sse({"jsonrpc": "2.0", "id": body.get("id"), "result": {}})

    return handler


def _adapter(handler, *, kb_id_map=None, **kwargs) -> VeragMcpKnowledgeAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return VeragMcpKnowledgeAdapter(
        kb_id_map={"KB-IT-SERVICE": 623} if kb_id_map is None else kb_id_map,
        url=URL,
        auth_headers=AUTH_HEADERS,
        client=ExternalMcpClient(url=URL, api_key="", auth_headers=AUTH_HEADERS, client=client),
        **kwargs,
    )


def _search(adapter, query: str = "VPN 怎么连接"):
    return adapter.search(
        employee_id="AI-GENERAL", knowledge_base_id="KB-IT-SERVICE",
        query=query, trace_id="T-VERAG",
    )


def test_search_returns_hits_and_sends_both_auth_headers():
    seen: list[dict] = []
    adapter = _adapter(_handler({
        "chunks": [
            {"doc_name": "办公IT服务目录.docx", "rank_score": 0.87,
             "content": json.dumps({"value": "公司SSL VPN的登录地址是 https://vpn.xyzq.cn"})},
        ],
        "total": 1,
    }, seen))

    data = _search(adapter)

    assert data["source"] == "verag"
    assert data["knowledge_base_id"] == "KB-IT-SERVICE"
    assert data["hits"] == [{
        "title": "办公IT服务目录.docx",
        "snippet": "公司SSL VPN的登录地址是 https://vpn.xyzq.cn",
        "score": 0.87,
    }]
    # 检索入参：kb_ids 传远端库 ID（不是平台 ID）
    call = [item for item in seen if item["body"].get("method") == "tools/call"][0]
    assert call["body"]["params"]["arguments"]["kb_ids"] == [623]
    # 双鉴权头必须都在
    assert call["headers"]["authorization"] == "Bearer test-token"
    assert call["headers"]["x-tenant"] == "6"


def test_search_tolerates_non_json_inner_content():
    """内层 content 不是 JSON 时用原文当片段，不能让整次检索失败。"""
    adapter = _adapter(_handler({
        "chunks": [
            {"doc_name": "A", "rank_score": 0.5, "content": "纯文本片段"},
            {"doc_name": "B", "similarity": 0.4, "content": None},
        ],
    }))

    hits = _search(adapter)["hits"]

    assert hits[0]["snippet"] == "纯文本片段"
    assert hits[1]["snippet"] == ""
    assert hits[1]["score"] == 0.4


def test_search_truncates_long_snippet():
    adapter = _adapter(_handler({
        "chunks": [{"doc_name": "长文", "content": "字" * 900}],
    }))

    assert len(_search(adapter)["hits"][0]["snippet"]) == 500


def test_search_requires_platform_kb_mapping():
    adapter = _adapter(_handler({"chunks": []}), kb_id_map={})

    with pytest.raises(VeragKnowledgeError, match="未配置平台知识库映射"):
        _search(adapter)


def test_search_rejects_blank_query():
    adapter = _adapter(_handler({"chunks": []}))

    with pytest.raises(VeragKnowledgeError, match="不能为空"):
        _search(adapter, "   ")


def test_search_maps_auth_failure_to_readable_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    adapter = _adapter(handler)

    with pytest.raises(VeragKnowledgeError, match="认证失败"):
        _search(adapter)


def test_search_maps_timeout_to_readable_error():
    adapter = _adapter(_handler(None, fail=httpx.ConnectTimeout("boom")))

    with pytest.raises(VeragKnowledgeError, match="超时|无法连接"):
        _search(adapter)


def test_search_rejects_empty_content():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "tools/call":
            return _sse({"jsonrpc": "2.0", "id": 1, "result": {"content": []}})
        if body.get("method") == "initialize":
            return _sse({"jsonrpc": "2.0", "id": body["id"], "result": {}})
        return _sse({"jsonrpc": "2.0", "result": {}})

    adapter = _adapter(handler)

    with pytest.raises(VeragKnowledgeError, match="返回内容为空"):
        _search(adapter)


def test_list_knowledge_bases_reads_items_and_total():
    seen: list[dict] = []
    adapter = _adapter(_handler({
        "total": 17, "has_more": False,
        "items": [{"knowledge_base_id": "623", "name": "信息技术部-QA库", "document_count": 1598}],
    }, seen))

    payload = adapter.list_knowledge_bases(page=1, page_size=50)

    assert payload["total"] == 17
    call = [item for item in seen if item["body"].get("method") == "tools/call"][0]
    assert call["body"]["params"]["name"] == "list_knowledge_bases"
    assert call["body"]["params"]["arguments"] == {"page": 1, "page_size": 50}
