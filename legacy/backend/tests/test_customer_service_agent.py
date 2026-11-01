# -*- coding: utf-8 -*-
"""
客服Agent测试
验证工单管理、投诉处理、满意度跟踪、FAQ知识库与决策功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.customer_service.agent import CustomerServiceAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> CustomerServiceAgent:
    """创建并初始化客服Agent（不启动主循环，仅注册身份和订阅）"""
    agent = CustomerServiceAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_ticket_created_event(
    ticket_id: str = "TKT-EVT-001",
    customer_id: str = "CUST-001",
    customer_name: str = "测试客户",
    subject: str = "测试工单",
    priority: str = "high",
) -> Event:
    """创建工单创建事件"""
    return Event(
        event_type=EventType.TICKET_CREATED,
        aggregate_id=ticket_id,
        aggregate_type="ticket",
        event_data={
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "subject": subject,
            "description": "测试工单描述",
            "priority": priority,
            "category": "技术支持",
            "status": "open",
        },
    )


def _make_ticket_resolved_event(
    ticket_id: str = "TKT-EVT-001",
    resolution: str = "问题已解决",
) -> Event:
    """创建工单解决事件"""
    return Event(
        event_type=EventType.TICKET_RESOLVED,
        aggregate_id=ticket_id,
        aggregate_type="ticket",
        event_data={
            "ticket_id": ticket_id,
            "resolution": resolution,
        },
    )


def _make_complaint_received_event(
    complaint_id: str = "COMP-EVT-001",
    severity: str = "high",
) -> Event:
    """创建投诉接收事件"""
    return Event(
        event_type=EventType.COMPLAINT_RECEIVED,
        aggregate_id=complaint_id,
        aggregate_type="complaint",
        event_data={
            "complaint_id": complaint_id,
            "customer_id": "CUST-001",
            "customer_name": "测试客户",
            "subject": "测试投诉",
            "description": "测试投诉描述",
            "severity": severity,
            "category": "服务",
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "客服Agent"
    assert agent.domain == "customer_service"
    assert agent.security_clearance == "internal"
    assert "ticket_management" in agent.capabilities
    assert "complaint_handling" in agent.capabilities
    assert "satisfaction_tracking" in agent.capabilities
    assert "knowledge_support" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载：5个FAQ和3个工单"""
    agent = await _setup_agent()

    tickets = agent.get_tickets()
    assert len(tickets) == 3

    # 验证种子工单状态
    in_progress = agent.get_tickets(status="in_progress")
    assert len(in_progress) == 1

    open_tickets = agent.get_tickets(status="open")
    assert len(open_tickets) == 1

    resolved = agent.get_tickets(status="resolved")
    assert len(resolved) == 1

    # 验证FAQ数量
    faq_results = agent.search_faq("系统")  # 搜索常见关键词
    assert len(agent._faq) == 5


# ========== test_ticket_created_event ==========

async def test_ticket_created_event():
    """工单创建事件同步到本地"""
    agent = await _setup_agent()

    event = _make_ticket_created_event(
        ticket_id="TKT-EVT-001",
        customer_id="CUST-001",
        subject="外部系统工单",
    )
    await agent.process_event(event)

    ticket = agent.get_ticket("TKT-EVT-001")
    assert ticket is not None
    assert ticket["subject"] == "外部系统工单"
    assert ticket["customer_id"] == "CUST-001"
    assert ticket["status"] == "open"

    # 重复事件不应重复创建
    await agent.process_event(event)
    all_tickets = agent.get_tickets()
    evt_tickets = [t for t in all_tickets if t["ticket_id"] == "TKT-EVT-001"]
    assert len(evt_tickets) == 1


# ========== test_ticket_resolved_event ==========

async def test_ticket_resolved_event():
    """工单解决事件更新状态"""
    agent = await _setup_agent()

    # 先创建工单
    ticket = agent.create_ticket(
        customer_id="CUST-001",
        subject="待解决工单",
        description="描述",
        priority="medium",
        category="功能使用",
    )

    # 发送解决事件
    event = _make_ticket_resolved_event(
        ticket_id=ticket["ticket_id"],
        resolution="已修复",
    )
    await agent.process_event(event)

    updated = agent.get_ticket(ticket["ticket_id"])
    assert updated["status"] == "resolved"
    assert updated["resolution"] == "已修复"
    assert updated["resolved_at"] is not None


