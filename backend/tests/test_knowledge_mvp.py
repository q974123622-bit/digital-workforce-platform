def _login(client, username="E10281", password="Demo@123456"):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response


def test_local_login_and_current_account(client):
    response = _login(client)
    assert response.json()["account"]["employee_no"] == "E10281"
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["roles"] == ["user"]


def test_agent_directory_has_bounded_twin_and_role_colleagues(client):
    _login(client)
    rows = {row["employee_id"]: row for row in client.get("/api/v1/agents").json()}
    assert rows["DT-E10281"]["identity_kind"] == "human_twin"
    assert rows["DT-E10281"]["delegation_policy"] == "bounded_single"
    assert set(rows["AI-GENERAL"]["knowledge_base_ids"]) == {
        "KB-PUBLIC", "KB-ONBOARD", "KB-INTERNAL", "KB-FINTECH", "KB-IT-SERVICE",
        "KB-REG-INTERNAL", "KB-REG-EXTERNAL",
    }
    assert set(rows["AI-INVESTMENT"]["knowledge_base_ids"]) == {
        "KB-SECURITIES", "KB-INVESTMENT-BANKING"
    }
    assert rows["DT-E10281"]["knowledge_base_ids"] == []
    assert set(rows["AI-GENERAL"]["knowledge_base_ids"]).isdisjoint(
        rows["AI-INVESTMENT"]["knowledge_base_ids"]
    )


def test_runtime_is_one_stable_harness_identity_per_agent(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.runtime_manager.ensure_container",
        lambda employee_id: f"dwp-harness-{employee_id.lower()}",
    )
    _login(client)
    runtime = client.get("/api/v1/agents/AI-GENERAL/runtime")
    assert runtime.status_code == 200
    assert runtime.json()["engine"] == "harness"
    assert runtime.json()["container_name"] == "dwp-harness-ai-general"
    assert client.post("/api/v1/agents/AI-GENERAL/runtime/start").status_code == 403
    _login(client, username="admin")
    started = client.post("/api/v1/agents/AI-GENERAL/runtime/start")
    assert started.status_code == 200
    assert started.json()["state"] == "ready"


def test_mock_wecom_identity_routes_to_own_twin(client):
    response = client.post(
        "/api/v1/integrations/wecom/mock-callback",
        json={"corp_id": "demo-corp", "wecom_user_id": "E10281", "content": "VPN怎么申请"},
    )
    assert response.status_code == 200
    assert response.json()["target_agent_id"] == "DT-E10281"


def test_non_admin_cannot_read_directory(client):
    _login(client, "E20999")
    assert client.get("/api/v1/directory/users").status_code == 403


def test_investment_agent_tool_catalog_is_built_from_exact_grants(db_session):
    from app.services.chat import ChatOrchestrator
    from app.services.identity import resolve_identity

    subject = resolve_identity(db_session, "AI-INVESTMENT")
    tools = ChatOrchestrator._tools_for_subject(db_session, subject, "E10281")
    description = tools[0]["function"]["parameters"]["properties"]["knowledge_base_id"]["description"]

    assert "KB-SECURITIES" in description
    assert "KB-INVESTMENT-BANKING" in description
    assert "KB-IT-SERVICE" not in description
    assert "KB-REG-INTERNAL" not in description
