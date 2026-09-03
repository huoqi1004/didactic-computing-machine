# -*- coding: utf-8 -*-
"""
销售Agent测试
验证销售订单管理、目标管理、销售预测、事件处理与决策功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.sales.agent import SalesAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> SalesAgent:
    """创建并初始化销售Agent（不启动主循环，仅注册身份和订阅）"""
    agent = SalesAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_sales_order_created_event(
    order_id: str = "SO-EVT-001",
    customer_id: str = "CUST-EVT-001",
    customer_name: str = "测试客户",
    total_amount: float = 200000,
) -> Event:
    """创建销售订单创建事件"""
    return Event(
        event_type=EventType.SALES_ORDER_CREATED,
        aggregate_id=order_id,
        aggregate_type="sales_order",
        event_data={
            "order_id": order_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "items": [
                {"product_id": "P001", "product_name": "ERP系统", "qty": 1,
                 "unit_price": total_amount},
            ],
            "total_amount": total_amount,
            "status": "pending",
        },
    )


def _make_sales_order_confirmed_event(
    order_id: str = "SO-EVT-001",
    salesperson_id: str = "SP-001",
) -> Event:
    """创建销售订单确认事件"""
    return Event(
        event_type=EventType.SALES_ORDER_CONFIRMED,
        aggregate_id=order_id,
        aggregate_type="sales_order",
        event_data={
            "order_id": order_id,
            "customer_id": "CUST-EVT-001",
            "total_amount": 200000,
            "status": "confirmed",
            "salesperson_id": salesperson_id,
        },
    )


def _make_sales_target_updated_event(
    salesperson_id: str = "SP-001",
    amount: float = 3000000,
    achieved_amount: float = 500000,
) -> Event:
    """创建销售目标更新事件"""
    return Event(
        event_type=EventType.SALES_TARGET_UPDATED,
        aggregate_id=f"target:{salesperson_id}",
        aggregate_type="sales_target",
        event_data={
            "salesperson_id": salesperson_id,
            "amount": amount,
            "achieved_amount": achieved_amount,
        },
    )


def _make_opportunity_won_event(
    customer_id: str = "CUST-OPP-001",
    customer_name: str = "赢单客户",
    amount: float = 300000,
    opportunity_name: str = "ERP系统采购",
) -> Event:
    """创建商机赢单事件"""
    return Event(
        event_type=EventType.OPPORTUNITY_WON,
        aggregate_id=customer_id,
        aggregate_type="opportunity",
        event_data={
            "customer_id": customer_id,
            "customer_name": customer_name,
            "amount": amount,
            "opportunity_name": opportunity_name,
            "product_id": "P001",
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "销售Agent"
    assert agent.domain == "sales"
    assert agent.security_clearance == "internal"
    assert "order_management" in agent.capabilities
    assert "sales_forecasting" in agent.capabilities
    assert "target_management" in agent.capabilities
    assert "contract_tracking" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载3个订单和2个目标"""
    agent = await _setup_agent()

    orders = agent.get_orders()
    assert len(orders) == 3

    # 验证订单状态分布
    confirmed = agent.get_orders(status="confirmed")
    pending = agent.get_orders(status="pending")
    assert len(confirmed) == 2
    assert len(pending) == 1

    targets = agent.get_targets()
    assert len(targets) == 2

    # 验证目标已根据已确认订单更新完成额
    sp001_targets = agent.get_targets(salesperson_id="SP-001")
    assert len(sp001_targets) == 1
    # 2个已确认订单(400000 + 250000)均分到2个目标 = 325000
    assert sp001_targets[0]["achieved_amount"] == 325000


# ========== test_create_order ==========

