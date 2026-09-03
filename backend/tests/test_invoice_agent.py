# -*- coding: utf-8 -*-
"""
发票Agent测试
验证发票开具、验证、红冲与统计分析功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.invoice.agent import InvoiceAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> InvoiceAgent:
    """创建并初始化发票Agent（不启动主循环，仅注册身份和订阅）"""
    agent = InvoiceAgent()
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
    amount: float = 10000.0,
) -> Event:
    """创建凭证过账事件"""
    return Event(
        event_type=EventType.VOUCHER_POSTED,
        aggregate_id=voucher_id,
        aggregate_type="voucher",
        event_data={
            "voucher_id": voucher_id,
            "amount": amount,
            "counterparty": "测试客户",
            "summary": "销售商品",
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "发票Agent"
    assert agent.domain == "invoice"
    assert agent.security_clearance == "confidential"
    assert "invoice_issuance" in agent.capabilities
    assert "invoice_verification" in agent.capabilities
    assert "invoice_management" in agent.capabilities
    assert "invoice_statistics" in agent.capabilities
    assert "tax_rate_matching" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_invoice_types_config ==========

async def test_invoice_types_config():
    """发票类型配置正确"""
    agent = await _setup_agent()

    assert "vat_special" in agent.INVOICE_TYPES
    assert "vat_normal" in agent.INVOICE_TYPES
    assert "electronic" in agent.INVOICE_TYPES
    assert "electronic_special" in agent.INVOICE_TYPES

    # 增值税专用发票税率为13%
    assert agent.INVOICE_TYPES["vat_special"]["tax_rate"] == 0.13
    # 增值税普通发票税率为3%
    assert agent.INVOICE_TYPES["vat_normal"]["tax_rate"] == 0.03


# ========== test_issue_invoice ==========

async def test_issue_invoice():
    """开具发票"""
    agent = await _setup_agent()

    invoice = await agent.issue_invoice({
        "invoice_type": "vat_special",
        "buyer_name": "测试客户",
        "buyer_tax_id": "91100000XXXXXXXXXX",
        "amount": 10000.0,
        "description": "咨询服务费",
    })

    assert "error" not in invoice
    assert invoice["invoice_type"] == "vat_special"
    assert invoice["buyer_name"] == "测试客户"
    assert invoice["amount"] == 10000.0
    assert invoice["tax_rate"] == 0.13
    assert invoice["tax_amount"] == 1300.0  # 10000 * 0.13
    assert invoice["total"] == 11300.0      # 10000 + 1300
    assert invoice["status"] == "issued"
    assert invoice["invoice_no"].startswith("INV")


# ========== test_issue_invoice_auto_generated ==========

async def test_issue_invoice_auto_generated():
    """自动生成的发票不校验购买方税号"""
    agent = await _setup_agent()

    invoice = await agent.issue_invoice({
        "invoice_type": "vat_normal",
        "buyer_name": "自动客户",
        "amount": 5000.0,
        "description": "商品销售",
        "auto_generated": True,
    })

    assert "error" not in invoice
    assert invoice["auto_generated"] is True
    assert invoice["status"] == "issued"


# ========== test_verify_invoice ==========

async def test_verify_invoice():
    """验证发票"""
    agent = await _setup_agent()

    invoice = await agent.issue_invoice({
        "invoice_type": "vat_special",
        "buyer_name": "验证客户",
        "buyer_tax_id": "91100000XXXXXXXXXX",
        "amount": 8000.0,
        "description": "技术服务",
    })
    invoice_id = invoice["invoice_id"]

    verified = await agent.verify_invoice(invoice_id)
    assert verified["status"] == "verified"
    assert "verified_at" in verified


# ========== test_verify_invoice_nonexistent ==========

async def test_verify_invoice_nonexistent():
    """验证不存在的发票"""
    agent = await _setup_agent()

    result = await agent.verify_invoice("nonexistent-id")
    assert "error" in result
    assert "不存在" in result["error"]


# ========== test_cancel_invoice ==========

async def test_cancel_invoice():
    """红冲发票"""
    agent = await _setup_agent()

    invoice = await agent.issue_invoice({
        "invoice_type": "vat_normal",
        "buyer_name": "红冲客户",
        "amount": 3000.0,
        "description": "测试商品",
    })
    invoice_id = invoice["invoice_id"]

    cancelled = await agent.cancel_invoice(invoice_id, reason="客户退货")
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_reason"] == "客户退货"
    assert "cancelled_at" in cancelled


# ========== test_cancel_invoice_nonexistent ==========

async def test_cancel_invoice_nonexistent():
    """红冲不存在的发票"""
    agent = await _setup_agent()

    result = await agent.cancel_invoice("nonexistent-id")
    assert "error" in result


# ========== test_get_invoices_filter ==========

async def test_get_invoices_filter():
    """发票列表过滤"""
    agent = await _setup_agent()

    # 开具两张不同类型的发票
    await agent.issue_invoice({
        "invoice_type": "vat_special",
        "buyer_name": "客户A",
        "amount": 10000.0,
        "description": "服务A",
    })
    await agent.issue_invoice({
        "invoice_type": "vat_normal",
        "buyer_name": "客户B",
        "amount": 5000.0,
        "description": "服务B",
    })

    all_invoices = agent.get_invoices()
    assert len(all_invoices) == 2

    special_only = agent.get_invoices(invoice_type="vat_special")
    assert len(special_only) == 1
    assert special_only[0]["invoice_type"] == "vat_special"


# ========== test_invoice_stats ==========

async def test_invoice_stats():
    """发票统计"""
    agent = await _setup_agent()

    await agent.issue_invoice({
        "invoice_type": "vat_special",
        "buyer_name": "客户A",
        "amount": 10000.0,
        "description": "服务A",
    })
    await agent.issue_invoice({
        "invoice_type": "vat_normal",
        "buyer_name": "客户B",
        "amount": 5000.0,
        "description": "服务B",
    })

    stats = agent.get_invoice_stats()
    assert stats["total_invoices"] == 2
    assert stats["issued_count"] == 2
    assert stats["total_amount"] == 15000.0
    assert stats["total_tax"] > 0
    assert "vat_special" in stats["by_type"]
    assert "vat_normal" in stats["by_type"]


# ========== test_make_decision_issue ==========

async def test_make_decision_issue():
    """发票开具决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "issue_invoice",
        "invoice_type": "vat_special",
        "amount": 10000.0,
    })

    assert decision.decision_type == "invoice_issue"
    assert decision.confidence == 0.95
    assert "增值税专用发票" in decision.action


