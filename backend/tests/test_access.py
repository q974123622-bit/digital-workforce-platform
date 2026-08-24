"""P20 Access Request API 测试：L3 白名单申请/审批/授权/审计链路。"""

import pytest

from app import models


def _apply(client, applicant, resource_type="knowledge", resource_id="KB-CUSTOMER-SENSITIVE", reason="演示申请"):
    return client.post(
        "/api/v1/access-requests",
        params={"applicant_no": applicant},
        json={"resource_type": resource_type, "resource_id": resource_id, "reason": reason},
    )


def _approve(client, request_id, actor, approve=True):
    return client.post(
        f"/api/v1/access-requests/{request_id}/approve",
        json={"approve": approve, "actor_no": actor},
    )


def test_intern_apply_denied_no_request_row(client, db_session):
    resp = _apply(client, "DT-E20999")
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "POLICY_DENIED"
    assert body["error"]["detail"]["policy_id"] == "ACCESS-FORMAL-ONLY"
    assert db_session.query(models.AccessRequest).count() == 0
    denies = (
        db_session.query(models.AuditEvent)
        .filter(models.AuditEvent.action == "access_apply", models.AuditEvent.decision == "deny")
        .all()
    )
    assert denies


def test_formal_apply_pending(client, db_session):
    resp = _apply(client, "DT-E10281")
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    request = db_session.get(models.AccessRequest, body["id"])
    assert request.applicant_no == "DT-E10281"
    assert request.resource_type == "knowledge"
    assert request.resource_id == "KB-CUSTOMER-SENSITIVE"
    apply_audit = (
        db_session.query(models.AuditEvent)
        .filter(models.AuditEvent.trace_id == f"ARQ-{body['id']}", models.AuditEvent.action == "access_apply")
        .one()
    )
    assert apply_audit.knowledge_base_id == "KB-CUSTOMER-SENSITIVE"


def test_approve_grants_whitelist_and_allow(client, db_session):
    request_id = _apply(client, "DT-E10281").json()["id"]
    resp = _approve(client, request_id, "DT-E10281", approve=True)
    assert resp.status_code == 200
    assert resp.json()["status"] == "granted"
    grant = (
        db_session.query(models.EmployeePluginGrant)
        .filter_by(employee_id="DT-E10281", plugin_id="knowledge-l3")
        .one()
    )
    assert grant.decision_mode == "allow"
    assert grant.grant_source == "whitelist"
    # 批准后再次访问 KB-CUSTOMER-SENSITIVE → allow
    r = client.post(
        "/internal/knowledge/search",
        json={
            "employee_id": "DT-E10281",
            "knowledge_base_id": "KB-CUSTOMER-SENSITIVE",
            "query": "KYC",
            "trace_id": f"ARQ-{request_id}",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "allow"
    assert body["data"]["source"] == "demo"
    assert len(body["data"]["hits"]) >= 1


def test_reject_stays_denied(client):
    request_id = _apply(client, "DT-E10281").json()["id"]
    resp = _approve(client, request_id, "DT-E10281", approve=False)
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    r = client.post(
        "/internal/knowledge/search",
        json={
            "employee_id": "DT-E10281",
            "knowledge_base_id": "KB-CUSTOMER-SENSITIVE",
            "query": "KYC",
            "trace_id": "T-ACCESS-REJECT-1",
        },
    )
    assert r.status_code == 403
    assert r.json()["error"]["detail"]["policy_id"] == "P-DATA-003"


def test_l3_plugin_whitelist_scenario(client, db_session):
    # 未白名单：VE-0001 执行 rpa-report（L3 插件）→ deny
    r1 = client.post(
        "/internal/gateway/invoke",
        json={"employee_id": "VE-0001", "plugin_id": "rpa-report", "action": "execute", "params": {}, "trace_id": "T-ACCESS-RPA-1"},
    )
    assert r1.status_code == 403
    assert r1.json()["error"]["detail"]["policy_id"] == "P-DATA-003"
    # VE-0001（owner 为正式员工）申请并批准
    request_id = _apply(client, "VE-0001", resource_type="plugin", resource_id="rpa-report").json()["id"]
    assert _approve(client, request_id, "DT-E10281", approve=True).json()["status"] == "granted"
    grant = (
        db_session.query(models.EmployeePluginGrant)
        .filter_by(employee_id="VE-0001", plugin_id="rpa-report")
        .one()
    )
    assert grant.grant_source == "whitelist"
    # 白名单生效 → allow
    r2 = client.post(
        "/internal/gateway/invoke",
        json={"employee_id": "VE-0001", "plugin_id": "rpa-report", "action": "execute", "params": {}, "trace_id": "T-ACCESS-RPA-2"},
    )
    assert r2.status_code == 200
    assert r2.json()["decision"] == "allow"
    assert r2.json()["data"]["status"] == "generated"


def test_terminal_reapprove_conflict(client):
    request_id = _apply(client, "DT-E10281").json()["id"]
    assert _approve(client, request_id, "DT-E10281", approve=True).status_code == 200
    resp = _approve(client, request_id, "DT-E10281", approve=False)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "STATE_CONFLICT"


def test_audit_trace_aggregation(client, db_session):
    request_id = _apply(client, "DT-E10281").json()["id"]
    _approve(client, request_id, "DT-E10281", approve=True)
    client.post(
        "/internal/knowledge/search",
        json={
            "employee_id": "DT-E10281",
            "knowledge_base_id": "KB-CUSTOMER-SENSITIVE",
            "query": "KYC",
            "trace_id": f"ARQ-{request_id}",
        },
    )
    audits = (
        db_session.query(models.AuditEvent)
        .filter(models.AuditEvent.trace_id == f"ARQ-{request_id}")
        .all()
    )
    actions = {a.action for a in audits}
    assert {"access_apply", "access_approve", "access_grant", "read"} <= actions
    assert all(a.knowledge_base_id == "KB-CUSTOMER-SENSITIVE" for a in audits)


def test_list_requests_filter(client):
    req1 = _apply(client, "DT-E10281").json()
    req2 = _apply(client, "VE-0001", resource_type="plugin", resource_id="rpa-report").json()
    pending = client.get("/api/v1/access-requests", params={"status": "pending"}).json()
    assert {r["id"] for r in pending} >= {req1["id"], req2["id"]}
    only_dt = client.get("/api/v1/access-requests", params={"applicant_no": "DT-E10281"}).json()
    assert {r["id"] for r in only_dt} == {req1["id"]}
