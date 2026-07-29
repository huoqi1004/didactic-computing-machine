"""
Agent-Native ERP系统 - 主应用入口
FastAPI应用，集成所有核心模块

启动顺序：
1. 初始化事件总线
2. 注册并启动业务Agent
3. 启动API服务
"""
import asyncio
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from app.core.config import settings
from app.core.event.bus import event_bus
from app.core.event.store import event_store
from app.core.event.saga import saga_coordinator
from app.api.middleware.auth import AuthMiddleware, PermissionMiddleware
from app.api.routes import (
    router,
    auth_router,
    rbac_router,
    workflow_router,
    knowledge_router,
    hr_router,
    sales_marketing_router,
    operations_strategy_router,
    admin_system_router,
)
from app.core.agent.orchestrator import orchestrator
from app.core.security.did import did_registry
from app.core.security.authorization import authz_engine
from app.core.security.audit import audit_chain
from app.core.security.guardrails import guardrails
from app.core.agent.tools import tool_registry, Tool
from app.core.knowledge import init_knowledge_base
from app.core.versioning import (
    version_registry,
    snapshot_manager,
)
from app.agents.inventory.agent import InventoryGuardianAgent
from app.agents.accounting.agent import BookkeepingAgent
from app.agents.procurement.agent import ProcurementAgent
from app.agents.treasury.agent import TreasuryAgent
from app.agents.audit.agent import InternalAuditAgent
from app.agents.tax.agent import TaxAgent
from app.agents.compliance.agent import ComplianceAgent
from app.agents.demand.agent import DemandForecastAgent
from app.agents.supply.agent import SupplyPlanningAgent
from app.agents.production.agent import ProductionAgent
from app.agents.logistics.agent import LogisticsAgent
from app.agents.scheduler.agent import DynamicSchedulerAgent
from app.agents.invoice.agent import InvoiceAgent
from app.agents.reimbursement.agent import ReimbursementAgent
from app.agents.knowledge.agent import KnowledgeAgent
# Phase 4: HR + 销售营销 Agent群
from app.agents.hr.agent import HRAgent
from app.agents.recruitment.agent import RecruitmentAgent
from app.agents.performance.agent import PerformanceAgent
from app.agents.payroll.agent import PayrollAgent
from app.agents.crm.agent import CRMAgent
from app.agents.sales.agent import SalesAgent
from app.agents.marketing.agent import MarketingAgent
from app.agents.customer_service.agent import CustomerServiceAgent
# Phase 5: 法务风控+运营管理+战略分析 Agent群
from app.agents.legal.agent import LegalAgent
from app.agents.risk.agent import RiskAgent
from app.agents.contract.agent import ContractAgent
from app.agents.quality.agent import QualityAgent
from app.agents.asset.agent import AssetAgent
from app.agents.project.agent import ProjectAgent
from app.agents.budget.agent import BudgetAgent
from app.agents.bi.agent import BIAgent
from app.agents.esg.agent import ESGAgent
from app.agents.admin.agent import AdminAgent

logger = structlog.get_logger()

# 全局Agent引用
inventory_agent: InventoryGuardianAgent = None
bookkeeping_agent: BookkeepingAgent = None
procurement_agent: ProcurementAgent = None
treasury_agent: TreasuryAgent = None
audit_agent: InternalAuditAgent = None
tax_agent: TaxAgent = None
compliance_agent: ComplianceAgent = None
demand_agent: DemandForecastAgent = None
supply_agent: SupplyPlanningAgent = None
production_agent: ProductionAgent = None
logistics_agent: LogisticsAgent = None
scheduler_agent: DynamicSchedulerAgent = None
invoice_agent: InvoiceAgent = None
reimbursement_agent: ReimbursementAgent = None
knowledge_agent: KnowledgeAgent = None
# Phase 4
hr_agent: HRAgent = None
recruitment_agent: RecruitmentAgent = None
performance_agent: PerformanceAgent = None
payroll_agent: PayrollAgent = None
crm_agent: CRMAgent = None
sales_agent: SalesAgent = None
marketing_agent: MarketingAgent = None
customer_service_agent: CustomerServiceAgent = None
# Phase 5
legal_agent: LegalAgent = None
risk_agent: RiskAgent = None
contract_agent: ContractAgent = None
quality_agent: QualityAgent = None
asset_agent: AssetAgent = None
project_agent: ProjectAgent = None
budget_agent: BudgetAgent = None
bi_agent: BIAgent = None
esg_agent: ESGAgent = None
admin_agent: AdminAgent = None


