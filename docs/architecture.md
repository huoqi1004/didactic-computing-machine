# Agent-Native ERP 系统架构文档

> **版本**: 1.0.0
> **最后更新**: 2026-07-30
> **文档状态**: 正式发布

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术栈选型说明](#2-技术栈选型说明)
3. [系统组件架构图](#3-系统组件架构图)
4. [33 个 Agent 分层架构](#4-33-个-agent-分层架构)
5. [Agent 通信机制](#5-agent-通信机制)
6. [认证授权架构](#6-认证授权架构)
7. [知识库架构](#7-知识库架构)
8. [文档生成架构](#8-文档生成架构)
9. [数据库架构](#9-数据库架构)
10. [降级策略说明](#10-降级策略说明)

---

## 1. 项目概述

### 1.1 Agent-Native 设计理念

Agent-Native ERP 是一种以**自主 AI Agent 为基本业务单元**的企业资源规划系统。与传统 ERP 的"人驱动系统"模式不同，本系统采用"Agent 自主执行 + 人类监督审批"的协作模式：

- **Agent 自主性**: 33 个业务 Agent 各自负责特定领域，具备独立决策、事件感知和工具调用能力
- **人在回路 (Human-in-the-Loop)**: 高风险操作自动触发人类审批，确保业务安全
- **事件驱动**: Agent 间通过事件总线异步通信，实现松耦合协作
- **认知增强**: 所有 Agent 可选接入 LLM (DeepSeek V4 Pro) 进行智能决策，LLM 不可用时自动降级到规则引擎
- **安全治理**: Agent 拥有 DID 身份、意图级授权、安全护栏和完整审计链

### 1.2 三域隔离架构

系统采用三域隔离架构，通过 Docker 自定义网络 (开发环境) 或 K8s 命名空间 + Istio mTLS (生产环境) 实现隔离：

| 域 | 说明 | K8s 命名空间 | Istio mTLS 模式 |
|---|---|---|---|
| Agent 执行域 | AI Agent 调度与执行 | `agent-execution` | STRICT |
| 数据知识域 | 数据库与缓存存储 | `data-knowledge` | STRICT |
| 用户交互域 | 前端展示与 API 网关 | `user-interaction` | PERMISSIVE |

---

## 2. 技术栈选型说明

### 2.1 技术栈总览

| 层级 | 技术选型 | 版本 | 选型理由 |
|---|---|---|---|
| 后端框架 | FastAPI | 0.110+ | 异步原生、自动 OpenAPI 文档、高性能 |
| ORM | SQLAlchemy | 2.0 (async) | 成熟稳定、异步支持、类型安全 |
| MySQL 驱动 | aiomysql | - | 纯 Python 异步 MySQL 驱动 |
| 缓存/消息 | Redis | 7.x | 高性能缓存 + Pub/Sub 事件总线 |
| 前端框架 | React | 18.x | 生态成熟、组件丰富 |
| 类型系统 | TypeScript | 5.5+ | 类型安全、开发体验好 |
| 构建工具 | Vite | 5.x | 极速 HMR、ESM 原生支持 |
| CSS 框架 | Tailwind CSS | 3.4+ | 原子化 CSS、快速开发 |
| 状态管理 | Zustand | 4.x | 轻量、无样板代码 |
| 数据库 | MySQL | 8.0 | 成熟关系型数据库、utf8mb4 支持 |
| LLM | DeepSeek V4 Pro | - | OpenAI 兼容 API、支持思考模式 |
| 容器化 | Docker + Docker Compose | - | 开发环境编排 |
| 编排 | Kubernetes + Istio | - | 生产环境服务网格 |
| 监控 | Prometheus + Grafana | - | 指标采集与可视化 |
| 日志 | structlog + Promtail + Loki | - | 结构化日志 + 日志聚合 |
| 告警 | Alertmanager | - | 告警路由与通知 |
| CI/CD | GitHub Actions | - | 代码托管 + CI/CD 一体化 |

### 2.2 关键依赖

**后端核心依赖** (`backend/requirements.txt`):

```text
fastapi          # Web 框架
uvicorn          # ASGI 服务器
sqlalchemy       # ORM (async)
aiomysql         # MySQL 异步驱动
redis            # Redis 异步客户端
pydantic         # 数据验证
pydantic-settings # 环境变量配置
python-jose      # JWT 令牌
passlib          # 密码哈希
structlog        # 结构化日志
prometheus-client # Prometheus 指标
httpx            # LLM API 异步 HTTP 客户端
openai           # OpenAI 兼容 LLM SDK
jieba            # 中文分词 (知识库)
python-docx      # DOCX 文档生成
openpyxl         # XLSX 文档生成
python-pptx      # PPTX 文档生成
reportlab        # PDF 文档生成
```

**前端核心依赖** (`frontend/package.json`):

```json
{
  "dependencies": {
    "react": "^18.3.1",
    "react-router-dom": "^6.26.0",
    "axios": "^1.7.4",
    "zustand": "^4.5.4",
    "recharts": "^2.12.7",
    "lucide-react": "^0.427.0",
    "tailwind-merge": "^2.5.2",
    "clsx": "^2.1.1"
  }
}
```

---

## 3. 系统组件架构图

### 3.1 整体架构

```mermaid
graph TB
    subgraph "用户交互域 (user-interaction)"
        FE["React 前端<br/>Vite + TypeScript + Tailwind"]
        NGINX["Nginx<br/>静态资源 + API 代理"]
    end

    subgraph "Agent 执行域 (agent-execution)"
        API["FastAPI 后端<br/>REST API + WebSocket"]
        MW["中间件层<br/>Auth + RBAC + RateLimit + RequestContext"]
        ORCH["Agent 编排器<br/>Orchestrator"]
        AGENTS["33 个业务 Agent"]
        KB["知识库引擎<br/>RAG + Graph + Rules"]
        DOC["文档生成器<br/>DOCX/XLSX/PPTX/PDF"]
        WF["工作流引擎<br/>三级审批"]
        LLM["LLM 客户端<br/>DeepSeek V4 Pro"]
        SEC["安全治理<br/>DID + Guardrails + AuditChain"]
    end

    subgraph "数据知识域 (data-knowledge)"
        MYSQL[("MySQL 8.0<br/>业务数据 + 事件存储 + 审计链")]
        REDIS[("Redis 7<br/>缓存 + 事件总线 Pub/Sub")]
    end

    subgraph "监控运维"
        PROM["Prometheus<br/>指标采集"]
        GRAF["Grafana<br/>可视化面板"]
        ALERT["Alertmanager<br/>告警通知"]
        LOKI["Loki<br/>日志聚合"]
    end

    FE --> NGINX
    NGINX --> API
    API --> MW
    MW --> ORCH
    ORCH --> AGENTS
    AGENTS --> KB
    AGENTS --> DOC
    AGENTS --> WF
    AGENTS --> LLM
    AGENTS --> SEC
    API --> MYSQL
    API --> REDIS
    AGENTS -.->|事件总线| REDIS
    API -->|/metrics| PROM
    PROM --> GRAF
    PROM --> ALERT
    API -->|structlog JSON| LOKI
```

### 3.2 请求处理流程

```mermaid
sequenceDiagram
    participant U as 用户
    participant N as Nginx
    participant M as 中间件链
    participant R as 路由
    participant A as Agent
    participant DB as MySQL
    participant RDS as Redis

    U->>N: HTTP 请求 (带 JWT)
    N->>M: 反向代理到 FastAPI
    M->>M: RequestContext (生成 request_id)
    M->>M: RateLimit (滑动窗口限流)
    M->>M: AuthMiddleware (JWT 验证)
    M->>M: PermissionMiddleware (RBAC 校验)
    M->>R: 路由分发
    R->>A: 调用 Agent 业务方法
    A->>DB: 数据读写
    A->>RDS: 缓存/事件发布
    A-->>R: 返回业务结果
    R-->>M: JSON 响应
    M-->>N: 响应回传
    N-->>U: HTTP 响应
```

### 3.3 中间件执行顺序

FastAPI 中间件采用洋葱模型，后添加的先执行：

```
请求 → RequestContext → RateLimit → Auth → Permission → CORS → 路由处理 → 响应
```

| 顺序 | 中间件 | 职责 |
|---|---|---|
| 1 | RequestContextMiddleware | 生成 request_id，注入日志上下文 |
| 2 | RateLimitMiddleware | 滑动窗口限流（全局/认证/写操作/LLM） |
| 3 | AuthMiddleware | JWT 令牌验证，注入 current_user |
| 4 | PermissionMiddleware | 路径+方法映射到 resource:action，RBAC 校验 |
| 5 | CORSMiddleware | 跨域资源共享（从环境变量读取允许源） |

---

## 4. 33 个 Agent 分层架构

### 4.1 Agent 分层总览

```mermaid
graph LR
    subgraph "Phase 1 - 财务域 (7个)"
        P1A[InventoryGuardian]
        P1B[Bookkeeping]
        P1C[Procurement]
        P1D[Treasury]
        P1E[InternalAudit]
        P1F[Tax]
        P1G[Compliance]
    end

    subgraph "Phase 2 - 供应链域 (5个)"
        P2A[DemandForecast]
        P2B[SupplyPlanning]
        P2C[Production]
        P2D[Logistics]
        P2E[DynamicScheduler]
    end

    subgraph "Phase 3 - 核心业务 (3个)"
        P3A[Invoice]
        P3B[Reimbursement]
        P3C[Knowledge]
    end

    subgraph "Phase 4 - HR+营销 (8个)"
        P4A[HR]
        P4B[Recruitment]
        P4C[Performance]
        P4D[Payroll]
        P4E[CRM]
        P4F[Sales]
        P4G[Marketing]
        P4H[CustomerService]
    end

    subgraph "Phase 5 - 法务+运营+战略 (10个)"
        P5A[Legal]
        P5B[Risk]
        P5C[Contract]
        P5D[Quality]
        P5E[Asset]
        P5F[Project]
        P5G[Budget]
        P5H[BI]
        P5I[ESG]
        P5J[Admin]
    end
```

### 4.2 Phase 1 - 财务域 (7 个 Agent)

| Agent 名称 | 类名 | 核心能力 | 说明 |
|---|---|---|---|
| 库存守护Agent | `InventoryGuardianAgent` | 库存监控、自动补货、出入库核对 | 实时监控库存水位，低于安全库存自动生成补货建议 |
| 会计Agent | `BookkeepingAgent` | 凭证生成、凭证审批、科目核对、日清日结 | 自动生成会计凭证，校验借贷平衡 |
| 采购Agent | `ProcurementAgent` | 供应商比价、采购下单、到货跟踪 | 多供应商比价决策，自动生成采购订单 |
| 资金Agent | `TreasuryAgent` | 现金流预测、支付排程、资金调度 | 短期现金流预测，优化支付时序 |
| 内审Agent | `InternalAuditAgent` | 风险扫描、异常检测、审计报告 | 持续监控业务异常，生成审计警报 |
| 税务Agent | `TaxAgent` | 税务计算、申报生成、税率管理 | 自动计算各税种，生成申报表 |
| 合规Agent | `ComplianceAgent` | 法规匹配、控制点检查、合规报告 | 维护法规库，自动检查业务合规性 |

### 4.3 Phase 2 - 供应链域 (5 个 Agent)

| Agent 名称 | 类名 | 核心能力 |
|---|---|---|
| 需求预测Agent | `DemandForecastAgent` | 历史数据分析、季节性预测、趋势预测 |
| 供应规划Agent | `SupplyPlanningAgent` | 供应商产能规划、采购计划优化 |
| 生产Agent | `ProductionAgent` | 生产排程、工序管理、产能调度 |
| 物流Agent | `LogisticsAgent` | 运输规划、物流跟踪、成本优化 |
| 动态调度Agent | `DynamicSchedulerAgent` | 实时调度、资源分配、冲突消解 |

### 4.4 Phase 3 - 核心业务 (3 个 Agent)

| Agent 名称 | 类名 | 核心能力 |
|---|---|---|
| 发票Agent | `InvoiceAgent` | 发票开具、验真查重、发票管理 |
| 报销Agent | `ReimbursementAgent` | 报销审批、费用核验、支付处理 |
| 知识Agent | `KnowledgeAgent` | 知识检索、文档摄入、知识图谱查询 |

### 4.5 Phase 4 - HR + 营销域 (8 个 Agent)

| Agent 名称 | 类名 | 核心能力 |
|---|---|---|
| 人力Agent | `HRAgent` | 员工档案、考勤管理、组织架构 |
| 招聘Agent | `RecruitmentAgent` | 职位管理、候选人筛选、面试安排 |
| 绩效Agent | `PerformanceAgent` | KPI 模板、绩效周期、评审管理 |
| 薪酬Agent | `PayrollAgent` | 薪资计算、社保公积金、个税扣除 |
| CRM Agent | `CRMAgent` | 客户档案、商机管理、联系人管理 |
| 销售Agent | `SalesAgent` | 销售订单、销售目标、销售预测 |
| 营销Agent | `MarketingAgent` | 营销活动、线索管理、ROI 分析 |
| 客服Agent | `CustomerServiceAgent` | 工单管理、投诉处理、满意度跟踪 |

### 4.6 Phase 5 - 法务 + 运营 + 战略 (10 个 Agent)

| Agent 名称 | 类名 | 核心能力 |
|---|---|---|
| 法务Agent | `LegalAgent` | 法律咨询、诉讼管理、法规查询 |
| 风控Agent | `RiskAgent` | 风险识别、风险评估、风险预警 |
| 合同Agent | `ContractAgent` | 合同全生命周期、模板管理、到期提醒 |
| 质量Agent | `QualityAgent` | 质量检验、标准管理、改进跟踪 |
| 资产Agent | `AssetAgent` | 资产登记、折旧计算、盘点管理 |
| 项目Agent | `ProjectAgent` | 项目管理、里程碑、成本控制 |
| 预算Agent | `BudgetAgent` | 预算编制、预算审批、执行监控 |
| BI Agent | `BIAgent` | 经营分析、KPI 仪表盘、趋势分析 |
| ESG Agent | `ESGAgent` | ESG 指标、碳排放、社会责任 |
| 行政Agent | `AdminAgent` | 行政管理、办公资源、后勤服务 |

### 4.7 Agent 生命周期状态机

```mermaid
stateDiagram-v2
    [*] --> CREATED: 实例化
    CREATED --> INITIALIZING: start()
    INITIALIZING --> RUNNING: initialize() 成功
    INITIALIZING --> ERROR: initialize() 失败
    RUNNING --> PAUSED: pause()
    PAUSED --> RUNNING: resume()
    RUNNING --> DEGRADED: 熔断器 half_open
    DEGRADED --> RUNNING: 恢复正常
    RUNNING --> FUSED: 熔断器 open
    FUSED --> DEGRADED: 超时后 half_open
    RUNNING --> TERMINATED: stop()
    PAUSED --> TERMINATED: stop()
    ERROR --> TERMINATED: stop()
    TERMINATED --> [*]
```

---

## 5. Agent 通信机制

### 5.1 事件总线架构

Agent 之间通过 **Redis Pub/Sub 事件总线** 进行异步通信，实现松耦合协作：

```mermaid
graph TB
    subgraph "事件总线 (Redis Pub/Sub)"
        EB[EventBus]
    end

    subgraph "生产者"
        A1[InventoryGuardian] -->|publish| EB
        A2[Bookkeeping] -->|publish| EB
        A3[Procurement] -->|publish| EB
    end

    subgraph "消费者"
        EB -->|subscribe| A4[InternalAudit]
        EB -->|subscribe| A5[Compliance]
        EB -->|subscribe| A6[Treasury]
    end

    subgraph "持久化"
        EB --> ES[EventStore<br/>MySQL]
        EB --> AC[AuditChain<br/>哈希链/区块链]
    end
```

### 5.2 事件类型

| 事件类型 | 说明 | 典型场景 |
|---|---|---|
| `AGENT_REGISTERED` | Agent 注册 | Agent 启动时发布 |
| `AGENT_STATE_CHANGED` | Agent 状态变更 | 暂停/恢复/熔断 |
| `AGENT_DECISION_MADE` | Agent 决策 | 业务决策记录 |
| `INVENTORY_LOW` | 库存低于安全线 | InventoryGuardian 检测到低库存 |
| `VOUCHER_CREATED` | 凭证创建 | Bookkeeping 生成凭证 |
| `PURCHASE_ORDER_CREATED` | 采购订单创建 | Procurement 生成订单 |
| `PAYMENT_SCHEDULED` | 支付排程 | Treasury 安排付款 |
| `AUDIT_ALERT` | 审计警报 | InternalAudit 发现异常 |
| `COMPLIANCE_VIOLATION` | 合规违规 | Compliance 检测到违规 |

### 5.3 事件订阅机制

每个 Agent 在 `initialize()` 阶段通过 `subscribe_events()` 订阅感兴趣的事件类型：

```python
# 示例: InternalAuditAgent 订阅凭证和采购事件
self.subscribe_events(
    event_types=[EventType.VOUCHER_CREATED, EventType.PURCHASE_ORDER_CREATED],
    handler=self.process_event,
    filter_expr={"risk_level": "high"},  # 可选: 事件过滤
)
```

### 5.4 事件持久化

所有事件通过 `EventStore` 持久化到 MySQL，支持事件溯源 (Event Sourcing)：

- **EventStore**: 将事件写入 `event_store` 表，支持按聚合根 ID 查询历史
- **AuditChain**: 将关键操作记录到审计链，使用 SHA-256 哈希链接确保不可篡改
- **区块链审计** (可选): 启用 `blockchain_enabled=true` 后，审计记录写入 Hyperledger Fabric

---

## 6. 认证授权架构

### 6.1 JWT 认证流程

```mermaid
sequenceDiagram
    participant U as 用户
    participant API as FastAPI
    participant JWT as JWT Manager
    participant RBAC as RBAC Engine
    participant SES as Session Manager

    U->>API: POST /auth/login (username, password)
    API->>RBAC: 查找用户
    RBAC-->>API: 用户记录
    API->>API: 验证密码 (passlib bcrypt)
    API->>RBAC: 获取角色和权限
    RBAC-->>API: role_codes + permissions
    API->>JWT: 签发 access_token (30min) + refresh_token (7d)
    API->>SES: 创建会话
    API-->>U: 返回双令牌

    Note over U,API: 后续请求携带 access_token

    U->>API: GET /xxx (Authorization: Bearer <token>)
    API->>JWT: 验证令牌签名和有效期
    API->>SES: 检查会话是否有效
    API->>RBAC: 校验 resource:action 权限
    API-->>U: 返回业务数据
```

### 6.2 RBAC 5 层权限模型

系统采用 5 层角色架构，23 个角色，35 个资源模块，6 种操作：

```mermaid
graph TB
    L1["L1 - 董事长 (chairman)<br/>全系统最高权限"]
    L2["L2 - CEO (ceo)<br/>全业务域管理权限"]
    L3["L3 - C-Level 高管<br/>CTO/CFO/CIO/CMO/CHO/CRO/COO"]
    L4["L4 - 部门经理<br/>fin_manager/legal_manager/..."]
    L5["L5 - 部门员工<br/>dept_staff"]

    L1 --> L2
    L2 --> L3
    L3 --> L4
    L4 --> L5
```

**权限定义**: `资源:动作` 二维权限模型

| 动作 | 说明 | 敏感度 |
|---|---|---|
| `read` | 读取/查询 | normal |
| `create` | 创建/新增 | sensitive |
| `update` | 修改/更新 | sensitive |
| `delete` | 删除 | high_risk |
| `approve` | 审批 | sensitive / high_risk (高危资源) |
| `manage` | 管理 (含所有操作) | high_risk |

**资源模块** (35 个): system, agent, security, inventory, accounting, invoice, reimbursement, tax, procurement, treasury, production, audit, compliance, user, role, permission, crm, sales, marketing, customer_service, hr, recruitment, performance, contract, legal, risk, quality, asset, project, budget, bi, esg, knowledge, admin, document

### 6.3 权限校验机制

权限校验在两个层面进行：

1. **中间件层 (自动)**: `PermissionMiddleware` 将 API 路径+方法映射到 `resource:action`，自动校验
2. **路由层 (显式)**: 使用 `require_permission("resource", "action")` 依赖注入进行细粒度控制

```python
# 中间件自动校验示例 (路径映射)
# GET  /api/v1/inventory        → inventory:read
# POST /api/v1/inventory        → inventory:create
# POST /api/v1/accounting/vouchers/{id}/approve → accounting:approve

# 路由显式校验示例
@router.post("/users")
async def create_user(
    req: CreateUserRequest,
    current_user: dict = Depends(require_permission("user", "manage")),
):
    ...
```

### 6.4 默认用户账号

系统预置 3 个默认账号 (种子数据):

| 用户名 | 角色 | 说明 |
|---|---|---|
| `admin` | chairman (L1) | 系统管理员，拥有全部权限 |
| `manager` | ceo (L2) | 业务管理员，拥有全业务域权限 |
| `employee` | dept_staff (L5) | 普通员工，仅拥有只读权限 |

---

## 7. 知识库架构

### 7.1 知识库整体架构

```mermaid
graph TB
    subgraph "知识库引擎"
        KM["KnowledgeManager<br/>文档管理 + 审批流程"]
        HE["HybridEngine<br/>混合检索 (向量+关键词+图谱)"]
        KG["KnowledgeGraph<br/>知识图谱"]
        RE["RuleEngine<br/>业务规则引擎"]
        RAG["VectorStore<br/>TF-IDF 向量存储"]
    end

    subgraph "数据源"
        DOCS["企业文档<br/>制度/流程/规范"]
        MASTER["主数据<br/>产品/供应商/客户"]
        RULES["业务规则<br/>审批规则/风控规则"]
    end

    DOCS --> KM
    MASTER --> KG
    RULES --> RE
    KM --> RAG
    KM --> HE
    HE --> RAG
    HE --> KG
    HE --> RE
```

### 7.2 RAG 检索框架

知识库采用 **TF-IDF + 余弦相似度** 实现零依赖检索，无需 Embedding API：

| 组件 | 说明 |
|---|---|
| **分词器** | 优先使用 jieba 中文分词，不可用时降级到字符级 2-gram |
| **向量化** | TF-IDF (Term Frequency - Inverse Document Frequency) |
| **相似度** | 余弦相似度 (Cosine Similarity)，范围 0.0 - 1.0 |
| **存储** | InMemoryVectorStore (进程内)，未来可切换到 Milvus |
| **过滤** | 支持 metadata 标签过滤 (权限隔离) |

**混合检索 (Hybrid Retrieval)**:

```mermaid
graph LR
    Q[用户查询] --> V[向量检索<br/>TF-IDF + Cosine]
    Q --> K[关键词检索<br/>BM25-style]
    Q --> G[图谱检索<br/>实体关系]
    V --> F[融合排序]
    K --> F
    G --> F
    F --> R[Top-K 结果]
```

### 7.3 知识图谱

知识图谱存储企业主数据及其关系：

- **节点类型**: product (产品), supplier (供应商), customer (客户), department (部门), employee (员工), warehouse (仓库)
- **边类型**: supplies (供应), purchases (采购), belongs_to (属于), manages (管理), stored_in (存储于)
- **查询语法**: `node_type:product[category=电子元件]` (类型+属性过滤)

### 7.4 文档审批流程

```mermaid
stateDiagram-v2
    [*] --> draft: 上传文档
    draft --> pending: 提交审批
    pending --> published: 审批通过
    pending --> rejected: 审批拒绝
    rejected --> draft: 重新编辑
    published --> archived: 归档
```

---

## 8. 文档生成架构

### 8.1 文档生成器架构

```mermaid
graph TB
    REQ["生成请求<br/>agent_id + format + template_type + data"]
    DG["DocumentGenerator<br/>统一入口"]
    
    DG --> DOCX["DocxGenerator<br/>python-docx"]
    DG --> XLSX["XlsxGenerator<br/>openpyxl"]
    DG --> PPTX["PptxGenerator<br/>python-pptx"]
    DG --> PDF["PdfGenerator<br/>reportlab"]
    
    DOCX --> FILE1[".docx 文件"]
    XLSX --> FILE2[".xlsx 文件"]
    PPTX --> FILE3[".pptx 文件"]
    PDF --> FILE4[".pdf 文件"]
```

### 8.2 支持的文档格式与模板

| 格式 | 库 | MIME 类型 |
|---|---|---|
| DOCX | python-docx | application/vnd.openxmlformats-officedocument.wordprocessingml.document |
| XLSX | openpyxl | application/vnd.openxmlformats-officedocument.spreadsheetml.sheet |
| PPTX | python-pptx | application/vnd.openxmlformats-officedocument.presentationml.presentation |
| PDF | reportlab | application/pdf |

### 8.3 Agent 文档模板映射

| Agent | 模板类型 | 格式 | 文档名称 |
|---|---|---|---|
| accounting | voucher | xlsx | 会计凭证表 |
| accounting | basic | pdf | 财务月报 |
| tax | tax | xlsx | 税务申报表 |
| inventory | inventory | xlsx | 库存盘点表 |
| procurement | contract | docx | 采购合同 |
| bi | business_analysis | pptx | 经营分析PPT |
| hr | personnel | docx | 入职通知 |
| payroll | payroll | xlsx | 工资条 |
| invoice | invoice | pdf | 增值税发票 |
| reimbursement | reimbursement | pdf | 报销审批单 |
| audit | audit_report | pdf | 审计报告 |
| risk | risk_report | pdf | 风险评估报告 |

---

## 9. 数据库架构

### 9.1 数据库架构图

```mermaid
graph TB
    subgraph "MySQL 8.0 (主数据库)"
        T1["users<br/>用户表"]
        T2["roles<br/>角色表"]
        T3["permissions<br/>权限表"]
        T4["user_roles<br/>用户-角色关联"]
        T5["role_permissions<br/>角色-权限关联"]
        T6["departments<br/>部门表"]
        T7["event_store<br/>事件存储"]
        T8["audit_chain<br/>审计链"]
        T9["workflow_requests<br/>审批请求"]
        T10["workflow_records<br/>审批记录"]
        T11["knowledge_documents<br/>知识文档"]
        T12["knowledge_categories<br/>知识分类"]
        T13["alembic_version<br/>迁移版本"]
    end

    subgraph "Redis 7 (缓存 + 消息)"
        R1["Session Cache<br/>JWT 会话"]
        R2["RBAC Cache<br/>权限缓存"]
        R3["Event Bus<br/>Pub/Sub"]
        R4["Rate Limiter<br/>滑动窗口"]
        R5["Tool Registry<br/>工具缓存"]
    end
```

### 9.2 MySQL 连接配置

```python
# 连接池配置 (config.py)
mysql_url = "mysql+aiomysql://erp:erp_password@localhost:3306/erp_db?charset=utf8mb4"
db_pool_size = 10           # 连接池常驻连接数
db_max_overflow = 20        # 允许溢出连接数
db_pool_timeout = 30        # 获取连接超时 (秒)
db_pool_recycle = 1800      # 连接回收周期 (秒)
```

### 9.3 Redis 用途

| 用途 | 说明 | 持久化 |
|---|---|---|
| 会话管理 | JWT 令牌黑名单、活跃会话列表 | AOF |
| RBAC 缓存 | 用户/角色/权限内存缓存 (从 MySQL 加载) | 否 (内存) |
| 事件总线 | Redis Pub/Sub 异步消息 | 否 (Pub/Sub) |
| 限流器 | 滑动窗口请求计数 | 否 (内存) |
| 通用缓存 | 业务数据缓存 (TTL 300s) | 否 (内存) |

---

## 10. 降级策略说明

### 10.1 多层降级架构

系统采用多层降级策略，确保在部分组件不可用时仍能提供服务：

```mermaid
graph TB
    subgraph "LLM 降级"
        L1["LLM 可用<br/>智能决策"] -->|LLM 不可用| L2["规则引擎<br/>预设规则决策"]
    end

    subgraph "数据库降级"
        D1["MySQL 可用<br/>持久化存储"] -->|MySQL 不可用| D2["内存模式<br/>种子数据运行"]
    end

    subgraph "区块链降级"
        B1["Fabric 可用<br/>区块链审计"] -->|Fabric 不可用| B2["哈希链<br/>SHA-256 哈希链"]
    end

    subgraph "事件总线降级"
        E1["Kafka 可用<br/>分布式事件"] -->|Kafka 不可用| E2["内存 EventBus<br/>进程内事件"]
    end

    subgraph "Agent 降级"
        A1["Agent 正常<br/>RUNNING"] -->|连续失败| A2["熔断 FUSED<br/>暂停执行"]
        A2 -->|超时恢复| A3["半开 DEGRADED<br/>试探执行"]
        A3 -->|成功| A1
    end
```

### 10.2 降级策略明细

| 组件 | 正常模式 | 降级模式 | 触发条件 | 影响 |
|---|---|---|---|---|
| LLM | DeepSeek V4 Pro 智能决策 | 规则引擎预设规则 | `llm_enabled=false` 或 API 调用失败 | 决策质量降低，但功能不中断 |
| MySQL | 持久化存储 | 内存模式 (种子数据) | 连接失败或初始化异常 | 数据不持久，重启后丢失变更 |
| Redis | 分布式缓存 + Pub/Sub | 内存缓存 + 进程内事件 | 连接失败 | 多实例不共享缓存 |
| 区块链 | Hyperledger Fabric | SHA-256 哈希链 | `blockchain_enabled=false` 或连接失败 | 审计链不可跨节点验证 |
| Kafka | 分布式事件总线 | 内存 EventBus | `kafka_enabled=false` 或连接失败 | 事件不跨进程传播 |
| 知识库 | TF-IDF + 图谱 + 规则 | 空知识库 | 初始化失败 | 检索无结果 |
| Agent | RUNNING (正常运行) | FUSED (熔断) → DEGRADED (半开) | 连续失败 5 次 | 暂停业务处理，等待恢复 |

### 10.3 LLM 降级流程

```mermaid
flowchart TD
    A[Agent 需要决策] --> B{LLM 启用?}
    B -->|否| G[规则引擎决策]
    B -->|是| C{LLM 客户端可用?}
    C -->|否| G
    C -->|是| D[输入安全护栏检查]
    D -->|BLOCK| H[返回空/拒绝]
    D -->|PASS| E[调用 LLM API]
    E -->|成功| F[输出安全护栏检查]
    E -->|失败/超时| G
    F -->|BLOCK| H
    F -->|PASS| I[返回 LLM 决策结果]
    G --> J[返回规则决策结果]
```

### 10.4 Agent 熔断器

熔断器采用三态模型保护 Agent 不被连续故障拖垮：

| 状态 | 说明 | 行为 |
|---|---|---|
| `closed` (关闭) | 正常状态 | 所有请求正常执行 |
| `open` (打开) | 熔断状态 | 拒绝所有请求，等待超时 (60s) |
| `half_open` (半开) | 试探状态 | 允许单个请求通过，成功则恢复，失败则重新熔断 |

触发条件: 连续失败次数 >= `agent_circuit_breaker_threshold` (默认 5 次)

---

## 附录: 关键配置参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `ERP_LLM_ENABLED` | false | LLM 认知引擎开关 |
| `ERP_LLM_FALLBACK_TO_RULES` | true | LLM 不可用时降级到规则引擎 |
| `ERP_LLM_TIMEOUT` | 60 | LLM 请求超时 (秒) |
| `ERP_LLM_MAX_RETRIES` | 3 | LLM 最大重试次数 (指数退避) |
| `ERP_LLM_RATE_LIMIT` | 10 | LLM 每秒最大请求数 |
| `ERP_AGENT_CIRCUIT_BREAKER_THRESHOLD` | 5 | Agent 熔断阈值 |
| `ERP_AGENT_HEALTH_CHECK_INTERVAL` | 30 | Agent 健康检查间隔 (秒) |
| `ERP_AGENT_SLO_RESPONSE_TIME_MS` | 5000 | Agent SLO 响应时间 (毫秒) |
| `ERP_JWT_EXPIRE_MINUTES` | 30 | JWT 访问令牌有效期 (分钟) |
| `ERP_AUDIT_CHAIN_ENABLED` | true | 审计链开关 |
| `ERP_BLOCKCHAIN_ENABLED` | false | 区块链审计开关 |
| `ERP_HUMAN_APPROVAL_TIMEOUT` | 3600 | 人类审批超时 (秒) |
