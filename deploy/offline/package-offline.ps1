param(
    [string]$Version = "0.1.0",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$releaseRoot = Join-Path $repoRoot "release"
$stage = Join-Path $releaseRoot "dwp-demo-offline-$Version-linux-amd64"
$archive = Join-Path $releaseRoot "dwp-demo-offline-$Version-linux-amd64.zip"
$imageTar = Join-Path $stage "images\dwp-demo-images-linux-amd64.tar"

if (-not $SkipBuild) {
    docker info | Out-Null
    docker build --platform linux/amd64 -f "$repoRoot\docker\Dockerfile.backend" -t "dwp-backend:0.1.0-offline" $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "后端 Docker 镜像构建失败" }
    docker build --platform linux/amd64 -f "$repoRoot\docker\Dockerfile.frontend" -t "dwp-frontend:0.1.0-offline" $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "前端 Docker 镜像构建失败" }
}

if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Path (Join-Path $stage "images") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stage "secrets") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stage "data") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stage "backups") -Force | Out-Null

Copy-Item "$PSScriptRoot\compose.yaml" $stage
Copy-Item "$PSScriptRoot\start.sh" $stage
Copy-Item "$PSScriptRoot\stop.sh" $stage
Copy-Item "$PSScriptRoot\status.sh" $stage
Copy-Item "$PSScriptRoot\backup.sh" $stage
Copy-Item "$PSScriptRoot\README-OFFLINE.md" $stage

docker save -o $imageTar "dwp-backend:0.1.0-offline" "dwp-frontend:0.1.0-offline"
if ($LASTEXITCODE -ne 0) { throw "离线 Docker 镜像导出失败" }

Get-ChildItem $stage -Filter "*.sh" | ForEach-Object {
    $content = [IO.File]::ReadAllText($_.FullName) -replace "`r`n", "`n"
    [IO.File]::WriteAllText($_.FullName, $content, [Text.UTF8Encoding]::new($false))
}

$hash = (Get-FileHash -Algorithm SHA256 $imageTar).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $stage "images\SHA256SUMS") -Value "$hash  dwp-demo-images-linux-amd64.tar" -Encoding ascii

if (Test-Path -LiteralPath $archive) {
    Remove-Item -LiteralPath $archive -Force
}
Compress-Archive -Path "$stage\*" -DestinationPath $archive -CompressionLevel Optimal
Write-Host "离线部署包已生成: $archive"
