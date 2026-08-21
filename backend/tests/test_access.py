"""L3 读取白名单和 L3 执行审批互不绕过。"""

from app import models


def _apply(client, applicant="DT-E10281"):
    return client.post(
        "/api/v1/access-requests",
        params={"applicant_no": applicant},
        json={"resource_type": "knowledge", "resource_id": "KB-CUSTOMER-SENSITIVE", "reason": "测试申请"},
    )


def _search(client, employee="DT-E10281", trace_id="T-L3"):
    return client.post(
        "/internal/knowledge/search",
        json={"employee_id": employee, "knowledge_base_id": "KB-CUSTOMER-SENSITIVE", "query": "KYC", "trace_id": trace_id},
    )


def test_l3_read_requires_whitelist_then_allows(client, db_session):
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
        employee_id="DT-E10281", plugin_id="knowledge-l3", action="read"
    ).one()
    assert grant.grant_source == "whitelist"
    assert _search(client, trace_id=f"ARQ-{request_id}").status_code == 200


def test_intern_cannot_apply_and_virtual_cannot_approve(client):
    denied = _apply(client, "DT-E20999")
    assert denied.status_code == 403
    assert denied.json()["error"]["detail"]["policy_id"] == "ACCESS-FORMAL-ONLY"

    request_id = _apply(client).json()["id"]
    denied_approval = client.post(
        f"/api/v1/access-requests/{request_id}/approve",
        json={"approve": True, "actor_no": "VE-0001"},
    )
    assert denied_approval.status_code == 403


def test_l3_execute_still_requires_policy_005(client):
    response = client.post(
        "/internal/gateway/invoke",
        json={"employee_id": "RPA-0001", "plugin_id": "rpa-report", "action": "execute", "params": {}, "trace_id": "T-L3-EXEC"},
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "approval"
    assert response.json()["policy_id"] == "POLICY-005"


def test_duplicate_pending_request_conflicts(client):
    assert _apply(client).status_code == 201
    duplicate = _apply(client)
    assert duplicate.status_code == 409
