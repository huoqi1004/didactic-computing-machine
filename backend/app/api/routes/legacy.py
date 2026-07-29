"""
API路由 - 意图感知网关
所有API端点经过意图授权中间件，实现动态数据脱敏
"""
import asyncio
import structlog
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from datetime import datetime, timezone

from app.core.agent.orchestrator import orchestrator
from app.core.event.bus import event_bus
from app.core.event.store import event_store
from app.core.event.saga import saga_coordinator
from app.core.security.did import did_registry
from app.core.security.authorization import authz_engine
from app.core.security.audit import audit_chain
from app.core.security.guardrails import guardrails
from app.core.agent.tools import tool_registry
from app.core.knowledge import (
    knowledge_graph,
    vector_store,
    rule_engine,
    Document,
)
from app.core.config import settings
from app.core.versioning import (
    version_registry,
    canary_manager,
    digital_twin,
    snapshot_manager,
    CanaryConfig,
    VersionStatus,
)

logger = structlog.get_logger()

router = APIRouter()


# ========== 系统监控 ==========

@router.get("/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": settings.app_version,
        "domain": settings.current_domain.value,
    }


@router.get("/system/stats")
async def system_stats():
    """系统全局统计"""
    return {
        "event_bus": event_bus.get_stats(),
        "event_store": event_store.get_stats(),
        "saga_coordinator": saga_coordinator.get_stats(),
        "did_registry": did_registry.get_stats(),
        "authorization": authz_engine.get_stats(),
        "audit_chain": audit_chain.get_stats(),
        "guardrails": guardrails.get_stats(),
        "orchestrator": orchestrator.get_stats(),
        "tool_registry": tool_registry.get_stats(),
    }


# ========== Agent管理 ==========

@router.get("/agents")
async def list_agents():
    """列出所有Agent状态"""
    return {"agents": orchestrator.list_agents()}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    """获取Agent详情"""
    agent = orchestrator.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.get_status()


@router.post("/agents/{agent_id}/pause")
async def pause_agent(agent_id: str, reason: str = "api_request"):
    """暂停Agent"""
    agent = orchestrator.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    await agent.pause(reason)
    return {"status": "paused", "agent_id": agent_id}


@router.post("/agents/{agent_id}/resume")
async def resume_agent(agent_id: str):
    """恢复Agent"""
    agent = orchestrator.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    await agent.resume()
    return {"status": "running", "agent_id": agent_id}


@router.post("/agents/{agent_id}/takeover")
async def human_takeover(agent_id: str):
    """人类接管Agent（人在回路安全阀）"""
    agent = orchestrator.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    await agent.human_takeover()
    return {"status": "human_takeover", "agent_id": agent_id}


@router.post("/agents/{agent_id}/release")
async def human_release(agent_id: str):
    """释放人类接管"""
    agent = orchestrator.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    await agent.human_release()
    return {"status": "released", "agent_id": agent_id}


# ========== 库存管理 ==========

class InventoryAdjustmentRequest(BaseModel):
    """库存调整请求"""
    product_id: str
    product_name: str
    quantity: int
    safety_stock: int = 50
    reorder_point: int = 100
    unit_cost: float = 0.0
    warehouse: str = "default"


@router.get("/inventory")
async def list_inventory():
    """获取库存列表"""
    agent = orchestrator.get_agent_by_name("库存守护Agent")
    if not agent:
        return {"inventory": []}
    return {"inventory": agent.get_inventory_status()}


@router.post("/inventory/adjust")
async def adjust_inventory(req: InventoryAdjustmentRequest):
    """调整库存（触发事件）"""
    from app.core.event.models import Event, EventType

    event = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id=req.product_id,
        aggregate_type="inventory",
        event_data={
            "product_id": req.product_id,
            "product_name": req.product_name,
            "quantity": req.quantity,
            "safety_stock": req.safety_stock,
            "reorder_point": req.reorder_point,
            "unit_cost": req.unit_cost,
            "warehouse": req.warehouse,
        },
    )
    await event_store.append(event)
    await event_bus.publish(event)

    return {
        "status": "published",
        "event_id": str(event.event_id),
        "product_id": req.product_id,
    }


@router.get("/inventory/proposals")
async def get_replenishment_proposals():
    """获取补货建议"""
    agent = orchestrator.get_agent_by_name("库存守护Agent")
    if not agent:
        return {"proposals": []}
    return {"proposals": agent.get_proposals()}


# ========== 会计凭证 ==========

@router.get("/accounting/vouchers")
async def list_vouchers(status: Optional[str] = None, limit: int = 50):
    """获取凭证列表"""
    agent = orchestrator.get_agent_by_name("记账Agent")
    if not agent:
        return {"vouchers": []}
    return {"vouchers": agent.get_vouchers(status, limit)}


@router.get("/accounting/vouchers/pending")
async def get_pending_vouchers():
    """获取待审批凭证"""
    agent = orchestrator.get_agent_by_name("记账Agent")
    if not agent:
        return {"vouchers": []}
    return {"vouchers": agent.get_pending_vouchers()}


class VoucherApprovalRequest(BaseModel):
    voucher_id: str
    approved: bool


