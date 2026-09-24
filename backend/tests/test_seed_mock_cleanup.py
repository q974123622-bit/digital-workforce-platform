"""历史 mock 知识库幂等清理测试。

覆盖：老库升级时删除虚构知识库及其授权/访问申请/历史分级 mock 插件；
内部知识引擎的真实库不受影响；描述里的演示字样被纠正；重复执行零影响。
"""

from app import models
from app.seed import (
    LEGACY_MOCK_KNOWLEDGE_BASE_IDS,
    ensure_mvp_seed,
    purge_legacy_mock_knowledge_bases,
)
from app.services.knowledge_catalog import catalog_ids

MOCK_KB_IDS = list(LEGACY_MOCK_KNOWLEDGE_BASE_IDS)
# 当前保留的真实库目录（权威文案）
CANONICAL_KB_IDS = tuple(catalog_ids())
# 老库里另外几条内部知识引擎真实库：白名单式删除会误删，必须原样保留。
# 刻意避开目录里的 ID，避免与「目录收敛」混为一谈。
OTHER_REAL_KB_IDS = (
    "KB-DISCIPLINE",
    "KB-FINANCE-REIMBURSE",
    "KB-OPERATIONS",
    "KB-LEGACY-REGULATION",
    "KB-LEGACY-CUSTODY",
)
LEGACY_PLUGIN_IDS = ("knowledge-l1", "knowledge-l2", "knowledge-l3")


def _add_legacy_mock_data(db) -> None:
    """模拟 9-14 之前旧 seed.json 灌入的老库残留。"""
    for kb_id in MOCK_KB_IDS:
        if db.get(models.KnowledgeBase, kb_id) is None:
            db.add(models.KnowledgeBase(
                id=kb_id, name=f"旧演示库 {kb_id}", level="L2", data_level="L2",
                description="虚构演示", status="active",
            ))
    for kb_id in OTHER_REAL_KB_IDS:
        if db.get(models.KnowledgeBase, kb_id) is None:
            db.add(models.KnowledgeBase(
                id=kb_id, name=f"真实库 {kb_id}", level="L2", data_level="L2",
                description="内部知识引擎真实库", status="active",
            ))
    # VE-0001 不在 grant_specs 里，只有清理逻辑会删它的授权。
    db.add(models.AgentKnowledgeGrant(employee_id="VE-0001", knowledge_base_id="KB-PUBLIC"))
    db.add(models.AgentKnowledgeGrant(employee_id="VE-0001", knowledge_base_id="KB-ONBOARD"))
    db.add(models.AccessRequest(
        applicant_no="E10281", resource_type="knowledge",
        resource_id="KB-CUSTOMER-SENSITIVE", reason="旧演示申请", status="pending",
    ))
    for plugin_id in LEGACY_PLUGIN_IDS:
        db.add(models.Plugin(
            id=plugin_id, name=f"旧插件 {plugin_id}", type="knowledge",
            plugin_type="mcp", endpoint_ref="mock://kb", status="active",
        ))
        version = models.PluginVersion(plugin_id=plugin_id, version="1.0.0", submitted_by="seed")
        db.add(version)
        db.flush()
        db.add(models.PluginReview(plugin_version_id=version.id, decision="approved", reviewer="seed"))
        db.add(models.PluginBuildJob(id=f"job-{plugin_id}", plugin_version_id=version.id))
        db.add(models.McpRuntimeInstance(plugin_version_id=version.id))
        db.add(models.AgentPluginBinding(plugin_id=plugin_id, target_agent_id="DT-E10281", authorized_by="seed"))
        db.add(models.EmployeePluginGrant(employee_id="DT-E10281", plugin_id=plugin_id, grant_source="seed"))
    db.commit()


def test_fresh_seed_has_no_mock_knowledge_bases(db_session):
    rows = db_session.query(models.KnowledgeBase).all()
    assert sorted(row.id for row in rows) == sorted(CANONICAL_KB_IDS)
    assert all("虚构" not in (row.description or "") for row in rows)
    assert all("演示" not in (row.description or "") for row in rows)


