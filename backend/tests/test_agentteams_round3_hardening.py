from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.agentteams_agent_runtime import AgentRuntimeConfig
from app.services.agentteams_runtime_config import load_strict_runtime_config


def _document() -> dict:
    skills = {
        "manager": "agentteams-onboarding-readiness", "hr": "hr-onboarding-assistant",
        "it": "it-service-assistant", "audit": "audit-evidence-review",
    }
    return {"matrix_url": "https://matrix.test", "room_id": "!room:test", "team_id": "TEAM-X",
            "platform": {"mxid": "@platform:test", "token": "platform"}, "team_catalog": {},
            "agents": {role: {"agent_id": role.upper() + "-1", "mxid": f"@{role}:test", "token": role + "-token", "allowed_skill_ids": [skill], "safe_tool_catalog": [{"name": "lookup", "plugin_id": "hr-employee-mcp", "action": "read"}]} for role, skill in skills.items()}}


def test_loader_rejects_symlink_authority_and_state_ancestor(tmp_path: Path):
    real = tmp_path / "real.json"; real.write_text(json.dumps(_document()), encoding="utf-8")
    link = tmp_path / "runtime.json"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError):
        load_strict_runtime_config(link)
    with pytest.raises(ValueError):
        AgentRuntimeConfig(agent_id="HR-1", role="hr", matrix_mxid="@hr:test", matrix_token="x", room_id="!r", trusted_sender_mxids={"platform": "@p", "HR-1": "@hr:test"}, allowed_skill_ids={"hr-onboarding-assistant"}, state_dir=link / "state")


def test_reparse_attribute_is_fail_closed(monkeypatch, tmp_path: Path):
    import app.services.agentteams_runtime_config as subject
    target = tmp_path / "runtime.json"; target.write_text(json.dumps(_document()), encoding="utf-8")
    original = Path.lstat
    class Reparse:
        st_mode = original(target).st_mode
        st_file_attributes = 0x400
    monkeypatch.setattr(Path, "lstat", lambda self: Reparse() if self == target else original(self))
    with pytest.raises(ValueError):
        subject.load_strict_runtime_config(target)
