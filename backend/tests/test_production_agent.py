# -*- coding: utf-8 -*-
"""
生产Agent测试
验证生产订单创建、BOM管理、产能分配、大额人工审批、LLM降级与查询接口
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.production.agent import ProductionAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> ProductionAgent:
    """创建并初始化生产Agent（不启动主循环，仅注册身份和订阅）"""
    agent = ProductionAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_replenishment_approved_event(
    product_id: str = "prod-001",
    product_name: str = "Widget",
    reorder_quantity: int = 100,
) -> Event:
    """创建补货审批事件"""
    return Event(
        event_type=EventType.REPLENISHMENT_APPROVED,
        aggregate_id=product_id,
        aggregate_type="inventory",
        event_data={
            "product_id": product_id,
            "product_name": product_name,
            "reorder_quantity": reorder_quantity,
        },
    )


def _make_stock_low_event(
    product_id: str = "prod-001",
    product_name: str = "Widget",
    safety_stock: int = 50,
    reorder_point: int = 100,
) -> Event:
    """创建库存低预警事件"""
    return Event(
        event_type=EventType.STOCK_LOW_WARNING,
        aggregate_id=product_id,
        aggregate_type="inventory",
        event_data={
            "product_id": product_id,
            "product_name": product_name,
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "生产Agent"
    assert agent.domain == "production"
    assert agent.security_clearance == "confidential"
    assert "production_planning" in agent.capabilities
    assert "capacity_management" in agent.capabilities
    assert "bom_management" in agent.capabilities
    assert "quality_control" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """BOM与产能种子数据加载"""
    agent = await _setup_agent()

    bom = agent.get_bom()
    assert "prod-001" in bom
    assert "prod-002" in bom
    assert "prod-003" in bom
    assert len(bom["prod-001"]) == 2

    capacity = agent.get_capacity()
    assert len(capacity) == 3
    line_ids = [c["line_id"] for c in capacity]
    assert "line-001" in line_ids
    assert "line-002" in line_ids
    assert "line-003" in line_ids


# ========== test_production_order_creation ==========

async def test_production_order_creation():
    """补货审批事件触发生产订单创建"""
    agent = await _setup_agent()

    event = _make_replenishment_approved_event(
        product_id="prod-001", product_name="Widget", reorder_quantity=100
    )
    await agent.process_event(event)

    orders = agent.get_production_orders()
    assert len(orders) == 1

    order = orders[0]
    assert order["product_id"] == "prod-001"
    assert order["product_name"] == "Widget"
    assert order["quantity"] == 100
    assert order["total_cost"] > 0
    assert order["assigned_line"] != ""
    assert "bom" in order
    assert "estimated_days" in order
    assert order["status"] in ("approved", "pending_approval")

    # 验证决策历史
    assert len(agent._decision_history) == 1
    decision = agent._decision_history[0]
    assert decision.decision_type == "production_order_creation"


# ========== test_small_order_auto_approved ==========

async def test_small_order_auto_approved():
    """小额生产订单自动批准"""
    agent = await _setup_agent()

    # prod-001 BOM: 2*5 + 1*10 = 20元/件，100件 = 2000元 < 500000
    event = _make_replenishment_approved_event(
        product_id="prod-001", reorder_quantity=100
    )
    await agent.process_event(event)

    orders = agent.get_production_orders()
    assert len(orders) == 1
    order = orders[0]

    assert order["total_cost"] < ProductionAgent.HUMAN_APPROVAL_THRESHOLD
    assert order["status"] == "approved"
    assert order["requires_human_approval"] is False


# ========== test_large_order_human_approval ==========

async def test_large_order_human_approval():
    """大额生产订单需人工审批（>50万）"""
    agent = await _setup_agent()

    # prod-001 BOM: 20元/件，30000件 = 600000元 > 500000
    event = _make_replenishment_approved_event(
        product_id="prod-001", reorder_quantity=30000
    )
    await agent.process_event(event)

    orders = agent.get_production_orders()
    assert len(orders) == 1
    order = orders[0]

    assert order["total_cost"] > ProductionAgent.HUMAN_APPROVAL_THRESHOLD
    assert order["status"] == "pending_approval"
    assert order["requires_human_approval"] is True


# ========== test_stock_low_triggers_production ==========

async def test_stock_low_triggers_production():
    """库存低预警事件触发补产订单"""
    agent = await _setup_agent()

    event = _make_stock_low_event(
        product_id="prod-001", safety_stock=50, reorder_point=100
    )
    await agent.process_event(event)

    orders = agent.get_production_orders()
    assert len(orders) == 1
    assert orders[0]["product_id"] == "prod-001"
    assert orders[0]["source"] == "replenishment_approved"


# ========== test_capacity_allocation ==========

async def test_capacity_allocation():
    """生产订单分配产能"""
    agent = await _setup_agent()

    event = _make_replenishment_approved_event(
        product_id="prod-001", reorder_quantity=100
    )
    await agent.process_event(event)

    orders = agent.get_production_orders()
    assigned_line = orders[0]["assigned_line"]

    capacity = agent.get_capacity()
    line = next(c for c in capacity if c["line_id"] == assigned_line)
    assert line["utilized"] >= 100
    assert line["status"] in ("running", "busy", "overloaded")


# ========== test_make_decision ==========

async def test_make_decision():
    """Agent生产决策功能"""
    agent = await _setup_agent()

    event = _make_replenishment_approved_event(
        product_id="prod-001", reorder_quantity=100
    )
    await agent.process_event(event)

    orders = agent.get_production_orders()
    order_id = orders[0]["order_id"]

    decision = await agent.make_decision({"order_id": order_id})
    assert decision.decision_type == "production_auto"
    assert decision.confidence > 0
    assert decision.requires_human_approval is False
    assert "自动批准" in decision.action


# ========== test_make_decision_large_order ==========

async def test_make_decision_large_order():
    """大额生产订单决策需人工审批"""
    agent = await _setup_agent()

    event = _make_replenishment_approved_event(
        product_id="prod-001", reorder_quantity=30000
    )
    await agent.process_event(event)

    orders = agent.get_production_orders()
    order_id = orders[0]["order_id"]

    decision = await agent.make_decision({"order_id": order_id})
    assert decision.decision_type == "production_approval"
    assert decision.requires_human_approval is True
    assert "人工审批" in decision.action


# ========== test_make_decision_not_found ==========

async def test_make_decision_not_found():
    """不存在的订单ID返回错误决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"order_id": "nonexistent"})
    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_get_orders_filter ==========

