"""管理端技能治理台接口测试。

覆盖：改业务分类 / 数据等级 / 推荐位、下架与恢复、重生成中文简介，以及权限与入参校验。
"""

from __future__ import annotations

from sqlalchemy import select

from app import models
from app.services import skill_intro, skillhub_client
from app.services.plugin_governance import market_plugins


def _login_admin(client) -> None:
    assert client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "Demo@123456"},
    ).status_code == 200


def _plugins(db_session, plugin_type: str) -> list[models.Plugin]:
    return list(db_session.scalars(
        select(models.Plugin).where(models.Plugin.plugin_type == plugin_type).order_by(models.Plugin.name)
    ).all())


def _make_mcp(db_session) -> models.Plugin:
    """种子数据里没有 MCP 插件，这里造一个用来验证「技能专属操作不得作用在 MCP 上」。"""
    plugin = models.Plugin(
        id="PLG-TEST-MCP", name="测试 MCP", type="mcp", plugin_type="mcp", scope="shared",
        status="active", data_level="L1", description="测试用 MCP 插件",
        runtime_meta={}, mcp_category="other", endpoint_ref="mock://",
    )
    db_session.add(plugin)
    db_session.commit()
    return plugin


def _governance(client, plugin_id: str, **payload):
    return client.put(f"/api/v1/admin/plugins/{plugin_id}/governance", json=payload)


def test_governance_updates_category_level_and_featured(client, db_session):
    _login_admin(client)
    skill = _plugins(db_session, "skill")[0]

    response = _governance(
        client, skill.id, skill_category="办公效率", data_level="L2", featured=True,
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "plugin_id": skill.id, "status": "active", "skill_category": "办公效率",
        "data_level": "L2", "featured": True,
    }
    db_session.refresh(skill)
    # 数据等级写的是 Plugin.data_level —— 授权判定（gateway）读的就是它
    assert skill.data_level == "L2"
    assert skill.skill_category == "办公效率"
    assert skill.featured is True


def test_governance_featured_skill_goes_to_front_of_shelf(client, db_session):
    _login_admin(client)
    skills = _plugins(db_session, "skill")
    assert len(skills) >= 2, "种子技能应至少两个，否则这条断言没有意义"
    last = skills[-1]

    before = market_plugins(db_session, "DT-E10281", "E10281", plugin_type="skill")
    assert before[0]["plugin_id"] != last.id

    assert _governance(client, last.id, featured=True).status_code == 200

    after = market_plugins(db_session, "DT-E10281", "E10281", plugin_type="skill")
    assert after[0]["plugin_id"] == last.id
    assert after[0]["featured"] is True
    # 精选前置是稳定分区：其余顺序不变
    assert [row["plugin_id"] for row in after[1:]] == [
        row["plugin_id"] for row in before if row["plugin_id"] != last.id
    ]


def test_governance_rejects_skill_category_on_mcp(client, db_session):
    _login_admin(client)
    mcp = _make_mcp(db_session)
    response = _governance(client, mcp.id, skill_category="办公效率")
    assert response.status_code == 422
    assert "技能" in response.json()["error"]["message"]


def test_governance_rejects_unknown_data_level(client, db_session):
    _login_admin(client)
    skill = _plugins(db_session, "skill")[0]
    assert _governance(client, skill.id, data_level="L9").status_code == 422


def test_governance_rejects_unknown_status(client, db_session):
    _login_admin(client)
    skill = _plugins(db_session, "skill")[0]
    assert _governance(client, skill.id, status="deleted").status_code == 422


def test_governance_withdraw_then_restore(client, db_session):
    _login_admin(client)
    skill = _plugins(db_session, "skill")[0]

    assert _governance(client, skill.id, status="withdrawn").status_code == 200
    db_session.refresh(skill)
    assert skill.status == "withdrawn"
    assert not [
        row for row in market_plugins(db_session, "DT-E10281", "E10281")
        if row["plugin_id"] == skill.id
    ], "下架后不应出现在货架"

    assert _governance(client, skill.id, status="active").status_code == 200
    db_session.refresh(skill)
    assert skill.status == "active"


def test_governance_unknown_plugin_returns_404(client):
    _login_admin(client)
    assert _governance(client, "SKILL-NOPE", featured=True).status_code == 404


def test_governance_requires_admin(client):
    assert client.post(
        "/api/v1/auth/login", json={"username": "E10281", "password": "Demo@123456"},
    ).status_code == 200
    assert _governance(client, "SKILL-NOPE", featured=True).status_code == 403


class _FakeProvider:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ARG002
        pass

    def structured_output(self, messages, schema):  # noqa: ANN001, ARG002
        assert all(message.get("source") == "demo" for message in messages)
        return {"description": "用于基金筛选与候选池排序。", "category": "数据分析"}


def test_regenerate_intro_writes_chinese_description(client, db_session, monkeypatch):
    _login_admin(client)
    skill = _plugins(db_session, "skill")[0]
    monkeypatch.setattr(skill_intro, "DeepSeekProvider", _FakeProvider)
    # 种子技能没有 SkillHub slug；这里顺带确认远端不可达时不影响生成。
    monkeypatch.setattr(
        skillhub_client, "get_skill",
        lambda slug: (_ for _ in ()).throw(skillhub_client.SkillHubError("SkillHub 不可达")),
    )

    response = client.post(f"/api/v1/admin/plugins/{skill.id}/intro", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "用于基金筛选与候选池排序。"
    assert body["skill_category"] == "数据分析"
    assert body["generated"] is True
    db_session.refresh(skill)
    assert skill.description == "用于基金筛选与候选池排序。"


def test_regenerate_intro_rejects_mcp(client, db_session):
    _login_admin(client)
    mcp = _make_mcp(db_session)
    assert client.post(f"/api/v1/admin/plugins/{mcp.id}/intro", json={}).status_code == 422


def test_admin_plugins_list_exposes_governance_fields(client, db_session):
    _login_admin(client)
    skill = _plugins(db_session, "skill")[0]
    _governance(client, skill.id, skill_category="内容创作", featured=True)

    rows = client.get("/api/v1/admin/plugins").json()
    row = next(item for item in rows if item["id"] == skill.id)
    assert row["skill_category"] == "内容创作"
    assert row["featured"] is True
    assert row["source"] == "platform"
    assert "description" in row
