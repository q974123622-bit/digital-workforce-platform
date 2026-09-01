$ErrorActionPreference = "Stop"

$backend = Join-Path $PSScriptRoot "..\backend"
Set-Location $backend

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".\.venv\Scripts\python.exe" -m app.seed --reset

Write-Host ""
Write-Host "Demo 环境初始化完成。"
Write-Host "启动后端: cd backend; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
Write-Host "启动前端: 仓库根目录执行 pnpm --filter frontend dev"
