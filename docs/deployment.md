# Agent-Native ERP 部署运维文档

| 属性 | 值 |
|------|------|
| **文档版本** | v1.0.0 |
| **最后更新日期** | 2026-07-30 |
| **文档状态** | 正式发布 |
| **适用版本** | Agent-Native ERP v1.0.0 |

---

## 目录

- [1. 环境要求](#1-环境要求)
- [2. 本地开发环境搭建](#2-本地开发环境搭建)
- [3. 环境变量配置说明](#3-环境变量配置说明)
- [4. Docker Compose 部署](#4-docker-compose-部署)
- [5. Kubernetes 部署](#5-kubernetes-部署)
- [6. CI/CD 流水线](#6-cicd-流水线)
- [7. 监控告警配置](#7-监控告警配置)
- [8. 日志收集配置](#8-日志收集配置)
- [9. 数据库迁移](#9-数据库迁移)
- [10. 备份恢复策略](#10-备份恢复策略)
- [11. 常见问题排查](#11-常见问题排查)

---

## 1. 环境要求

### 1.1 开发环境

| 组件 | 最低版本 | 推荐版本 | 说明 |
|------|---------|---------|------|
| Python | 3.12 | 3.12.x | 后端运行时 |
| Node.js | 18 LTS | 18.20.x | 前端构建 |
| MySQL | 8.0 | 8.0.36 | 关系型数据库 |
| Redis | 7.0 | 7.2-alpine | 缓存与事件总线 |
| Docker | 24.0 | 25.0+ | 容器运行时 |
| Docker Compose | 2.20 | 2.24+ | 容器编排 |
| Git | 2.40 | 2.43+ | 版本控制 |

### 1.2 生产环境 (Kubernetes)

| 组件 | 最低版本 | 说明 |
|------|---------|------|
| Kubernetes | 1.27 | 容器编排平台 |
| Istio | 1.19 | 服务网格 (mTLS + 流量管理) |
| Prometheus | 2.45 | 指标采集 |
| Grafana | 10.0 | 可视化面板 |
| Alertmanager | 0.26 | 告警路由 |
| Promtail | 2.9 | 日志采集 |
| Loki | 2.9 | 日志存储与查询 |

### 1.3 硬件资源建议

| 环境 | CPU | 内存 | 磁盘 | 说明 |
|------|-----|------|------|------|
| 开发 (Docker Compose) | 4 核 | 8 GB | 40 GB SSD | 单机部署全部服务 |
| 测试 (K8s 单节点) | 8 核 | 16 GB | 100 GB SSD | Minikube/Kind |
| 生产 (K8s 集群) | 16 核+ | 32 GB+ | 500 GB SSD | 3 节点起步, 含持久化卷 |

---

## 2. 本地开发环境搭建

### 2.1 克隆项目

```bash
git clone <repository-url> erp
cd erp
```

### 2.2 后端环境搭建

```bash
# 进入后端目录
cd backend

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境 (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# 激活虚拟环境 (Linux/macOS)
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 安装开发依赖
pip install pytest pytest-asyncio pytest-cov ruff black bandit

# 复制环境变量模板
cp .env.example .env
# 编辑 .env 文件, 至少配置以下项:
#   ERP_JWT_SECRET=<your-secret-key>
#   ERP_LLM_API_KEY=<your-deepseek-api-key>
#   ERP_MYSQL_URL=mysql+aiomysql://erp:erp_password@localhost:3306/erp_db?charset=utf8mb4
#   ERP_REDIS_URL=redis://localhost:6379/0
```

### 2.3 前端环境搭建

```bash
# 进入前端目录
cd frontend

# 安装依赖
npm ci

# 启动开发服务器
npm run dev
# 开发服务器运行在 http://localhost:5173
```

### 2.4 数据库与缓存准备

使用 Docker 快速启动 MySQL 和 Redis:

```bash
# 启动 MySQL 8.0（init-db.sql 已退役：建表以 app/db/models.py 为唯一事实源，
# 由启动期 bootstrap_schema 兜底建表/补列 + alembic 基线迁移承载版本化）
docker run -d --name erp-mysql \
  -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=root_admin \
  -e MYSQL_DATABASE=erp_db \
  -e MYSQL_USER=erp \
  -e MYSQL_PASSWORD=erp_password \
  mysql:8.0 \
  --character-set-server=utf8mb4 \
  --collation-server=utf8mb4_unicode_ci

# 启动 Redis 7
docker run -d --name erp-redis \
  -p 6379:6379 \
  redis:7-alpine \
  redis-server --appendonly yes

# 数据库结构准备（二选一，均为幂等操作）
# 方式 A（推荐）：无需手工执行——后端启动时 bootstrap_schema 自动建表/补列
# 方式 B：显式执行基线迁移
cd backend
alembic upgrade head
```

### 2.5 启动后端服务

```bash
cd backend

# 开发模式 (热重载)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 访问 API 文档
# Swagger UI:  http://localhost:8000/docs
# ReDoc:       http://localhost:8000/redoc
```

### 2.6 验证环境

```bash
# 健康检查
curl http://localhost:8000/health

# 预期响应:
# {"status": "healthy", "version": "1.0.0", ...}
```

---

## 3. 环境变量配置说明

所有环境变量使用 `ERP_` 前缀, 由 `pydantic-settings` 自动注入 `Settings` 类。配置文件位于 `backend/.env`。

### 3.1 应用配置

| 变量名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ERP_APP_NAME` | str | `Agent-Native ERP` | 应用名称 |
| `ERP_APP_VERSION` | str | `1.0.0` | 应用版本 |
| `ERP_DEBUG` | bool | `true` | 调试模式 (生产环境必须设为 `false`) |
| `ERP_ENVIRONMENT` | str | `development` | 运行环境 (`development` / `production`) |
| `ERP_CURRENT_DOMAIN` | str | `agent_execution` | 当前域 (三域隔离架构) |

### 3.2 数据库配置

| 变量名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ERP_MYSQL_URL` | str | `mysql+aiomysql://...` | MySQL 异步连接 URL |
| `ERP_DB_BACKEND` | str | `mysql` | 持久化后端 (`memory` / `mysql`) |
| `ERP_DB_POOL_SIZE` | int | `10` | 连接池常驻连接数 |
| `ERP_DB_MAX_OVERFLOW` | int | `20` | 连接池溢出上限 |
| `ERP_DB_POOL_TIMEOUT` | int | `30` | 获取连接超时 (秒) |
| `ERP_DB_POOL_RECYCLE` | int | `1800` | 连接回收周期 (秒) |
| `ERP_DB_ECHO` | bool | `false` | 是否打印 SQL 日志 |

> **注意**: `pool_pre_ping=True` 与 aiomysql 的 `ping()` 签名不兼容, 使用 `pool_recycle=1800` 替代。

### 3.3 Redis 配置

| 变量名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ERP_REDIS_URL` | str | `redis://localhost:6379/0` | Redis 连接 URL |
| `ERP_CACHE_BACKEND` | str | `memory` | 缓存后端 (`memory` / `redis`) |
| `ERP_REDIS_MAX_CONNECTIONS` | int | `20` | Redis 连接池上限 |
| `ERP_REDIS_DEFAULT_TTL` | int | `300` | 默认缓存过期时间 (秒) |

### 3.4 Kafka 事件总线配置 (可选)

| 变量名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ERP_KAFKA_ENABLED` | bool | `false` | 是否启用 Kafka (关闭时使用内存 EventBus) |
| `ERP_KAFKA_BOOTSTRAP_SERVERS` | str | `localhost:9092` | Kafka broker 地址 |
| `ERP_KAFKA_EVENT_TOPIC` | str | `erp.events` | 事件主题 |
| `ERP_KAFKA_AUDIT_TOPIC` | str | `erp.audit` | 审计主题 |
| `ERP_KAFKA_CONSUMER_GROUP` | str | `erp-agent-group` | 消费者组 |

### 3.5 LLM 配置 (DeepSeek V4 Pro)

| 变量名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ERP_LLM_BASE_URL` | str | `https://api.deepseek.com` | LLM API 地址 |
| `ERP_LLM_MODEL` | str | `deepseek-v4-pro` | 模型名称 |
| `ERP_LLM_API_KEY` | str | `sk-placeholder` | API 密钥 (**必须配置**) |
| `ERP_LLM_TEMPERATURE` | float | `0.3` | 温度参数 |
| `ERP_LLM_MAX_TOKENS` | int | `8192` | 最大 token 数 |
| `ERP_LLM_ENABLED` | bool | `false` | LLM 开关 (设为 `true` 启用 33 个 Agent 的 LLM 调用) |
| `ERP_LLM_FALLBACK_TO_RULES` | bool | `true` | LLM 不可用时降级到规则引擎 |
| `ERP_LLM_TIMEOUT` | int | `60` | 请求超时 (秒, 思考模式需要更长时间) |
| `ERP_LLM_MAX_RETRIES` | int | `3` | 最大重试次数 (指数退避) |
| `ERP_LLM_RATE_LIMIT` | int | `10` | 速率限制 (请求/秒, 令牌桶算法) |
| `ERP_LLM_THINKING_ENABLED` | bool | `true` | DeepSeek 思考模式开关 |

> **重要**: DeepSeek V4 Pro 不提供 Embedding API, RAG 向量检索使用 TF-IDF + 余弦相似度。

### 3.6 JWT 认证配置

| 变量名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ERP_JWT_SECRET` | str | (空) | JWT 签名密钥 (**生产环境必须设置**) |
| `ERP_JWT_ALGORITHM` | str | `HS256` | 签名算法 |
| `ERP_JWT_EXPIRE_MINUTES` | int | `30` | Token 过期时间 (分钟) |
| `ERP_MTLS_ENABLED` | bool | `false` | 是否启用 mTLS (K8s+Istio 环境设为 `true`) |

### 3.7 审计与区块链配置

| 变量名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ERP_AUDIT_CHAIN_ENABLED` | bool | `true` | 审计链开关 |
| `ERP_AUDIT_HASH_ALGORITHM` | str | `sha256` | 哈希算法 |
| `ERP_BLOCKCHAIN_ENABLED` | bool | `false` | Hyperledger Fabric 开关 |
| `ERP_BLOCKCHAIN_CHANNEL` | str | `audit-channel` | Fabric 通道 |
| `ERP_BLOCKCHAIN_CHAINCODE` | str | `audit-chaincode` | 链码名称 |
| `ERP_BLOCKCHAIN_PEER_URL` | str | `grpcs://peer0.erp.com:7051` | Peer 节点地址 |
| `ERP_BLOCKCHAIN_ORDERER_URL` | str | `grpcs://orderer.erp.com:7050` | Orderer 地址 |
| `ERP_BLOCKCHAIN_TIMEOUT` | int | `10` | Chaincode 调用超时 (秒) |
| `ERP_BLOCKCHAIN_MAX_RETRIES` | int | `3` | 最大重试次数 |

> 区块链不可用时自动降级到内存 SHA-256 哈希链 (`HashChainFallbackClient`)。

### 3.8 Agent 配置

| 变量名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ERP_AGENT_HEALTH_CHECK_INTERVAL` | int | `30` | 健康检查间隔 (秒) |
| `ERP_AGENT_SLO_RESPONSE_TIME_MS` | int | `5000` | SLO 响应时间 (毫秒) |
| `ERP_AGENT_CIRCUIT_BREAKER_THRESHOLD` | int | `5` | 熔断器阈值 (连续失败次数) |
| `ERP_AGENT_CONSENSUS_THRESHOLD` | float | `0.67` | 多 Agent 共识阈值 |

### 3.9 人在回路配置

| 变量名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ERP_HUMAN_APPROVAL_TIMEOUT` | int | `3600` | 人类审批超时 (秒) |
| `ERP_AUTO_FALLBACK_TO_HUMAN` | bool | `true` | 超时自动转人工 |

### 3.10 监控配置

| 变量名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ERP_PROMETHEUS_PORT` | int | `9090` | Prometheus 指标端口 |
| `ERP_METRICS_ENABLED` | bool | `true` | 指标采集开关 |

### 3.11 版本化与灰度发布配置

| 变量名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ERP_VERSIONING_ENABLED` | bool | `true` | 版本化开关 |
| `ERP_CANARY_DEFAULT_TRAFFIC` | int | `10` | 默认灰度流量百分比 |
| `ERP_CANARY_SUCCESS_THRESHOLD` | float | `0.95` | 灰度成功阈值 |
| `ERP_CANARY_ERROR_RATE_THRESHOLD` | float | `0.05` | 灰度错误率阈值 |
| `ERP_CANARY_DURATION_SECONDS` | int | `3600` | 灰度持续时间 (秒) |
| `ERP_DIGITAL_TWIN_ENABLED` | bool | `true` | 数字孪生开关 |

---

## 4. Docker Compose 部署

### 4.1 架构概述

Docker Compose 用于开发和测试环境, 包含 6 个服务:

```mermaid
graph TB
    subgraph "用户交互域 (erp-ui-net)"
        FE[frontend<br/>Nginx :3000]
    end

    subgraph "Agent执行域 (erp-agent-net)"
        BE[backend<br/>FastAPI :8000]
    end

    subgraph "数据知识域 (erp-data-net)"
        DB[(MySQL 8.0<br/>:3306)]
        RD[(Redis 7<br/>:6379)]
    end

    subgraph "监控域"
        PROM[Prometheus<br/>:9090]
        GRAF[Grafana<br/>:3001]
    end

    FE -->|API 代理| BE
    BE -->|async| DB
    BE -->|cache/events| RD
    PROM -->|scrape /metrics| BE
    GRAF -->|query| PROM
```

### 4.2 部署步骤

```bash
# 1. 准备环境变量文件
cp backend/.env.example backend/.env
# 编辑 backend/.env, 配置生产参数:
#   ERP_JWT_SECRET=<strong-secret>
#   ERP_LLM_API_KEY=<your-key>
#   ERP_DEBUG=false
#   ERP_ENVIRONMENT=production
#   ERP_LLM_ENABLED=true

# 2. 构建并启动所有服务
docker compose up -d --build

# 3. 查看服务状态
docker compose ps

# 4. 查看日志
docker compose logs -f backend
docker compose logs -f frontend

# 5. 健康检查
curl http://localhost:8000/health
curl http://localhost:3000
```

### 4.3 服务端口映射

| 服务 | 容器端口 | 宿主端口 | 说明 |
|------|---------|---------|------|
| backend | 8000 | 8000 | FastAPI 后端 |
| frontend | 3000 | 3000 | Nginx 前端 |
| mysql | 3306 | 3306 | MySQL 数据库 |
| redis | 6379 | 6379 | Redis 缓存 |
| prometheus | 9090 | 9090 | 指标采集 |
| grafana | 3000 | 3001 | 监控面板 (避免与前端冲突) |

### 4.4 资源限制

| 服务 | CPU 限制 | 内存限制 |
|------|---------|---------|
| backend | 1.0 | 1G |
| frontend | 0.5 | 256M |
| mysql | 0.5 | 512M |
| redis | 0.3 | 384M |
| prometheus | 0.5 | 512M |
| grafana | 0.3 | 256M |

### 4.5 常用操作

```bash
# 停止所有服务
docker compose down

# 停止并删除数据卷 (谨慎!)
docker compose down -v

# 重新构建单个服务
docker compose build backend
docker compose up -d backend

# 进入容器调试
docker compose exec backend bash
docker compose exec mysql mysql -u erp -p erp_db

# 查看资源使用
docker stats
```

---

## 5. Kubernetes 部署

### 5.1 三域隔离架构

```mermaid
graph TB
    subgraph "istio-system"
        GW[Istio Ingress Gateway]
    end

    subgraph "user-interaction 命名空间"
        FE_K8s[frontend<br/>Deployment + Service]
    end

    subgraph "agent-execution 命名空间"
        BE_K8s[backend<br/>Deployment x2 + Service]
    end

    subgraph "data-knowledge 命名空间"
        DB_K8s[(MySQL<br/>StatefulSet)]
        RD_K8s[(Redis<br/>Deployment)]
    end

    GW -->|HTTP| FE_K8s
    FE_K8s -->|mTLS STRICT| BE_K8s
    BE_K8s -->|mTLS STRICT| DB_K8s
    BE_K8s -->|mTLS STRICT| RD_K8s
```

### 5.2 命名空间与 Istio Sidecar

三个命名空间均启用 Istio sidecar 自动注入:

| 命名空间 | 对应 Docker 网络 | mTLS 模式 | 运行服务 |
|----------|-----------------|-----------|---------|
| `agent-execution` | `erp-agent-net` | STRICT | backend (FastAPI) |
| `data-knowledge` | `erp-data-net` | STRICT | MySQL, Redis |
| `user-interaction` | `erp-ui-net` | PERMISSIVE | frontend (Nginx) |

### 5.3 部署步骤

```bash
# 1. 创建命名空间 (自动注入 Istio sidecar)
kubectl apply -f infra/k8s/namespace.yaml

# 2. 创建 ConfigMap (非敏感配置)
kubectl apply -f infra/k8s/configmap.yaml

# 3. 创建 Secret (敏感配置)
# JWT 密钥
kubectl create secret generic jwt-secret \
  --from-literal=ERP_JWT_SECRET=<your-jwt-secret> \
  -n agent-execution

# LLM API 密钥
kubectl create secret generic llm-api-secret \
  --from-literal=ERP_LLM_API_KEY=<your-deepseek-key> \
  -n agent-execution

# 4. 部署数据层 (data-knowledge 命名空间)
kubectl apply -f infra/k8s/redis-deployment.yaml
# MySQL StatefulSet (如有)
kubectl apply -f infra/k8s/mysql-statefulset.yaml

# 5. 部署后端 (agent-execution 命名空间)
kubectl apply -f infra/k8s/backend-deployment.yaml

# 6. 部署前端 (user-interaction 命名空间)
kubectl apply -f infra/k8s/frontend-deployment.yaml

# 7. 配置 Istio 流量管理
kubectl apply -f infra/istio/gateway.yaml
kubectl apply -f infra/istio/destination-rules.yaml
kubectl apply -f infra/istio/authorization-policies.yaml
kubectl apply -f infra/istio/peer-authentication.yaml

# 8. 验证部署
kubectl get pods -n agent-execution
kubectl get pods -n data-knowledge
kubectl get pods -n user-interaction

# 9. 获取 Ingress Gateway 外部 IP
kubectl get svc istio-ingressgateway -n istio-system
```

### 5.4 Backend Deployment 关键配置

后端部署关键参数 (`infra/k8s/backend-deployment.yaml`):

| 配置项 | 值 | 说明 |
|--------|------|------|
| replicas | 2 | 双副本高可用 |
| CPU request/limit | 500m / 1000m | CPU 资源 |
| Memory request/limit | 512Mi / 1Gi | 内存资源 |
| livenessProbe | /health, 30s | 存活探针 |
| readinessProbe | /health, 10s | 就绪探针 |
| startupProbe | /health, 120s | 启动探针 |
| runAsNonRoot | true | 非 root 运行 |
| runAsUser | 1000 | 用户 ID |
| podAntiAffinity | preferred | 副本反亲和调度 |
| terminationGracePeriod | 30s | 优雅终止 |

### 5.5 Istio 安全策略

```yaml
# PeerAuthentication - mTLS 模式
# agent-execution: STRICT (仅接受 mTLS 流量)
# data-knowledge:  STRICT (仅接受 mTLS 流量)
# user-interaction: PERMISSIVE (接受 mTLS 和明文, 因需接收外部请求)

# AuthorizationPolicy - 工作负载身份访问控制
# backend: 仅允许 frontend 和 istio-system 命名空间流量
# mysql:   仅允许 agent-execution 命名空间 (backend) 访问
# redis:   仅允许 agent-execution 命名空间 (backend) 访问
```

### 5.6 滚动更新与回滚

```bash
# 滚动更新镜像
kubectl set image deployment/backend \
  backend=erp-backend:v1.1.0 \
  -n agent-execution

# 查看滚动更新状态
kubectl rollout status deployment/backend -n agent-execution

# 查看发布历史
kubectl rollout history deployment/backend -n agent-execution

# 回滚到上一版本
kubectl rollout undo deployment/backend -n agent-execution

# 回滚到指定版本
kubectl rollout undo deployment/backend \
  --to-revision=2 \
  -n agent-execution
```

---

## 6. CI/CD 流水线

### 6.1 流水线概览

```mermaid
graph LR
    subgraph "CI (ci.yml)"
        A[Push/PR] --> B[backend-test]
        A --> C[backend-lint]
        A --> D[frontend-build]
        A --> E[security-scan]
        B --> F[docker-build]
        D --> F
    end

    subgraph "CD (deploy.yml)"
        F -->|workflow_run success| G[deploy-staging]
        G --> H[Build Images]
        H --> I[Docker Compose Up]
        I --> J[Health Check]
        J --> K[Deploy Complete]
    end
```

### 6.2 CI 流水线 (ci.yml)

**触发条件**: push 到 `main`/`develop` 分支, 或 PR 到 `main` 分支。

| Job 名称 | 说明 | 依赖 |
|----------|------|------|
| `backend-test` | Python 3.12 + pytest 单元测试, 生成 JUnit 报告 | - |
| `backend-lint` | ruff 代码检查 + black 格式检查 | - |
| `frontend-build` | npm ci + tsc 类型检查 + vite build | - |
| `security-scan` | bandit (Python SAST, -ll 中等级别) + npm audit (high 级别) | - |
| `docker-build` | 构建 erp-backend 和 erp-frontend Docker 镜像 | `backend-test`, `frontend-build` |

```yaml
# CI 关键配置
concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true  # 同分支新提交取消旧构建
```

### 6.3 CD 流水线 (deploy.yml)

**触发条件**: CI 流水线 (`ci.yml`) 在 `main` 分支上成功完成。

| 步骤 | 说明 |
|------|------|
| Checkout | 检出 CI 对应的 commit SHA |
| Prepare .env | 从 GitHub Secrets 注入敏感配置, 生成 `backend/.env` |
| Build | `docker compose build` 构建所有服务镜像 |
| Deploy | `docker compose up -d` 启动所有服务 |
| Wait | 等待后端健康检查通过 (最多 5 分钟, 30 次重试) |
| Health Check | `curl http://localhost:8000/health` |
| Logs Upload | 失败时上传容器日志作为 artifact |

### 6.4 必需的 GitHub Secrets

| Secret 名称 | 说明 |
|-------------|------|
| `ERP_JWT_SECRET` | JWT 签名密钥 |
| `ERP_LLM_API_KEY` | DeepSeek API 密钥 |
| `ERP_MYSQL_URL` | MySQL 连接 URL |
| `ERP_REDIS_URL` | Redis 连接 URL |
| `ERP_CORS_ORIGINS` | CORS 允许的源 (逗号分隔) |

```bash
# 设置 GitHub Secrets (使用 gh CLI)
gh secret set ERP_JWT_SECRET --body "your-jwt-secret"
gh secret set ERP_LLM_API_KEY --body "sk-your-deepseek-key"
gh secret set ERP_MYSQL_URL --body "mysql+aiomysql://erp:password@host:3306/erp_db"
gh secret set ERP_REDIS_URL --body "redis://host:6379/0"
gh secret set ERP_CORS_ORIGINS --body "https://erp.example.com"
```

---

## 7. 监控告警配置

### 7.1 监控架构

```mermaid
graph TB
    subgraph "应用服务"
        BE[Backend FastAPI<br/>/metrics]
        FE[Frontend Nginx]
        DB[(MySQL)]
        RD[(Redis)]
    end

    subgraph "监控栈"
        PROM[Prometheus<br/>:9090<br/>指标采集与存储]
        GRAF[Grafana<br/>:3001<br/>可视化面板]
        AM[Alertmanager<br/>告警路由]
    end

    subgraph "通知渠道"
        SLACK[Slack/Webhook]
        EMAIL[Email]
    end

    PROM -->|scrape 15s| BE
    PROM -->|scrape| DB
    PROM -->|scrape| RD
    PROM -->|evaluate rules| AM
    GRAF -->|query| PROM
    AM -->|notify| SLACK
    AM -->|notify| EMAIL
```

### 7.2 Prometheus 配置

配置文件: `infra/monitoring/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  scrape_timeout: 10s

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'erp-backend'
    metrics_path: '/metrics'
    scrape_interval: 15s
    static_configs:
      - targets: ['backend:8000']
        labels:
          domain: 'agent-execution'
          service: 'fastapi-backend'
```

### 7.3 告警规则

配置文件: `infra/prometheus/rules.yml`

#### 系统资源告警

| 告警名称 | 条件 | 持续时间 | 严重级别 |
|----------|------|---------|---------|
| HighCPUUsage | CPU > 80% | 5m | warning |
| HighMemoryUsage | 内存 > 85% | 5m | warning |
| LowDiskSpace | 磁盘可用 < 15% | 10m | warning |
| HighDiskIO | IO 等待 > 20% | 5m | warning |

#### 应用健康告警

| 告警名称 | 条件 | 持续时间 | 严重级别 |
|----------|------|---------|---------|
| ServiceDown | backend up == 0 | 1m | critical |
| HighErrorRate | 5xx 错误率 > 5% | 5m | critical |
| SlowApiResponse | P95 响应时间 > 2s | 5m | warning |
| RateLimitTriggered | 429 响应 > 10/s | 5m | warning |

#### 数据库告警

| 告警名称 | 条件 | 持续时间 | 严重级别 |
|----------|------|---------|---------|
| HighDBConnections | 连接数 > 100 | 5m | warning |
| SlowDBQueries | 5分钟慢查询 > 10 | 5m | warning |
| DBReplicationLag | 主从延迟 > 60s | 5m | warning |

#### Agent 系统告警

| 告警名称 | 条件 | 持续时间 | 严重级别 |
|----------|------|---------|---------|
| AgentCircuitBreaker | 熔断器触发 | 1m | critical |
| AgentResponseTimeout | P95 > 30s | 5m | warning |
| LLMApiUnavailable | LLM API 不可用 | 2m | warning |

### 7.4 Grafana 面板

Grafana 自动配置:

| 配置文件 | 路径 | 说明 |
|---------|------|------|
| 数据源 | `infra/monitoring/grafana/datasource.yml` | Prometheus 数据源自动注册 |
| 面板注册 | `infra/monitoring/grafana/dashboards.yml` | 面板自动发现 |
| ERP 面板 | `infra/monitoring/grafana/dashboard.json` | 系统监控面板 |

**访问 Grafana**:
- URL: `http://localhost:3001`
- 默认账号: `admin`
- 默认密码: `admin`

### 7.5 Alertmanager 配置 (可选)

```yaml
# alertmanager.yml
route:
  receiver: 'default'
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

receivers:
  - name: 'default'
    webhook_configs:
      - url: 'https://hooks.slack.com/services/xxx'
```

---

## 8. 日志收集配置

### 8.1 日志架构

```mermaid
graph LR
    subgraph "应用层"
        APP[Backend FastAPI<br/>structlog JSON]
    end

    subgraph "采集层"
        PT[Promtail<br/>日志采集]
    end

    subgraph "存储查询层"
        LOKI[Loki<br/>日志存储]
        GRAF2[Grafana<br/>日志查询]
    end

    APP -->|stdout/stderr| PT
    PT -->|push| LOKI
    GRAF2 -->|LogQL| LOKI
```

### 8.2 结构化日志 (structlog)

后端使用 `structlog` 输出 JSON 格式日志, 包含 `request_id` 用于链路追踪:

```python
# 日志格式示例
{
    "event": "request_completed",
    "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "method": "POST",
    "path": "/api/v1/auth/login",
    "status_code": 200,
    "duration_ms": 45.2,
    "user_id": "usr_001",
    "timestamp": "2026-07-30T10:00:00.000Z",
    "level": "info"
}
```

关键日志配置 (`backend/app/core/logging_config.py`):

```python
import structlog

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,    # 合并上下文变量
        structlog.processors.add_log_level,          # 添加日志级别
        structlog.processors.TimeStamper(fmt="iso"), # ISO 时间戳
        structlog.processors.JSONRenderer(),         # JSON 输出
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)
```

### 8.3 Promtail 配置 (K8s 环境)

```yaml
# promtail-config.yaml
server:
  http_listen_port: 9080

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  # 采集后端容器日志
  - job_name: erp-backend
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names: [agent-execution]
    pipeline_stages:
      - json:
          expressions:
            level: level
            event: event
            request_id: request_id
      - labels:
          level:
          event:
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_name]
        target_label: pod_name
      - source_labels: [__meta_kubernetes_namespace]
        target_label: namespace
```

### 8.4 LogQL 查询示例

```logql
# 查询所有 ERROR 级别日志
{namespace="agent-execution", level="error"}

# 按 request_id 追踪请求链路
{namespace="agent-execution"} |= "a1b2c3d4-e5f6-7890"

# 查询特定 Agent 的日志
{namespace="agent-execution", event=~"agent_.*"} |= "InvoiceAgent"

# 统计过去 1 小时的错误日志数量
sum(count_over_time({namespace="agent-execution", level="error"}[1h]))
```

---

## 9. 数据库迁移

### 9.1 Alembic 配置

配置文件: `backend/alembic.ini`

```ini
[alembic]
script_location = alembic
sqlalchemy.url = mysql+pymysql://erp:erp_password@localhost:3306/erp_db?charset=utf8mb4
timezone = Asia/Shanghai
file_template = %%(year)d%%(month).2d%%(day).2d_%%(hour).2d%%(minute).2d_%%(rev)s_%%(slug)s
```

> **注意**: Alembic 使用 `pymysql` (同步驱动) 执行迁移, 应用运行时使用 `aiomysql` (异步驱动)。

### 9.2 常用命令

```bash
cd backend

# 生成自动迁移脚本 (检测 ORM 模型变化)
alembic revision --autogenerate -m "add_invoice_table"

# 应用所有待执行迁移
alembic upgrade head

# 回滚一个版本
alembic downgrade -1

# 回滚到指定版本
alembic downgrade <revision_id>

# 查看迁移历史
alembic history

# 查看当前版本
alembic current

# 查看待执行迁移
alembic heads
```

### 9.3 迁移开发规范

1. **修改 ORM 模型后, 始终生成新的迁移脚本**: `alembic revision --autogenerate -m "description"`
2. **检查生成的迁移脚本**: 自动生成可能遗漏复杂变更, 需人工审查
3. **先在开发环境测试**: 确保迁移脚本可正确执行 upgrade 和 downgrade
4. **迁移脚本提交到版本库**: 迁移脚本属于代码, 必须随代码一起提交
5. **不要修改已发布的迁移脚本**: 已在生产环境执行过的迁移不可修改

### 9.4 生产环境迁移

```bash
# 在部署流程中执行迁移 (推荐在 CI/CD 中自动执行)
docker compose exec backend alembic upgrade head

# 或在 K8s 中使用 Job 执行
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: db-migration
  namespace: agent-execution
spec:
  template:
    spec:
      containers:
        - name: migrator
          image: erp-backend:latest
          command: ["alembic", "upgrade", "head"]
          envFrom:
            - configMapRef:
                name: backend-config
      restartPolicy: OnFailure
  backoffLimit: 3
EOF
```

---

## 10. 备份恢复策略

### 10.1 MySQL 备份

#### 定时备份脚本

```bash
#!/bin/bash
# mysql-backup.sh - MySQL 定时备份脚本

BACKUP_DIR="/backups/mysql"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/erp_db_${DATE}.sql.gz"
RETENTION_DAYS=30

# 创建备份目录
mkdir -p ${BACKUP_DIR}

# 执行备份 (使用 mysqldump)
docker compose exec -T mysql mysqldump \
  -u root \
  -p${MYSQL_ROOT_PASSWORD} \
  --single-transaction \
  --routines \
  --triggers \
  --databases erp_db \
  | gzip > ${BACKUP_FILE}

# 验证备份文件
if [ -s ${BACKUP_FILE} ]; then
  echo "[$(date)] Backup successful: ${BACKUP_FILE} ($(du -h ${BACKUP_FILE} | cut -f1))"
else
  echo "[$(date)] ERROR: Backup file is empty!"
  exit 1
fi

# 清理过期备份
find ${BACKUP_DIR} -name "erp_db_*.sql.gz" -mtime +${RETENTION_DAYS} -delete
echo "[$(date)] Cleaned backups older than ${RETENTION_DAYS} days"
```

#### Crontab 配置

```bash
# 每天凌晨 2 点执行备份
0 2 * * * /opt/erp/scripts/mysql-backup.sh >> /var/log/mysql-backup.log 2>&1
```

### 10.2 MySQL 恢复

```bash
# 恢复指定备份
gunzip < /backups/mysql/erp_db_20260730_020000.sql.gz \
  | docker compose exec -T mysql mysql -u root -p${MYSQL_ROOT_PASSWORD} erp_db

# 验证恢复
docker compose exec mysql mysql -u erp -p -e "SELECT COUNT(*) FROM users;" erp_db
```

### 10.3 Redis 备份与恢复

```bash
# Redis RDB 快照备份
docker compose exec redis redis-cli BGSAVE
# 等待快照完成
sleep 5
# 复制 RDB 文件
docker cp erp-redis:/data/dump.rdb /backups/redis/dump_$(date +%Y%m%d).rdb

# Redis 恢复
docker compose stop redis
docker cp /backups/redis/dump_20260730.rdb erp-redis:/data/dump.rdb
docker compose start redis
```

### 10.4 备份策略矩阵

| 数据类型 | 备份频率 | 保留期限 | 存储位置 | 说明 |
|----------|---------|---------|---------|------|
| MySQL 全量 | 每日 02:00 | 30 天 | 本地 + 远程 | mysqldump --single-transaction |
| MySQL binlog | 实时 | 7 天 | 本地 | Point-in-Time 恢复 |
| Redis RDB | 每日 03:00 | 7 天 | 本地 | BGSAVE 快照 |
| Prometheus TSDB | 每周周日 04:00 | 12 周 | 本地 | 30 天 retention |
| Grafana 面板 | 随代码 | 永久 | Git 仓库 | dashboard.json |
| K8s 资源 | 每周 | 90 天 | 远程 | `kubectl get all -o yaml` |

### 10.5 灾难恢复流程

```mermaid
graph TD
    A[灾难发生] --> B{评估影响}
    B -->|数据损坏| C[停止服务]
    B -->|硬件故障| D[切换备机]
    C --> E[恢复最近备份]
    E --> F[执行 Alembic 迁移]
    F --> G[验证数据完整性]
    G --> H[重启服务]
    H --> I[健康检查]
    I -->|通过| J[恢复完成]
    I -->|失败| K[回滚 + 人工介入]
    D --> E
```

---

## 11. 常见问题排查

### 11.1 服务启动失败

#### 问题: Backend 容器启动后立即退出

```bash
# 查看容器日志
docker compose logs backend

# 常见原因:
# 1. MySQL 未就绪 -> 检查 MySQL 健康状态
docker compose ps mysql
# 2. .env 文件缺失 -> 确认 backend/.env 存在且配置正确
# 3. ERP_JWT_SECRET 为空 -> 设置 JWT 密钥
# 4. 数据库迁移未执行 -> 运行 alembic upgrade head
```

#### 问题: Frontend 构建失败

```bash
# 查看 CI 构建日志
# 常见原因:
# 1. TypeScript 类型错误 -> 运行 npx tsc --noEmit 检查
# 2. npm 依赖冲突 -> 删除 node_modules 后重新 npm ci
cd frontend
rm -rf node_modules package-lock.json
npm ci
```

### 11.2 数据库连接问题

#### 问题: `sqlalchemy.exc.OperationalError: (aiomysql...)`

```bash
# 1. 检查 MySQL 是否运行
docker compose ps mysql
docker compose exec mysql mysqladmin ping -u root -p

# 2. 检查网络连通性
docker compose exec backend python -c "
import asyncio, aiomysql
async def test():
    try:
        conn = await aiomysql.connect(host='mysql', port=3306, user='erp', password='erp_password', db='erp_db')
        print('Connection OK')
        conn.close()
    except Exception as e:
        print(f'Connection failed: {e}')
asyncio.run(test())
"

# 3. 检查连接池配置
# pool_recycle=1800 避免连接被数据库端断开
# 不要使用 pool_pre_ping=True (与 aiomysql 不兼容)
```

### 11.3 Redis 连接问题

#### 问题: `redis.exceptions.ConnectionError`

```bash
# 1. 检查 Redis 是否运行
docker compose ps redis
docker compose exec redis redis-cli ping
# 预期: PONG

# 2. 检查 Redis 内存
docker compose exec redis redis-cli INFO memory | grep used_memory_human

# 3. 如果内存不足, 清理缓存
docker compose exec redis redis-cli FLUSHDB
```

### 11.4 LLM API 问题

#### 问题: Agent 响应超时或降级到规则引擎

```bash
# 1. 检查 LLM 配置
docker compose exec backend python -c "
from app.core.config import settings
print(f'LLM Enabled: {settings.llm_enabled}')
print(f'LLM Base URL: {settings.llm_base_url}')
print(f'LLM Model: {settings.llm_model}')
print(f'LLM Timeout: {settings.llm_timeout}s')
"

# 2. 测试 LLM API 连通性
docker compose exec backend python -c "
import httpx, asyncio
async def test():
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                'https://api.deepseek.com/v1/chat/completions',
                headers={'Authorization': 'Bearer sk-your-key'},
                json={'model': 'deepseek-v4-pro', 'messages': [{'role': 'user', 'content': 'hello'}]},
                timeout=60
            )
            print(f'Status: {resp.status_code}')
            print(f'Response: {resp.text[:200]}')
        except Exception as e:
            print(f'Error: {e}')
asyncio.run(test())
"

# 3. 检查是否已降级
# 日志中搜索 "fallback_to_rules" 或 "llm_unavailable"
docker compose logs backend | grep -i "fallback\|llm_unavailable"
```

### 11.5 K8s Pod 问题

#### 问题: Pod 处于 CrashLoopBackOff

```bash
# 查看 Pod 日志
kubectl logs <pod-name> -n agent-execution --previous

# 查看 Pod 事件
kubectl describe pod <pod-name> -n agent-execution

# 常见原因:
# 1. 健康检查失败 -> 检查 livenessProbe 配置
# 2. 资源不足 -> 检查 resources limits
# 3. ConfigMap/Secret 缺失 -> 检查 envFrom 引用
# 4. 镜像拉取失败 -> 检查 imagePullPolicy
```

#### 问题: Istio sidecar 未注入

```bash
# 检查命名空间标签
kubectl get namespace agent-execution --show-labels
# 应包含: istio-injection=enabled

# 检查 Pod 是否有 sidecar
kubectl get pods -n agent-execution -o jsonpath='{.items[*].spec.containers[*].name}'
# 应包含: backend, istio-proxy

# 如果未注入, 重启 Pod
kubectl delete pod <pod-name> -n agent-execution
```

### 11.6 性能问题

#### 问题: API 响应缓慢

```bash
# 1. 检查后端资源使用
docker stats erp-backend

# 2. 查看 Prometheus 指标
# P95 响应时间:
# histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by(le))

# 3. 检查数据库慢查询
docker compose exec mysql mysql -u root -p -e "
  SELECT * FROM information_schema.processlist
  WHERE TIME > 5
  ORDER BY TIME DESC;
"

# 4. 检查 Redis 连接数
docker compose exec redis redis-cli INFO clients
```

#### 问题: 内存持续增长

```bash
# 1. 监控内存趋势 (Prometheus)
# (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100

# 2. 检查连接池
docker compose exec backend python -c "
from app.core.database import db_manager
print(f'DB pool size: {db_manager.pool.size()}')
print(f'DB pool checked out: {db_manager.pool.checkedout()}')
"

# 3. 如有必要, 重启服务
docker compose restart backend
```

### 11.7 日志排查快速参考

```bash
# 后端错误日志
docker compose logs backend | grep '"level": "error"'

# 特定 request_id 追踪
docker compose logs backend | grep "a1b2c3d4-e5f6"

# Agent 熔断日志
docker compose logs backend | grep "circuit_breaker"

# 数据库迁移日志
docker compose logs backend | grep "alembic"

# K8s 环境日志
kubectl logs -n agent-execution -l app=backend --tail=100 -f
```
