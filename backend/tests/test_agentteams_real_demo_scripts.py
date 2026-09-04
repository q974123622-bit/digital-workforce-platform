from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT = PROJECT_ROOT / "deploy" / "agentteams-matrix"
COMPOSE = DEPLOYMENT / "docker-compose.yml"
HOMESERVER = DEPLOYMENT / "homeserver.template.yaml"
BOOTSTRAP = PROJECT_ROOT / "scripts" / "bootstrap_agentteams_matrix.py"
START = PROJECT_ROOT / "scripts" / "start_agentteams_real_demo.ps1"
STOP = PROJECT_ROOT / "scripts" / "stop_agentteams_real_demo.ps1"


def _bootstrap_module():
    assert BOOTSTRAP.is_file(), "Task 7 bootstrap script is missing"
    spec = importlib.util.spec_from_file_location("agentteams_bootstrap", BOOTSTRAP)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_compose_contract_is_private_pinned_and_persistent():
    assert COMPOSE.is_file(), "Task 7 compose file is missing"
    assert HOMESERVER.is_file(), "Task 7 homeserver template is missing"
    compose = COMPOSE.read_text(encoding="utf-8")
    homeserver = HOMESERVER.read_text(encoding="utf-8")

    assert "matrixdotorg/synapse:v1.117.0" in compose
    assert "127.0.0.1:8008:8008" in compose
    assert "dwp-agentteams-synapse-data" in compose
    assert "COMPOSE_PROJECT_NAME=dwp-agentteams" in compose
    assert "enable_registration: false" in homeserver
    assert "federation_domain_whitelist: []" in homeserver
    assert "registration_shared_secret_path:" in homeserver
    assert "SYNAPSE_REGISTRATION_SHARED_SECRET" not in compose


def test_bootstrap_produces_exactly_five_strict_identities_and_redacts_secrets(tmp_path, capsys):
    bootstrap = _bootstrap_module()
    credentials = {
        name: {"mxid": f"@{name}:matrix.local", "token": f"secret-{name}"}
        for name in ("platform", "manager", "hr", "it", "audit")
    }

    runtime = bootstrap.build_runtime_configuration(credentials, "!private:matrix.local")
    target = tmp_path / "runtime.json"
    bootstrap.write_runtime_configuration(target, runtime)
    bootstrap.print_bootstrap_success(runtime)

    assert set(credentials) == {"platform", "manager", "hr", "it", "audit"}
    assert runtime["team_id"] == "TEAM-ONBOARD"
    assert set(runtime["agents"]) == {"manager", "hr", "it", "audit"}
    assert runtime["agents"]["manager"]["allowed_skill_ids"] == ["agentteams-onboarding-readiness"]
    assert runtime["agents"]["manager"]["safe_tool_catalog"] == []
    assert all(runtime["agents"][role]["safe_tool_catalog"] for role in ("hr", "it", "audit"))
    assert set(runtime["team_catalog"]) == {"VE-0002", "VE-0003", "VE-0005"}

    from app.services.agentteams_runtime_config import load_strict_runtime_config

    loaded = load_strict_runtime_config(target, environ={})
    assert loaded.team_id == "TEAM-ONBOARD"
    assert set(loaded.agents) == {"manager", "hr", "it", "audit"}
    rendered = target.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "secret-" not in output
    assert "secret-" in rendered
    assert not list(tmp_path.glob("*.tmp"))


def test_bootstrap_is_idempotent_and_verifies_private_membership():
    bootstrap = _bootstrap_module()

    class FakeMatrix:
        def __init__(self):
            self.accounts = {}
            self.room_id = "!private:matrix.local"
            self.members = set()
            self.register_calls = []

        def ensure_account(self, name, password):
            self.register_calls.append((name, password))
            return self.accounts.setdefault(name, {"mxid": f"@{name}:matrix.local", "token": f"token-{name}"})

        def ensure_private_room(self, platform, aliases):
            assert platform["mxid"] == "@platform:matrix.local"
            return self.room_id

        def invite_and_join(self, room_id, identity):
            assert room_id == self.room_id
            self.members.add(identity["mxid"])

        def joined_members(self, room_id, platform):
            assert room_id == self.room_id
            return self.members

    matrix = FakeMatrix()
    first = bootstrap.bootstrap_matrix(matrix, password_factory=lambda name: f"pw-{name}")
    second = bootstrap.bootstrap_matrix(matrix, password_factory=lambda name: f"pw-{name}")

    expected = {"@platform:matrix.local", "@manager:matrix.local", "@hr:matrix.local", "@it:matrix.local", "@audit:matrix.local"}
    assert first["room_id"] == second["room_id"] == "!private:matrix.local"
    assert matrix.members == expected
    assert len(matrix.register_calls) == 10


@pytest.mark.parametrize("role", ("manager", "hr", "it", "audit"))
def test_start_script_launches_each_named_role_with_the_single_runtime_path(role):
    assert START.is_file(), "Task 7 start script is missing"
    source = START.read_text(encoding="utf-8")
    assert f"--role {role}" in source
    assert "DWP_AGENTTEAMS_RUNTIME_CONFIG" in source
    assert "-WindowStyle Hidden" in source
    assert ".agentteams-runtime\\pids.json" in source


def test_stop_script_targets_only_proven_recorded_processes_and_named_compose_project():
    assert STOP.is_file(), "Task 7 stop script is missing"
    source = STOP.read_text(encoding="utf-8")
    assert ".agentteams-runtime\\pids.json" in source
    assert "Get-CimInstance Win32_Process" in source
    assert "Stop-Process -Id" in source
    assert "docker compose -p dwp-agentteams" in source
    assert " down" in source
    lowered = source.lower()
    assert "get-process python" not in lowered
    assert "taskkill" not in lowered
    assert " down -v" not in lowered
