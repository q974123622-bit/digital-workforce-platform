"""知识源可达性探测与环境判定测试。

覆盖：REST/VeRAG 探测成功与失败、环境优先级（优先生产 VeRAG → 只通测试用 REST →
都不可达）、状态落库与缓存失效、探测失败不抛异常。
"""

import pytest

from app.services import config
from app.services import knowledge_source_health as health
from app.services import knowledge_source_probe as probe
from app.services import knowledge_source_registry as registry


def _configure(monkeypatch, sources=("rest", "verag")):
    """把「已配置」判定收敛到测试可控的集合。"""
    monkeypatch.setattr(registry, "source_configured", lambda source: source in sources)


def _enable_endpoints(monkeypatch):
    """让探测实现层认为两条通道的地址与凭据都齐备（不依赖真实环境变量）。"""
    monkeypatch.setattr(config, "internal_kb_endpoint_configured", lambda: True)
    monkeypatch.setattr(config, "verag_mcp_configured", lambda: True)


def _mark(db, source: str, state: str) -> None:
    health.upsert_health(db, {
        "source": source, "state": state, "latency_ms": 12,
        "last_error": "" if state == "available" else "连接超时", "detail": {},
    })


# ---- 探测 ---------------------------------------------------------------


def test_probe_rest_available(monkeypatch):
    _configure(monkeypatch)
    _enable_endpoints(monkeypatch)
    monkeypatch.setattr(
        "app.services.internal_knowledge_adapter.InternalKnowledgeAdapter.probe",
        lambda self, timeout_seconds=None: {"visible_knowledge_bases": 99},
    )

    result = probe.probe_source(registry.SOURCE_REST)

    assert result["state"] == probe.STATE_AVAILABLE
    assert result["detail"] == {"visible_knowledge_bases": 99}
    assert result["last_error"] == ""
    assert result["latency_ms"] >= 0


def test_probe_rest_failure_is_swallowed(monkeypatch):
    _configure(monkeypatch)
    _enable_endpoints(monkeypatch)

    def boom(self, timeout_seconds=None):
        raise RuntimeError("无法连接内部知识引擎")

    monkeypatch.setattr("app.services.internal_knowledge_adapter.InternalKnowledgeAdapter.probe", boom)

    result = probe.probe_source(registry.SOURCE_REST)

    # 探测失败不抛异常，转成不可用状态与可读原因
    assert result["state"] == probe.STATE_UNAVAILABLE
    assert "无法连接内部知识引擎" in result["last_error"]


def test_probe_rest_unconfigured_marks_unavailable(monkeypatch):
    _configure(monkeypatch)

    result = probe.probe_source(registry.SOURCE_REST)

    assert result["state"] == probe.STATE_UNAVAILABLE
    assert "未配置" in result["last_error"]


def test_probe_verag_reports_server_and_visible_bases(monkeypatch):
    _configure(monkeypatch)
    _enable_endpoints(monkeypatch)
    monkeypatch.setattr(
        "app.services.verag_mcp_knowledge_adapter.VeragMcpKnowledgeAdapter.describe",
        lambda self: {"server_info": {"name": "VeRAG MCP"}, "tools_count": 5, "protocol_version": "2024-11-05"},
    )
    monkeypatch.setattr(
        "app.services.verag_mcp_knowledge_adapter.VeragMcpKnowledgeAdapter.list_knowledge_bases",
        lambda self, page=1, page_size=50: {"total": 17, "items": []},
    )

    result = probe.probe_source(registry.SOURCE_VERAG)

    assert result["state"] == probe.STATE_AVAILABLE
    assert result["detail"]["server_info"] == {"name": "VeRAG MCP"}
    assert result["detail"]["visible_knowledge_bases"] == 17


def test_probe_verag_list_failure_still_reachable(monkeypatch):
    """清单工具失败不影响「通道可达」判定（握手成功即视为可达）。"""
    _configure(monkeypatch)
    _enable_endpoints(monkeypatch)
    monkeypatch.setattr(
        "app.services.verag_mcp_knowledge_adapter.VeragMcpKnowledgeAdapter.describe",
        lambda self: {"tools_count": 5},
    )

    def boom(self, page=1, page_size=50):
        raise RuntimeError("清单工具不可用")

    monkeypatch.setattr("app.services.verag_mcp_knowledge_adapter.VeragMcpKnowledgeAdapter.list_knowledge_bases", boom)

    result = probe.probe_source(registry.SOURCE_VERAG)

    assert result["state"] == probe.STATE_AVAILABLE
    assert "visible_knowledge_bases" not in result["detail"]


def test_probe_unknown_source_marks_unavailable():
    result = probe.probe_source("rest-v2")

    assert result["state"] == probe.STATE_UNAVAILABLE
    assert "未知知识源" in result["last_error"]


# ---- 状态落库与缓存 ------------------------------------------------------


def test_health_upsert_is_idempotent_and_invalidates_cache(db_session, monkeypatch):
    _configure(monkeypatch)

    _mark(db_session, registry.SOURCE_VERAG, "available")
    assert health.source_state(db_session, registry.SOURCE_VERAG) == "available"

    _mark(db_session, registry.SOURCE_VERAG, "unavailable")
    # 写入后缓存必须立即失效，不能读到上一轮的旧状态
    assert health.source_state(db_session, registry.SOURCE_VERAG) == "unavailable"
    assert health.get_health(db_session, registry.SOURCE_VERAG).last_error == "连接超时"
    assert len(health.list_health(db_session)) == 1


def test_unconfigured_source_is_unavailable(db_session, monkeypatch):
    _configure(monkeypatch, sources=("rest",))

    assert health.source_state(db_session, registry.SOURCE_VERAG) == "unavailable"
    assert health.source_available(db_session, registry.SOURCE_VERAG) is False


# ---- 环境判定 -----------------------------------------------------------


def test_environment_prefers_production_verag(db_session, monkeypatch):
    _configure(monkeypatch)
    _mark(db_session, registry.SOURCE_REST, "available")
    _mark(db_session, registry.SOURCE_VERAG, "available")

    assert health.resolve_environment(db_session) == registry.ENV_PROD


def test_environment_falls_back_to_test_when_production_unreachable(db_session, monkeypatch):
    _configure(monkeypatch)
    _mark(db_session, registry.SOURCE_REST, "available")
    _mark(db_session, registry.SOURCE_VERAG, "unavailable")

    assert health.resolve_environment(db_session) == registry.ENV_TEST


def test_environment_unknown_when_all_unreachable(db_session, monkeypatch):
    _configure(monkeypatch)
    _mark(db_session, registry.SOURCE_REST, "unavailable")
    _mark(db_session, registry.SOURCE_VERAG, "unavailable")

    assert health.resolve_environment(db_session) == registry.ENV_UNKNOWN


def test_explicit_test_preference_wins(db_session, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("DWP_KB_SOURCE_ENV", "test")
    _mark(db_session, registry.SOURCE_REST, "available")
    _mark(db_session, registry.SOURCE_VERAG, "available")

    assert registry.preferred_source_order()[0] == registry.SOURCE_REST
    assert health.resolve_environment(db_session) == registry.ENV_TEST


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("auto", "auto"), ("PROD", "prod"), ("test", "test"), ("nonsense", "auto"), ("", "auto")],
)
def test_env_preference_normalization(monkeypatch, raw, expected):
    monkeypatch.setenv("DWP_KB_SOURCE_ENV", raw)

    assert config.knowledge_source_env_preference() == expected