@router.post("/accounting/vouchers/approve")
async def approve_voucher(req: VoucherApprovalRequest):
    """审批凭证"""
    agent = orchestrator.get_agent_by_name("记账Agent")
    if not agent:
        raise HTTPException(status_code=404, detail="BookkeepingAgent not found")
    result = await agent.approve_voucher(req.voucher_id, req.approved)
    if not result:
        raise HTTPException(status_code=404, detail="Voucher not found")
    return {"status": "approved" if req.approved else "rejected", "voucher_id": req.voucher_id}


@router.get("/accounting/settlement")
async def daily_settlement():
    """日清日结汇总"""
    agent = orchestrator.get_agent_by_name("记账Agent")
    if not agent:
        raise HTTPException(status_code=404, detail="BookkeepingAgent not found")
    return await agent.daily_settlement()


# ========== 采购管理 ==========

@router.get("/procurement/orders")
async def list_purchase_orders(status: Optional[str] = None):
    """获取采购订单列表"""
    agent = orchestrator.get_agent_by_name("采购Agent")
    if not agent:
        return {"orders": []}
    return {"orders": agent.get_purchase_orders(status)}


@router.get("/procurement/orders/{po_id}")
async def get_purchase_order(po_id: str):
    """获取采购订单详情"""
    agent = orchestrator.get_agent_by_name("采购Agent")
    if not agent:
        raise HTTPException(status_code=404, detail="ProcurementAgent not found")
    orders = agent.get_purchase_orders()
    for order in orders:
        if order["po_id"] == po_id:
            return order
    raise HTTPException(status_code=404, detail="Purchase order not found")


class PurchaseOrderApprovalRequest(BaseModel):
    approved: bool


@router.post("/procurement/orders/{po_id}/approve")
async def approve_purchase_order(po_id: str, req: PurchaseOrderApprovalRequest):
    """审批采购订单"""
    agent = orchestrator.get_agent_by_name("采购Agent")
    if not agent:
        raise HTTPException(status_code=404, detail="ProcurementAgent not found")
    result = await agent.approve_purchase_order(po_id, req.approved)
    if not result:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return {
        "status": "approved" if req.approved else "rejected",
        "po_id": po_id,
    }


@router.get("/procurement/suppliers")
async def list_suppliers():
    """获取供应商列表"""
    agent = orchestrator.get_agent_by_name("采购Agent")
    if not agent:
        return {"suppliers": []}
    return {"suppliers": agent.get_suppliers()}


# ========== 资金管理 ==========

@router.get("/treasury/forecast")
async def treasury_forecast(days: int = 7):
    """获取现金流预测"""
    agent = orchestrator.get_agent_by_name("资金Agent")
    if not agent:
        return {"forecast": {}}
    return {"forecast": await agent.get_cash_flow_forecast(days)}


@router.get("/treasury/payments")
async def treasury_payments(status: Optional[str] = None):
    """获取支付排程"""
    agent = orchestrator.get_agent_by_name("资金Agent")
    if not agent:
        return {"payments": []}
    return {"payments": agent.get_payment_schedule(status)}


@router.get("/treasury/transfers")
async def treasury_transfers(status: Optional[str] = None):
    """获取资金调拨记录"""
    agent = orchestrator.get_agent_by_name("资金Agent")
    if not agent:
        return {"transfers": []}
    return {"transfers": agent.get_fund_transfers(status)}


class FundTransferApprovalRequest(BaseModel):
    approved: bool


@router.post("/treasury/transfers/{transfer_id}/approve")
async def approve_fund_transfer(transfer_id: str, req: FundTransferApprovalRequest):
    """审批资金调拨"""
    agent = orchestrator.get_agent_by_name("资金Agent")
    if not agent:
        raise HTTPException(status_code=404, detail="TreasuryAgent not found")
    result = await agent.approve_fund_transfer(transfer_id, req.approved)
    if not result:
        raise HTTPException(status_code=404, detail="Fund transfer not found")
    return {
        "status": "approved" if req.approved else "rejected",
        "transfer_id": transfer_id,
    }


# ========== 内审管理 ==========

@router.get("/audit/alerts")
async def audit_alerts(severity: Optional[str] = None):
    """获取内审警报列表"""
    agent = orchestrator.get_agent_by_name("内审Agent")
    if not agent:
        return {"alerts": []}
    return {"alerts": agent.get_alerts(severity)}


@router.get("/audit/frozen")
async def audit_frozen():
    """获取冻结的流程列表"""
    agent = orchestrator.get_agent_by_name("内审Agent")
    if not agent:
        return {"frozen": []}
    return {"frozen": agent.get_frozen_processes()}


@router.post("/audit/frozen/{process_id}/unfreeze")
async def unfreeze_process(process_id: str):
    """解冻流程"""
    agent = orchestrator.get_agent_by_name("内审Agent")
    if not agent:
        raise HTTPException(status_code=404, detail="InternalAuditAgent not found")
    result = await agent.unfreeze_process(process_id)
    if not result:
        raise HTTPException(status_code=404, detail="Frozen process not found")
    return {"status": "unfrozen", "process_id": process_id}


