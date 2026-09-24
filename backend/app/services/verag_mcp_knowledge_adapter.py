"""内部知识引擎 VeRAG MCP 知识适配器（生产环境通道）。

与 ``InternalKnowledgeAdapter``（测试环境 REST）并存：同一个平台知识库在
测试环境走 REST、生产环境走 VeRAG，由 ``knowledge_adapter.select_adapter`` 选路。

协议要点（实测，2026-09-21）：
- 标准 Streamable HTTP MCP，鉴权是**双头**：``Authorization: Bearer <token>``
  与 ``X-Tenant: 6``；因此复用 ``ExternalMcpClient`` 的 ``auth_headers``。
- 检索工具 ``multi_retrieval``，入参 ``{"question": ..., "kb_ids": [...]}``
  （支持一次多库；本期只传单库，保持 ``search_knowledge`` 的单库契约与审计口径）。
- 返回体要**解两层 JSON**：``result.content[0].text`` 是 JSON 字符串，其内部
  ``content`` 字段还是 ``{"value": ...}`` 形式的 JSON 字符串。任一层不是 JSON
  时只跳过该条，不能整单失败。
- 命中字段是 ``doc_name`` / ``rank_score`` / ``similarity``，``total`` 上限 30。

安全：token 只经 ``ExternalMcpClient`` 的请求头发送，本模块不落日志、不落库，
异常只暴露可读原因。
"""

from __future__ import annotations

import json
from typing import Any

from . import config
from .external_mcp_client import ExternalMcpClient, ExternalMcpError
from .knowledge_adapter import KnowledgeAdapter

# 单条片段截断长度，与 REST 适配器保持一致，避免把整篇文档塞进上下文。
SNIPPET_MAX_CHARS = 500


class VeragKnowledgeError(RuntimeError):
    """VeRAG 检索失败（不暴露凭据与响应正文）。"""


class VeragMcpKnowledgeAdapter(KnowledgeAdapter):
    def __init__(
        self,
        *,
        kb_id_map: dict[str, int] | None = None,
        url: str | None = None,
        auth_headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        retrieval_tool: str | None = None,
        list_tool: str | None = None,
        protocol_version: str | None = None,
        client: ExternalMcpClient | None = None,
    ):
        self._kb_id_map = dict(kb_id_map or {})
        self._url = (url or config.verag_mcp_url() or "").rstrip("/")
        self._auth_headers = dict(
            auth_headers if auth_headers is not None else config.verag_mcp_auth_headers()
        )
        self._timeout_seconds = timeout_seconds or config.verag_mcp_timeout()
        self._retrieval_tool = retrieval_tool or config.verag_mcp_retrieval_tool()
        self._list_tool = list_tool or config.verag_mcp_list_tool()
        self._protocol_version = protocol_version or config.verag_mcp_protocol_version()
        self._client = client

    # -- client ----------------------------------------------------------

    def _get_client(self) -> ExternalMcpClient:
        if self._client is None:
            if not self._url:
                raise VeragKnowledgeError("未配置内部知识引擎 VeRAG MCP 地址")
            self._client = ExternalMcpClient(
                url=self._url,
                api_key="",
                timeout_seconds=self._timeout_seconds,
                auth_headers=self._auth_headers,
                protocol_version=self._protocol_version,
            )
        return self._client

    def describe(self) -> dict[str, Any]:
        """握手并返回非敏感通道摘要（服务名 / 协议版本 / 工具数），供探测复用。"""
        client = self._get_client()
        try:
            handshake = client.initialize()
            tools = client.list_tools()
        except ExternalMcpError as exc:
            raise VeragKnowledgeError(str(exc)) from None
        server_info = handshake.get("serverInfo")
        return {
            "protocol_version": str(handshake.get("protocolVersion") or ""),
            "server_info": server_info if isinstance(server_info, dict) else {},
            "tools_count": len(tools),
        }

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用 MCP 工具并解析第一段 text 载荷（单层 JSON）。"""
        try:
            result = self._get_client().call_tool(name, arguments)
        except ExternalMcpError as exc:
            raise VeragKnowledgeError(str(exc)) from None
        payload = self._first_text_payload(result)
        if payload is None:
            raise VeragKnowledgeError("VeRAG 返回内容为空")
        return payload

    @staticmethod
    def _first_text_payload(result: dict[str, Any]) -> dict[str, Any] | None:
        for item in result.get("content") or []:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            raw = item.get("text")
            if not isinstance(raw, str) or not raw.strip():
                continue
            try:
                parsed = json.loads(raw)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    # -- 检索 ------------------------------------------------------------

    def search(self, *, employee_id: str, knowledge_base_id: str, query: str, trace_id: str) -> dict:
        remote_kb_id = self._kb_id_map.get(knowledge_base_id)
        if remote_kb_id is None:
            raise VeragKnowledgeError(f"未配置平台知识库映射：{knowledge_base_id}")
        if not query.strip():
            raise VeragKnowledgeError("内部知识检索问题不能为空")
        payload = self._call_tool(
            self._retrieval_tool, {"question": query, "kb_ids": [remote_kb_id]},
        )
        chunks = payload.get("chunks")
        if not isinstance(chunks, list):
            raise VeragKnowledgeError("VeRAG 检索响应缺少 chunks")
        hits: list[dict[str, Any]] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            score = chunk.get("rank_score")
            if score is None:
                score = chunk.get("similarity")
            hits.append({
                "title": str(chunk.get("doc_name") or ""),
                "snippet": _unwrap_content(chunk.get("content"))[:SNIPPET_MAX_CHARS],
                "score": score,
            })
        return {
            "source": "verag",
            "knowledge_base_id": knowledge_base_id,
            "query": query,
            "hits": hits,
        }

    # -- 清单（探测与目录同步复用）---------------------------------------

    def list_knowledge_bases(self, *, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        """返回 VeRAG 可见知识库清单（``items`` / ``total`` / ``has_more``）。"""
        return self._call_tool(self._list_tool, {"page": page, "page_size": page_size})


def _unwrap_content(raw: Any) -> str:
    """解开片段内容的第二层 JSON：``{"value": "..."}`` → 正文。

    任一层不是 JSON 时原样返回（不能因为一条脏数据让整次检索失败）。
    """
    if raw is None:
        return ""
    text = raw if isinstance(raw, str) else str(raw)
    if not text.strip():
        return ""
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if isinstance(parsed, dict):
        value = parsed.get("value")
        if isinstance(value, str):
            return value
        if value is not None:
            return json.dumps(value, ensure_ascii=False)
    return text
