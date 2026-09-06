"""LLMProvider 统一接口与 DeepSeek 实现（Sprint 4）。

- 业务代码不得直接调用 DeepSeek API，只能经本模块。
- API Key 只允许来自环境变量（DEEPSEEK_API_KEY），不落库、不落日志、不入 Prompt。
- SAFEMODE：进入 LLM 的每个消息必须带 source=demo（虚构/已允许外发），否则拒发。
"""

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

DEEPSEEK_API_KEY = "DEEPSEEK_API_KEY"
DEEPSEEK_BASE_URL = "DEEPSEEK_BASE_URL"
DEEPSEEK_MODEL = "DEEPSEEK_MODEL"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "v4-flash"  # 演示环境指定模型；可用环境变量覆盖为 deepseek-chat 等


class LLMUnavailableError(Exception):
    """LLM 不可用（Key 缺失 / 网络失败 / SAFEMODE 拒发）。"""

    def __init__(self, message: str, *, code: str = "llm_unavailable") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMProvider(ABC):
    """统一 LLM 接口。"""

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        """普通对话；提供 tools 时模型可返回工具意图。"""

    @abstractmethod
    def tool_call(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        """强制一次工具调用意图（至少一个 tool_call）。"""

    @abstractmethod
    def structured_output(self, messages: list[dict], schema: dict) -> dict:
        """要求 JSON 结构化输出。"""


def _assert_safemode(messages: list[dict]) -> None:
    """SAFEMODE：所有消息必须带 source=demo，否则拒发。"""
    for msg in messages:
        if msg.get("source") != "demo":
            raise LLMUnavailableError("SAFEMODE 拒绝发送：存在非 demo 来源的 prompt 段")


def _response_message(data: dict) -> dict:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailableError(
            "DeepSeek 响应结构无效", code="invalid_response_json",
        ) from exc
    if not isinstance(message, dict):
        raise LLMUnavailableError(
            "DeepSeek 响应结构无效", code="invalid_response_json",
        )
    return message


def _response_tool_calls(message: dict) -> list[ToolCall]:
    raw_tool_calls = message.get("tool_calls")
    if raw_tool_calls is None:
        return []
    if not isinstance(raw_tool_calls, list):
        raise LLMUnavailableError(
            "DeepSeek 工具调用结构无效", code="invalid_response_json",
        )
    tool_calls: list[ToolCall] = []
    for item in raw_tool_calls:
        if not isinstance(item, dict) or not isinstance(item.get("function"), dict):
            raise LLMUnavailableError(
                "DeepSeek 工具调用结构无效", code="invalid_response_json",
            )
        tool_call_id = item.get("id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise LLMUnavailableError(
                "DeepSeek 工具调用结构无效", code="invalid_response_json",
            )
        function = item["function"]
        name = function.get("name")
        raw_arguments = function.get("arguments")
        if not isinstance(name, str) or not name or not isinstance(raw_arguments, str) or not raw_arguments:
            raise LLMUnavailableError(
                "DeepSeek 工具调用结构无效", code="invalid_response_json",
            )
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise LLMUnavailableError(
                "DeepSeek 工具调用结构无效", code="invalid_response_json",
            ) from exc
        if not isinstance(arguments, dict):
            raise LLMUnavailableError(
                "DeepSeek 工具调用结构无效", code="invalid_response_json",
            )
        tool_calls.append(ToolCall(id=tool_call_id, name=name, arguments=arguments))
    return tool_calls


def _http_failure_code(status_code: int) -> str:
    if status_code == 429:
        return "rate_limited"
    if 400 <= status_code < 500:
        return "client_error"
    if 500 <= status_code < 600:
        return "server_error"
    return "http_error"


class DeepSeekProvider(LLMProvider):
    """DeepSeek（OpenAI 兼容协议）实现；Key 仅从环境变量读取。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or os.environ.get(DEEPSEEK_API_KEY)
        self.base_url = (base_url or os.environ.get(DEEPSEEK_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get(DEEPSEEK_MODEL) or DEFAULT_MODEL

    def _post(self, payload: dict) -> dict:
        if not self.api_key:
            raise LLMUnavailableError("DEEPSEEK_API_KEY 未配置（环境变量）")
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=60.0,
            )
        except httpx.HTTPError as exc:
            raise LLMUnavailableError("DeepSeek 网络请求失败", code="network_error") from exc
        if resp.status_code != 200:
            # 错误详情不落日志、不包含 Key
            raise LLMUnavailableError(
                "DeepSeek HTTP 请求失败", code=_http_failure_code(resp.status_code),
            )
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMUnavailableError(
                "DeepSeek 响应不是有效 JSON", code="invalid_response_json",
            ) from exc

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        _assert_safemode(messages)
        payload: dict = {
            "model": self.model,
            "messages": [{k: v for k, v in m.items() if k != "source"} for m in messages],
            "temperature": 0.3,
        }
        if tools:
            payload["tools"] = tools
        data = self._post(payload)
        msg = _response_message(data)
        raw_content = msg.get("content")
        if raw_content is None:
            content = ""
        elif isinstance(raw_content, str):
            content = raw_content
        else:
            raise LLMUnavailableError(
                "DeepSeek 响应内容结构无效", code="invalid_response_json",
            )
        return LLMResponse(content=content, tool_calls=_response_tool_calls(msg))

    def tool_call(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        resp = self.chat(messages, tools=tools)
        if not resp.tool_calls:
            raise LLMUnavailableError("模型未返回工具调用意图")
        return resp

    def structured_output(self, messages: list[dict], schema: dict) -> dict:
        _assert_safemode(messages)
        payload: dict = {
            "model": self.model,
            "messages": [{k: v for k, v in m.items() if k != "source"} for m in messages],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        data = self._post(payload)
        content = _response_message(data).get("content")
        if not isinstance(content, str) or not content:
            raise LLMUnavailableError(
                "模型未返回结构化输出", code="invalid_structured_json",
            )
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise LLMUnavailableError(
                "模型返回非 JSON 结构化输出", code="invalid_structured_json",
            ) from exc
