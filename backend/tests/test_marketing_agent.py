# -*- coding: utf-8 -*-
"""
市场Agent测试
验证营销活动管理、线索生成与质量评估、ROI分析、事件处理与决策
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.marketing.agent import MarketingAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> MarketingAgent:
    """创建并初始化市场Agent（注册身份、订阅事件、加载种子数据）"""
    agent = MarketingAgent()
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

    assert agent.name == "市场Agent"
    assert agent.domain == "marketing"
    assert agent.security_clearance == "internal"
    assert "campaign_management" in agent.capabilities
    assert "brand_management" in agent.capabilities
    assert "lead_generation" in agent.capabilities
    assert "market_analysis" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载：2个营销活动、3条线索、1个品牌资产"""
    agent = await _setup_agent()

    campaigns = agent.get_campaigns()
    assert len(campaigns) == 2

    leads = agent.get_leads()
    assert len(leads) == 3

    stats = agent.get_stats()
    assert stats["brand_assets"] == 1
    # 1个active, 1个planning
    assert stats["active_campaigns"] == 1
    assert stats["planning_campaigns"] == 1

    # 种子线索来源分布：线上广告、行业展会、邮件营销
    sources = {l["source"] for l in leads}
    assert "线上广告" in sources
    assert "行业展会" in sources
    assert "邮件营销" in sources


# ========== test_campaign_launched_event ==========

async def test_campaign_launched_event():
    """营销活动启动事件：更新活动状态为active"""
    agent = await _setup_agent()

    # 获取planning状态的活动
    planning = agent.get_campaigns(status="planning")
    campaign_id = planning[0]["campaign_id"]

    event = Event(
        event_type=EventType.CAMPAIGN_LAUNCHED,
        aggregate_id=campaign_id,
        aggregate_type="campaign",
        event_data={"campaign_id": campaign_id},
    )
    await agent.process_event(event)

    campaign = agent.get_campaign(campaign_id)
    assert campaign["status"] == "active"
    assert campaign["launched_at"] is not None


# ========== test_campaign_completed_event ==========

async def test_campaign_completed_event():
    """营销活动完成事件：更新状态并记录收入和支出"""
    agent = await _setup_agent()

    # 获取active状态的活动
    active = agent.get_campaigns(status="active")
    campaign_id = active[0]["campaign_id"]

    event = Event(
        event_type=EventType.CAMPAIGN_COMPLETED,
        aggregate_id=campaign_id,
        aggregate_type="campaign",
        event_data={
            "campaign_id": campaign_id,
            "revenue": 800000,
            "actual_spend": 400000,
            "results": {"conversion_rate": 0.15},
        },
    )
    await agent.process_event(event)

    campaign = agent.get_campaign(campaign_id)
    assert campaign["status"] == "completed"
    assert campaign["revenue"] == 800000
    assert campaign["actual_spend"] == 400000
    assert campaign["completed_at"] is not None
    assert campaign["results"]["conversion_rate"] == 0.15


# ========== test_lead_generated_event ==========

async def test_lead_generated_event():
    """线索生成事件：同步外部线索到本地"""
    agent = await _setup_agent()

    initial_count = len(agent.get_leads())

    event = Event(
        event_type=EventType.LEAD_GENERATED,
        aggregate_id="LEAD-EXT-001",
        aggregate_type="lead",
        event_data={
            "lead_id": "LEAD-EXT-001",
            "name": "外部线索用户",
            "source": "展会",
            "contact_info": "ext@example.com",
            "interest": "CRM系统",
            "campaign_id": "",
        },
    )
    await agent.process_event(event)

    leads = agent.get_leads()
    assert len(leads) == initial_count + 1

    new_lead = [l for l in leads if l["lead_id"] == "LEAD-EXT-001"]
    assert len(new_lead) == 1
    assert new_lead[0]["name"] == "外部线索用户"
    assert new_lead[0]["status"] == "new"
    assert new_lead[0]["qualification_score"] == 0.0


# ========== test_create_campaign ==========

async def test_create_campaign():
    """创建营销活动"""
    agent = await _setup_agent()

    campaign = agent.create_campaign(
        name="双十一促销",
        budget=200000,
        channels=["线上广告", "社交媒体"],
        start_date="2026-11-01",
        end_date="2026-11-11",
        target_audience="中小企业",
    )

    assert "campaign_id" in campaign
    assert campaign["name"] == "双十一促销"
    assert campaign["budget"] == 200000
    assert campaign["channels"] == ["线上广告", "社交媒体"]
    assert campaign["status"] == "planning"
    assert campaign["launched_at"] is None
    assert campaign["completed_at"] is None
    assert campaign["actual_spend"] == 0.0
    assert campaign["revenue"] == 0.0


