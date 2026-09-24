"""双后端选路与降级测试。

覆盖：一个平台知识库双后端（测试 REST / 生产 VeRAG）、按环境与可达性自动选路、
单边不可用降级、两边都不可用时抛可读错误并从工具清单摘除。
"""

import pytest

from app import models
from app.seed import ensure_mvp_seed
from app.services import knowledge_source_health as health
from app.services import knowledge_source_registry as registry
from app.services.harness_token import issue_token
from app.services.internal_knowledge_adapter import InternalKnowledgeAdapter
from app.services.knowledge_adapter import KnowledgeSourceUnavailable, select_adapter
from app.services.knowledge_registry import accessible_knowledge_bases
from app.services.identity import resolve_identity
from app.services.verag_mcp_knowledge_adapter import VeragMcpKnowledgeAdapter


def _configure(monkeypatch, sources=("rest", "verag")):
    monkeypatch.setattr(registry, "source_configured", lambda source: source in sources)


def _mark(db, source: str, state: str) -> None:
    health.upsert_health(db, {
        "source": source, "state": state, "latency_ms": 5,
        "last_error": "" if state == "available" else "连接超时", "detail": {},
    })


def _kb(db, kb_id: str) -> models.KnowledgeBase:
    return db.get(models.KnowledgeBase, kb_id)


@pytest.fixture()
def catalog_db(db_session):
    """seed_data 只灌 seed.json 的两条库；完整目录与独占授权由 ensure_mvp_seed 收敛。"""
    ensure_mvp_seed(db_session)
    return db_session


# ---- 选路 ---------------------------------------------------------------


def test_select_adapter_prefers_verag_in_auto_mode(catalog_db, monkeypatch):
    _configure(monkeypatch)

    adapter = select_adapter(None, _kb(catalog_db, "KB-IT-SERVICE"), catalog_db)

    assert isinstance(adapter, VeragMcpKnowledgeAdapter)
    # 传入的映射是 VeRAG 侧的远端库 ID（623），不是 REST 的 751
    assert adapter._kb_id_map == {"KB-IT-SERVICE": 623}  # noqa: SLF001


def test_select_adapter_falls_back_to_rest_when_verag_unreachable(catalog_db, monkeypatch):
    _configure(monkeypatch)
    _mark(catalog_db, registry.SOURCE_VERAG, "unavailable")

    adapter = select_adapter(None, _kb(catalog_db, "KB-IT-SERVICE"), catalog_db)

    assert isinstance(adapter, InternalKnowledgeAdapter)
    assert adapter._kb_id_map == {"KB-IT-SERVICE": 751}  # noqa: SLF001


def test_select_adapter_uses_single_backend_when_only_one_registered(catalog_db, monkeypatch):
    _configure(monkeypatch)

    # KB-WEALTH 只有生产 VeRAG 后端
    assert isinstance(select_adapter(None, _kb(catalog_db, "KB-WEALTH"), catalog_db), VeragMcpKnowledgeAdapter)

    # KB-AUDIT 只有测试 REST 后端
    assert isinstance(select_adapter(None, _kb(catalog_db, "KB-AUDIT"), catalog_db), InternalKnowledgeAdapter)


def test_select_adapter_raises_when_all_backends_unreachable(catalog_db, monkeypatch):
    _configure(monkeypatch)
    _mark(catalog_db, registry.SOURCE_REST, "unavailable")
    _mark(catalog_db, registry.SOURCE_VERAG, "unavailable")

    with pytest.raises(KnowledgeSourceUnavailable, match="内部知识源当前不可用"):
        select_adapter(None, _kb(catalog_db, "KB-IT-SERVICE"), catalog_db)


def test_select_adapter_legacy_signature_still_fail_closed(monkeypatch):
    """旧调用签名（无知识库上下文）保持原有 fail-closed 语义。"""
    for name in ("DWP_INTERNAL_KB_BASE_URL", "DWP_INTERNAL_KB_X_ORG", "DWP_INTERNAL_KB_X_TENANT",
                 "DWP_INTERNAL_KB_X_USER", "DWP_INTERNAL_KB_AUTHORIZATION", "DWP_INTERNAL_KB_ID_MAP"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="配置不完整"):
        select_adapter()


