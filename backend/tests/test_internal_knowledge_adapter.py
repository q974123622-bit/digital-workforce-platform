import httpx
import pytest

from app import models
from app.services.gateway import _result_summary
from app.services.internal_knowledge_adapter import (
    InternalKnowledgeAdapter,
    InternalKnowledgeError,
)
from app.services.knowledge_adapter import InternalKnowledgeAdapterStub, select_adapter


def _client(handler):
    return httpx.Client(
        base_url="https://internal.invalid",
        headers={"Authorization": "secret-value"},
        transport=httpx.MockTransport(handler),
    )


def test_internal_adapter_retrieves_with_filters_and_normalizes_hits():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["payload"] = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "chunks": [
                        {
                            "docnm_kwd": "VPN指南.pdf",
                            "content_with_weight": "申请流程" * 200,
                            "rank_score": 0.91,
                            "similarity": 0.72,
                        }
                    ]
                },
            },
        )

    client = _client(handler)
    adapter = InternalKnowledgeAdapter(kb_id_map={"KB-IT-SERVICE": 751}, client=client)
    result = adapter.search(
        employee_id="DT-E10281",
        knowledge_base_id="KB-IT-SERVICE",
        query="VPN 怎么申请",
        trace_id="T-INTERNAL-001",
    )
    client.close()

    assert captured["path"] == "/marketing_agent/api/v2/rag/chunk/retrieval"
    assert captured["payload"]["kb_id"] == 751
    assert captured["payload"]["enable_filters"] is True
    assert captured["payload"]["top_n"] == 5
    assert result["source"] == "internal"
    assert result["knowledge_base_id"] == "KB-IT-SERVICE"
    assert result["hits"][0]["title"] == "VPN指南.pdf"
    assert result["hits"][0]["score"] == 0.91
    assert len(result["hits"][0]["snippet"]) == 500


def test_internal_adapter_requires_explicit_mapping():
    adapter = InternalKnowledgeAdapter(kb_id_map={})
    with pytest.raises(InternalKnowledgeError, match="未配置平台知识库映射"):
        adapter.search(
            employee_id="DT-E10281",
            knowledge_base_id="KB-IT-SERVICE",
            query="VPN",
            trace_id="T-INTERNAL-002",
        )


def test_real_internal_adapter_requires_explicit_internal_mode(db_session, monkeypatch):
    plugin = db_session.get(models.Plugin, "knowledge-l2")
    kb = db_session.get(models.KnowledgeBase, "KB-IT-SERVICE")
    monkeypatch.setenv("DWP_KB_MODE", "mock")
    monkeypatch.setenv("DWP_INTERNAL_KB_BASE_URL", "https://internal.invalid")
    monkeypatch.setenv("DWP_INTERNAL_KB_X_ORG", "org")
    monkeypatch.setenv("DWP_INTERNAL_KB_X_TENANT", "tenant")
    monkeypatch.setenv("DWP_INTERNAL_KB_X_USER", "user")
    monkeypatch.setenv("DWP_INTERNAL_KB_AUTHORIZATION", "secret-value")
    monkeypatch.setenv("DWP_INTERNAL_KB_ID_MAP", '{"KB-IT-SERVICE": 751}')

    assert not isinstance(select_adapter(plugin, kb), InternalKnowledgeAdapter)
    plugin.endpoint_ref = "internal://kb/l2"
    assert isinstance(select_adapter(plugin, kb), InternalKnowledgeAdapterStub)


def test_internal_adapter_does_not_expose_service_error_body_or_credentials():
    def handler(request):
        return httpx.Response(403, text="secret-value internal response")

    client = _client(handler)
    adapter = InternalKnowledgeAdapter(kb_id_map={"KB-IT-SERVICE": 751}, client=client)
    with pytest.raises(InternalKnowledgeError) as caught:
        adapter.search(
            employee_id="DT-E10281",
            knowledge_base_id="KB-IT-SERVICE",
            query="VPN",
            trace_id="T-INTERNAL-003",
        )
    client.close()

    assert "403" not in str(caught.value)
    assert "secret-value" not in str(caught.value)
    assert "无权访问" in str(caught.value)


def test_internal_result_audit_summary_excludes_knowledge_content():
    summary = _result_summary(
        {
            "source": "internal",
            "knowledge_base_id": "KB-IT-SERVICE",
            "query": "sensitive question",
            "hits": [{"title": "internal.pdf", "snippet": "sensitive content", "score": 0.9}],
        }
    )

    assert '"source": "internal"' in summary
    assert '"hit_count": 1' in summary
    assert "sensitive question" not in summary
    assert "sensitive content" not in summary
    assert "internal.pdf" not in summary


def test_internal_mode_runs_through_gateway_policy_and_safe_audit(client, monkeypatch):
    monkeypatch.setenv("DWP_KB_MODE", "internal")
    monkeypatch.setenv("DWP_INTERNAL_KB_BASE_URL", "https://internal.invalid")
    monkeypatch.setenv("DWP_INTERNAL_KB_X_ORG", "org")
    monkeypatch.setenv("DWP_INTERNAL_KB_X_TENANT", "tenant")
    monkeypatch.setenv("DWP_INTERNAL_KB_X_USER", "user")
    monkeypatch.setenv("DWP_INTERNAL_KB_AUTHORIZATION", "secret-value")
    monkeypatch.setenv("DWP_INTERNAL_KB_ID_MAP", '{"KB-IT-SERVICE": 751}')

    def fake_search(self, *, employee_id, knowledge_base_id, query, trace_id):
        assert self._kb_id_map[knowledge_base_id] == 751
        return {
            "source": "internal",
            "knowledge_base_id": knowledge_base_id,
            "query": query,
            "hits": [
                {
                    "title": "internal.pdf",
                    "snippet": "sensitive internal content",
                    "score": 0.95,
                }
            ],
        }

    monkeypatch.setattr(InternalKnowledgeAdapter, "search", fake_search)

    response = client.post(
        "/internal/knowledge/search",
        json={
            "employee_id": "DT-E10281",
            "knowledge_base_id": "KB-IT-SERVICE",
            "query": "sensitive question",
            "trace_id": "T-INTERNAL-CHAIN-001",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "allow"
    assert body["policy_id"] == "POLICY-001"
    assert body["data"]["source"] == "internal"
    audit = client.get(f"/api/v1/audit/{body['audit_ids'][0]}").json()
    assert '"hit_count": 1' in audit["result_summary"]
    assert "sensitive question" not in audit["result_summary"]
    assert "sensitive internal content" not in audit["result_summary"]
    assert "internal.pdf" not in audit["result_summary"]