# ========== test_launch_campaign ==========

async def test_launch_campaign():
    """启动营销活动"""
    agent = await _setup_agent()

    campaign = agent.create_campaign(
        name="测试活动", budget=100000,
        channels=["线上广告"], start_date="2026-01-01",
        end_date="2026-12-31", target_audience="测试受众",
    )
    result = await agent.launch_campaign(campaign["campaign_id"])

    assert result["status"] == "active"
    assert result["launched_at"] is not None


# ========== test_complete_campaign ==========

async def test_complete_campaign():
    """完成营销活动并记录结果"""
    agent = await _setup_agent()

    campaign = agent.create_campaign(
        name="测试活动", budget=100000,
        channels=["线上广告"], start_date="2026-01-01",
        end_date="2026-12-31", target_audience="测试受众",
    )
    await agent.launch_campaign(campaign["campaign_id"])
    result = await agent.complete_campaign(campaign["campaign_id"], {
        "revenue": 300000,
        "actual_spend": 100000,
        "conversion_rate": 0.2,
    })

    assert result["status"] == "completed"
    assert result["revenue"] == 300000
    assert result["actual_spend"] == 100000
    assert result["completed_at"] is not None
    assert result["results"]["conversion_rate"] == 0.2


# ========== test_add_lead ==========

async def test_add_lead():
    """添加线索"""
    agent = await _setup_agent()

    initial_count = len(agent.get_leads())
    lead = agent.add_lead(
        name="测试用户",
        source="线上广告",
        contact_info="test@example.com",
        interest="ERP系统",
    )

    assert "lead_id" in lead
    assert lead["name"] == "测试用户"
    assert lead["source"] == "线上广告"
    assert lead["status"] == "new"
    assert lead["qualification_score"] == 0.0
    assert len(agent.get_leads()) == initial_count + 1


# ========== test_qualify_lead_hot ==========

async def test_qualify_lead_hot():
    """线索质量评估：评分>=80为hot"""
    agent = await _setup_agent()

    lead = agent.add_lead("测试", "线上广告", "t@e.com", "ERP")
    result = agent.qualify_lead(lead["lead_id"], 85, "高质量线索")

    assert result["status"] == "hot"
    assert result["qualification_score"] == 85
    assert result["notes"] == "高质量线索"


# ========== test_qualify_lead_qualified ==========

async def test_qualify_lead_qualified():
    """线索质量评估：60<=评分<80为qualified"""
    agent = await _setup_agent()

    lead = agent.add_lead("测试", "线上广告", "t@e.com", "ERP")
    result = agent.qualify_lead(lead["lead_id"], 65, "合格线索")

    assert result["status"] == "qualified"
    assert result["qualification_score"] == 65


# ========== test_qualify_lead_unqualified ==========

async def test_qualify_lead_unqualified():
    """线索质量评估：评分<60为unqualified"""
    agent = await _setup_agent()

    lead = agent.add_lead("测试", "线上广告", "t@e.com", "ERP")
    result = agent.qualify_lead(lead["lead_id"], 40, "不合格线索")

    assert result["status"] == "unqualified"
    assert result["qualification_score"] == 40


# ========== test_get_campaign_roi ==========

async def test_get_campaign_roi():
    """获取营销活动ROI"""
    agent = await _setup_agent()

    campaign = agent.create_campaign(
        name="ROI测试", budget=100000,
        channels=["线上广告"], start_date="2026-01-01",
        end_date="2026-12-31", target_audience="测试受众",
    )
    await agent.launch_campaign(campaign["campaign_id"])
    await agent.complete_campaign(campaign["campaign_id"], {
        "revenue": 200000,
        "actual_spend": 100000,
    })

    roi = agent.get_campaign_roi(campaign["campaign_id"])
    assert roi["found"] is True
    assert roi["roi"] == 100.0  # (200000 - 100000) / 100000 * 100
    assert roi["profit"] == 100000.0
    assert roi["revenue"] == 200000
    assert roi["actual_spend"] == 100000


# ========== test_make_decision_launch_large_budget ==========

