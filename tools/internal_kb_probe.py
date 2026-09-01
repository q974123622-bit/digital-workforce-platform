#!/usr/bin/env python3
"""Read-only connectivity probe for the internal knowledge engine."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


LIST_PATH = "/marketing_agent/api/v2/rag/kb/list"
RETRIEVAL_PATH = "/marketing_agent/api/v2/rag/chunk/retrieval"
DEFAULT_TIMEOUT_SECONDS = 15.0
SNIPPET_LIMIT = 240

ENV_BASE_URL = "DWP_INTERNAL_KB_BASE_URL"
ENV_X_ORG = "DWP_INTERNAL_KB_X_ORG"
ENV_X_TENANT = "DWP_INTERNAL_KB_X_TENANT"
ENV_X_USER = "DWP_INTERNAL_KB_X_USER"
ENV_AUTHORIZATION = "DWP_INTERNAL_KB_AUTHORIZATION"
REQUIRED_ENV_VARS = (
    ENV_BASE_URL,
    ENV_X_ORG,
    ENV_X_TENANT,
    ENV_X_USER,
    ENV_AUTHORIZATION,
)


class ProbeError(Exception):
    """A safe, user-facing probe error that never contains request headers."""


@dataclass(frozen=True)
class ProbeConfig:
    base_url: str
    x_org: str
    x_tenant: str
    x_user: str
    authorization: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "ProbeConfig":
        values = os.environ if environ is None else environ
        missing = [name for name in REQUIRED_ENV_VARS if not values.get(name, "").strip()]
        if missing:
            raise ProbeError("缺少必需环境变量：" + ", ".join(missing))

        base_url = values[ENV_BASE_URL].strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ProbeError(f"{ENV_BASE_URL} 必须以 http:// 或 https:// 开头")

        return cls(
            base_url=base_url,
            x_org=values[ENV_X_ORG].strip(),
            x_tenant=values[ENV_X_TENANT].strip(),
            x_user=values[ENV_X_USER].strip(),
            authorization=values[ENV_AUTHORIZATION].strip(),
        )

    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "x-org": self.x_org,
            "x-tenant": self.x_tenant,
            "X-User": self.x_user,
            "Authorization": self.authorization,
        }


OpenUrl = Callable[..., Any]


def _safe_service_message(message: Any, config: ProbeConfig) -> str:
    safe_message = str(message).strip()[:200] if message is not None else "未提供错误信息"
    sensitive_values = (
        config.authorization,
        config.x_org,
        config.x_tenant,
        config.x_user,
        config.base_url,
    )
    for value in sensitive_values:
        if value:
            safe_message = safe_message.replace(value, "[REDACTED]")
    return safe_message


def _request_json(
    config: ProbeConfig,
    *,
    method: str,
    path: str,
    query: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
    opener: OpenUrl = urlopen,
) -> Any:
    url = config.base_url + path
    if query:
        url += "?" + urlencode(query)

    body = None
    headers = config.headers()
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=body, headers=headers, method=method)
    try:
        with opener(request, timeout=config.timeout_seconds) as response:
            raw_body = response.read()
    except HTTPError as exc:
        if exc.code == 401:
            detail = "认证失败，请检查身份与认证环境变量"
        elif exc.code == 403:
            detail = "访问被拒绝，请检查当前身份的知识库权限"
        elif exc.code == 404:
            detail = "接口不存在，请检查基础地址和接口版本"
        else:
            detail = "服务返回 HTTP 错误"
        raise ProbeError(f"{detail}（HTTP {exc.code}）") from None
    except (TimeoutError, socket.timeout):
        raise ProbeError(f"请求超时（{config.timeout_seconds:g} 秒）") from None
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise ProbeError(f"请求超时（{config.timeout_seconds:g} 秒）") from None
        raise ProbeError("无法连接内部知识引擎，请检查网络和基础地址") from None
    except OSError:
        raise ProbeError("无法连接内部知识引擎，请检查网络和基础地址") from None

    try:
        envelope = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProbeError("内部知识引擎返回了无效的 JSON 响应") from None
    if not isinstance(envelope, dict):
        raise ProbeError("内部知识引擎返回了非预期的响应结构")

    code = envelope.get("code")
    if code != 0:
        safe_message = _safe_service_message(envelope.get("msg"), config)
        raise ProbeError(f"内部知识引擎业务错误（code={code}）：{safe_message}")
    return envelope.get("data")


def list_knowledge_bases(
    config: ProbeConfig,
    keyword: str | None = None,
    *,
    opener: OpenUrl = urlopen,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "page": 1,
        "page_size": 150,
        "strict": "true",
        "filter_system_created": "true",
    }
    if keyword:
        query["keywords"] = keyword

    data = _request_json(config, method="GET", path=LIST_PATH, query=query, opener=opener)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ProbeError("知识库列表响应缺少 data.items")
    return [item for item in data["items"] if isinstance(item, dict)]


def retrieve_chunks(
    config: ProbeConfig,
    kb_id: int,
    question: str,
    *,
    opener: OpenUrl = urlopen,
) -> list[dict[str, Any]]:
    payload = {
        "kb_id": kb_id,
        "question": question,
        "similarity_threshold": 0.1,
        "dense_weight": 0.5,
        "top_k": 10,
        "top_n": 5,
        "enable_filters": True,
        "enable_rerank": True,
        "enable_llm_rerank": False,
    }
    data = _request_json(
        config,
        method="POST",
        path=RETRIEVAL_PATH,
        payload=payload,
        opener=opener,
    )
    if not isinstance(data, dict) or not isinstance(data.get("chunks"), list):
        raise ProbeError("检索响应缺少 data.chunks")
    return [chunk for chunk in data["chunks"] if isinstance(chunk, dict)]


def _single_line(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def print_knowledge_bases(items: Sequence[Mapping[str, Any]]) -> None:
    if not items:
        print("未找到当前身份可访问的知识库。")
        return
    for item in items:
        print(
            f"ID: {_single_line(item.get('id'), limit=60)} | "
            f"名称: {_single_line(item.get('name'), limit=120)} | "
            f"文档数: {_single_line(item.get('doc_num'), limit=30)}"
        )


def print_retrieval_results(chunks: Sequence[Mapping[str, Any]]) -> None:
    if not chunks:
        print("未检索到匹配片段。")
        return
    for index, chunk in enumerate(chunks, start=1):
        if chunk.get("rank_score") is not None:
            score_name = "rank_score"
            score = chunk.get("rank_score")
        else:
            score_name = "similarity"
            score = chunk.get("similarity")
        print(f"[{index}] 文档: {_single_line(chunk.get('docnm_kwd'), limit=160)}")
        print(f"    分数({score_name}): {_single_line(score, limit=40)}")
        print(
            "    片段: "
            + _single_line(chunk.get("content_with_weight"), limit=SNIPPET_LIMIT)
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="内部知识引擎只读连通性探测工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出当前身份可访问的知识库")
    list_parser.add_argument("--keyword", help="按知识库名称或描述筛选")

    retrieve_parser = subparsers.add_parser("retrieve", help="在指定知识库中只读检索")
    retrieve_parser.add_argument("--kb-id", required=True, type=int, help="知识库 ID")
    retrieve_parser.add_argument("--question", required=True, help="检索问题")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = ProbeConfig.from_environment()
        if args.command == "list":
            print_knowledge_bases(list_knowledge_bases(config, args.keyword))
        else:
            print_retrieval_results(retrieve_chunks(config, args.kb_id, args.question))
    except ProbeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())