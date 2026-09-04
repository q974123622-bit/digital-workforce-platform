[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
# Runtime record: .agentteams-runtime\pids.json
$pidsPath = Join-Path $workspace '.agentteams-runtime\pids.json'
$composeFile = Join-Path $workspace 'deploy\agentteams-matrix\docker-compose.yml'

function Test-RecordedWorktreeProcess([object]$Record) {
    if ($null -eq $Record -or $Record.worktree -ne $workspace -or -not ($Record.pid -is [int] -or $Record.pid -is [long]) -or [string]::IsNullOrWhiteSpace($Record.command)) { return $null }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($Record.pid)" -ErrorAction SilentlyContinue
    if ($null -eq $process -or [string]::IsNullOrWhiteSpace($process.CommandLine)) { return $null }
    if ($process.CommandLine.IndexOf($workspace, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $null }
    if ($Record.command.IndexOf($workspace, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $null }
    return $process
}

if (Test-Path -LiteralPath $pidsPath) {
    try { $records = (Get-Content -LiteralPath $pidsPath -Raw | ConvertFrom-Json).processes } catch { throw 'Invalid AgentTeams PID record; refusing to stop any process.' }
    foreach ($record in @($records)) {
        $process = Test-RecordedWorktreeProcess $record
        if ($null -ne $process) {
            Stop-Process -Id $record.pid -Force -ErrorAction Stop
            Write-Host "Stopped recorded $($record.name) process."
        } else {
            Write-Warning "Skipped unproven recorded PID $($record.pid)."
        }
    }
} else {
    Write-Host 'No AgentTeams PID record exists; no Windows process was stopped.'
}

& docker compose -p dwp-agentteams -f $composeFile down
if ($LASTEXITCODE -ne 0) { throw 'Synapse compose shutdown failed.' }
Write-Host 'Stopped only the dwp-agentteams Compose project; the named Synapse volume was retained.'