@router.get("/audit/patterns")
async def audit_patterns():
    """获取已识别的交易模式"""
    agent = orchestrator.get_agent_by_name("内审Agent")
    if not agent:
        return {"patterns": []}
    return {"patterns": agent.get_transaction_patterns()}


# ========== 税务管理 ==========

@router.get("/tax/records")
async def tax_records(tax_type: Optional[str] = None):
    """获取税务记录列表"""
    agent = orchestrator.get_agent_by_name("税务Agent")
    if not agent:
        return {"records": []}
    return {"records": agent.get_tax_records(tax_type)}


@router.get("/tax/declarations")
async def tax_declarations(status: Optional[str] = None):
    """获取税务申报列表"""
    agent = orchestrator.get_agent_by_name("税务Agent")
    if not agent:
        return {"declarations": []}
    return {"declarations": agent.get_tax_declarations(status)}


@router.get("/tax/rates")
async def tax_rates():
    """获取税率表"""
    agent = orchestrator.get_agent_by_name("税务Agent")
    if not agent:
        return {"rates": {}}
    return {"rates": agent.get_tax_rates()}


# ========== 合规管理 ==========

@router.get("/compliance/regulations")
async def compliance_regulations(category: Optional[str] = None):
    """获取法规库列表"""
    agent = orchestrator.get_agent_by_name("合规Agent")
    if not agent:
        return {"regulations": []}
    return {"regulations": agent.get_regulations(category)}


@router.get("/compliance/control-points")
async def compliance_control_points(process: Optional[str] = None):
    """获取控制点列表"""
    agent = orchestrator.get_agent_by_name("合规Agent")
    if not agent:
        return {"control_points": []}
    return {"control_points": agent.get_control_points(process)}


class ComplianceCheckRequest(BaseModel):
    process: str = "general"
    amount: float = 0.0
    requires_human_approval: bool = False


@router.post("/compliance/check")
async def compliance_check(req: ComplianceCheckRequest):
    """执行合规检查"""
    agent = orchestrator.get_agent_by_name("合规Agent")
    if not agent:
        raise HTTPException(status_code=404, detail="ComplianceAgent not found")
    result = agent.check_compliance(req.model_dump())
    return result


@router.get("/compliance/reports")
async def compliance_reports():
    """获取合规报告列表"""
    agent = orchestrator.get_agent_by_name("合规Agent")
    if not agent:
        return {"reports": []}
    return {"reports": agent.get_compliance_reports()}


# ========== 需求预测管理 ==========

@router.get("/demand/forecasts")
async def demand_forecasts(product_id: Optional[str] = None):
    """获取需求预测列表"""
    agent = orchestrator.get_agent_by_name("需求先知Agent")
    if not agent:
        return {"forecasts": []}
    return {"forecasts": agent.get_forecasts(product_id)}


@router.get("/demand/trends")
async def demand_trends(product_id: Optional[str] = None):
    """获取需求趋势列表"""
    agent = orchestrator.get_agent_by_name("需求先知Agent")
    if not agent:
        return {"trends": []}
    return {"trends": agent.get_trends(product_id)}


# ========== 供应规划管理 ==========

@router.get("/supply/evaluations")
async def supply_evaluations(supplier_id: Optional[str] = None):
    """获取供应商评估列表"""
    agent = orchestrator.get_agent_by_name("供应规划Agent")
    if not agent:
        return {"evaluations": []}
    return {"evaluations": agent.get_supplier_evaluations(supplier_id)}


@router.get("/supply/risks")
async def supply_risks(risk_type: Optional[str] = None):
    """获取供应风险列表"""
    agent = orchestrator.get_agent_by_name("供应规划Agent")
    if not agent:
        return {"risks": []}
    return {"risks": agent.get_supply_risks(risk_type)}


# ========== 生产管理 ==========

@router.get("/production/orders")
async def production_orders(status: Optional[str] = None):
    """获取生产订单列表"""
    agent = orchestrator.get_agent_by_name("生产Agent")
    if not agent:
        return {"orders": []}
    return {"orders": agent.get_production_orders(status)}


@router.get("/production/bom")
async def production_bom(product_id: Optional[str] = None):
    """获取BOM（物料清单）"""
    agent = orchestrator.get_agent_by_name("生产Agent")
    if not agent:
        return {"bom": {}}
    return {"bom": agent.get_bom(product_id)}


@router.get("/production/capacity")
async def production_capacity():
    """获取产能状态"""
    agent = orchestrator.get_agent_by_name("生产Agent")
    if not agent:
        return {"capacity": []}
    return {"capacity": agent.get_capacity()}


# ========== 物流管理 ==========

@router.get("/logistics/shipments")
async def logistics_shipments(status: Optional[str] = None):
    """获取发货记录列表"""
    agent = orchestrator.get_agent_by_name("物流Agent")
    if not agent:
        return {"shipments": []}
    return {"shipments": agent.get_shipments(status)}


@router.get("/logistics/routes")
async def logistics_routes():
    """获取路线记录列表"""
    agent = orchestrator.get_agent_by_name("物流Agent")
    if not agent:
        return {"routes": []}
    return {"routes": agent.get_routes()}


