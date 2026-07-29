# -*- coding: utf-8 -*-
"""
物流Agent测试
验证发货管理、路线优化、交付确认、高成本人工审批、LLM降级与查询接口
"""
import pytest
from datetime import datetime, timezone, timedelta

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.logistics.agent import LogisticsAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> LogisticsAgent:
    """创建并初始化物流Agent（不启动主循环，仅注册身份和订阅）"""
    agent = LogisticsAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_production_order_event(
    order_id: str = "PRD-001",
    product_id: str = "prod-001",
    product_name: str = "Widget",
    quantity: int = 100,
    assigned_line: str = "line-001",
) -> Event:
    """创建生产订单创建事件"""
    return Event(
        event_type=EventType.PRODUCTION_ORDER_CREATED,
        aggregate_id=order_id,
        aggregate_type="production_order",
        event_data={
            "order_id": order_id,
            "product_id": product_id,
            "product_name": product_name,
            "quantity": quantity,
            "assigned_line": assigned_line,
        },
    )


def _make_po_approved_event(
    po_id: str = "PO-001",
    supplier_id: str = "sup-001",
    product_id: str = "mat-001",
    product_name: str = "原材料A",
    quantity: int = 500,
) -> Event:
    """创建采购订单审批事件"""
    return Event(
        event_type=EventType.PURCHASE_ORDER_APPROVED,
        aggregate_id=po_id,
        aggregate_type="purchase_order",
        event_data={
            "po_id": po_id,
            "supplier_id": supplier_id,
            "product_id": product_id,
            "product_name": product_name,
            "quantity": quantity,
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "物流Agent"
    assert agent.domain == "logistics"
    assert agent.security_clearance == "internal"
    assert "shipment_tracking" in agent.capabilities
    assert "route_optimization" in agent.capabilities
    assert "delivery_management" in agent.capabilities
    assert "cost_optimization" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_outbound_shipment_creation ==========

async def test_outbound_shipment_creation():
    """生产订单创建事件触发成品发货"""
    agent = await _setup_agent()

    event = _make_production_order_event(
        product_id="prod-001", product_name="Widget", quantity=100
    )
    await agent.process_event(event)

    shipments = agent.get_shipments()
    assert len(shipments) == 1

    shipment = shipments[0]
    assert shipment["shipment_type"] == "outbound"
    assert shipment["product_id"] == "prod-001"
    assert shipment["quantity"] == 100
    assert shipment["status"] == "dispatched"
    assert shipment["transport_cost"] > 0
    assert shipment["destination"] != ""
    assert "route_id" in shipment

    # 验证决策历史
    assert len(agent._decision_history) == 1
    assert agent._decision_history[0].decision_type == "shipment_dispatch"


# ========== test_inbound_shipment_creation ==========

async def test_inbound_shipment_creation():
    """采购订单审批事件触发采购入库物流"""
    agent = await _setup_agent()

    event = _make_po_approved_event(
        po_id="PO-001", supplier_id="sup-001", quantity=500
    )
    await agent.process_event(event)

    shipments = agent.get_shipments()
    assert len(shipments) == 1

    shipment = shipments[0]
    assert shipment["shipment_type"] == "inbound"
    assert shipment["quantity"] == 500


# ========== test_route_optimization_rules ==========

async def test_route_optimization_rules():
    """规则模式路线优化（LLM默认关闭）"""
    agent = await _setup_agent()

    route = agent._optimize_route_rules("prod-001", 100, "line-001")

    assert route["method"] == "rules"
    assert route["destination"] != ""
    assert route["transport_cost"] > 0
    assert route["estimated_days"] > 0
    assert "route_id" in route
    assert "reasoning" in route

    # 路线应记录在_routes中
    routes = agent.get_routes()
    assert len(routes) >= 1


# ========== test_low_cost_auto_approved ==========

async def test_low_cost_auto_approved():
    """低运费发货自动批准"""
    agent = await _setup_agent()

    # 默认距离100公里 * 2元/公里 = 200元 < 10000元
    event = _make_production_order_event(quantity=100)
    await agent.process_event(event)

    shipments = agent.get_shipments()
    assert len(shipments) == 1
    shipment = shipments[0]

    assert shipment["transport_cost"] < LogisticsAgent.HIGH_COST_THRESHOLD
    assert shipment["requires_human_approval"] is False


# ========== test_make_decision ==========

async def test_make_decision():
    """Agent物流决策功能"""
    agent = await _setup_agent()

    event = _make_production_order_event(quantity=100)
    await agent.process_event(event)

    shipments = agent.get_shipments()
    shipment_id = shipments[0]["shipment_id"]

    decision = await agent.make_decision({"shipment_id": shipment_id})
    assert decision.decision_type == "logistics_auto"
    assert decision.confidence > 0
    assert decision.requires_human_approval is False
    assert "自动批准" in decision.action


# ========== test_make_decision_not_found ==========

async def test_make_decision_not_found():
    """不存在的发货ID返回错误决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"shipment_id": "nonexistent"})
    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_confirm_delivery ==========

async def test_confirm_delivery():
    """确认交付流程"""
    agent = await _setup_agent()

    event = _make_production_order_event(quantity=100)
    await agent.process_event(event)

    shipments = agent.get_shipments()
    shipment_id = shipments[0]["shipment_id"]
    assert shipments[0]["status"] == "dispatched"

    # 确认交付
    result = await agent.confirm_delivery(shipment_id)
    assert result is True

    # 验证状态已更新
    shipments = agent.get_shipments()
    assert shipments[0]["status"] == "delivered"
    assert "delivered_at" in shipments[0]


# ========== test_confirm_delivery_not_found ==========

async def test_confirm_delivery_not_found():
    """确认不存在的发货返回False"""
    agent = await _setup_agent()

    result = await agent.confirm_delivery("nonexistent")
    assert result is False


# ========== test_get_shipments_filter ==========

async def test_get_shipments_filter():
    """发货记录按状态过滤"""
    agent = await _setup_agent()

    # 创建2个发货
    event1 = _make_production_order_event(
        order_id="PRD-001", product_name="WidgetA", quantity=100
    )
    await agent.process_event(event1)

    event2 = _make_production_order_event(
        order_id="PRD-002", product_name="WidgetB", quantity=200
    )
    await agent.process_event(event2)

    all_shipments = agent.get_shipments()
    assert len(all_shipments) == 2

    dispatched = agent.get_shipments(status="dispatched")
    assert len(dispatched) == 2

    # 确认一个交付
    shipment_id = all_shipments[0]["shipment_id"]
    await agent.confirm_delivery(shipment_id)

    delivered = agent.get_shipments(status="delivered")
    assert len(delivered) == 1

    dispatched = agent.get_shipments(status="dispatched")
    assert len(dispatched) == 1


# ========== test_llm_fallback ==========

async def test_llm_fallback():
    """LLM不可用时降级到规则模式"""
    from app.core.config import settings

    assert settings.llm_enabled is False

    agent = await _setup_agent()

    # LLM路线优化应返回None
    result = await agent._optimize_route_llm("prod-001", "Widget", 100, "line-001")
    assert result is None

    # 规则路线优化应正常工作
    route = agent._optimize_route_rules("prod-001", 100, "line-001")
    assert route["method"] == "rules"
    assert route["destination"] != ""
    assert route["transport_cost"] > 0


# ========== test_on_tick ==========

async def test_on_tick():
    """周期性任务：检查在途发货是否超期"""
    agent = await _setup_agent()

    # 创建一个发货
    event = _make_production_order_event(quantity=100)
    await agent.process_event(event)

    # 手动将预期交付时间设为过去，模拟超期
    shipments = agent.get_shipments()
    shipment_id = shipments[0]["shipment_id"]
    past_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    agent._shipments[shipment_id]["expected_delivery"] = past_time

    # 执行on_tick
    await agent.on_tick()

    # 验证状态已更新为异常
    shipments = agent.get_shipments()
    assert shipments[0]["status"] == "exception"
