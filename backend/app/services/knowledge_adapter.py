"""KnowledgeAdapter 统一接口。

search(employee_id, knowledge_base_id, query, trace_id)

实现：
- InternalKnowledgeAdapter：真实内部知识引擎 HTTP 检索（见 internal_knowledge_adapter.py）

禁止：业务模块直接调用本模块；必须经 Plugin Gateway（gateway.search_knowledge）。
"""

from abc import ABC, abstractmethod

from . import config


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


def select_adapter(plugin=None, kb=None) -> KnowledgeAdapter:
    """返回真实内部知识引擎 Adapter（fail-closed：配置不完整即抛错，绝不降级为演示数据）。

    ``plugin`` / ``kb`` 参数保留以兼容旧调用签名，当前实现不再依赖它们。
    """
    if not config.internal_kb_configured():
        raise RuntimeError("内部知识引擎配置不完整，禁止降级为 Mock 或 Stub")
    from .internal_knowledge_adapter import InternalKnowledgeAdapter

    return InternalKnowledgeAdapter()
