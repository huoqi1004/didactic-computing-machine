# -*- coding: utf-8 -*-
"""
绩效Agent测试
验证KPI模板管理、绩效周期管理、绩效评审、目标跟踪与决策功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.performance.agent import PerformanceAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> PerformanceAgent:
    """创建并初始化绩效Agent（不启动主循环，仅注册身份和订阅）"""
    agent = PerformanceAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_performance_reviewed_event(
    employee_id: str = "emp-001",
    cycle_id: str = "CYCLE0001",
    review_id: str = "rev-001",
    total_score: float = 88.0,
    grade: str = "A",
) -> Event:
    """创建绩效评审事件"""
    return Event(
        event_type=EventType.PERFORMANCE_REVIEWED,
        aggregate_id=review_id,
        aggregate_type="performance_review",
        event_data={
            "review_id": review_id,
            "employee_id": employee_id,
            "cycle_id": cycle_id,
            "scores": {"KPI0001": 88},
            "total_score": total_score,
            "grade": grade,
            "comments": "外部评审同步",
            "status": "reviewed",
        },
    )


def _make_kpi_updated_event(
    kpi_id: str = "KPI0001",
    target: float = 120,
    weight: float = 0.35,
) -> Event:
    """创建KPI更新事件"""
    return Event(
        event_type=EventType.KPI_UPDATED,
        aggregate_id=kpi_id,
        aggregate_type="kpi",
        event_data={
            "kpi_id": kpi_id,
            "target": target,
            "weight": weight,
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "绩效Agent"
    assert agent.domain == "performance"
    assert agent.security_clearance == "confidential"
    assert "kpi_management" in agent.capabilities
    assert "performance_review" in agent.capabilities
    assert "goal_tracking" in agent.capabilities
    assert "competency_assessment" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载4个KPI模板"""
    agent = await _setup_agent()

    templates = agent.get_kpi_templates()
    assert len(templates) == 4

    kpi_ids = {t["kpi_id"] for t in templates}
    assert "KPI0001" in kpi_ids
    assert "KPI0002" in kpi_ids
    assert "KPI0003" in kpi_ids
    assert "KPI0004" in kpi_ids

    # 验证编号计数器
    assert agent._kpi_seq == 4

    # 验证种子数据字段
    sales_kpi = agent._kpi_templates["KPI0001"]
    assert sales_kpi["name"] == "销售额"
    assert sales_kpi["department"] == "市场部"
    assert sales_kpi["weight"] == 0.3


# ========== test_create_kpi_template ==========

async def test_create_kpi_template():
    """创建KPI模板"""
    agent = await _setup_agent()

    template = agent.create_kpi_template(
        name="研发效率",
        department="研发部",
        metric="人均代码产出（行/天）",
        target=200,
        weight=0.15,
        unit="行/天",
        description="衡量研发人员代码产出效率",
    )

    assert template["kpi_id"] == "KPI0005"
    assert template["name"] == "研发效率"
    assert template["department"] == "研发部"
    assert template["target"] == 200
    assert template["weight"] == 0.15
    assert template["status"] == "active"

    # 验证已存入内部存储
    assert "KPI0005" in agent._kpi_templates
    assert agent._kpi_seq == 5


# ========== test_create_review_cycle ==========

async def test_create_review_cycle():
    """创建绩效周期"""
    agent = await _setup_agent()

    cycle = agent.create_review_cycle(
        name="2026年上半年绩效评审",
        period="2026H1",
        start_date="2026-01-01",
        end_date="2026-06-30",
        description="上半年绩效评审周期",
    )

    assert cycle["cycle_id"] == "CYCLE0001"
    assert cycle["name"] == "2026年上半年绩效评审"
    assert cycle["period"] == "2026H1"
    assert cycle["status"] == "draft"

    # 验证已存入内部存储
    assert "CYCLE0001" in agent._review_cycles
    assert agent._cycle_seq == 1


# ========== test_submit_review ==========

