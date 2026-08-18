"""Qwen Embedding 客户端（RAG 索引/检索用，OpenAI 兼容接口）。

调用：POST {base_url}/embeddings
- model: qwen3.7-text-embedding（默认，DWP_EMBED_MODEL 可覆盖）
- dimensions: 1024（默认，DWP_EMBED_DIMENSIONS）
- encoding_format: float
- input 支持批量（list[str]），响应按 data[i].embedding 取值

凭据只从环境变量读取（DWP_EMBED_API_KEY，兼容 DASHSCOPE_API_KEY），
绝不入 Git/Prompt/日志；超时/网络/响应异常统一映射 EmbeddingUnavailableError。
"""

import hashlib
import math
from typing import Iterable

import httpx

from . import config

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-text-embedding"
DEFAULT_DIMENSIONS = 1024


class EmbeddingUnavailableError(RuntimeError):
    """嵌入服务不可用（缺 Key / 超时 / 网络失败 / 响应异常）。"""


class QwenEmbeddingClient:
    """OpenAI 兼容的 Qwen Embedding 客户端；批量输入，统一错误映射。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        timeout: float = 10.0,
    ):
        self.base_url = (base_url or config.get(config.EMBED_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else config.embedding_api_key()
        self.model = model or config.get(config.EMBED_MODEL) or DEFAULT_MODEL
        self.dimensions = dimensions if dimensions is not None else config.embedding_dimensions()
        self.timeout = timeout

    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        """批量嵌入；返回与输入顺序一致的向量列表。"""
        items = list(texts)
        if not items:
            return []
        if not self.api_key:
            raise EmbeddingUnavailableError("未配置嵌入 API Key（DWP_EMBED_API_KEY / DASHSCOPE_API_KEY）")
        payload = {
            "model": self.model,
            "input": items,
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            resp = httpx.post(
                f"{self.base_url}/embeddings",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json().get("data") or []
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            return [item["embedding"] for item in ordered]
        except EmbeddingUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingUnavailableError(f"嵌入服务调用失败（{type(exc).__name__}）") from exc


def create_embedder() -> QwenEmbeddingClient:
    """按配置构建嵌入客户端（select_adapter / kb_index 使用；测试可替换该工厂）。"""
    return QwenEmbeddingClient()


def local_demo_embedding(text: str, dims: int = DEFAULT_DIMENSIONS) -> list[float]:
    """无 Key 时的本地确定性演示向量（字符 1-2 gram 特征哈希 + L2 归一化）。

    仅用于离线索引构建演示；接入真实 Key 后请重新执行 `python -m app.kb_index --rebuild`
    以重建真实向量索引。
    """
    vec = [0.0] * dims
    t = text.lower()
    grams = set(t)
    grams.update(t[i : i + 2] for i in range(len(t) - 1))
    for gram in grams:
        digest = hashlib.md5(gram.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "little") % dims
        sign = 1.0 if (digest[4] & 1) else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]
