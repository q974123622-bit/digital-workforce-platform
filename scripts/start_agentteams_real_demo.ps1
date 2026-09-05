[CmdletBinding()]
param([switch]$NoFrontend)

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
. (Join-Path $PSScriptRoot 'agentteams_process_helpers.ps1')

function Test-CommandAvailable([string]$Name) { return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue) }
function Test-DeepSeekConfigured {
    if (-not [string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) { return $true }
    $envFile = Join-Path $backend '.env'; if (-not (Test-Path -LiteralPath $envFile)) { return $false }
    return [bool](Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^\s*DEEPSEEK_API_KEY\s*=\s*[^\s#]+' } | Select-Object -First 1)
}
function Import-DeepSeekKeyForChildProcesses {
    if (-not [string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) { return }
    $line = Get-Content -LiteralPath (Join-Path $backend '.env') | Where-Object { $_ -match '^\s*DEEPSEEK_API_KEY\s*=\s*[^\s#]+' } | Select-Object -First 1
    if ($null -eq $line) { throw 'DEEPSEEK_API_KEY is required for the strict real demo.' }
    $env:DEEPSEEK_API_KEY = (($line -split '=', 2)[1]).Trim()
}
function Test-StringArrayEqual([object[]]$Actual, [object[]]$Expected) {
    if ($Actual.Count -ne $Expected.Count) { return $false }; for ($i=0; $i -lt $Actual.Count; $i++) { if ([string]$Actual[$i] -cne [string]$Expected[$i]) { return $false } }; return $true
}
function Get-ExpectedArguments([string]$Name) {
    switch ($Name) {
        'backend' { return @('-m','uvicorn','app.main:app','--app-dir',$backend,'--host','127.0.0.1','--port','8000') }
        'manager' { return @($agentScript,'--role','manager','--config',$runtimeConfig) }
        'hr' { return @($agentScript,'--role','hr','--config',$runtimeConfig) }
        'it' { return @($agentScript,'--role','it','--config',$runtimeConfig) }
        'audit' { return @($agentScript,'--role','audit','--config',$runtimeConfig) }
        'frontend' { return @((Join-Path $workspace 'frontend\node_modules\vite\bin\vite.js'),'--host','127.0.0.1','--port','4173') }
        default { throw 'invalid owned process name' }
    }
}
function Get-ExpectedExecutable([string]$Name) { if ($Name -eq 'frontend') { return (Get-Command node -ErrorAction Stop).Source }; return $python }
function Test-RecordShape([object]$Record) {
    if ($null -eq $Record -or $Record.workspace -cne $workspace -or [string]::IsNullOrWhiteSpace($Record.name) -or [string]::IsNullOrWhiteSpace($Record.executable_path) -or [string]::IsNullOrWhiteSpace($Record.working_directory) -or $null -eq $Record.argv -or [string]::IsNullOrWhiteSpace($Record.started_at)) { return $false }
    try { return ([IO.Path]::GetFullPath([string]$Record.executable_path) -ieq (Resolve-Path -LiteralPath (Get-ExpectedExecutable $Record.name)).Path) -and ([IO.Path]::GetFullPath([string]$Record.working_directory) -ieq (Resolve-Path -LiteralPath $(if ($Record.name -eq 'backend') {$backend} elseif ($Record.name -eq 'frontend') {Join-Path $workspace 'frontend'} else {$workspace})).Path) -and (Test-StringArrayEqual @($Record.argv) (Get-ExpectedArguments $Record.name)) } catch { return $false }
}
function Test-OwnedRecordedProcess([object]$Record) {
    if (-not (Test-RecordShape $Record) -or -not ($Record.process_id -is [int] -or $Record.process_id -is [long])) { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($Record.process_id)" -ErrorAction SilentlyContinue
    if ($null -eq $process -or [string]::IsNullOrWhiteSpace($process.CommandLine) -or [string]::IsNullOrWhiteSpace($process.ExecutablePath) -or [string]::IsNullOrWhiteSpace($process.CreationDate)) { return $false }
    try {
        $recordedStart=[DateTime]::Parse([string]$Record.started_at).ToUniversalTime(); $actualStart=[System.Management.ManagementDateTimeConverter]::ToDateTime([string]$process.CreationDate).ToUniversalTime()
        if ([Math]::Abs(($recordedStart-$actualStart).TotalSeconds) -gt 2 -or [IO.Path]::GetFullPath([string]$process.ExecutablePath) -ine (Resolve-Path -LiteralPath $Record.executable_path).Path) { return $false }
        foreach ($argument in @($Record.argv)) { if ($process.CommandLine.IndexOf([string]$argument,[System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false } }
        return $process.CommandLine.IndexOf($workspace,[System.StringComparison]::OrdinalIgnoreCase) -ge 0
    } catch { return $false }
}
function Start-OwnedProcess([string]$Name,[string]$WorkingDirectory,[string]$ExecutablePath,[object[]]$Arguments) {
    $started=Start-AgentTeamsOwnedProcess -ExecutablePath $ExecutablePath -WorkingDirectory $WorkingDirectory -Arguments $Arguments
    $process=$started.Process; Start-Sleep -Milliseconds 700
    if ($process.HasExited) { throw "$Name exited during startup (exit code $($process.ExitCode))." }
    return @{name=$Name;process_id=[int]$process.Id;executable_path=$started.ExecutablePath;working_directory=$started.WorkingDirectory;argv=@($started.Arguments);workspace=$workspace;started_at=$process.StartTime.ToUniversalTime().ToString('o')}
}
function Write-ProcessRecord([object[]]$Processes,[string]$Status) {
    New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null; $payload=@{workspace=$workspace;status=$Status;created_at=[DateTime]::UtcNow.ToString('o');processes=$Processes}|ConvertTo-Json -Depth 6
    $temporary=Join-Path $runtimeDirectory ('.pids.'+[guid]::NewGuid().ToString('N')+'.tmp'); [IO.File]::WriteAllText($temporary,$payload,[Text.UTF8Encoding]::new($false)); Move-Item -LiteralPath $temporary -Destination $pidsPath -Force
}
function Stop-OnlyOwnedRecords([object[]]$Records) { foreach ($record in $Records) { if (Test-OwnedRecordedProcess $record) { Stop-Process -Id $record.process_id -Force -ErrorAction SilentlyContinue } } }
function Stop-OwnedComposeProject {
    try {
        & docker compose -p dwp-agentteams -f $composeFile down 1>$null 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Owned Synapse compose cleanup failed; preserving the original startup error.'
        }
    } catch {
        Write-Warning 'Owned Synapse compose cleanup raised an error; preserving the original startup error.'
    }
}
function Set-AgentTeamsStrictRuntimeEnvironment {
    $env:DWP_AGENTTEAMS_MODE = 'agentteams_strict'
    $env:DWP_AGENTTEAMS_COLLAB_ENABLED = '1'
    $env:DWP_AGENTTEAMS_RUNTIME_CONFIG = $runtimeConfig
}
function Assert-NoExistingDemoProcesses {
    if (-not (Test-Path -LiteralPath $pidsPath)) { return }; try { $saved=Get-Content -LiteralPath $pidsPath -Raw|ConvertFrom-Json } catch { throw 'Invalid existing AgentTeams PID record; refusing duplicate start.' }
    if ($saved.status -eq 'running') { foreach ($record in @($saved.processes)) { if ($NoFrontend -and $record.name -eq 'frontend') { continue }; if (Test-OwnedRecordedProcess $record) { throw "An AgentTeams real-demo process is already running for this worktree (PID $($record.process_id))." } } }
}
function Wait-SynapseReady { for ($attempt=0;$attempt -lt 30;$attempt++) { try { if ((Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8008/_matrix/client/versions' -TimeoutSec 2).StatusCode -eq 200) { return $true } } catch {}; Start-Sleep -Seconds 1 }; return $false }
if ($MyInvocation.InvocationName -eq '.') { return }

if (-not (Test-CommandAvailable 'docker')) { throw 'Docker Desktop CLI is unavailable.' }; & docker version --format '{{.Server.Version}}' 1>$null 2>$null; if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop daemon is unavailable.' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Backend virtual environment Python is missing.' }; if (-not (Test-DeepSeekConfigured)) { throw 'DEEPSEEK_API_KEY is required for the strict real demo (value not shown).' }; Assert-NoExistingDemoProcesses
if (-not $NoFrontend) { if (-not (Test-CommandAvailable 'node')) { throw 'Node.js is unavailable.' }; if (-not (Test-Path -LiteralPath (Join-Path $workspace 'frontend\node_modules\vite\bin\vite.js'))) { throw 'Frontend dependencies are missing.' } }
$alreadyRunning=@(& docker compose -p dwp-agentteams -f $composeFile ps --status running --services 2>$null); if ($LASTEXITCODE -ne 0) { throw 'Unable to determine whether the scoped Synapse project is already running.' }; if (@($alreadyRunning|Where-Object {$_ -eq 'synapse'}).Count -gt 0) { throw 'The dwp-agentteams Synapse project is already running; refusing to claim or stop it.' }
Set-AgentTeamsStrictRuntimeEnvironment; $records=@(); $ownedCompose=$false; $success=$false
try {
    Import-DeepSeekKeyForChildProcesses; & $python $bootstrap --runtime-dir $runtimeDirectory --prepare-synapse-env; if ($LASTEXITCODE -ne 0) { throw 'Synapse secret preparation failed.' }
    $ownedCompose=$true; & docker compose -p dwp-agentteams -f $composeFile up -d; if ($LASTEXITCODE -ne 0) { throw 'Synapse compose startup failed.' }; if (-not (Wait-SynapseReady)) { throw 'Synapse did not become ready at 127.0.0.1:8008.' }
    & $python $bootstrap --runtime-dir $runtimeDirectory --matrix-url 'http://127.0.0.1:8008'; if ($LASTEXITCODE -ne 0) { throw 'Matrix account and private-room bootstrap failed.' }
    $records+=Start-OwnedProcess 'backend' $backend $python (Get-ExpectedArguments 'backend'); foreach ($role in 'manager','hr','it','audit') { $records+=Start-OwnedProcess $role $workspace $python (Get-ExpectedArguments $role) }; if (-not $NoFrontend) { $records+=Start-OwnedProcess 'frontend' (Join-Path $workspace 'frontend') (Get-ExpectedExecutable 'frontend') (Get-ExpectedArguments 'frontend') }
    Write-ProcessRecord $records 'running'; $success=$true; Write-Host "AgentTeams strict demo started: recorded $($records.Count) worktree-owned processes."
} catch { try { Write-ProcessRecord @() 'failed' } catch {}; throw } finally { if (-not $success) { Stop-OnlyOwnedRecords $records; if ($ownedCompose) { Stop-OwnedComposeProject } } }