async def test_submit_review():
    """提交绩效评审并计算总分与等级"""
    agent = await _setup_agent()

    # 创建绩效周期
    cycle = agent.create_review_cycle(
        name="2026年Q1绩效评审",
        period="2026Q1",
        start_date="2026-01-01",
        end_date="2026-03-31",
    )

    # 提交评审（使用种子KPI模板的权重计算）
    review = agent.submit_review(
        employee_id="emp-001",
        cycle_id=cycle["cycle_id"],
        scores={"KPI0001": 95, "KPI0002": 85},
        comments="表现优秀",
    )

    # KPI0001权重0.3, KPI0002权重0.25
    # total_score = (95*0.3 + 85*0.25) / (0.3+0.25) = 49.75/0.55 = 90.45
    assert review["employee_id"] == "emp-001"
    assert review["cycle_id"] == cycle["cycle_id"]
    assert review["total_score"] == 90.45
    assert review["grade"] == "S"
    assert review["status"] == "submitted"
    assert review["source"] == "manual"

    # 验证评审已记录
    reviews = agent.get_reviews()
    assert len(reviews) == 1


# ========== test_score_to_grade ==========

async def test_score_to_grade():
    """分数转等级边界测试"""
    agent = await _setup_agent()

    assert agent._score_to_grade(95) == "S"
    assert agent._score_to_grade(90) == "S"
    assert agent._score_to_grade(89.9) == "A"
    assert agent._score_to_grade(85) == "A"
    assert agent._score_to_grade(80) == "A"
    assert agent._score_to_grade(79.9) == "B"
    assert agent._score_to_grade(75) == "B"
    assert agent._score_to_grade(70) == "B"
    assert agent._score_to_grade(69.9) == "C"
    assert agent._score_to_grade(65) == "C"
    assert agent._score_to_grade(60) == "C"
    assert agent._score_to_grade(59.9) == "D"
    assert agent._score_to_grade(50) == "D"
    assert agent._score_to_grade(0) == "D"


# ========== test_performance_reviewed_event ==========

async def test_performance_reviewed_event():
    """绩效评审事件同步外部评审数据"""
    agent = await _setup_agent()

    event = _make_performance_reviewed_event(
        employee_id="emp-002",
        cycle_id="ext-cycle-001",
        review_id="ext-rev-001",
        total_score=82.0,
        grade="A",
    )
    await agent.process_event(event)

    reviews = agent.get_reviews()
    assert len(reviews) == 1

    review = reviews[0]
    assert review["employee_id"] == "emp-002"
    assert review["cycle_id"] == "ext-cycle-001"
    assert review["total_score"] == 82.0
    assert review["grade"] == "A"
    assert review["source"] == "event"
    assert review["status"] == "reviewed"


# ========== test_kpi_updated_event ==========

async def test_kpi_updated_event():
    """KPI更新事件修改已有KPI模板"""
    agent = await _setup_agent()

    original = agent._kpi_templates["KPI0001"]
    assert original["target"] == 100
    assert original["weight"] == 0.3

    event = _make_kpi_updated_event(
        kpi_id="KPI0001",
        target=120,
        weight=0.35,
    )
    await agent.process_event(event)

    updated = agent._kpi_templates["KPI0001"]
    assert updated["target"] == 120
    assert updated["weight"] == 0.35
    assert "updated_at" in updated


# ========== test_make_decision_submit_review ==========

async def test_make_decision_submit_review():
    """绩效评审决策：活跃周期内提交评审"""
    agent = await _setup_agent()

    cycle = agent.create_review_cycle(
        name="2026年Q2绩效评审",
        period="2026Q2",
        start_date="2026-04-01",
        end_date="2026-06-30",
        status="active",
    )

    decision = await agent.make_decision({
        "action": "submit_review",
        "employee_id": "emp-001",
        "cycle_id": cycle["cycle_id"],
        "scores": {"KPI0001": 95},
    })

    assert decision.decision_type == "performance_review"
    assert decision.confidence == 0.9
    assert decision.requires_human_approval is False
    assert decision.context["total_score"] == 95.0
    assert decision.context["grade"] == "S"


# ========== test_make_decision_close_cycle ==========

