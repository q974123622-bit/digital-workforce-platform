from __future__ import annotations

import json

import httpx
import pytest

from app.services import llm
from app.services.llm import DeepSeekProvider, LLMUnavailableError


DEMO_MESSAGES = [{"role": "user", "content": "demo", "source": "demo"}]


class StubResponse:
    def __init__(self, *, status_code: int = 200, data=None, json_error: Exception | None = None):
        self.status_code = status_code
        self._data = data
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._data


def provider() -> DeepSeekProvider:
    return DeepSeekProvider(api_key="test-key", base_url="https://example.invalid", model="test")


def test_deepseek_structured_output_adds_json_instruction_to_outbound_messages(monkeypatch):
    captured_payload = {}

    def capture_post(*_args, **kwargs):
        captured_payload.update(kwargs["json"])
        return StubResponse(data={"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(llm.httpx, "post", capture_post)

    assert provider().structured_output(DEMO_MESSAGES, {"type": "object"}) == {}
    assert captured_payload["response_format"] == {"type": "json_object"}
    assert any(
        "json" in message.get("content", "")
        for message in captured_payload["messages"]
    )
    assert captured_payload["messages"][0] == {"role": "user", "content": "demo"}


def test_deepseek_network_failure_has_safe_machine_code(monkeypatch):
    request = httpx.Request("POST", "https://example.invalid/chat/completions")

    def fail_network(*_args, **_kwargs):
        raise httpx.ConnectError("secret provider detail", request=request)

    monkeypatch.setattr(llm.httpx, "post", fail_network)

    with pytest.raises(LLMUnavailableError) as caught:
        provider().structured_output(DEMO_MESSAGES, {"type": "object"})

    assert caught.value.code == "network_error"
    assert "secret provider detail" not in str(caught.value)


@pytest.mark.parametrize(
    "status_code,expected_code",
    [
        pytest.param(429, "rate_limited", id="rate-limited"),
        pytest.param(400, "client_error", id="bad-request"),
        pytest.param(401, "client_error", id="unauthorized"),
        pytest.param(500, "server_error", id="internal-server-error"),
        pytest.param(503, "server_error", id="service-unavailable"),
        pytest.param(302, "http_error", id="other-non-success"),
    ],
)
def test_deepseek_http_failure_has_safe_machine_code(monkeypatch, status_code, expected_code):
    response = StubResponse(status_code=status_code)
    response.text = "token=response-body-secret"
    response.headers = {"authorization": "Bearer header-secret"}
    monkeypatch.setattr(llm.httpx, "post", lambda *_args, **_kwargs: response)

    with pytest.raises(LLMUnavailableError) as caught:
        provider().structured_output(DEMO_MESSAGES, {"type": "object"})

    assert caught.value.code == expected_code
    assert "response-body-secret" not in str(caught.value)
    assert "header-secret" not in str(caught.value)


def test_deepseek_invalid_response_json_has_safe_machine_code(monkeypatch):
    raw_provider_body = "token=provider-secret"
    parse_error = json.JSONDecodeError("bad response", raw_provider_body, 0)
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *_args, **_kwargs: StubResponse(json_error=parse_error),
    )

    with pytest.raises(LLMUnavailableError) as caught:
        provider().structured_output(DEMO_MESSAGES, {"type": "object"})

    assert caught.value.code == "invalid_response_json"
    assert raw_provider_body not in str(caught.value)


def test_deepseek_invalid_structured_json_has_safe_machine_code(monkeypatch):
    raw_model_content = "api_key=model-secret"
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *_args, **_kwargs: StubResponse(
            data={"choices": [{"message": {"content": raw_model_content}}]},
        ),
    )

    with pytest.raises(LLMUnavailableError) as caught:
        provider().structured_output(DEMO_MESSAGES, {"type": "object"})

    assert caught.value.code == "invalid_structured_json"
    assert raw_model_content not in str(caught.value)


