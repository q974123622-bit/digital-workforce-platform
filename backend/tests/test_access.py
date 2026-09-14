"""L3 读取白名单和 L3 执行审批互不绕过。"""

import pytest

from app import models

L3_KB_ID = "KB-L3-TEST"


@pytest.fixture()
def l3_kb(db_session):
    """测试自建 L3 资源，验证白名单/审批机制（不依赖种子 mock 数据）。"""
    db_session.add(models.KnowledgeBase(
        id=L3_KB_ID, name="测试 L3 知识库", level="L3", data_level="L3",
        resource_type="knowledge", allowed_employment_type=["formal"],
        department_scope=["*"], domain="测试", description="L3 白名单测试资源",
        status="active",
    ))
    db_session.commit()
    return L3_KB_ID


def _apply(client, applicant="DT-E10281"):
    return client.post(
        "/api/v1/access-requests",
        params={"applicant_no": applicant},
        json={"resource_type": "knowledge", "resource_id": L3_KB_ID, "reason": "测试申请"},
    )


def _search(client, employee="DT-E10281", trace_id="T-L3"):
    return client.post(
        "/internal/knowledge/search",
        json={"employee_id": employee, "knowledge_base_id": L3_KB_ID, "query": "KYC", "trace_id": trace_id},
    )


def test_l3_plugin_whitelist_does_not_bypass_knowledge_grant(client, db_session, l3_kb):
    denied = _search(client)
    assert denied.status_code == 403
    assert denied.json()["error"]["detail"]["policy_id"] == "P-DATA-003"

    request_id = _apply(client).json()["id"]
    approved = client.post(
        f"/api/v1/access-requests/{request_id}/approve",
        json={"approve": True, "actor_no": "DT-E10281"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "granted"
    grant = db_session.query(models.EmployeePluginGrant).filter_by(
        employee_id="DT-E10281", plugin_id="L3", action="read"
    ).one()
    assert grant.grant_source == "whitelist"
    # 张三没有任何具体知识库授权；插件白名单不能绕过逐库授权。
    assert _search(client, trace_id=f"ARQ-{request_id}").status_code == 403


def test_intern_cannot_apply_and_virtual_cannot_approve(client, l3_kb):
    denied = _apply(client, "DT-E20999")
    assert denied.status_code == 403
    assert denied.json()["error"]["detail"]["policy_id"] == "ACCESS-FORMAL-ONLY"

    request_id = _apply(client).json()["id"]
    denied_approval = client.post(
        f"/api/v1/access-requests/{request_id}/approve",
        json={"approve": True, "actor_no": "AI-GENERAL"},
    )
    assert denied_approval.status_code == 403


def test_duplicate_pending_request_conflicts(client, l3_kb):
    assert _apply(client).status_code == 201
    duplicate = _apply(client)
    assert duplicate.status_code == 409
