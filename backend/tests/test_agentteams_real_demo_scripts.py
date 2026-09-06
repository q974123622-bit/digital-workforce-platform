from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT = PROJECT_ROOT / "deploy" / "agentteams-matrix"
COMPOSE = DEPLOYMENT / "docker-compose.yml"
HOMESERVER = DEPLOYMENT / "homeserver.template.yaml"
BOOTSTRAP = PROJECT_ROOT / "scripts" / "bootstrap_agentteams_matrix.py"
START = PROJECT_ROOT / "scripts" / "start_agentteams_real_demo.ps1"
STOP = PROJECT_ROOT / "scripts" / "stop_agentteams_real_demo.ps1"
PROCESS_HELPERS = PROJECT_ROOT / "scripts" / "agentteams_process_helpers.ps1"
POWERSHELL_51 = shutil.which("powershell")


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


def test_compose_mounts_a_real_read_only_synapse_log_configuration():
    log_config = DEPLOYMENT / "log.config"
    assert log_config.is_file(), "Synapse log configuration is missing"
    compose = COMPOSE.read_text(encoding="utf-8")
    contents = log_config.read_text(encoding="utf-8")

    assert "./log.config:/data/matrix.log.config:ro" in compose
    assert "handlers:" in contents
    assert "root:" in contents


