"""原子能力 Plugin 测试：knowledge-catalog / employee-search / document-catalog。"""

from app.services import gateway


def _invoke(db, plugin_id, params, employee_id="DT-E10281", action="read", trace_id="T-CATALOG"):
    return gateway.invoke_plugin(
        db,
        employee_id=employee_id,
        plugin_id=plugin_id,
        action=action,
        params=params,
        trace_id=trace_id,
    )


# ---- knowledge-catalog ----


def test_knowledge_catalog_all(db_session):
    result = _invoke(db_session, "knowledge-catalog", {})
    assert result["ok"] is True
    assert result["decision"] == "allow"
    kbs = result["data"]["knowledge_bases"]
    assert {kb["id"] for kb in kbs} == {
        "KB-PUBLIC", "KB-ONBOARD", "KB-INTERNAL", "KB-FINTECH",
        "KB-IT-SERVICE", "KB-SECURITIES", "KB-REG-INTERNAL", "KB-REG-EXTERNAL",
        "KB-CUSTOMER-SENSITIVE",
        "KB-HR-POLICY", "KB-AUDIT-PROCEDURE",
    }
    for kb in kbs:
        assert set(kb) == {"id", "name", "level", "domain", "description"}


def test_knowledge_catalog_level_filter(db_session):
    l1 = {kb["id"] for kb in _invoke(db_session, "knowledge-catalog", {"level": "L1"})["data"]["knowledge_bases"]}
    assert l1 == {"KB-PUBLIC", "KB-ONBOARD", "KB-REG-EXTERNAL"}
    l2 = {kb["id"] for kb in _invoke(db_session, "knowledge-catalog", {"level": "L2"})["data"]["knowledge_bases"]}
    assert l2 == {
        "KB-INTERNAL", "KB-FINTECH", "KB-IT-SERVICE", "KB-SECURITIES",
        "KB-REG-INTERNAL", "KB-HR-POLICY", "KB-AUDIT-PROCEDURE",
    }


def test_knowledge_catalog_unknown_level_empty(db_session):
    result = _invoke(db_session, "knowledge-catalog", {"level": "L9"})
    assert result["data"]["status"] == "success"
    assert result["data"]["knowledge_bases"] == []


# ---- employee-search ----


def test_employee_search_by_employee_no(db_session):
    result = _invoke(db_session, "employee-search", {"keyword": "DT-E10281"})
    employees = result["data"]["employees"]
    assert employees and employees[0]["employee_no"] == "DT-E10281"


def test_employee_search_by_department(db_session):
    result = _invoke(db_session, "employee-search", {"department": "人力资源部"})
    employees = result["data"]["employees"]
    assert employees
    assert all(e["department"] == "人力资源部" for e in employees)


def test_employee_search_digital_only(db_session):
    result = _invoke(db_session, "employee-search", {"digital_only": True, "type": "virtual"})
    employees = result["data"]["employees"]
    assert employees
    assert all(e["type"] == "virtual" for e in employees)


def test_employee_search_no_match(db_session):
    result = _invoke(db_session, "employee-search", {"keyword": "ZZZ-NO-MATCH"})
    assert result["data"]["status"] == "success"
    assert result["data"]["employees"] == []


def test_employee_search_keyword_without_space_matches_space_name(db_session):
    # 关键词“HR助理”（无空格）应命中姓名“HR 助理”的 VE-0002
    result = _invoke(db_session, "employee-search", {"keyword": "HR助理", "digital_only": True})
    employees = result["data"]["employees"]
    assert any(e["employee_no"] == "VE-0002" for e in employees)


# ---- document-catalog ----


def test_document_catalog_lists_demo_docs(db_session):
    result = _invoke(db_session, "document-catalog", {})
    assert result["ok"] is True
    docs = result["data"]["documents"]
    names = {d["document_name"] for d in docs}
    assert {"normal-document.md", "empty-document.md", "conflict-document-a.md", "conflict-document-b.md"} <= names
    for doc in docs:
        assert "document_name" in doc
        assert "size" in doc
        assert "content" not in doc
