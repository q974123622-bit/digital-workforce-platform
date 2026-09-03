param(
    [string]$Version = "0.2.0",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$releaseRoot = Join-Path $repoRoot "release"
$packageName = "dwp-ai-employee-platform-offline-$Version-linux-amd64"
$stage = Join-Path $releaseRoot $packageName
$archive = Join-Path $releaseRoot "$packageName.zip"
$imageTar = Join-Path $stage "images\dwp-ai-employee-platform-images-linux-amd64.tar"

docker info | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker Engine is unavailable" }

if (-not $SkipBuild) {
    docker build --platform linux/amd64 -f "$repoRoot\docker\Dockerfile.backend" -t "dwp-backend:0.2.0-offline" $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "Backend image build failed" }
    docker build --platform linux/amd64 -f "$repoRoot\docker\Dockerfile.frontend" -t "dwp-frontend:0.2.0-offline" $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "Frontend image build failed" }
    docker build --platform linux/amd64 -f "$repoRoot\docker\Dockerfile.dsh" -t "dwp-dsh:rc6" $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "Harness image build failed" }
}

# Cleanup is restricted to the exact versioned folder under release/.
$resolvedRelease = [IO.Path]::GetFullPath($releaseRoot)
$resolvedStage = [IO.Path]::GetFullPath($stage)
if (-not $resolvedStage.StartsWith($resolvedRelease + [IO.Path]::DirectorySeparatorChar) -or
    [IO.Path]::GetFileName($resolvedStage) -ne $packageName) {
    throw "Refusing to clean unsafe release path: $resolvedStage"
}
if (Test-Path -LiteralPath $resolvedStage) {
    Remove-Item -LiteralPath $resolvedStage -Recurse -Force
}

foreach ($directory in @("images", "secrets", "data", "backups", "harness")) {
    New-Item -ItemType Directory -Path (Join-Path $stage $directory) -Force | Out-Null
}

foreach ($file in @(
    "compose.yaml", "config.env.example", "start.sh", "stop.sh",
    "status.sh", "backup.sh", "verify.sh", "README-OFFLINE.md"
)) {
    Copy-Item (Join-Path $PSScriptRoot $file) $stage
}

docker save -o $imageTar "dwp-backend:0.2.0-offline" "dwp-frontend:0.2.0-offline" "dwp-dsh:rc6"
if ($LASTEXITCODE -ne 0) { throw "Offline image export failed" }

Get-ChildItem $stage -Filter "*.sh" | ForEach-Object {
    $content = [IO.File]::ReadAllText($_.FullName) -replace "`r`n", "`n"
    [IO.File]::WriteAllText($_.FullName, $content, [Text.UTF8Encoding]::new($false))
}

$hash = (Get-FileHash -Algorithm SHA256 $imageTar).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $stage "images\SHA256SUMS") `
    -Value "$hash  dwp-ai-employee-platform-images-linux-amd64.tar" -Encoding ascii

$forbidden = Get-ChildItem $stage -Recurse -Force | Where-Object {
    $_.Name -eq ".env" -or $_.Name -like ".env.*" -or
    $_.Extension -in @(".db", ".log", ".key", ".pem")
}
if ($forbidden) {
    throw "Forbidden files found in release: $($forbidden.FullName -join ', ')"
}

if (Test-Path -LiteralPath $archive) {
    Remove-Item -LiteralPath $archive -Force
}
Compress-Archive -Path "$stage\*" -DestinationPath $archive -CompressionLevel Optimal

Write-Host "Offline directory: $stage"
Write-Host "Offline archive: $archive"
Write-Host "Archive SHA256: $((Get-FileHash -Algorithm SHA256 $archive).Hash.ToLowerInvariant())"
