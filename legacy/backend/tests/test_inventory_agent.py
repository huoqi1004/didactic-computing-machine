"""
库存守护Agent测试
验证库存更新、低库存补货建议、需求预测
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.inventory.agent import InventoryGuardianAgent, InventoryAggregate


# ========== 辅助函数 ==========

async def _setup_agent() -> InventoryGuardianAgent:
    """创建并初始化库存Agent（不启动主循环，仅注册身份和订阅）"""
    agent = InventoryGuardianAgent()
    # 手动注册身份，不启动 run_loop
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_inventory_event(
    product_id: str = "prod-001",
    product_name: str = "Widget",
    quantity: int = 100,
    safety_stock: int = 50,
    reorder_point: int = 100,
    unit_cost: float = 10.0,
    warehouse: str = "WH-A",
) -> Event:
    """创建库存调整事件"""
    return Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id=product_id,
        aggregate_type="inventory",
        event_data={
            "product_id": product_id,
            "product_name": product_name,
            "quantity": quantity,
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
            "unit_cost": unit_cost,
            "warehouse": warehouse,
        },
    )


# ========== test_inventory_update ==========

async def test_inventory_update():
    """库存更新"""
    agent = await _setup_agent()

    event = _make_inventory_event(
        product_id="prod-001",
        product_name="Widget",
        quantity=200,
        safety_stock=50,
        reorder_point=100,
        unit_cost=10.0,
    )
    await agent.process_event(event)

    # 验证库存缓存已更新
    status_list = agent.get_inventory_status()
    assert len(status_list) == 1
    status = status_list[0]
    assert status["product_id"] == "prod-001"
    assert status["product_name"] == "Widget"
    assert status["quantity"] == 200
    assert status["safety_stock"] == 50
    assert status["reorder_point"] == 100
    assert status["needs_replenishment"] is False  # 200 > 100

    # 更新库存（第二次事件）
    event2 = _make_inventory_event(
        product_id="prod-001",
        product_name="Widget",
        quantity=80,
    )
    await agent.process_event(event2)

    status_list = agent.get_inventory_status()
    assert status_list[0]["quantity"] == 80
    assert status_list[0]["needs_replenishment"] is True  # 80 <= 100


# ========== test_replenishment_proposal ==========

async def test_replenishment_proposal():
    """低库存触发补货建议"""
    agent = await _setup_agent()

    # 设置低库存（低于再订货点）
    event = _make_inventory_event(
        product_id="prod-low",
        product_name="LowStockProduct",
        quantity=30,
        safety_stock=50,
        reorder_point=100,
        unit_cost=20.0,
    )
    await agent.process_event(event)

    # 库存低于再订货点，应触发补货建议
    proposals = agent.get_proposals()
    assert len(proposals) == 1

    proposal = proposals[0]
    assert proposal["product_id"] == "prod-low"
    assert proposal["product_name"] == "LowStockProduct"
    assert proposal["reorder_qty"] > 0
    assert proposal["total_cost"] > 0
    # 成本 = 补货量 * 单价
    assert proposal["total_cost"] == proposal["reorder_qty"] * 20.0

    # 验证决策历史
    assert len(agent._decision_history) == 1
    decision = agent._decision_history[0]
    assert decision.decision_type == "replenishment_proposal"
    assert decision.confidence > 0
    assert "LowStockProduct" in decision.reasoning


# ========== test_demand_forecast ==========

async def test_demand_forecast():
    """需求预测"""
    agent = await _setup_agent()

    # 创建一个库存聚合根
    inventory = InventoryAggregate("prod-forecast")
    inventory.product_name = "ForecastProduct"
    inventory.quantity = 40
    inventory.safety_stock = 50
    inventory.reorder_point = 100
    inventory.unit_cost = 15.0

    forecast = await agent._forecast_demand(inventory)

    # 验证预测结果结构
    assert "predicted_demand" in forecast
    assert "suggested_qty" in forecast
    assert "confidence" in forecast
    assert "factors" in forecast
    assert "time_horizon" in forecast

    # 预测需求应为正数
    assert forecast["predicted_demand"] > 0
    assert forecast["suggested_qty"] > 0

    # 置信度在0到1之间
    assert 0 < forecast["confidence"] <= 1
    assert forecast["time_horizon"] == "7_days"
    assert isinstance(forecast["factors"], list)
    assert len(forecast["factors"]) > 0


# ========== 额外测试：高成本补货需人工审批 ==========

async def test_high_cost_replenishment():
    """高额补货需人工审批"""
    agent = await _setup_agent()

    # 设置高单价产品，使补货成本超过5万
    event = _make_inventory_event(
        product_id="prod-expensive",
        product_name="ExpensiveProduct",
        quantity=10,
        safety_stock=100,
        reorder_point=200,
        unit_cost=500.0,  # 高单价
    )
    await agent.process_event(event)

    proposals = agent.get_proposals()
    assert len(proposals) == 1
    proposal = proposals[0]
    # 补货量至少 = safety_stock * 2 - quantity = 100*2 - 10 = 190
    # 成本 = 190 * 500 = 95000 > 50000 → 需要人工审批
    assert proposal["total_cost"] > 50000
    assert proposal["status"] == "pending_approval"


# ========== 额外测试：正常库存不触发补货 ==========

async def test_normal_inventory_no_proposal():
    """库存充足时不触发补货"""
    agent = await _setup_agent()

    event = _make_inventory_event(
        product_id="prod-ok",
        product_name="OKProduct",
        quantity=500,
        safety_stock=50,
        reorder_point=100,
    )
    await agent.process_event(event)

    proposals = agent.get_proposals()
    assert len(proposals) == 0  # 库存充足，无补货建议


# ========== 额外测试：补货建议审批事件处理 ==========

async def test_replenishment_approval_event():
    """补货审批事件处理"""
    agent = await _setup_agent()

    # 先触发补货建议
    event = _make_inventory_event(
        product_id="prod-approval",
        product_name="ApprovalProduct",
        quantity=20,
        safety_stock=50,
        reorder_point=100,
        unit_cost=10.0,
    )
    await agent.process_event(event)

    proposals = agent.get_proposals()
    assert len(proposals) == 1
    proposal_id = proposals[0]["decision_id"]

    # 模拟审批通过事件
    approval_event = Event(
        event_type=EventType.REPLENISHMENT_APPROVED,
        aggregate_id="prod-approval",
        aggregate_type="inventory",
        event_data={"proposal_id": proposal_id},
    )
    await agent.process_event(approval_event)

    # 验证建议状态已更新
    proposals = agent.get_proposals()
    assert proposals[0]["status"] == "approved"


# ========== 额外测试：make_decision ==========

async def test_make_decision():
    """Agent决策功能"""
    agent = await _setup_agent()

    # 先更新库存
    event = _make_inventory_event(
        product_id="prod-decision",
        product_name="DecisionProduct",
        quantity=30,
        safety_stock=50,
        reorder_point=100,
        unit_cost=10.0,
    )
    await agent.process_event(event)

    # 做出补货决策
    decision = await agent.make_decision({"product_id": "prod-decision"})

    assert decision.decision_type == "replenishment_decision"
    assert decision.confidence > 0
    assert "补货" in decision.action
    assert "prod-decision" in decision.context.get("product_id", "")


# ========== 额外测试：make_decision 不存在的产品 ==========

async def test_make_decision_nonexistent_product():
    """对不存在的产品做出决策返回错误"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"product_id": "nonexistent"})

    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ---------------------------------------------------------------------------
# 生产化整改（2026-10-04）：本文件用例验证**规则路径**的确定性输出，
# 显式钉 LLM 关闭——LLM 路径由 test_e2e_flows 场景 1（真 Agnes）与
# 注入/安全套件覆盖。（真 LLM 接入曾使本组用例全部走网络调用而失败）
# ---------------------------------------------------------------------------
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _pin_rule_mode(monkeypatch):
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "llm_enabled", False)