async def test_make_decision_launch_large_budget():
    """决策：大预算活动(>500000)需人工确认"""
    agent = await _setup_agent()

    campaign = agent.create_campaign(
        name="大预算活动", budget=600000,
        channels=["线上广告"], start_date="2026-01-01",
        end_date="2026-12-31", target_audience="测试受众",
    )
    decision = await agent.make_decision({
        "action": "launch_campaign",
        "campaign_id": campaign["campaign_id"],
    })

    assert decision.decision_type == "campaign_launch"
    assert decision.requires_human_approval is True
    assert decision.confidence == 0.9


# ========== test_make_decision_launch_small_budget ==========

async def test_make_decision_launch_small_budget():
    """决策：小预算活动(<=500000)自动启动"""
    agent = await _setup_agent()

    campaign = agent.create_campaign(
        name="小预算活动", budget=400000,
        channels=["线上广告"], start_date="2026-01-01",
        end_date="2026-12-31", target_audience="测试受众",
    )
    decision = await agent.make_decision({
        "action": "launch_campaign",
        "campaign_id": campaign["campaign_id"],
    })

    assert decision.decision_type == "campaign_launch"
    assert decision.requires_human_approval is False
    assert decision.confidence == 0.85


# ========== test_make_decision_qualify_lead ==========

async def test_make_decision_qualify_lead():
    """决策：高质量线索转交销售"""
    agent = await _setup_agent()

    lead = agent.add_lead("决策测试", "线上广告", "t@e.com", "ERP")
    decision = await agent.make_decision({
        "action": "qualify_lead",
        "lead_id": lead["lead_id"],
        "qualification_score": 85,
    })

    assert decision.decision_type == "lead_qualification"
    assert decision.confidence == 0.9
    assert "hot" in decision.action or "销售" in decision.action

    # 低分线索进入培育流程
    decision_low = await agent.make_decision({
        "action": "qualify_lead",
        "lead_id": lead["lead_id"],
        "qualification_score": 50,
    })
    assert decision_low.decision_type == "lead_qualification"
    assert decision_low.confidence == 0.75
    assert "培育" in decision_low.action


# ========== test_make_decision_analyze_roi_negative ==========

async def test_make_decision_analyze_roi_negative():
    """决策：负ROI活动分析"""
    agent = await _setup_agent()

    # 种子active活动：budget=500000, revenue=0, actual_spend=0
    # actual_spend = 0 or budget = 500000, roi = (0-500000)/500000*100 = -100
    active = agent.get_campaigns(status="active")
    campaign_id = active[0]["campaign_id"]

    decision = await agent.make_decision({
        "action": "analyze_roi",
        "campaign_id": campaign_id,
    })

    assert decision.decision_type == "campaign_analysis"
    assert decision.confidence == 0.85
    assert "负收益" in decision.reasoning or "优化" in decision.reasoning


# ========== test_get_campaigns_filter ==========

async def test_get_campaigns_filter():
    """营销活动列表按状态过滤"""
    agent = await _setup_agent()

    all_campaigns = agent.get_campaigns()
    assert len(all_campaigns) == 2

    active = agent.get_campaigns(status="active")
    assert len(active) == 1
    assert all(c["status"] == "active" for c in active)

    planning = agent.get_campaigns(status="planning")
    assert len(planning) == 1
    assert all(c["status"] == "planning" for c in planning)


# ========== test_get_leads_filter ==========

async def test_get_leads_filter():
    """线索列表按来源和状态过滤"""
    agent = await _setup_agent()

    all_leads = agent.get_leads()
    assert len(all_leads) == 3

    # 按来源过滤
    online = agent.get_leads(source="线上广告")
    assert len(online) == 1
    assert all(l["source"] == "线上广告" for l in online)

    exhibition = agent.get_leads(source="行业展会")
    assert len(exhibition) == 1

    # 按状态过滤
    new_leads = agent.get_leads(status="new")
    assert len(new_leads) == 2
    assert all(l["status"] == "new" for l in new_leads)

    qualified = agent.get_leads(status="qualified")
    assert len(qualified) == 1


# ========== test_get_stats ==========

async def test_get_stats():
    """市场Agent统计信息"""
    agent = await _setup_agent()

    stats = agent.get_stats()
    assert stats["campaigns"] == 2
    assert stats["active_campaigns"] == 1
    assert stats["planning_campaigns"] == 1
    assert stats["completed_campaigns"] == 0
    assert stats["leads"] == 3
    assert stats["new_leads"] == 2
    assert stats["qualified_leads"] == 1  # 1 qualified + 0 hot
    assert stats["brand_assets"] == 1
    assert stats["total_budget"] > 0
