param([switch]$NoFrontend, [switch]$Strict)

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

function Stop-AgentTeamsProcessTree {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)

    if ($null -eq (Get-Process -Id $RootProcessId -ErrorAction SilentlyContinue)) {
        # The wrapper may exit before its child closes redirected log handles.
        # Do not leak that unrelated native exit code to callers performing
        # best-effort cleanup in a finally block.
        $global:LASTEXITCODE = 0
        return
    }
    # /T follows only descendants of this exact root PID; it never searches by
    # executable name, so unrelated Python/Node processes are untouched.
    & taskkill.exe /PID $RootProcessId /T /F 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        if ($null -ne (Get-Process -Id $RootProcessId -ErrorAction SilentlyContinue)) {
            throw "Could not stop process tree rooted at PID $RootProcessId."
        }
        $global:LASTEXITCODE = 0
    }
}

function Get-AgentTeamsFrontendArguments {
    param([Parameter(Mandatory = $true)][int]$Port)

    # pnpm passes arguments following the script name through to Vite. A bare
    # separator here is itself forwarded by the installed pnpm version.
    return @('--dir', 'frontend', 'dev', '--host', '127.0.0.1', '--port', [string]$Port)
}

function Start-AgentTeamsFrontendProcess {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$StandardOutputPath,
        [Parameter(Mandatory = $true)][string]$StandardErrorPath
    )

    $frontendRoot = Join-Path $projectRoot 'frontend'
    $viteScriptPath = 'node_modules\vite\bin\vite.js'
    $viteScript = Join-Path $frontendRoot $viteScriptPath
    if (-not (Test-Path -LiteralPath $viteScript -PathType Leaf)) {
        throw "Vite entrypoint not found at $viteScript. Frontend dependencies are not ready."
    }
    # Starting pnpm.cmd returns a short-lived cmd wrapper on Windows. Returning
    # the real Node process keeps readiness and tree cleanup bound to the same
    # service process, so Vite cannot be orphaned on port 4173.
    # Use a path relative to WorkingDirectory because Windows Start-Process
    # joins ArgumentList into a command line and does not quote paths for us.
    return Start-Process -FilePath 'node.exe' `
        -ArgumentList @($viteScriptPath, '--host', '127.0.0.1', '--port', [string]$Port) `
        -WorkingDirectory $frontendRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StandardOutputPath `
        -RedirectStandardError $StandardErrorPath `
        -PassThru
}

function Invoke-AgentTeamsLocalGet {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$TimeoutSeconds = 2
    )

    # HttpWebRequest is available in both Windows PowerShell 5.1 and PowerShell
    # 7. Setting Proxy to null keeps localhost health checks away from any
    # machine-level HTTP proxy without relying on the newer -NoProxy switch.
    $request = [System.Net.HttpWebRequest]::Create($Uri)
    $request.Method = 'GET'
    $request.Proxy = $null
    $request.Timeout = $TimeoutSeconds * 1000
    $response = $null
    $reader = $null
    try {
        $response = $request.GetResponse()
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        return [pscustomobject]@{
            StatusCode = [int]$response.StatusCode
            Content = $reader.ReadToEnd()
        }
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        if ($null -ne $response) { $response.Dispose() }
    }
}

function Wait-AgentTeamsHttpReady {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [int]$Attempts = 30,
        [int]$DelayMilliseconds = 500
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $Process.Refresh()
        if ($Process.HasExited) {
            return $false
        }
        try {
            $response = Invoke-AgentTeamsLocalGet -Uri $Uri -TimeoutSeconds 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return $true
            }
        } catch {
            # Vite may still be binding; the bounded loop remains the readiness authority.
        }
        if ($attempt -lt $Attempts) {
            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }
    return $false
}

function Wait-AgentTeamsBackendReady {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [int]$Attempts = 30,
        [int]$DelayMilliseconds = 1000
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $Process.Refresh()
        if ($Process.HasExited) {
            return $false
        }
        try {
            $response = Invoke-AgentTeamsLocalGet -Uri $Uri -TimeoutSeconds 2
            $health = $response.Content | ConvertFrom-Json
            if ($health.status -eq 'ok') {
                return $true
            }
        } catch {
            # The backend may still be importing; the bounded loop remains authoritative.
        }
        if ($attempt -lt $Attempts) {
            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }
    return $false
}

function Test-CollaborationPreflightReady {
    param([AllowNull()][object]$Health)

    return ($null -ne $Health -and $Health.ready_for_demo -eq $true)
}

function Set-AgentTeamsDemoProcessEnvironment {
    param([bool]$StrictMode)

    # Process scope is inherited by child processes and never persists to .env.
    $env:DWP_AGENTTEAMS_COLLAB_ENABLED = '1'
    $env:DWP_SKIP_AUTO_SEED = '1'
    $env:DWP_AGENTTEAMS_MODE = if ($StrictMode) { 'agentteams_strict' } else { 'local_demo' }
}

if ($MyInvocation.InvocationName -eq '.') {
    return
}

if (-not (Test-Path -LiteralPath $python)) {
    throw 'backend Python environment is missing. Prepare backend/.venv with installed dependencies first.'
}
if (-not (Test-Path -LiteralPath $databasePath)) {
    throw 'backend/dwp.db is missing. Database initialization is an explicit, separately approved operation; this demo script will not create a database or seed data.'
}

