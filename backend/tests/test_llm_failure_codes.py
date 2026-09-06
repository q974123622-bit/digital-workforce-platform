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


def test_deepseek_network_failure_has_safe_machine_code(monkeypatch):
    request = httpx.Request("POST", "https://example.invalid/chat/completions")

    def fail_network(*_args, **_kwargs):
        raise httpx.ConnectError("secret provider detail", request=request)

    monkeypatch.setattr(llm.httpx, "post", fail_network)

    with pytest.raises(LLMUnavailableError) as caught:
        provider().structured_output(DEMO_MESSAGES, {"type": "object"})

    assert caught.value.code == "network_error"
    assert "secret provider detail" not in str(caught.value)


def test_deepseek_http_failure_has_safe_machine_code(monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", lambda *_args, **_kwargs: StubResponse(status_code=429))

    with pytest.raises(LLMUnavailableError) as caught:
        provider().structured_output(DEMO_MESSAGES, {"type": "object"})

    assert caught.value.code == "http_error"


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
