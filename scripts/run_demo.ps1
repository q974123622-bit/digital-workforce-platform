param(
    [switch]$NoReset,
    [switch]$Docker
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$venvPy = Join-Path $backend ".venv\Scripts\python.exe"

function Stop-Port([int]$port) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        Write-Host ("  释放端口 {0}（旧进程 PID {1}）" -f $port, $conn.OwningProcess)
    }
}

Write-Host "======================================================"
Write-Host " 数字员工平台 Demo · 一键启动"
Write-Host "======================================================"

# 1) venv 检查（不存在则创建并装依赖）
if (-not (Test-Path $venvPy)) {
    Write-Host "[1/6] 创建 Python venv ..."
    Push-Location $backend
    python -m venv .venv
    Pop-Location
    & $venvPy -m pip install -r (Join-Path $backend "requirements.txt")
} else {
    & $venvPy -c "import fastapi, uvicorn, dotenv" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[1/6] 安装后端依赖 ..."
        & $venvPy -m pip install -r (Join-Path $backend "requirements.txt")
    } else {
        Write-Host "[1/6] Python 环境就绪"
    }
}

# 2) 前端依赖检查
if (-not (Test-Path (Join-Path $root "node_modules"))) {
    Write-Host "[2/6] 安装前端依赖 ..."
    Push-Location $root
    pnpm install
    Pop-Location
} else {
    Write-Host "[2/6] 前端依赖就绪"
}

# 3) Docker（可选）：确保 Harness 镜像
if ($Docker) {
    $img = docker images -q dwp-dsh:rc6 2>$null
    if (-not $img) {
        Write-Host "[3/6] 构建 Harness Docker 镜像（dwp-dsh:rc6，约 8 分钟）..."
        Push-Location $root
        docker build -f docker\Dockerfile.dsh -t dwp-dsh:rc6 docker\
        Pop-Location
    } else {
        Write-Host "[3/6] Harness 镜像已存在"
    }
    $env:DWP_HARNESS_ENABLED = "1"
    Write-Host "      已启用 Harness Docker 模式（Team 子任务经容器真实执行，耗时更长）"
} else {
    $env:DWP_HARNESS_ENABLED = "0"
    Write-Host "[3/6] 未启用 Docker（演示默认 demo 模式，快）"
}

# 4) 重置数据（默认）
if (-not $NoReset) {
    Write-Host "[4/6] 重置数据库 + 灌入虚构种子 ..."
    Push-Location $backend
    & $venvPy -m app.seed --reset
    Pop-Location
} else {
    Write-Host "[4/6] 跳过重置（-NoReset）"
}

# 5) 启动后端
Write-Host "[5/6] 启动后端（端口 8000）..."
Stop-Port 8000
Start-Process -FilePath $venvPy -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8000" -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardOutput (Join-Path $backend "uvicorn-out.log") -RedirectStandardError (Join-Path $backend "uvicorn-err.log")

# 6) 启动前端
Write-Host "[6/6] 启动前端（端口 5173）..."
Stop-Port 5173
Start-Process -FilePath "pnpm.cmd" -ArgumentList "--filter", "frontend", "dev" -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $root "frontend\vite-dev.log") -RedirectStandardError (Join-Path $root "frontend\vite-dev.err.log")

# 等待后端就绪
Write-Host ""
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}

Write-Host "======================================================"
if ($ready) {
    Write-Host " 后端就绪：http://127.0.0.1:8000/health"
} else {
    Write-Host " 后端未就绪，请检查 backend 日志"
}
Write-Host " 前端入口：http://localhost:5173"
Write-Host ""
Write-Host " 黄金链路联调："
Write-Host "   cd backend; .\.venv\Scripts\python.exe ..\scripts\golden_chain.py"
Write-Host " 自动化测试："
Write-Host "   cd backend; .\.venv\Scripts\python.exe -m pytest tests -q"
Write-Host "======================================================"
