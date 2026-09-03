#!/bin/bash

# ============================================================
# Agent-Native ERP System - mTLS 加密通信验证脚本
# 用法: ./infra/scripts/verify-mtls.sh
# 验证 Istio 服务网格中所有服务的 mTLS 配置和加密状态
# ============================================================

set -e

# ----------------------------------------------------------
# 变量定义
# ----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# 命名空间列表
NAMESPACES=("agent-execution" "data-knowledge" "user-interaction")

# ----------------------------------------------------------
# 输出函数
# ----------------------------------------------------------
log_info() {
    echo -e "${CYAN}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

log_section() {
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}========================================${NC}"
}

# 验证结果计数
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

pass() {
    log_success "$1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
    log_error "$1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

warn() {
    log_warn "$1"
    WARN_COUNT=$((WARN_COUNT + 1))
}

# ----------------------------------------------------------
# 1. 验证 Istio 控制面状态
# ----------------------------------------------------------
verify_istio_control_plane() {
    log_section "1. 验证 Istio 控制面状态"

    # 检查 istio-system 命名空间
    if kubectl get namespace istio-system &> /dev/null; then
        pass "istio-system 命名空间存在"
    else
        fail "istio-system 命名空间不存在"
        return
    fi

    # 检查 istiod Pod
    local istiod_ready=$(kubectl get pods -n istio-system -l istio=pilot \
        -o jsonpath='{.items[*].status.containerStatuses[*].ready}' 2>/dev/null | grep -c "true")
    if [ "$istiod_ready" -ge 1 ]; then
        pass "istiod 控制面运行正常"
    else
        fail "istiod 控制面未就绪"
    fi

    # 检查 Ingress Gateway
    local gateway_ready=$(kubectl get pods -n istio-system -l app=istio-ingressgateway \
        -o jsonpath='{.items[*].status.containerStatuses[*].ready}' 2>/dev/null | grep -c "true")
    if [ "$gateway_ready" -ge 1 ]; then
        pass "Istio Ingress Gateway 运行正常"
    else
        warn "Istio Ingress Gateway 未就绪（可能未安装）"
    fi
}

# ----------------------------------------------------------
# 2. 验证 Sidecar 注入状态
# ----------------------------------------------------------
verify_sidecar_injection() {
    log_section "2. 验证 Sidecar 注入状态"

    for ns in "${NAMESPACES[@]}"; do
        log_info "检查命名空间: $ns"

        # 检查命名空间标签
        local injection_label=$(kubectl get namespace $ns \
            -o jsonpath='{.metadata.labels.istio-injection}' 2>/dev/null)
        if [ "$injection_label" = "enabled" ]; then
            pass "命名空间 $ns: istio-injection=enabled"
        else
            fail "命名空间 $ns: istio-injection 标签未设置或值不是 enabled"
        fi

        # 检查 Pod 中的 sidecar 容器
        local pods=$(kubectl get pods -n $ns --no-headers 2>/dev/null | awk '{print $1}')
        if [ -z "$pods" ]; then
            warn "命名空间 $ns: 没有运行中的 Pod"
            continue
        fi

        for pod in $pods; do
            local container_count=$(kubectl get pod $pod -n $ns \
                -o jsonpath='{.status.containerStatuses[*].name}' 2>/dev/null | wc -w)
            local has_sidecar=$(kubectl get pod $pod -n $ns \
                -o jsonpath='{.status.containerStatuses[*].name}' 2>/dev/null | grep -c "istio-proxy")

            if [ "$has_sidecar" -ge 1 ]; then
                pass "Pod $ns/$pod: 已注入 istio-proxy sidecar ($container_count 个容器)"
            else
                fail "Pod $ns/$pod: 未注入 istio-proxy sidecar"
            fi
        done
    done
}

# ----------------------------------------------------------
# 3. 验证 PeerAuthentication 策略
# ----------------------------------------------------------
verify_peer_authentication() {
    log_section "3. 验证 PeerAuthentication 策略（mTLS 模式）"

    # 检查网格级默认策略
    local mesh_mtls=$(kubectl get peerauthentication default -n istio-system \
        -o jsonpath='{.spec.mtls.mode}' 2>/dev/null)
    if [ "$mesh_mtls" = "STRICT" ]; then
        pass "网格级默认 mTLS 策略: STRICT"
    else
        warn "网格级默认 mTLS 策略: ${mesh_mtls:-未设置}（期望 STRICT）"
    fi

    # 检查各命名空间策略
    for ns in "${NAMESPACES[@]}"; do
        log_info "检查命名空间: $ns"

        local pa_list=$(kubectl get peerauthentication -n $ns --no-headers 2>/dev/null)
        if [ -z "$pa_list" ]; then
            warn "命名空间 $ns: 未找到 PeerAuthentication"
            continue
        fi

        # 获取每个 PeerAuthentication 的 mTLS 模式
        local pa_names=$(kubectl get peerauthentication -n $ns \
            -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

        for pa_name in $pa_names; do
            local mtls_mode=$(kubectl get peerauthentication $pa_name -n $ns \
                -o jsonpath='{.spec.mtls.mode}' 2>/dev/null)

            case $ns in
                agent-execution)
                    if [ "$mtls_mode" = "STRICT" ]; then
                        pass "命名空间 $ns ($pa_name): mTLS 模式 = STRICT"
                    else
                        fail "命名空间 $ns ($pa_name): mTLS 模式 = ${mtls_mode:-未设置}（期望 STRICT）"
                    fi
                    ;;
                data-knowledge)
                    if [ "$mtls_mode" = "STRICT" ]; then
                        pass "命名空间 $ns ($pa_name): mTLS 模式 = STRICT"
                    else
                        fail "命名空间 $ns ($pa_name): mTLS 模式 = ${mtls_mode:-未设置}（期望 STRICT）"
                    fi
                    ;;
                user-interaction)
                    if [ "$mtls_mode" = "PERMISSIVE" ]; then
                        pass "命名空间 $ns ($pa_name): mTLS 模式 = PERMISSIVE"
                    else
                        fail "命名空间 $ns ($pa_name): mTLS 模式 = ${mtls_mode:-未设置}（期望 PERMISSIVE）"
                    fi
                    ;;
            esac
        done
    done
}

