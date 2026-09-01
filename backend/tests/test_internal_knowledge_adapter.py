import httpx
import pytest

from app.services.gateway import _result_summary
from app.services.internal_knowledge_adapter import InternalKnowledgeAdapter, InternalKnowledgeError


def test_internal_adapter_uses_filters_and_normalizes_hits():
    captured = {}

    def handler(request):
        captured["payload"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"chunks": [{
            "docnm_kwd": "guide.pdf", "content_with_weight": "step" * 200,
            "rank_score": 0.91, "similarity": 0.72,
        }]}})

    client = httpx.Client(base_url="https://internal.invalid", transport=httpx.MockTransport(handler))
    result = InternalKnowledgeAdapter(kb_id_map={"KB-IT-SERVICE": 751}, client=client).search(
        employee_id="DT-E10281", knowledge_base_id="KB-IT-SERVICE",
        query="how", trace_id="T-INTERNAL-001",
    )
    client.close()
    assert captured["payload"]["enable_filters"] is True
    assert captured["payload"]["kb_id"] == 751
    assert result["source"] == "internal"
    assert result["hits"][0]["score"] == 0.91
    assert len(result["hits"][0]["snippet"]) == 500


def test_internal_adapter_requires_mapping():
    with pytest.raises(InternalKnowledgeError, match="未配置平台知识库映射"):
        InternalKnowledgeAdapter(kb_id_map={}).search(
            employee_id="X", knowledge_base_id="KB-X", query="q", trace_id="T",
        )


def test_internal_error_and_audit_do_not_leak_content():
    def handler(_request):
        return httpx.Response(403, text="credential and internal response")

    client = httpx.Client(base_url="https://internal.invalid", transport=httpx.MockTransport(handler))
    with pytest.raises(InternalKnowledgeError) as caught:
        InternalKnowledgeAdapter(kb_id_map={"KB-X": 1}, client=client).search(
            employee_id="X", knowledge_base_id="KB-X", query="secret question", trace_id="T",
        )
    client.close()
    assert "credential" not in str(caught.value)
    summary = _result_summary({
        "source": "internal", "knowledge_base_id": "KB-X", "query": "secret question",
        "hits": [{"title": "secret.pdf", "snippet": "secret content"}],
    })
    assert "secret question" not in summary
    assert "secret content" not in summary
    assert '"hit_count": 1' in summary

    mock_summary = _result_summary({
        "source": "demo", "knowledge_base_id": "KB-X", "query": "mock question",
        "hits": [{"title": "mock.pdf", "snippet": "mock content"}],
    })
    assert '"source": "mock"' in mock_summary
    assert "mock question" not in mock_summary
    assert "mock content" not in mock_summary
