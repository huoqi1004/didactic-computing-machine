# -*- coding: utf-8 -*-
"""
项目Agent测试
验证项目全生命周期管理：规划、进度跟踪、成本控制、里程碑管理与决策功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.project.agent import ProjectAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> ProjectAgent:
    """创建并初始化项目Agent（不启动主循环，仅注册身份和订阅）"""
    agent = ProjectAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_sales_confirmed_event(
    order_id: str = "SO-001",
    customer_name: str = "客户A",
    product_name: str = "智能终端A",
    total_amount: float = 300000.0,
) -> Event:
    """创建销售订单确认事件"""
    return Event(
        event_type=EventType.SALES_ORDER_CONFIRMED,
        aggregate_id=order_id,
        aggregate_type="sales_order",
        event_data={
            "order_id": order_id,
            "customer_name": customer_name,
            "product_name": product_name,
            "total_amount": total_amount,
            "status": "confirmed",
        },
    )


def _make_milestone_reached_event(
    milestone_id: str = "ms-002",
    project_id: str = "proj-001",
    actual_date: str = "2026-06-28",
) -> Event:
    """创建里程碑达成事件"""
    return Event(
        event_type=EventType.PROJECT_MILESTONE_REACHED,
        aggregate_id=milestone_id,
        aggregate_type="milestone",
        event_data={
            "milestone_id": milestone_id,
            "project_id": project_id,
            "name": "系统配置与开发完成",
            "actual_date": actual_date,
            "notes": "按期完成",
        },
    )


def _make_project_completed_event(
    project_id: str = "proj-002",
    name: str = "企业官网改版项目",
) -> Event:
    """创建项目完成事件"""
    return Event(
        event_type=EventType.PROJECT_COMPLETED,
        aggregate_id=project_id,
        aggregate_type="project",
        event_data={
            "project_id": project_id,
            "name": name,
            "manager": "孙设计",
            "budget": 150000.0,
            "completed_at": "2026-07-31T10:00:00+00:00",
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "项目Agent"
    assert agent.domain == "project"
    assert agent.security_clearance == "internal"
    assert "project_planning" in agent.capabilities
    assert "progress_tracking" in agent.capabilities
    assert "cost_management" in agent.capabilities
    assert "milestone_management" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None
    assert agent.BUDGET_WARNING_THRESHOLD == 0.9
    assert agent.HUMAN_APPROVAL_ON_COMPLETE is True


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载3个项目、8个里程碑、4条成本记录"""
    agent = await _setup_agent()

    assert len(agent._projects) == 3
    assert len(agent._milestones) == 8
    assert len(agent._costs) == 4

    # 验证项目
    assert "proj-001" in agent._projects
    assert "proj-002" in agent._projects
    assert "proj-003" in agent._projects
    assert agent._projects["proj-001"]["name"] == "ERP系统实施项目"

    # 验证里程碑
    ms_ids = {m["milestone_id"] for m in agent._milestones.values()}
    assert len(ms_ids) == 8

    # 验证成本记录
    assert all(c["cost_id"].startswith("cost-") for c in agent._costs)


# ========== test_create_project ==========

async def test_create_project():
    """创建项目"""
    agent = await _setup_agent()

    project = agent.create_project(
        name="数据中台建设项目",
        description="企业数据中台规划与建设",
        manager="陈数据",
        start_date="2026-05-01",
        end_date="2026-12-31",
        budget=600000.0,
        department="信息技术部",
    )

    assert project["name"] == "数据中台建设项目"
    assert project["manager"] == "陈数据"
    assert project["budget"] == 600000.0
    assert project["status"] == "planning"
    assert project["department"] == "信息技术部"

    # 验证已存入内部存储
    assert project["project_id"] in agent._projects
    assert project["project_id"] in agent._members


# ========== test_update_project_status ==========

async def test_update_project_status():
    """更新项目状态"""
    agent = await _setup_agent()

    # 将proj-001从in_progress更新为pending_acceptance
    project = await agent.update_project_status("proj-001", "pending_acceptance")

    assert project["status"] == "pending_acceptance"
    assert agent._projects["proj-001"]["status"] == "pending_acceptance"

    # 验证决策已记录
    decisions = [d for d in agent._decision_history
                 if d.decision_type == "project_status_update"]
    assert len(decisions) >= 1
    assert decisions[-1].requires_human_approval is False


# ========== test_create_milestone ==========

async def test_create_milestone():
    """创建里程碑"""
    agent = await _setup_agent()

    milestone = agent.create_milestone(
        project_id="proj-001",
        name="UAT测试完成",
        target_date="2026-08-15",
        description="完成用户验收测试",
    )

    assert milestone["project_id"] == "proj-001"
    assert milestone["name"] == "UAT测试完成"
    assert milestone["status"] == "pending"
    assert milestone["actual_date"] is None

    # 验证已存入内部存储
    assert milestone["milestone_id"] in agent._milestones


