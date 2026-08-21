"""Sprint 2 Core Control Plane 测试：
Employee Identity / Policy Engine（四维评估）/ Plugin Gateway / Audit 落库。
覆盖：ALLOW、DENY、APPROVAL、Internet Deny、Local Execution Deny。
"""


def _evaluate(client, employee_no, resource_type, resource_id, data_level, action, context=None):
    body = {
        "subject": {"employee_no": employee_no, "type": "twin", "employment_type": "formal"},
        "resource": {"type": resource_type, "id": resource_id, "data_level": data_level},
        "action": action,
    }
    if context is not None:
        body["context"] = context
    return client.post("/internal/policy/evaluate", json=body)


# ---- Employee Identity ----


def test_identity_formal_twin(client):
    emp = client.get("/api/v1/employees/DT-E10281").json()
    assert emp["type"] == "twin"
    assert emp["source_human_no"] == "E10281"
    assert emp["owner_human_no"] == "E10281"
    assert emp["department"] == "架构部"


def test_identity_intern_twin(client):
    emp = client.get("/api/v1/employees/DT-E20999").json()
    assert emp["type"] == "twin"
    assert emp["source_human_no"] == "E20999"
    assert emp["owner_human_no"] == "E20999"


def test_identity_virtual_employee(client):
    emp = client.get("/api/v1/employees/VE-0001").json()
    assert emp["type"] == "virtual"
    assert emp["owner_human_no"] == "E10021"


# ---- Policy Engine：四维评估 ----


def test_policy_001_formal_twin_allow_internal_kb(client):
    resp = _evaluate(client, "DT-E10281", "knowledge", "knowledge-l2", "L2", "read")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "allow"
    assert body["policy_id"] == "POLICY-001"


def test_policy_002_intern_twin_deny_internal_kb(client):
    resp = _evaluate(client, "DT-E20999", "knowledge", "knowledge-l2", "L2", "read")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "deny"
    assert body["policy_id"] == "POLICY-002"


def test_policy_003_internet_deny(client):
    # 禁网员工调用公网插件（type=http）：DENY by POLICY-003
    resp = _evaluate(client, "DT-E10281", "http", "internet-search", "L1", "search")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "deny"
    assert body["policy_id"] == "POLICY-003"


def test_policy_004_local_execution_deny(client):
    # remote_only 员工请求本地执行：DENY by POLICY-004
    resp = _evaluate(client, "DT-E10281", "sandbox", "local", "L1", "execute")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "deny"
    assert body["policy_id"] == "POLICY-004"


def test_policy_l3_sensitive_requires_whitelist(client):
    # P20：L3 资源一律走白名单；无白名单授权 → P-DATA-003 DENY（原 POLICY-005 审批语义已被取代）
    resp = _evaluate(client, "RPA-0001", "rpa", "rpa-report", "L3", "execute")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "deny"
    assert body["policy_id"] == "P-DATA-003"


def test_policy_default_l1_read_allow(client):
    resp = _evaluate(client, "DT-E20999", "knowledge", "knowledge-l1", "L1", "read")
    assert resp.status_code == 200
    assert resp.json()["decision"] == "allow"


def test_policy_identity_cannot_be_spoofed(client):
    # 声称 formal，但数据库身份为 intern（DT-E20999）：以 DB 为准 → DENY
    resp = _evaluate(client, "DT-E20999", "knowledge", "knowledge-l2", "L2", "read")
    assert resp.json()["decision"] == "deny"
    assert resp.json()["policy_id"] == "POLICY-002"


# ---- Plugin Gateway：Identity → Policy → Gateway → Adapter → Result + Audit ----


