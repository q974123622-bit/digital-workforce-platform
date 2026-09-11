from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select

from app import models
from app.services.memory_service import five_complete_rounds, save_explicit


def _login(client, username):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "Demo@123456"})
    assert response.status_code == 200
    return response


def _zip(files: dict[str, str]) -> bytes:
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return out.getvalue()


def test_only_three_mvp_accounts_are_active(client, db_session):
    active = db_session.scalars(select(models.Account.username).where(models.Account.status == "active")).all()
    assert set(active) == {"E10281", "E20999", "admin"}
    assert _login(client, "E10281").json()["account"]["roles"] == ["user"]
    assert _login(client, "admin").json()["account"]["roles"] == ["agent_admin", "security_admin", "platform_admin"]
    assert client.post("/api/v1/auth/login", json={"username": "E10021", "password": "Demo@123456"}).status_code in {401, 403}


def test_personal_safe_skill_auto_publishes_and_isolated(client):
    _login(client, "E10281")
    package = _zip({
        "plugin.yaml": "name: concise-reply\ndescription: 简洁回复\ninstruction_summary: 先给结论\n",
        "SKILL.md": "# 简洁回复\n先给结论，再列依据。",
    })
    response = client.post("/api/v1/my/plugins/submissions", data={
        "name": "简洁回复", "plugin_type": "skill", "scope": "personal",
        "deployment_mode": "instruction", "data_level": "L1", "version": "1.0.0",
        "target_agent_id": "DT-E10281",
    }, files={"file": ("skill.zip", package, "application/zip")})
    assert response.status_code == 201
    assert response.json()["review_status"] == "approved"
    assert response.json()["publish_status"] == "published"
    mine = client.get("/api/v1/my/plugins").json()["effective"]
    assert any(row["name"] == "简洁回复" for row in mine)
    _login(client, "E20999")
    assert not any(row["name"] == "简洁回复" for row in client.get("/api/v1/my/plugins").json()["effective"])


def test_mcp_requires_admin_review_and_manual_publish(client):
    _login(client, "E10281")
    package = _zip({
        "plugin.yaml": "name: fund-query\ntools:\n  - name: query_fund_profile\n    inputSchema: {type: object}\n",
        "examples.json": "{}",
    })
    response = client.post("/api/v1/my/plugins/submissions", data={
        "name": "基金查询", "plugin_type": "mcp", "scope": "personal",
        "mcp_category": "fund", "deployment_mode": "external", "data_level": "L1", "version": "1.0.0",
    }, files={"file": ("mcp.zip", package, "application/zip")})
    assert response.status_code == 201
    submission = response.json()
    assert submission["review_status"] == "pending"
    _login(client, "admin")
    assert client.post(f"/api/v1/admin/plugin-submissions/{submission['id']}/approve", json={"note": "扫描通过"}).status_code == 200
    published = client.post(f"/api/v1/admin/plugins/{submission['plugin_id']}/versions/1.0.0/publish")
    assert published.status_code == 200
    assert published.json()["publish_status"] == "published"


def test_plugin_safe_detail_exposes_useful_metadata_without_secrets(client, db_session):
    _login(client, "E10281")
    package = _zip({
        "plugin.yaml": (
            "name: safe-fund-query\n"
            "description: 查询基金公开资料\n"
            "tools:\n"
            "  - name: query_fund_profile\n"
            "    description: 按基金代码读取公开档案\n"
            "authorization: placeholder-must-not-leak\n"
        ),
        "examples.json": "{}",
    })
    response = client.post("/api/v1/my/plugins/submissions", data={
        "name": "安全基金查询", "plugin_type": "mcp", "scope": "personal",
        "mcp_category": "fund", "deployment_mode": "external", "data_level": "L1", "version": "1.0.0",
    }, files={"file": ("mcp.zip", package, "application/zip")})
    # The scanner rejects credential-shaped fields before any detail can be published.
    assert response.status_code == 422

    clean_package = _zip({
        "plugin.yaml": (
            "name: safe-fund-query\n"
            "description: 查询基金公开资料\n"
            "tools:\n"
            "  - name: query_fund_profile\n"
            "    description: 按基金代码读取公开档案\n"
        ),
        "examples.json": "{}",
    })
    created = client.post("/api/v1/my/plugins/submissions", data={
        "name": "安全基金查询", "plugin_type": "mcp", "scope": "personal",
        "mcp_category": "fund", "deployment_mode": "external", "data_level": "L1", "version": "1.0.0",
    }, files={"file": ("mcp.zip", clean_package, "application/zip")}).json()
    plugin = db_session.get(models.Plugin, created["plugin_id"])
    plugin.description = "只显示安全业务说明"
    plugin.endpoint_ref = "https://internal.example.invalid/secret"
    db_session.commit()

    detail_response = client.get(f"/api/v1/my/plugins/{created['plugin_id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["description"] == "只显示安全业务说明"
    assert detail["tools"] == [{"name": "query_fund_profile", "description": "按基金代码读取公开档案"}]
    serialized = detail_response.text.lower()
    assert "endpoint" not in serialized
    assert "authorization" in detail and "internal.example" not in serialized
    assert "artifact_path" not in serialized and "sha256" not in serialized

    _login(client, "admin")
    admin_detail = client.get(f"/api/v1/admin/plugins/{created['plugin_id']}")
    assert admin_detail.status_code == 200
    assert "versions" in admin_detail.json() and "bindings" in admin_detail.json()
    assert "internal.example" not in admin_detail.text.lower()


def test_personal_plugin_detail_is_not_visible_to_another_employee(client):
    _login(client, "E10281")
    package = _zip({
        "plugin.yaml": "name: private-style\ndescription: 个人工作方法\ninstruction_summary: 先列结论\n",
        "SKILL.md": "# 个人工作方法\n先列结论。",
    })
    created = client.post("/api/v1/my/plugins/submissions", data={
        "name": "个人工作方法", "plugin_type": "skill", "scope": "personal",
        "deployment_mode": "instruction", "data_level": "L1", "version": "1.0.0",
    }, files={"file": ("skill.zip", package, "application/zip")}).json()
    _login(client, "E20999")
    assert client.get(f"/api/v1/my/plugins/{created['plugin_id']}").status_code == 404


def test_zip_security_rejects_scripts_and_credentials(client):
    _login(client, "E10281")
    bad = _zip({"plugin.yaml": "name: bad\nauthorization: Bearer real\n", "SKILL.md": "ok", "run.py": "print(1)"})
    response = client.post("/api/v1/my/plugins/submissions", data={
        "name": "bad", "plugin_type": "skill", "scope": "personal",
        "deployment_mode": "instruction", "data_level": "L1", "version": "1.0.0",
    }, files={"file": ("bad.zip", bad, "application/zip")})
    assert response.status_code == 422


def test_five_rounds_and_memory_namespace_isolation(db_session):
    history = []
    for index in range(1, 7):
        history += [{"role": "user", "content": f"q{index}"}, {"role": "assistant", "content": f"a{index}"}]
    history.append({"role": "user", "content": "current goal"})
    window = five_complete_rounds(history)
    assert len(window) == 10 and window[0]["content"] == "q2" and window[-1]["content"] == "a6"
    first = save_explicit(db_session, "E10281", "AI-GENERAL", "偏好简洁答复")
    second = save_explicit(db_session, "E20999", "AI-GENERAL", "偏好详细答复")
    assert first.requester_human_no != second.requester_human_no
