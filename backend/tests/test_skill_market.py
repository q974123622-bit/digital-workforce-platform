"""货架按类型拆成「技能市场 / MCP 市场」的测试。

拆市场只是展示层过滤：六态计算、申请链路、授权事实都不变。
"""

from __future__ import annotations

from app.services.plugin_governance import market_plugins


def _login(client, key: str) -> None:
    assert client.post("/api/v1/auth/key-login", json={"access_key": key}).status_code == 200


def _market(client, **params) -> dict:
    response = client.get("/api/v1/my/plugins", params=params)
    assert response.status_code == 200
    return response.json()


def test_market_splits_by_plugin_type(client):
    _login(client, "test-key-e10281")

    everything = _market(client)["market"]
    skills = _market(client, plugin_type="skill")["market"]
    mcps = _market(client, plugin_type="mcp")["market"]

    assert {row["plugin_type"] for row in skills} <= {"skill"}
    assert {row["plugin_type"] for row in mcps} <= {"mcp"}
    assert len(skills) + len(mcps) == len(everything)
    assert {row["plugin_id"] for row in skills} | {row["plugin_id"] for row in mcps} == {
        row["plugin_id"] for row in everything
    }


def test_market_counts_cover_both_markets(client):
    _login(client, "test-key-e10281")

    payload = _market(client)
    counts = payload["market_counts"]

    assert counts.get("skill", 0) == len(_market(client, plugin_type="skill")["market"])
    assert counts.get("mcp", 0) == len(_market(client, plugin_type="mcp")["market"])
    assert sum(counts.values()) == len(payload["market"])


def test_unknown_type_returns_empty_not_error(client):
    _login(client, "test-key-e10281")
    assert _market(client, plugin_type="rpa")["market"] == []


def test_six_states_unchanged_by_split(client, db_session):
    _login(client, "test-key-e10281")

    full = market_plugins(db_session, "DT-E10281", "E10281")
    skills = market_plugins(db_session, "DT-E10281", "E10281", plugin_type="skill")

    assert skills == [row for row in full if row["plugin_type"] == "skill"]
    # 六态枚举不变（拆分不得引入新状态）
    assert {row["state"] for row in full} <= {
        "own_draft", "enabled", "granted", "blocked", "pending", "requestable",
    }
    # 来源徽标：平台自带的共享资源一律 platform（SkillHub 导入的见 test_skillhub_api）
    assert {row["source"] for row in full} <= {"platform", "personal", "skillhub"}