async def test_create_order():
    """创建销售订单"""
    agent = await _setup_agent()

    order = await agent.create_order(
        customer_id="CUST-001",
        items=[{"product_id": "P001", "product_name": "ERP系统", "qty": 1,
                "unit_price": 200000}],
        total_amount=200000,
        delivery_date="2026-09-01",
    )

    assert order["order_id"].startswith("SO-")
    assert order["customer_id"] == "CUST-001"
    assert order["total_amount"] == 200000
    assert order["status"] == "pending"
    assert order["confirmed_at"] is None

    # 验证已加入订单列表
    found = agent.get_order(order["order_id"])
    assert found is not None
    assert found["customer_id"] == "CUST-001"


# ========== test_confirm_order ==========

async def test_confirm_order():
    """确认销售订单"""
    agent = await _setup_agent()

    order = await agent.create_order(
        customer_id="CUST-001",
        items=[{"product_id": "P001", "product_name": "ERP系统", "qty": 1,
                "unit_price": 200000}],
        total_amount=200000,
        delivery_date="2026-09-01",
    )

    confirmed = await agent.confirm_order(order["order_id"])

    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_at"] is not None

    # 验证订单列表中状态已更新
    found = agent.get_order(order["order_id"])
    assert found["status"] == "confirmed"


# ========== test_set_target ==========

async def test_set_target():
    """设定销售目标"""
    agent = await _setup_agent()

    target = agent.set_target(
        salesperson_id="SP-003",
        period="2026Q3",
        amount=1800000,
        product_line="咨询服务",
    )

    assert target["target_id"].startswith("TGT-")
    assert target["salesperson_id"] == "SP-003"
    assert target["period"] == "2026Q3"
    assert target["amount"] == 1800000
    assert target["product_line"] == "咨询服务"
    assert target["achieved_amount"] == 0.0

    # 验证已加入目标列表
    targets = agent.get_targets(salesperson_id="SP-003")
    assert len(targets) == 1


# ========== test_get_target_progress ==========

async def test_get_target_progress():
    """获取目标完成率"""
    agent = await _setup_agent()

    # 使用种子目标的salesperson_id和period
    seed_targets = agent.get_targets()
    salesperson_id = seed_targets[0]["salesperson_id"]
    period = seed_targets[0]["period"]

    progress = agent.get_target_progress(salesperson_id, period)

    assert progress["found"] is True
    assert progress["salesperson_id"] == salesperson_id
    assert progress["period"] == period
    assert progress["target_amount"] == 2000000
    assert progress["achieved_amount"] == 325000
    assert progress["completion_rate"] == round(325000 / 2000000 * 100, 2)
    assert progress["remaining"] == 2000000 - 325000

    # 验证不存在的目标
    progress_none = agent.get_target_progress("NONEXIST", "2026Q1")
    assert progress_none["found"] is False
    assert progress_none["completion_rate"] == 0.0


# ========== test_forecast_sales_historical ==========

async def test_forecast_sales_historical():
    """历史均值法销售预测"""
    agent = await _setup_agent()

    # 种子数据有2个已确认订单(400000 + 250000)
    forecast = agent.forecast_sales("2026Q4", method="historical")

    assert forecast["method"] == "historical"
    assert forecast["period"] == "2026Q4"
    assert forecast["data_points"] == 2
    avg = (400000 + 250000) / 2
    assert forecast["predicted_amount"] == round(avg, 2)
    assert forecast["historical_avg"] == round(avg, 2)
    # 2个数据点 < 3，置信度0.5
    assert forecast["confidence"] == 0.5
    assert "reasoning" in forecast


# ========== test_forecast_sales_trend ==========

async def test_forecast_sales_trend():
    """趋势分析法销售预测"""
    agent = await _setup_agent()

    forecast = agent.forecast_sales("2026Q4", method="trend")

    assert forecast["method"] == "trend"
    assert forecast["period"] == "2026Q4"
    assert forecast["data_points"] == 2
    assert forecast["predicted_amount"] >= 0
    # 2个数据点 >= 2 但 < 3，置信度0.6
    assert forecast["confidence"] == 0.6
    assert "reasoning" in forecast
    assert "historical_avg" in forecast


# ========== test_sales_order_created_event ==========

