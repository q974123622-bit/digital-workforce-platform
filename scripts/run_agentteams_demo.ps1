param([switch]$NoFrontend)

$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backend = Join-Path $projectRoot 'backend'
$frontend = Join-Path $projectRoot 'frontend'
$python = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
$backendEnv = Join-Path $backend '.env'
$databasePath = Join-Path $backend 'dwp.db'
$backendStdout = Join-Path $backend 'agentteams-backend.out.log'
$backendStderr = Join-Path $backend 'agentteams-backend.err.log'
$frontendStdout = Join-Path $frontend 'agentteams-frontend.out.log'
$frontendStderr = Join-Path $frontend 'agentteams-frontend.err.log'
$backendHealthUrl = 'http://127.0.0.1:8000/health'
$collaborationHealthUrl = 'http://127.0.0.1:8000/api/v1/agentteams-collaboration/health'
$smokeCommand = 'cd backend; .\.venv\Scripts\python.exe ..\scripts\agentteams_collaboration_smoke.py'

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    $pattern = '^\s*' + [regex]::Escape($Name) + '\s*=\s*(?<value>.*?)\s*$'
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match $pattern) {
            $value = $Matches['value'].Trim()
            if ($value.Length -ge 2 -and (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            )) {
                return $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return $null
}

function Assert-PortAvailable {
    param([Parameter(Mandatory = $true)][int]$Port)

    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $listener) {
        throw "Port $Port is already occupied. This script will not stop or replace an existing service."
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw 'backend Python environment is missing. Prepare backend/.venv with installed dependencies first.'
}
if (-not (Test-Path -LiteralPath $databasePath)) {
    throw 'backend/dwp.db is missing. Database initialization is an explicit, separately approved operation; this demo script will not create a database or seed data.'
}

# Deliberately process-scoped: do not write these values to backend/.env or any other file.
$env:DWP_AGENTTEAMS_COLLAB_ENABLED = '1'
$env:DWP_SKIP_AUTO_SEED = '1'

Write-Host 'Starting AgentTeams collaboration demo (no installs, resets, or Matrix service creation).'

$backendEnvPresent = Test-Path -LiteralPath $backendEnv
Write-Host (if ($backendEnvPresent) { 'Backend .env: present (read-only check)' } else { 'Backend .env: missing; process environment can provide configuration (read-only check)' })

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerReady = $false
if ($null -ne $dockerCommand) {
    & docker version --format '{{.Server.Version}}' 1>$null 2>$null
    $dockerReady = $LASTEXITCODE -eq 0
}
Write-Host (if ($dockerReady) { 'Docker: available (read-only check)' } else { 'Docker: unavailable or daemon not running (read-only check)' })

$controllerContainer = $env:AGENTTEAMS_CONTROLLER_CONTAINER
if ([string]::IsNullOrWhiteSpace($controllerContainer)) {
    $controllerContainer = Get-DotEnvValue -Path $backendEnv -Name 'AGENTTEAMS_CONTROLLER_CONTAINER'
}
if ([string]::IsNullOrWhiteSpace($controllerContainer)) {
    $controllerContainer = 'agentteams-controller'
}

$controllerRunning = $false
if ($dockerReady) {
    $controllerState = & docker container inspect $controllerContainer --format '{{.State.Running}}' 2>$null
    $controllerRunning = $LASTEXITCODE -eq 0 -and [string]::Equals(
        ([string]$controllerState).Trim(), 'true', [System.StringComparison]::OrdinalIgnoreCase
    )
}
Write-Host (if ($controllerRunning) { 'Configured AgentTeams controller: running (read-only check)' } else { 'Configured AgentTeams controller: not confirmed running (read-only check)' })

Assert-PortAvailable -Port 8000
$backendProcess = Start-Process -FilePath $python `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000') `
    -WorkingDirectory $backend `
    -WindowStyle Hidden `
    -RedirectStandardOutput $backendStdout `
    -RedirectStandardError $backendStderr `
    -PassThru

$frontendProcess = $null
if (-not $NoFrontend) {
    Assert-PortAvailable -Port 5173
    $frontendProcess = Start-Process -FilePath 'pnpm.cmd' `
        -ArgumentList @('--dir', 'frontend', 'dev', '--', '--host', '127.0.0.1', '--port', '5173') `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $frontendStdout `
        -RedirectStandardError $frontendStderr `
        -PassThru
}

$backendReady = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri $backendHealthUrl -Method Get -TimeoutSec 2
        if ($health.status -eq 'ok') {
            $backendReady = $true
            break
        }
    } catch {
        # The launched process may still be importing; detailed diagnostics are in its log files.
    }
}

$collaborationReady = $false
$collaborationStatus = 'unavailable'
$collaborationReason = 'Backend health check failed or collaboration health endpoint is unavailable'
if ($backendReady) {
    try {
        $collaborationHealth = Invoke-RestMethod -Uri $collaborationHealthUrl -Method Get -TimeoutSec 10
        $collaborationReady = $collaborationHealth.ready_for_demo -eq $true
        $collaborationStatus = if ($collaborationReady) { 'ready' } else { 'blocked' }
        $collaborationReason = [string]$collaborationHealth.reason
        if ([string]::IsNullOrWhiteSpace($collaborationReason)) {
            $collaborationReason = 'none'
        }
    } catch {
        # Do not surface raw responses or exception text because they can include deployment details.
    }
}

$realPreflightReady = $collaborationReady

Write-Host ''
Write-Host "Backend health: $(if ($backendReady) { 'ok' } else { 'unavailable' })"
Write-Host "Collaboration health: status=$collaborationStatus; reason=$collaborationReason"
Write-Host 'Backend URL: http://127.0.0.1:8000/health'
Write-Host 'Collaboration health URL: http://127.0.0.1:8000/api/v1/agentteams-collaboration/health'
Write-Host "Backend PID: $($backendProcess.Id)"
Write-Host "Backend logs: $backendStdout; $backendStderr"

if ($null -ne $frontendProcess) {
    Write-Host 'Frontend URL: http://127.0.0.1:5173'
    Write-Host "Frontend PID: $($frontendProcess.Id)"
    Write-Host "Frontend logs: $frontendStdout; $frontendStderr"
    Write-Host 'The frontend is UI-only and cannot prove Matrix/AgentTeams connectivity by itself.'
} else {
    Write-Host 'Frontend: skipped by -NoFrontend (UI-only).'
}

Write-Host "Real collaboration smoke: $smokeCommand"
Write-Host "Stop backend: Stop-Process -Id $($backendProcess.Id)"
if ($null -ne $frontendProcess) {
    Write-Host "Stop frontend: Stop-Process -Id $($frontendProcess.Id)"
}

if ($realPreflightReady) {
    Write-Host 'Collaboration preflight passed. Run the smoke command above for independent real-collaboration evidence.'
} else {
    Write-Warning 'Collaboration preflight is blocked. Only local services were started; this does not claim AgentTeams is connected. Fix the preflight items, then run the smoke command above for evidence.'
}
