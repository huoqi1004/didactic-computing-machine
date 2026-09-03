# ============================================================
# Agent-Native ERP System - Windows PowerShell 启动脚本
# 用法: .\infra\scripts\start.ps1 [up|down|restart|logs|status|build|cleanup]
# ============================================================

param(
    [Parameter(Position=0)]
    [ValidateSet("up", "down", "restart", "build", "logs", "status", "cleanup", "help")]
    [string]$Action = "up",

    [Parameter(Position=1)]
    [string]$ServiceName = ""
)

$ErrorActionPreference = "Stop"

# ----------------------------------------------------------
# 变量定义
# ----------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$ComposeFile = Join-Path $ProjectRoot "docker-compose.yml"

# ----------------------------------------------------------
# 输出函数
# ----------------------------------------------------------
function Write-Info($message) {
    Write-Host "[INFO] $message" -ForegroundColor Cyan
}

function Write-Success($message) {
    Write-Host "[SUCCESS] $message" -ForegroundColor Green
}

function Write-Warn($message) {
    Write-Host "[WARN] $message" -ForegroundColor Yellow
}

function Write-Err($message) {
    Write-Host "[ERROR] $message" -ForegroundColor Red
}

function Write-Header {
    Write-Host "============================================" -ForegroundColor Blue
    Write-Host "  Agent-Native ERP System" -ForegroundColor Blue
    Write-Host "  Docker 部署管理脚本" -ForegroundColor Blue
    Write-Host "============================================" -ForegroundColor Blue
    Write-Host ""
}

function Write-Services {
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host "  服务访问地址" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  前端界面:     http://localhost:3000" -ForegroundColor White
    Write-Host "  后端API:      http://localhost:8000" -ForegroundColor White
    Write-Host "  API文档:      http://localhost:8000/docs" -ForegroundColor White
    Write-Host "  健康检查:     http://localhost:8000/health" -ForegroundColor White
    Write-Host "  Prometheus:   http://localhost:9090" -ForegroundColor White
    Write-Host "  Grafana:      http://localhost:3001 (admin/admin)" -ForegroundColor White
    Write-Host ""
    Write-Host "  MySQL:        localhost:3306 (erp_db / erp / erp_password)" -ForegroundColor White
    Write-Host "  Redis:        localhost:6379" -ForegroundColor White
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
}

# ----------------------------------------------------------
# 前置检查
# ----------------------------------------------------------
function Test-Prerequisites {
    Write-Info "检查前置条件..."

    # 检查 Docker
    try {
        $null = docker --version 2>&1
        if ($LASTEXITCODE -ne 0) { throw "Docker not found" }
    }
    catch {
        Write-Err "Docker 未安装或未运行。请先安装 Docker Desktop。"
        Write-Info "下载地址: https://www.docker.com/products/docker-desktop"
        exit 1
    }

    # 检查 Docker Compose
    try {
        $null = docker compose version 2>&1
        if ($LASTEXITCODE -ne 0) { throw "Docker Compose not found" }
    }
    catch {
        Write-Err "Docker Compose 不可用。请更新 Docker Desktop。"
        exit 1
    }

    # 检查 Docker 守护进程
    try {
        $null = docker info 2>&1
        if ($LASTEXITCODE -ne 0) { throw "Docker daemon not running" }
    }
    catch {
        Write-Err "Docker 守护进程未运行。请启动 Docker Desktop。"
        exit 1
    }

    Write-Success "前置条件检查通过"
}

# ----------------------------------------------------------
# 检查并创建 .env 文件
# ----------------------------------------------------------
function Test-EnvFile {
    $envPath = Join-Path $ProjectRoot "backend\.env"
    $envExamplePath = Join-Path $ProjectRoot "backend\.env.example"

    if (-not (Test-Path $envPath)) {
        Write-Warn "未找到 backend\.env 文件"
        Write-Info "从模板创建 backend\.env ..."
        Copy-Item $envExamplePath $envPath
        Write-Warn "请根据需要修改 backend\.env 中的配置 (特别是密码和密钥)"
    }
}

# ----------------------------------------------------------
# 启动服务
# ----------------------------------------------------------
function Start-Services {
    Test-EnvFile
    Write-Info "构建并启动所有服务..."
    Set-Location $ProjectRoot
    docker compose -f $ComposeFile up -d --build

    if ($LASTEXITCODE -ne 0) {
        Write-Err "服务启动失败"
        exit 1
    }

    Write-Info "等待服务健康检查完成..."
    Start-Sleep -Seconds 10

    Write-Services
    Write-Success "所有服务已启动"
}