async def test_sales_order_created_event():
    """销售订单创建事件同步订单"""
    agent = await _setup_agent()

    initial_count = len(agent.get_orders())

    event = _make_sales_order_created_event(
        order_id="SO-EVT-001",
        customer_id="CUST-EVT-001",
        total_amount=200000,
    )
    await agent.process_event(event)

    assert len(agent.get_orders()) == initial_count + 1

    order = agent.get_order("SO-EVT-001")
    assert order is not None
    assert order["customer_id"] == "CUST-EVT-001"
    assert order["total_amount"] == 200000
    assert order["status"] == "pending"


# ========== test_sales_order_confirmed_event ==========

async def test_sales_order_confirmed_event():
    """销售订单确认事件更新订单状态与目标完成额"""
    agent = await _setup_agent()

    # 获取种子数据中的待确认订单
    pending_orders = agent.get_orders(status="pending")
    assert len(pending_orders) == 1
    pending_order = pending_orders[0]
    order_id = pending_order["order_id"]

    # 记录确认前SP-001的完成额
    sp001_before = agent.get_targets(salesperson_id="SP-001")[0]["achieved_amount"]

    event = _make_sales_order_confirmed_event(
        order_id=order_id,
        salesperson_id="SP-001",
    )
    await agent.process_event(event)

    # 验证订单状态已更新
    order = agent.get_order(order_id)
    assert order["status"] == "confirmed"
    assert order["confirmed_at"] is not None

    # 验证SP-001目标完成额已增加
    sp001_after = agent.get_targets(salesperson_id="SP-001")[0]["achieved_amount"]
    assert sp001_after == sp001_before + pending_order["total_amount"]


# ========== test_sales_target_updated_event ==========

async def test_sales_target_updated_event():
    """销售目标更新事件更新目标金额"""
    agent = await _setup_agent()

    event = _make_sales_target_updated_event(
        salesperson_id="SP-001",
        amount=3000000,
        achieved_amount=500000,
    )
    await agent.process_event(event)

    sp001_targets = agent.get_targets(salesperson_id="SP-001")
    assert len(sp001_targets) == 1
    assert sp001_targets[0]["amount"] == 3000000
    assert sp001_targets[0]["achieved_amount"] == 500000


# ========== test_opportunity_won_event ==========

async def test_opportunity_won_event():
    """商机赢单事件自动创建销售订单"""
    agent = await _setup_agent()

    initial_count = len(agent.get_orders())

    event = _make_opportunity_won_event(
        customer_id="CUST-OPP-001",
        amount=300000,
    )
    await agent.process_event(event)

    # 验证新订单已创建
    assert len(agent.get_orders()) == initial_count + 1

    # 找到新创建的订单（客户ID为CUST-OPP-001）
    new_orders = agent.get_orders(customer_id="CUST-OPP-001")
    assert len(new_orders) == 1
    assert new_orders[0]["total_amount"] == 300000
    assert new_orders[0]["status"] == "pending"


# ========== test_make_decision_confirm_large_order ==========

async def test_make_decision_confirm_large_order():
    """大额订单确认决策需人工审批（>50万）"""
    agent = await _setup_agent()

    order = await agent.create_order(
        customer_id="CUST-LARGE",
        items=[{"product_id": "P001", "product_name": "大型ERP", "qty": 1,
                "unit_price": 600000}],
        total_amount=600000,
        delivery_date="2026-09-01",
    )

    decision = await agent.make_decision({
        "action": "confirm_order",
        "order_id": order["order_id"],
    })

    assert decision.decision_type == "order_confirmation"
    assert decision.confidence == 0.9
    assert decision.requires_human_approval is True
    assert "人工确认" in decision.action


# ========== test_make_decision_confirm_small_order ==========

async def test_make_decision_confirm_small_order():
    """小额订单确认决策自动通过（<=50万）"""
    agent = await _setup_agent()

    order = await agent.create_order(
        customer_id="CUST-SMALL",
        items=[{"product_id": "P001", "product_name": "小型系统", "qty": 1,
                "unit_price": 200000}],
        total_amount=200000,
        delivery_date="2026-09-01",
    )

    decision = await agent.make_decision({
        "action": "confirm_order",
        "order_id": order["order_id"],
    })

    assert decision.decision_type == "order_confirmation"
    assert decision.confidence == 0.95
    assert decision.requires_human_approval is False
    assert "自动确认" in decision.action


