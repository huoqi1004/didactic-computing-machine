#!/bin/bash

# ============================================================
# Agent-Native ERP System - Linux 启动脚本
# 用法: ./infra/scripts/start.sh [up|down|restart|logs|status|build]
# ============================================================

set -e

# ----------------------------------------------------------
# 变量定义
# ----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"
ACTION="${1:-up}"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ----------------------------------------------------------
# 输出函数
# ----------------------------------------------------------
log_info() {
    echo -e "${CYAN}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "${BLUE}"
    echo "============================================"
    echo "  Agent-Native ERP System"
    echo "  Docker 部署管理脚本"
    echo "============================================"
    echo -e "${NC}"
}

print_services() {
    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}  服务访问地址${NC}"
    echo -e "${GREEN}============================================${NC}"
    echo ""
    echo -e "  ${CYAN}前端界面:${NC}     http://localhost:3000"
    echo -e "  ${CYAN}后端API:${NC}      http://localhost:8000"
    echo -e "  ${CYAN}API文档:${NC}      http://localhost:8000/docs"
    echo -e "  ${CYAN}健康检查:${NC}     http://localhost:8000/health"
    echo -e "  ${CYAN}Prometheus:${NC}   http://localhost:9090"
    echo -e "  ${CYAN}Grafana:${NC}      http://localhost:3001 (admin/admin)"
    echo ""
    echo -e "  ${CYAN}PostgreSQL:${NC}   localhost:5432 (erp_db / erp_user / erp_password)"
    echo -e "  ${CYAN}Redis:${NC}        localhost:6379"
    echo ""
    echo -e "${GREEN}============================================${NC}"
}

# ----------------------------------------------------------
# 前置检查
# ----------------------------------------------------------
check_prerequisites() {
    log_info "检查前置条件..."

    # 检查 Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker 未安装。请先安装 Docker。"
        log_info "安装指南: https://docs.docker.com/engine/install/"
        exit 1
    fi

    # 检查 Docker Compose
    if ! docker compose version &> /dev/null; then
        log_error "Docker Compose 不可用。请安装 Docker Compose V2。"
        log_info "安装指南: https://docs.docker.com/compose/install/"
        exit 1
    fi

    # 检查 Docker 守护进程
    if ! docker info &> /dev/null; then
        log_error "Docker 守护进程未运行。请启动 Docker 服务。"
        exit 1
    fi

    log_success "前置条件检查通过"
}

# ----------------------------------------------------------
# 检查并创建 .env 文件
# ----------------------------------------------------------
check_env_file() {
    if [ ! -f "$PROJECT_ROOT/backend/.env" ]; then
        log_warn "未找到 backend/.env 文件"
        log_info "从模板创建 backend/.env ..."
        cp "$PROJECT_ROOT/backend/.env.example" "$PROJECT_ROOT/backend/.env"
        log_warn "请根据需要修改 backend/.env 中的配置 (特别是密码和密钥)"
    fi
}

# ----------------------------------------------------------
# 启动服务
# ----------------------------------------------------------
start_services() {
    check_env_file
    log_info "构建并启动所有服务..."
    cd "$PROJECT_ROOT"
    docker compose -f "$COMPOSE_FILE" up -d --build

    log_info "等待服务健康检查完成..."
    sleep 10

    print_services
    log_success "所有服务已启动"
}

# ----------------------------------------------------------
# 停止服务
# ----------------------------------------------------------
stop_services() {
    log_info "停止所有服务..."
    cd "$PROJECT_ROOT"
    docker compose -f "$COMPOSE_FILE" down
    log_success "所有服务已停止"
}

# ----------------------------------------------------------
# 重启服务
# ----------------------------------------------------------
restart_services() {
    log_info "重启所有服务..."
    cd "$PROJECT_ROOT"
    docker compose -f "$COMPOSE_FILE" restart
    log_success "所有服务已重启"
}

# ----------------------------------------------------------
# 查看日志
# ----------------------------------------------------------
view_logs() {
    local service="${2:-}"
    cd "$PROJECT_ROOT"
    if [ -n "$service" ]; then
        log_info "查看 $service 日志..."
        docker compose -f "$COMPOSE_FILE" logs -f "$service"
    else
        log_info "查看所有服务日志..."
        docker compose -f "$COMPOSE_FILE" logs -f
    fi
}

# ----------------------------------------------------------
# 查看状态
# ----------------------------------------------------------
view_status() {
    log_info "当前服务状态:"
    cd "$PROJECT_ROOT"
    docker compose -f "$COMPOSE_FILE" ps
}

# ----------------------------------------------------------
# 重新构建
# ----------------------------------------------------------
rebuild_services() {
    log_info "重新构建所有服务..."
    cd "$PROJECT_ROOT"
    docker compose -f "$COMPOSE_FILE" up -d --build --force-recreate
    log_success "所有服务已重新构建并启动"
    print_services
}

# ----------------------------------------------------------
# 清理数据 (危险操作)
# ----------------------------------------------------------
cleanup_services() {
    log_warn "此操作将删除所有容器和数据卷!"
    read -p "确定要继续吗? (输入 YES 确认): " confirm
    if [ "$confirm" = "YES" ]; then
        log_info "停止并删除所有容器和数据卷..."
        cd "$PROJECT_ROOT"
        docker compose -f "$COMPOSE_FILE" down -v
        log_success "所有容器和数据卷已删除"
    else
        log_info "操作已取消"
    fi
}

# ----------------------------------------------------------
# 显示帮助
# ----------------------------------------------------------
show_help() {
    echo ""
    echo "用法: $0 [命令]"
    echo ""
    echo "命令:"
    echo "  up        启动所有服务 (默认)"
    echo "  down      停止所有服务"
    echo "  restart   重启所有服务"
    echo "  build     重新构建并启动所有服务"
    echo "  logs      查看所有服务日志"
    echo "  logs <服务名>  查看指定服务日志 (backend/frontend/postgres/redis/prometheus/grafana)"
    echo "  status    查看服务状态"
    echo "  cleanup   删除所有容器和数据卷 (危险操作)"
    echo "  help      显示帮助信息"
    echo ""
}

# ----------------------------------------------------------
# 主逻辑
# ----------------------------------------------------------
main() {
    print_header

    case "$ACTION" in
        up)
            check_prerequisites
            start_services
            ;;
        down)
            stop_services
            ;;
        restart)
            restart_services
            ;;
        build)
            check_prerequisites
            rebuild_services
            ;;
        logs)
            view_logs "$@"
            ;;
        status)
            view_status
            ;;
        cleanup)
            cleanup_services
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "未知命令: $ACTION"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
