# ============================================================
# 企业数字化大脑 - 桌面一键停止脚本
# 扫描端口范围 (后端 8000-8005, 前端 5173-5178),
# 仅停止归属于本项目的进程, 不误杀其他项目
# ============================================================

$ErrorActionPreference = "Continue"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  企业数字化大脑 - 一键停止服务" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

function Get-ProcessCommandLine([int]$id) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
    if (-not $proc) { return "" }
    $cmdline = "$($proc.CommandLine)"
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)" -ErrorAction SilentlyContinue
    if ($parent) { $cmdline += " || $($parent.CommandLine)" }
    return $cmdline
}

function Stop-OurPortServices([int[]]$ports, [string]$name) {
    foreach ($port in $ports) {
        $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if (-not $conns) { continue }
        $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($pidValue in $pids) {
            $cmdline = Get-ProcessCommandLine $pidValue
            if ($cmdline -match "企业数字化大脑") {
                try {
                    $proc = Get-Process -Id $pidValue -ErrorAction Stop
                    Write-Host "[停止] $name (端口 $port, PID $pidValue / $($proc.ProcessName)) ..." -ForegroundColor Yellow
                    Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
                    Write-Host "[完成] $name 已停止" -ForegroundColor Green
                }
                catch {
                    Write-Host "[警告] 无法停止进程 PID $pidValue" -ForegroundColor Yellow
                }
            }
            else {
                Write-Host "[跳过] 端口 $port 被其他项目进程占用 (PID $pidValue), 未停止" -ForegroundColor Yellow
            }
        }
    }
}

$backendPorts = 8000..8005
$frontendPorts = 5173..5178

# 先停后端, 再停前端
Stop-OurPortServices $backendPorts "后端服务"
Stop-OurPortServices $frontendPorts "前端服务"

# 兜底: 若仍有本项目窗口进程 (cmd title ERP-Backend / ERP-Frontend) 未随端口进程退出, 提示即可
Write-Host ""
Write-Host "[完成] 处理结束" -ForegroundColor Green
Write-Host ""
pause
