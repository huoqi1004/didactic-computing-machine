# -*- coding: utf-8 -*-
"""
报销Agent测试
验证报销申请、核验、审批、付款与统计分析功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.reimbursement.agent import ReimbursementAgent, ReimbursementCategory


# ========== 辅助函数 ==========

async def _setup_agent() -> ReimbursementAgent:
    """创建并初始化报销Agent（不启动主循环，仅注册身份和订阅）"""
    agent = ReimbursementAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_invoice_verified_event(
    invoice_id: str = "INV-001",
    reimb_id: str = "",
) -> Event:
    """创建发票验证事件（关联报销）"""
    return Event(
        event_type=EventType.INVOICE_VERIFIED,
        aggregate_id=invoice_id,
        aggregate_type="invoice",
        event_data={
            "invoice_id": invoice_id,
            "linked_reimbursement": reimb_id,
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "报销Agent"
    assert agent.domain == "reimbursement"
    assert agent.security_clearance == "confidential"
    assert "reimbursement_submission" in agent.capabilities
    assert "receipt_verification" in agent.capabilities
    assert "expense_validation" in agent.capabilities
    assert "reimbursement_approval" in agent.capabilities
    assert "payment_processing" in agent.capabilities
    assert "project_cost_tracking" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_category_config ==========

async def test_category_config():
    """报销类别配置正确"""
    agent = await _setup_agent()

    assert ReimbursementCategory.TRAVEL in agent.CATEGORY_CONFIG
    assert ReimbursementCategory.MEAL in agent.CATEGORY_CONFIG
    assert ReimbursementCategory.PROJECT in agent.CATEGORY_CONFIG

    # 差旅费限额5000
    assert agent.CATEGORY_CONFIG[ReimbursementCategory.TRAVEL]["limit"] == 5000
    # 餐饮费需要发票
    assert agent.CATEGORY_CONFIG[ReimbursementCategory.MEAL]["require_receipt"] is True
    # 招待费不可抵税
    assert agent.CATEGORY_CONFIG[ReimbursementCategory.ENTERTAINMENT]["tax_deductible"] is False


# ========== test_submit_reimbursement ==========

async def test_submit_reimbursement():
    """提交报销申请"""
    agent = await _setup_agent()

    request = await agent.submit_reimbursement({
        "employee_id": "EMP0001",
        "employee_name": "张伟",
        "department": "技术部",
        "category": "travel",
        "amount": 2000.0,
        "description": "北京出差交通费",
        "has_receipt": True,
    })

    assert "error" not in request
    assert request["employee_name"] == "张伟"
    assert request["category"] == "travel"
    assert request["amount"] == 2000.0
    assert request["status"] == "pending"
    assert request["reimbursement_no"].startswith("REIMB")


# ========== test_make_decision_submit_no_receipt ==========

async def test_make_decision_submit_no_receipt():
    """需要发票但未提供时决策标记问题"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "submit_reimbursement",
        "category": "travel",
        "amount": 2000.0,
        "has_receipt": False,
    })

    assert decision.decision_type == "reimbursement_submit"
    assert "拒绝提交" in decision.action
    assert "发票" in decision.reasoning or "收据" in decision.reasoning


# ========== test_make_decision_submit_over_limit ==========

async def test_make_decision_submit_over_limit():
    """超过类别限额时决策标记问题"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "submit_reimbursement",
        "category": "meal",
        "amount": 1000.0,  # 餐饮费限额500
        "has_receipt": True,
    })

    assert decision.decision_type == "reimbursement_submit"
    assert "拒绝提交" in decision.action
    assert "限额" in decision.reasoning


# ========== test_make_decision_submit_invalid_category ==========

async def test_make_decision_submit_invalid_category():
    """无效报销类别决策拒绝"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "submit_reimbursement",
        "category": "invalid_category",
        "amount": 100.0,
        "has_receipt": True,
    })

    assert decision.decision_type == "reimbursement_submit"
    assert "拒绝提交" in decision.action
    assert "无效" in decision.reasoning


# ========== test_verify_reimbursement ==========

async def test_verify_reimbursement():
    """核验报销申请"""
    agent = await _setup_agent()

    request = await agent.submit_reimbursement({
        "employee_id": "EMP0001",
        "category": "office",
        "amount": 500.0,
        "has_receipt": True,
    })
    reimb_id = request["reimbursement_id"]

    verified = await agent.verify_reimbursement(reimb_id)
    assert verified["status"] == "verified"
    assert "verified_at" in verified


# ========== test_verify_reimbursement_nonexistent ==========

async def test_verify_reimbursement_nonexistent():
    """核验不存在的报销申请"""
    agent = await _setup_agent()

    result = await agent.verify_reimbursement("nonexistent-id")
    assert "error" in result


# ========== test_approve_reimbursement ==========

async def test_approve_reimbursement():
    """审批报销申请"""
    agent = await _setup_agent()

    request = await agent.submit_reimbursement({
        "employee_id": "EMP0001",
        "category": "office",
        "amount": 500.0,
        "has_receipt": True,
    })
    await agent.verify_reimbursement(request["reimbursement_id"])

    approved = await agent.approve_reimbursement(
        request["reimbursement_id"], approved=True, remark="同意"
    )
    assert approved["status"] == "approved"
    assert approved["approval_remark"] == "同意"