def test_purge_removes_legacy_mock_knowledge_bases_and_references(db_session):
    _add_legacy_mock_data(db_session)

    result = purge_legacy_mock_knowledge_bases(db_session)
    db_session.commit()

    assert result["knowledge_bases"] == len(MOCK_KB_IDS)
    for kb_id in MOCK_KB_IDS:
        assert db_session.get(models.KnowledgeBase, kb_id) is None
    for kb_id in CANONICAL_KB_IDS + OTHER_REAL_KB_IDS:
        assert db_session.get(models.KnowledgeBase, kb_id) is not None

    assert db_session.query(models.AgentKnowledgeGrant).filter(
        models.AgentKnowledgeGrant.knowledge_base_id.in_(MOCK_KB_IDS),
    ).count() == 0
    assert db_session.query(models.AccessRequest).filter(
        models.AccessRequest.resource_id.in_(MOCK_KB_IDS),
    ).count() == 0

    assert result["knowledge_plugins"] == len(LEGACY_PLUGIN_IDS)
    for plugin_id in LEGACY_PLUGIN_IDS:
        assert db_session.get(models.Plugin, plugin_id) is None
    assert db_session.query(models.PluginVersion).filter(
        models.PluginVersion.plugin_id.in_(LEGACY_PLUGIN_IDS),
    ).count() == 0
    assert db_session.query(models.PluginReview).count() == 0
    assert db_session.query(models.PluginBuildJob).count() == 0
    assert db_session.query(models.McpRuntimeInstance).count() == 0
    assert db_session.query(models.AgentPluginBinding).filter(
        models.AgentPluginBinding.plugin_id.in_(LEGACY_PLUGIN_IDS),
    ).count() == 0
    assert db_session.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.plugin_id.in_(LEGACY_PLUGIN_IDS),
    ).count() == 0


def test_purge_corrects_demo_description_markers(db_session):
    db_session.get(models.KnowledgeBase, "KB-IT-SERVICE").description = "IT 服务与办公工具使用问答（虚构演示）"
    db_session.get(models.KnowledgeBase, "KB-SECURITIES").description = "人工维护的证券说明"
    db_session.commit()

    result = purge_legacy_mock_knowledge_bases(db_session)
    db_session.commit()

    assert result["corrected_descriptions"] == 1
    assert db_session.get(models.KnowledgeBase, "KB-IT-SERVICE").description == "IT 服务与办公工具使用问答（内部知识引擎）"
    assert db_session.get(models.KnowledgeBase, "KB-SECURITIES").description == "人工维护的证券说明"


def test_purge_is_idempotent(db_session):
    _add_legacy_mock_data(db_session)
    purge_legacy_mock_knowledge_bases(db_session)
    db_session.commit()

    second = purge_legacy_mock_knowledge_bases(db_session)
    db_session.commit()

    assert all(value == 0 for value in second.values())


def test_ensure_mvp_seed_purges_legacy_mock_data_on_upgrade(db_session):
    """升级路径：非空老库只走 ensure_mvp_seed，也必须把 mock 清干净。"""
    _add_legacy_mock_data(db_session)

    ensure_mvp_seed(db_session)

    assert db_session.get(models.KnowledgeBase, "KB-CUSTOMER-SENSITIVE") is None
    assert db_session.get(models.KnowledgeBase, "KB-PUBLIC") is None
    assert db_session.get(models.KnowledgeBase, "KB-ONBOARD") is None
    assert db_session.get(models.KnowledgeBase, "KB-OPERATIONS") is not None
    assert db_session.query(models.AgentKnowledgeGrant).filter_by(employee_id="VE-0001").count() == 0
    assert db_session.get(models.Plugin, "knowledge-l2") is None


def test_knowledge_bases_api_excludes_legacy_mock(client, db_session):
    _add_legacy_mock_data(db_session)
    ensure_mvp_seed(db_session)

    ids = [row["id"] for row in client.get("/api/v1/knowledge-bases").json()]

    assert "KB-CUSTOMER-SENSITIVE" not in ids
    assert "KB-PUBLIC" not in ids
    assert "KB-IT-SERVICE" in ids