class DeliveryConfirmRequest(BaseModel):
    shipment_id: str


@router.post("/logistics/shipments/confirm")
async def confirm_delivery(req: DeliveryConfirmRequest):
    """确认交付"""
    agent = orchestrator.get_agent_by_name("物流Agent")
    if not agent:
        raise HTTPException(status_code=404, detail="LogisticsAgent not found")
    result = await agent.confirm_delivery(req.shipment_id)
    if not result:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return {"status": "delivered", "shipment_id": req.shipment_id}


# ========== 调度管理 ==========

@router.get("/scheduler/schedule")
async def scheduler_schedule(status: Optional[str] = None):
    """获取调度计划列表"""
    agent = orchestrator.get_agent_by_name("动态调度Agent")
    if not agent:
        return {"schedule": []}
    return {"schedule": agent.get_schedule(status)}


@router.get("/scheduler/priorities")
async def scheduler_priorities(decision_type: Optional[str] = None):
    """获取优先级记录列表"""
    agent = orchestrator.get_agent_by_name("动态调度Agent")
    if not agent:
        return {"priorities": []}
    return {"priorities": agent.get_priorities(decision_type)}


@router.get("/scheduler/resources")
async def scheduler_resources():
    """获取资源分配列表"""
    agent = orchestrator.get_agent_by_name("动态调度Agent")
    if not agent:
        return {"resources": []}
    return {"resources": agent.get_resource_allocation()}


# ========== 事件查询 ==========

@router.get("/events")
async def list_events(aggregate_id: Optional[str] = None,
                      event_type: Optional[str] = None,
                      limit: int = 100):
    """查询事件流"""
    from app.core.event.models import EventType as ET

    if aggregate_id:
        events = await event_store.get_events(aggregate_id)
    elif event_type:
        try:
            et = ET(event_type)
            events = await event_store.get_events_by_type(et, limit)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown event type: {event_type}")
    else:
        # 返回所有最新事件
        events = []
        for stream_id, stream_events in event_store._streams.items():
            events.extend(stream_events[-10:])
        events = events[-limit:]

    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
    }


# ========== 安全与审计 ==========

@router.get("/security/identities")
async def list_identities():
    """列出所有Agent身份"""
    return {"identities": did_registry.list_identities()}


@router.get("/security/audit")
async def query_audit(actor_did: Optional[str] = None,
                      action: Optional[str] = None,
                      risk_level: Optional[str] = None,
                      limit: int = 100):
    """查询审计日志"""
    entries = audit_chain.query(
        actor_did=actor_did,
        action=action,
        risk_level=risk_level,
        limit=limit,
    )
    return {"entries": entries, "count": len(entries)}


@router.get("/security/audit/verify")
async def verify_audit_integrity():
    """验证审计链完整性"""
    return {"integrity_valid": audit_chain.verify_integrity()}


@router.get("/security/stats")
async def security_stats():
    """安全统计"""
    return {
        "did_registry": did_registry.get_stats(),
        "authorization": authz_engine.get_stats(),
        "audit_chain": audit_chain.get_stats(),
        "guardrails": guardrails.get_stats(),
    }


# ========== 区块链审计 ==========

@router.get("/security/blockchain/stats")
async def blockchain_stats():
    """
    区块链审计统计。

    返回区块链后端实现、链高度、记录总数、风险分布等信息。
    若区块链客户端未初始化，返回 ``available=False`` 与降级原因。
    """
    if audit_chain._blockchain_client is None:
        return {
            "available": False,
            "reason": "blockchain_client_not_initialized",
            "blockchain_enabled": settings.blockchain_enabled,
            "blockchain_initialized": audit_chain._blockchain_initialized,
        }

    try:
        client = audit_chain._blockchain_client
        stats = await client.get_chain_stats()
        # 合并审计链本地的统计字段
        stats["audit_chain_stats"] = audit_chain.get_stats()
        stats["available"] = True
        return stats
    except Exception as e:
        logger.warning(
            "api.blockchain_stats_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        return {
            "available": False,
            "reason": "stats_query_failed",
            "error": str(e),
            "blockchain_enabled": settings.blockchain_enabled,
        }


@router.get("/security/blockchain/verify/{entry_id}")
async def verify_on_blockchain(entry_id: str):
    """
    在区块链上验证单条审计记录的完整性。

    路径参数：
    - ``entry_id``：审计条目ID（UUID）

    返回 ``{"verified": true/false, "entry_id": "..."}``。
    若区块链不可用或记录不存在，返回 ``verified=false``。
    """
    verified = await audit_chain.verify_on_blockchain(entry_id)
    return {
        "entry_id": entry_id,
        "verified": verified,
        "blockchain_available": audit_chain._blockchain_client is not None,
    }