# ========== test_make_decision_set_target ==========

async def test_make_decision_set_target():
    """设定销售目标决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "set_target",
        "salesperson_id": "SP-001",
        "period": "2026Q4",
        "amount": 2500000,
    })

    assert decision.decision_type == "target_setting"
    assert decision.confidence == 0.85
    assert decision.requires_human_approval is False
    assert "设定销售目标" in decision.action


# ========== test_make_decision_forecast ==========

async def test_make_decision_forecast():
    """销售预测决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "forecast",
        "period": "2026Q4",
    })

    assert decision.decision_type == "sales_forecast"
    assert decision.confidence == 0.8
    assert decision.requires_human_approval is False
    assert "预测" in decision.action


# ========== test_get_orders_filter ==========

async def test_get_orders_filter():
    """订单列表按状态和客户过滤"""
    agent = await _setup_agent()

    # 种子数据: 2 confirmed + 1 pending
    all_orders = agent.get_orders()
    assert len(all_orders) == 3

    # 按状态过滤
    confirmed = agent.get_orders(status="confirmed")
    assert len(confirmed) == 2
    assert all(o["status"] == "confirmed" for o in confirmed)

    pending = agent.get_orders(status="pending")
    assert len(pending) == 1
    assert all(o["status"] == "pending" for o in pending)

    # 按客户过滤
    cust1_orders = agent.get_orders(customer_id="CUST-SEED-001")
    assert len(cust1_orders) == 1
    assert cust1_orders[0]["customer_name"] == "华盛科技有限公司"

    # 组合过滤
    cust1_confirmed = agent.get_orders(
        status="confirmed", customer_id="CUST-SEED-001"
    )
    assert len(cust1_confirmed) == 1


# ========== test_get_targets_filter ==========

async def test_get_targets_filter():
    """目标列表按周期和销售人员过滤"""
    agent = await _setup_agent()

    all_targets = agent.get_targets()
    assert len(all_targets) == 2

    # 获取种子数据的周期
    period = all_targets[0]["period"]

    # 按销售人员过滤
    sp001 = agent.get_targets(salesperson_id="SP-001")
    assert len(sp001) == 1
    assert sp001[0]["salesperson_id"] == "SP-001"
    assert sp001[0]["salesperson_name"] == "刘洋"

    sp002 = agent.get_targets(salesperson_id="SP-002")
    assert len(sp002) == 1
    assert sp002[0]["salesperson_name"] == "陈芳"

    # 按周期过滤
    period_targets = agent.get_targets(period=period)
    assert len(period_targets) == 2

    # 不存在的周期
    empty = agent.get_targets(period="2099Q1")
    assert len(empty) == 0


# ========== test_get_stats ==========

async def test_get_stats():
    """销售统计信息"""
    agent = await _setup_agent()

    stats = agent.get_stats()

    # 种子数据: 3个订单(2 confirmed + 1 pending), 2个目标
    assert stats["orders"] == 3
    assert stats["confirmed_orders"] == 2
    assert stats["pending_orders"] == 1
    assert stats["targets"] == 2

    # 已确认订单金额: 400000 + 250000 = 650000
    assert stats["total_confirmed_amount"] == 650000
    # 待确认订单金额: 500000
    assert stats["total_pending_amount"] == 500000

    # 目标总金额: 2000000 + 1500000 = 3500000
    assert stats["total_target_amount"] == 3500000
    # 完成额: 325000 + 325000 = 650000
    assert stats["total_achieved_amount"] == 650000

    # 整体完成率: 650000 / 3500000 * 100
    expected_rate = round(650000 / 3500000 * 100, 2)
    assert stats["overall_completion_rate"] == expected_rate
