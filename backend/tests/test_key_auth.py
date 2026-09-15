def test_access_key_directly_resolves_employee_identity(client):
    response = client.post("/api/v1/auth/key-login", json={"access_key": "test-key-e10281"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["account"]["username"] == "E10281"
    assert payload["account"]["employee_no"] == "E10281"
    assert payload["account"]["must_change_password"] is False


def test_access_key_directly_resolves_admin_identity(client):
    response = client.post("/api/v1/auth/key-login", json={"access_key": "test-key-admin00"})
    assert response.status_code == 200
    assert "platform_admin" in response.json()["account"]["roles"]


def test_invalid_access_key_does_not_reveal_account(client):
    response = client.post("/api/v1/auth/key-login", json={"access_key": "invalid-key-000"})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "访问密钥无效或账号已停用"


def test_legacy_password_login_is_disabled_by_default(client, monkeypatch):
    monkeypatch.setenv("DWP_ENABLE_LEGACY_PASSWORD_LOGIN", "0")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "E10281", "password": "Demo@123456"},
    )
    assert response.status_code == 404


def test_current_digital_colleagues_expose_cartoon_avatar_assets(client):
    response = client.get("/api/v1/employees")
    assert response.status_code == 200
    employees = {item["employee_no"]: item for item in response.json()}
    for employee_no in (
        "DT-E10281",
        "DT-E20999",
        "AI-GENERAL",
        "AI-INVESTMENT",
        "AI-RESEARCH",
        "AI-FUND",
        "AI-XYZQ",
    ):
        assert employees[employee_no]["avatar_url"].startswith("/avatars/")
        assert employees[employee_no]["avatar_url"].endswith(".webp")
