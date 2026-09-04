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

function Start-AgentTeamsOwnedProcess {
    param(
        [string]$ExecutablePath,
        [string]$WorkingDirectory,
        [object[]]$Arguments
    )
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = (Resolve-Path -LiteralPath $ExecutablePath).Path
    $info.WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $usedArgumentList = Set-AgentTeamsProcessArguments -Info $info -Arguments $Arguments
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