def register_tools():
    """注册系统工具"""
    # 查询库存工具
    async def query_inventory(product_id: str = "") -> dict:
        if not product_id:
            return {"inventory": inventory_agent.get_inventory_status()}
        for inv_status in inventory_agent.get_inventory_status():
            if inv_status["product_id"] == product_id:
                return inv_status
        return {"error": "Product not found"}

    tool_registry.register(Tool(
        name="query_inventory",
        description="查询库存状态",
        handler=query_inventory,
        required_permissions=["read"],
        risk_level="low",
    ))

    # 查询凭证工具
    async def query_vouchers(status: str = "") -> dict:
        return {"vouchers": bookkeeping_agent.get_vouchers(status if status else None)}

    tool_registry.register(Tool(
        name="query_vouchers",
        description="查询会计凭证",
        handler=query_vouchers,
        required_permissions=["read"],
        risk_level="low",
    ))

    # 审计查询工具
    async def query_audit_logs(risk_level: str = "") -> dict:
        return {"entries": audit_chain.query(risk_level=risk_level if risk_level else None)}

    tool_registry.register(Tool(
        name="query_audit_logs",
        description="查询审计日志",
        handler=query_audit_logs,
        required_permissions=["read"],
        risk_level="medium",
    ))

    # 查询采购订单工具
    async def query_purchase_orders(status: str = "") -> dict:
        if not procurement_agent:
            return {"orders": []}
        return {"orders": procurement_agent.get_purchase_orders(status if status else None)}

    tool_registry.register(Tool(
        name="query_purchase_orders",
        description="查询采购订单列表",
        handler=query_purchase_orders,
        required_permissions=["read"],
        risk_level="low",
    ))

    # 查询供应商工具
    async def query_suppliers() -> dict:
        if not procurement_agent:
            return {"suppliers": []}
        return {"suppliers": procurement_agent.get_suppliers()}

    tool_registry.register(Tool(
        name="query_suppliers",
        description="查询供应商列表",
        handler=query_suppliers,
        required_permissions=["read"],
        risk_level="low",
    ))

    # 查询现金流预测工具
    async def query_cash_flow_forecast(days: int = 7) -> dict:
        if not treasury_agent:
            return {"forecast": {}}
        return {"forecast": await treasury_agent.get_cash_flow_forecast(days)}

    tool_registry.register(Tool(
        name="query_cash_flow_forecast",
        description="查询现金流预测",
        handler=query_cash_flow_forecast,
        required_permissions=["read"],
        risk_level="low",
    ))

    # 查询支付排程工具
    async def query_payment_schedule(status: str = "") -> dict:
        if not treasury_agent:
            return {"payments": []}
        return {"payments": treasury_agent.get_payment_schedule(status if status else None)}

    tool_registry.register(Tool(
        name="query_payment_schedule",
        description="查询支付排程",
        handler=query_payment_schedule,
        required_permissions=["read"],
        risk_level="low",
    ))

    # 查询内审警报工具
    async def query_audit_alerts(severity: str = "") -> dict:
        if not audit_agent:
            return {"alerts": []}
        return {"alerts": audit_agent.get_alerts(severity if severity else None)}

    tool_registry.register(Tool(
        name="query_audit_alerts",
        description="查询内审警报",
        handler=query_audit_alerts,
        required_permissions=["read"],
        risk_level="medium",
    ))

    # 查询税务记录工具
    async def query_tax_records(tax_type: str = "") -> dict:
        if not tax_agent:
            return {"records": []}
        return {"records": tax_agent.get_tax_records(tax_type if tax_type else None)}

    tool_registry.register(Tool(
        name="query_tax_records",
        description="查询税务记录",
        handler=query_tax_records,
        required_permissions=["read"],
        risk_level="low",
    ))

    # 查询税率表工具
    async def query_tax_rates() -> dict:
        if not tax_agent:
            return {"rates": {}}
        return {"rates": tax_agent.get_tax_rates()}

    tool_registry.register(Tool(
        name="query_tax_rates",
        description="查询税率表",
        handler=query_tax_rates,
        required_permissions=["read"],
        risk_level="low",
    ))

    # 查询法规库工具
    async def query_regulations(category: str = "") -> dict:
        if not compliance_agent:
            return {"regulations": []}
        return {"regulations": compliance_agent.get_regulations(category if category else None)}

    tool_registry.register(Tool(
        name="query_regulations",
        description="查询法规库",
        handler=query_regulations,
        required_permissions=["read"],
        risk_level="low",
    ))

    # 查询控制点工具
    async def query_control_points(process: str = "") -> dict:
        if not compliance_agent:
            return {"control_points": []}
        return {"control_points": compliance_agent.get_control_points(process if process else None)}

    tool_registry.register(Tool(
        name="query_control_points",
        description="查询合规控制点",
        handler=query_control_points,
        required_permissions=["read"],
        risk_level="low",
    ))

    # 知识检索工具
    async def knowledge_search(query: str = "", top_k: int = 5) -> dict:
        if not knowledge_agent:
            return {"results": []}
        results = await knowledge_agent.retrieve(
            query=query,
            top_k=top_k,
        )
        return {"results": results}

    tool_registry.register(Tool(
        name="knowledge_search",
        description="知识库检索（向量+关键词+图谱三路混合检索）",
        handler=knowledge_search,
        required_permissions=["read"],
        risk_level="low",
    ))

    # 知识摄入工具
    async def knowledge_ingest(text: str, category: str = "uncategorized") -> dict:
        if not knowledge_agent:
            return {"error": "knowledge_agent_not_available"}
        record = await knowledge_agent.ingest_text(
            text=text,
            category=category,
        )
        return record

    tool_registry.register(Tool(
        name="knowledge_ingest",
        description="向知识库摄入文本文档",
        handler=knowledge_ingest,
        required_permissions=["create"],
        risk_level="medium",
    ))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global inventory_agent, bookkeeping_agent, procurement_agent
    global treasury_agent, audit_agent, tax_agent, compliance_agent
    global demand_agent, supply_agent, production_agent
    global logistics_agent, scheduler_agent
    global knowledge_agent
    global hr_agent, recruitment_agent, performance_agent, payroll_agent
    global crm_agent, sales_agent, marketing_agent, customer_service_agent
    global legal_agent, risk_agent, contract_agent, quality_agent
    global asset_agent, project_agent, budget_agent, bi_agent, esg_agent
    global admin_agent

    logger.info("app.starting", version=settings.app_version)

    # 1. 启动事件总线
    await event_bus.start()
    logger.info("app.event_bus_started")

    # 2. 初始化持久化层（MySQL + Redis）
    # 数据库初始化失败时降级到内存模式，不影响应用启动

    # 2.0 初始化 MySQL 引擎（RBAC 缓存在 2.2 步统一加载）
    try:
        from app.core.database import init_db as init_mysql
        mysql_engine = await init_mysql()
        if mysql_engine is not None:
            logger.info("app.mysql_ready", backend="mysql")
        else:
            logger.warning("app.mysql_unavailable_degraded_to_memory")
    except Exception as e:
        logger.warning("app.mysql_init_failed", error=str(e), degraded="memory")

    try:
        store_ready = await event_store.init_db()
        if store_ready:
            logger.info("app.event_store_db_ready", backend="mysql")
        else:
            logger.warning("app.event_store_db_unavailable_degraded_to_memory")
    except Exception as e:
        logger.warning("app.event_store_init_failed",
                       error=str(e), degraded="memory")

    try:
        audit_ready = await audit_chain.init_db()
        if audit_ready:
            logger.info("app.audit_chain_db_ready", backend="mysql")
        else:
            logger.warning("app.audit_chain_db_unavailable_degraded_to_memory")
    except Exception as e:
        logger.warning("app.audit_chain_init_failed",
                       error=str(e), degraded="memory")

    # 2.1 初始化区块链审计客户端（Hyperledger Fabric，可选）
    # 根据 settings.blockchain_enabled 选择 Fabric 或降级到哈希链
    # 失败时自动降级，不影响应用启动
    try:
        blockchain_ready = await audit_chain.init_blockchain()
        if blockchain_ready:
            impl = audit_chain._blockchain_client.implementation_name
            logger.info(
                "app.audit_chain_blockchain_ready",
                implementation=impl,
                blockchain_enabled=settings.blockchain_enabled,
            )
        else:
            logger.warning("app.audit_chain_blockchain_unavailable")
    except Exception as e:
        logger.warning(
            "app.audit_chain_blockchain_init_failed",
            error=str(e),
            error_type=type(e).__name__,
            degraded="hash_chain_only",
        )

    # 2.2 初始化主数据库 MySQL + 从 DB 加载 RBAC 缓存
    # 初始化失败时降级到内存种子数据，不影响应用启动
    try:
        from app.core.database import init_db as _init_mysql
        from app.core.rbac import rbac_engine as _rbac_engine
        mysql_engine = await _init_mysql()
        if mysql_engine is not None:
            logger.info("app.mysql_ready", backend="mysql")
            # 从 MySQL 加载 RBAC 数据到内存缓存
            # 若 DB 未播种，load_from_db 会保留现有种子数据
            await _rbac_engine.load_from_db()
            rbac_stats = _rbac_engine.get_stats()
            logger.info(
                "app.rbac_cache_loaded",
                users=rbac_stats["users"],
                roles=rbac_stats["roles"],
                permissions=rbac_stats["permissions"],
                initialized=rbac_stats["initialized"],
            )
        else:
            logger.warning("app.mysql_unavailable_degraded_to_memory")
    except Exception as e:
        logger.warning(
            "app.rbac_load_failed",
            error=str(e),
            error_type=type(e).__name__,
            degraded="memory_seed_data",
        )

    # 3. 初始化认知与知识层（知识图谱 + RAG 检索 + 规则引擎）
    # 在Agent启动前注入示例企业主数据、业务文档与预置规则
    # 初始化失败时降级到空知识库，不影响Agent启动
    try:
        kb_stats = await init_knowledge_base()
        logger.info(
            "app.knowledge_base_initialized",
            nodes=kb_stats.get("graph", {}).get("nodes", 0),
            edges=kb_stats.get("graph", {}).get("edges", 0),
            documents=kb_stats.get("documents", 0),
            rules=kb_stats.get("rules", 0),
        )
    except Exception as e:
        logger.warning(
            "app.knowledge_base_init_failed",
            error=str(e),
            error_type=type(e).__name__,
            degraded="empty_knowledge_base",
        )

    # 4. 创建并注册业务Agent
    inventory_agent = InventoryGuardianAgent()
    bookkeeping_agent = BookkeepingAgent()
    procurement_agent = ProcurementAgent()
    treasury_agent = TreasuryAgent()
    audit_agent = InternalAuditAgent()
    tax_agent = TaxAgent()
    compliance_agent = ComplianceAgent()
    demand_agent = DemandForecastAgent()
    supply_agent = SupplyPlanningAgent()
    production_agent = ProductionAgent()
    logistics_agent = LogisticsAgent()
    scheduler_agent = DynamicSchedulerAgent()
    invoice_agent = InvoiceAgent()
    reimbursement_agent = ReimbursementAgent()
    knowledge_agent = KnowledgeAgent()
    # Phase 4: HR + 销售营销 Agent群
    hr_agent = HRAgent()
    recruitment_agent = RecruitmentAgent()
    performance_agent = PerformanceAgent()
    payroll_agent = PayrollAgent()
    crm_agent = CRMAgent()
    sales_agent = SalesAgent()
    marketing_agent = MarketingAgent()
    customer_service_agent = CustomerServiceAgent()
    # Phase 5: 法务风控+运营管理+战略分析 Agent群
    legal_agent = LegalAgent()
    risk_agent = RiskAgent()
    contract_agent = ContractAgent()
    quality_agent = QualityAgent()
    asset_agent = AssetAgent()
    project_agent = ProjectAgent()
    budget_agent = BudgetAgent()
    bi_agent = BIAgent()
    esg_agent = ESGAgent()
    admin_agent = AdminAgent()

    await orchestrator.register_agent(inventory_agent)
    await orchestrator.register_agent(bookkeeping_agent)
    await orchestrator.register_agent(procurement_agent)
    await orchestrator.register_agent(treasury_agent)
    await orchestrator.register_agent(audit_agent)
    await orchestrator.register_agent(tax_agent)
    await orchestrator.register_agent(compliance_agent)
    await orchestrator.register_agent(demand_agent)
    await orchestrator.register_agent(supply_agent)
    await orchestrator.register_agent(production_agent)
    await orchestrator.register_agent(logistics_agent)
    await orchestrator.register_agent(scheduler_agent)
    await orchestrator.register_agent(invoice_agent)
    await orchestrator.register_agent(reimbursement_agent)
    await orchestrator.register_agent(knowledge_agent)
    # Phase 4
    await orchestrator.register_agent(hr_agent)
    await orchestrator.register_agent(recruitment_agent)
    await orchestrator.register_agent(performance_agent)
    await orchestrator.register_agent(payroll_agent)
    await orchestrator.register_agent(crm_agent)
    await orchestrator.register_agent(sales_agent)
    await orchestrator.register_agent(marketing_agent)
    await orchestrator.register_agent(customer_service_agent)
    # Phase 5
    await orchestrator.register_agent(legal_agent)
    await orchestrator.register_agent(risk_agent)
    await orchestrator.register_agent(contract_agent)
    await orchestrator.register_agent(quality_agent)
    await orchestrator.register_agent(asset_agent)
    await orchestrator.register_agent(project_agent)
    await orchestrator.register_agent(budget_agent)
    await orchestrator.register_agent(bi_agent)
    await orchestrator.register_agent(esg_agent)
    await orchestrator.register_agent(admin_agent)

    # 5. 启动Agent
    await inventory_agent.start()
    await bookkeeping_agent.start()
    await procurement_agent.start()
    await treasury_agent.start()
    await audit_agent.start()
    await tax_agent.start()
    await compliance_agent.start()
    await demand_agent.start()
    await supply_agent.start()
    await production_agent.start()
    await logistics_agent.start()
    await scheduler_agent.start()
    await invoice_agent.start()
    await reimbursement_agent.start()
    await knowledge_agent.start()
    # Phase 4
    await hr_agent.start()
    await recruitment_agent.start()
    await performance_agent.start()
    await payroll_agent.start()
    await crm_agent.start()
    await sales_agent.start()
    await marketing_agent.start()
    await customer_service_agent.start()
    # Phase 5
    await legal_agent.start()
    await risk_agent.start()
    await contract_agent.start()
    await quality_agent.start()
    await asset_agent.start()
    await project_agent.start()
    await budget_agent.start()
    await bi_agent.start()
    await esg_agent.start()
    await admin_agent.start()

    # 6. 注册工具
    register_tools()

    # 6.1 注入 Agent 引用到路由模块（供 API 路由访问）
    from app.api.routes._agent_refs import set_agents
    set_agents(
        hr=hr_agent,
        recruitment=recruitment_agent,
        performance=performance_agent,
        payroll=payroll_agent,
        crm=crm_agent,
        sales=sales_agent,
        marketing=marketing_agent,
        customer_service=customer_service_agent,
        # Phase 5
        legal=legal_agent,
        risk=risk_agent,
        contract=contract_agent,
        quality=quality_agent,
        asset=asset_agent,
        project=project_agent,
        budget=budget_agent,
        bi=bi_agent,
        esg=esg_agent,
        admin=admin_agent,
    )

    # 6.1 初始化版本化与灰度发布系统
    # 在知识库初始化后创建初始版本快照，并为每个运行中的Agent注册初始版本
    if settings.versioning_enabled:
        try:
            # 创建初始知识库快照
            initial_snapshot = await snapshot_manager.create_snapshot()
            logger.info(
                "app.initial_snapshot_created",
                snapshot_id=initial_snapshot.snapshot_id,
            )

            # 为每个运行中的Agent注册初始版本
            initial_agents = [
                inventory_agent,
                bookkeeping_agent,
                procurement_agent,
                treasury_agent,
                audit_agent,
                tax_agent,
                compliance_agent,
                demand_agent,
                supply_agent,
                production_agent,
                logistics_agent,
                scheduler_agent,
                invoice_agent,
                reimbursement_agent,
                knowledge_agent,
                # Phase 4
                hr_agent,
                recruitment_agent,
                performance_agent,
                payroll_agent,
                crm_agent,
                sales_agent,
                marketing_agent,
                customer_service_agent,
                # Phase 5
                legal_agent,
                risk_agent,
                contract_agent,
                quality_agent,
                asset_agent,
                project_agent,
                budget_agent,
                bi_agent,
                esg_agent,
                admin_agent,
            ]
            for agent in initial_agents:
                if agent is None:
                    continue
                version_registry.register_version(
                    agent_name=agent.name,
                    version="1.0.0",
                    config={
                        "capabilities": list(agent.capabilities),
                        "security_clearance": agent.security_clearance,
                        "model_params": {"confidence": 0.9},
                    },
                    rules=[],
                    changelog="初始版本",
                    knowledge_snapshot_id=initial_snapshot.snapshot_id,
                    created_by="system",
                )
            logger.info(
                "app.versioning_initialized",
                agents_registered=len(initial_agents),
                snapshot_id=initial_snapshot.snapshot_id,
            )
        except Exception as e:
            logger.warning(
                "app.versioning_init_failed",
                error=str(e),
                error_type=type(e).__name__,
                degraded="versioning_disabled",
            )

    # 7. 审计启动事件
    await audit_chain.log(
        actor_did="system",
        action="system.start",
        resource="erp_system",
        result="success",
        details={"version": settings.app_version, "agents": 33},
        risk_level="low",
    )

    logger.info("app.started",
                agents=33,
                version=settings.app_version)

    yield

    # 关闭
    logger.info("app.stopping")
    await orchestrator.stop_all()
    await event_bus.stop()

    # 关闭持久化层连接
    try:
        from app.core.database import close_all
        await close_all()
    except Exception as e:
        logger.warning("app.database_close_failed", error=str(e))

    logger.info("app.stopped")


