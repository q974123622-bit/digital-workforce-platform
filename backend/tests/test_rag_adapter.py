"""RAG Knowledge Adapter 测试（FakeEmbedding 固定向量，不依赖真实 API）。

覆盖：索引构建（四分类全部文件产生 chunk）、top-k 排序与 score、hits 契约、
链路 allow/deny 与审计、嵌入不可用自动降级 Mock。
"""

import hashlib
import math

import pytest
from sqlalchemy.orm import sessionmaker

from app import models
from app.services import embedding as embedding_mod
from app.services import kb_index
from app.services.embedding import EmbeddingUnavailableError
from app.services.rag_knowledge_adapter import RAGKnowledgeAdapter

REPO_ROOT = kb_index.REPO_ROOT


class FakeEmbedding:
    """确定性向量：字符 1-2 gram 特征哈希，相似文本向量相关，可复现 top-k 排序。"""

    def __init__(self, dims: int = 32):
        self.dims = dims

    def embed(self, texts):
        return [self._vec(t) for t in texts]

    def _vec(self, text):
        vec = [0.0] * self.dims
        t = text.lower()
        grams = set(t)
        grams.update(t[i : i + 2] for i in range(len(t) - 1))
        for gram in grams:
            digest = hashlib.md5(gram.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dims
            sign = 1.0 if (digest[4] & 1) else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class RaisingEmbedding:
    def embed(self, texts):
        raise EmbeddingUnavailableError("fake embedding unavailable")


@pytest.fixture()
def engine_factory(db_session):
    """基于 fixture 会话同一引擎的独立 session factory（StaticPool 共享连接，数据可见）。"""
    engine = db_session.get_bind()
    return sessionmaker(bind=engine, expire_on_commit=False)


def _build_index(db_session):
    return kb_index.build_index(db_session, embedder=FakeEmbedding(), rebuild=True)


def _search(client, employee_id, kb_id, query, trace_id):
    return client.post(
        "/internal/knowledge/search",
        json={
            "employee_id": employee_id,
            "knowledge_base_id": kb_id,
            "query": query,
            "trace_id": trace_id,
        },
    )


def test_index_build_all_categories_produce_chunks(db_session):
    stats = _build_index(db_session)
    for kb_id in ("KB-IT-SERVICE", "KB-SECURITIES", "KB-REG-INTERNAL", "KB-REG-EXTERNAL"):
        assert stats.get(kb_id, 0) > 0
    chunks = db_session.query(models.KnowledgeChunk).all()
    assert chunks
    for cat, kb_id in (
        ("it-service", "KB-IT-SERVICE"),
        ("securities", "KB-SECURITIES"),
        ("internal-reg", "KB-REG-INTERNAL"),
        ("external-reg", "KB-REG-EXTERNAL"),
    ):
        expected = {
            p.relative_to(REPO_ROOT).as_posix()
            for p in (REPO_ROOT / "mock-data" / "kb" / cat).rglob("*")
            if p.is_file()
        }
        sources = {c.source_file for c in chunks if c.kb_id == kb_id}
        assert sources == expected, f"{kb_id} 应覆盖分类目录全部文件"
    sample = chunks[0]
    assert sample.embedding and sample.dims == FakeEmbedding().dims


def test_retrieval_topk_and_score_contract(db_session, monkeypatch, engine_factory):
    _build_index(db_session)
    monkeypatch.setattr("app.services.rag_knowledge_adapter.session_factory", engine_factory)
    adapter = RAGKnowledgeAdapter(embedder=FakeEmbedding(), top_k=3)
    result = adapter.search(
        employee_id="DT-E10281",
        knowledge_base_id="KB-IT-SERVICE",
        query="VPN 怎么连",
        trace_id="T-RAG-001",
    )
    assert result["source"] == "rag"
    assert result["knowledge_base_id"] == "KB-IT-SERVICE"
    hits = result["hits"]
    assert hits and len(hits) <= 3
    for hit in hits:
        # score 为新增可选字段，{title, snippet} 契约保持向后兼容
        assert {"title", "snippet", "score"} <= set(hit)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert 0.0 <= scores[0] <= 1.0


def test_chain_rag_allow_with_score(client, db_session, monkeypatch, engine_factory):
    _build_index(db_session)
    monkeypatch.setenv("DWP_KB_MODE", "rag")
    monkeypatch.setattr(embedding_mod, "create_embedder", lambda: FakeEmbedding())
    monkeypatch.setattr("app.services.rag_knowledge_adapter.session_factory", engine_factory)
    resp = _search(client, "AI-GENERAL", "KB-IT-SERVICE", "VPN 怎么连", "T-RAG-CHAIN-001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["decision"] == "allow"
    assert body["data"]["source"] == "rag"
    assert body["data"]["hits"]
    assert "score" in body["data"]["hits"][0]


def test_chain_intern_deny_with_audit(client, db_session, monkeypatch, engine_factory):
    _build_index(db_session)
    monkeypatch.setenv("DWP_KB_MODE", "rag")
    monkeypatch.setattr(embedding_mod, "create_embedder", lambda: FakeEmbedding())
    monkeypatch.setattr("app.services.rag_knowledge_adapter.session_factory", engine_factory)
    resp = _search(client, "DT-E20999", "KB-SECURITIES", "融资融券流程", "T-RAG-CHAIN-002")
    assert resp.status_code == 403
    error = resp.json()["error"]
    assert error["code"] == "POLICY_DENIED"
    assert error["detail"]["policy_id"] == "POLICY-002"
    audit = client.get(f"/api/v1/audit/{error['detail']['audit_id']}").json()
    assert audit["knowledge_base_id"] == "KB-SECURITIES"
    assert audit["decision"] == "deny"
    assert audit["employee_id"] == "DT-E20999"


def test_degradation_when_embedding_unavailable(client, db_session, monkeypatch, engine_factory):
    monkeypatch.setenv("DWP_KB_MODE", "rag")
    monkeypatch.setattr(embedding_mod, "create_embedder", lambda: RaisingEmbedding())
    monkeypatch.setattr("app.services.knowledge_adapter.session_factory", engine_factory)
    resp = _search(client, "AI-GENERAL", "KB-IT-SERVICE", "VPN 怎么连", "T-RAG-DEGRADE-001")
    assert resp.status_code == 200  # 不抛 5xx
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["source"] == "demo"  # 降级 Mock
    assert len(body["data"]["hits"]) >= 1
    degraded = (
        db_session.query(models.AuditEvent)
        .filter(models.AuditEvent.reason.like("%降级%"))
        .all()
    )
    assert degraded


def test_rag_adapter_raises_when_unavailable():
    adapter = RAGKnowledgeAdapter(embedder=RaisingEmbedding())
    with pytest.raises(EmbeddingUnavailableError):
        adapter.search(
            employee_id="DT-E10281",
            knowledge_base_id="KB-IT-SERVICE",
            query="x",
            trace_id="T-RAG-RAISE",
        )
