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
    assert "Start-Process" in source
    assert "-WindowStyle Hidden" in source
    assert "-RedirectStandardOutput" in source
    assert "-RedirectStandardError" in source
    lowered = source.lower()
    assert "pnpm install" not in lowered
    assert "pip install" not in lowered
    assert "docker compose" not in lowered
    assert "--reset" not in lowered
