# -*- coding: utf-8 -*-
"""
供应规划Agent测试
验证供应商绩效评估、供应风险预警、供应商集中度风险、LLM降级与查询接口
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.supply.agent import SupplyPlanningAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> SupplyPlanningAgent:
    """创建并初始化供应规划Agent（不启动主循环，仅注册身份和订阅）"""
    agent = SupplyPlanningAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_po_approved_event(
    po_id: str = "PO-001",
    supplier_id: str = "sup-001",
    supplier_name: str = "供应商A",
    total_amount: float = 50000.0,
) -> Event:
    """创建采购订单审批事件"""
    return Event(
        event_type=EventType.PURCHASE_ORDER_APPROVED,
        aggregate_id=po_id,
        aggregate_type="purchase_order",
        event_data={
            "po_id": po_id,
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "total_amount": total_amount,
            "status": "approved",
        },
    )


def _make_delivery_delayed_event(
    po_id: str = "PO-001",
    supplier_id: str = "sup-001",
    supplier_name: str = "供应商A",
) -> Event:
    """创建交期延期事件"""
    return Event(
        event_type=EventType.DELIVERY_DELAYED,
        aggregate_id=po_id,
        aggregate_type="purchase_order",
        event_data={
            "po_id": po_id,
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "delay_days": 5,
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "供应规划Agent"
    assert agent.domain == "supply"
    assert agent.security_clearance == "internal"
    assert "supplier_evaluation" in agent.capabilities
    assert "supply_optimization" in agent.capabilities
    assert "risk_assessment" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_supplier_transaction_recording ==========

async def test_supplier_transaction_recording():
    """采购订单审批事件记录供应商交易"""
    agent = await _setup_agent()

    event = _make_po_approved_event(supplier_id="sup-001")
    await agent.process_event(event)

    assert agent._supplier_transactions["sup-001"] == 1

    # 应生成评估记录
    evaluations = agent.get_supplier_evaluations()
    assert len(evaluations) == 1
    assert evaluations[0]["supplier_id"] == "sup-001"
    assert "overall_score" in evaluations[0]
    assert "on_time_rate" in evaluations[0]


# ========== test_supplier_evaluation_rules ==========

async def test_supplier_evaluation_rules():
    """规则模式评估供应商绩效"""
    agent = await _setup_agent()

    # 记录3次交易
    for i in range(3):
        event = _make_po_approved_event(
            po_id=f"PO-{i:03d}", supplier_id="sup-001"
        )
        await agent.process_event(event)

    evaluations = agent.get_supplier_evaluations()
    assert len(evaluations) == 1
    evaluation = evaluations[0]

    assert evaluation["method"] == "rules"
    assert evaluation["on_time_rate"] == 1.0  # 无延期
    assert evaluation["overall_score"] > 0
    assert evaluation["risk_level"] == "low"
    assert evaluation["total_transactions"] == 3
    assert evaluation["delay_count"] == 0


# ========== test_delivery_delay_risk_warning ==========

async def test_delivery_delay_risk_warning():
    """交期延期事件触发供应风险预警"""
    agent = await _setup_agent()

    # 先记录一次正常交易
    event1 = _make_po_approved_event(po_id="PO-001", supplier_id="sup-001")
    await agent.process_event(event1)

    # 发送交期延期事件
    event2 = _make_delivery_delayed_event(po_id="PO-001", supplier_id="sup-001")
    await agent.process_event(event2)

    risks = agent.get_supply_risks()
    assert len(risks) >= 1

    risk = risks[0]
    assert risk["supplier_id"] == "sup-001"
    assert risk["risk_type"] == "delivery_delay"
    assert risk["severity"] == "high"
    assert "delay_count" in risk


# ========== test_repeated_delays_human_approval ==========

async def test_repeated_delays_human_approval():
    """累计延期3次以上需人工审批"""
    agent = await _setup_agent()

    # 先记录1次正常交易
    await agent.process_event(
        _make_po_approved_event(po_id="PO-001", supplier_id="sup-001")
    )

    # 发送3次延期事件
    for i in range(3):
        await agent.process_event(
            _make_delivery_delayed_event(
                po_id=f"PO-{i+1:03d}", supplier_id="sup-001"
            )
        )

    # 最后一次延期决策应需要人工审批
    decisions = agent._decision_history
    delay_decisions = [
        d for d in decisions if d.decision_type == "supply_risk_warning"
    ]
    assert len(delay_decisions) >= 3
    # 第三次延期（delay_count=3）应需要人工审批
    assert delay_decisions[-1].requires_human_approval is True


# ========== test_make_decision ==========

async def test_make_decision():
    """Agent供应规划决策功能"""
    agent = await _setup_agent()

    # 记录交易并评估
    await agent.process_event(
        _make_po_approved_event(supplier_id="sup-001")
    )

    decision = await agent.make_decision({"supplier_id": "sup-001"})
    assert decision.decision_type == "supply_assessment"
    assert decision.confidence > 0
    assert decision.requires_human_approval is False
    assert "sup-001" in decision.context.get("supplier_id", "")


# ========== test_make_decision_not_found ==========

async def test_make_decision_not_found():
    """不存在的供应商ID返回错误决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"supplier_id": "nonexistent"})
    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_make_decision_high_risk ==========