def test_gateway_allow_chain(client):
    resp = client.post(
        "/internal/gateway/invoke",
        json={
            "employee_id": "DT-E10281",
            "plugin_id": "knowledge-l2",
            "action": "read",
            "params": {"query": "入职流程"},
            "trace_id": "T-GW-ALLOW-001",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["decision"] == "allow"
    assert body["policy_id"] == "POLICY-001"
    assert body["data"]["source"] == "demo"
    assert len(body["audit_ids"]) == 1

    # 审计字段齐全：trace_id / employee_id / plugin_id / action / decision / reason / ts / result_summary
    audit = client.get(f"/api/v1/audit/{body['audit_ids'][0]}").json()
    assert audit["trace_id"] == "T-GW-ALLOW-001"
    assert audit["employee_id"] == "DT-E10281"
    assert audit["plugin_id"] == "knowledge-l2"
    assert audit["action"] == "read"
    assert audit["decision"] == "allow"
    assert audit["reason"] == "正式员工数字分身可访问内部知识库"
    assert audit["ts"]
    assert audit["result_summary"]


def test_gateway_deny_internal_kb(client):
    resp = client.post(
        "/internal/gateway/invoke",
        json={
            "employee_id": "DT-E20999",
            "plugin_id": "knowledge-l2",
            "action": "read",
            "params": {},
            "trace_id": "T-GW-DENY-001",
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "POLICY_DENIED"
    assert body["error"]["detail"]["policy_id"] == "POLICY-002"
    assert body["error"]["detail"]["reason"]
    audit_id = body["error"]["detail"]["audit_id"]
    audit = client.get(f"/api/v1/audit/{audit_id}").json()
    assert audit["decision"] == "deny"
    assert audit["employee_id"] == "DT-E20999"
    assert audit["trace_id"] == "T-GW-DENY-001"


def test_gateway_internet_deny(client):
    resp = client.post(
        "/internal/gateway/invoke",
        json={
            "employee_id": "DT-E10281",
            "plugin_id": "internet-search",
            "action": "search",
            "params": {"query": "anything"},
            "trace_id": "T-GW-NET-001",
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "POLICY_DENIED"
    assert body["error"]["detail"]["policy_id"] == "POLICY-003"
    audit = client.get(f"/api/v1/audit/{body['error']['detail']['audit_id']}").json()
    assert audit["decision"] == "deny"
    assert audit["plugin_id"] == "internet-search"


def test_gateway_l3_plugin_denied_without_whitelist(client):
    resp = client.post(
        "/internal/gateway/invoke",
        json={
            "employee_id": "RPA-0001",
            "plugin_id": "rpa-report",
            "action": "execute",
            "params": {},
            "trace_id": "T-GW-L3-001",
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "POLICY_DENIED"
    assert body["error"]["detail"]["policy_id"] == "P-DATA-003"
    audit = client.get(f"/api/v1/audit/{body['error']['detail']['audit_id']}").json()
    assert audit["decision"] == "deny"


def test_gateway_unknown_plugin_default_deny(client):
    resp = client.post(
        "/internal/gateway/invoke",
        json={
            "employee_id": "DT-E10281",
            "plugin_id": "not-registered-plugin",
            "action": "read",
            "params": {},
            "trace_id": "T-GW-UNK-001",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_gateway_no_grant_default_deny(client):
    # DT-E20999 对 adp-onboarding 无授权：DENY + 审计
    resp = client.post(
        "/internal/gateway/invoke",
        json={
            "employee_id": "DT-E20999",
            "plugin_id": "adp-onboarding",
            "action": "execute",
            "params": {},
            "trace_id": "T-GW-NOGRANT-001",
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "POLICY_DENIED"
    audit = client.get(f"/api/v1/audit/{body['error']['detail']['audit_id']}").json()
    assert audit["decision"] == "deny"
    assert "未授权插件" in audit["reason"]


def test_gateway_missing_employee(client):
    resp = client.post(
        "/internal/gateway/invoke",
        json={
            "employee_id": "VE-9999",
            "plugin_id": "knowledge-l1",
            "action": "read",
            "params": {},
            "trace_id": "T-GW-NOEMP-001",
        },
    )
    assert resp.status_code == 404


def test_gateway_virtual_employee_allow_via_grant(client):
    # VE-0001 经 grant allow 执行 adp-onboarding（P-PLUGIN-007 规则）
    resp = client.post(
        "/internal/gateway/invoke",
        json={
            "employee_id": "VE-0001",
            "plugin_id": "adp-onboarding",
            "action": "execute",
            "params": {"employee_name": "王小明"},
            "trace_id": "T-GW-VE-001",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["decision"] == "allow"
    assert body["policy_id"] == "P-PLUGIN-007"
    assert body["data"]["workflow"] == "adp-onboarding"
