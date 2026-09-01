"""Read-only adapter for the internal knowledge engine retrieval API."""

from __future__ import annotations

from typing import Any

import httpx

from . import config
from .knowledge_adapter import KnowledgeAdapter

RETRIEVAL_PATH = "/marketing_agent/api/v2/rag/chunk/retrieval"


class InternalKnowledgeError(RuntimeError):
    """Internal retrieval failed without exposing credentials or response bodies."""


class InternalKnowledgeAdapter(KnowledgeAdapter):
    def __init__(
        self,
        *,
        base_url: str | None = None,
        x_org: str | None = None,
        x_tenant: str | None = None,
        x_user: str | None = None,
        authorization: str | None = None,
        kb_id_map: dict[str, int] | None = None,
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ):
        self._base_url = (base_url or config.internal_kb_base_url() or "").rstrip("/")
        self._x_org = x_org or config.get(config.INTERNAL_KB_X_ORG) or ""
        self._x_tenant = x_tenant or config.get(config.INTERNAL_KB_X_TENANT) or ""
        self._x_user = x_user or config.get(config.INTERNAL_KB_X_USER) or ""
        self._authorization = authorization or config.get(config.INTERNAL_KB_AUTHORIZATION) or ""
        self._kb_id_map = kb_id_map if kb_id_map is not None else config.internal_kb_id_map()
        self._timeout_seconds = timeout_seconds
        self._client = client

    def search(self, *, employee_id: str, knowledge_base_id: str, query: str, trace_id: str) -> dict:
        remote_kb_id = self._kb_id_map.get(knowledge_base_id)
        if remote_kb_id is None:
            raise InternalKnowledgeError(f"未配置平台知识库映射：{knowledge_base_id}")
        if not query.strip():
            raise InternalKnowledgeError("内部知识检索问题不能为空")
        payload = {
            "kb_id": remote_kb_id,
            "question": query,
            "similarity_threshold": 0.1,
            "dense_weight": 0.5,
            "top_k": 10,
            "top_n": 5,
            "enable_filters": True,
            "enable_rerank": True,
            "enable_llm_rerank": False,
        }
        try:
            if self._client is not None:
                response = self._client.post(RETRIEVAL_PATH, json=payload)
            else:
                with httpx.Client(
                    base_url=self._base_url,
                    headers=self._headers(),
                    timeout=self._timeout_seconds,
                ) as client:
                    response = client.post(RETRIEVAL_PATH, json=payload)
            response.raise_for_status()
        except httpx.TimeoutException:
            raise InternalKnowledgeError("内部知识引擎请求超时") from None
        except httpx.HTTPStatusError as exc:
            messages = {
                401: "内部知识引擎认证失败",
                403: "当前身份无权访问内部知识库",
                404: "内部知识引擎检索接口不存在",
            }
            raise InternalKnowledgeError(messages.get(exc.response.status_code, "内部知识引擎请求失败")) from None
        except httpx.RequestError:
            raise InternalKnowledgeError("无法连接内部知识引擎") from None
        try:
            envelope: Any = response.json()
        except ValueError:
            raise InternalKnowledgeError("内部知识引擎返回了无效 JSON") from None
        if not isinstance(envelope, dict):
            raise InternalKnowledgeError("内部知识引擎返回了非预期响应")
        if envelope.get("code") != 0:
            raise InternalKnowledgeError(f"内部知识引擎业务错误：code={envelope.get('code')}")
        data = envelope.get("data")
        chunks = data.get("chunks") if isinstance(data, dict) else None
        if not isinstance(chunks, list):
            raise InternalKnowledgeError("内部知识引擎响应缺少 data.chunks")
        hits = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            score = chunk.get("rank_score")
            if score is None:
                score = chunk.get("similarity")
            hits.append({
                "title": str(chunk.get("docnm_kwd") or ""),
                "snippet": str(chunk.get("content_with_weight") or "")[:500],
                "score": score,
            })
        return {
            "source": "internal",
            "knowledge_base_id": knowledge_base_id,
            "query": query,
            "hits": hits,
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "x-org": self._x_org,
            "x-tenant": self._x_tenant,
            "X-User": self._x_user,
            "Authorization": self._authorization,
        }
