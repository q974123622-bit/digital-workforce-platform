from __future__ import annotations

import json

import pytest


def _runtime_config() -> dict:
    return {
        "matrix_url": "http://matrix.test",
        "room_id": "!team:test",
        "team_id": "TEAM-ONBOARD",
        "platform": {"mxid": "@platform:test", "token": "platform-token"},
        "agents": {
            "manager": {"agent_id": "VE-0001", "mxid": "@manager:test", "token": "manager-token"},
            "hr": {"agent_id": "VE-0002", "mxid": "@hr:test", "token": "hr-token"},
            "it": {"agent_id": "VE-0003", "mxid": "@it:test", "token": "it-token"},
            "audit": {"agent_id": "VE-0005", "mxid": "@audit:test", "token": "audit-token"},
        },
        "team_catalog": {},
    }


def test_runtime_config_has_five_unique_identities_and_a_secret_free_public_view(tmp_path):
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(_runtime_config()), encoding="utf-8")

    runtime = load_strict_runtime_config(path, environ={})

    assert runtime.platform.mxid == "@platform:test"
    assert set(runtime.agents) == {"manager", "hr", "it", "audit"}
    public = runtime.public_view()
    assert "token" not in json.dumps(public)
    assert public["platform_mxid"] == "@platform:test"


@pytest.mark.parametrize("mutate", [
    lambda value: value["agents"]["hr"].update(token="manager-token"),
    lambda value: value["agents"]["hr"].update(mxid="@manager:test"),
    lambda value: value["agents"]["audit"].pop("token"),
])
def test_runtime_config_fails_closed_for_duplicate_or_missing_credentials(tmp_path, mutate):
    from app.services.agentteams_runtime_config import load_strict_runtime_config

    value = _runtime_config()
    mutate(value)
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid_runtime_config"):
        load_strict_runtime_config(path, environ={})