# Refuse conflicts for every requested service before running even the strict
# read-only external diagnostics or starting either application process.
Assert-PortAvailable -Port 8000
if (-not $NoFrontend) {
    Assert-PortAvailable -Port 4173
}

# Deliberately process-scoped: do not write these values to backend/.env or any other file.
Set-AgentTeamsDemoProcessEnvironment -StrictMode:$Strict

Write-Host "Starting AgentTeams collaboration demo in $($env:DWP_AGENTTEAMS_MODE) mode (no installs, resets, or service creation)."

if ($Strict) {
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
} else {
    Write-Host 'Local demo: Matrix credentials are not required, used, probed, or output; Docker and controller checks are skipped.'
}

$backendProcess = $null
$frontendProcess = $null
try {
    $backendProcess = Start-Process -FilePath $python `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000') `
        -WorkingDirectory $backend `
        -WindowStyle Hidden `
        -RedirectStandardOutput $backendStdout `
        -RedirectStandardError $backendStderr `
        -PassThru

    $backendReady = Wait-AgentTeamsBackendReady `
        -Uri $backendHealthUrl `
        -Process $backendProcess `
        -Attempts 30 `
        -DelayMilliseconds 1000
    if (-not $backendReady) {
        throw 'Backend failed to become ready at http://127.0.0.1:8000 or exited early. Check the backend logs.'
    }

    if (-not $NoFrontend) {
        $frontendProcess = Start-AgentTeamsFrontendProcess `
            -Port 4173 `
            -StandardOutputPath $frontendStdout `
            -StandardErrorPath $frontendStderr
        $frontendReady = Wait-AgentTeamsHttpReady `
            -Uri 'http://127.0.0.1:4173' `
            -Process $frontendProcess `
            -Attempts 30 `
            -DelayMilliseconds 500
        if (-not $frontendReady) {
            throw 'Frontend failed to become ready at http://127.0.0.1:4173. Check the frontend logs.'
        }
    }

    $collaborationHealth = $null
    $collaborationReady = $false
    $collaborationStatus = 'unavailable'
    $collaborationReason = 'Backend health check failed or collaboration health endpoint is unavailable'
    if ($backendReady) {
        try {
            $collaborationResponse = Invoke-AgentTeamsLocalGet -Uri $collaborationHealthUrl -TimeoutSeconds 10
            $collaborationHealth = $collaborationResponse.Content | ConvertFrom-Json
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

    $realPreflightReady = Test-CollaborationPreflightReady -Health $collaborationHealth

    Write-Host ''
    Write-Host "Backend health: $(if ($backendReady) { 'ok' } else { 'unavailable' })"
    Write-Host "Collaboration health: status=$collaborationStatus; reason=$collaborationReason"
    Write-Host 'Backend URL: http://127.0.0.1:8000/health'
    Write-Host 'Collaboration health URL: http://127.0.0.1:8000/api/v1/agentteams-collaboration/health'
    Write-Host "Backend PID: $($backendProcess.Id)"
    Write-Host "Backend logs: $backendStdout; $backendStderr"

    if ($null -ne $frontendProcess) {
        Write-Host 'Frontend URL: http://127.0.0.1:4173'
        Write-Host "Frontend PID: $($frontendProcess.Id)"
        Write-Host "Frontend logs: $frontendStdout; $frontendStderr"
        Write-Host 'The frontend is UI-only and cannot prove Matrix/AgentTeams connectivity by itself.'
    } else {
        Write-Host 'Frontend: skipped by -NoFrontend (UI-only).'
    }

    $escapedScriptPath = $PSCommandPath.Replace("'", "''")
    Write-Host "Stop backend tree: powershell -NoProfile -ExecutionPolicy Bypass -Command `". '$escapedScriptPath'; Stop-AgentTeamsProcessTree -RootProcessId $($backendProcess.Id)`""
    if ($null -ne $frontendProcess) {
        Write-Host "Stop frontend (including Vite tree): powershell -NoProfile -ExecutionPolicy Bypass -Command `". '$escapedScriptPath'; Stop-AgentTeamsProcessTree -RootProcessId $($frontendProcess.Id)`""
    }

    if ($realPreflightReady -and $Strict) {
        Write-Host "Real collaboration smoke: $smokeCommand"
        Write-Host 'Strict collaboration preflight passed. Run the smoke command above for independent real-collaboration evidence.'
    } elseif ($realPreflightReady) {
        Write-Host 'Local demo preflight passed. The Agent outputs are deterministic demo data; platform orchestration and persistence remain real.'
    } else {
        Write-Warning 'Collaboration preflight is blocked. Services starting does not prove the selected transport is ready; fix the server-reported items before submitting.'
    }
} catch {
    Write-Warning 'AgentTeams demo startup failed; stopping only the process trees created by this invocation.'
    if ($null -ne $frontendProcess) {
        Stop-AgentTeamsProcessTree -RootProcessId $frontendProcess.Id
    }
    if ($null -ne $backendProcess) {
        Stop-AgentTeamsProcessTree -RootProcessId $backendProcess.Id
    }
    throw
}