# ----------------------------------------------------------
# 停止服务
# ----------------------------------------------------------
function Stop-Services {
    Write-Info "停止所有服务..."
    Set-Location $ProjectRoot
    docker compose -f $ComposeFile down

    if ($LASTEXITCODE -eq 0) {
        Write-Success "所有服务已停止"
    } else {
        Write-Err "停止服务时出错"
    }
}

# ----------------------------------------------------------
# 重启服务
# ----------------------------------------------------------
function Restart-Services {
    Write-Info "重启所有服务..."
    Set-Location $ProjectRoot
    docker compose -f $ComposeFile restart

    if ($LASTEXITCODE -eq 0) {
        Write-Success "所有服务已重启"
    } else {
        Write-Err "重启服务时出错"
    }
}

# ----------------------------------------------------------
# 查看日志
# ----------------------------------------------------------
function View-Logs {
    Set-Location $ProjectRoot
    if ($ServiceName -ne "") {
        Write-Info "查看 $ServiceName 日志..."
        docker compose -f $ComposeFile logs -f $ServiceName
    } else {
        Write-Info "查看所有服务日志..."
        docker compose -f $ComposeFile logs -f
    }
}

# ----------------------------------------------------------
# 查看状态
# ----------------------------------------------------------
function View-Status {
    Write-Info "当前服务状态:"
    Write-Host ""
    Set-Location $ProjectRoot
    docker compose -f $ComposeFile ps
    Write-Host ""

    # 显示网络信息
    Write-Info "三域网络状态:"
    docker network ls --filter "name=erp-" --format "table {{.Name}}\t{{.Driver}}\t{{.Scope}}"
}

# ----------------------------------------------------------
# 重新构建
# ----------------------------------------------------------
function Rebuild-Services {
    Test-EnvFile
    Write-Info "重新构建所有服务..."
    Set-Location $ProjectRoot
    docker compose -f $ComposeFile up -d --build --force-recreate

    if ($LASTEXITCODE -ne 0) {
        Write-Err "重新构建失败"
        exit 1
    }

    Write-Services
    Write-Success "所有服务已重新构建并启动"
}

# ----------------------------------------------------------
# 清理数据
# ----------------------------------------------------------
function Cleanup-Services {
    Write-Warn "此操作将删除所有容器和数据卷!"
    $confirm = Read-Host "确定要继续吗? (输入 YES 确认)"

    if ($confirm -eq "YES") {
        Write-Info "停止并删除所有容器和数据卷..."
        Set-Location $ProjectRoot
        docker compose -f $ComposeFile down -v

        if ($LASTEXITCODE -eq 0) {
            Write-Success "所有容器和数据卷已删除"
        } else {
            Write-Err "清理时出错"
        }
    } else {
        Write-Info "操作已取消"
    }
}

# ----------------------------------------------------------
# 显示帮助
# ----------------------------------------------------------
function Show-Help {
    Write-Host ""
    Write-Host "用法: .\start.ps1 [命令] [服务名]"
    Write-Host ""
    Write-Host "命令:" -ForegroundColor Cyan
    Write-Host "  up         启动所有服务 (默认)"
    Write-Host "  down       停止所有服务"
    Write-Host "  restart    重启所有服务"
    Write-Host "  build      重新构建并启动所有服务"
    Write-Host "  logs       查看所有服务日志"
    Write-Host "  logs <服务名>  查看指定服务日志 (backend/frontend/mysql/redis/prometheus/grafana)"
    Write-Host "  status     查看服务状态"
    Write-Host "  cleanup    删除所有容器和数据卷 (危险操作)"
    Write-Host "  help       显示帮助信息"
    Write-Host ""
    Write-Host "示例:" -ForegroundColor Yellow
    Write-Host "  .\start.ps1              # 启动所有服务"
    Write-Host "  .\start.ps1 up           # 启动所有服务"
    Write-Host "  .\start.ps1 logs backend # 查看后端日志"
    Write-Host "  .\start.ps1 status       # 查看服务状态"
    Write-Host "  .\start.ps1 down         # 停止所有服务"
    Write-Host ""
}

# ----------------------------------------------------------
# 主逻辑
# ----------------------------------------------------------
Write-Header

switch ($Action) {
    "up" {
        Test-Prerequisites
        Start-Services
    }
    "down" {
        Stop-Services
    }
    "restart" {
        Restart-Services
    }
    "build" {
        Test-Prerequisites
        Rebuild-Services
    }
    "logs" {
        View-Logs
    }
    "status" {
        View-Status
    }
    "cleanup" {
        Cleanup-Services
    }
    "help" {
        Show-Help
    }
    default {
        Write-Err "未知命令: $Action"
        Show-Help
        exit 1
    }
}
