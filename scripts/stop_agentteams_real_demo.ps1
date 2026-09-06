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
. (Join-Path $PSScriptRoot 'agentteams_process_helpers.ps1')

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
        $recordedStart=ConvertTo-AgentTeamsUtcDateTime $Record.started_at; $actualStart=ConvertTo-AgentTeamsUtcDateTime $process.CreationDate
        if ([Math]::Abs(($recordedStart-$actualStart).TotalSeconds) -gt 2 -or [IO.Path]::GetFullPath([string]$process.ExecutablePath) -ine $expectedExecutable) { return $null }
        foreach ($argument in @($Record.argv)) { if ($process.CommandLine.IndexOf([string]$argument,[System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $null } }
        if ($process.CommandLine.IndexOf($workspace,[System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $null }
        return $process
    } catch { return $null }
}
function Get-CurrentOwnedBackendListenerRecord([object]$Record) {
    if ($Record.name -cne 'backend' -or $null -eq (Test-RecordedWorktreeProcess $Record)) { throw 'backend_launcher_not_proven' }
    $owners = @(
        Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { [int]$_.OwningProcess } |
            Sort-Object -Unique
    )
    if ($owners.Count -ne 1) { throw 'backend_listener_not_proven' }
    $listenerId=[int]$owners[0]
    $listener=Get-CimInstance Win32_Process -Filter "ProcessId = $listenerId" -ErrorAction SilentlyContinue
    if ($null -eq $listener -or [string]::IsNullOrWhiteSpace($listener.ExecutablePath) -or [string]::IsNullOrWhiteSpace($listener.CommandLine) -or [string]::IsNullOrWhiteSpace($listener.CreationDate)) { throw 'backend_listener_not_proven' }
    $rootStart=ConvertTo-AgentTeamsUtcDateTime $Record.started_at; $listenerStart=ConvertTo-AgentTeamsUtcDateTime $listener.CreationDate
    if ($listenerId -ne [int]$Record.process_id) {
        if ([int]$listener.ParentProcessId -ne [int]$Record.process_id -or $listenerStart -lt $rootStart.AddSeconds(-2) -or $listenerStart -gt $rootStart.AddSeconds(10)) { throw 'backend_listener_parent_not_proven' }
        if ([IO.Path]::GetFullPath([string]$listener.ExecutablePath) -ine (Get-AgentTeamsVenvBasePython $Record.executable_path)) { throw 'backend_listener_executable_not_proven' }
    }
    foreach ($argument in @($Record.argv)) { if ($listener.CommandLine.IndexOf([string]$argument,[System.StringComparison]::OrdinalIgnoreCase) -lt 0) { throw 'backend_listener_command_not_proven' } }
    if ($listener.CommandLine.IndexOf($workspace,[System.StringComparison]::OrdinalIgnoreCase) -lt 0) { throw 'backend_listener_workspace_not_proven' }
    return @{
        process_id=$listenerId
        parent_process_id=$(if ($listenerId -eq [int]$Record.process_id) { $null } else { [int]$Record.process_id })
        executable_path=[IO.Path]::GetFullPath([string]$listener.ExecutablePath)
        started_at=$listenerStart.ToString('o')
        port=[int]8000
    }
}
function Get-ProvenRecordedProcessIds([object]$Record) {
    $root = Test-RecordedWorktreeProcess $Record
    if ($null -eq $root) { return @() }
    if ($Record.name -cne 'backend') { return @([int]$Record.process_id) }
    try {
        $actual=Get-CurrentOwnedBackendListenerRecord $Record
        if ($null -ne $Record.PSObject.Properties['listener']) {
            $recorded=$Record.listener
            if ($null -eq $recorded -or -not ($recorded.process_id -is [int] -or $recorded.process_id -is [long]) -or -not ($recorded.port -is [int] -or $recorded.port -is [long]) -or [int]$recorded.port -ne 8000 -or [string]::IsNullOrWhiteSpace($recorded.executable_path) -or [string]::IsNullOrWhiteSpace($recorded.started_at)) { return @() }
            if ([int]$actual.process_id -ne [int]$recorded.process_id -or [int]$actual.port -ne [int]$recorded.port -or [IO.Path]::GetFullPath([string]$actual.executable_path) -ine [IO.Path]::GetFullPath([string]$recorded.executable_path)) { return @() }
            if ([Math]::Abs(((ConvertTo-AgentTeamsUtcDateTime $actual.started_at)-(ConvertTo-AgentTeamsUtcDateTime $recorded.started_at)).TotalSeconds) -gt 2) { return @() }
            if ([int]$actual.process_id -ne [int]$Record.process_id -and (-not ($recorded.parent_process_id -is [int] -or $recorded.parent_process_id -is [long]) -or [int]$recorded.parent_process_id -ne [int]$Record.process_id)) { return @() }
        }
        if ([int]$actual.process_id -eq [int]$Record.process_id) { return @([int]$Record.process_id) }
        if ([int]$actual.parent_process_id -ne [int]$Record.process_id) { return @() }
        return @([int]$actual.process_id,[int]$Record.process_id)
    } catch { return @() }
}
if ($MyInvocation.InvocationName -eq '.') { return }
if (Test-Path -LiteralPath $pidsPath) {
    try { $saved=Get-Content -LiteralPath $pidsPath -Raw|ConvertFrom-Json } catch { throw 'Invalid AgentTeams PID record; refusing to stop any process.' }
    foreach ($record in @($saved.processes)) { $processIds=@(Get-ProvenRecordedProcessIds $record); if ($processIds.Count -gt 0) { foreach ($processId in $processIds) { Stop-Process -Id $processId -Force -ErrorAction Stop }; Write-Host "Stopped recorded $($record.name) process tree." } else { Write-Warning "Skipped unproven recorded PID $($record.process_id)." } }
} else { Write-Host 'No AgentTeams PID record exists; no Windows process was stopped.' }
& docker compose -p dwp-agentteams -f $composeFile down
if ($LASTEXITCODE -ne 0) { throw 'Synapse compose shutdown failed.' }
Write-Host 'Stopped only the dwp-agentteams Compose project; the named Synapse volume was retained.'
