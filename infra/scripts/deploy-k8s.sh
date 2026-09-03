#!/bin/bash

# ============================================================
# Agent-Native ERP System - Kubernetes + Istio 部署脚本
# 用法: ./infra/scripts/deploy-k8s.sh [step]
# 步骤: all | check | namespace | config | database | app | istio | verify
# ============================================================

set -e

# ----------------------------------------------------------
# 变量定义
# ----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
K8S_DIR="$PROJECT_ROOT/infra/k8s"
ISTIO_DIR="$PROJECT_ROOT/infra/istio"
STEP="${1:-all}"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

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

log_step() {
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}========================================${NC}"
}

# ----------------------------------------------------------
# 步骤 1: 检查前置条件
# ----------------------------------------------------------
check_prerequisites() {
    log_step "步骤 1: 检查前置条件"

    local has_error=0

    # 检查 kubectl
    if ! command -v kubectl &> /dev/null; then
        log_error "kubectl 未安装"
        log_info "安装指南: https://kubernetes.io/docs/tasks/tools/"
        has_error=1
    else
        log_success "kubectl 已安装: $(kubectl version --client --short 2>/dev/null || kubectl version --client 2>/dev/null | head -1)"
    fi

    # 检查 istioctl
    if ! command -v istioctl &> /dev/null; then
        log_error "istioctl 未安装"
        log_info "安装指南: https://istio.io/latest/docs/setup/getting-started/#download"
        has_error=1
    else
        log_success "istioctl 已安装: $(istioctl version 2>/dev/null | head -1)"
    fi

    # 检查 Kubernetes 集群连接
    if ! kubectl cluster-info &> /dev/null; then
        log_error "无法连接到 Kubernetes 集群"
        log_info "请确保集群已启动（k3d cluster create 或 minikube start）"
        has_error=1
    else
        log_success "Kubernetes 集群已连接"
        kubectl get nodes -o wide 2>/dev/null | head -5
    fi

    # 检查 Istio 控制面
    if ! kubectl get namespace istio-system &> /dev/null; then
        log_warn "istio-system 命名空间不存在，开始安装 Istio..."
        install_istio
    else
        log_success "Istio 控制面已安装"
        kubectl get pods -n istio-system 2>/dev/null | head -10
    fi

    if [ $has_error -eq 1 ]; then
        log_error "前置条件检查失败，请安装缺失的工具后重试"
        exit 1
    fi

    log_success "前置条件检查通过"
}

# ----------------------------------------------------------
# 安装 Istio 控制面
# ----------------------------------------------------------
install_istio() {
    log_info "安装 Istio 控制面（default 配置）..."
    istioctl install --set values.defaultRevision=default -y

    log_info "等待 Istio 组件就绪..."
    kubectl wait --for=condition=ready pod -l istio=pilot -n istio-system --timeout=120s
    kubectl wait --for=condition=ready pod -l app=istio-ingressgateway -n istio-system --timeout=120s

    log_success "Istio 控制面安装完成"
}

# ----------------------------------------------------------
# 步骤 2: 创建命名空间并启用 Sidecar 注入
# ----------------------------------------------------------
create_namespaces() {
    log_step "步骤 2: 创建命名空间并启用 Istio Sidecar 注入"

    log_info "创建三域命名空间..."
    kubectl apply -f "$K8S_DIR/namespace.yaml"

    log_info "验证命名空间标签..."
    kubectl get namespaces --show-labels | grep -E "agent-execution|data-knowledge|user-interaction"

    log_success "命名空间创建完成，Istio sidecar 注入已启用"
}

# ----------------------------------------------------------
# 步骤 3: 应用 ConfigMap 和 Secret
# ----------------------------------------------------------
apply_config() {
    log_step "步骤 3: 应用 ConfigMap 和 Secret"

    log_info "应用 ConfigMap..."
    kubectl apply -f "$K8S_DIR/configmap.yaml"

    log_info "应用 Secret..."
    kubectl apply -f "$K8S_DIR/secret.yaml"

    log_success "ConfigMap 和 Secret 已创建"
}

