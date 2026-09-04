[CmdletBinding()]
param(
    [switch]$NoFrontend
)

$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeDirectory = Join-Path $workspace '.agentteams-runtime'
$runtimeConfig = Join-Path $runtimeDirectory 'runtime.json'
# Runtime record: .agentteams-runtime\pids.json
$pidsPath = Join-Path $runtimeDirectory 'pids.json'
$composeFile = Join-Path $workspace 'deploy\agentteams-matrix\docker-compose.yml'
$bootstrap = Join-Path $workspace 'scripts\bootstrap_agentteams_matrix.py'
$agentScript = Join-Path $workspace 'scripts\run_agentteams_agent.py'
$backend = Join-Path $workspace 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'

function Test-CommandAvailable([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-DeepSeekConfigured {
    if (-not [string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) { return $true }
    $envFile = Join-Path $backend '.env'
    if (-not (Test-Path -LiteralPath $envFile)) { return $false }
    return [bool](Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^\s*DEEPSEEK_API_KEY\s*=\s*[^\s#]+' } | Select-Object -First 1)
}

function Import-DeepSeekKeyForChildProcesses {
    if (-not [string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) { return }
    $envFile = Join-Path $backend '.env'
    $line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^\s*DEEPSEEK_API_KEY\s*=\s*[^\s#]+' } | Select-Object -First 1
    if ($null -eq $line) { throw 'DEEPSEEK_API_KEY is required for the strict real demo.' }
    $env:DEEPSEEK_API_KEY = (($line -split '=', 2)[1]).Trim()
}

function Test-OwnedRecordedProcess([object]$Record) {
    if ($null -eq $Record -or $Record.worktree -ne $workspace -or -not ($Record.pid -is [int] -or $Record.pid -is [long])) { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($Record.pid)" -ErrorAction SilentlyContinue
    if ($null -eq $process -or [string]::IsNullOrWhiteSpace($process.CommandLine)) { return $false }
    return $process.CommandLine.IndexOf($workspace, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Assert-NoExistingDemoProcesses {
    if (-not (Test-Path -LiteralPath $pidsPath)) { return }
    try { $records = (Get-Content -LiteralPath $pidsPath -Raw | ConvertFrom-Json).processes } catch { throw 'Invalid existing AgentTeams PID record; use the scoped stop script after checking it.' }
    foreach ($record in @($records)) {
        if (Test-OwnedRecordedProcess $record) {
            throw "An AgentTeams real-demo process is already running for this worktree (PID $($record.pid)). Use scripts/stop_agentteams_real_demo.ps1 first."
        }
    }
}

function Wait-SynapseReady {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8008/_matrix/client/versions' -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Write-ProcessRecord([object[]]$Processes) {
    New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
    $payload = @{ worktree = $workspace; created_at = [DateTime]::UtcNow.ToString('o'); processes = $Processes } | ConvertTo-Json -Depth 5
    $temporary = Join-Path $runtimeDirectory ('.pids.' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($temporary, $payload, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $pidsPath -Force
}

if (-not (Test-CommandAvailable 'docker')) { throw 'Docker Desktop CLI is unavailable.' }
& docker version --format '{{.Server.Version}}' 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop daemon is unavailable.' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Backend virtual environment Python is missing.' }
if (-not (Test-DeepSeekConfigured)) { throw 'DEEPSEEK_API_KEY is required for the strict real demo (value not shown).' }
Import-DeepSeekKeyForChildProcesses
if (-not (Test-CommandAvailable 'node')) { throw 'Node.js is unavailable.' }
if (-not $NoFrontend -and -not (Test-Path -LiteralPath (Join-Path $workspace 'frontend\node_modules\vite\bin\vite.js'))) { throw 'Frontend dependencies are missing.' }
Assert-NoExistingDemoProcesses

$env:DWP_AGENTTEAMS_MODE = 'agentteams_strict'
$env:DWP_AGENTTEAMS_RUNTIME_CONFIG = $runtimeConfig
& $python $bootstrap --runtime-dir $runtimeDirectory --prepare-synapse-env
if ($LASTEXITCODE -ne 0) { throw 'Synapse secret preparation failed.' }
& docker compose -p dwp-agentteams -f $composeFile up -d
if ($LASTEXITCODE -ne 0) { throw 'Synapse compose startup failed.' }
if (-not (Wait-SynapseReady)) { throw 'Synapse did not become ready at 127.0.0.1:8008.' }
& $python $bootstrap --runtime-dir $runtimeDirectory --matrix-url 'http://127.0.0.1:8008'
if ($LASTEXITCODE -ne 0) { throw 'Matrix account and private-room bootstrap failed.' }

$logs = Join-Path $runtimeDirectory 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$records = @()
try {
    $backendProcess = Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'backend.out.log') -RedirectStandardError (Join-Path $logs 'backend.err.log') -PassThru
    $records += @{ name = 'backend'; pid = [int]$backendProcess.Id; worktree = $workspace; command = "$python -m uvicorn app.main:app --host 127.0.0.1 --port 8000" }

    $agentArguments = @{
        manager = @($agentScript, '--role manager', '--config', $runtimeConfig)
        hr = @($agentScript, '--role hr', '--config', $runtimeConfig)
        it = @($agentScript, '--role it', '--config', $runtimeConfig)
        audit = @($agentScript, '--role audit', '--config', $runtimeConfig)
    }
    foreach ($role in 'manager', 'hr', 'it', 'audit') {
        $process = Start-Process -FilePath $python -ArgumentList $agentArguments[$role] -WorkingDirectory $workspace -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "$role.out.log") -RedirectStandardError (Join-Path $logs "$role.err.log") -PassThru
        $records += @{ name = $role; pid = [int]$process.Id; worktree = $workspace; command = "$python $agentScript --role $role --config $runtimeConfig" }
    }
    if (-not $NoFrontend) {
        $vite = Join-Path $workspace 'frontend\node_modules\vite\bin\vite.js'
        $node = (Get-Command node -ErrorAction Stop).Source
        $process = Start-Process -FilePath $node -ArgumentList @($vite, '--host', '127.0.0.1', '--port', '4173') -WorkingDirectory (Join-Path $workspace 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'frontend.out.log') -RedirectStandardError (Join-Path $logs 'frontend.err.log') -PassThru
        $records += @{ name = 'frontend'; pid = [int]$process.Id; worktree = $workspace; command = "$node $vite --host 127.0.0.1 --port 4173" }
    }
    Write-ProcessRecord $records
    Write-Host "AgentTeams strict demo started: recorded $($records.Count) worktree-owned processes."
    Write-Host 'Runtime credentials remain in .agentteams-runtime and are not printed.'
} catch {
    foreach ($record in $records) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)" -ErrorAction SilentlyContinue
        if ($process -and $process.CommandLine -and $process.CommandLine.IndexOf($workspace, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            Stop-Process -Id $record.pid -Force -ErrorAction SilentlyContinue
        }
    }
    throw
}
