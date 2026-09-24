"""知识源注册表：平台支持的知识通道、环境归属与选路偏好。

两条通道并存：
- ``rest``：内部知识引擎 REST（测试环境，``x-tenant=8``）；
- ``verag``：内部知识引擎 VeRAG MCP（生产环境，``X-Tenant=6``）。

平台知识库行上的 ``engine_map`` 形如 ``{"rest": 751, "verag": 623}``，
本模块只负责「源是什么、属于哪个环境、按什么顺序选」，不碰数据库与网络。
"""

from __future__ import annotations

from . import config

SOURCE_REST = "rest"
SOURCE_VERAG = "verag"

ENV_TEST = "test"
ENV_PROD = "prod"
ENV_UNKNOWN = "unknown"

# 源元数据：label 用于管理端与前端展示，environment 是该源对应的部署环境。
SOURCE_META: dict[str, dict[str, str]] = {
    SOURCE_REST: {
        "label": "内部知识引擎（测试 REST）",
        "short_label": "测试 REST",
        "environment": ENV_TEST,
        "kind": "rest",
    },
    SOURCE_VERAG: {
        "label": "内部知识引擎（生产 VeRAG MCP）",
        "short_label": "生产 VeRAG",
        "environment": ENV_PROD,
        "kind": "mcp",
    },
}

ENVIRONMENT_LABEL: dict[str, str] = {
    ENV_TEST: "测试环境",
    ENV_PROD: "生产环境",
    ENV_UNKNOWN: "未识别",
}

# 环境偏好 → 选路顺序。auto 与 prod 都是「生产优先」，区别只在人工显式指定时的语义。
_PREFERENCE_ORDER: dict[str, tuple[str, ...]] = {
    "auto": (SOURCE_VERAG, SOURCE_REST),
    "prod": (SOURCE_VERAG, SOURCE_REST),
    "test": (SOURCE_REST, SOURCE_VERAG),
}


def known_sources() -> tuple[str, ...]:
    return (SOURCE_REST, SOURCE_VERAG)


def source_label(source: str) -> str:
    meta = SOURCE_META.get(source)
    return meta["label"] if meta else source


def source_short_label(source: str) -> str:
    meta = SOURCE_META.get(source)
    return meta["short_label"] if meta else source


def source_environment(source: str) -> str:
    meta = SOURCE_META.get(source)
    return meta["environment"] if meta else ENV_UNKNOWN


def environment_label(environment: str) -> str:
    return ENVIRONMENT_LABEL.get(environment, environment)


def preferred_source_order() -> tuple[str, ...]:
    """按环境偏好返回源优先顺序。"""
    return _PREFERENCE_ORDER.get(config.knowledge_source_env_preference(), (SOURCE_VERAG, SOURCE_REST))


def source_configured(source: str) -> bool:
    """该源的地址与凭据是否配置齐备。"""
    if source == SOURCE_REST:
        return config.internal_kb_endpoint_configured()
    if source == SOURCE_VERAG:
        return config.verag_mcp_configured()
    return False


def engine_map(kb) -> dict[str, int]:
    """读取知识库行的双后端映射；老部署回退到 ``DWP_INTERNAL_KB_ID_MAP``。

    ``kb`` 可以是 ORM 行，也可以是带 ``id`` / ``engine_map`` 的任意对象。
    """
    raw = getattr(kb, "engine_map", None)
    mapping: dict[str, int] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                mapping[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
    if SOURCE_REST not in mapping:
        legacy = config.internal_kb_id_map().get(str(getattr(kb, "id", "") or ""))
        if legacy is not None:
            mapping[SOURCE_REST] = int(legacy)
    return {key: value for key, value in mapping.items() if key in SOURCE_META}


def engine_id(kb, source: str) -> int | None:
    return engine_map(kb).get(source)


def sources_for_kb(kb) -> list[str]:
    """该知识库登记了哪些后端（不含可达性判断）。"""
    return [source for source in preferred_source_order() if engine_id(kb, source) is not None]