# ----------------------------------------------------------
# 步骤 4: 部署数据库层
# ----------------------------------------------------------
deploy_database() {
    log_step "步骤 4: 部署数据库层（MySQL + Redis）"

    log_info "部署 MySQL StatefulSet..."
    kubectl apply -f "$K8S_DIR/mysql-statefulset.yaml"

    log_info "部署 Redis Deployment..."
    kubectl apply -f "$K8S_DIR/redis-deployment.yaml"

    log_info "等待数据库就绪..."

    # 等待 MySQL 就绪
    log_info "等待 MySQL Pod 就绪（最多120秒）..."
    kubectl wait --for=condition=ready pod -l app=mysql -n data-knowledge --timeout=120s

    # 等待 Redis 就绪
    log_info "等待 Redis Pod 就绪（最多60秒）..."
    kubectl wait --for=condition=ready pod -l app=redis -n data-knowledge --timeout=60s

    log_success "数据库层部署完成"

    log_info "数据库 Pod 状态:"
    kubectl get pods -n data-knowledge -o wide
}

# ----------------------------------------------------------
# 步骤 5: 部署应用层
# ----------------------------------------------------------
deploy_app() {
    log_step "步骤 5: 部署应用层（Backend + Frontend）"

    log_info "部署 Backend Deployment..."
    kubectl apply -f "$K8S_DIR/backend-deployment.yaml"

    log_info "部署 Frontend Deployment..."
    kubectl apply -f "$K8S_DIR/frontend-deployment.yaml"

    log_info "等待 Backend Pod 就绪（最多180秒）..."
    kubectl wait --for=condition=ready pod -l app=backend -n agent-execution --timeout=180s

    log_info "等待 Frontend Pod 就绪（最多120秒）..."
    kubectl wait --for=condition=ready pod -l app=frontend -n user-interaction --timeout=120s

    log_success "应用层部署完成"

    log_info "应用 Pod 状态:"
    kubectl get pods -n agent-execution -o wide
    echo ""
    kubectl get pods -n user-interaction -o wide
}

# ----------------------------------------------------------
# 步骤 6: 应用 Istio 安全策略
# ----------------------------------------------------------
apply_istio_policies() {
    log_step "步骤 6: 应用 Istio 安全策略（mTLS + 授权策略）"

    log_info "应用 MeshConfig..."
    kubectl apply -f "$ISTIO_DIR/mesh-config.yaml"

    log_info "应用 PeerAuthentication（mTLS 策略）..."
    kubectl apply -f "$ISTIO_DIR/peer-authentication.yaml"

    log_info "应用 DestinationRule（目标规则）..."
    kubectl apply -f "$ISTIO_DIR/destination-rules.yaml"

    log_info "应用 Gateway 和 VirtualService..."
    kubectl apply -f "$ISTIO_DIR/gateway.yaml"

    log_info "应用 AuthorizationPolicy（授权策略）..."
    kubectl apply -f "$ISTIO_DIR/authorization-policies.yaml"

    log_info "应用 ServiceEntry（外部服务入口）..."
    kubectl apply -f "$ISTIO_DIR/service-entries.yaml"

    log_info "应用 EnvoyFilter（审计过滤器）..."
    kubectl apply -f "$ISTIO_DIR/envoy-filter-audit.yaml"

    log_success "Istio 安全策略已应用"

    log_info "Istio 配置分析:"
    istioctl analyze 2>/dev/null || log_warn "istioctl analyze 跳过"
}

