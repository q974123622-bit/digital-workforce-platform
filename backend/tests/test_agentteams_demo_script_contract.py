from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "run_agentteams_demo.ps1"
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
    assert "param([switch]$NoFrontend)" in source
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
