param(
    [string]$Version = "0.3.0",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$releaseRoot = Join-Path $repoRoot "release"
$packageName = "dwp-ai-employee-platform-offline-$Version-linux-amd64"
$stage = Join-Path $releaseRoot $packageName
$archive = Join-Path $releaseRoot "$packageName.zip"
$imageTar = Join-Path $stage "images\dwp-ai-employee-platform-images-linux-amd64.tar"
$imageTag = "$Version-offline"
$backendImage = "dwp-backend:$imageTag"
$frontendImage = "dwp-frontend:$imageTag"
$frontendBuilderImage = "dwp-frontend-builder:$imageTag"
$dshImage = "dwp-dsh:rc6"

docker info | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker Engine is unavailable" }

if (-not $SkipBuild) {
    docker build --platform linux/amd64 -f "$repoRoot\docker\Dockerfile.backend" -t $backendImage $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "Backend image build failed" }
    docker build --platform linux/amd64 --target build -f "$repoRoot\docker\Dockerfile.frontend" -t $frontendBuilderImage $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "Frontend builder image build failed" }
    docker build --platform linux/amd64 -f "$repoRoot\docker\Dockerfile.frontend" -t $frontendImage $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "Frontend image build failed" }
    docker build --platform linux/amd64 -f "$repoRoot\docker\Dockerfile.dsh" -t $dshImage $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "Harness image build failed" }
}

foreach ($requiredImage in @($backendImage, $frontendImage, $frontendBuilderImage, $dshImage)) {
    docker image inspect $requiredImage | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Required image is unavailable: $requiredImage" }
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

foreach ($directory in @("images", "secrets", "data", "backups", "harness", "source")) {
    New-Item -ItemType Directory -Path (Join-Path $stage $directory) -Force | Out-Null
}

foreach ($file in @(
    "compose.yaml", "config.env.example", "start.sh", "stop.sh",
    "status.sh", "backup.sh", "verify.sh", "rebuild.sh", "README-OFFLINE.md",
    "REBUILD-ON-BASTION.md", "DEPLOYMENT-AND-TEST.md"
)) {
    Copy-Item (Join-Path $PSScriptRoot $file) $stage
}

# Copy the current tracked source tree, excluding presentation output and old
# release artifacts. Files are copied from the working tree so packaging and
# adapter documentation edits made for this release are preserved.
$trackedFiles = @(& git -C $repoRoot ls-files)
if ($LASTEXITCODE -ne 0 -or -not $trackedFiles) { throw "Unable to enumerate tracked source files" }
$excludedPrefixes = @("output/", "release/")
foreach ($relativePath in $trackedFiles) {
    $normalized = $relativePath.Replace("\", "/")
    if ($excludedPrefixes | Where-Object { $normalized.StartsWith($_) }) { continue }
    $sourceFile = Join-Path $repoRoot $relativePath
    if (-not (Test-Path -LiteralPath $sourceFile -PathType Leaf)) { continue }
    $destination = Join-Path (Join-Path $stage "source") $relativePath
    New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $sourceFile -Destination $destination
}

# These release files may be new in the working tree before the release commit.
foreach ($relativePath in @(
    "docs\TARGET_BASTION_ENVIRONMENT.md",
    "docs\EXTENSION_CONFIGURATION_GUIDE.md",
    "docs\UI_REDESIGN_IMPLEMENTATION.md",
    "deploy\offline\DEPLOYMENT-AND-TEST.md",
    "backend\tests\test_llm_config.py",
    "deploy\offline\rebuild.sh",
    "deploy\offline\REBUILD-ON-BASTION.md",
    "deploy\offline\rebuild\Dockerfile.backend-hotfix",
    "deploy\offline\rebuild\Dockerfile.frontend-hotfix",
    "deploy\offline\rebuild\Dockerfile.dsh-profile-hotfix",
    "extensions\README.md",
    "extensions\mcp-example\plugin.yaml",
    "extensions\mcp-example\test-cases.json"
)) {
    $sourceFile = Join-Path $repoRoot $relativePath
    if (Test-Path -LiteralPath $sourceFile -PathType Leaf) {
        $destination = Join-Path (Join-Path $stage "source") $relativePath
        New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $sourceFile -Destination $destination -Force
    }
}

# Overlay complete application source folders from the working tree. This is
# deliberate: UI refactors often introduce new components and offline fonts
# before the release commit exists. Restrict the overlay to source-only roots
# that cannot contain runtime databases, logs or local secret files.
foreach ($relativeDirectory in @(
    "backend\app",
    "backend\tests",
    "frontend\src",
    "frontend\public",
    "shared-schema"
)) {
    $sourceDirectory = Join-Path $repoRoot $relativeDirectory
    if (Test-Path -LiteralPath $sourceDirectory -PathType Container) {
        $destinationDirectory = Join-Path (Join-Path $stage "source") $relativeDirectory
        New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
        Copy-Item -Path (Join-Path $sourceDirectory "*") -Destination $destinationDirectory -Recurse -Force
    }
}

$baseCommit = (& git -C $repoRoot rev-parse HEAD).Trim()
@(
    "base_commit=$baseCommit"
    "package_version=$Version"
    "packaged_at_utc=$([DateTime]::UtcNow.ToString('o'))"
    "source_policy=tracked working-tree files; output, release, secrets and runtime data excluded"
) | Set-Content -LiteralPath (Join-Path $stage "SOURCE-REVISION.txt") -Encoding utf8

docker save -o $imageTar $backendImage $frontendImage $frontendBuilderImage $dshImage
if ($LASTEXITCODE -ne 0) { throw "Offline image export failed" }

Get-ChildItem $stage -Filter "*.sh" | ForEach-Object {
    $content = [IO.File]::ReadAllText($_.FullName) -replace "`r`n", "`n"
    [IO.File]::WriteAllText($_.FullName, $content, [Text.UTF8Encoding]::new($false))
}

$hash = (Get-FileHash -Algorithm SHA256 $imageTar).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $stage "images\SHA256SUMS") `
    -Value "$hash  dwp-ai-employee-platform-images-linux-amd64.tar" -Encoding ascii

$forbidden = Get-ChildItem $stage -Recurse -Force | Where-Object {
    $_.Name -eq ".env" -or ($_.Name -like ".env.*" -and $_.Name -ne ".env.example") -or
    $_.Extension -in @(".db", ".log", ".key", ".pem")
}
if ($forbidden) {
    throw "Forbidden files found in release: $($forbidden.FullName -join ', ')"
}

if (Test-Path -LiteralPath $archive) {
    Remove-Item -LiteralPath $archive -Force
}
Compress-Archive -Path "$stage\*" -DestinationPath $archive -CompressionLevel Optimal
$archiveHash = (Get-FileHash -Algorithm SHA256 $archive).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$archive.sha256" -Value "$archiveHash  $([IO.Path]::GetFileName($archive))" -Encoding ascii

Write-Host "Offline directory: $stage"
Write-Host "Offline archive: $archive"
Write-Host "Archive SHA256: $archiveHash"
