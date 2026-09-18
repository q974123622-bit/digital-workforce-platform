from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
from threading import Thread

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "run_agentteams_demo.ps1"
REAL_START_SCRIPT = PROJECT_ROOT / "scripts" / "start_agentteams_real_demo.ps1"
ENV_EXAMPLE = PROJECT_ROOT / "backend" / ".env.example"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required to parse the demo script")
def test_agentteams_demo_script_is_parseable_and_avoids_mutating_setup_commands():
    """Catch a script regression that would install, reset, or create services."""
    source = SCRIPT.read_text(encoding="utf-8")
    parse = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-Command",
            "[scriptblock]::Create((Get-Content -LiteralPath 'scripts/run_agentteams_demo.ps1' -Raw)) | Out-Null",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert parse.returncode == 0, parse.stderr
    assert "param([switch]$NoFrontend, [switch]$Strict)" in source
    assert "$ErrorActionPreference = 'Stop'" in source
    assert "$env:DWP_AGENTTEAMS_COLLAB_ENABLED = '1'" in source
    assert "$env:DWP_SKIP_AUTO_SEED = '1'" in source
    assert "$databasePath = Join-Path $backend 'dwp.db'" in source
    assert "database initialization is an explicit, separately approved operation" in source.lower()
    assert "if (-not (Test-Path -LiteralPath $databasePath))" in source
    assert "process environment can provide configuration" in source
    assert "$realPreflightReady = Test-CollaborationPreflightReady -Health $collaborationHealth" in source
    assert "Start-Process" in source
    assert "-WindowStyle Hidden" in source
    assert "-RedirectStandardOutput" in source
    assert "-RedirectStandardError" in source
    lowered = source.lower()
    assert "pnpm install" not in lowered
    assert "pip install" not in lowered
    assert "docker compose" not in lowered
    assert "--reset" not in lowered


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required to probe Synapse readiness")
def test_real_demo_synapse_probe_bypasses_host_proxy_for_loopback():
    """A configured HTTP proxy must not intercept the local Synapse readiness probe."""
    class ReadyHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler contract
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):  # noqa: A002 - stdlib handler contract
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ReadyHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = os.environ.copy()
    environment.update({"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""})
    try:
        probe = subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                (
                    f". '{REAL_START_SCRIPT.as_posix()}'; "
                    f"if (-not (Wait-SynapseReady -BaseUri 'http://127.0.0.1:{server.server_port}')) "
                    "{ throw 'local Synapse probe was intercepted' }"
                ),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert probe.returncode == 0, probe.stderr


def test_local_demo_health_probes_bypass_host_proxy():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "function Invoke-AgentTeamsLocalGet" in source
    assert "$request.Proxy = $null" in source
    assert "Invoke-AgentTeamsLocalGet -Uri $Uri -TimeoutSeconds 2" in source
    assert "Invoke-AgentTeamsLocalGet -Uri $collaborationHealthUrl -TimeoutSeconds 10" in source


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required to probe demo mode selection")
@pytest.mark.parametrize(
    ("strict", "expected_mode"),
    [(False, "local_demo"), (True, "agentteams_strict")],
)
def test_demo_script_selects_process_scoped_transport_mode(strict: bool, expected_mode: str):
    """Catch default local mode or -Strict mode leaking to the wrong transport."""
    strict_literal = "$true" if strict else "$false"
    probe = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$ErrorActionPreference = 'Stop'; "
                f". '{SCRIPT.as_posix()}'; "
                "$before = [Environment]::GetEnvironmentVariable('DWP_AGENTTEAMS_MODE', 'User'); "
                f"Set-AgentTeamsDemoProcessEnvironment -StrictMode {strict_literal}; "
                f"if ($env:DWP_AGENTTEAMS_MODE -ne '{expected_mode}') {{ throw 'wrong process mode' }}; "
                "$after = [Environment]::GetEnvironmentVariable('DWP_AGENTTEAMS_MODE', 'User'); "
                "if ($before -ne $after) { throw 'mode escaped process scope' }; "
                "[Console]::WriteLine($env:DWP_AGENTTEAMS_MODE)"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == expected_mode


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required to probe the demo preflight function")
def test_dot_sourced_script_exposes_health_only_preflight_function():
    """Dot-sourcing must return before startup operations and expose only pure readiness logic."""
    source = SCRIPT.read_text(encoding="utf-8")
    function_marker = "function Test-CollaborationPreflightReady"
    guard_marker = "if ($MyInvocation.InvocationName -eq '.')"
    startup_marker = "if (-not (Test-Path -LiteralPath $python))"

    assert function_marker in source
    assert guard_marker in source
    assert "$realPreflightReady = Test-CollaborationPreflightReady -Health $collaborationHealth" in source
    assert source.index(function_marker) < source.index(guard_marker) < source.index(startup_marker)

    probe = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$ErrorActionPreference = 'Stop'; "
                f". '{SCRIPT.as_posix()}'; "
                "$ready = Test-CollaborationPreflightReady -Health ([pscustomobject]@{ ready_for_demo = $true }); "
                "$blocked = Test-CollaborationPreflightReady -Health ([pscustomobject]@{ ready_for_demo = $false }); "
                "if ($ready -ne $true -or $blocked -ne $false) { throw 'unexpected pure preflight result' }; "
                "[Console]::WriteLine('pure-preflight-ok')"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "pure-preflight-ok"


def test_demo_script_checks_all_required_ports_before_starting_any_process():
    source = SCRIPT.read_text(encoding="utf-8")
    backend_check = source.index("Assert-PortAvailable -Port 8000")
    frontend_check = source.index("Assert-PortAvailable -Port 4173")
    first_spawn = source.index("$backendProcess = Start-Process")

    assert backend_check < first_spawn
    assert frontend_check < first_spawn
    assert "if (-not $NoFrontend)" in source[:frontend_check]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required to probe exact tree cleanup")
def test_demo_process_tree_helper_stops_only_the_supplied_root_tree():
    probe = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$ErrorActionPreference = 'Stop'; "
                f". '{SCRIPT.as_posix()}'; "
                "$unrelated = Start-Process powershell -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 30' -PassThru -WindowStyle Hidden; "
                "$root = Start-Process powershell -ArgumentList '-NoProfile','-Command','$child = Start-Process powershell -ArgumentList ''-NoProfile'',''-Command'',''Start-Sleep -Seconds 30'' -PassThru -WindowStyle Hidden; Wait-Process -Id $child.Id' -PassThru -WindowStyle Hidden; "
                "try { Start-Sleep -Milliseconds 700; Stop-AgentTeamsProcessTree -RootProcessId $root.Id; Start-Sleep -Milliseconds 300; "
                "if (Get-Process -Id $root.Id -ErrorAction SilentlyContinue) { throw 'root survived' }; "
                "if (-not (Get-Process -Id $unrelated.Id -ErrorAction SilentlyContinue)) { throw 'unrelated process was stopped' }; "
                "[Console]::WriteLine('exact-tree-cleanup-ok') } finally { Stop-Process -Id $unrelated.Id -Force -ErrorAction SilentlyContinue }"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert probe.returncode == 0, probe.stderr
    assert "exact-tree-cleanup-ok" in probe.stdout


def test_env_example_remains_fail_closed_while_demo_script_opts_in_local_mode():
    env_source = ENV_EXAMPLE.read_text(encoding="utf-8")
    script_source = SCRIPT.read_text(encoding="utf-8")

    assert "DWP_AGENTTEAMS_MODE=agentteams_strict" in env_source
    assert "{ 'agentteams_strict' } else { 'local_demo' }" in script_source


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required to inspect frontend arguments")
def test_frontend_arguments_do_not_forward_a_bare_separator_to_vite():
    probe = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$ErrorActionPreference = 'Stop'; "
                f". '{SCRIPT.as_posix()}'; "
                "@(Get-AgentTeamsFrontendArguments -Port 4173) | ConvertTo-Json -Compress"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == '["--dir","frontend","dev","--host","127.0.0.1","--port","4173"]'


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required for the frontend readiness probe")
def test_frontend_helper_reaches_ipv4_http_and_cleans_its_temporary_tree():
    port = 4173
    stdout_path = (PROJECT_ROOT / "backend" / "tests" / ".agentteams-vite.out.log").as_posix()
    stderr_path = (PROJECT_ROOT / "backend" / "tests" / ".agentteams-vite.err.log").as_posix()
    probe = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$ErrorActionPreference = 'Stop'; $frontendProcess = $null; "
                f". '{SCRIPT.as_posix()}'; "
                "try { "
                f"$frontendProcess = Start-AgentTeamsFrontendProcess -Port {port} -StandardOutputPath '{stdout_path}' -StandardErrorPath '{stderr_path}'; "
                f"$ready = Wait-AgentTeamsHttpReady -Uri 'http://127.0.0.1:{port}' -Process $frontendProcess -Attempts 40 -DelayMilliseconds 250; "
                "if (-not $ready) { throw 'frontend did not become ready' }; "
                f"$response = Invoke-AgentTeamsLocalGet -Uri 'http://127.0.0.1:{port}' -TimeoutSeconds 5; "
                "if ($response.StatusCode -ne 200) { throw 'unexpected frontend response' }; "
                "[Console]::WriteLine('frontend-ipv4-ready') "
                "} finally { "
                "if ($null -ne $frontendProcess) { Stop-AgentTeamsProcessTree -RootProcessId $frontendProcess.Id }; "
                f"Remove-Item -LiteralPath '{stdout_path}','{stderr_path}' -Force -ErrorAction SilentlyContinue "
                "}"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert probe.returncode == 0, probe.stderr
    assert "frontend-ipv4-ready" in probe.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required for backend readiness probe")
def test_backend_readiness_detects_early_exit_before_frontend_spawn():
    source = SCRIPT.read_text(encoding="utf-8")
    backend_gate = source.index("if (-not $backendReady)")
    frontend_spawn = source.index("$frontendProcess = Start-AgentTeamsFrontendProcess")
    assert backend_gate < frontend_spawn

    probe = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$ErrorActionPreference = 'Stop'; "
                f". '{SCRIPT.as_posix()}'; "
                "$backend = Start-Process powershell -ArgumentList '-NoProfile','-Command','exit 7' -PassThru -WindowStyle Hidden; "
                "$ready = Wait-AgentTeamsBackendReady -Uri 'http://127.0.0.1:9/health' -Process $backend -Attempts 3 -DelayMilliseconds 50; "
                "if ($ready) { throw 'early-exit backend reported ready' }; "
                "[Console]::WriteLine('backend-early-exit-blocked')"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert probe.returncode == 0, probe.stderr
    assert "backend-early-exit-blocked" in probe.stdout
