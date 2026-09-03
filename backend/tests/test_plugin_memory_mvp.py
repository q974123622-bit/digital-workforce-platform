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
