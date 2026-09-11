"""Standard Streamable HTTP MCP client for external MCP servers.

本模块实现平台「作为 MCP Client 连接第三方 MCP Server」的能力。仅负责
协议层（initialize / tools/list / tools/call），不涉及授权、缓存与命名空间，
这些由 ``external_mcp_adapter`` 负责。

安全约定（与 InternalKnowledgeAdapter 一致）：
- 凭据（``api_key``）仅通过 ``x-api-key`` 请求头发送，不写入日志；
- 异常只暴露错误类型与简要信息，不暴露凭据或响应大段内容；
- 支持注入 ``httpx.Client``，便于使用 ``httpx.MockTransport`` 做离线单测。
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class ExternalMcpError(RuntimeError):
    """External MCP call failed without exposing credentials or response bodies."""


class ExternalMcpClient:
    """Minimal stateless Streamable HTTP MCP client (JSON-RPC 2.0 over HTTP)."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        timeout_seconds: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not url:
            raise ExternalMcpError("External MCP URL 未配置")
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._next_id = 1
        self._protocol_version = "2024-11-05"
        self._initialized = False
        self._session_id = ""

    # -- helpers ---------------------------------------------------------

    def _request_id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        return request_id

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            if self._client is not None:
                response = self._client.post(
                    self._url, json=payload, headers=self._headers(),
                )
            else:
                with httpx.Client(timeout=self._timeout_seconds) as client:
                    response = client.post(
                        self._url, json=payload, headers=self._headers(),
                    )
            response.raise_for_status()
        except httpx.TimeoutException:
            raise ExternalMcpError("External MCP 请求超时") from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            message = {
                401: "External MCP 认证失败（x-api-key 无效）",
                403: "External MCP 拒绝访问",
                404: "External MCP 端点不存在",
            }.get(status, f"External MCP HTTP {status}")
            raise ExternalMcpError(message) from None
        except httpx.RequestError:
            raise ExternalMcpError("无法连接 External MCP 服务") from None

        # 保存会话 ID（stateful streamable HTTP 需要后续请求携带）
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id

        content_type = response.headers.get("content-type", "")
        text = response.text
        if "text/event-stream" in content_type or text.lstrip().startswith(("data:", "event:", "id:")):
            envelope = self._parse_sse(text)
        else:
            try:
                envelope = response.json()
            except ValueError:
                raise ExternalMcpError("External MCP 返回了无效 JSON") from None
        if not isinstance(envelope, dict):
            raise ExternalMcpError("External MCP 返回了非预期响应") from None

        if "error" in envelope:
            error = envelope.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise ExternalMcpError(f"External MCP 协议错误 {code}: {message}")
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise ExternalMcpError("External MCP 响应缺少 result")
        return result

    def _parse_sse(self, text: str) -> dict[str, Any]:
        """解析 SSE 文本，返回最后一个 data: 事件的 JSON 对象。

        某些 MCP server（如兴业证券）在 tools/list、tools/call 时以
        ``id:``/``event:``/``data:`` 的 SSE 帧返回，需提取最后一个 data 载荷。
        """
        data_payload = None
        for line in text.splitlines():
            if line.startswith("data:"):
                data_payload = line[5:].strip()
        if not data_payload:
            raise ExternalMcpError("External MCP 返回了空 SSE 数据")
        try:
            envelope = json.loads(data_payload)
        except ValueError:
            raise ExternalMcpError("External MCP SSE 数据非有效 JSON") from None
        return envelope

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self.initialize()
        self._initialized = True

    # -- public API ------------------------------------------------------

    def initialize(self) -> dict[str, Any]:
        """执行 MCP initialize 握手，返回 server capabilities。"""
        result = self._post({
            "jsonrpc": "2.0",
            "id": self._request_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": self._protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "dwp-platform", "version": "0.3.0"},
            },
        })
        # 通知服务器客户端已完成初始化（stateless server 可忽略，但保持协议完整）
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except ExternalMcpError:
            # 部分 server 对 notification 不返回 result，忽略即可
            pass
        self._initialized = True
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """返回 MCP server 暴露的工具列表（含 name/description/inputSchema）。"""
        self._ensure_initialized()
        result = self._post({
            "jsonrpc": "2.0",
            "id": self._request_id(),
            "method": "tools/list",
            "params": {},
        })
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise ExternalMcpError("External MCP tools/list 缺少 tools 数组")
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用指定工具，返回 MCP result（通常含 ``content`` 与 ``isError``）。"""
        self._ensure_initialized()
        return self._post({
            "jsonrpc": "2.0",
            "id": self._request_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