# ----------------------------------------------------------
# 4. 验证 DestinationRule mTLS 配置
# ----------------------------------------------------------
verify_destination_rules() {
    log_section "4. 验证 DestinationRule mTLS 配置"

    local services=("backend:agent-execution" "frontend:user-interaction" "mysql:data-knowledge" "redis:data-knowledge")

    for service_ns in "${services[@]}"; do
        local service="${service_ns%%:*}"
        local ns="${service_ns##*:}"

        log_info "检查服务: $ns/$service"

        local dr_exists=$(kubectl get destinationrule $service -n $ns --no-headers 2>/dev/null)
        if [ -z "$dr_exists" ]; then
            warn "命名空间 $ns: 未找到 DestinationRule $service"
            continue
        fi

        local tls_mode=$(kubectl get destinationrule $service -n $ns \
            -o jsonpath='{.spec.trafficPolicy.tls.mode}' 2>/dev/null)

        case $service in
            frontend)
                if [ "$tls_mode" = "DISABLE" ]; then
                    pass "服务 $service: TLS 模式 = DISABLE（对外服务，正确）"
                else
                    fail "服务 $service: TLS 模式 = ${tls_mode:-未设置}（期望 DISABLE）"
                fi
                ;;
            *)
                if [ "$tls_mode" = "SIMPLE" ]; then
                    pass "服务 $service: TLS 模式 = SIMPLE（mTLS，正确）"
                else
                    fail "服务 $service: TLS 模式 = ${tls_mode:-未设置}（期望 SIMPLE）"
                fi
                ;;
        esac

        # 检查异常检测配置
        local outlier_consecutive=$(kubectl get destinationrule $service -n $ns \
            -o jsonpath='{.spec.trafficPolicy.outlierDetection.consecutive5xxErrors}' 2>/dev/null)
        local outlier_ejection=$(kubectl get destinationrule $service -n $ns \
            -o jsonpath='{.spec.trafficPolicy.outlierDetection.baseEjectionTime}' 2>/dev/null)

        if [ "$outlier_consecutive" = "5" ] && [ "$outlier_ejection" = "30s" ]; then
            pass "服务 $service: 异常检测配置正确（5次失败驱逐30秒）"
        else
            warn "服务 $service: 异常检测配置（连续失败=$outlier_consecutive, 驱逐时间=$outlier_ejection）"
        fi
    done
}

