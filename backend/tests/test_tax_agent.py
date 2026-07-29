# -*- coding: utf-8 -*-
"""
税务Agent测试
验证增值税计算、税务申报、税务风险预警与税务筹划
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.tax.agent import TaxAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> TaxAgent:
    """创建并初始化税务Agent（不启动主循环，仅注册身份和订阅）"""
    agent = TaxAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


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


def _make_po_approved_event(
    po_id: str = "PO-001",
    total_amount: float = 50000.0,
) -> Event:
    """创建采购订单审批事件"""
    return Event(
        event_type=EventType.PURCHASE_ORDER_APPROVED,
        aggregate_id=po_id,
        aggregate_type="purchase_order",
        event_data={
            "po_id": po_id,
            "total_amount": total_amount,
            "supplier_id": "sup-001",
            "status": "approved",
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "税务Agent"
    assert agent.domain == "tax"
    assert agent.security_clearance == "confidential"
    assert "tax_calculation" in agent.capabilities
    assert "tax_declaration" in agent.capabilities
    assert "tax_planning" in agent.capabilities
    assert "risk_warning" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_tax_rates_default ==========

async def test_tax_rates_default():
    """默认税率表正确"""
    agent = await _setup_agent()

    rates = agent.get_tax_rates()
    assert rates["vat_standard"] == 0.13
    assert rates["vat_reduced_1"] == 0.09
    assert rates["vat_reduced_2"] == 0.06
    assert rates["corporate_income"] == 0.25


# ========== test_voucher_posted_calculates_output_tax ==========

async def test_voucher_posted_calculates_output_tax():
    """凭证过账事件触发销项税额计算"""
    agent = await _setup_agent()

    event = _make_voucher_posted_event(
        voucher_id="V-001", total_debit=10000.0, total_credit=10000.0,
    )
    await agent.process_event(event)

    records = agent.get_tax_records()
    assert len(records) == 1

    record = records[0]
    assert record["tax_type"] == "output_vat"
    assert record["source_id"] == "V-001"
    assert record["amount"] == 10000.0
    assert record["tax_amount"] > 0
    assert record["vat_rate"] == 0.13  # 默认标准税率

    # 验证销项税累计
    assert agent._output_tax_total == record["tax_amount"]


# ========== test_po_approved_calculates_input_tax ==========

async def test_po_approved_calculates_input_tax():
    """采购订单审批事件触发进项税额计算"""
    agent = await _setup_agent()

    event = _make_po_approved_event(po_id="PO-001", total_amount=50000.0)
    await agent.process_event(event)

    records = agent.get_tax_records()
    assert len(records) == 1

    record = records[0]
    assert record["tax_type"] == "input_vat"
    assert record["source_id"] == "PO-001"
    assert record["amount"] == 50000.0
    assert record["tax_amount"] > 0
    assert record["vat_rate"] == 0.13

    # 验证进项税累计
    assert agent._input_tax_total == record["tax_amount"]


# ========== test_tax_records_filter ==========

async def test_tax_records_filter():
    """税务记录按类型过滤"""
    agent = await _setup_agent()

    # 销项税
    event1 = _make_voucher_posted_event(voucher_id="V-001", total_debit=10000.0)
    await agent.process_event(event1)

    # 进项税
    event2 = _make_po_approved_event(po_id="PO-001", total_amount=50000.0)
    await agent.process_event(event2)

    all_records = agent.get_tax_records()
    assert len(all_records) == 2

    output_records = agent.get_tax_records(tax_type="output_vat")
    assert len(output_records) == 1

    input_records = agent.get_tax_records(tax_type="input_vat")
    assert len(input_records) == 1


# ========== test_generate_tax_declaration ==========

async def test_generate_tax_declaration():
    """生成税务申报草稿"""
    agent = await _setup_agent()

    # 产生一些税务记录
    await agent.process_event(_make_voucher_posted_event(
        voucher_id="V-001", total_debit=10000.0))
    await agent.process_event(_make_po_approved_event(
        po_id="PO-001", total_amount=50000.0))

    declaration = await agent.generate_tax_declaration(period="2026-07")

    assert declaration["period"] == "2026-07"
    assert declaration["status"] == "draft"
    assert declaration["input_tax"] > 0
    assert declaration["output_tax"] > 0
    assert declaration["payable_tax"] >= 0
    assert "declaration_id" in declaration

    # 验证申报记录
    declarations = agent.get_tax_declarations()
    assert len(declarations) == 1


# ========== test_tax_declaration_default_period ==========

async def test_tax_declaration_default_period():
    """生成税务申报使用默认期间"""
    agent = await _setup_agent()

    await agent.process_event(_make_voucher_posted_event(
        voucher_id="V-001", total_debit=10000.0))

    declaration = await agent.generate_tax_declaration()

    assert declaration["period"] != ""
    assert len(declaration["period"]) == 7  # YYYY-MM格式


# ========== test_calculate_tax_sync ==========

async def test_calculate_tax_sync():
    """同步税额计算方法"""
    agent = await _setup_agent()

    voucher_data = {"total_debit": 11300.0, "total_credit": 11300.0}
    result = agent.calculate_tax(voucher_data)

    assert result["amount"] == 11300.0
    assert result["vat_rate"] == 0.13
    assert result["tax_amount"] > 0
    assert result["tax_type"] == "output_vat"


# ========== test_make_decision ==========

async def test_make_decision():
    """Agent税务决策功能"""
    agent = await _setup_agent()

    event = _make_voucher_posted_event(
        voucher_id="V-001", total_debit=10000.0)
    await agent.process_event(event)

    records = agent.get_tax_records()
    record_id = records[0]["record_id"]

    decision = await agent.make_decision({"record_id": record_id})
    assert decision.decision_type == "tax_assessment"
    assert decision.confidence > 0
    assert "确认" in decision.action


# ========== test_make_decision_nonexistent ==========

async def test_make_decision_nonexistent():
    """对不存在的税务记录做出决策返回错误"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"record_id": "nonexistent"})
    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_tax_planning_suggestion ==========

async def test_tax_planning_suggestion():
    """税务筹划建议"""
    agent = await _setup_agent()

    # 产生销项税
    await agent.process_event(_make_voucher_posted_event(
        voucher_id="V-001", total_debit=100000.0))

    suggestion = await agent.suggest_tax_planning()

    assert suggestion is not None
    assert "suggestion" in suggestion
    assert "method" in suggestion
    assert "tax_burden" in suggestion
