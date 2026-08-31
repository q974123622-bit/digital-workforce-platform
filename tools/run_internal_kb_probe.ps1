param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("list", "retrieve")]
    [string]$Command,

    [string]$Keyword,
    [int]$KbId,
    [string]$Question
)

$ErrorActionPreference = "Stop"
$envPath = Join-Path $PSScriptRoot ".env"
$probePath = Join-Path $PSScriptRoot "internal_kb_probe.py"
$requiredNames = @(
    "DWP_INTERNAL_KB_BASE_URL",
    "DWP_INTERNAL_KB_X_ORG",
    "DWP_INTERNAL_KB_X_TENANT",
    "DWP_INTERNAL_KB_X_USER",
    "DWP_INTERNAL_KB_AUTHORIZATION"
)

if (-not (Test-Path $envPath -PathType Leaf)) {
    throw "Missing local configuration: tools/.env"
}

try {
    foreach ($line in Get-Content $envPath -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        $separator = $trimmed.IndexOf("=")
        if ($separator -lt 1) {
            throw "Invalid tools/.env line: expected NAME=VALUE"
        }

        $name = $trimmed.Substring(0, $separator).Trim()
        if ($name -notin $requiredNames) {
            throw "Unsupported variable in tools/.env: $name"
        }

        $value = $trimmed.Substring($separator + 1).Trim()
        if ($value.Length -ge 2) {
            $first = $value[0]
            $last = $value[$value.Length - 1]
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        Set-Item -Path "Env:$name" -Value $value
    }

    $missing = @($requiredNames | Where-Object {
        [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_, "Process"))
    })
    if ($missing.Count -gt 0) {
        throw "Missing values in tools/.env: $($missing -join ', ')"
    }

    $python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (-not (Test-Path $python -PathType Leaf)) {
        $pythonCommand = Get-Command python -ErrorAction Stop
        $python = $pythonCommand.Source
    }

    $arguments = @($probePath, $Command)
    if ($Command -eq "list") {
        if ($Keyword) {
            $arguments += @("--keyword", $Keyword)
        }
    } else {
        if ($KbId -le 0 -or [string]::IsNullOrWhiteSpace($Question)) {
            throw "retrieve requires -KbId and -Question"
        }
        $arguments += @("--kb-id", $KbId, "--question", $Question)
    }

    & $python @arguments
    $probeExitCode = $LASTEXITCODE
} finally {
    foreach ($name in $requiredNames) {
        Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
    }
}

exit $probeExitCode