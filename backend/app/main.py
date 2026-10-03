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
from typing import Any
from uuid import uuid4
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from app.core.config import settings
from app.core.security.startup_checks import sanitize_cors_origins, validate_jwt_secret

# P1-3（fail-closed）：生产未配置/弱熵 JWT 密钥直接拒绝启动；
# debug 模式自动生成临时密钥。必须在路由导入（jwt_manager 初始化）之前完成。
settings.jwt_secret = validate_jwt_secret(settings.jwt_secret, settings.debug)

from app.core.logging_config import configure_logging, RequestContextMiddleware
from app.core.event.bus import event_bus
from app.core.event.store import event_store
from app.core.event.saga import saga_coordinator
from app.api.middleware.auth import AuthMiddleware, PermissionMiddleware
from app.api.middleware.rate_limit import RateLimitMiddleware
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
    conversation_router,
    document_router,
    messaging_router,
    reimbursement_router,
    agent_ops_router,
    customizations_router,
    workflow_definitions_router,
    mcp_servers_router,
    mcp_exposure_router,
    skills_admin_router,
    operation_logs_router,
    operation_rollback_router,
)
from app.api.routes.tenant import router as tenant_router
from app.core.tenant import TenantMiddleware
from app.core.agent.orchestrator import orchestrator
from app.core.security.did import did_registry
from app.core.security.authorization import authz_engine
from app.core.security.audit import audit_chain
from app.core.maintenance.retention import retention_loop
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
from app.agents.project_supervisor.agent import ProjectSupervisorAgent

