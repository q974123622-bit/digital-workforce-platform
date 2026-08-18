"""RAGKnowledgeAdapter：向量检索实现（Query Embedding + 余弦 top-k）。

与 KnowledgeAdapter 同一 search() 契约；hits 在 {title, snippet} 基础上新增可选 score，
向后兼容。嵌入不可用（无 Key/超时/网络失败）时抛 EmbeddingUnavailableError，
由 select_adapter 的 rag 兜底层自动降级 MockKnowledgeAdapter（写降级审计，链路不中断）。

rerank：本期不实现（语料规模小、收益低），_rerank() 为预留扩展点。
"""

import numpy as np

from .. import models
from ..database import SessionLocal
from . import embedding
from .knowledge_adapter import KnowledgeAdapter


class RAGKnowledgeAdapter(KnowledgeAdapter):
    """基于 kb_chunk 索引的 RAG 检索（Embed + 余弦相似度 top-k）。"""

    def __init__(self, embedder=None, top_k: int = 5):
        self._embedder = embedder if embedder is not None else embedding.create_embedder()
        self.top_k = top_k

    def search(
        self,
        *,
        employee_id: str,
        knowledge_base_id: str,
        query: str,
        trace_id: str,
    ) -> dict:
        qvec = self._embed_query(query)
        rows = self._load_chunks(knowledge_base_id)
        if not rows:
            return {
                "source": "rag",
                "knowledge_base_id": knowledge_base_id,
                "query": query,
                "hits": [],
            }
        scored = self._cosine_topk(qvec, rows, self.top_k)
        hits = [
            {
                "title": row.title,
                "snippet": (row.content or "")[:200],
                "score": round(float(score), 4),
            }
            for score, row in scored
        ]
        return {
            "source": "rag",
            "knowledge_base_id": knowledge_base_id,
            "query": query,
            "hits": self._rerank(hits, query),
        }

    def _embed_query(self, query: str) -> np.ndarray:
        vectors = self._embedder.embed([query])
        if not vectors:
            raise embedding.EmbeddingUnavailableError("嵌入服务返回空结果")
        return np.asarray(vectors[0], dtype="float64")

    def _load_chunks(self, knowledge_base_id: str) -> list:
        db = session_factory()
        try:
            return (
                db.query(models.KnowledgeChunk)
                .filter(models.KnowledgeChunk.kb_id == knowledge_base_id)
                .order_by(models.KnowledgeChunk.id)
                .all()
            )
        finally:
            db.close()

    def _cosine_topk(self, qvec: np.ndarray, rows: list, top_k: int) -> list[tuple[float, object]]:
        if not rows or rows[0].embedding is None:
            return []
        mat = np.vstack([np.frombuffer(r.embedding, dtype="float32") for r in rows])
        if mat.shape[1] != qvec.shape[0]:
            # 索引维度与查询维度不一致（接入真实 Key 后未重建索引），返回空
            return []
        norms = np.linalg.norm(mat, axis=1)
        qnorm = np.linalg.norm(qvec)
        denom = norms * qnorm
        scores = np.zeros(len(rows))
        valid = denom > 0
        if np.any(valid):
            scores[valid] = (mat[valid] @ qvec) / denom[valid]
        order = np.argsort(-scores)[:top_k]
        return [(float(scores[i]), rows[i]) for i in order if scores[i] > 0]

    def _rerank(self, hits: list[dict], query: str) -> list[dict]:
        """预留 rerank 扩展点：本期不实现（语料规模小，收益低且增加延迟/成本）。"""
        return hits


# 会话工厂：生产环境默认 SessionLocal；测试可替换为 fixture 会话以隔离数据库。
session_factory = SessionLocal