# 创建FastAPI应用
app = FastAPI(
    title="Agent-Native ERP System",
    description="智能体原生ERP系统 - 以自主AI Agent为基本业务单元",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# JWT认证中间件 + RBAC权限校验中间件
# 顺序：PermissionMiddleware → AuthMiddleware → CORS
# Starlette中间件执行顺序：后添加的先执行（洋葱模型）
app.add_middleware(PermissionMiddleware)
app.add_middleware(AuthMiddleware)

# 路由
app.include_router(router, prefix="/api/v1")
# Phase 2/3/4 路由：直接包含各子路由器（避免嵌套 include_router 导致路由不展开）
app.include_router(auth_router)
app.include_router(rbac_router)
app.include_router(workflow_router)
app.include_router(knowledge_router)
app.include_router(hr_router)
app.include_router(sales_marketing_router)
app.include_router(operations_strategy_router)
app.include_router(admin_system_router)

# Prometheus指标
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


@app.get("/")
async def root():
    """根端点"""
    return {
        "name": "Agent-Native ERP System",
        "version": "1.0.0",
        "description": "智能体原生ERP系统",
        "docs": "/docs",
        "api": "/api/v1",
        "metrics": "/metrics",
    }


@app.get("/health")
async def health():
    """健康检查端点 - 供 Docker 健康检查和负载均衡器使用"""
    return {
        "status": "healthy",
        "service": "erp-backend",
        "version": settings.app_version,
        "timestamp": asyncio.get_event_loop().time(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level="info" if settings.debug else "warning",
    )