def test_compose_initializes_only_the_project_synapse_volume_before_running_as_synapse_user():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))

    init = compose["services"]["synapse-volume-init"]
    synapse = compose["services"]["synapse"]

    assert init["image"] == "matrixdotorg/synapse:v1.117.0"
    assert init["user"] == "0:0"
    assert init["restart"] == "no"
    assert init["volumes"] == ["dwp-agentteams-synapse-data:/data"]
    assert init["command"] == ["chown -R 991:991 /data && chmod 750 /data"]
    assert "777" not in init["command"][0]
    assert synapse["depends_on"]["synapse-volume-init"]["condition"] == "service_completed_successfully"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_start_cleanup_preserves_the_original_startup_error_when_scoped_compose_down_fails():
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "function docker { "
        "[Console]::WriteLine('docker=' + ($args -join ' ')); $global:LASTEXITCODE = 17 }; "
        f". '{START.as_posix()}'; "
        "try { try { throw 'startup-root-error' } finally { Stop-OwnedComposeProject } } "
        "catch { [Console]::WriteLine('caught=' + $_.Exception.Message) }"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    docker_call = next(line for line in completed.stdout.splitlines() if line.startswith("docker="))
    assert docker_call.startswith("docker=compose ")
    assert "-p dwp-agentteams" in docker_call
    assert "-f " in docker_call
    assert docker_call.endswith(" down")
    assert "caught=startup-root-error" in completed.stdout


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_strict_start_environment_enables_the_collaboration_loop():
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{START.as_posix()}'; "
        "Set-AgentTeamsStrictRuntimeEnvironment; "
        "[Console]::WriteLine($env:DWP_AGENTTEAMS_MODE + '|' + $env:DWP_AGENTTEAMS_COLLAB_ENABLED)"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "agentteams_strict|1"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_strict_start_loads_role_keys_from_a_synthetic_env_file_with_process_override(tmp_path):
    env_file = tmp_path / "role-keys.env"
    env_file.write_text(
        "DEEPSEEK_API_KEY_MANAGER=file-manager\n"
        "DEEPSEEK_API_KEY_HR=file-hr\n"
        "DEEPSEEK_API_KEY_IT=file-it\n"
        "DEEPSEEK_API_KEY_AUDIT=file-audit\n"
        "DEEPSEEK_API_KEY=legacy-file-key\n",
        encoding="utf-8",
    )
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{START.as_posix()}'; "
        f"$keys = Get-AgentRoleApiKeyValues -EnvFile '{env_file.as_posix()}' -ProcessEnvironment @{{ DEEPSEEK_API_KEY_HR = 'process-hr' }}; "
        "if ((@(Get-MissingAgentRoleApiKeys $keys).Count -ne 0) -or $keys.manager -ne 'file-manager' -or $keys.hr -ne 'process-hr' -or $keys.it -ne 'file-it' -or $keys.audit -ne 'file-audit') { throw 'role key loading failed' }; "
        "[Console]::WriteLine('role-key-load-ok')"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "role-key-load-ok"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_strict_start_reports_only_missing_role_names_for_role_key_preflight():
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{START.as_posix()}'; "
        "$missing = @(Get-MissingAgentRoleApiKeys @{ "
        "manager = 'manager-only'; "
        "hr = ''; "
        "it = 'it-only' "
        "}); "
        "[Console]::WriteLine($missing -join ',')"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "hr,audit"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_process_helper_blocks_dotenv_backfill_and_injects_only_one_child_key(tmp_path):
    capture = tmp_path / "capture_environment.py"
    output = tmp_path / "environment.json"
    env_file = tmp_path / "role-keys.env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=legacy-file-key\n"
        "DEEPSEEK_API_KEY_MANAGER=file-manager\n"
        "DEEPSEEK_API_KEY_HR=file-hr\n"
        "DEEPSEEK_API_KEY_IT=file-it\n"
        "DEEPSEEK_API_KEY_AUDIT=file-audit\n",
        encoding="utf-8",
    )
    capture.write_text(
        "import json, os, pathlib, sys\n"
        "from dotenv import load_dotenv\n"
        "names = ('DEEPSEEK_API_KEY', 'DEEPSEEK_API_KEY_MANAGER', 'DEEPSEEK_API_KEY_HR', 'DEEPSEEK_API_KEY_IT', 'DEEPSEEK_API_KEY_AUDIT')\n"
        "load_dotenv(sys.argv[1])\n"
        "pathlib.Path(sys.argv[2]).write_text(json.dumps({name: os.environ[name] for name in names if name in os.environ}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "$env:DEEPSEEK_API_KEY = 'legacy-parent'; "
        "$env:DEEPSEEK_API_KEY_MANAGER = 'manager-parent'; "
        "$env:DEEPSEEK_API_KEY_HR = 'hr-parent'; "
        "$env:DEEPSEEK_API_KEY_IT = 'it-parent'; "
        "$env:DEEPSEEK_API_KEY_AUDIT = 'audit-parent'; "
        f". '{PROCESS_HELPERS.as_posix()}'; "
        f"$started = Start-AgentTeamsOwnedProcess -ExecutablePath '{sys.executable}' -WorkingDirectory '{tmp_path.as_posix()}' -Arguments @('{capture.as_posix()}', '{env_file.as_posix()}', '{output.as_posix()}') -Environment @{{ DEEPSEEK_API_KEY_HR = 'hr-child' }}; "
        "$started.Process.WaitForExit(); "
        "if ($started.Process.ExitCode -ne 0) { throw 'capture failed' }"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "DEEPSEEK_API_KEY": "",
        "DEEPSEEK_API_KEY_MANAGER": "",
        "DEEPSEEK_API_KEY_HR": "hr-child",
        "DEEPSEEK_API_KEY_IT": "",
        "DEEPSEEK_API_KEY_AUDIT": "",
    }


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_process_helper_blocks_dotenv_backfill_for_backend_and_frontend_children(tmp_path):
    capture = tmp_path / "capture_environment.py"
    output = tmp_path / "environment.json"
    env_file = tmp_path / "role-keys.env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=legacy-file-key\n"
        "DEEPSEEK_API_KEY_MANAGER=file-manager\n"
        "DEEPSEEK_API_KEY_HR=file-hr\n"
        "DEEPSEEK_API_KEY_IT=file-it\n"
        "DEEPSEEK_API_KEY_AUDIT=file-audit\n",
        encoding="utf-8",
    )
    capture.write_text(
        "import json, os, pathlib, sys\n"
        "from dotenv import load_dotenv\n"
        "names = ('DEEPSEEK_API_KEY', 'DEEPSEEK_API_KEY_MANAGER', 'DEEPSEEK_API_KEY_HR', 'DEEPSEEK_API_KEY_IT', 'DEEPSEEK_API_KEY_AUDIT')\n"
        "load_dotenv(sys.argv[1])\n"
        "pathlib.Path(sys.argv[2]).write_text(json.dumps({name: os.environ[name] for name in names if name in os.environ}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "$env:DEEPSEEK_API_KEY_MANAGER = 'manager-parent'; "
        "$env:DEEPSEEK_API_KEY_HR = 'hr-parent'; "
        f". '{PROCESS_HELPERS.as_posix()}'; "
        f"$started = Start-AgentTeamsOwnedProcess -ExecutablePath '{sys.executable}' -WorkingDirectory '{tmp_path.as_posix()}' -Arguments @('{capture.as_posix()}', '{env_file.as_posix()}', '{output.as_posix()}'); "
        "$started.Process.WaitForExit(); "
        "if ($started.Process.ExitCode -ne 0) { throw 'capture failed' }"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "DEEPSEEK_API_KEY": "",
        "DEEPSEEK_API_KEY_MANAGER": "",
        "DEEPSEEK_API_KEY_HR": "",
        "DEEPSEEK_API_KEY_IT": "",
        "DEEPSEEK_API_KEY_AUDIT": "",
    }


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_process_record_whitelists_fields_and_never_serializes_an_accidental_key(tmp_path):
    runtime = tmp_path / "runtime"
    pids = runtime / "pids.json"
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{START.as_posix()}'; "
        f"$runtimeDirectory = '{runtime.as_posix()}'; $pidsPath = '{pids.as_posix()}'; "
        "$record = @{ name = 'hr'; process_id = [int]123; executable_path = 'python'; working_directory = 'workspace'; argv = @('--role', 'hr'); workspace = 'workspace'; started_at = '2026-09-06T00:00:00+00:00'; api_key = 'synthetic-key-must-not-persist' }; "
        "Write-ProcessRecord @($record) 'running'; "
        "[Console]::WriteLine([IO.File]::ReadAllText($pidsPath))"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert "synthetic-key-must-not-persist" not in completed.stdout
    assert '"api_key"' not in completed.stdout


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
    expected_schema = {
        "type": "object",
        "required": ["employee_ref"],
        "properties": {
            "employee_ref": {"type": "string", "const": "新员工A", "maxLength": 16},
        },
        "additionalProperties": False,
    }
    assert {
        role: runtime["agents"][role]["safe_tool_catalog"][0]["input_schema"]
        for role in ("hr", "it", "audit")
    } == {role: expected_schema for role in ("hr", "it", "audit")}
    assert set(runtime["team_catalog"]) == {"VE-0002", "VE-0003", "VE-0005"}

    from app.services.agentteams_runtime_config import load_strict_runtime_config

    loaded = load_strict_runtime_config(target, environ={})
    assert loaded.team_id == "TEAM-ONBOARD"
    assert set(loaded.agents) == {"manager", "hr", "it", "audit"}
    assert loaded.agents["hr"].safe_tool_catalog[0]["input_schema"] == expected_schema
    rendered = target.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "secret-" not in output
    assert "secret-" in rendered
    assert not list(tmp_path.glob("*.tmp"))


