# -*- coding: utf-8 -*-
"""
客户关系Agent测试
验证客户档案管理、商机跟踪、客户分级与联系人管理
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.crm.agent import CRMAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> CRMAgent:
    """创建并初始化CRM Agent（不启动主循环，仅注册身份和订阅）"""
    agent = CRMAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "客户关系Agent"
    assert agent.domain == "crm"
    assert agent.security_clearance == "internal"
    assert "customer_management" in agent.capabilities
    assert "opportunity_tracking" in agent.capabilities
    assert "customer_segmentation" in agent.capabilities
    assert "contact_management" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载正确"""
    agent = await _setup_agent()

    # 5个种子客户
    customers = agent.get_customers()
    assert len(customers) == 5

    # 5个商机
    opportunities = agent.get_opportunities()
    assert len(opportunities) == 5

    # 5个联系人
    contacts = agent.get_contacts()
    assert len(contacts) == 5


# ========== test_get_customers_filter ==========

async def test_get_customers_filter():
    """客户列表过滤"""
    agent = await _setup_agent()

    # 按分级过滤
    a_customers = agent.get_customers(segment="A")
    assert len(a_customers) == 2

    b_customers = agent.get_customers(segment="B")
    assert len(b_customers) == 2

    c_customers = agent.get_customers(segment="C")
    assert len(c_customers) == 1

    # 按行业过滤
    tech_customers = agent.get_customers(industry="信息技术")
    assert len(tech_customers) == 1


# ========== test_get_customer_detail ==========

async def test_get_customer_detail():
    """获取客户详情（含联系人和商机）"""
    agent = await _setup_agent()

    customers = agent.get_customers()
    first_customer = customers[0]
    customer_id = first_customer["customer_id"]

    detail = agent.get_customer(customer_id)
    assert detail is not None
    assert "contacts" in detail
    assert "opportunities" in detail
    assert len(detail["contacts"]) >= 1
    assert len(detail["opportunities"]) >= 1


# ========== test_get_customer_nonexistent ==========

async def test_get_customer_nonexistent():
    """获取不存在的客户"""
    agent = await _setup_agent()

    result = agent.get_customer("NONEXISTENT")
    assert result is None


# ========== test_create_customer ==========

async def test_create_customer():
    """创建客户档案"""
    agent = await _setup_agent()

    customer = agent.create_customer(
        name="新客户公司",
        industry="教育",
        segment="B",
        contact_name="联系人A",
        contact_phone="13900000000",
    )

    assert customer["name"] == "新客户公司"
    assert customer["industry"] == "教育"
    assert customer["segment"] == "B"
    assert customer["status"] == "active"
    assert customer["customer_id"].startswith("CUST-")
    # 自动创建主联系人
    assert "contacts" in customer
    assert len(customer["contacts"]) == 1


# ========== test_update_customer ==========

async def test_update_customer():
    """更新客户信息"""
    agent = await _setup_agent()

    customers = agent.get_customers()
    customer_id = customers[0]["customer_id"]

    updated = agent.update_customer(customer_id, segment="A", status="vip")
    assert updated["segment"] == "A"
    assert updated["status"] == "vip"


# ========== test_create_opportunity ==========

async def test_create_opportunity():
    """创建商机"""
    agent = await _setup_agent()

    customers = agent.get_customers()
    customer_id = customers[0]["customer_id"]

    opp = agent.create_opportunity(
        customer_id=customer_id,
        name="新项目商机",
        amount=200000,
        stage="qualified",
        expected_close_date="2026-12-31",
    )

    assert opp["name"] == "新项目商机"
    assert opp["amount"] == 200000
    assert opp["status"] == "open"
    assert opp["opportunity_id"].startswith("OPP-")


# ========== test_update_opportunity_stage ==========

async def test_update_opportunity_stage():
    """更新商机阶段"""
    agent = await _setup_agent()

    opportunities = agent.get_opportunities(status="open")
    assert len(opportunities) > 0
    opp_id = opportunities[0]["opportunity_id"]

    updated = agent.update_opportunity_stage(opp_id, "won")
    assert updated["stage"] == "won"
    assert updated["status"] == "won"


