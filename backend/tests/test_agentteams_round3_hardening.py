from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.agentteams_agent_runtime import AgentRuntimeConfig
from app.services.agentteams_agent_runtime import AgentRuntime
from app.services.agentteams_gateway import MatrixSyncBatch
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


def test_state_save_never_reuses_legacy_fixed_tmp_sentinel(tmp_path: Path):
    class Gateway:
        def sync_messages(self, *_args): return MatrixSyncBatch(events=[], next_cursor="c1")
        def close(self): pass
    state = tmp_path / "state"; state.mkdir()
    sentinel = state / "HR-1.json.tmp"; sentinel.write_text("do-not-touch", encoding="utf-8")
    config = AgentRuntimeConfig(agent_id="HR-1", role="hr", matrix_mxid="@hr:test", matrix_token="token", room_id="!r", trusted_sender_mxids={"platform": "@p", "HR-1": "@hr:test"}, allowed_skill_ids={"hr-onboarding-assistant"}, state_dir=state)
    runtime = AgentRuntime(config, Gateway(), object(), pause=lambda _: None)
    runtime.run_once()
    assert sentinel.read_text(encoding="utf-8") == "do-not-touch"


def _runtime(state_dir: Path, *, safe_tool_catalog: list[dict[str, str]] | None = None) -> AgentRuntime:
    class Gateway:
        def sync_messages(self, *_args): return MatrixSyncBatch(events=[], next_cursor="c1")
        def close(self): pass
    config = AgentRuntimeConfig(
        agent_id="HR-1", role="hr", matrix_mxid="@hr:test", matrix_token="token",
        room_id="!r", trusted_sender_mxids={"platform": "@p", "HR-1": "@hr:test"},
        allowed_skill_ids={"hr-onboarding-assistant"}, state_dir=state_dir,
        safe_tool_catalog=safe_tool_catalog or [],
    )
    return AgentRuntime(config, Gateway(), object(), pause=lambda _: None)


def test_state_read_rechecks_path_identity_after_read(monkeypatch, tmp_path: Path):
    """A replacement after open must not be trusted merely because open was safe."""
    import app.services.agentteams_agent_runtime as subject

    state_dir = tmp_path / "state"; state_dir.mkdir()
    state_path = state_dir / "HR-1.json"
    state_path.write_text(json.dumps({"cursor": "safe", "processed_event_ids": [], "processed_idempotency_keys": [], "tasks": {}}), encoding="utf-8")
    runtime = _runtime(state_dir)
    original_lstat = subject.os.lstat
    calls = 0

    def changing_lstat(path):
        nonlocal calls
        metadata = original_lstat(path)
        if Path(path) == state_path:
            calls += 1
            # The first lookup is require_safe_path; the second is the
            # open-time identity check.  Only a post-read recheck sees this.
            if calls == 3:
                return SimpleNamespace(
                    st_mode=metadata.st_mode, st_file_attributes=0,
                    st_dev=metadata.st_dev, st_ino=metadata.st_ino + 1,
                )
        return metadata

    monkeypatch.setattr(subject.os, "lstat", changing_lstat)

    assert runtime._load_state()["cursor"] is None


def test_state_save_refuses_detectable_replace_race_without_deleting_new_target(monkeypatch, tmp_path: Path):
    """If target identity changes at replace time, the foreign file remains untouched."""
    import app.services.agentteams_agent_runtime as subject

    state_dir = tmp_path / "state"; state_dir.mkdir()
    runtime = _runtime(state_dir)
    foreign = runtime._state_path
    original_replace = subject.os.replace

    def raced_replace(_source, destination):
        Path(destination).write_text("foreign", encoding="utf-8")
        # Leave the source in place; production code must reject the new target.

    monkeypatch.setattr(subject.os, "replace", raced_replace)
    with pytest.raises(ValueError, match="invalid_runtime_config"):
        runtime._save_state()
    assert foreign.read_text(encoding="utf-8") == "foreign"
    # Restore before pytest's monkeypatch teardown so no unexpected side effects leak.
    monkeypatch.setattr(subject.os, "replace", original_replace)


def test_state_save_rechecks_unique_tmp_identity_before_replace(monkeypatch, tmp_path: Path):
    """A unique O_EXCL name is still rejected if its identity changes before replace."""
    import app.services.agentteams_agent_runtime as subject

    state_dir = tmp_path / "state"; state_dir.mkdir()
    runtime = _runtime(state_dir)
    original_lstat = subject.os.lstat

    def changed_tmp_lstat(path):
        metadata = original_lstat(path)
        if Path(path).parent == state_dir and Path(path).name.startswith(".HR-1."):
            return SimpleNamespace(
                st_mode=metadata.st_mode, st_file_attributes=0,
                st_dev=metadata.st_dev, st_ino=metadata.st_ino + 1,
            )
        return metadata

    monkeypatch.setattr(subject.os, "lstat", changed_tmp_lstat)
    monkeypatch.setattr(subject.os, "replace", lambda *_args: pytest.fail("replace must not run"))

    with pytest.raises(ValueError, match="invalid_runtime_config"):
        runtime._save_state()
    assert not runtime._state_path.exists()


def test_state_save_rejects_reparse_target_after_replace(monkeypatch, tmp_path: Path):
    """A final target marked as a Windows reparse point is never accepted."""
    import app.services.agentteams_agent_runtime as subject

    state_dir = tmp_path / "state"; state_dir.mkdir()
    runtime = _runtime(state_dir)
    original_lstat = subject.os.lstat

    def reparse_final_lstat(path):
        metadata = original_lstat(path)
        if Path(path) == runtime._state_path:
            return SimpleNamespace(
                st_mode=metadata.st_mode, st_file_attributes=0x400,
                st_dev=metadata.st_dev, st_ino=metadata.st_ino,
            )
        return metadata

    monkeypatch.setattr(subject.os, "lstat", reparse_final_lstat)
    with pytest.raises(ValueError, match="invalid_runtime_config"):
        runtime._save_state()


def test_runtime_never_emits_an_empty_tool_action(tmp_path: Path):
    runtime = _runtime(
        tmp_path / "state",
        safe_tool_catalog=[{"name": "lookup", "plugin_id": "hr-employee-mcp", "action": ""}],
    )

    assert runtime._tool("lookup") is None
