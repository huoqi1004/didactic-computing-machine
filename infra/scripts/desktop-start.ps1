# ============================================================
# 企业数字化大脑 - 桌面一键启动脚本 (本地开发模式)
# 后端 uvicorn (8000, 冲突自动降级 8001-8005)
# 前端 vite    (5173, 冲突自动降级 5174-5178)
# 端口被其他项目占用时自动切换可用端口并提示, 始终尝试启动本项目
# ============================================================

$ErrorActionPreference = "Continue"

$ProjectRoot  = "E:\企业数字化大脑"
$BackendDir   = Join-Path $ProjectRoot "backend"
$FrontendDir  = Join-Path $ProjectRoot "frontend"
$Python       = Join-Path $BackendDir ".venv\Scripts\python.exe"

# ----------------------------------------------------------
# 工具函数
# ----------------------------------------------------------
function Get-PortOwner([int]$port) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $conn) { return $null }
    return Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
}

function Test-IsOurProject($proc) {
    if (-not $proc) { return $false }
    $cmdline = "$($proc.CommandLine)"
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)" -ErrorAction SilentlyContinue
    if ($parent) { $cmdline += " || $($parent.CommandLine)" }
    return ($cmdline -match "企业数字化大脑")
}

# 从起始端口开始找空闲端口; 返回端口号, 找不到返回 $null
function Get-FreePort([int]$start, [int]$end) {
    for ($p = $start; $p -le $end; $p++) {
        if (-not (Get-PortOwner $p)) { return $p }
    }
    return $null
}

function Resolve-BackendPort {
    $owner = Get-PortOwner 8000
    if (-not $owner) {
        return @{ Port = 8000; Skip = $false; Note = "" }
    }
    if (Test-IsOurProject $owner) {
        return @{ Port = 8000; Skip = $true; Note = "本项目后端已在运行" }
    }
    $alt = Get-FreePort 8001 8005
    if (-not $alt) {
        return @{ Port = $null; Skip = $false; Note = "端口 8000-8005 均被占用" }
    }
    return @{ Port = $alt; Skip = $false; Note = "端口 8000 被其他项目占用, 降级到 $alt" }
}

function Resolve-FrontendPort {
    $owner = Get-PortOwner 5173
    if (-not $owner) {
        return @{ Port = 5173; Skip = $false; Note = "" }
    }
    if (Test-IsOurProject $owner) {
        return @{ Port = 5173; Skip = $true; Note = "本项目前端已在运行" }
    }
    $alt = Get-FreePort 5174 5178
    if (-not $alt) {
        return @{ Port = $null; Skip = $false; Note = "端口 5173-5178 均被占用" }
    }
    return @{ Port = $alt; Skip = $false; Note = "端口 5173 被其他项目占用, 降级到 $alt" }
}

# ----------------------------------------------------------
# 主流程
# ----------------------------------------------------------
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  企业数字化大脑 (Agent-Native ERP) 一键启动" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 解析后端端口 ----------
$be = Resolve-BackendPort
if ($be.Skip) {
    Write-Host "[跳过] $($be.Note) -> http://localhost:$($be.Port)" -ForegroundColor Yellow
}
elseif (-not $be.Port) {
    Write-Host "[错误] $($be.Note), 无法启动本项目后端" -ForegroundColor Red
    Write-Host "       请停止占用 8000-8005 端口的进程后重试。" -ForegroundColor Red
    pause
    exit 1
}
else {
    if ($be.Port -ne 8000) {
        Write-Host "[降级] $($be.Note)" -ForegroundColor Yellow
    }
    if (-not (Test-Path $Python)) {
        Write-Host "[错误] 未找到虚拟环境: $Python" -ForegroundColor Red
        Write-Host "       请先在 backend 目录执行: python -m venv .venv" -ForegroundColor Red
        pause
        exit 1
    }
    Write-Host "[启动] 后端服务 (uvicorn :$($be.Port)) ..." -ForegroundColor Green
    Start-Process -FilePath "cmd.exe" -ArgumentList @(
        "/k",
        "title ERP-Backend && ""$Python"" -m uvicorn app.main:app --host 0.0.0.0 --port $($be.Port)"
    ) -WorkingDirectory $BackendDir
}

# ---------- 解析前端端口 ----------
$fe = Resolve-FrontendPort
if ($fe.Skip) {
    Write-Host "[跳过] $($fe.Note) -> http://localhost:$($fe.Port)" -ForegroundColor Yellow
}
elseif (-not $fe.Port) {
    Write-Host "[错误] $($fe.Note), 无法启动本项目前端" -ForegroundColor Red
    Write-Host "       请停止占用 5173-5178 端口的进程后重试。" -ForegroundColor Red
    pause
    exit 1
}
else {
    if ($fe.Port -ne 5173) {
        Write-Host "[降级] $($fe.Note)" -ForegroundColor Yellow
    }
    # 后端使用降级端口时, 让前端代理指向实际后端端口 (vite 继承环境变量)
    if ($be.Port -and $be.Port -ne 8000 -and -not $be.Skip) {
        $env:ERP_API_TARGET = "http://localhost:$($be.Port)"
        Write-Host "[配置] 前端 API 代理指向 $env:ERP_API_TARGET" -ForegroundColor DarkCyan
    }
    Write-Host "[启动] 前端服务 (vite :$($fe.Port)) ..." -ForegroundColor Green
    Start-Process -FilePath "cmd.exe" -ArgumentList @(
        "/k",
        "title ERP-Frontend && npm run dev -- --port $($fe.Port) --strictPort"
    ) -WorkingDirectory $FrontendDir
}

# ---------- 等待后端健康检查 ----------
$backendUrl = "http://localhost:$($be.Port)"
$frontendUrl = "http://localhost:$($fe.Port)"

if (-not $be.Skip) {
    Write-Host ""
    Write-Host "[等待] 后端健康检查 (最多 60 秒) ..." -ForegroundColor Cyan
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        try {
            $resp = Invoke-WebRequest -Uri "$backendUrl/health" -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) { $ready = $true; break }
        }
        catch { }
    }
    if ($ready) {
        Write-Host "[成功] 后端健康检查通过" -ForegroundColor Green
    }
    else {
        Write-Host "[警告] 后端健康检查超时, 请查看 ERP-Backend 窗口日志" -ForegroundColor Yellow
    }
}

# ---------- 打开浏览器 ----------
Start-Sleep -Seconds 2
Start-Process $frontendUrl

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  服务访问地址" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host "  前端界面:    $frontendUrl" -ForegroundColor White
Write-Host "  后端 API:    $backendUrl" -ForegroundColor White
Write-Host "  API 文档:    $backendUrl/docs" -ForegroundColor White
Write-Host "  健康检查:    $backendUrl/health" -ForegroundColor White
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "提示: 关闭 ERP-Backend / ERP-Frontend 黑色窗口即可停止对应服务;" -ForegroundColor Yellow
Write-Host "      或双击桌面上的 [企业数字化大脑-停止服务] 快捷方式一键停止。" -ForegroundColor Yellow
Write-Host ""
pause