# 初始化结构化日志（JSON 格式，便于 Loki / ELK 采集）
configure_logging(debug=settings.debug)

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
project_supervisor_agent: ProjectSupervisorAgent = None


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
    global admin_agent, project_supervisor_agent

    logger.info("app.starting", version=settings.app_version)

    # 1. 启动事件总线
    await event_bus.start()
    logger.info("app.event_bus_started")

    # 2. 初始化持久化层（MySQL + Redis）
    # 数据库初始化失败时降级到内存模式，不影响应用启动

    # 2.0 初始化 MySQL 引擎 + 建库兜底 + RBAC 播种与缓存加载
    # （此前 2.0 与 2.2 重复调用 init_db 两次，已合并；评估报告 C-01/问题#20）
    try:
        from app.core.database import init_db as init_mysql, bootstrap_schema
        from app.core.rbac import rbac_engine as _rbac_engine
        mysql_engine = await init_mysql()
        if mysql_engine is not None:
            # 建库兜底：空库时以 app/db/models.py 为唯一事实源建出全部业务表，
            # 非空库自动跳过不触碰存量结构（解决"全新部署必然失败"的 C-01）
            schema_info = await bootstrap_schema()
            logger.info("app.mysql_ready", backend="mysql", schema=schema_info)

            # 先将种子数据幂等播种到 MySQL（全新空库也能获得完整 RBAC 数据），
            # 再从 DB 加载到内存缓存（覆盖导入期的内存种子数据，与 DB 保持一致）
            from app.core.rbac.seed import persist_seed_data
            await persist_seed_data(_rbac_engine)
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
            # 降级必须显式告警（ERROR 级），禁止"表面 healthy、实际零持久化"的静默降级
            logger.error(
                "app.mysql_unavailable_degraded_to_memory",
                impact="auth/rbac/messaging 持久化全部失效，重启即丢数据，请立即排查 MySQL",
            )
            # 无 DB 降级：内存种子（随机密码）即真实登录凭据，一次性打印
            from app.core.rbac.seed import print_seed_credentials

            print_seed_credentials()
    except Exception as e:
        logger.error(
            "app.mysql_init_failed",
            error=str(e),
            error_type=type(e).__name__,
            degraded="memory",
            impact="auth/rbac/messaging 持久化全部失效，请立即排查 MySQL",
        )

    # 2.0.5 初始化 Redis（会话/吊销黑名单/限流计数的共享存储，路线图 2.5）
    # 失败时各使用方自动降级进程内实现（显式节流告警），不影响应用启动
    try:
        from app.core.database import init_redis as _init_redis

        redis_client = await _init_redis()
        if redis_client is not None:
            logger.info("app.redis_ready", url=settings.redis_url)
        else:
            logger.warning(
                "app.redis_unavailable_degraded_to_memory",
                impact="会话/登出吊销/限流退化为单实例内存态，多实例部署不一致",
            )
    except Exception as e:
        logger.warning(
            "app.redis_init_failed",
            error=str(e),
            error_type=type(e).__name__,
            degraded="in_memory",
        )

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
            # 审计哈希链续接（D-05 缺陷1）：从库中恢复链尾，重启不再断链
            await audit_chain.restore_chain_from_db()
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

    # 2.2 主数据库 RBAC 播种与缓存加载已合并到 2.0 步
    # （此前在 2.0 与 2.2 重复调用 init_db 两次，见评估报告问题总表 #20）

    # 2.3 操作日志底座（G2.2）：启动异步刷盘任务 + 订阅审计事件流
    # （治理面/工作流/Agent 全部审计条目自动流入 operation_logs）
    try:
        from app.core.observability.operation_log import operation_log_buffer

        await operation_log_buffer.start()
        logger.info("app.operation_log_ready", buffered=operation_log_buffer.pending())
    except Exception as e:
        logger.warning("app.operation_log_start_failed",
                       error=str(e), degraded="memory_only")

    # 2.4 工作流 Saga 恢复（G5.2）：续跑上次进程未完成的失败补偿（kill -9 语义）
    try:
        from app.core.agent.workflow_saga import saga_recover_pending

        recover_out = await saga_recover_pending()
        if recover_out.get("recovered"):
            logger.warning("app.workflow_saga_recovered", **recover_out)
        else:
            logger.info("app.workflow_saga_clean")
    except Exception as e:
        logger.warning("app.workflow_saga_recover_failed", error=str(e))

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
        # 知识分块持久化水合（E-04：重启不归零——DB 分块重建内存索引）
        from app.core.knowledge.persistence import hydrate_knowledge_from_db

        hydrate_info = await hydrate_knowledge_from_db()
        logger.info("app.knowledge_hydrated", **hydrate_info)
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
    project_supervisor_agent = ProjectSupervisorAgent()

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
    await orchestrator.register_agent(project_supervisor_agent)

    # 4.5 Agent 状态持久化水合（路线图 2.2/2.3：重启一致性）
    # DB 有数据则以库为准覆盖内存种子；DB 空则将内存种子幂等播种入库；
    # DB 不可用时保持内存态（repository 层节流告警）。
    # 顺序敏感：crm 必须先于 sales（销售种子订单引用种子客户的 FK 前置）。
    batch_persist_agents = {
        "payroll": payroll_agent,
        "hr": hr_agent,
        "crm": crm_agent,
        "sales": sales_agent,
        "treasury": treasury_agent,
        "contract": contract_agent,
        "reimbursement": reimbursement_agent,
    }
    for agent_key, agent_obj in batch_persist_agents.items():
        try:
            hydrated = await agent_obj.hydrate_from_db()
            logger.info(
                "app.agent_hydration_complete",
                agent=agent_key,
                hydrated_from_db=hydrated,
            )
        except Exception as e:
            logger.warning(
                "app.agent_hydration_failed",
                agent=agent_key,
                error=str(e),
                error_type=type(e).__name__,
                degraded="in_memory_seed",
            )

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
    await project_supervisor_agent.start()

    register_tools()

    # 5.4 注册知识检索工具（知识库改进：Agent 问答赋能）
    try:
        import app.agents.knowledge.tools  # noqa: F401

        logger.info("app.knowledge_tools_registered")
    except Exception as e:
        logger.warning("app.knowledge_tools_register_failed",
                       error=str(e), error_type=type(e).__name__)

    # 5.5 总调度 Agent（Phase C：多域协同入口）——非 BaseAgent，
    # 不进 orchestrator 状态机；经 messaging _AGENT_REFS_GETTERS 暴露
    from app.core.agent.supervisor import get_supervisor_agent

    get_supervisor_agent()
    logger.info("app.supervisor_agent_ready", domain="supervisor")

    # 6.0 注册场景工具（Phase A：报销/资金域治理面工具，LangGraph 编排可调用）
    try:
        import app.agents.reimbursement.tools  # noqa: F401
        import app.agents.treasury.tools  # noqa: F401
        import app.agents.payroll.tools  # noqa: F401  (Phase B)
        import app.agents.hr.tools  # noqa: F401  (Phase B)
        import app.agents.crm.tools  # noqa: F401  (Phase B)
        import app.agents.sales.tools  # noqa: F401  (Phase B)
        import app.agents.contract.tools  # noqa: F401  (Phase B)
        from app.core.agent.tools_v2 import tool_registry_v2

        logger.info(
            "app.scenario_tools_registered",
            count=len(tool_registry_v2.list_specs()),
            tools=[s.name for s in tool_registry_v2.list_specs()],
        )
    except Exception as e:
        logger.warning(
            "app.scenario_tools_register_failed",
            error=str(e), error_type=type(e).__name__,
        )

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
        project_supervisor=project_supervisor_agent,
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
                project_supervisor_agent,
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

    # 8. 事件/审计表保留策略后台任务（B-03：events 表无界膨胀治理）
    retention_task = asyncio.create_task(retention_loop())
    logger.info(
        "app.retention_loop_started",
        event_retention_days=settings.event_retention_days,
        audit_retention_days=settings.audit_retention_days,
        cleanup_interval_hours=settings.retention_cleanup_interval_hours,
    )

    # 8.2 审计链完整性校验调度（G3.3：增量每日 + 全量每周 + 死信补推）
    from app.core.security.audit import audit_verify_loop

    audit_verify_task = asyncio.create_task(audit_verify_loop())
    logger.info("app.audit_verify_loop_started",
                incremental_hours=settings.audit_verify_incremental_hours,
                full_days=settings.audit_verify_full_days)

    # 8.5 外部 MCP 工具桥接（D3 环境变量清单 + F4.1 管理端登记清单）
    #     register_external_tools() 此前只在测试里被调用过，运行期从未接线，
    #     因此真实进程中不存在任何 external_* 工具（F4.1 修复）。
    #     外部 server 不可达不得阻塞启动，故整段降级为 warning。
    try:
        from app.core.agent import mcp_config
        from app.core.agent.external_tools import register_external_tools

        env_count = await register_external_tools()
        db_sync = await mcp_config.sync_from_db()
        logger.info("app.external_mcp_ready", env_registered=env_count, **db_sync)
    except Exception as e:  # noqa: BLE001
        logger.warning("app.external_mcp_failed", error=str(e))

    logger.info("app.started",
                agents=33,
                version=settings.app_version)

    yield

    # 关闭（优雅关机：按序 + 超时保护）
    shutdown_started = asyncio.get_event_loop().time()
    logger.info("app.stopping", reason="shutdown")

    # 0. 停止保留策略后台任务
    retention_task.cancel()
    try:
        await retention_task
    except asyncio.CancelledError:
        pass

    # 0.3 停止审计链校验调度（G3.3）
    try:
        audit_verify_task.cancel()
        await audit_verify_task
    except asyncio.CancelledError:
        pass

    # 0.5 停止操作日志刷盘并最终清空缓冲（G2.2：优雅退出不丢日志）
    try:
        from app.core.observability.operation_log import operation_log_buffer

        await operation_log_buffer.stop()
    except Exception as e:
        logger.warning("app.operation_log_stop_failed", error=str(e))

    # 1. 停止编排器（先停止 Agent，不再接受新任务）
    try:
        await asyncio.wait_for(orchestrator.stop_all(), timeout=15.0)
        logger.info("app.orchestrator_stopped")
    except asyncio.TimeoutError:
        logger.error("app.orchestrator_stop_timeout")
    except Exception as e:
        logger.warning("app.orchestrator_stop_failed", error=str(e))

    # 2. 排空并停止事件总线（等待在途消息处理完成）
    try:
        await asyncio.wait_for(event_bus.drain(), timeout=10.0)
        await event_bus.stop()
        logger.info("app.event_bus_stopped")
    except asyncio.TimeoutError:
        logger.error("app.event_bus_drain_timeout")
        await event_bus.stop()
    except Exception as e:
        logger.warning("app.event_bus_stop_failed", error=str(e))

    # 3. 关闭持久化层连接
    try:
        from app.core.database import close_all
        await asyncio.wait_for(close_all(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.error("app.database_close_timeout")
    except Exception as e:
        logger.warning("app.database_close_failed", error=str(e))

    shutdown_seconds = round(asyncio.get_event_loop().time() - shutdown_started, 2)
    logger.info("app.stopped", shutdown_seconds=shutdown_seconds)


# 创建FastAPI应用
app = FastAPI(
    title="Agent-Native ERP System",
    description="智能体原生ERP系统 - 以自主AI Agent为基本业务单元",
    version="1.0.0",
    lifespan=lifespan,
)

# 全局异常处理器：捕获未处理异常并记录完整堆栈
from fastapi import Request
from fastapi.responses import JSONResponse
import traceback as _tb

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """捕获所有未处理异常，任何模式下都不向客户端泄露内部信息（评估报告 D-06）"""
    tb_str = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
    request_id = str(uuid4())
    logger.error(
        "app.unhandled_exception",
        request_id=request_id,
        path=request.url.path,
        method=request.method,
        error_type=type(exc).__name__,
        error=str(exc),
        traceback=tb_str,  # 完整堆栈只进服务端日志
    )
    # 统一返回通用错误信息 + request_id 引用，便于凭 request_id 到日志中定位堆栈；
    # 不再因 debug=True 把 traceback 原文放进响应体（生产误开 debug 即泄露内部路径与 SQL）
    return JSONResponse(
        status_code=500,
        content={"detail": "内部服务器错误，请联系管理员", "request_id": request_id},
    )

# CORS中间件 - 从环境变量读取允许的源，避免通配符+credentials安全问题
import os as _os
_cors_origins = _os.environ.get("ERP_CORS_ORIGINS", "http://localhost:5173,http://localhost:5174,http://localhost:5175,http://localhost:5176,http://localhost:5177,http://localhost:5178,http://localhost:3000").split(",")
# P1-4：生产环境剥离 localhost/127.0.0.1 开发源（显式告警留痕）
_cors_origins = sanitize_cors_origins(
    [o.strip() for o in _cors_origins if o.strip()], settings.debug)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# JWT认证中间件 + RBAC权限校验中间件 + 限流中间件 + 请求上下文中间件 + 租户隔离中间件
# Starlette中间件执行顺序：后添加的先执行（洋葱模型）
# 实际执行顺序：RequestContext → RateLimit → Auth → Tenant → Permission → CORS → 路由
# 说明：Tenant 需在 Auth 之后（读取 request.state.user），在 Permission 之前（配额校验）
app.add_middleware(PermissionMiddleware)
app.add_middleware(TenantMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)

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
app.include_router(conversation_router)
app.include_router(document_router)
app.include_router(messaging_router)
app.include_router(reimbursement_router)
app.include_router(agent_ops_router)
app.include_router(customizations_router)
app.include_router(workflow_definitions_router)
app.include_router(mcp_servers_router)
app.include_router(operation_logs_router)
app.include_router(operation_rollback_router)
app.include_router(mcp_exposure_router)
app.include_router(skills_admin_router)
app.include_router(tenant_router)

# Prometheus指标
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Prometheus FastAPI Instrumentator — 自动采集 HTTP 请求指标
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_respect_env_var=False,
        excluded_handlers=["/metrics", "/health", "/health/live", "/health/ready"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    logger.info("app.prometheus_instrumentator_enabled")
except ImportError:
    logger.warning("app.prometheus_instrumentator_unavailable")

# 分布式追踪（OpenTelemetry）
from app.core.tracing import init_tracing as _init_tracing
_init_tracing(app)


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
    # 检查事件总线健康状态
    bus_health = {"status": "unknown"}
    try:
        if hasattr(event_bus, 'health_check'):
            bus_health = await event_bus.health_check()
        else:
            bus_health = {"status": "healthy", "implementation": "memory"}
    except Exception as e:
        bus_health = {"status": "unhealthy", "error": str(e)}

    # 综合健康状态
    overall = "healthy" if bus_health.get("status") in ("healthy", "degraded") else "unhealthy"

    return {
        "status": overall,
        "service": "erp-backend",
        "version": settings.app_version,
        "event_bus": bus_health,
        "timestamp": asyncio.get_event_loop().time(),
    }


@app.get("/health/live")
async def health_live():
    """Liveness 探针 - 仅检查进程存活（轻量级，不检查依赖）"""
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready():
    """
    Readiness 探针 - 检查服务是否准备好处理请求。

    检查项：
    - 事件总线状态
    - 数据库连通性（如果配置了）
    - Redis 连通性（如果配置了）
    """
    checks: dict[str, Any] = {}
    all_ready = True

    # 1. 事件总线
    try:
        if hasattr(event_bus, 'health_check'):
            bus_hc = await event_bus.health_check()
            checks["event_bus"] = bus_hc.get("status", "unknown")
            if bus_hc.get("status") == "unhealthy":
                all_ready = False
        else:
            checks["event_bus"] = "healthy"
    except Exception as e:
        checks["event_bus"] = f"error: {e}"
        all_ready = False

    # 2. 数据库（MySQL）
    try:
        from app.core.database import check_db_connection
        db_ok = await check_db_connection()
        checks["database"] = "ready" if db_ok else "unavailable"
        if not db_ok:
            all_ready = False
    except ImportError:
        checks["database"] = "not_configured"
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
        all_ready = False

    # 3. Redis
    try:
        from app.core.database import check_redis_connection
        redis_ok = await check_redis_connection()
        checks["redis"] = "ready" if redis_ok else "unavailable"
        if not redis_ok:
            all_ready = False
    except ImportError:
        checks["redis"] = "not_configured"
    except Exception as e:
        checks["redis"] = f"error: {type(e).__name__}"
        all_ready = False

    return {
        "status": "ready" if all_ready else "not_ready",
        "checks": checks,
        "timestamp": asyncio.get_event_loop().time(),
    }


# ===========================================================================
# WebSocket 端点 — 内部通讯实时推送
# ===========================================================================

import json as _json
from datetime import datetime as _datetime, timezone as _timezone
from fastapi import WebSocket as _WebSocket, WebSocketDisconnect as _WebSocketDisconnect
from app.api.middleware.ws_manager import ws_connection_manager as _ws_manager
from app.core.auth import jwt_manager as _jwt_manager


@app.websocket("/api/v1/ws/chat")
async def chat_websocket(websocket: _WebSocket):
    """聊天 WebSocket 端点 — 基于 token 认证。

    客户端通过 query param ``?token=<JWT>`` 传递认证令牌。
    连接建立后，客户端可发送以下消息类型：
    - ``ping``:               心跳，服务端回复 ``pong``
    - ``join_channel``:       加入频道（参数 ``channel_id``）
    - ``leave_channel``:      离开频道（参数 ``channel_id``）

    服务端通过 ``ws_connection_manager`` 推送消息、频道更新等事件。
    """
    # 从 query params 获取 token
    token = websocket.query_params.get("token", "")
    user_id = None
    try:
        # 复用现有 JWT 验证逻辑（兼容 PyJWT 与手动实现降级）
        payload = _jwt_manager.verify_token(token)
        if payload is not None:
            user_id = payload.get("user_id", "")
    except Exception:
        user_id = None

    if not user_id:
        await websocket.close(code=4001, reason="认证失败")
        return

    await _ws_manager.connect(websocket, user_id)
    try:
        # 发送连接成功消息
        await websocket.send_json({
            "type": "connected",
            "user_id": user_id,
            "timestamp": _datetime.now(_timezone.utc).isoformat(),
        })
        # 接收消息循环
        while True:
            data = await websocket.receive_text()
            # 处理客户端消息（心跳、加入频道等）
            try:
                msg = _json.loads(data)
                msg_type = msg.get("type", "")
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg_type == "join_channel":
                    channel_id = msg.get("channel_id", "")
                    if channel_id:
                        _ws_manager.join_channel(channel_id, user_id)
                        await websocket.send_json({"type": "joined", "channel_id": channel_id})
                elif msg_type == "leave_channel":
                    channel_id = msg.get("channel_id", "")
                    if channel_id:
                        _ws_manager.leave_channel(channel_id, user_id)
                        await websocket.send_json({"type": "left", "channel_id": channel_id})
            except _json.JSONDecodeError:
                pass
    except _WebSocketDisconnect:
        pass
    finally:
        await _ws_manager.disconnect(websocket, user_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level="info" if settings.debug else "warning",
    )