# ========== test_approve_reimbursement_reject ==========

async def test_approve_reimbursement_reject():
    """拒绝报销申请"""
    agent = await _setup_agent()

    request = await agent.submit_reimbursement({
        "employee_id": "EMP0001",
        "category": "office",
        "amount": 500.0,
        "has_receipt": True,
    })

    rejected = await agent.approve_reimbursement(
        request["reimbursement_id"], approved=False, remark="不合理"
    )
    assert rejected["status"] == "rejected"


# ========== test_pay_reimbursement ==========

async def test_pay_reimbursement():
    """付款"""
    agent = await _setup_agent()

    request = await agent.submit_reimbursement({
        "employee_id": "EMP0001",
        "employee_name": "张伟",
        "category": "office",
        "amount": 500.0,
        "has_receipt": True,
    })
    await agent.verify_reimbursement(request["reimbursement_id"])
    await agent.approve_reimbursement(request["reimbursement_id"], approved=True)

    paid = await agent.pay_reimbursement(request["reimbursement_id"])
    assert paid["status"] == "paid"
    assert "paid_at" in paid


# ========== test_pay_reimbursement_not_approved ==========

async def test_pay_reimbursement_not_approved():
    """未审批的报销付款决策标记问题"""
    agent = await _setup_agent()

    request = await agent.submit_reimbursement({
        "employee_id": "EMP0001",
        "category": "office",
        "amount": 500.0,
        "has_receipt": True,
    })
    # 直接付款决策（未经审批），决策应拒绝付款
    decision = await agent.make_decision({
        "action": "pay_reimbursement",
        "reimbursement_id": request["reimbursement_id"],
    })

    assert decision.decision_type == "reimbursement_pay"
    assert "拒绝付款" in decision.action
    assert "status" in decision.reasoning or "无法付款" in decision.reasoning or "状态" in decision.reasoning


# ========== test_get_pending_reimbursements ==========

async def test_get_pending_reimbursements():
    """获取待处理报销"""
    agent = await _setup_agent()

    await agent.submit_reimbursement({
        "employee_id": "EMP0001",
        "category": "office",
        "amount": 200.0,
        "has_receipt": True,
    })
    await agent.submit_reimbursement({
        "employee_id": "EMP0002",
        "category": "transport",
        "amount": 100.0,
        "has_receipt": False,
    })

    pending = agent.get_pending_reimbursements()
    # office需要发票且提供了发票 -> 提交成功
    # transport不需要发票 -> 提交成功
    # 两条均为 pending 状态
    assert len(pending) == 2
    for r in pending:
        assert r["status"] in ("pending", "verified")


# ========== test_reimbursement_stats ==========

async def test_reimbursement_stats():
    """报销统计"""
    agent = await _setup_agent()

    await agent.submit_reimbursement({
        "employee_id": "EMP0001",
        "category": "office",
        "amount": 200.0,
        "has_receipt": True,
    })

    stats = agent.get_reimbursement_stats()
    assert stats["total_requests"] >= 1
    assert "by_category" in stats
    assert "office" in stats["by_category"]


# ========== test_register_project_and_employee ==========

async def test_register_project_and_employee():
    """注册项目和员工"""
    agent = await _setup_agent()

    project = agent.register_project("PROJ-001", "测试项目", budget=100000)
    assert project["project_id"] == "PROJ-001"
    assert project["name"] == "测试项目"
    assert project["budget"] == 100000

    employee = agent.register_employee("EMP-TEST", "测试员工", "技术部")
    assert employee["employee_id"] == "EMP-TEST"
    assert employee["name"] == "测试员工"


# ========== test_make_decision_submit ==========

async def test_make_decision_submit():
    """报销提交决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "submit_reimbursement",
        "category": "office",
        "amount": 200.0,
        "has_receipt": True,
    })

    assert decision.decision_type == "reimbursement_submit"
    assert decision.confidence > 0


# ========== test_make_decision_unknown ==========

async def test_make_decision_unknown():
    """未知操作决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"action": "unknown_action"})
    assert decision.decision_type == "reimbursement_unknown"
    assert decision.confidence == 0.0


# ========== test_invoice_verified_event ==========

async def test_invoice_verified_event():
    """发票验证事件关联报销"""
    agent = await _setup_agent()

    # 先提交报销
    request = await agent.submit_reimbursement({
        "employee_id": "EMP0001",
        "category": "office",
        "amount": 300.0,
        "has_receipt": True,
    })
    reimb_id = request["reimbursement_id"]

    # 发送发票验证事件
    event = _make_invoice_verified_event(invoice_id="INV-001", reimb_id=reimb_id)
    await agent.process_event(event)

    # 验证发票已关联
    updated = agent.get_reimbursement(reimb_id)
    assert updated["invoice_id"] == "INV-001"
    assert updated["invoice_verified"] is True


# ========== test_agent_start_stop ==========

async def test_agent_start_stop():
    """Agent启动与停止状态管理"""
    from app.core.config import AgentState

    agent = ReimbursementAgent()
    await agent.start()

    assert agent.state == AgentState.RUNNING
    assert agent.identity is not None

    await agent.stop()
    assert agent.state == AgentState.TERMINATED