# ----------------------------------------------------------
# 步骤 7: 验证部署
# ----------------------------------------------------------
verify_deployment() {
    log_step "步骤 7: 验证部署"

    log_info "1. 验证所有 Pod 状态（应有 2/2 容器：应用 + istio-proxy）..."

    echo ""
    log_info "Agent 执行域:"
    kubectl get pods -n agent-execution

    echo ""
    log_info "数据知识域:"
    kubectl get pods -n data-knowledge

    echo ""
    log_info "用户交互域:"
    kubectl get pods -n user-interaction

    echo ""
    log_info "2. 验证 Sidecar 注入状态..."
    for ns in agent-execution data-knowledge user-interaction; do
        local pod_count=$(kubectl get pods -n $ns --no-headers 2>/dev/null | wc -l)
        local ready_count=$(kubectl get pods -n $ns --no-headers 2>/dev/null | grep "2/2" | wc -l)
        if [ "$pod_count" -eq "$ready_count" ]; then
            log_success "命名空间 $ns: 所有 Pod 已注入 sidecar ($ready_count/$pod_count)"
        else
            log_warn "命名空间 $ns: 部分 Pod 未就绪 ($ready_count/$pod_count)"
        fi
    done

    echo ""
    log_info "3. 验证 Istio 配置..."
    istioctl analyze 2>/dev/null || true

    echo ""
    log_info "4. 验证 mTLS 策略..."
    kubectl get peerauthentication --all-namespaces

    echo ""
    log_info "5. 验证 Gateway 和 VirtualService..."
    kubectl get gateway --all-namespaces
    kubectl get virtualservice --all-namespaces

    echo ""
    log_info "6. 验证授权策略..."
    kubectl get authorizationpolicy --all-namespaces

    echo ""
    log_info "7. 验证服务状态..."
    kubectl get svc --all-namespaces | grep -E "backend|frontend|mysql|redis"

    echo ""
    log_success "部署验证完成"

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  部署完成！${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "  ${CYAN}访问方式:${NC}"
    echo -e "  ${CYAN}前端界面:${NC}     http://localhost:3000 (port-forward)"
    echo -e "  ${CYAN}后端API:${NC}      http://localhost:8000 (port-forward)"
    echo -e "  ${CYAN}健康检查:${NC}     http://localhost:8000/health"
    echo ""
    echo -e "  ${CYAN}端口转发命令:${NC}"
    echo -e "  kubectl port-forward -n user-interaction svc/frontend 3000:3000"
    echo -e "  kubectl port-forward -n agent-execution svc/backend 8000:8000"
    echo ""
    echo -e "  ${CYAN}mTLS 验证:${NC}"
    echo -e "  ./infra/scripts/verify-mtls.sh"
    echo ""
}

# ----------------------------------------------------------
# 显示帮助
# ----------------------------------------------------------
show_help() {
    echo ""
    echo "用法: $0 [步骤]"
    echo ""
    echo "步骤:"
    echo "  all        执行完整部署（默认）"
    echo "  check      仅检查前置条件"
    echo "  namespace  仅创建命名空间"
    echo "  config     仅应用 ConfigMap 和 Secret"
    echo "  database   仅部署数据库层"
    echo "  app        仅部署应用层"
    echo "  istio      仅应用 Istio 安全策略"
    echo "  verify     仅验证部署"
    echo "  help       显示帮助信息"
    echo ""
}

# ----------------------------------------------------------
# 主逻辑
# ----------------------------------------------------------
main() {
    echo -e "${BLUE}"
    echo "============================================"
    echo "  Agent-Native ERP System"
    echo "  Kubernetes + Istio 部署脚本"
    echo "============================================"
    echo -e "${NC}"

    case "$STEP" in
        all)
            check_prerequisites
            create_namespaces
            apply_config
            deploy_database
            deploy_app
            apply_istio_policies
            verify_deployment
            ;;
        check)
            check_prerequisites
            ;;
        namespace)
            create_namespaces
            ;;
        config)
            apply_config
            ;;
        database)
            deploy_database
            ;;
        app)
            deploy_app
            ;;
        istio)
            apply_istio_policies
            ;;
        verify)
            verify_deployment
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "未知步骤: $STEP"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
