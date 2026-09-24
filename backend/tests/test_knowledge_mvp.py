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
    from app.services.knowledge_catalog import GRANT_SPECS

    assert set(rows["AI-GENERAL"]["knowledge_base_ids"]) == set(GRANT_SPECS["AI-GENERAL"])
    assert set(rows["AI-INVESTMENT"]["knowledge_base_ids"]) == set(GRANT_SPECS["AI-INVESTMENT"])
    assert rows["DT-E10281"]["knowledge_base_ids"] == []
    assert rows["DT-E20999"]["knowledge_base_ids"] == []
    # 独占口径：任意两个 AI 员工的知识库集合两两不相交。
    role_ids = [key for key, value in GRANT_SPECS.items() if value]
    for index, left in enumerate(role_ids):
        for right in role_ids[index + 1:]:
            assert set(rows[left]["knowledge_base_ids"]).isdisjoint(
                rows[right]["knowledge_base_ids"]
            ), f"{left} 与 {right} 的知识库授权出现交叉"


def test_version_of_runtime_derives_version_and_falls_back(monkeypatch):
    """runtime 目录名约定 `dsh-<version>`；指向 current 或取不到目录名时回落部署声明值。"""
    from app.services import runtime_registry

    monkeypatch.setenv("DWP_DSH_RUNTIME_VERSION", "0.1.5-rc.1")
    assert runtime_registry.version_of_runtime("./runtime/dsh-0.1.5-rc.1") == "0.1.5-rc.1"
    assert runtime_registry.version_of_runtime("./runtime/current") == "0.1.5-rc.1"
    assert runtime_registry.version_of_runtime("image-baked") == "0.1.5-rc.1"
    assert runtime_registry.version_of_runtime("") == "0.1.5-rc.1"


def test_agent_rows_expose_harness_image_and_version(client, monkeypatch):
    """Harness 列要能显示底座镜像与 dsh 版本：优先取容器标签，其余回落部署声明值。"""
    from app.services import runtime_registry

    monkeypatch.setenv("DWP_HARNESS_IMAGE", "dwp-dsh:rc6")
    monkeypatch.setenv("DWP_DSH_RUNTIME_VERSION", "0.1.5-rc.1")
    monkeypatch.setattr(
        runtime_registry,
        "harness_environments",
        lambda: {
            "AI-GENERAL": runtime_registry.HarnessEnvironment(
                image="dwp-dsh:rc6", runtime_version="0.1.5-rc.1", profile_rev="v1-20260910"
            )
        },
    )
    _login(client)
    rows = {row["employee_id"]: row for row in client.get("/api/v1/agents").json()}
    assert rows["AI-GENERAL"]["runtime_image"] == "dwp-dsh:rc6"
    assert rows["AI-GENERAL"]["runtime_version"] == "0.1.5-rc.1"
    assert rows["AI-GENERAL"]["runtime_profile_rev"] == "v1-20260910"
    # 没有容器标签的行（未启动 / Docker 不可达）回落声明值，字段不能为空。
    assert rows["AI-INVESTMENT"]["runtime_image"] == "dwp-dsh:rc6"
    assert rows["AI-INVESTMENT"]["runtime_version"] == "0.1.5-rc.1"


def test_runtime_info_lists_platform_images(client, monkeypatch):
    monkeypatch.setenv("DWP_IMAGE_TAG", "0.4.0-offline")
    monkeypatch.setenv("DWP_HARNESS_IMAGE", "dwp-dsh:rc6")
    monkeypatch.setenv("DWP_DSH_RUNTIME_VERSION", "0.1.5-rc.1")
    monkeypatch.setenv("DWP_PROFILE_REV", "v1-20260910")
    _login(client)
    response = client.get("/api/v1/agents/runtime-info")
    assert response.status_code == 200
    body = response.json()
    assert body["delivery_tag"] == "0.4.0-offline"
    images = {item["role"]: item for item in body["images"]}
    assert images["后端服务"]["name"] == "dwp-backend:0.4.0-offline"
    assert images["前端控制台"]["name"] == "dwp-frontend:0.4.0-offline"
    assert images["Harness 底座"]["name"] == "dwp-dsh:rc6"
    assert "0.1.5-rc.1" in images["Harness 底座"]["note"]


def test_twins_hold_no_knowledge_grants_or_plugin_bindings(db_session):
    from app import models

    knowledge_plugin_ids = [
        row.id for row in db_session.query(models.Plugin).filter(models.Plugin.type == "knowledge").all()
    ]
    for employee_id in ["DT-E10281", "DT-E20999"]:
        assert db_session.query(models.AgentKnowledgeGrant).filter(
            models.AgentKnowledgeGrant.employee_id == employee_id
        ).count() == 0
        assert db_session.query(models.AgentPluginBinding).filter(
            models.AgentPluginBinding.target_agent_id == employee_id,
            models.AgentPluginBinding.plugin_id.in_(knowledge_plugin_ids),
        ).count() == 0


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