def test_deepseek_chat_invalid_response_shape_has_safe_machine_code(monkeypatch):
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *_args, **_kwargs: StubResponse(data={"unexpected": "private provider shape"}),
    )

    with pytest.raises(LLMUnavailableError) as caught:
        provider().chat(DEMO_MESSAGES)

    assert caught.value.code == "invalid_response_json"
    assert "private provider shape" not in str(caught.value)


@pytest.mark.parametrize(
    "raw_message",
    [
        pytest.param({}, id="missing"),
        pytest.param({"content": None}, id="null"),
        pytest.param({"content": ""}, id="empty"),
    ],
)
def test_deepseek_structured_output_rejects_missing_content(monkeypatch, raw_message):
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *_args, **_kwargs: StubResponse(data={"choices": [{"message": raw_message}]}),
    )

    with pytest.raises(LLMUnavailableError) as caught:
        provider().structured_output(DEMO_MESSAGES, {"type": "object"})

    assert caught.value.code == "invalid_structured_json"


@pytest.mark.parametrize(
    "tool_calls",
    [
        pytest.param({"private": "not-a-list"}, id="tool-calls-not-list"),
        pytest.param([None], id="item-not-object"),
        pytest.param([{}], id="function-missing"),
        pytest.param([{"function": None}], id="function-not-object"),
        pytest.param([{"function": {"arguments": "{}"}}], id="name-missing"),
        pytest.param([{"function": {"name": "", "arguments": "{}"}}], id="name-empty"),
        pytest.param([{"function": {"name": "safe"}}], id="arguments-missing"),
        pytest.param([{"function": {"name": "safe", "arguments": ""}}], id="arguments-empty"),
        pytest.param([{"function": {"name": "safe", "arguments": {}}}], id="arguments-not-string"),
        pytest.param([{"function": {"name": "safe", "arguments": "not-json token=private"}}], id="arguments-invalid-json"),
        pytest.param([{"function": {"name": "safe", "arguments": "[]"}}], id="arguments-not-object-json"),
    ],
)
def test_deepseek_chat_rejects_malformed_tool_calls_with_safe_code(monkeypatch, tool_calls):
    raw_provider_data = {"choices": [{"message": {"content": "", "tool_calls": tool_calls}}]}
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *_args, **_kwargs: StubResponse(data=raw_provider_data),
    )

    with pytest.raises(LLMUnavailableError) as caught:
        provider().chat(DEMO_MESSAGES)

    assert caught.value.code == "invalid_response_json"
    assert "token=private" not in str(caught.value)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param({"private": "object"}, id="object"),
        pytest.param(["private", "array"], id="array"),
        pytest.param(7, id="number"),
    ],
)
def test_deepseek_chat_rejects_non_string_content_with_safe_code(monkeypatch, content):
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *_args, **_kwargs: StubResponse(data={"choices": [{"message": {"content": content}}]}),
    )

    with pytest.raises(LLMUnavailableError) as caught:
        provider().chat(DEMO_MESSAGES)

    assert caught.value.code == "invalid_response_json"
    assert "private" not in str(caught.value)


def test_deepseek_chat_allows_null_content(monkeypatch):
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *_args, **_kwargs: StubResponse(data={"choices": [{"message": {"content": None}}]}),
    )

    assert provider().chat(DEMO_MESSAGES).content == ""


@pytest.mark.parametrize(
    "id_present,tool_call_id",
    [
        pytest.param(False, None, id="missing"),
        pytest.param(True, None, id="null"),
        pytest.param(True, "", id="empty"),
        pytest.param(True, {}, id="object"),
        pytest.param(True, [], id="array"),
        pytest.param(True, 7, id="number"),
    ],
)
def test_deepseek_chat_rejects_invalid_tool_call_id_with_safe_code(monkeypatch, id_present, tool_call_id):
    tool_call = {"function": {"name": "safe", "arguments": "{}"}}
    if id_present:
        tool_call["id"] = tool_call_id
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda *_args, **_kwargs: StubResponse(
            data={"choices": [{"message": {"content": "", "tool_calls": [tool_call]}}]},
        ),
    )

    with pytest.raises(LLMUnavailableError) as caught:
        provider().chat(DEMO_MESSAGES)

    assert caught.value.code == "invalid_response_json"
