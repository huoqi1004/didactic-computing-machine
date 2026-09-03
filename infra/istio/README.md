# Istio 服务网格部署指南

本文档描述如何为 Agent-Native ERP 系统部署 Istio 服务网格，实现三域隔离架构下的 mTLS 加密通信、身份感知路由和审计链集成。

## 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                    Istio Mesh (cluster.local)                    │
│                                                                   │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────┐ │
│  │ agent-execution   │  │ data-knowledge   │  │ user-interaction│ │
│  │ (STRICT mTLS)     │  │ (STRICT mTLS)    │  │ (PERMISSIVE)   │ │
│  │                    │  │                  │  │                 │ │
│  │  backend (FastAPI) │  │  mysql           │  │  frontend       │ │
│  │  Agent执行调度     │  │  redis           │  │  Nginx          │ │
│  └────────┬───────────┘  └────────┬─────────┘  └────────┬───────┘ │
│           │                       │                     │         │
│           └─────── mTLS ──────────┴───────── mTLS ───────┘         │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │ istio-system: istiod, ingress-gateway, jaeger               │ │
│  └──────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## 前置条件

### 1. 工具安装

| 工具 | 最低版本 | 安装方式 | 用途 |
|------|---------|---------|------|
| kubectl | v1.28+ | [官方文档](https://kubernetes.io/docs/tasks/tools/) | Kubernetes 命令行 |
| istioctl | 1.20+ | 见下方安装步骤 | Istio 配置与验证 |
| k3d / minikube | 最新稳定版 | 见下方说明 | 本地 Kubernetes 集群 |
| helm | 3.13+ | [官方文档](https://helm.sh/docs/intro/install/) | 包管理（可选） |

### 2. 安装 istioctl

```bash
# 方式一：直接下载二进制
curl -L https://istio.io/downloadIstio | sh -

# 进入解压目录
cd istio-*/
# 将 istioctl 添加到 PATH
export PATH=$PWD/bin:$PATH

# 验证安装
istioctl version

# 方式二：使用 Homebrew (macOS)
brew install istioctl
```

### 3. 准备 Kubernetes 集群

#### 方式一：k3d（推荐，轻量级）

```bash
# 安装 k3d
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash

# 创建集群（映射前端端口）
k3d cluster create erp-cluster \
  --agents 2 \
  --port 80:80@loadbalancer \
  --port 443:443@loadbalancer

# 验证集群
kubectl cluster-info
kubectl get nodes
```

#### 方式二：minikube

```bash
# 安装 minikube
# macOS: brew install minikube
# Linux: 参见 https://minikube.sigs.k8s.io/docs/start/

# 启动集群
minikube start --cpus 4 --memory 8192 --driver=docker

# 启用 ingress 插件
minikube addons enable ingress

# 验证集群
kubectl cluster-info
kubectl get nodes
```

## 安装步骤

### 步骤 1：安装 Istio 控制面

```bash
# 使用 default 配置安装（包含 Pilot、Ingress Gateway、Egress Gateway）
istioctl install --set values.defaultRevision=default -y

# 验证 Istio 组件状态
kubectl get pods -n istio-system
# 期望输出：
# NAME                                    READY   STATUS    RESTARTS   AGE
# istiod-xxxxx-xxxxx                      1/1     Running   0          1m
# istio-ingressgateway-xxxxx-xxxxx        1/1     Running   0          1m
```

### 步骤 2：创建命名空间并启用 Sidecar 注入

```bash
# 创建三域命名空间
kubectl apply -f infra/k8s/namespace.yaml

# 验证命名空间标签（sidecar 注入已通过 namespace.yaml 中的标签启用）
kubectl get namespaces --show-labels
# 期望：agent-execution, data-knowledge, user-interaction 均有 istio-injection=enabled
```

### 步骤 3：应用 Istio 网格配置

```bash
# 应用 MeshConfig（全局网格配置）
kubectl apply -f infra/istio/mesh-config.yaml

# 应用 PeerAuthentication（对等认证策略 - mTLS）
kubectl apply -f infra/istio/peer-authentication.yaml

# 应用 DestinationRule（目标规则 - 负载均衡、连接池、异常检测）
kubectl apply -f infra/istio/destination-rules.yaml

# 应用 Gateway 和 VirtualService（流量入口）
kubectl apply -f infra/istio/gateway.yaml

# 应用 AuthorizationPolicy（授权策略 - 身份感知路由）
kubectl apply -f infra/istio/authorization-policies.yaml

# 应用 ServiceEntry（外部服务入口 - LLM API）
kubectl apply -f infra/istio/service-entries.yaml

# 应用 EnvoyFilter（审计过滤器）
kubectl apply -f infra/istio/envoy-filter-audit.yaml
```

### 步骤 4：部署应用

```bash
# 执行一键部署脚本
chmod +x infra/scripts/deploy-k8s.sh
./infra/scripts/deploy-k8s.sh
```

或手动按顺序部署：

```bash
# 1. 配置与密钥
kubectl apply -f infra/k8s/configmap.yaml
kubectl apply -f infra/k8s/secret.yaml

# 2. 数据库层
kubectl apply -f infra/k8s/mysql-statefulset.yaml
kubectl apply -f infra/k8s/redis-deployment.yaml

# 3. 等待数据库就绪
kubectl wait --for=condition=ready pod -l app=mysql -n data-knowledge --timeout=120s
kubectl wait --for=condition=ready pod -l app=redis -n data-knowledge --timeout=60s

# 4. 应用层
kubectl apply -f infra/k8s/backend-deployment.yaml
kubectl apply -f infra/k8s/frontend-deployment.yaml
```

## 验证方法

### 1. 验证 Istio 组件健康

```bash
# 检查 Istio 控制面
kubectl get pods -n istio-system

# 检查 Istio 配置同步状态
istioctl analyze
# 期望输出：✔ No validation issues found
```

### 2. 验证 Sidecar 注入

```bash
# 检查所有 Pod 是否注入了 Istio sidecar（每个 Pod 应有 2/2 容器）
kubectl get pods -n agent-execution
kubectl get pods -n data-knowledge
kubectl get pods -n user-interaction

# 验证 sidecar 版本
istioctl version
istioctl proxy-status
```

### 3. 验证 mTLS 加密

```bash
# 执行 mTLS 验证脚本
chmod +x infra/scripts/verify-mtls.sh
./infra/scripts/verify-mtls.sh

# 手动验证 - 检查 PeerAuthentication 状态
kubectl get peerauthentication --all-namespaces

# 手动验证 - 检查目标规则 mTLS 模式
kubectl get destinationrule --all-namespaces -o yaml | grep -A5 trafficPolicy
```

### 4. 验证服务间通信

```bash
# 检查 backend 到 mysql 的连接（应使用 mTLS）
kubectl exec -n agent-execution deploy/backend -c istio-proxy \
  -- curl -s http://mysql.data-knowledge.svc.cluster.local:3306

# 查看 Envoy 访问日志（确认 mTLS 通信）
kubectl logs -n agent-execution deploy/backend -c istio-proxy | grep "mTLS"

# 检查 Gateway 路由
kubectl get gateway -n user-interaction
kubectl get virtualservice -n user-interaction
```

### 5. 验证授权策略

```bash
# 检查 AuthorizationPolicy
kubectl get authorizationpolicy --all-namespaces

# 验证请求级授权（JWT）
kubectl get requestauthentication --all-namespaces

# 测试：从 frontend 命名空间访问 backend（应成功）
kubectl exec -n user-interaction deploy/frontend -c istio-proxy \
  -- curl -s http://backend.agent-execution.svc.cluster.local:8000/health

# 测试：从 user-interaction 命名空间访问 mysql（应被拒绝）
kubectl exec -n user-interaction deploy/frontend -c istio-proxy \
  -- curl -s http://mysql.data-knowledge.svc.cluster.local:3306
```

### 6. 验证分布式追踪

```bash
# 部署 Jaeger（如未随 Istio 安装）
kubectl apply -f https://raw.githubusercontent.com/istio/istio/release-1.20/samples/addons/jaeger.yaml

# 端口转发访问 Jaeger UI
kubectl port-forward -n istio-system svc/tracing 16686:16686

# 浏览器访问: http://localhost:16686
```

## 配置文件说明

| 文件 | 用途 |
|------|------|
| `mesh-config.yaml` | 全局 MeshConfig：mTLS 模式、信任域、重试策略、追踪采样 |
| `peer-authentication.yaml` | 对等认证：三域各自的 mTLS 模式 |
| `destination-rules.yaml` | 目标规则：负载均衡、连接池、异常检测 |
| `gateway.yaml` | 网关与虚拟服务：HTTP 入口、路由规则、超时配置 |
| `authorization-policies.yaml` | 授权策略：身份感知路由、JWT 请求级授权 |
| `service-entries.yaml` | 外部服务入口：LLM API、TLS 配置 |
| `envoy-filter-audit.yaml` | Envoy 过滤器：审计链注入、敏感 header 过滤 |

## 卸载

```bash
# 删除 Istio 配置
kubectl delete -f infra/istio/ --recursive

# 卸载 Istio 控制面
istioctl x uninstall --purge

# 删除命名空间
kubectl delete namespace agent-execution data-knowledge user-interaction

# 删除本地集群（k3d）
k3d cluster delete erp-cluster
```

## 常见问题

### Q: Pod 停止在 0/1 Running 状态

Sidecar 注入失败。检查命名空间是否有 `istio-injection=enabled` 标签：
```bash
kubectl get namespace <ns> --show-labels
```

### Q: mTLS 通信失败 (503 RBAC)

授权策略阻止了请求。检查 AuthorizationPolicy 的 source principals：
```bash
kubectl get authorizationpolicy -n <ns> -o yaml
```

### Q: 外部 API 无法访问

MeshConfig 配置了 `REGISTRY_ONLY` 模式，需为外部服务创建 ServiceEntry：
```bash
kubectl apply -f infra/istio/service-entries.yaml
```