async def test_make_decision_close_cycle():
    """关闭绩效周期决策需人工审批"""
    agent = await _setup_agent()

    cycle = agent.create_review_cycle(
        name="2026年Q3绩效评审",
        period="2026Q3",
        start_date="2026-07-01",
        end_date="2026-09-30",
    )

    decision = await agent.make_decision({
        "action": "close_cycle",
        "cycle_id": cycle["cycle_id"],
    })

    assert decision.decision_type == "cycle_close"
    assert decision.confidence == 0.9
    assert decision.requires_human_approval is True


# ========== test_make_decision_update_kpi ==========

async def test_make_decision_update_kpi():
    """KPI更新决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "update_kpi",
        "kpi_id": "KPI0001",
    })

    assert decision.decision_type == "kpi_update"
    assert decision.confidence == 0.9
    assert decision.requires_human_approval is False


# ========== test_make_decision_unknown ==========

async def test_make_decision_unknown():
    """未知操作返回错误决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "unknown_action",
    })

    assert decision.decision_type == "performance_unknown"
    assert decision.confidence == 0.0


# ========== test_set_goal ==========

async def test_set_goal():
    """设定目标"""
    agent = await _setup_agent()

    goal = agent.set_goal(
        employee_id="emp-001",
        goal_type="performance",
        description="2026年Q2销售额提升20%",
        target_date="2026-06-30",
    )

    assert goal["employee_id"] == "emp-001"
    assert goal["goal_type"] == "performance"
    assert goal["description"] == "2026年Q2销售额提升20%"
    assert goal["status"] == "active"
    assert goal["progress"] == 0

    # 验证已存入内部存储
    assert goal["goal_id"] in agent._goals


# ========== test_get_kpi_templates_filter ==========

async def test_get_kpi_templates_filter():
    """KPI模板列表按部门过滤"""
    agent = await _setup_agent()

    all_templates = agent.get_kpi_templates()
    assert len(all_templates) == 4

    tech_templates = agent.get_kpi_templates(department="技术部")
    assert len(tech_templates) == 2
    assert all(t["department"] == "技术部" for t in tech_templates)

    market_templates = agent.get_kpi_templates(department="市场部")
    assert len(market_templates) == 1
    assert market_templates[0]["name"] == "销售额"


# ========== test_get_reviews_filter ==========

async def test_get_reviews_filter():
    """评审记录按员工和周期过滤"""
    agent = await _setup_agent()

    cycle = agent.create_review_cycle(
        name="2026年Q1绩效评审",
        period="2026Q1",
        start_date="2026-01-01",
        end_date="2026-03-31",
    )

    # 提交多条评审
    agent.submit_review("emp-001", cycle["cycle_id"], {"KPI0001": 95})
    agent.submit_review("emp-002", cycle["cycle_id"], {"KPI0001": 75})
    agent.submit_review("emp-003", cycle["cycle_id"], {"KPI0001": 60})

    all_reviews = agent.get_reviews()
    assert len(all_reviews) == 3

    emp001_reviews = agent.get_reviews(employee_id="emp-001")
    assert len(emp001_reviews) == 1
    assert emp001_reviews[0]["employee_id"] == "emp-001"

    cycle_reviews = agent.get_reviews(cycle_id=cycle["cycle_id"])
    assert len(cycle_reviews) == 3


# ========== test_get_stats ==========

async def test_get_stats():
    """绩效统计信息"""
    agent = await _setup_agent()

    cycle = agent.create_review_cycle(
        name="2026年Q1绩效评审",
        period="2026Q1",
        start_date="2026-01-01",
        end_date="2026-03-31",
        status="active",
    )

    agent.submit_review("emp-001", cycle["cycle_id"], {"KPI0001": 95})
    agent.submit_review("emp-002", cycle["cycle_id"], {"KPI0001": 75})
    agent.set_goal("emp-001", "performance", "目标A", "2026-06-30")

    stats = agent.get_stats()

    assert stats["total_kpi_templates"] == 4
    assert stats["total_cycles"] == 1
    assert stats["active_cycles"] == 1
    assert stats["total_reviews"] == 2
    assert stats["avg_score"] == 85.0  # (95 + 75) / 2
    assert stats["total_goals"] == 1
    assert stats["active_goals"] == 1
    assert "S" in stats["reviews_by_grade"]
    assert "B" in stats["reviews_by_grade"]