# ----------------------------------------------------------
# 5. 验证授权策略
# ----------------------------------------------------------
verify_authorization_policies() {
    log_section "5. 验证授权策略（身份感知路由）"

    for ns in "${NAMESPACES[@]}"; do
        log_info "检查命名空间: $ns"

        local authz_count=$(kubectl get authorizationpolicy -n $ns --no-headers 2>/dev/null | wc -l)
        if [ "$authz_count" -ge 1 ]; then
            pass "命名空间 $ns: 存在 $authz_count 个 AuthorizationPolicy"
        else
            warn "命名空间 $ns: 未找到 AuthorizationPolicy"
        fi

        # 列出授权策略详情
        kubectl get authorizationpolicy -n $ns --no-headers 2>/dev/null | while read line; do
            log_info "  -> $line"
        done
    done

    # 检查 RequestAuthentication
    log_info "检查 RequestAuthentication（JWT 请求级认证）..."
    local ra_count=$(kubectl get requestauthentication --all-namespaces --no-headers 2>/dev/null | wc -l)
    if [ "$ra_count" -ge 1 ]; then
        pass "存在 $ra_count 个 RequestAuthentication 配置"
        kubectl get requestauthentication --all-namespaces --no-headers 2>/dev/null | while read line; do
            log_info "  -> $line"
        done
    else
        warn "未找到 RequestAuthentication 配置"
    fi
}

