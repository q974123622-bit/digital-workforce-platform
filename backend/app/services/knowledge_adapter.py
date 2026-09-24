"""KnowledgeAdapter 统一接口 + 双通道选路。

search(employee_id, knowledge_base_id, query, trace_id)

实现：
- InternalKnowledgeAdapter：内部知识引擎 REST 检索（测试环境，见 internal_knowledge_adapter.py）
- VeragMcpKnowledgeAdapter：内部知识引擎 VeRAG MCP 检索（生产环境，见 verag_mcp_knowledge_adapter.py）

选路（``select_adapter``）：平台知识库行上的 ``engine_map`` 同时登记两个远端库 ID，
按「当前环境 + 各源可达性」自动选择通道 —— 优先生产 VeRAG，只通测试就回落 REST，
两边都不可达才抛 ``KnowledgeSourceUnavailable``（业务可读提示，不是裸 502）。

禁止：业务模块直接调用本模块；必须经 Plugin Gateway（gateway.search_knowledge）。
"""

from abc import ABC, abstractmethod

from . import config


class KnowledgeSourceUnavailable(RuntimeError):
    """该知识库的所有后端当前都不可达（可读提示，不泄露内部细节）。"""


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


def select_adapter(plugin=None, kb=None, db=None) -> KnowledgeAdapter:
    """按知识库的双后端映射 + 当前环境可达性选路。

    - ``kb`` 为空（旧调用签名）时保持既有 fail-closed 语义：配置不完整即抛错；
    - ``kb`` 有 ``engine_map`` 时，按「优先生产 VeRAG → 只通测试用 REST」选路；
    - 所有已登记后端都不可达才抛 ``KnowledgeSourceUnavailable``。

    ``plugin`` 参数仅为兼容旧调用签名保留。
    """
    if kb is None:
        if not config.internal_kb_configured():
            raise RuntimeError("内部知识引擎配置不完整，禁止降级为 Mock 或 Stub")
        from .internal_knowledge_adapter import InternalKnowledgeAdapter

        return InternalKnowledgeAdapter()

    from . import knowledge_source_registry as registry

    engine_map = registry.engine_map(kb)
    if not engine_map:
        # 老部署：远端 ID 仍在 DWP_INTERNAL_KB_ID_MAP 里，只有 REST 一条通道。
        if not config.internal_kb_configured():
            raise RuntimeError("内部知识引擎配置不完整，禁止降级为 Mock 或 Stub")
        from .internal_knowledge_adapter import InternalKnowledgeAdapter

        return InternalKnowledgeAdapter()

    available = _available_sources(db)
    for source in registry.preferred_source_order():
        remote_id = engine_map.get(source)
        if remote_id is None or source not in available:
            continue
        return _build_adapter(source, kb, remote_id)
    raise KnowledgeSourceUnavailable(
        "该知识库对应的内部知识源当前不可用，请稍后重试；"
        "若持续不可用，请联系管理员检查知识源连通性。"
    )


def _available_sources(db) -> set[str]:
    """可达的源集合；没有数据库会话时退化为「已配置即可用」。"""
    from . import knowledge_source_registry as registry

    if db is None:
        return {source for source in registry.known_sources() if registry.source_configured(source)}
    from . import knowledge_source_health

    return knowledge_source_health.available_sources(db)


def _build_adapter(source: str, kb, remote_id: int) -> KnowledgeAdapter:
    from . import knowledge_source_registry as registry

    if source == registry.SOURCE_REST:
        from .internal_knowledge_adapter import InternalKnowledgeAdapter

        return InternalKnowledgeAdapter(kb_id_map={kb.id: remote_id})
    if source == registry.SOURCE_VERAG:
        from .verag_mcp_knowledge_adapter import VeragMcpKnowledgeAdapter

        return VeragMcpKnowledgeAdapter(kb_id_map={kb.id: remote_id})
    raise RuntimeError(f"未知知识源：{source}")
