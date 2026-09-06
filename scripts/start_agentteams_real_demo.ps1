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
$AgentRoleApiKeyVariables = [ordered]@{
    manager = 'DEEPSEEK_API_KEY_MANAGER'
    hr = 'DEEPSEEK_API_KEY_HR'
    it = 'DEEPSEEK_API_KEY_IT'
    audit = 'DEEPSEEK_API_KEY_AUDIT'
}
function ConvertFrom-AgentRoleApiKeyText([string]$Text) {
    $value = $Text.Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        return $value.Substring(1, $value.Length - 2)
    }
    return ($value -replace '\s+#.*$', '').Trim()
}
function Get-AgentRoleApiKeyValues([string]$EnvFile = (Join-Path $backend '.env'), [hashtable]$ProcessEnvironment = $null) {
    $fromFile = @{}
    if (Test-Path -LiteralPath $EnvFile -PathType Leaf) {
        foreach ($line in Get-Content -LiteralPath $EnvFile -ErrorAction Stop) {
            foreach ($variableName in $AgentRoleApiKeyVariables.Values) {
                if ($line -match ('^\s*' + [regex]::Escape($variableName) + '\s*=\s*(.*)$')) {
                    $fromFile[$variableName] = ConvertFrom-AgentRoleApiKeyText $Matches[1]
                }
            }
        }
    }
    $resolved = @{}
    foreach ($role in $AgentRoleApiKeyVariables.Keys) {
        $variableName = $AgentRoleApiKeyVariables[$role]
        $processValue = if ($null -eq $ProcessEnvironment) { [Environment]::GetEnvironmentVariable($variableName, 'Process') } else { $ProcessEnvironment[$variableName] }
        $resolved[$role] = if (-not [string]::IsNullOrWhiteSpace([string]$processValue)) { [string]$processValue } else { [string]$fromFile[$variableName] }
    }
    return $resolved
}
function Get-MissingAgentRoleApiKeys([hashtable]$RoleApiKeys) {
    $missing = @()
    foreach ($role in $AgentRoleApiKeyVariables.Keys) {
        $value = $RoleApiKeys[$role]
        if ([string]::IsNullOrWhiteSpace([string]$value)) { $missing += $role }
    }
    return $missing
}
function Assert-AgentRoleApiKeysConfigured([hashtable]$RoleApiKeys) {
    $missing = @(Get-MissingAgentRoleApiKeys $RoleApiKeys)
    if ($missing.Count -gt 0) { throw ($missing -join ',') }
}
function Get-AgentRoleApiKeyEnvironment([string]$Role, [hashtable]$RoleApiKeys) {
    $variableName = $AgentRoleApiKeyVariables[$Role]
    $value = $RoleApiKeys[$Role]
    if ([string]::IsNullOrWhiteSpace($variableName) -or [string]::IsNullOrWhiteSpace([string]$value)) { throw $Role }
    return @{$variableName = $value}
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
        $recordedStart=ConvertTo-AgentTeamsUtcDateTime $Record.started_at; $actualStart=ConvertTo-AgentTeamsUtcDateTime $process.CreationDate
        if ([Math]::Abs(($recordedStart-$actualStart).TotalSeconds) -gt 2 -or [IO.Path]::GetFullPath([string]$process.ExecutablePath) -ine (Resolve-Path -LiteralPath $Record.executable_path).Path) { return $false }
        foreach ($argument in @($Record.argv)) { if ($process.CommandLine.IndexOf([string]$argument,[System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false } }
        return $process.CommandLine.IndexOf($workspace,[System.StringComparison]::OrdinalIgnoreCase) -ge 0
    } catch { return $false }
}
function Get-OwnedBackendListenerRecord([object]$Record) {
    if ($Record.name -cne 'backend' -or -not (Test-OwnedRecordedProcess $Record)) { throw 'backend_launcher_not_proven' }
    $owners = @(
        Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { [int]$_.OwningProcess } |
            Sort-Object -Unique
    )
    if ($owners.Count -ne 1) { throw 'backend_listener_not_proven' }
    $listenerId = [int]$owners[0]
    $listener = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerId" -ErrorAction SilentlyContinue
    if ($null -eq $listener -or [string]::IsNullOrWhiteSpace($listener.ExecutablePath) -or [string]::IsNullOrWhiteSpace($listener.CommandLine) -or [string]::IsNullOrWhiteSpace($listener.CreationDate)) { throw 'backend_listener_not_proven' }
    $rootStart = ConvertTo-AgentTeamsUtcDateTime $Record.started_at
    $listenerStart = ConvertTo-AgentTeamsUtcDateTime $listener.CreationDate
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
function Start-OwnedProcess([string]$Name,[string]$WorkingDirectory,[string]$ExecutablePath,[object[]]$Arguments,[hashtable]$Environment=$null) {
    $started=Start-AgentTeamsOwnedProcess -ExecutablePath $ExecutablePath -WorkingDirectory $WorkingDirectory -Arguments $Arguments -Environment $Environment
    $process=$started.Process; Start-Sleep -Milliseconds 700
    if ($process.HasExited) { throw "$Name exited during startup (exit code $($process.ExitCode))." }
    return @{name=$Name;process_id=[int]$process.Id;executable_path=$started.ExecutablePath;working_directory=$started.WorkingDirectory;argv=@($started.Arguments);workspace=$workspace;started_at=$process.StartTime.ToUniversalTime().ToString('o')}
}
function ConvertTo-AgentTeamsProcessRecord([object]$Record) {
    $public = [ordered]@{}
    foreach ($name in 'name','process_id','executable_path','working_directory','argv','workspace','started_at') {
        $value = Get-AgentTeamsRecordMember $Record $name
        $public[$name] = if ($name -eq 'argv') { @($value) } else { $value }
    }
    if (Test-AgentTeamsRecordMember $Record 'listener') {
        $listener = Get-AgentTeamsRecordMember $Record 'listener'
        $public.listener = [ordered]@{}
        foreach ($name in 'process_id','parent_process_id','executable_path','started_at','port') {
            $public.listener[$name] = Get-AgentTeamsRecordMember $listener $name
        }
    }
    return $public
}
function Write-ProcessRecord([object[]]$Processes,[string]$Status) {
    $publicProcesses = @($Processes | ForEach-Object { ConvertTo-AgentTeamsProcessRecord $_ })
    New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null; $payload=@{workspace=$workspace;status=$Status;created_at=[DateTime]::UtcNow.ToString('o');processes=$publicProcesses}|ConvertTo-Json -Depth 6
    $temporary=Join-Path $runtimeDirectory ('.pids.'+[guid]::NewGuid().ToString('N')+'.tmp'); [IO.File]::WriteAllText($temporary,$payload,[Text.UTF8Encoding]::new($false)); Move-Item -LiteralPath $temporary -Destination $pidsPath -Force
}
function Get-StartProvenProcessIds([object]$Record) {
    if (-not (Test-OwnedRecordedProcess $Record)) { return @() }
    if ($Record.name -cne 'backend') { return @([int]$Record.process_id) }
    try {
        $actual=Get-OwnedBackendListenerRecord $Record
        if (Test-AgentTeamsRecordMember $Record 'listener') {
            $recorded=Get-AgentTeamsRecordMember $Record 'listener'
            if ([int]$actual.process_id -ne [int]$recorded.process_id -or [int]$actual.port -ne [int]$recorded.port -or [IO.Path]::GetFullPath([string]$actual.executable_path) -ine [IO.Path]::GetFullPath([string]$recorded.executable_path)) { return @() }
            if ([Math]::Abs(((ConvertTo-AgentTeamsUtcDateTime $actual.started_at)-(ConvertTo-AgentTeamsUtcDateTime $recorded.started_at)).TotalSeconds) -gt 2) { return @() }
            if ([int]$actual.process_id -ne [int]$Record.process_id -and [int]$recorded.parent_process_id -ne [int]$Record.process_id) { return @() }
        }
        if ([int]$actual.process_id -eq [int]$Record.process_id) { return @([int]$Record.process_id) }
        if ([int]$actual.parent_process_id -ne [int]$Record.process_id) { return @() }
        return @([int]$actual.process_id,[int]$Record.process_id)
    } catch { return @([int]$Record.process_id) }
}
function Stop-OnlyOwnedRecords([object[]]$Records) { foreach ($record in $Records) { foreach ($processId in @(Get-StartProvenProcessIds $record)) { Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue } } }
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
    $env:DWP_SKIP_AUTO_SEED = '1'
    $env:DWP_AGENTTEAMS_RUNTIME_CONFIG = $runtimeConfig
}
function Assert-NoExistingDemoProcesses {
    if (-not (Test-Path -LiteralPath $pidsPath)) { return }; try { $saved=Get-Content -LiteralPath $pidsPath -Raw|ConvertFrom-Json } catch { throw 'Invalid existing AgentTeams PID record; refusing duplicate start.' }
    if ($saved.status -eq 'running') { foreach ($record in @($saved.processes)) { if ($NoFrontend -and $record.name -eq 'frontend') { continue }; if (Test-OwnedRecordedProcess $record) { throw "An AgentTeams real-demo process is already running for this worktree (PID $($record.process_id))." } } }
}
function Wait-SynapseReady { for ($attempt=0;$attempt -lt 30;$attempt++) { try { if ((Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8008/_matrix/client/versions' -TimeoutSec 2).StatusCode -eq 200) { return $true } } catch {}; Start-Sleep -Seconds 1 }; return $false }
if ($MyInvocation.InvocationName -eq '.') { return }

$roleApiKeys = Get-AgentRoleApiKeyValues
Assert-AgentRoleApiKeysConfigured $roleApiKeys
if (-not (Test-CommandAvailable 'docker')) { throw 'Docker Desktop CLI is unavailable.' }; & docker version --format '{{.Server.Version}}' 1>$null 2>$null; if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop daemon is unavailable.' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Backend virtual environment Python is missing.' }; Assert-NoExistingDemoProcesses
if (-not $NoFrontend) { if (-not (Test-CommandAvailable 'node')) { throw 'Node.js is unavailable.' }; if (-not (Test-Path -LiteralPath (Join-Path $workspace 'frontend\node_modules\vite\bin\vite.js'))) { throw 'Frontend dependencies are missing.' } }
$alreadyRunning=@(& docker compose -p dwp-agentteams -f $composeFile ps --status running --services 2>$null); if ($LASTEXITCODE -ne 0) { throw 'Unable to determine whether the scoped Synapse project is already running.' }; if (@($alreadyRunning|Where-Object {$_ -eq 'synapse'}).Count -gt 0) { throw 'The dwp-agentteams Synapse project is already running; refusing to claim or stop it.' }
Set-AgentTeamsStrictRuntimeEnvironment; $records=@(); $ownedCompose=$false; $success=$false
try {
    & $python $bootstrap --runtime-dir $runtimeDirectory --prepare-synapse-env; if ($LASTEXITCODE -ne 0) { throw 'Synapse secret preparation failed.' }
    $ownedCompose=$true; & docker compose -p dwp-agentteams -f $composeFile up -d; if ($LASTEXITCODE -ne 0) { throw 'Synapse compose startup failed.' }; if (-not (Wait-SynapseReady)) { throw 'Synapse did not become ready at 127.0.0.1:8008.' }
    & $python $bootstrap --runtime-dir $runtimeDirectory --matrix-url 'http://127.0.0.1:8008'; if ($LASTEXITCODE -ne 0) { throw 'Matrix account and private-room bootstrap failed.' }
    $backendRecord=Start-OwnedProcess 'backend' $backend $python (Get-ExpectedArguments 'backend'); $records+=$backendRecord
    $listener=$null; for ($attempt=0;$attempt -lt 40 -and $null -eq $listener;$attempt++) { try { $listener=Get-OwnedBackendListenerRecord $backendRecord } catch { if ($attempt -eq 39) { throw }; Start-Sleep -Milliseconds 250 } }; $backendRecord.listener=$listener
    foreach ($role in 'manager','hr','it','audit') { $records+=Start-OwnedProcess $role $workspace $python (Get-ExpectedArguments $role) (Get-AgentRoleApiKeyEnvironment $role $roleApiKeys) }; if (-not $NoFrontend) { $records+=Start-OwnedProcess 'frontend' (Join-Path $workspace 'frontend') (Get-ExpectedExecutable 'frontend') (Get-ExpectedArguments 'frontend') }
    Write-ProcessRecord $records 'running'; $success=$true; Write-Host "AgentTeams strict demo started: recorded $($records.Count) worktree-owned processes."
} catch { try { Write-ProcessRecord @() 'failed' } catch {}; throw } finally { if (-not $success) { Stop-OnlyOwnedRecords $records; if ($ownedCompose) { Stop-OwnedComposeProject } } }