# ========== test_complete_milestone ==========

async def test_complete_milestone():
    """完成里程碑并检查项目完成状态"""
    agent = await _setup_agent()

    # proj-002有两个里程碑：ms-004(completed), ms-005(in_progress)
    # 完成ms-005后，所有里程碑完成，项目应变为pending_acceptance
    milestone = await agent.complete_milestone(
        milestone_id="ms-005",
        actual_date="2026-06-28",
        notes="前端开发按期完成",
    )

    assert milestone["status"] == "completed"
    assert milestone["actual_date"] == "2026-06-28"
    assert milestone["notes"] == "前端开发按期完成"

    # 所有里程碑完成后项目状态应变为pending_acceptance
    assert agent._projects["proj-002"]["status"] == "pending_acceptance"

    # 验证决策已记录
    decisions = [d for d in agent._decision_history
                 if d.decision_type == "milestone_completion"]
    assert len(decisions) >= 1


# ========== test_record_cost ==========

async def test_record_cost():
    """记录项目成本"""
    agent = await _setup_agent()

    cost = await agent.record_cost(
        project_id="proj-001",
        category="consulting",
        amount=50000.0,
        description="额外咨询服务费",
        date="2026-05-01",
    )

    assert cost["project_id"] == "proj-001"
    assert cost["category"] == "consulting"
    assert cost["amount"] == 50000.0

    # 验证已追加到成本列表
    assert len(agent._costs) == 5

    # 验证决策已记录
    decisions = [d for d in agent._decision_history
                 if d.decision_type == "project_cost_record"]
    assert len(decisions) >= 1


# ========== test_get_progress ==========

async def test_get_progress():
    """获取项目进度"""
    agent = await _setup_agent()

    # proj-001: 3个里程碑(1个completed), 预算500000, 成本120000+80000=200000
    progress = agent.get_progress("proj-001")

    assert progress["project_id"] == "proj-001"
    assert progress["project_name"] == "ERP系统实施项目"
    assert progress["total_milestones"] == 3
    assert progress["completed_milestones"] == 1
    assert progress["milestone_completion_rate"] == round(1 / 3, 4)
    assert progress["budget"] == 500000.0
    assert progress["total_cost"] == 200000.0
    assert progress["budget_usage_rate"] == 0.4
    assert progress["budget_warning"] is False
    assert "schedule_variance" in progress
    assert "milestones" in progress


# ========== test_sales_confirmed_event ==========

async def test_sales_confirmed_event():
    """销售订单确认事件自动创建交付项目"""
    agent = await _setup_agent()

    original_count = len(agent._projects)

    event = _make_sales_confirmed_event(
        order_id="SO-NEW-001",
        customer_name="新客户B",
        product_name="智能终端C",
        total_amount=400000.0,
    )
    await agent.process_event(event)

    # 验证新增了交付项目
    assert len(agent._projects) == original_count + 1

    # 查找新创建的交付项目
    delivery_projects = [p for p in agent._projects.values()
                         if p.get("source_order_id") == "SO-NEW-001"]
    assert len(delivery_projects) == 1

    project = delivery_projects[0]
    assert "新客户B" in project["name"]
    assert "智能终端C" in project["name"]
    assert project["department"] == "交付部"
    assert project["budget"] == 60000.0  # 400000 * 0.15 = 60000


# ========== test_milestone_reached_event ==========

async def test_milestone_reached_event():
    """里程碑达成事件同步里程碑状态"""
    agent = await _setup_agent()

    # ms-002初始状态为in_progress
    assert agent._milestones["ms-002"]["status"] == "in_progress"

    event = _make_milestone_reached_event(
        milestone_id="ms-002",
        project_id="proj-001",
        actual_date="2026-06-28",
    )
    await agent.process_event(event)

    milestone = agent._milestones["ms-002"]
    assert milestone["status"] == "completed"
    assert milestone["actual_date"] == "2026-06-28"


# ========== test_project_completed_event ==========

async def test_project_completed_event():
    """项目完成事件同步项目状态"""
    agent = await _setup_agent()

    # proj-002初始状态为in_progress
    assert agent._projects["proj-002"]["status"] == "in_progress"

    event = _make_project_completed_event(project_id="proj-002")
    await agent.process_event(event)

    project = agent._projects["proj-002"]
    assert project["status"] == "completed"
    assert "completed_at" in project


# ========== test_make_decision_complete ==========

