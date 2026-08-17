def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_seed_counts(client):
    employees = client.get("/api/v1/employees").json()
    assert len(employees) == 5
    assert len([e for e in employees if e["type"] == "twin"]) == 2
    assert len([e for e in employees if e["type"] == "virtual"]) == 3
    assert len(client.get("/api/v1/plugins").json()) == 6
    assert len(client.get("/api/v1/policies").json()) == 8
    assert len(client.get("/api/v1/knowledge-bases").json()) == 4
    teams = client.get("/api/v1/teams").json()
    assert len(teams) == 1
    assert len(teams[0]["members"]) == 3


def test_employee_list_filters(client):
    twins = client.get("/api/v1/employees", params={"type": "twin"}).json()
    assert [e["employee_no"] for e in twins] == ["DT-E10281", "DT-E20999"]
    virtuals = client.get("/api/v1/employees", params={"type": "virtual"}).json()
    assert all(e["type"] == "virtual" for e in virtuals)


def test_employee_detail_grants(client):
    emp = client.get("/api/v1/employees/DT-E10281").json()
    assert emp["owner_human_no"] == "E10281"
    modes = {g["plugin_id"]: g["decision_mode"] for g in emp["grants"]}
    assert modes["knowledge-l2"] == "allow"
    assert modes["rpa-report"] == "deny"


def test_employee_crud(client):
    resp = client.post(
        "/api/v1/employees",
        json={
            "name": "测试虚拟员工",
            "type": "virtual",
            "owner_human_no": "E10281",
            "department": "测试部",
        },
    )
    assert resp.status_code == 201
    employee_no = resp.json()["employee_no"]
    assert employee_no == "VE-0004"

    resp = client.put(f"/api/v1/employees/{employee_no}", json={"name": "改名后的虚拟员工"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "改名后的虚拟员工"

    assert client.delete(f"/api/v1/employees/{employee_no}").status_code == 204
    assert client.get(f"/api/v1/employees/{employee_no}").status_code == 404


def test_plugin_crud(client):
    resp = client.post(
        "/api/v1/plugins",
        json={"id": "test-plugin", "name": "测试插件", "type": "http", "data_level": "L1"},
    )
    assert resp.status_code == 201
    assert client.put("/api/v1/plugins/test-plugin", json={"status": "disabled"}).json()["status"] == "disabled"
    assert client.delete("/api/v1/plugins/test-plugin").status_code == 204
    assert client.get("/api/v1/plugins/test-plugin").status_code == 404


def test_policy_crud(client):
    resp = client.post(
        "/api/v1/policies",
        json={"id": "P-TEST-001", "name": "测试策略", "effect": "deny", "priority": 50},
    )
    assert resp.status_code == 201
    assert client.put("/api/v1/policies/P-TEST-001", json={"enabled": False}).json()["enabled"] is False
    assert client.delete("/api/v1/policies/P-TEST-001").status_code == 204


def test_audit_events(client):
    created = client.post(
        "/api/v1/audit",
        json={
            "trace_id": "T-TEST-001",
            "actor": "DT-E10281",
            "employee_id": "DT-E10281",
            "plugin_id": "knowledge-l1",
            "action": "read",
            "decision": "allow",
        },
    )
    assert created.status_code == 201
    assert created.json()["trace_id"] == "T-TEST-001"

    denied = client.get("/api/v1/audit", params={"decision": "deny"}).json()
    assert len(denied) >= 2
    assert all(e["decision"] == "deny" for e in denied)

    by_trace = client.get("/api/v1/audit", params={"trace_id": "T-DEMO-001"}).json()
    assert len(by_trace) == 2

    event_id = created.json()["id"]
    assert client.delete(f"/api/v1/audit/{event_id}").status_code == 204
    assert client.get(f"/api/v1/audit/{event_id}").status_code == 404


def test_teams_and_knowledge(client):
    team = client.get("/api/v1/teams/TEAM-ONBOARD").json()
    assert team["leader_employee_id"] == "VE-0001"
    assert {m["employee_id"] for m in team["members"]} == {"VE-0001", "VE-0002", "VE-0003"}

    kb = client.get("/api/v1/knowledge-bases/KB-L1-PUB").json()
    assert kb["level"] == "L1"
    assert client.get("/api/v1/knowledge-bases/NOT-EXIST").status_code == 404


def test_error_shape(client):
    resp = client.get("/api/v1/employees/NOT-EXIST")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "NOT_FOUND"
