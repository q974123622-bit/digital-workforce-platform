"""LLMProvider 统一接口与 DeepSeek 实现（Sprint 4）。

- 业务代码不得直接调用 DeepSeek API，只能经本模块。
- API Key 只允许来自环境变量（DEEPSEEK_API_KEY），不落库、不落日志、不入 Prompt。
- SAFEMODE：进入 LLM 的每个消息必须带 source=demo（虚构/已允许外发），否则拒发。
"""

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .prompt_shared import SafeModeViolation, assert_safemode

DEEPSEEK_API_KEY = "DEEPSEEK_API_KEY"
DEEPSEEK_BASE_URL = "DEEPSEEK_BASE_URL"
DEEPSEEK_MODEL = "DEEPSEEK_MODEL"
DEEPSEEK_CHAT_COMPLETIONS_PATH = "DEEPSEEK_CHAT_COMPLETIONS_PATH"
# 运维 CLI 用的密钥文件兜底（容器 entrypoint 会把它的值 export 成 DEEPSEEK_API_KEY，
# 但 `docker exec ... python -m tools.x` 起的新进程拿不到那份 export）。
DEEPSEEK_API_KEY_FILE = "DEEPSEEK_API_KEY_FILE"
DEFAULT_API_KEY_FILE = "/run/secrets/llm_api_key"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "v4-flash"  # 演示环境指定模型；可用环境变量覆盖为 deepseek-chat 等


def ensure_api_key(*, allow_secret_file: bool = False) -> str:
    """确保 ``DEEPSEEK_API_KEY`` 可用，返回来源：``env`` / ``secret_file`` / ``missing``。

    环境变量永远优先。``allow_secret_file=True`` 只给运维 CLI 用：它们以 ``docker exec``
    起独立进程，拿不到容器 entrypoint 注入的环境变量，而密钥文件本来就以只读方式挂在
    容器里。**只读取、不回显、不落盘**，也仍然不会进入 Prompt 或日志。
    """
    if os.environ.get(DEEPSEEK_API_KEY):
        return "env"
    if not allow_secret_file:
        return "missing"
    path = Path(os.environ.get(DEEPSEEK_API_KEY_FILE) or DEFAULT_API_KEY_FILE)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "missing"
    if not value:
        return "missing"
    os.environ[DEEPSEEK_API_KEY] = value
    return "secret_file"


class LLMUnavailableError(Exception):
    """LLM 不可用（Key 缺失 / 网络失败 / SAFEMODE 拒发）。"""


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
    """SAFEMODE：所有消息必须带 source=demo，否则拒发。

    规则本身在 `prompt_shared`（与 Harness 主链路共用），这里只做异常类型适配，
    保证调用方仍按 LLMUnavailableError 处理。
    """
    try:
        assert_safemode(messages)
    except SafeModeViolation as exc:
        raise LLMUnavailableError(str(exc)) from exc


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
        raw_path = (os.environ.get(DEEPSEEK_CHAT_COMPLETIONS_PATH) or "/chat/completions").strip()
        self.chat_completions_path = raw_path if raw_path.startswith("/") and "://" not in raw_path else "/chat/completions"

    def _post(self, payload: dict) -> dict:
        if not self.api_key:
            raise LLMUnavailableError("DEEPSEEK_API_KEY 未配置（环境变量）")
        try:
            resp = httpx.post(
                f"{self.base_url}{self.chat_completions_path}",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=60.0,
            )
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"DeepSeek 请求失败：{exc.__class__.__name__}") from exc
        if resp.status_code != 200:
            # 错误详情不落日志、不包含 Key
            raise LLMUnavailableError(f"DeepSeek 返回错误：HTTP {resp.status_code}")
        return resp.json()

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
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            raw = tc["function"].get("arguments") or "{}"
            try:
                arguments = json.loads(raw)
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(ToolCall(id=tc.get("id", ""), name=tc["function"]["name"], arguments=arguments))
        return LLMResponse(content=content, tool_calls=tool_calls)

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
        content = data["choices"][0]["message"].get("content") or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMUnavailableError("模型返回非 JSON 结构化输出") from exc