async def test_get_orders_filter():
    """生产订单列表按状态过滤"""
    agent = await _setup_agent()

    # 小额订单（approved）
    event1 = _make_replenishment_approved_event(
        product_id="prod-001", reorder_quantity=100
    )
    await agent.process_event(event1)

    # 大额订单（pending_approval）
    event2 = _make_replenishment_approved_event(
        product_id="prod-002", reorder_quantity=30000
    )
    await agent.process_event(event2)

    all_orders = agent.get_production_orders()
    assert len(all_orders) == 2

    approved = agent.get_production_orders(status="approved")
    assert len(approved) == 1

    pending = agent.get_production_orders(status="pending_approval")
    assert len(pending) == 1


# ========== test_get_bom_filter ==========

async def test_get_bom_filter():
    """BOM按产品ID过滤"""
    agent = await _setup_agent()

    bom_001 = agent.get_bom(product_id="prod-001")
    assert "prod-001" in bom_001
    assert len(bom_001["prod-001"]) == 2

    bom_002 = agent.get_bom(product_id="prod-002")
    assert "prod-002" in bom_002

    bom_all = agent.get_bom()
    assert len(bom_all) == 3


# ========== test_llm_fallback ==========

async def test_llm_fallback():
    """LLM不可用时降级到规则模式"""
    from app.core.config import settings

    assert settings.llm_enabled is False

    agent = await _setup_agent()

    # LLM计划应返回None
    result = await agent._plan_production_llm("prod-001", "Widget", 100)
    assert result is None

    # 规则计划应正常工作
    plan = agent._plan_production_rules("prod-001", "Widget", 100)
    assert plan["method"] == "rules"
    assert plan["assigned_line"] != ""
    assert plan["unit_cost"] > 0
    assert plan["estimated_days"] > 0
    assert "reasoning" in plan


# ========== test_on_tick ==========

async def test_on_tick():
    """周期性任务：更新产能利用率状态"""
    agent = await _setup_agent()

    # 分配一些产能
    event = _make_replenishment_approved_event(
        product_id="prod-001", reorder_quantity=100
    )
    await agent.process_event(event)

    # on_tick应正常执行不报错
    await agent.on_tick()

    # 验证产能状态已更新
    capacity = agent.get_capacity()
    for line in capacity:
        assert line["status"] in ("idle", "running", "busy", "overloaded")