# ========== test_complaint_received_event ==========

async def test_complaint_received_event():
    """投诉接收事件同步到本地"""
    agent = await _setup_agent()

    event = _make_complaint_received_event(
        complaint_id="COMP-EVT-001",
        severity="high",
    )
    await agent.process_event(event)

    complaints = agent.get_complaints()
    assert len(complaints) >= 1

    complaint = None
    for c in complaints:
        if c["complaint_id"] == "COMP-EVT-001":
            complaint = c
            break
    assert complaint is not None
    assert complaint["severity"] == "high"
    assert complaint["status"] == "open"

    # 重复事件不应重复创建
    await agent.process_event(event)
    assert len(agent.get_complaints()) == 1


# ========== test_create_ticket ==========

async def test_create_ticket():
    """创建工单"""
    agent = await _setup_agent()

    ticket = agent.create_ticket(
        customer_id="CUST-001",
        subject="新工单",
        description="工单描述",
        priority="medium",
        category="技术支持",
    )
    assert ticket["ticket_id"].startswith("TKT-")
    assert ticket["status"] == "open"
    assert ticket["priority"] == "medium"
    assert ticket["assigned_agent"] is None
    assert ticket["resolved_at"] is None

    # 验证已加入工单列表
    found = agent.get_ticket(ticket["ticket_id"])
    assert found is not None
    assert found["subject"] == "新工单"


# ========== test_update_ticket_status ==========

