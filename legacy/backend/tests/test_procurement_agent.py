# -*- coding: utf-8 -*-
"""
采购Agent测试
验证采购订单生成、供应商比价、人在回路审批、交期跟踪与异常处理
"""
import pytest
from datetime import datetime, timezone, timedelta

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.procurement.agent import (
    ProcurementAgent,
    KnowledgeGraph,
    knowledge_graph,
)


# ========== 辅助函数 ==========

async def _setup_agent() -> ProcurementAgent:
    """创建并初始化采购Agent（不启动主循环，仅注册身份和订阅）"""
    agent = ProcurementAgent()
    # 手动注册身份，不启动 run_loop
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_replenishment_event(
    product_id: str = "prod-001",
    product_name: str = "Widget",
    reorder_quantity: int = 100,
    total_cost: float = 1000.0,
    unit_cost: float = 10.0,
    proposal_id: str = "prop-001",
) -> Event:
    """创建补货建议事件（模拟库存守护Agent发布的事件）"""
    return Event(
        event_type=EventType.REPLENISHMENT_PROPOSED,
        aggregate_id=product_id,
        aggregate_type="inventory",
        event_data={
            "proposal_id": proposal_id,
            "product_id": product_id,
            "product_name": product_name,
            "current_quantity": 30,
            "reorder_quantity": reorder_quantity,
            "total_cost": total_cost,
            "unit_cost": unit_cost,
            "confidence": 0.78,
            "requires_approval": False,
            "urgency": "高",
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "采购Agent"
    assert agent.domain == "procurement"
    assert agent.security_clearance == "confidential"
    assert "supplier_comparison" in agent.capabilities
    assert "purchase_order_generation" in agent.capabilities
    assert "delivery_tracking" in agent.capabilities
    assert "contract_negotiation" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_supplier_seeding ==========

async def test_supplier_seeding():
    """供应商种子数据从知识图谱加载"""
    agent = await _setup_agent()

    suppliers = agent.get_suppliers()
    assert len(suppliers) == 3

    supplier_ids = [s["supplier_id"] for s in suppliers]
    assert "sup-001" in supplier_ids
    assert "sup-002" in supplier_ids
    assert "sup-003" in supplier_ids

    # 验证供应商数据结构
    for sup in suppliers:
        assert "name" in sup
        assert "rating" in sup
        assert "products" in sup
        assert "lead_time" in sup
        assert "payment_terms" in sup


# ========== test_replenishment_triggers_purchase_order ==========

async def test_replenishment_triggers_purchase_order():
    """补货建议事件触发采购订单生成"""
    agent = await _setup_agent()

    event = _make_replenishment_event(
        product_id="prod-001",
        product_name="Widget",
        reorder_quantity=100,
        total_cost=1000.0,
        unit_cost=10.0,
    )
    await agent.process_event(event)

    orders = agent.get_purchase_orders()
    assert len(orders) == 1

    po = orders[0]
    assert po["product_id"] == "prod-001"
    assert po["product_name"] == "Widget"
    assert po["quantity"] == 100
    assert po["total_amount"] > 0
    assert po["supplier_id"] is not None
    assert po["supplier_name"] != ""
    assert "expected_delivery_date" in po
    assert po["status"] in ("approved", "pending_approval")

    # 验证决策历史
    assert len(agent._decision_history) == 1
    decision = agent._decision_history[0]
    assert decision.decision_type == "purchase_order_generation"
    assert decision.confidence > 0


# ========== test_supplier_comparison_rules ==========

async def test_supplier_comparison_rules():
    """规则比价选择最优供应商"""
    agent = await _setup_agent()

    suppliers = knowledge_graph.find_suppliers_for_product("prod-001")
    assert len(suppliers) == 3

    result = agent._compare_suppliers_rules(suppliers, "prod-001", 100)

    assert result["method"] == "rules"
    assert result["recommended_supplier"] is not None
    assert result["recommended_supplier_id"] is not None
    assert result["confidence"] > 0
    assert len(result["analysis"]) == 3

    # 验证分析结果包含评分
    for item in result["analysis"]:
        assert "supplier_id" in item
        assert "total_score" in item
        assert "rating_score" in item
        assert "price_score" in item
        assert "lead_score" in item

    # 验证按综合评分降序排列
    scores = [item["total_score"] for item in result["analysis"]]
    assert scores == sorted(scores, reverse=True)

    # sup-003（评分4.8，交期5天）综合评分应最高
    assert result["recommended_supplier_id"] == "sup-003"


# ========== test_small_amount_auto_approved ==========

async def test_small_amount_auto_approved():
    """小额采购自动批准"""
    agent = await _setup_agent()

    # prod-001 由 sup-003 供应，单价 10.50
    # 100 * 10.50 = 1050 < 100000
    event = _make_replenishment_event(
        product_id="prod-001",
        product_name="Widget",
        reorder_quantity=100,
        total_cost=1050.0,
        unit_cost=10.50,
    )
    await agent.process_event(event)

    orders = agent.get_purchase_orders()
    assert len(orders) == 1
    po = orders[0]

    assert po["total_amount"] < ProcurementAgent.HUMAN_APPROVAL_THRESHOLD
    assert po["status"] == "approved"
    assert po["requires_human_approval"] is False
    assert po["requires_consensus"] is False


# ========== test_large_amount_human_approval ==========

async def test_large_amount_human_approval():
    """大额采购需人在回路确认（>10万）"""
    agent = await _setup_agent()

    # 10000 * 10.50 = 105000 > 100000
    event = _make_replenishment_event(
        product_id="prod-001",
        product_name="Widget",
        reorder_quantity=10000,
        total_cost=105000.0,
        unit_cost=10.50,
    )
    await agent.process_event(event)

    orders = agent.get_purchase_orders()
    assert len(orders) == 1
    po = orders[0]

    assert po["total_amount"] > ProcurementAgent.HUMAN_APPROVAL_THRESHOLD
    assert po["status"] == "pending_approval"
    assert po["requires_human_approval"] is True
    assert po["requires_consensus"] is False


# ========== test_consensus_threshold ==========

async def test_consensus_threshold():
    """超大额采购触发多Agent共识（>50万）"""
    agent = await _setup_agent()

    # 50000 * 10.50 = 525000 > 500000
    event = _make_replenishment_event(
        product_id="prod-001",
        product_name="Widget",
        reorder_quantity=50000,
        total_cost=525000.0,
        unit_cost=10.50,
    )
    await agent.process_event(event)

    orders = agent.get_purchase_orders()
    assert len(orders) == 1
    po = orders[0]

    assert po["total_amount"] > ProcurementAgent.CONSENSUS_THRESHOLD
    assert po["status"] == "pending_approval"
    assert po["requires_human_approval"] is True
    assert po["requires_consensus"] is True


# ========== test_purchase_order_approval ==========

async def test_purchase_order_approval():
    """采购订单审批通过流程"""
    agent = await _setup_agent()

    # 创建需要审批的大额订单
    event = _make_replenishment_event(
        product_id="prod-001",
        product_name="Widget",
        reorder_quantity=10000,
        total_cost=105000.0,
        unit_cost=10.50,
    )
    await agent.process_event(event)

    orders = agent.get_purchase_orders()
    assert len(orders) == 1
    po_id = orders[0]["po_id"]
    assert orders[0]["status"] == "pending_approval"

    # 审批通过
    result = await agent.approve_purchase_order(po_id, approved=True)
    assert result is True

    # 验证状态已更新
    orders = agent.get_purchase_orders()
    assert orders[0]["status"] == "approved"
    assert "approved_at" in orders[0]


# ========== test_purchase_order_rejection ==========

async def test_purchase_order_rejection():
    """采购订单审批拒绝流程"""
    agent = await _setup_agent()

    event = _make_replenishment_event(
        product_id="prod-001",
        product_name="Widget",
        reorder_quantity=10000,
        total_cost=105000.0,
        unit_cost=10.50,
    )
    await agent.process_event(event)

    orders = agent.get_purchase_orders()
    po_id = orders[0]["po_id"]

    # 审批拒绝
    result = await agent.approve_purchase_order(po_id, approved=False)
    assert result is True

    orders = agent.get_purchase_orders()
    assert orders[0]["status"] == "rejected"
    assert "rejected_at" in orders[0]


# ========== test_delivery_tracking_delayed ==========

async def test_delivery_tracking_delayed():
    """交期跟踪检测延期订单"""
    agent = await _setup_agent()

    # 创建一个小额订单（自动批准）
    event = _make_replenishment_event(
        product_id="prod-001",
        product_name="Widget",
        reorder_quantity=100,
        total_cost=1050.0,
        unit_cost=10.50,
    )
    await agent.process_event(event)

    orders = agent.get_purchase_orders()
    assert len(orders) == 1
    po_id = orders[0]["po_id"]

    # 手动将交期设为过去时间，模拟延期
    past_date = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    agent._purchase_orders[po_id]["expected_delivery_date"] = past_date
    agent._purchase_orders[po_id]["status"] = "approved"

    # 执行交期跟踪
    delayed = await agent._track_delivery()

    assert len(delayed) == 1
    assert delayed[0]["po_id"] == po_id

    # 验证状态已更新为 delayed
    orders = agent.get_purchase_orders()
    assert orders[0]["status"] == "delayed"


# ========== test_make_decision ==========

async def test_make_decision():
    """Agent采购决策功能"""
    agent = await _setup_agent()

    # 创建小额订单
    event = _make_replenishment_event(
        product_id="prod-001",
        product_name="Widget",
        reorder_quantity=100,
        total_cost=1050.0,
        unit_cost=10.50,
    )
    await agent.process_event(event)

    orders = agent.get_purchase_orders()
    po_id = orders[0]["po_id"]

    # 小额决策
    decision = await agent.make_decision({"po_id": po_id})
    assert decision.decision_type == "procurement_auto"
    assert decision.confidence > 0
    assert decision.requires_human_approval is False
    assert "自动批准" in decision.action


# ========== test_make_decision_large_amount ==========

async def test_make_decision_large_amount():
    """大额采购决策需人工审批"""
    agent = await _setup_agent()

    event = _make_replenishment_event(
        product_id="prod-001",
        product_name="Widget",
        reorder_quantity=10000,
        total_cost=105000.0,
        unit_cost=10.50,
    )
    await agent.process_event(event)

    orders = agent.get_purchase_orders()
    po_id = orders[0]["po_id"]

    decision = await agent.make_decision({"po_id": po_id})
    assert decision.decision_type == "procurement_approval"
    assert decision.requires_human_approval is True
    assert "人工审批" in decision.action


# ========== test_make_decision_consensus ==========

async def test_make_decision_consensus():
    """超大额采购决策需多Agent共识"""
    agent = await _setup_agent()

    event = _make_replenishment_event(
        product_id="prod-001",
        product_name="Widget",
        reorder_quantity=50000,
        total_cost=525000.0,
        unit_cost=10.50,
    )
    await agent.process_event(event)

    orders = agent.get_purchase_orders()
    po_id = orders[0]["po_id"]

    decision = await agent.make_decision({"po_id": po_id})
    assert decision.decision_type == "procurement_consensus"
    assert decision.requires_human_approval is True
    assert decision.context.get("requires_consensus") is True


# ========== test_get_purchase_orders_filter ==========

async def test_get_purchase_orders_filter():
    """采购订单列表按状态过滤"""
    agent = await _setup_agent()

    # 创建一个小额订单（approved）
    event1 = _make_replenishment_event(
        product_id="prod-001",
        product_name="WidgetA",
        reorder_quantity=100,
        total_cost=1050.0,
        unit_cost=10.50,
        proposal_id="prop-001",
    )
    await agent.process_event(event1)

    # 创建一个大额订单（pending_approval）
    event2 = _make_replenishment_event(
        product_id="prod-002",
        product_name="WidgetB",
        reorder_quantity=10000,
        total_cost=150000.0,
        unit_cost=15.00,
        proposal_id="prop-002",
    )
    await agent.process_event(event2)

    # 全部订单
    all_orders = agent.get_purchase_orders()
    assert len(all_orders) == 2

    # 按状态过滤
    approved = agent.get_purchase_orders(status="approved")
    assert len(approved) == 1
    assert approved[0]["product_name"] == "WidgetA"

    pending = agent.get_purchase_orders(status="pending_approval")
    assert len(pending) == 1
    assert pending[0]["product_name"] == "WidgetB"


# ========== test_knowledge_graph ==========

def test_knowledge_graph():
    """知识图谱节点查询"""
    kg = KnowledgeGraph()
    kg.add_node("supplier", "sup-test-1", name="测试供应商A", rating=4.0)
    kg.add_node("supplier", "sup-test-2", name="测试供应商B", rating=4.5)
    kg.add_node("product", "prod-test", name="测试产品")

    suppliers = kg.find_nodes("supplier")
    assert len(suppliers) == 2

    products = kg.find_nodes("product")
    assert len(products) == 1

    # 查找单个节点
    node = kg.find_node("supplier", "sup-test-1")
    assert node is not None
    assert node["name"] == "测试供应商A"

    # 不存在的节点类型
    empty = kg.find_nodes("nonexistent")
    assert empty == []

    # clear
    kg.clear()
    assert kg.find_nodes("supplier") == []


# ========== test_approve_nonexistent_po ==========

async def test_approve_nonexistent_po():
    """审批不存在的采购订单返回False"""
    agent = await _setup_agent()

    result = await agent.approve_purchase_order("nonexistent-po", approved=True)
    assert result is False


# ========== test_make_decision_nonexistent_po ==========

async def test_make_decision_nonexistent_po():
    """对不存在的采购订单做出决策返回错误"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"po_id": "nonexistent"})
    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_fallback_suppliers_for_unknown_product ==========

async def test_fallback_suppliers_for_unknown_product():
    """无精确匹配供应商时回退到全部供应商"""
    agent = await _setup_agent()

    # 使用不在任何供应商产品列表中的产品ID
    event = _make_replenishment_event(
        product_id="prod-unknown",
        product_name="UnknownProduct",
        reorder_quantity=50,
        total_cost=500.0,
        unit_cost=10.0,
    )
    await agent.process_event(event)

    orders = agent.get_purchase_orders()
    assert len(orders) == 1
    po = orders[0]

    # 应使用补货单价（10.0）作为报价
    assert po["unit_price"] == 10.0
    assert po["total_amount"] == 50 * 10.0
    assert po["status"] == "approved"  # 500 < 100000


# ========== test_multiple_purchase_orders ==========

async def test_multiple_purchase_orders():
    """多个补货建议生成多个采购订单"""
    agent = await _setup_agent()

    for i in range(3):
        event = _make_replenishment_event(
            product_id=f"prod-001",
            product_name=f"Widget{i}",
            reorder_quantity=100,
            total_cost=1050.0,
            unit_cost=10.50,
            proposal_id=f"prop-{i}",
        )
        await agent.process_event(event)

    orders = agent.get_purchase_orders()
    assert len(orders) == 3

    # 验证每个订单有不同的 po_id
    po_ids = [o["po_id"] for o in orders]
    assert len(set(po_ids)) == 3


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