async def test_make_decision_complete():
    """项目完成决策需人工验收"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "complete",
        "project_id": "proj-001",
    })

    assert decision.decision_type == "project_completion"
    assert decision.confidence == 0.9
    assert decision.requires_human_approval is True
    assert "proj-001" in decision.reasoning
    assert "milestone_completion_rate" in decision.context["progress"]


# ========== test_make_decision_budget_warning ==========

async def test_make_decision_budget_warning():
    """预算超支预警决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "budget_warning",
        "project_id": "proj-001",
    })

    assert decision.decision_type == "budget_warning"
    assert decision.confidence == 0.85
    assert decision.requires_human_approval is False
    assert "proj-001" in decision.reasoning


# ========== test_make_decision_not_found ==========

async def test_make_decision_not_found():
    """不存在的项目ID返回错误决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "complete",
        "project_id": "nonexistent-project",
    })

    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_get_projects_filter ==========

async def test_get_projects_filter():
    """项目列表按状态与项目经理过滤"""
    agent = await _setup_agent()

    all_projects = agent.get_projects()
    assert len(all_projects) == 3

    # 所有种子项目状态都是in_progress
    in_progress = agent.get_projects(status="in_progress")
    assert len(in_progress) == 3

    # 按项目经理过滤
    chen_projects = agent.get_projects(manager="陈项目经理")
    assert len(chen_projects) == 1
    assert chen_projects[0]["project_id"] == "proj-001"


# ========== test_get_milestones ==========

async def test_get_milestones():
    """获取项目里程碑列表"""
    agent = await _setup_agent()

    # proj-001有3个里程碑
    milestones = agent.get_milestones("proj-001")
    assert len(milestones) == 3

    ms_ids = {m["milestone_id"] for m in milestones}
    assert "ms-001" in ms_ids
    assert "ms-002" in ms_ids
    assert "ms-003" in ms_ids

    # proj-002有2个里程碑
    milestones_002 = agent.get_milestones("proj-002")
    assert len(milestones_002) == 2

    # proj-003有3个里程碑
    milestones_003 = agent.get_milestones("proj-003")
    assert len(milestones_003) == 3


# ========== test_get_costs_filter ==========

async def test_get_costs_filter():
    """成本记录按项目与类别过滤"""
    agent = await _setup_agent()

    all_costs = agent.get_costs()
    assert len(all_costs) == 4

    # 按项目过滤
    proj001_costs = agent.get_costs(project_id="proj-001")
    assert len(proj001_costs) == 2
    assert all(c["project_id"] == "proj-001" for c in proj001_costs)

    # 按类别过滤
    consulting_costs = agent.get_costs(category="consulting")
    assert len(consulting_costs) == 1
    assert consulting_costs[0]["amount"] == 80000.0

    # 组合过滤
    proj001_software = agent.get_costs(
        project_id="proj-001", category="software_license"
    )
    assert len(proj001_software) == 1
    assert proj001_software[0]["cost_id"] == "cost-001"


# ========== test_on_tick ==========

async def test_on_tick():
    """周期性任务：检查预算使用率与项目进度"""
    agent = await _setup_agent()

    # 正常情况下on_tick应无异常执行
    await agent.on_tick()

    # 添加成本使proj-002预算使用率超过阈值
    # proj-002预算150000, 已有成本45000, 再加100000 = 145000, 使用率=0.9667>0.9
    await agent.record_cost(
        project_id="proj-002",
        category="material",
        amount=100000.0,
        description="追加材料费",
        date="2026-05-20",
    )

    # on_tick应对超预算项目发出预警（无异常即可）
    await agent.on_tick()

    # 验证预算确实超过阈值
    progress = agent.get_progress("proj-002")
    assert progress["budget_usage_rate"] >= agent.BUDGET_WARNING_THRESHOLD


# ========== test_get_stats ==========

async def test_get_stats():
    """项目统计信息"""
    agent = await _setup_agent()

    stats = agent.get_stats()

    assert stats["total_projects"] == 3
    assert stats["by_status"].get("in_progress") == 3
    assert stats["total_milestones"] == 8
    assert stats["completed_milestones"] == 3  # ms-001, ms-004, ms-006
    assert stats["pending_milestones"] == 2  # ms-003, ms-008
    assert stats["in_progress_milestones"] == 3  # ms-002, ms-005, ms-007
    assert stats["total_budget"] == 1450000.0  # 500000+150000+800000
    assert stats["total_cost"] == 445000.0  # 120000+80000+45000+200000
    assert stats["total_cost_records"] == 4
    assert "by_department" in stats
    assert "over_budget_projects" in stats
    assert "budget_warning_projects" in stats
