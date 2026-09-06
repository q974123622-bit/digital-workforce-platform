function Add-AgentTeamsBackslashes {
    param([System.Text.StringBuilder]$Builder, [int]$Count)
    if ($Count -gt 0) { [void]$Builder.Append((-join ('\' * $Count))) }
}

function ConvertTo-AgentTeamsWindowsArgument {
    <# CommandLineToArgvW/CRT-compatible quoting for ProcessStartInfo.Arguments. #>
    param([AllowNull()][string]$Argument)
    if ($null -eq $Argument -or $Argument.Length -eq 0) { return '""' }
    if ($Argument -notmatch '[\s"]') { return $Argument }

    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq '\') { $backslashes++; continue }
        if ($character -eq '"') {
            Add-AgentTeamsBackslashes $builder (($backslashes * 2) + 1)
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        Add-AgentTeamsBackslashes $builder $backslashes
        [void]$builder.Append($character)
        $backslashes = 0
    }
    Add-AgentTeamsBackslashes $builder ($backslashes * 2)
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Set-AgentTeamsProcessArguments {
    param([object]$Info, [object[]]$Arguments)
    $argumentList = $Info.PSObject.Properties['ArgumentList']
    if ($null -ne $argumentList -and $null -ne $argumentList.Value) {
        foreach ($argument in $Arguments) { [void]$Info.ArgumentList.Add([string]$argument) }
        return $true
    }
    $Info.Arguments = ((@($Arguments) | ForEach-Object { ConvertTo-AgentTeamsWindowsArgument $_ }) -join ' ')
    return $false
}

function Set-AgentTeamsProcessRoleKeyEnvironment {
    <# Keep parent role credentials out of every child except its intended Agent. #>
    param([object]$Info, [hashtable]$Environment)

    $roleKeyNames = @(
        'DEEPSEEK_API_KEY',
        'DEEPSEEK_API_KEY_MANAGER',
        'DEEPSEEK_API_KEY_HR',
        'DEEPSEEK_API_KEY_IT',
        'DEEPSEEK_API_KEY_AUDIT'
    )
    $processEnvironment = $Info.Environment
    if ($null -eq $processEnvironment) { $processEnvironment = $Info.EnvironmentVariables }
    if ($null -eq $processEnvironment) { throw 'agent_process_environment_unavailable' }
    foreach ($name in $roleKeyNames) {
        # Empty, rather than absent, prevents python-dotenv override=False from
        # reloading a sibling role's local value into this child process.
        $processEnvironment[$name] = ''
    }
    if ($null -eq $Environment) { return }
    $nonEmptyRoleKeys = 0
    foreach ($entry in $Environment.GetEnumerator()) {
        if ($entry.Key -notin $roleKeyNames[1..4] -or -not ($entry.Value -is [string])) {
            throw 'invalid_agent_role_key_environment'
        }
        if (-not [string]::IsNullOrWhiteSpace([string]$entry.Value)) { $nonEmptyRoleKeys++ }
    }
    if ($nonEmptyRoleKeys -gt 1) { throw 'invalid_agent_role_key_environment' }
    foreach ($entry in $Environment.GetEnumerator()) {
        $processEnvironment[[string]$entry.Key] = [string]$entry.Value
    }
}

function Start-AgentTeamsOwnedProcess {
    param(
        [string]$ExecutablePath,
        [string]$WorkingDirectory,
        [object[]]$Arguments,
        [hashtable]$Environment = $null
    )
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = (Resolve-Path -LiteralPath $ExecutablePath).Path
    $info.WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $usedArgumentList = Set-AgentTeamsProcessArguments -Info $info -Arguments $Arguments
    Set-AgentTeamsProcessRoleKeyEnvironment -Info $info -Environment $Environment
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    if (-not $process.Start()) { throw 'owned_process_start_failed' }
    [pscustomobject]@{
        Process = $process
        ExecutablePath = $info.FileName
        WorkingDirectory = $info.WorkingDirectory
        Arguments = @($Arguments)
        UsedArgumentList = $usedArgumentList
    }
}

function ConvertTo-AgentTeamsUtcDateTime {
    param([Parameter(Mandatory = $true)][object]$Value)

    if ($Value -is [DateTime]) {
        return ([DateTime]$Value).ToUniversalTime()
    }
    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).UtcDateTime
    }
    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) {
        throw 'invalid_process_creation_time'
    }
    if ($text -match '^\d{14}\.\d{6}[+-]\d{3}$') {
        return [System.Management.ManagementDateTimeConverter]::ToDateTime($text).ToUniversalTime()
    }
    return [DateTimeOffset]::Parse(
        $text,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind
    ).UtcDateTime
}

function Get-AgentTeamsVenvBasePython {
    param([Parameter(Mandatory = $true)][string]$LauncherPath)

    $configuration = Join-Path (Split-Path (Split-Path $LauncherPath -Parent) -Parent) 'pyvenv.cfg'
    $line = Get-Content -LiteralPath $configuration -ErrorAction Stop |
        Where-Object { $_ -match '^\s*executable\s*=\s*\S' } |
        Select-Object -First 1
    if ($null -eq $line) { throw 'venv_base_python_not_found' }
    $candidate = (($line -split '=', 2)[1]).Trim()
    return (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path
}

function Test-AgentTeamsRecordMember {
    param(
        [Parameter(Mandatory = $true)][object]$Record,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if ($Record -is [Collections.IDictionary]) {
        return $Record.Contains($Name)
    }
    return $null -ne $Record.PSObject.Properties[$Name]
}

function Get-AgentTeamsRecordMember {
    param(
        [Parameter(Mandatory = $true)][object]$Record,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if ($Record -is [Collections.IDictionary]) {
        return $Record[$Name]
    }
    $property = $Record.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}