# ========== test_make_decision_verify ==========

async def test_make_decision_verify():
    """发票验证决策"""
    agent = await _setup_agent()

    invoice = await agent.issue_invoice({
        "invoice_type": "vat_normal",
        "buyer_name": "决策客户",
        "amount": 3000.0,
        "description": "测试",
    })

    decision = await agent.make_decision({
        "action": "verify_invoice",
        "invoice_id": invoice["invoice_id"],
    })

    assert decision.decision_type == "invoice_verify"
    assert decision.confidence >= 0.5


# ========== test_make_decision_verify_nonexistent ==========

async def test_make_decision_verify_nonexistent():
    """验证不存在的发票决策返回错误"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "verify_invoice",
        "invoice_id": "nonexistent",
    })

    assert decision.decision_type == "invoice_verify"
    assert decision.confidence == 0.0


# ========== test_make_decision_unknown ==========

async def test_make_decision_unknown():
    """未知操作决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"action": "unknown_action"})
    assert decision.decision_type == "invoice_unknown"
    assert decision.confidence == 0.0


# ========== test_voucher_posted_auto_issue ==========

async def test_voucher_posted_auto_issue():
    """凭证过账事件触发自动开票"""
    agent = await _setup_agent()

    event = _make_voucher_posted_event(voucher_id="V-001", amount=10000.0)
    await agent.process_event(event)

    invoices = agent.get_invoices()
    assert len(invoices) == 1
    assert invoices[0]["auto_generated"] is True
    assert invoices[0]["amount"] == 10000.0


# ========== test_agent_start_stop ==========

async def test_agent_start_stop():
    """Agent启动与停止状态管理"""
    from app.core.config import AgentState

    agent = InvoiceAgent()
    await agent.start()

    assert agent.state == AgentState.RUNNING
    assert agent.identity is not None
    assert agent.identity.did is not None

    await agent.stop()
    assert agent.state == AgentState.TERMINATED