# ========== test_add_contact ==========

async def test_add_contact():
    """添加联系人"""
    agent = await _setup_agent()

    customers = agent.get_customers()
    customer_id = customers[0]["customer_id"]

    contact = agent.add_contact(
        customer_id=customer_id,
        name="新联系人",
        title="技术经理",
        phone="13800000000",
        email="new@example.com",
    )

    assert contact["name"] == "新联系人"
    assert contact["customer_id"] == customer_id
    assert contact["contact_id"].startswith("CONT-")


# ========== test_customer_segments ==========

async def test_customer_segments():
    """客户分级统计"""
    agent = await _setup_agent()

    segments = agent.get_customer_segments()
    assert segments["total"] == 5
    assert segments["segments"]["A"] == 2
    assert segments["segments"]["B"] == 2
    assert segments["segments"]["C"] == 1


# ========== test_get_stats ==========

async def test_get_stats():
    """获取CRM统计信息"""
    agent = await _setup_agent()

    stats = agent.get_stats()
    assert stats["customers"] == 5
    assert stats["opportunities"] == 5
    assert "win_rate" in stats
    assert "total_pipeline_amount" in stats
    assert "segments" in stats


# ========== test_make_decision_create_opportunity ==========

async def test_make_decision_create_opportunity():
    """创建商机决策"""
    agent = await _setup_agent()

    customers = agent.get_customers(segment="A")
    customer_id = customers[0]["customer_id"]

    decision = await agent.make_decision({
        "action": "create_opportunity",
        "customer_id": customer_id,
        "amount": 600000,
    })

    # A级客户高金额商机高优先级
    assert decision.decision_type == "opportunity_priority"
    assert decision.confidence == 0.9


# ========== test_make_decision_update_segment ==========

async def test_make_decision_update_segment():
    """更新客户分级决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "update_segment",
        "customer_id": "CUST-001",
        "segment": "A",
    })

    assert decision.decision_type == "customer_segmentation"
    assert decision.confidence == 0.8


# ========== test_make_decision_default ==========

async def test_make_decision_default():
    """默认决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"action": "unknown_action"})
    assert decision.decision_type == "crm_general"
    assert decision.confidence == 0.7


# ========== test_opportunity_won_event ==========

async def test_opportunity_won_event():
    """商机赢单事件处理"""
    agent = await _setup_agent()

    opportunities = agent.get_opportunities(status="open")
    assert len(opportunities) > 0
    opp_id = opportunities[0]["opportunity_id"]

    event = Event(
        event_type=EventType.OPPORTUNITY_WON,
        aggregate_id=opp_id,
        aggregate_type="opportunity",
        event_data={
            "opportunity_id": opp_id,
            "actual_amount": 500000,
        },
    )
    await agent.process_event(event)

    opp = agent.get_opportunities(status="won")
    won_ids = [o["opportunity_id"] for o in opp]
    assert opp_id in won_ids


# ========== test_customer_created_event ==========

async def test_customer_created_event():
    """客户创建事件同步"""
    agent = await _setup_agent()

    initial_count = len(agent.get_customers())

    event = Event(
        event_type=EventType.CUSTOMER_CREATED,
        aggregate_id="CUST-EXT-001",
        aggregate_type="customer",
        event_data={
            "customer_id": "CUST-EXT-001",
            "name": "外部客户",
            "industry": "物流",
            "segment": "B",
            "status": "active",
        },
    )
    await agent.process_event(event)

    assert len(agent.get_customers()) == initial_count + 1
    customer = agent.get_customer("CUST-EXT-001")
    assert customer is not None
    assert customer["name"] == "外部客户"


# ========== test_agent_start_stop ==========

async def test_agent_start_stop():
    """Agent启动与停止状态管理"""
    from app.core.config import AgentState

    agent = CRMAgent()
    await agent.start()

    assert agent.state == AgentState.RUNNING
    assert agent.identity is not None

    await agent.stop()
    assert agent.state == AgentState.TERMINATED
