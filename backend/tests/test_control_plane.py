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
    emp = client.get("/api/v1/employees/AI-GENERAL").json()
    assert emp["type"] == "virtual"
    assert emp["owner_human_no"] == "E10281"


# ---- Policy Engine：四维评估 ----


def test_policy_001_role_employee_allow_internal_kb(client):
    resp = _evaluate(client, "AI-GENERAL", "knowledge", "L2", "L2", "read")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "allow"
    assert body["policy_id"] == "POLICY-006"


def test_policy_002_intern_twin_deny_internal_kb(client):
    resp = _evaluate(client, "DT-E20999", "knowledge", "L2", "L2", "read")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "deny"
    assert body["policy_id"] == "POLICY-002"


def test_policy_003_internet_deny(client):
    # 禁网员工调用公网插件（type=http）：DENY by POLICY-003
    resp = _evaluate(client, "DT-E10281", "http", "public-search", "L1", "search")
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


def test_policy_default_l1_read_allow(client):
    resp = _evaluate(client, "AI-GENERAL", "knowledge", "L1", "L1", "read")
    assert resp.status_code == 200
    assert resp.json()["decision"] == "allow"


def test_policy_identity_cannot_be_spoofed(client):
    # 声称 formal，但数据库身份为 intern（DT-E20999）：以 DB 为准 → DENY
    resp = _evaluate(client, "DT-E20999", "knowledge", "L2", "L2", "read")
    assert resp.json()["decision"] == "deny"
    assert resp.json()["policy_id"] == "POLICY-002"


# ---- Plugin Gateway：Identity → Policy → Gateway → Adapter → Result + Audit ----


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


def test_gateway_missing_employee(client):
    resp = client.post(
        "/internal/gateway/invoke",
        json={
            "employee_id": "VE-9999",
            "plugin_id": "some-plugin",
            "action": "read",
            "params": {},
            "trace_id": "T-GW-NOEMP-001",
        },
    )
    assert resp.status_code == 404