@router.get("/security/blockchain/query")
async def query_from_blockchain(
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    actor_did: Optional[str] = None,
    action: Optional[str] = None,
    risk_level: Optional[str] = None,
    limit: int = 100,
):
    """
    从区块链查询审计记录。

    查询参数：
    - ``start_time`` / ``end_time``：ISO8601 时间范围
    - ``actor_did``：按操作者DID过滤
    - ``action``：按操作类型过滤
    - ``risk_level``：按风险等级过滤
    - ``limit``：返回条数上限（默认 100）
    """
    filters = {
        "start_time": start_time,
        "end_time": end_time,
        "actor_did": actor_did,
        "action": action,
        "risk_level": risk_level,
        "limit": limit,
    }
    entries = await audit_chain.query_from_blockchain(filters)
    return {
        "entries": entries,
        "count": len(entries),
        "blockchain_available": audit_chain._blockchain_client is not None,
    }


# ========== 工具管理 ==========

@router.get("/tools")
async def list_tools():
    """列出所有注册工具"""
    return {"tools": tool_registry.list_tools()}


# ========== 知识与认知层 ==========

@router.get("/knowledge/graph")
async def query_knowledge_graph(
    node_type: Optional[str] = None,
    q: Optional[str] = None,
):
    """查询知识图谱

    支持两种查询方式：
    - 简单查询语法：?q=node_type:product[category=电子元件]
    - 类型过滤：?node_type=product
    """
    if q:
        nodes = knowledge_graph.query(q)
    else:
        nodes = [n.to_dict() for n in knowledge_graph.find_nodes(node_type=node_type)]
    return {
        "nodes": nodes,
        "count": len(nodes),
        "stats": knowledge_graph.get_stats(),
    }


class KnowledgeNodeRequest(BaseModel):
    """添加知识图谱节点请求"""
    node_type: str
    properties: dict = {}


class KnowledgeEdgeRequest(BaseModel):
    """添加知识图谱边请求"""
    source_id: str
    target_id: str
    edge_type: str
    properties: dict = {}


@router.post("/knowledge/graph/nodes")
async def add_knowledge_node(req: KnowledgeNodeRequest):
    """添加知识图谱节点"""
    try:
        node_id = knowledge_graph.add_node(req.node_type, req.properties)
        return {"node_id": node_id, "node_type": req.node_type}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/knowledge/graph/edges")
async def add_knowledge_edge(req: KnowledgeEdgeRequest):
    """添加知识图谱边（关系）"""
    try:
        edge_id = knowledge_graph.add_edge(
            req.source_id, req.target_id, req.edge_type, req.properties
        )
        return {"edge_id": edge_id, "edge_type": req.edge_type}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/knowledge/graph/nodes/{node_id}")
async def get_knowledge_node(node_id: str):
    """获取知识图谱节点详情（含邻居）"""
    node = knowledge_graph.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    neighbors = knowledge_graph.get_neighbors(node_id)
    return {
        "node": node.to_dict(),
        "neighbors": [
            {"edge": e.to_dict(), "node": n.to_dict()}
            for e, n in neighbors
        ],
    }


@router.get("/knowledge/documents")
async def search_documents(
    q: str,
    top_k: int = 5,
    tag: Optional[str] = None,
    value: Optional[str] = None,
):
    """搜索文档（RAG 检索）

    支持标签过滤：?tag=permission_level&value=internal
    """
    filter_tags = None
    if tag and value:
        filter_tags = {tag: value}
    results = await vector_store.search(q, top_k=top_k, filter_tags=filter_tags)
    return {
        "query": q,
        "results": [
            {
                "doc": doc.to_dict(),
                "score": score,
            }
            for doc, score in results
        ],
        "count": len(results),
        "stats": vector_store.get_stats(),
    }


class AddDocumentRequest(BaseModel):
    """添加文档请求"""
    content: str
    metadata: dict = {}


@router.post("/knowledge/documents")
async def add_document(req: AddDocumentRequest):
    """添加文档到向量库"""
    doc = Document(content=req.content, metadata=req.metadata)
    doc_ids = await vector_store.add_documents([doc])
    return {"doc_id": doc_ids[0] if doc_ids else None}


@router.get("/knowledge/rules")
async def list_rules(only_enabled: bool = False):
    """列出所有规则"""
    rules = rule_engine.list_rules(only_enabled=only_enabled)
    return {
        "rules": [r.to_dict() for r in rules],
        "count": len(rules),
        "stats": rule_engine.get_stats(),
    }


class EvaluateRulesRequest(BaseModel):
    """评估规则请求"""
    context: dict


@router.post("/knowledge/rules/evaluate")
async def evaluate_rules(req: EvaluateRulesRequest):
    """评估规则，返回所有匹配的规则及其执行结果"""
    matched = await rule_engine.evaluate(req.context)
    executed = await rule_engine.execute(req.context)
    return {
        "matched_rules": [r.to_dict() for r in matched],
        "matched_count": len(matched),
        "execution_results": executed,
    }


# ========== 版本化与灰度发布 ==========


class RegisterVersionRequest(BaseModel):
    """注册新版本请求"""
    version: str
    config: dict = {}
    rules: list[dict] = []
    changelog: str = ""
    parent_version: Optional[str] = None
    knowledge_snapshot_id: Optional[str] = None
    created_by: str = "api"


class StartCanaryRequest(BaseModel):
    """启动灰度请求"""
    traffic_percentage: int = 10
    evaluation_metrics: dict = {}
    success_threshold: float = 0.95
    error_rate_threshold: float = 0.05
    duration_seconds: int = 3600