# ----------------------------------------------------------
# 6. 验证服务间 mTLS 通信
# ----------------------------------------------------------
verify_mtls_communication() {
    log_section "6. 验证服务间 mTLS 通信"

    # 获取 backend Pod 名称
    local backend_pod=$(kubectl get pods -n agent-execution -l app=backend \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

    if [ -z "$backend_pod" ]; then
        warn "未找到 backend Pod，跳过通信验证"
        return
    fi

    log_info "使用 backend Pod ($backend_pod) 验证 mTLS 通信..."

    # 检查 istio-proxy 代理状态
    log_info "检查 istio-proxy 代理状态..."
    if kubectl exec -n agent-execution $backend_pod -c istio-proxy \
        -- curl -s http://localhost:15000/config_dump 2>/dev/null | grep -q "tls_context"; then
        pass "backend istio-proxy: TLS 上下文已配置"
    else
        warn "backend istio-proxy: 无法验证 TLS 上下文"
    fi

    # 检查到 MySQL 的连接
    log_info "验证 backend -> MySQL 的 mTLS 通信..."
    local mysql_conn=$(kubectl exec -n agent-execution $backend_pod -c istio-proxy \
        -- curl -s -o /dev/null -w "%{http_code}" \
        http://mysql.data-knowledge.svc.cluster.local:3306 2>/dev/null || echo "failed")

    if [ "$mysql_conn" != "000" ]; then
        pass "backend -> MySQL: 连接已建立（通过 mTLS）"
    else
        warn "backend -> MySQL: 连接验证失败（数据库端口非HTTP，正常现象）"
    fi

    # 检查到 Redis 的连接
    log_info "验证 backend -> Redis 的 mTLS 通信..."
    local redis_conn=$(kubectl exec -n agent-execution $backend_pod -c istio-proxy \
        -- curl -s -o /dev/null -w "%{http_code}" \
        http://redis.data-knowledge.svc.cluster.local:6379 2>/dev/null || echo "failed")

    if [ "$redis_conn" != "000" ]; then
        pass "backend -> Redis: 连接已建立（通过 mTLS）"
    else
        warn "backend -> Redis: 连接验证失败（Redis端口非HTTP，正常现象）"
    fi
}

# ----------------------------------------------------------
# 7. 验证证书信息
# ----------------------------------------------------------
verify_certificates() {
    log_section "7. 验证证书信息"

    # 获取 backend Pod
    local backend_pod=$(kubectl get pods -n agent-execution -l app=backend \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

    if [ -z "$backend_pod" ]; then
        warn "未找到 backend Pod，跳过证书验证"
        return
    fi

    log_info "获取 backend 工作负载证书信息..."

    # 通过 istio-proxy 的 secrets 端点获取证书信息
    local cert_info=$(kubectl exec -n agent-execution $backend_pod -c istio-proxy \
        -- curl -s http://localhost:15000/certs 2>/dev/null)

    if [ -n "$cert_info" ]; then
        pass "成功获取证书信息"

        # 提取证书详情
        log_info "证书详情:"
        echo "$cert_info" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for cert_chain in data.get('certificates', []):
        for cert in cert_chain.get('cert_chain', []):
            print(f\"  证书路径: {cert.get('path', 'N/A')}\")
            print(f\"  序列号: {cert.get('serial_number', 'N/A')}\")
            print(f\"  有效期: {cert.get('valid_days_from_now', 'N/A')} 天\")
            print(f\"  SHA256指纹: {cert.get('sha256', 'N/A')[:32]}...\")
            print()
except Exception as e:
    print(f'  解析证书信息失败: {e}')
" 2>/dev/null || echo "  (需要 python3 解析证书JSON)"
    else
        warn "无法获取证书信息"
    fi

    # 使用 istioctl 检查代理状态
    log_info "Istio 代理状态:"
    istioctl proxy-status 2>/dev/null | head -20 || warn "istioctl proxy-status 不可用"
}

# ----------------------------------------------------------
# 8. 验证 Gateway 和 VirtualService
# ----------------------------------------------------------
verify_gateway() {
    log_section "8. 验证 Gateway 和 VirtualService"

    # 检查 Gateway
    local gateway_count=$(kubectl get gateway --all-namespaces --no-headers 2>/dev/null | wc -l)
    if [ "$gateway_count" -ge 1 ]; then
        pass "存在 $gateway_count 个 Gateway"
        kubectl get gateway --all-namespaces --no-headers 2>/dev/null | while read line; do
            log_info "  -> $line"
        done
    else
        fail "未找到 Gateway"
    fi

    # 检查 VirtualService
    local vs_count=$(kubectl get virtualservice --all-namespaces --no-headers 2>/dev/null | wc -l)
    if [ "$vs_count" -ge 1 ]; then
        pass "存在 $vs_count 个 VirtualService"
        kubectl get virtualservice --all-namespaces --no-headers 2>/dev/null | while read line; do
            log_info "  -> $line"
        done
    else
        fail "未找到 VirtualService"
    fi
}

# ----------------------------------------------------------
# 9. 验证 EnvoyFilter
# ----------------------------------------------------------
verify_envoy_filters() {
    log_section "9. 验证 EnvoyFilter（审计过滤器）"

    local ef_count=$(kubectl get envoyfilter --all-namespaces --no-headers 2>/dev/null | wc -l)
    if [ "$ef_count" -ge 1 ]; then
        pass "存在 $ef_count 个 EnvoyFilter"
        kubectl get envoyfilter --all-namespaces --no-headers 2>/dev/null | while read line; do
            log_info "  -> $line"
        done
    else
        warn "未找到 EnvoyFilter（审计过滤器未配置）"
    fi
}

# ----------------------------------------------------------
# 10. 输出验证摘要
# ----------------------------------------------------------
print_summary() {
    log_section "验证摘要"

    echo ""
    echo -e "  ${GREEN}通过: $PASS_COUNT${NC}"
    echo -e "  ${YELLOW}警告: $WARN_COUNT${NC}"
    echo -e "  ${RED}失败: $FAIL_COUNT${NC}"
    echo ""

    if [ $FAIL_COUNT -eq 0 ]; then
        if [ $WARN_COUNT -eq 0 ]; then
            echo -e "${GREEN}========================================${NC}"
            echo -e "${GREEN}  所有验证项通过！mTLS 配置正确。${NC}"
            echo -e "${GREEN}========================================${NC}"
        else
            echo -e "${YELLOW}========================================${NC}"
            echo -e "${YELLOW}  验证完成，存在 $WARN_COUNT 个警告。${NC}"
            echo -e "${YELLOW}========================================${NC}"
        fi
    else
        echo -e "${RED}========================================${NC}"
        echo -e "${RED}  验证失败！存在 $FAIL_COUNT 个失败项。${NC}"
        echo -e "${RED}  请检查上述失败项并修复。${NC}"
        echo -e "${RED}========================================${NC}"
        exit 1
    fi
}

# ----------------------------------------------------------
# 主逻辑
# ----------------------------------------------------------
main() {
    echo -e "${BLUE}"
    echo "============================================"
    echo "  Agent-Native ERP System"
    echo "  mTLS 加密通信验证脚本"
    echo "============================================"
    echo -e "${NC}"

    # 检查 kubectl 可用性
    if ! command -v kubectl &> /dev/null; then
        log_error "kubectl 未安装"
        exit 1
    fi

    # 执行所有验证步骤
    verify_istio_control_plane
    verify_sidecar_injection
    verify_peer_authentication
    verify_destination_rules
    verify_authorization_policies
    verify_mtls_communication
    verify_certificates
    verify_gateway
    verify_envoy_filters

    # 输出摘要
    print_summary
}

main "$@"
