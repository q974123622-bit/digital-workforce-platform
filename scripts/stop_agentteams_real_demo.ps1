[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
# Runtime record: .agentteams-runtime\pids.json
$pidsPath = Join-Path $workspace '.agentteams-runtime\pids.json'
$composeFile = Join-Path $workspace 'deploy\agentteams-matrix\docker-compose.yml'
$backend = Join-Path $workspace 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$agentScript = Join-Path $workspace 'scripts\run_agentteams_agent.py'
$runtimeConfig = Join-Path $workspace '.agentteams-runtime\runtime.json'

function Test-StringArrayEqual([object[]]$Actual,[object[]]$Expected) { if ($Actual.Count -ne $Expected.Count) { return $false }; for ($i=0;$i -lt $Actual.Count;$i++) { if ([string]$Actual[$i] -cne [string]$Expected[$i]) { return $false } }; return $true }
function Get-ExpectedArguments([string]$Name) {
    switch ($Name) {
        'backend' { return @('-m','uvicorn','app.main:app','--app-dir',$backend,'--host','127.0.0.1','--port','8000') }
        'manager' { return @($agentScript,'--role','manager','--config',$runtimeConfig) }
        'hr' { return @($agentScript,'--role','hr','--config',$runtimeConfig) }
        'it' { return @($agentScript,'--role','it','--config',$runtimeConfig) }
        'audit' { return @($agentScript,'--role','audit','--config',$runtimeConfig) }
        'frontend' { return @((Join-Path $workspace 'frontend\node_modules\vite\bin\vite.js'),'--host','127.0.0.1','--port','4173') }
        default { throw 'invalid record name' }
    }
}
function Get-ExpectedExecutable([string]$Name) { if ($Name -eq 'frontend') { return (Get-Command node -ErrorAction Stop).Source }; return $python }
function Test-RecordedWorktreeProcess([object]$Record) {
    if ($null -eq $Record -or $Record.workspace -cne $workspace -or -not ($Record.process_id -is [int] -or $Record.process_id -is [long]) -or [string]::IsNullOrWhiteSpace($Record.executable_path) -or [string]::IsNullOrWhiteSpace($Record.working_directory) -or [string]::IsNullOrWhiteSpace($Record.started_at) -or $null -eq $Record.argv) { return $null }
    try {
        $expectedExecutable=(Resolve-Path -LiteralPath (Get-ExpectedExecutable $Record.name)).Path
        $expectedWorkingDirectory=(Resolve-Path -LiteralPath $(if ($Record.name -eq 'backend') {$backend} elseif ($Record.name -eq 'frontend') {Join-Path $workspace 'frontend'} else {$workspace})).Path
        if ([IO.Path]::GetFullPath([string]$Record.executable_path) -ine $expectedExecutable -or [IO.Path]::GetFullPath([string]$Record.working_directory) -ine $expectedWorkingDirectory -or -not (Test-StringArrayEqual @($Record.argv) (Get-ExpectedArguments $Record.name))) { return $null }
        $process=Get-CimInstance Win32_Process -Filter "ProcessId = $($Record.process_id)" -ErrorAction SilentlyContinue
        if ($null -eq $process -or [string]::IsNullOrWhiteSpace($process.ExecutablePath) -or [string]::IsNullOrWhiteSpace($process.CommandLine) -or [string]::IsNullOrWhiteSpace($process.CreationDate)) { return $null }
        $recordedStart=[DateTime]::Parse([string]$Record.started_at).ToUniversalTime(); $actualStart=[System.Management.ManagementDateTimeConverter]::ToDateTime([string]$process.CreationDate).ToUniversalTime()
        if ([Math]::Abs(($recordedStart-$actualStart).TotalSeconds) -gt 2 -or [IO.Path]::GetFullPath([string]$process.ExecutablePath) -ine $expectedExecutable) { return $null }
        foreach ($argument in @($Record.argv)) { if ($process.CommandLine.IndexOf([string]$argument,[System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $null } }
        if ($process.CommandLine.IndexOf($workspace,[System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $null }
        return $process
    } catch { return $null }
}
if ($MyInvocation.InvocationName -eq '.') { return }
if (Test-Path -LiteralPath $pidsPath) {
    try { $saved=Get-Content -LiteralPath $pidsPath -Raw|ConvertFrom-Json } catch { throw 'Invalid AgentTeams PID record; refusing to stop any process.' }
    foreach ($record in @($saved.processes)) { $process=Test-RecordedWorktreeProcess $record; if ($null -ne $process) { Stop-Process -Id $record.process_id -Force -ErrorAction Stop; Write-Host "Stopped recorded $($record.name) process." } else { Write-Warning "Skipped unproven recorded PID $($record.process_id)." } }
} else { Write-Host 'No AgentTeams PID record exists; no Windows process was stopped.' }
& docker compose -p dwp-agentteams -f $composeFile down
if ($LASTEXITCODE -ne 0) { throw 'Synapse compose shutdown failed.' }
Write-Host 'Stopped only the dwp-agentteams Compose project; the named Synapse volume was retained.'
