"""KnowledgeAdapter 统一接口（Sprint 3）。

search(employee_id, knowledge_base_id, query, trace_id)

实现：
- MockKnowledgeAdapter：读取 mock-data/kb/ 虚构文档返回片段
- InternalKnowledgeAdapterStub：只保留接口与配置结构，不接入任何真实内容

禁止：业务模块直接调用本模块；必须经 Plugin Gateway（gateway.search_knowledge）。
"""

import re
from abc import ABC, abstractmethod
from pathlib import Path

from .. import models
from . import config

REPO_ROOT = Path(__file__).resolve().parents[3]


class KnowledgeAdapter(ABC):
    """统一知识库访问接口。"""

    @abstractmethod
    def search(
        self,
        *,
        employee_id: str,
        knowledge_base_id: str,
        query: str,
        trace_id: str,
    ) -> dict:
        """返回统一结构：{source, knowledge_base_id, hits: [...]}。"""


class MockKnowledgeAdapter(KnowledgeAdapter):
    """从 mock-data/kb/ 虚构文档返回片段；所有内容均为虚构。"""

    def __init__(self, kb: models.KnowledgeBase | None = None):
        self._kb = kb

    def search(
        self,
        *,
        employee_id: str,
        knowledge_base_id: str,
        query: str,
        trace_id: str,
    ) -> dict:
        kb = self._kb
        hits: list[dict] = []
        if kb and kb.doc_path:
            path = REPO_ROOT / kb.doc_path
            if path.exists():
                text = path.read_text(encoding="utf-8")
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        hits.append({"title": stripped.lstrip("# ").strip(), "snippet": ""})
                    elif stripped and len(stripped) > 6:
                        if hits:
                            hits[-1]["snippet"] = stripped[:80]
                        elif len(hits) < 10:
                            hits.append({"title": kb.name, "snippet": stripped[:80]})
                    if len(hits) >= 6:
                        break
        return {
            "source": "demo",
            "knowledge_base_id": knowledge_base_id,
            "query": query,
            "hits": hits or [{"title": kb.name if kb else knowledge_base_id, "snippet": "（虚构文档暂无内容）"}],
        }


class InternalKnowledgeAdapterStub(KnowledgeAdapter):
    """内部知识库 Adapter 占位：只保留接口与配置结构。

    配置引用（环境变量，正式员工受控环境设置）：
    - DWP_INTERNAL_KB_ENDPOINT
    - DWP_INTERNAL_KB_CREDENTIAL_REF
    本阶段不接入真实内容；调用返回 stub 状态，绝不落真实数据。
    """

    def __init__(self, endpoint_ref: str | None = None, credential_ref: str | None = None):
        self.endpoint_ref = endpoint_ref or config.get(config.INTERNAL_KB_ENDPOINT)
        self.credential_ref = credential_ref or config.credential_ref(config.INTERNAL_KB_CREDENTIAL_REF)

    def search(
        self,
        *,
        employee_id: str,
        knowledge_base_id: str,
        query: str,
        trace_id: str,
    ) -> dict:
        return {
            "source": "stub",
            "knowledge_base_id": knowledge_base_id,
            "status": "stub",
            "configured": bool(self.endpoint_ref and self.credential_ref),
            "message": "InternalKnowledgeAdapterStub：未接入真实知识库（仅接口与配置结构）",
        }


def select_adapter(plugin: models.Plugin, kb: models.KnowledgeBase | None) -> KnowledgeAdapter:
    """按插件/资源类型选择 Adapter；internal:// 或 resource_type=internal 走 Stub。"""
    if plugin.endpoint_ref.startswith("internal://") or (kb and kb.resource_type == "internal"):
        return InternalKnowledgeAdapterStub()
    return MockKnowledgeAdapter(kb=kb)