# ---- 摘除 ---------------------------------------------------------------


def test_accessible_knowledge_bases_drops_unreachable_source(catalog_db, monkeypatch):
    _configure(monkeypatch)
    subject = resolve_identity(catalog_db, "AI-GENERAL")

    before = {row["id"] for row in accessible_knowledge_bases(catalog_db, subject)}
    assert "KB-IT-SERVICE" in before
    # 每个知识库都带当前生效通道
    assert all("active_source" in row for row in accessible_knowledge_bases(catalog_db, subject))

    _mark(catalog_db, registry.SOURCE_REST, "unavailable")
    _mark(catalog_db, registry.SOURCE_VERAG, "unavailable")

    assert accessible_knowledge_bases(catalog_db, subject) == []


def test_accessible_knowledge_bases_keeps_kb_when_one_backend_alive(catalog_db, monkeypatch):
    _configure(monkeypatch)
    subject = resolve_identity(catalog_db, "AI-GENERAL")

    _mark(catalog_db, registry.SOURCE_VERAG, "unavailable")

    ids = {row["id"] for row in accessible_knowledge_bases(catalog_db, subject)}
    assert "KB-IT-SERVICE" in ids
    # 生效通道回落为测试 REST
    row = next(
        item for item in accessible_knowledge_bases(catalog_db, subject) if item["id"] == "KB-IT-SERVICE"
    )
    assert row["active_source"] == registry.SOURCE_REST


def test_tools_list_omits_search_knowledge_when_all_sources_down(client, db_session, monkeypatch):
    """所有后端都不可达时不暴露 search_knowledge（空枚举会让模型反复试错）。"""
    _configure(monkeypatch)
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")
    _mark(db_session, registry.SOURCE_REST, "unavailable")
    _mark(db_session, registry.SOURCE_VERAG, "unavailable")

    token = issue_token(employee_id="AI-GENERAL", requester_human_no="E10281", trace_id="T-DOWN", depth=0)
    response = client.post(
        "/internal/agent-tools/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )

    assert response.status_code == 200
    names = [tool["name"] for tool in response.json()["result"]["tools"]]
    assert "search_knowledge" not in names


def test_tools_list_keeps_search_knowledge_when_source_alive(client, db_session, monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("DWP_HARNESS_TOOL_SIGNING_SECRET", "unit-test-secret")

    token = issue_token(employee_id="AI-GENERAL", requester_human_no="E10281", trace_id="T-UP", depth=0)
    response = client.post(
        "/internal/agent-tools/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )

    tool = next(
        item for item in response.json()["result"]["tools"] if item["name"] == "search_knowledge"
    )
    enum = tool["inputSchema"]["properties"]["knowledge_base_id"]["enum"]
    # 枚举只包含该员工独占持有的库，且不泄露未授权库名
    assert "KB-IT-SERVICE" in enum
    assert "KB-SECURITIES" not in enum


# ---- 员工核验页状态 ------------------------------------------------------


def test_effective_capabilities_reports_source_unavailable(client, db_session, monkeypatch):
    _configure(monkeypatch)
    _mark(db_session, registry.SOURCE_REST, "unavailable")
    _mark(db_session, registry.SOURCE_VERAG, "unavailable")

    response = client.get("/api/v1/agents/AI-GENERAL/effective-capabilities")

    assert response.status_code == 200
    items = {row["id"]: row for row in response.json()["capabilities"]}
    assert items["knowledge:KB-IT-SERVICE"]["status"] == "source_unavailable"
    assert "不可达" in items["knowledge:KB-IT-SERVICE"]["reason"]
    # 未授权的库仍然是 unauthorized，不能把连通性问题算成权限问题
    assert items["knowledge:KB-SECURITIES"]["status"] == "unauthorized"