async def test_make_decision_high_risk():
    """高风险供应商决策需人工审批"""
    agent = await _setup_agent()

    # 1次正常交易 + 多次延期，降低准时率
    await agent.process_event(
        _make_po_approved_event(po_id="PO-001", supplier_id="sup-001")
    )

    # 发送多次延期，使准时率低于0.8
    for i in range(2):
        await agent.process_event(
            _make_delivery_delayed_event(
                po_id=f"PO-D{i+1:03d}", supplier_id="sup-001"
            )
        )

    decision = await agent.make_decision({"supplier_id": "sup-001"})
    # 准时率 = (3-2)/3 = 0.33 < 0.8，高风险
    assert decision.decision_type == "supply_risk_mitigation"
    assert decision.requires_human_approval is True


# ========== test_concentration_risk ==========

async def test_concentration_risk():
    """供应商集中度风险检测"""
    agent = await _setup_agent()

    # 让sup-001占超过60%的交易
    for i in range(7):
        await agent.process_event(
            _make_po_approved_event(
                po_id=f"PO-A{i:03d}", supplier_id="sup-001"
            )
        )
    for i in range(3):
        await agent.process_event(
            _make_po_approved_event(
                po_id=f"PO-B{i:03d}", supplier_id="sup-002"
            )
        )

    # 执行on_tick触发集中度检查
    await agent.on_tick()

    risks = agent.get_supply_risks()
    concentration_risks = [r for r in risks if r["risk_type"] == "concentration"]
    assert len(concentration_risks) >= 1
    assert concentration_risks[0]["supplier_id"] == "sup-001"
    assert concentration_risks[0]["concentration_ratio"] > 0.6


# ========== test_get_evaluations_filter ==========

async def test_get_evaluations_filter():
    """供应商评估列表按ID过滤"""
    agent = await _setup_agent()

    await agent.process_event(
        _make_po_approved_event(supplier_id="sup-001")
    )
    await agent.process_event(
        _make_po_approved_event(supplier_id="sup-002")
    )

    all_evals = agent.get_supplier_evaluations()
    assert len(all_evals) == 2

    sup_001 = agent.get_supplier_evaluations(supplier_id="sup-001")
    assert len(sup_001) == 1
    assert sup_001[0]["supplier_id"] == "sup-001"


# ========== test_get_risks_filter ==========

async def test_get_risks_filter():
    """供应风险列表按类型过滤"""
    agent = await _setup_agent()

    await agent.process_event(
        _make_po_approved_event(supplier_id="sup-001")
    )
    await agent.process_event(
        _make_delivery_delayed_event(supplier_id="sup-001")
    )

    all_risks = agent.get_supply_risks()
    assert len(all_risks) >= 1

    delay_risks = agent.get_supply_risks(risk_type="delivery_delay")
    assert all(r["risk_type"] == "delivery_delay" for r in delay_risks)
    assert len(delay_risks) >= 1


# ========== test_llm_fallback ==========

async def test_llm_fallback(monkeypatch):
    """LLM不可用时降级到规则模式"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "llm_enabled", False)

    agent = await _setup_agent()

    # LLM评估应返回None
    result = await agent._evaluate_supplier_llm("sup-001")
    assert result is None

    # 规则评估应正常工作
    agent._supplier_transactions["sup-001"] = 5
    agent._supplier_delays["sup-001"] = 1
    evaluation = agent._evaluate_supplier_rules("sup-001")
    assert evaluation["method"] == "rules"
    assert evaluation["overall_score"] > 0
    assert "reasoning" in evaluation
