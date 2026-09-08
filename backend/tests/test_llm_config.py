from app.services.llm import DeepSeekProvider


class _Response:
    status_code = 200

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": "ok"}}]}


def test_model_gateway_address_model_and_path_are_configurable(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs["json"]
        return _Response()

    monkeypatch.setenv("DEEPSEEK_CHAT_COMPLETIONS_PATH", "/compatible/chat")
    monkeypatch.setattr("app.services.llm.httpx.post", fake_post)
    provider = DeepSeekProvider(
        api_key="test-only-key",
        base_url="http://gateway.invalid:19000/v1/",
        model="enabled-model",
    )

    response = provider.chat([{"role": "user", "content": "hello", "source": "demo"}])

    assert response.content == "ok"
    assert captured["url"] == "http://gateway.invalid:19000/v1/compatible/chat"
    assert captured["payload"]["model"] == "enabled-model"


def test_invalid_absolute_chat_path_falls_back_to_safe_relative_path(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_CHAT_COMPLETIONS_PATH", "https://untrusted.invalid/path")
    provider = DeepSeekProvider(api_key="test-only-key", base_url="http://gateway.invalid/v1")
    assert provider.chat_completions_path == "/chat/completions"