def test_bootstrap_refuses_a_reparse_runtime_ancestor_before_writing(tmp_path, monkeypatch):
    bootstrap = _bootstrap_module()

    def reject_reparse(_path, *, allow_missing=False):
        raise ValueError("invalid_runtime_config")

    monkeypatch.setattr(bootstrap, "require_safe_path", reject_reparse, raising=False)
    with pytest.raises(bootstrap.BootstrapError, match="invalid_runtime_path"):
        bootstrap.write_runtime_configuration(tmp_path / "runtime.json", {"safe": True})


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
    assert f"'--role','{role}'" in source
    assert "DWP_AGENTTEAMS_RUNTIME_CONFIG" in source
    assert "agentteams_process_helpers.ps1" in source
    assert "CreateNoWindow = $true" in PROCESS_HELPERS.read_text(encoding="utf-8")
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


def test_start_script_uses_space_safe_argument_list_and_rolls_back_only_owned_compose():
    source = START.read_text(encoding="utf-8")
    helpers = PROCESS_HELPERS.read_text(encoding="utf-8")

    assert "System.Diagnostics.ProcessStartInfo" in helpers
    assert ".ArgumentList.Add" in helpers
    assert "ConvertTo-AgentTeamsWindowsArgument" in helpers
    assert "function Start-OwnedProcess" in source
    assert "Start-Process" not in source
    assert "ownedCompose" in source
    assert "docker compose -p dwp-agentteams -f $composeFile down" in source
    assert "finally" in source
    assert source.index("if (-not $NoFrontend") < source.index("Test-CommandAvailable 'node'")


