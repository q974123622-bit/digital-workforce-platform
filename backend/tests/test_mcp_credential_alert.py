"""用户级 MCP 令牌（按人签发、会过期）的到期提醒。

过期后调用才失败代价太大，因此要在**到期前**提醒：状态判定四态（ok / expiring /
expired / unknown）、阈值可配、接口只回状态与到期时间，绝不回任何凭据内容。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import models
from app.services import mcp_credentials

PLUGIN_ID = "PLG-MCP-EHOME-ALERT"
HUMAN = "E10281"
PLUGIN_NAME = "兴证e家员工服务"


@pytest.fixture(autouse=True)
def _credential_key(monkeypatch):
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_KEY", "unit-test-key-material")
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_KEY_FILE", "/nonexistent/mcp_credential_key")


def _make_plugin(db_session, *, plugin_id: str = PLUGIN_ID, status: str = "active") -> str:
    db_session.add(models.Plugin(
        id=plugin_id, name=PLUGIN_NAME, type="mcp", plugin_type="mcp", scope="shared",
        mcp_category="employee_service", data_level="L2", status=status,
        runtime_meta={"url": "http://10.32.253.43/ehome/mcp/claw", "authenticated": True},
    ))
    db_session.commit()
    return plugin_id


def _save_token(db_session, expires_at, *, plugin_id: str = PLUGIN_ID, secret: str = "jwt-token-value") -> None:
    mcp_credentials.save_user_credential(
        db_session, plugin_id=plugin_id, human_no=HUMAN, header_name="X-MCP-JWT",
        secret=secret, updated_by=HUMAN, expires_at=expires_at,
    )
    db_session.commit()


def _login_employee(client) -> None:
    response = client.post("/api/v1/auth/key-login", json={"access_key": "test-key-e10281"})
    assert response.status_code == 200


def _alerts(client) -> dict:
    response = client.get("/api/v1/my/mcp/credential-alerts")
    assert response.status_code == 200, response.text
    return response.json()


# -- 状态判定 ------------------------------------------------------------------


class _Row:
    """只带 expires_at 的轻量替身，避免为纯函数测试建库。"""

    def __init__(self, expires_at):
        self.expires_at = expires_at


def test_credential_state_thresholds():
    now = datetime(2026, 9, 24, 10, 0, 0)
    assert mcp_credentials.credential_state(None, warn_days=7, now=now)["state"] == "unknown"
    assert mcp_credentials.credential_state(_Row(None), warn_days=7, now=now)["state"] == "unknown"

    far = mcp_credentials.credential_state(_Row(now + timedelta(days=30)), warn_days=7, now=now)
    assert far["state"] == "ok"
    assert far["days_left"] == 30
    assert far["expires_at"] == now + timedelta(days=30)

    near = mcp_credentials.credential_state(_Row(now + timedelta(days=3)), warn_days=7, now=now)
    assert near["state"] == "expiring" and near["days_left"] == 3

    # 边界：正好落在阈值上算「即将过期」
    edge = mcp_credentials.credential_state(_Row(now + timedelta(days=7)), warn_days=7, now=now)
    assert edge["state"] == "expiring"

    expired = mcp_credentials.credential_state(_Row(now - timedelta(hours=1)), warn_days=7, now=now)
    assert expired["state"] == "expired" and expired["days_left"] == 0

    # 不足一天但还没过期：按 1 天显示，不出现「0 天后过期」
    today = mcp_credentials.credential_state(_Row(now + timedelta(hours=5)), warn_days=7, now=now)
    assert today["state"] == "expiring" and today["days_left"] == 1


# -- 员工侧提醒接口 ------------------------------------------------------------


def test_alerts_only_cover_expiring_and_expired(client, db_session):
    _make_plugin(db_session)
    _login_employee(client)

    # 没有凭据：不提醒
    body = _alerts(client)
    assert body == {"warn_days": 7, "alerts": [], "expired_count": 0, "expiring_count": 0}

    # 还早：不提醒
    _save_token(db_session, datetime.now() + timedelta(days=30))
    assert _alerts(client)["alerts"] == []

    # 阈值内：提醒并给出剩余天数与到期时间（剩余天数向下取整，多给几小时避免边界抖动）
    expires = datetime.now() + timedelta(days=3, hours=6)
    _save_token(db_session, expires)
    body = _alerts(client)
    assert body["expiring_count"] == 1
    alert = body["alerts"][0]
    assert alert["plugin_id"] == PLUGIN_ID
    assert alert["name"] == PLUGIN_NAME
    assert alert["mcp_category"] == "employee_service"
    assert alert["state"] == "expiring"
    assert alert["days_left"] == 3
    assert alert["expires_at"]

    # 已过期排最前
    _save_token(db_session, datetime.now() - timedelta(minutes=5))
    body = _alerts(client)
    assert body["expired_count"] == 1
    assert body["alerts"][0]["state"] == "expired"
    assert body["alerts"][0]["days_left"] == 0


def test_alerts_never_leak_credential_material(client, db_session):
    _make_plugin(db_session)
    _save_token(db_session, datetime.now() + timedelta(days=1), secret="top-secret-jwt")
    _login_employee(client)

    response = client.get("/api/v1/my/mcp/credential-alerts")
    assert response.status_code == 200
    assert "top-secret-jwt" not in response.text
    # 连掩码都不回（提醒只需要状态与时间）
    assert "****" not in response.text
    assert "secret_hint" not in response.text


def test_alerts_skip_withdrawn_service(client, db_session):
    """已下架的服务不再提醒：工具清单本来就取不到，提醒只会造成困扰。"""
    _make_plugin(db_session, status="withdrawn")
    _save_token(db_session, datetime.now() + timedelta(days=1))
    _login_employee(client)
    assert _alerts(client)["alerts"] == []


def test_warn_days_can_be_configured(client, db_session, monkeypatch):
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_WARN_DAYS", "30")
    _make_plugin(db_session)
    _save_token(db_session, datetime.now() + timedelta(days=20))
    _login_employee(client)
    body = _alerts(client)
    assert body["warn_days"] == 30
    assert body["expiring_count"] == 1


def test_warn_days_falls_back_on_garbage_value(client, db_session, monkeypatch):
    monkeypatch.setenv("DWP_MCP_CREDENTIAL_WARN_DAYS", "not-a-number")
    _make_plugin(db_session)
    _save_token(db_session, datetime.now() + timedelta(days=3))
    _login_employee(client)
    assert _alerts(client)["warn_days"] == 7


def test_alerts_require_employee_identity(client, db_session):
    """管理员账号没有个人 MCP 目录，不应看到任何人的令牌状态。"""
    _make_plugin(db_session)
    _save_token(db_session, datetime.now() + timedelta(days=1))
    assert client.post(
        "/api/v1/auth/key-login", json={"access_key": "test-key-admin00"},
    ).status_code == 200
    assert client.get("/api/v1/my/mcp/credential-alerts").status_code == 403


def test_my_services_carry_own_credential_state(client, db_session):
    """本人登记的个人 MCP 也带上本人的凭据状态（与共享 MCP 同一份结构）。"""
    _make_plugin(db_session, plugin_id="PLG-MCP-PERSONAL")
    plugin = db_session.get(models.Plugin, "PLG-MCP-PERSONAL")
    plugin.scope, plugin.owner_human_no = "personal", HUMAN
    db_session.commit()
    _save_token(db_session, datetime.now() + timedelta(days=2, hours=6), plugin_id="PLG-MCP-PERSONAL")

    _login_employee(client)
    response = client.get("/api/v1/my/mcp/plugins")
    assert response.status_code == 200, response.text
    service = response.json()["services"][0]
    assert service["credential"]["state"] == "expiring"
    assert service["credential"]["days_left"] == 2
    assert "jwt-token-value" not in response.text
