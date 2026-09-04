from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.agentteams_agent_runtime import AgentRuntimeConfig, heartbeat_signature
from app.services.agentteams_runtime_config import load_strict_runtime_config
from app.services.agentteams_runner import AgentTeamsCollaborationRunner


def _runtime_document() -> dict:
    return {
        "matrix_url": "https://matrix.test", "room_id": "!room:test", "team_id": "TEAM-DYNAMIC",
        "platform": {"mxid": "@platform:test", "token": "platform-token"},
        "agents": {
            "manager": {"agent_id": "MGR-1", "mxid": "@manager:test", "token": "manager-token"},
            "hr": {"agent_id": "HR-1", "mxid": "@hr:test", "token": "hr-token"},
            "it": {"agent_id": "IT-1", "mxid": "@it:test", "token": "it-token"},
            "audit": {"agent_id": "AUD-1", "mxid": "@audit:test", "token": "audit-token"},
        },
        "team_catalog": {},
    }


def test_runtime_contract_requires_team_and_rejects_windows_device_agent_ids(tmp_path: Path):
    path = tmp_path / "runtime.json"
    document = _runtime_document()
    document.pop("team_id")
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_runtime_config"):
        load_strict_runtime_config(path)
    document = _runtime_document()
    document["agents"]["manager"]["agent_id"] = "CON"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_runtime_config"):
        load_strict_runtime_config(path)


def test_heartbeat_signature_binds_identity_and_timestamp():
    signature = heartbeat_signature("MGR-1", "@manager:test", "2026-09-04T00:00:00+00:00", "manager-token")
    assert signature != heartbeat_signature("MGR-1", "@other:test", "2026-09-04T00:00:00+00:00", "manager-token")
    with pytest.raises(ValueError):
        AgentRuntimeConfig(
            agent_id="..", role="manager", matrix_mxid="@manager:test", matrix_token="token",
            room_id="!room:test", trusted_sender_mxids={"platform": "@platform:test", "..": "@manager:test"},
            allowed_skill_ids={"agentteams-onboarding-readiness"},
        )


def test_runner_defaults_to_strict_transport_mode_without_local_demo_imports():
    class Gateway:
        def close(self):
            pass
    assert AgentTeamsCollaborationRunner(gateway=Gateway()).transport_mode == "agentteams_strict"


def test_unknown_agentteams_mode_fails_closed_as_strict(monkeypatch):
    from app.services import config
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "typo")
    assert config.agentteams_mode() == "agentteams_strict"
