# -*- coding: utf-8 -*-
"""
资金Agent测试
验证现金流预测、支付排程、资金调拨与短期投资建议
"""
import pytest
from datetime import date, timedelta

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.treasury.agent import TreasuryAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> TreasuryAgent:
    """创建并初始化资金Agent（不启动主循环，仅注册身份和订阅）"""
    agent = TreasuryAgent()
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
    total_amount: float = 50000.0,
    supplier_id: str = "sup-001",
) -> Event:
    """创建采购订单审批事件"""
    return Event(
        event_type=EventType.PURCHASE_ORDER_APPROVED,
        aggregate_id=po_id,
        aggregate_type="purchase_order",
        event_data={
            "po_id": po_id,
            "total_amount": total_amount,
            "supplier_id": supplier_id,
            "status": "approved",
        },
    )


def _make_voucher_posted_event(
    voucher_id: str = "V-001",
    total_debit: float = 10000.0,
    total_credit: float = 10000.0,
) -> Event:
    """创建凭证过账事件"""
    return Event(
        event_type=EventType.VOUCHER_POSTED,
        aggregate_id=voucher_id,
        aggregate_type="voucher",
        event_data={
            "voucher_id": voucher_id,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "entries": [
                {"account_code": "1001", "debit": total_debit, "credit": 0},
                {"account_code": "6001", "debit": 0, "credit": total_credit},
            ],
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "资金Agent"
    assert agent.domain == "treasury"
    assert agent.security_clearance == "confidential"
    assert "cash_flow_forecasting" in agent.capabilities
    assert "payment_scheduling" in agent.capabilities
    assert "short_term_investment" in agent.capabilities
    assert "credit_management" in agent.capabilities
    assert "fund_transfer" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None
    assert agent._cash_balance > 0


# ========== test_po_approved_triggers_payment_schedule ==========

async def test_po_approved_triggers_payment_schedule():
    """采购订单审批事件触发支付排程"""
    agent = await _setup_agent()

    event = _make_po_approved_event(
        po_id="PO-001", total_amount=50000.0, supplier_id="sup-001",
    )
    await agent.process_event(event)

    payments = agent.get_payment_schedule()
    assert len(payments) == 1

    payment = payments[0]
    assert payment["po_id"] == "PO-001"
    assert payment["amount"] == 50000.0
    assert payment["status"] == "scheduled"
    assert payment["supplier_id"] == "sup-001"
    assert "payment_date" in payment

    # 验证决策历史
    assert len(agent._decision_history) == 1
    assert agent._decision_history[0].decision_type == "payment_scheduling"


# ========== test_voucher_posted_records_cash_flow ==========

async def test_voucher_posted_records_cash_flow():
    """凭证过账事件记录资金变动"""
    agent = await _setup_agent()

    initial_balance = agent._cash_balance
    event = _make_voucher_posted_event(
        voucher_id="V-001", total_debit=5000.0, total_credit=5000.0,
    )
    await agent.process_event(event)

    # 验证现金流缓存已更新
    today = date.today().isoformat()
    assert today in agent._cash_flow_cache
    record = agent._cash_flow_cache[today]
    assert record["inflow"] == 5000.0
    assert record["outflow"] == 5000.0
    assert len(record["details"]) == 1


# ========== test_cash_flow_forecast_rules ==========

async def test_cash_flow_forecast_rules():
    """规则现金流预测（LLM未启用时降级）"""
    agent = await _setup_agent()

    # 先记录一些现金流数据
    event = _make_voucher_posted_event(
        voucher_id="V-001", total_debit=3000.0, total_credit=8000.0,
    )
    await agent.process_event(event)

    forecast = await agent.get_cash_flow_forecast(days=7)

    assert forecast["horizon_days"] == 7
    assert forecast["method"] == "rules"
    assert len(forecast["forecast"]) == 7
    assert "projected_balance" in forecast
    assert forecast["confidence"] > 0

    # 验证预测数据结构
    for item in forecast["forecast"]:
        assert "date" in item
        assert "projected_inflow" in item
        assert "projected_outflow" in item
        assert "projected_net" in item


# ========== test_fund_transfer_small_amount ==========

async def test_fund_transfer_small_amount():
    """小额资金调拨自动批准"""
    agent = await _setup_agent()

    transfer = await agent.request_fund_transfer(
        amount=100000.0,
        from_account="main",
        to_account="subsidiary",
        reason="日常调拨",
    )

    assert transfer is not None
    assert transfer["amount"] == 100000.0
    assert transfer["status"] == "approved"
    assert transfer["requires_human_approval"] is False

    transfers = agent.get_fund_transfers()
    assert len(transfers) == 1


# ========== test_fund_transfer_large_amount ==========

async def test_fund_transfer_large_amount():
    """大额资金调拨需人在回路确认（>50万）"""
    agent = await _setup_agent()

    transfer = await agent.request_fund_transfer(
        amount=600000.0,
        from_account="main",
        to_account="subsidiary",
        reason="大额调拨",
    )

    assert transfer is not None
    assert transfer["amount"] == 600000.0
    assert transfer["status"] == "pending_approval"
    assert transfer["requires_human_approval"] is True
    assert transfer["amount"] > TreasuryAgent.FUND_TRANSFER_THRESHOLD


# ========== test_approve_fund_transfer ==========

async def test_approve_fund_transfer():
    """资金调拨审批通过流程"""
    agent = await _setup_agent()

    transfer = await agent.request_fund_transfer(
        amount=600000.0,
        from_account="main",
        to_account="subsidiary",
        reason="大额调拨",
    )
    transfer_id = transfer["transfer_id"]

    initial_balance = agent._cash_balance
    result = await agent.approve_fund_transfer(transfer_id, approved=True)
    assert result is True

    transfers = agent.get_fund_transfers()
    assert transfers[0]["status"] == "approved"
    assert "approved_at" in transfers[0]
    # 余额应减少
    assert agent._cash_balance == initial_balance - 600000.0


# ========== test_reject_fund_transfer ==========

async def test_reject_fund_transfer():
    """资金调拨审批拒绝流程"""
    agent = await _setup_agent()

    transfer = await agent.request_fund_transfer(
        amount=600000.0,
        from_account="main",
        to_account="subsidiary",
        reason="大额调拨",
    )
    transfer_id = transfer["transfer_id"]
    initial_balance = agent._cash_balance

    result = await agent.approve_fund_transfer(transfer_id, approved=False)
    assert result is True

    transfers = agent.get_fund_transfers()
    assert transfers[0]["status"] == "rejected"
    # 拒绝时余额不变
    assert agent._cash_balance == initial_balance


# ========== test_make_decision ==========

async def test_make_decision():
    """Agent资金决策功能"""
    agent = await _setup_agent()

    # 大额调拨
    transfer = await agent.request_fund_transfer(
        amount=600000.0,
        from_account="main",
        to_account="subsidiary",
        reason="大额调拨",
    )
    transfer_id = transfer["transfer_id"]

    decision = await agent.make_decision({"transfer_id": transfer_id})
    assert decision.decision_type == "fund_transfer_approval"
    assert decision.requires_human_approval is True
    assert "人工审批" in decision.action


# ========== test_make_decision_small_amount ==========

async def test_make_decision_small_amount():
    """小额资金调拨决策自动批准"""
    agent = await _setup_agent()

    transfer = await agent.request_fund_transfer(
        amount=100000.0,
        from_account="main",
        to_account="subsidiary",
        reason="日常调拨",
    )
    transfer_id = transfer["transfer_id"]

    decision = await agent.make_decision({"transfer_id": transfer_id})
    assert decision.decision_type == "fund_transfer_auto"
    assert decision.requires_human_approval is False
    assert "自动批准" in decision.action


# ========== test_make_decision_nonexistent ==========

async def test_make_decision_nonexistent():
    """对不存在的资金调拨做出决策返回错误"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"transfer_id": "nonexistent"})
    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_approve_nonexistent_transfer ==========

async def test_approve_nonexistent_transfer():
    """审批不存在的资金调拨返回False"""
    agent = await _setup_agent()

    result = await agent.approve_fund_transfer("nonexistent", approved=True)
    assert result is False


# ========== test_payment_schedule_filter ==========

async def test_payment_schedule_filter():
    """支付排程按状态过滤"""
    agent = await _setup_agent()

    # 创建两笔支付排程
    event1 = _make_po_approved_event(po_id="PO-001", total_amount=50000.0)
    event2 = _make_po_approved_event(po_id="PO-002", total_amount=80000.0)
    await agent.process_event(event1)
    await agent.process_event(event2)

    all_payments = agent.get_payment_schedule()
    assert len(all_payments) == 2

    scheduled = agent.get_payment_schedule(status="scheduled")
    assert len(scheduled) == 2