def test_no_frontend_path_skips_node_and_vite_preflight():
    source = START.read_text(encoding="utf-8")

    preflight = source[source.index("if (-not $NoFrontend)"):source.index("$alreadyRunning")]
    frontend_guard = preflight.index("if (-not $NoFrontend)")
    node_check = preflight.index("Test-CommandAvailable 'node'")
    vite_check = preflight.index("vite\\bin\\vite.js")
    assert frontend_guard < node_check < vite_check
    assert "if (-not $NoFrontend) { $records+=Start-OwnedProcess 'frontend'" in source
    assert "if ($NoFrontend -and $record.name -eq 'frontend') { continue }" in source


def test_stop_script_fails_closed_when_executable_arguments_or_start_time_do_not_match():
    source = STOP.read_text(encoding="utf-8")

    for required in ("executable_path", "argv", "started_at", "ExecutablePath", "CreationDate", "CommandLine"):
        assert required in source
    assert "Test-RecordedWorktreeProcess" in source
    assert "return $null" in source


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_process_helper_dot_source_does_not_change_callers_strict_mode():
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "Set-StrictMode -Off; "
        f". '{PROCESS_HELPERS.as_posix()}'; "
        "$value = $agentteamsStrictModeLeakProbe; "
        "if ($null -ne $value) { throw 'unexpected probe value' }; "
        "[Console]::WriteLine('strictmode-still-off')"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "strictmode-still-off"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_process_helper_round_trips_difficult_arguments_through_real_windows_powershell(tmp_path):
    """Exercise the PS5.1 fallback against CreateProcess, not a string mock."""
    capture = tmp_path / "capture argv.py"
    output = tmp_path / "captured.json"
    capture.write_text(
        "import json, pathlib, sys\npathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    arguments = [str(capture), str(output), "", "has space", 'embedded"quote', 'slashes\\\\"quote', "trailing\\"]
    literals = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{PROCESS_HELPERS.as_posix()}'; "
        f"$started = Start-AgentTeamsOwnedProcess -ExecutablePath '{sys.executable}' -WorkingDirectory '{tmp_path.as_posix()}' -Arguments @({literals}); "
        "$started.Process.WaitForExit(); "
        "if ($started.Process.ExitCode -ne 0) { throw 'capture failed' }; "
        "[Console]::WriteLine($started.UsedArgumentList)"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().lower() == "false", "powershell.exe must exercise the PS5.1 fallback"
    assert json.loads(output.read_text(encoding="utf-8")) == arguments[2:]


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_process_helper_uses_argument_list_when_the_property_is_available():
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{PROCESS_HELPERS.as_posix()}'; "
        "$list = [System.Collections.ArrayList]::new(); "
        "$info = [pscustomobject]@{ ArgumentList = $list; Arguments = '' }; "
        "$used = Set-AgentTeamsProcessArguments -Info $info -Arguments @('', 'space value', 'quote\"value', 'trailing\\'); "
        "if (-not $used -or @($list).Count -ne 4 -or $list[0] -ne '' -or $list[1] -ne 'space value' -or $list[2] -ne 'quote\"value' -or $list[3] -ne 'trailing\\') { throw 'ArgumentList not preserved' }; "
        "[Console]::WriteLine('argument-list-ok')"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "argument-list-ok"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_start_and_stop_accept_cim_creation_date_already_materialized_as_datetime():
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{START.as_posix()}'; "
        "$started = [DateTime]::UtcNow.AddSeconds(-1); "
        "$record = [pscustomobject]@{ name='backend'; process_id=[int]41001; executable_path=$python; working_directory=$backend; argv=@(Get-ExpectedArguments 'backend'); workspace=$workspace; started_at=$started.ToString('o') }; "
        "$line = ('\"' + $python + '\" ' + ((Get-ExpectedArguments 'backend') -join ' ') + ' ' + $workspace); "
        "$global:fakeProcess = [pscustomobject]@{ ProcessId=[int]41001; ParentProcessId=[int]1; ExecutablePath=$python; CommandLine=$line; CreationDate=$started }; "
        "function Get-CimInstance { return $global:fakeProcess }; "
        "$startAccepted = Test-OwnedRecordedProcess $record; "
        f". '{STOP.as_posix()}'; "
        "$stopAccepted = $null -ne (Test-RecordedWorktreeProcess $record); "
        "[Console]::WriteLine([string]$startAccepted + '|' + [string]$stopAccepted)"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().lower() == "true|true"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_process_time_converter_accepts_both_datetime_and_dmtf_values():
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{PROCESS_HELPERS.as_posix()}'; "
        "$date = [DateTime]::Now.AddSeconds(-1); "
        "$dmtf = [System.Management.ManagementDateTimeConverter]::ToDmtfDateTime($date); "
        "$fromDate = ConvertTo-AgentTeamsUtcDateTime $date; $fromDmtf = ConvertTo-AgentTeamsUtcDateTime $dmtf; "
        "if ([Math]::Abs(($fromDate-$fromDmtf).TotalSeconds) -gt 0.001) { throw 'time conversion mismatch' }; "
        "[Console]::WriteLine('datetime-dmtf-ok')"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "datetime-dmtf-ok"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_start_captures_a_proven_direct_backend_child_that_owns_port_8000():
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{START.as_posix()}'; "
        "$rootStart = [DateTime]::UtcNow.AddSeconds(-2); $childStart = $rootStart.AddMilliseconds(300); "
        "$args = @(Get-ExpectedArguments 'backend'); $line = ('\"' + $python + '\" ' + ($args -join ' ') + ' ' + $workspace); "
        "$record = @{ name='backend'; process_id=[int]42001; executable_path=$python; working_directory=$backend; argv=$args; workspace=$workspace; started_at=$rootStart.ToString('o') }; "
        "$global:rootProcess = [pscustomobject]@{ ProcessId=[int]42001; ParentProcessId=[int]1; ExecutablePath=$python; CommandLine=$line; CreationDate=$rootStart }; "
        "$global:childProcess = [pscustomobject]@{ ProcessId=[int]42002; ParentProcessId=[int]42001; ExecutablePath='D:\\python\\python.exe'; CommandLine=$line; CreationDate=$childStart }; "
        "function Get-CimInstance { param($ClassName, $Filter); if ($Filter -match '42002') { return $global:childProcess }; return $global:rootProcess }; "
        "function Get-NetTCPConnection { return [pscustomobject]@{ OwningProcess=[int]42002; LocalPort=[int]8000; State='Listen' } }; "
        "$listener = Get-OwnedBackendListenerRecord $record; "
        "[Console]::WriteLine([string]$listener.process_id + '|' + [string]$listener.parent_process_id + '|' + [string]$listener.port)"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "42002|42001|8000"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_startup_rollback_stops_the_proven_backend_listener_before_its_launcher():
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{START.as_posix()}'; "
        "$rootStart = [DateTime]::UtcNow.AddSeconds(-2); $childStart = $rootStart.AddMilliseconds(300); "
        "$args = @(Get-ExpectedArguments 'backend'); $line = ('\"' + $python + '\" ' + ($args -join ' ') + ' ' + $workspace); "
        "$record = [pscustomobject]@{ name='backend'; process_id=[int]42001; executable_path=$python; working_directory=$backend; argv=$args; workspace=$workspace; started_at=$rootStart.ToString('o'); listener=[pscustomobject]@{ process_id=[int]42002; parent_process_id=[int]42001; executable_path='D:\\python\\python.exe'; started_at=$childStart.ToString('o'); port=[int]8000 } }; "
        "$global:rootProcess = [pscustomobject]@{ ProcessId=[int]42001; ParentProcessId=[int]1; ExecutablePath=$python; CommandLine=$line; CreationDate=$rootStart }; "
        "$global:childProcess = [pscustomobject]@{ ProcessId=[int]42002; ParentProcessId=[int]42001; ExecutablePath='D:\\python\\python.exe'; CommandLine=$line; CreationDate=$childStart }; "
        "function Get-CimInstance { param($ClassName, $Filter); if ($Filter -match '42002') { return $global:childProcess }; return $global:rootProcess }; "
        "function Get-NetTCPConnection { return [pscustomobject]@{ OwningProcess=[int]42002; LocalPort=[int]8000; State='Listen' } }; "
        "$global:stopped = @(); function Stop-Process { param([int]$Id); $global:stopped += $Id }; "
        "Stop-OnlyOwnedRecords @($record); [Console]::WriteLine($global:stopped -join ',')"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "42002,42001"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_legacy_launcher_only_record_can_prove_its_current_backend_listener_child():
    common = (
        "$rootStart = [DateTime]::UtcNow.AddSeconds(-2); $childStart = $rootStart.AddMilliseconds(300); "
        "$args = @(Get-ExpectedArguments 'backend'); $line = ('\"' + $python + '\" ' + ($args -join ' ') + ' ' + $workspace); "
        "$record = [pscustomobject]@{ name='backend'; process_id=[int]42001; executable_path=$python; working_directory=$backend; argv=$args; workspace=$workspace; started_at=$rootStart.ToString('o') }; "
        "$global:rootProcess = [pscustomobject]@{ ProcessId=[int]42001; ParentProcessId=[int]1; ExecutablePath=$python; CommandLine=$line; CreationDate=$rootStart }; "
        "$global:childProcess = [pscustomobject]@{ ProcessId=[int]42002; ParentProcessId=[int]42001; ExecutablePath='D:\\python\\python.exe'; CommandLine=$line; CreationDate=$childStart }; "
        "function Get-CimInstance { param($ClassName, $Filter); if ($Filter -match '42002') { return $global:childProcess }; return $global:rootProcess }; "
        "function Get-NetTCPConnection { return [pscustomobject]@{ OwningProcess=[int]42002; LocalPort=[int]8000; State='Listen' } }; "
    )
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{START.as_posix()}'; " + common +
        "$startIds = @(Get-StartProvenProcessIds $record); "
        f". '{STOP.as_posix()}'; " + common +
        "$stopIds = @(Get-ProvenRecordedProcessIds $record); "
        "[Console]::WriteLine(($startIds -join ',') + '|' + ($stopIds -join ','))"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "42002,42001|42002,42001"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_startup_rollback_still_stops_a_proven_launcher_when_listener_probe_fails():
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{START.as_posix()}'; "
        "$rootStart = [DateTime]::UtcNow.AddSeconds(-1); $args = @(Get-ExpectedArguments 'backend'); "
        "$line = ('\"' + $python + '\" ' + ($args -join ' ') + ' ' + $workspace); "
        "$record = @{ name='backend'; process_id=[int]43001; executable_path=$python; working_directory=$backend; argv=$args; workspace=$workspace; started_at=$rootStart.ToString('o') }; "
        "$global:rootProcess = [pscustomobject]@{ ProcessId=[int]43001; ParentProcessId=[int]1; ExecutablePath=$python; CommandLine=$line; CreationDate=$rootStart }; "
        "function Get-CimInstance { return $global:rootProcess }; function Get-NetTCPConnection { return @() }; "
        "$ids = @(Get-StartProvenProcessIds $record); [Console]::WriteLine($ids -join ',')"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "43001"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_startup_rollback_reads_listener_metadata_from_the_real_hashtable_record_shape():
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{START.as_posix()}'; "
        "$rootStart = [DateTime]::UtcNow.AddSeconds(-2); $childStart = $rootStart.AddMilliseconds(300); $args = @(Get-ExpectedArguments 'backend'); "
        "$line = ('\"' + $python + '\" ' + ($args -join ' ') + ' ' + $workspace); "
        "$record = @{ name='backend'; process_id=[int]42001; executable_path=$python; working_directory=$backend; argv=$args; workspace=$workspace; started_at=$rootStart.ToString('o'); listener=@{ process_id=[int]49999; parent_process_id=[int]42001; executable_path='D:\\python\\python.exe'; started_at=$childStart.ToString('o'); port=[int]8000 } }; "
        "$global:rootProcess = [pscustomobject]@{ ProcessId=[int]42001; ParentProcessId=[int]1; ExecutablePath=$python; CommandLine=$line; CreationDate=$rootStart }; "
        "$global:childProcess = [pscustomobject]@{ ProcessId=[int]42002; ParentProcessId=[int]42001; ExecutablePath='D:\\python\\python.exe'; CommandLine=$line; CreationDate=$childStart }; "
        "function Get-CimInstance { param($ClassName, $Filter); if ($Filter -match '42002') { return $global:childProcess }; return $global:rootProcess }; "
        "function Get-NetTCPConnection { return [pscustomobject]@{ OwningProcess=[int]42002; LocalPort=[int]8000; State='Listen' } }; "
        "$ids = @(Get-StartProvenProcessIds $record); [Console]::WriteLine($ids.Count)"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "0"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
def test_stop_proves_recorded_backend_listener_child_before_returning_exact_stop_order():
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{STOP.as_posix()}'; "
        "$rootStart = [DateTime]::UtcNow.AddSeconds(-2); $childStart = $rootStart.AddMilliseconds(300); "
        "$args = @(Get-ExpectedArguments 'backend'); $line = ('\"' + $python + '\" ' + ($args -join ' ') + ' ' + $workspace); "
        "$record = [pscustomobject]@{ name='backend'; process_id=[int]42001; executable_path=$python; working_directory=$backend; argv=$args; workspace=$workspace; started_at=$rootStart.ToString('o'); listener=[pscustomobject]@{ process_id=[int]42002; parent_process_id=[int]42001; executable_path='D:\\python\\python.exe'; started_at=$childStart.ToString('o'); port=[int]8000 } }; "
        "$global:rootProcess = [pscustomobject]@{ ProcessId=[int]42001; ParentProcessId=[int]1; ExecutablePath=$python; CommandLine=$line; CreationDate=$rootStart }; "
        "$global:childProcess = [pscustomobject]@{ ProcessId=[int]42002; ParentProcessId=[int]42001; ExecutablePath='D:\\python\\python.exe'; CommandLine=$line; CreationDate=$childStart }; "
        "function Get-CimInstance { param($ClassName, $Filter); if ($Filter -match '42002') { return $global:childProcess }; return $global:rootProcess }; "
        "function Get-NetTCPConnection { return [pscustomobject]@{ OwningProcess=[int]42002; LocalPort=[int]8000; State='Listen' } }; "
        "$ids = @(Get-ProvenRecordedProcessIds $record); [Console]::WriteLine($ids -join ',')"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "42002,42001"


@pytest.mark.skipif(POWERSHELL_51 is None, reason="Windows PowerShell 5.1 is required")
@pytest.mark.parametrize(
    ("child_parent", "port_owner", "child_executable"),
    (
        (99999, 42002, r"D:\python\python.exe"),
        (42001, 99999, r"D:\python\python.exe"),
        (42001, 42002, r"D:\unexpected\python.exe"),
    ),
)
def test_stop_rejects_backend_child_when_parent_or_listening_port_cannot_be_proven(
    child_parent, port_owner, child_executable,
):
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{STOP.as_posix()}'; "
        "$rootStart = [DateTime]::UtcNow.AddSeconds(-2); $childStart = $rootStart.AddMilliseconds(300); "
        "$args = @(Get-ExpectedArguments 'backend'); $line = ('\"' + $python + '\" ' + ($args -join ' ') + ' ' + $workspace); "
        f"$record = [pscustomobject]@{{ name='backend'; process_id=[int]42001; executable_path=$python; working_directory=$backend; argv=$args; workspace=$workspace; started_at=$rootStart.ToString('o'); listener=[pscustomobject]@{{ process_id=[int]42002; parent_process_id=[int]42001; executable_path='{child_executable}'; started_at=$childStart.ToString('o'); port=[int]8000 }} }}; "
        "$global:rootProcess = [pscustomobject]@{ ProcessId=[int]42001; ParentProcessId=[int]1; ExecutablePath=$python; CommandLine=$line; CreationDate=$rootStart }; "
        f"$global:childProcess = [pscustomobject]@{{ ProcessId=[int]42002; ParentProcessId=[int]{child_parent}; ExecutablePath='{child_executable}'; CommandLine=$line; CreationDate=$childStart }}; "
        "function Get-CimInstance { param($ClassName, $Filter); if ($Filter -match '42002') { return $global:childProcess }; return $global:rootProcess }; "
        f"function Get-NetTCPConnection {{ return [pscustomobject]@{{ OwningProcess=[int]{port_owner}; LocalPort=[int]8000; State='Listen' }} }}; "
        "$ids = @(Get-ProvenRecordedProcessIds $record); [Console]::WriteLine($ids.Count)"
    )
    completed = subprocess.run(
        [POWERSHELL_51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "0"