class CreateTwinRequest(BaseModel):
    """创建数字孪生请求"""
    agent_name: str
    version_id: str


class SimulateRequest(BaseModel):
    """运行仿真请求"""
    test_scenarios: Optional[list[dict]] = None


@router.get("/versioning/agents/{agent_name}/versions")
async def list_agent_versions(agent_name: str):
    """列出Agent所有版本"""
    versions = version_registry.get_versions(agent_name)
    active = version_registry.get_active_version(agent_name)
    return {
        "agent_name": agent_name,
        "versions": [v.to_dict() for v in versions],
        "count": len(versions),
        "active_version_id": active.version_id if active else None,
    }


@router.post("/versioning/agents/{agent_name}/versions")
async def register_agent_version(agent_name: str, req: RegisterVersionRequest):
    """注册新版本"""
    version = version_registry.register_version(
        agent_name=agent_name,
        version=req.version,
        config=req.config,
        rules=req.rules,
        changelog=req.changelog,
        parent_version=req.parent_version,
        knowledge_snapshot_id=req.knowledge_snapshot_id,
        created_by=req.created_by,
    )
    return version.to_dict()


@router.post("/versioning/versions/{version_id}/activate")
async def activate_version(version_id: str):
    """激活版本（一键切换）"""
    success = version_registry.activate_version(version_id)
    if not success:
        raise HTTPException(status_code=404, detail="Version not found or cannot be activated")
    version = version_registry.get_version(version_id)
    return {"status": "activated", "version_id": version_id, "version": version.version}


@router.post("/versioning/agents/{agent_name}/rollback")
async def rollback_version(agent_name: str, target_version_id: Optional[str] = None):
    """回滚版本（默认回滚到上一个ACTIVE）"""
    rolled_back = version_registry.rollback_version(agent_name, target_version_id)
    if rolled_back is None:
        raise HTTPException(status_code=404, detail="No version to rollback to")
    return {"status": "rolled_back", "version_id": rolled_back.version_id, "version": rolled_back.version}


@router.post("/versioning/versions/{version_id}/canary")
async def start_canary(version_id: str, req: StartCanaryRequest):
    """启动灰度发布"""
    config = CanaryConfig(
        version_id=version_id,
        traffic_percentage=req.traffic_percentage,
        evaluation_metrics=req.evaluation_metrics,
        success_threshold=req.success_threshold,
        error_rate_threshold=req.error_rate_threshold,
        duration_seconds=req.duration_seconds,
    )
    try:
        result = await canary_manager.start_canary(version_id, config)
        return result.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/versioning/versions/{version_id}/canary/status")
async def canary_status(version_id: str):
    """灰度状态"""
    status = canary_manager.get_canary_status(version_id)
    return status


@router.post("/versioning/versions/{version_id}/canary/promote")
async def promote_canary(version_id: str):
    """灰度全量切换"""
    success = await canary_manager.promote_canary(version_id)
    if not success:
        raise HTTPException(status_code=400, detail="Canary not running or promotion failed")
    return {"status": "promoted", "version_id": version_id}


@router.post("/versioning/versions/{version_id}/canary/abort")
async def abort_canary(version_id: str, reason: str = "api_request"):
    """中止灰度"""
    success = await canary_manager.abort_canary(version_id, reason)
    if not success:
        raise HTTPException(status_code=400, detail="Canary not running or abort failed")
    return {"status": "aborted", "version_id": version_id, "reason": reason}


@router.get("/versioning/versions/{version_id}/canary/evaluate")
async def evaluate_canary(version_id: str):
    """评估灰度结果"""
    result = await canary_manager.evaluate_canary(version_id)
    return result