async def test_update_ticket_status():
    """更新工单状态"""
    agent = await _setup_agent()

    ticket = agent.create_ticket(
        customer_id="CUST-001",
        subject="状态测试",
        description="描述",
        priority="medium",
        category="general",
    )

    # 更新为处理中
    updated = agent.update_ticket_status(ticket["ticket_id"], "in_progress")
    assert updated["status"] == "in_progress"

    # 更新为已解决
    resolved = agent.update_ticket_status(
        ticket["ticket_id"], "resolved", resolution="问题已修复"
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolution"] == "问题已修复"
    assert resolved["resolved_at"] is not None

    # 不存在的工单应抛出异常
    with pytest.raises(ValueError):
        agent.update_ticket_status("nonexistent", "resolved")


# ========== test_assign_ticket ==========

async def test_assign_ticket():
    """分配工单给客服人员"""
    agent = await _setup_agent()

    ticket = agent.create_ticket(
        customer_id="CUST-001",
        subject="分配测试",
        description="描述",
        priority="medium",
        category="general",
    )

    assigned = agent.assign_ticket(ticket["ticket_id"], "AGT-001")
    assert assigned["assigned_agent"] == "AGT-001"
    # open状态分配后自动变为in_progress
    assert assigned["status"] == "in_progress"

    # 不存在的工单应抛出异常
    with pytest.raises(ValueError):
        agent.assign_ticket("nonexistent", "AGT-001")


# ========== test_create_complaint ==========

async def test_create_complaint():
    """创建投诉"""
    agent = await _setup_agent()

    complaint = agent.create_complaint(
        customer_id="CUST-001",
        subject="服务投诉",
        description="投诉描述",
        severity="high",
        category="服务",
    )
    assert complaint["complaint_id"].startswith("COMP-")
    assert complaint["status"] == "open"
    assert complaint["severity"] == "high"
    assert complaint["follow_up"] is False

    # 验证已加入投诉列表
    complaints = agent.get_complaints(status="open")
    assert any(c["complaint_id"] == complaint["complaint_id"] for c in complaints)


# ========== test_resolve_complaint ==========

async def test_resolve_complaint():
    """解决投诉"""
    agent = await _setup_agent()

    complaint = agent.create_complaint(
        customer_id="CUST-001",
        subject="待解决投诉",
        description="描述",
        severity="medium",
        category="服务",
    )

    resolved = agent.resolve_complaint(
        complaint["complaint_id"],
        resolution="已与客户沟通并补偿",
        follow_up=True,
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolution"] == "已与客户沟通并补偿"
    assert resolved["follow_up"] is True
    assert resolved["resolved_at"] is not None

    # 不存在的投诉应抛出异常
    with pytest.raises(ValueError):
        agent.resolve_complaint("nonexistent", "解决")


# ========== test_record_satisfaction ==========

async def test_record_satisfaction():
    """记录满意度调查"""
    agent = await _setup_agent()

    survey = agent.record_satisfaction(
        customer_id="CUST-001",
        ticket_id="TKT-001",
        score=5,
        feedback="服务非常好",
    )
    assert survey["survey_id"].startswith("SVY-")
    assert survey["score"] == 5
    assert survey["customer_id"] == "CUST-001"
    assert survey["ticket_id"] == "TKT-001"
    assert "period" in survey
    assert survey["feedback"] == "服务非常好"

    # 验证已加入调查列表
    surveys = agent.get_satisfaction_surveys(customer_id="CUST-001")
    assert len(surveys) >= 1


# ========== test_search_faq ==========

async def test_search_faq():
    """FAQ关键词搜索"""
    agent = await _setup_agent()

    # 搜索"密码"应匹配到密码重置FAQ
    results = agent.search_faq("密码")
    assert len(results) >= 1
    assert any("密码" in r["question"] for r in results)

    # 搜索"浏览器"应匹配到浏览器支持FAQ
    results = agent.search_faq("浏览器")
    assert len(results) >= 1

    # 搜索"导出"应匹配到报表导出FAQ
    results = agent.search_faq("导出")
    assert len(results) >= 1

    # 搜索不存在的内容应返回空
    results = agent.search_faq("完全不存在的关键词xyz123")
    assert len(results) == 0


# ========== test_add_faq ==========

async def test_add_faq():
    """添加FAQ条目"""
    agent = await _setup_agent()

    initial_count = len(agent._faq)

    faq = agent.add_faq(
        question="如何申请休假？",
        answer="请在HR系统中提交休假申请，等待主管审批。",
        category="人事管理",
    )
    assert faq["faq_id"].startswith("FAQ-")
    assert faq["question"] == "如何申请休假？"

    # 验证FAQ数量增加
    assert len(agent._faq) == initial_count + 1

    # 搜索新增的FAQ
    results = agent.search_faq("休假")
    assert len(results) >= 1
    assert results[0]["question"] == "如何申请休假？"


# ========== test_make_decision_urgent_ticket ==========

async def test_make_decision_urgent_ticket():
    """紧急工单决策：立即分配"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "create_ticket",
        "priority": "urgent",
    })
    assert decision.decision_type == "ticket_priority"
    assert decision.confidence == 0.95
    assert decision.requires_human_approval is False
    assert "立即分配" in decision.action


# ========== test_make_decision_critical_complaint ==========

async def test_make_decision_critical_complaint():
    """严重投诉决策：升级处理需人工审批"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "handle_complaint",
        "severity": "critical",
    })
    assert decision.decision_type == "complaint_handling"
    assert decision.requires_human_approval is True
    assert decision.confidence == 0.95
    assert "升级" in decision.action


# ========== test_make_decision_search_faq ==========

async def test_make_decision_search_faq():
    """FAQ搜索决策：有匹配结果"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "search_faq",
        "query": "密码",
    })
    assert decision.decision_type == "knowledge_support"
    assert decision.confidence == 0.85
    assert "找到" in decision.reasoning

    # 无匹配结果时降低置信度
    decision2 = await agent.make_decision({
        "action": "search_faq",
        "query": "完全不存在的关键词xyz123",
    })
    assert decision2.decision_type == "knowledge_support"
    assert decision2.confidence == 0.7
    assert "人工" in decision2.action


# ========== test_get_stats ==========

async def test_get_stats():
    """客服统计信息"""
    agent = await _setup_agent()

    stats = agent.get_stats()
    assert stats["tickets"] == 3  # 种子工单
    assert stats["faq_entries"] == 5  # 种子FAQ
    assert stats["complaints"] == 0
    assert stats["surveys"] == 0
    assert "resolution_rate" in stats
    assert "avg_satisfaction" in stats
    assert stats["avg_satisfaction"] == 0.0  # 无调查数据

    # 验证工单状态分布
    assert stats["open_tickets"] == 1
    assert stats["in_progress_tickets"] == 1
    assert stats["resolved_tickets"] == 1

    # 解决率 = 1/3 * 100 = 33.33
    assert stats["resolution_rate"] > 0
