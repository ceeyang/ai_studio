# 开发态一键启动：后端 uv run studio-server（127.0.0.1:8700）+ 前端 Vite（127.0.0.1:5173，/api 与 /media 代理到后端）。
# 生产态不需要这个脚本：cd frontend; npm run build 之后只跑 uv run studio-server，打开 http://127.0.0.1:8700。
# 用法：powershell -ExecutionPolicy Bypass -File start.ps1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$frontend = Join-Path $root 'frontend'

if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
    Write-Host '首次运行：安装前端依赖（npm install）…'
    Push-Location $frontend
    try { npm install } finally { Pop-Location }
}

Write-Host '启动后端：uv run studio-server（127.0.0.1:8700）'
$backend = Start-Process -FilePath 'uv' -ArgumentList 'run', 'studio-server' -WorkingDirectory $root -NoNewWindow -PassThru

try {
    Write-Host '启动前端：http://127.0.0.1:5173 （Ctrl+C 退出，后端会一起关掉）'
    Push-Location $frontend
    npm run dev
}
finally {
    Pop-Location
    if ($backend -and -not $backend.HasExited) {
        & taskkill /PID $backend.Id /T /F | Out-Null
    }
}