@router.post("/versioning/digital-twin")
async def create_digital_twin(req: CreateTwinRequest):
    """创建数字孪生"""
    try:
        twin_id = await digital_twin.create_twin(req.agent_name, req.version_id)
        return {"twin_id": twin_id, "agent_name": req.agent_name, "version_id": req.version_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/versioning/digital-twin/{twin_id}/simulate")
async def run_simulation(twin_id: str, req: SimulateRequest):
    """运行仿真测试"""
    try:
        report = await digital_twin.run_simulation(twin_id, req.test_scenarios)
        return report
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/versioning/digital-twin/{twin_id}")
async def get_twin_status(twin_id: str):
    """获取数字孪生状态"""
    return digital_twin.get_twin_status(twin_id)


@router.post("/versioning/digital-twin/{twin_id}/compare")
async def compare_twin_with_production(twin_id: str):
    """与生产环境对比"""
    try:
        report = await digital_twin.compare_with_production(twin_id)
        return report
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/versioning/digital-twin/{twin_id}")
async def destroy_digital_twin(twin_id: str):
    """销毁数字孪生"""
    await digital_twin.destroy_twin(twin_id)
    return {"status": "destroyed", "twin_id": twin_id}


@router.get("/versioning/snapshots")
async def list_snapshots():
    """列出知识库快照"""
    return {"snapshots": snapshot_manager.list_snapshots()}


@router.post("/versioning/snapshots")
async def create_snapshot():
    """创建知识库快照"""
    snapshot = await snapshot_manager.create_snapshot()
    return snapshot.to_dict()


@router.get("/versioning/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    """获取快照详情"""
    snapshot = snapshot_manager.get_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snapshot.to_dict()


@router.post("/versioning/snapshots/{snapshot_id}/restore")
async def restore_snapshot(snapshot_id: str):
    """恢复知识库快照"""
    success = await snapshot_manager.restore_snapshot(snapshot_id)
    if not success:
        raise HTTPException(status_code=404, detail="Snapshot not found or restore failed")
    return {"status": "restored", "snapshot_id": snapshot_id}


@router.get("/versioning/stats")
async def versioning_stats():
    """版本化系统统计"""
    return {
        "version_registry": version_registry.get_stats(),
    }


# ========== WebSocket 实时通信 ==========

class ConnectionManager:
    """WebSocket连接管理器"""
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self._connections.append(websocket)
        logger.info("websocket.connected", total=len(self._connections))

    def disconnect(self, websocket: WebSocket):
        self._connections.remove(websocket)
        logger.info("websocket.disconnected", total=len(self._connections))

    async def broadcast(self, message: dict):
        """广播消息给所有连接"""
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket端点 - 实时事件推送"""
    await ws_manager.connect(websocket)

    # 订阅事件总线，将事件推送给前端
    from app.core.event.models import EventSubscription, EventType

    async def push_event(event):
        await ws_manager.broadcast({
            "type": "event",
            "event_type": event.event_type.value,
            "data": event.to_dict(),
            "timestamp": event.timestamp.isoformat(),
        })

    sub = EventSubscription(
        subscriber_id=f"ws:{id(websocket)}",
        event_types=list(EventType),  # 订阅所有事件
        handler=push_event,
    )
    event_bus.subscribe(sub)

    try:
        while True:
            data = await websocket.receive_text()
            # 处理前端发来的消息
            await ws_manager.broadcast({
                "type": "message",
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
        event_bus.unsubscribe(sub.subscriber_id)


# ========== 发票管理 ==========

class InvoiceIssueRequest(BaseModel):
    invoice_type: str = "vat_normal"
    buyer_name: str = ""
    buyer_tax_id: str = ""
    buyer_address: str = ""
    buyer_phone: str = ""
    buyer_bank: str = ""
    buyer_account: str = ""
    seller_name: str = ""
    amount: float = 0
    description: str = ""


@router.get("/invoice/list")
async def invoice_list(
    status: Optional[str] = None,
    invoice_type: Optional[str] = None,
    limit: int = 50,
):
    """获取发票列表"""
    agent = orchestrator.get_agent_by_name("发票Agent")
    if not agent:
        return {"invoices": []}
    return {"invoices": agent.get_invoices(status=status, invoice_type=invoice_type, limit=limit)}


@router.get("/invoice/stats")
async def invoice_stats():
    """发票统计"""
    agent = orchestrator.get_agent_by_name("发票Agent")
    if not agent:
        return {
            "total_invoices": 0, "issued_count": 0, "cancelled_count": 0,
            "total_amount": 0, "total_tax": 0, "total_with_tax": 0, "by_type": {},
        }
    return agent.get_invoice_stats()


@router.post("/invoice/issue")
async def invoice_issue(req: InvoiceIssueRequest):
    """开具发票"""
    agent = orchestrator.get_agent_by_name("发票Agent")
    if not agent:
        raise HTTPException(status_code=503, detail="发票Agent未启动")
    result = await agent.issue_invoice(req.model_dump())
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/invoice/{invoice_id}/verify")
async def invoice_verify(invoice_id: str):
    """验证发票"""
    agent = orchestrator.get_agent_by_name("发票Agent")
    if not agent:
        raise HTTPException(status_code=503, detail="发票Agent未启动")
    result = await agent.verify_invoice(invoice_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/invoice/{invoice_id}/cancel")
async def invoice_cancel(invoice_id: str, reason: str = ""):
    """红冲发票"""
    agent = orchestrator.get_agent_by_name("发票Agent")
    if not agent:
        raise HTTPException(status_code=503, detail="发票Agent未启动")
    result = await agent.cancel_invoice(invoice_id, reason)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ========== 报销管理 ==========

class ReimbursementSubmitRequest(BaseModel):
    employee_id: str = ""
    employee_name: str = ""
    department: str = ""
    category: str = "other"
    amount: float = 0
    description: str = ""
    has_receipt: bool = False
    receipt_amount: float = 0
    invoice_id: str = ""
    project_id: str = ""
    project_name: str = ""


class ReimbursementApproveRequest(BaseModel):
    reimbursement_id: str
    approved: bool
    remark: str = ""


@router.get("/reimbursement/list")
async def reimbursement_list(
    status: Optional[str] = None,
    employee_id: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 50,
):
    """获取报销列表"""
    agent = orchestrator.get_agent_by_name("报销Agent")
    if not agent:
        return {"reimbursements": []}
    return {
        "reimbursements": agent.get_reimbursements(
            status=status, employee_id=employee_id, project_id=project_id, limit=limit,
        )
    }


@router.get("/reimbursement/pending")
async def reimbursement_pending():
    """获取待处理报销"""
    agent = orchestrator.get_agent_by_name("报销Agent")
    if not agent:
        return {"reimbursements": []}
    return {"reimbursements": agent.get_pending_reimbursements()}


@router.get("/reimbursement/stats")
async def reimbursement_stats():
    """报销统计"""
    agent = orchestrator.get_agent_by_name("报销Agent")
    if not agent:
        return {
            "total_requests": 0, "pending_count": 0, "approved_count": 0,
            "paid_count": 0, "rejected_count": 0,
            "total_pending_amount": 0, "total_paid_amount": 0,
            "by_category": {}, "by_project": {},
        }
    return agent.get_reimbursement_stats()


@router.post("/reimbursement/submit")
async def reimbursement_submit(req: ReimbursementSubmitRequest):
    """提交报销申请"""
    agent = orchestrator.get_agent_by_name("报销Agent")
    if not agent:
        raise HTTPException(status_code=503, detail="报销Agent未启动")
    result = await agent.submit_reimbursement(req.model_dump())
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/reimbursement/{reimb_id}/verify")
async def reimbursement_verify(reimb_id: str):
    """核验报销申请"""
    agent = orchestrator.get_agent_by_name("报销Agent")
    if not agent:
        raise HTTPException(status_code=503, detail="报销Agent未启动")
    result = await agent.verify_reimbursement(reimb_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/reimbursement/approve")
async def reimbursement_approve(req: ReimbursementApproveRequest):
    """审批报销"""
    agent = orchestrator.get_agent_by_name("报销Agent")
    if not agent:
        raise HTTPException(status_code=503, detail="报销Agent未启动")
    result = await agent.approve_reimbursement(req.reimbursement_id, req.approved, req.remark)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/reimbursement/{reimb_id}/pay")
async def reimbursement_pay(reimb_id: str):
    """付款"""
    agent = orchestrator.get_agent_by_name("报销Agent")
    if not agent:
        raise HTTPException(status_code=503, detail="报销Agent未启动")
    result = await agent.pay_reimbursement(reimb_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/reimbursement/project/register")
async def reimbursement_register_project(
    project_id: str = "",
    name: str = "",
    budget: float = 0,
):
    """注册项目"""
    agent = orchestrator.get_agent_by_name("报销Agent")
    if not agent:
        raise HTTPException(status_code=503, detail="报销Agent未启动")
    return agent.register_project(project_id, name, budget)


@router.post("/reimbursement/employee/register")
async def reimbursement_register_employee(
    employee_id: str = "",
    name: str = "",
    department: str = "",
):
    """注册员工"""
    agent = orchestrator.get_agent_by_name("报销Agent")
    if not agent:
        raise HTTPException(status_code=503, detail="报销Agent未启动")
    return agent.register_employee(employee_id, name, department)


# ========== 报税增强 ==========

@router.get("/tax/dashboard")
async def tax_dashboard():
    """报税综合看板：整合税务记录、申报表、发票统计与报销可抵扣项"""
    tax_agent = orchestrator.get_agent_by_name("税务Agent")
    invoice_agent_inst = orchestrator.get_agent_by_name("发票Agent")
    reimb_agent = orchestrator.get_agent_by_name("报销Agent")

    tax_records = tax_agent.get_tax_records() if tax_agent else []
    declarations = tax_agent.get_tax_declarations() if tax_agent else []
    tax_rates = tax_agent.get_tax_rates() if tax_agent else {}
    invoice_stats = invoice_agent_inst.get_invoice_stats() if invoice_agent_inst else {}
    reimb_stats = reimb_agent.get_reimbursement_stats() if reimb_agent else {}

    # 计算可抵扣进项税（来自已验证发票）
    input_tax = 0
    if invoice_agent_inst:
        verified_invoices = [
            inv for inv in invoice_agent_inst.get_invoices(status="verified")
        ]
        input_tax = sum(inv.get("tax_amount", 0) for inv in verified_invoices)

    # 计算可抵扣报销费用
    deductible_expenses = 0
    if reimb_agent:
        paid_reimbs = [r for r in reimb_agent.get_reimbursements(status="paid")]
        deductible_expenses = sum(r.get("amount", 0) for r in paid_reimbs)

    # 计算应缴税额 = 销项税 - 进项税
    output_tax = invoice_stats.get("total_tax", 0) if invoice_stats else 0
    vat_payable = max(0, output_tax - input_tax)

    return {
        "tax_records": tax_records,
        "declarations": declarations,
        "tax_rates": tax_rates,
        "invoice_stats": invoice_stats,
        "reimbursement_stats": reimb_stats,
        "summary": {
            "output_tax": round(output_tax, 2),
            "input_tax": round(input_tax, 2),
            "vat_payable": round(vat_payable, 2),
            "deductible_expenses": round(deductible_expenses, 2),
            "total_invoices": invoice_stats.get("total_invoices", 0) if invoice_stats else 0,
            "pending_reimbursements": reimb_stats.get("pending_count", 0) if reimb_stats else 0,
        },
    }
