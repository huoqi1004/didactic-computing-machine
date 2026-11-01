# -*- coding: utf-8 -*-
"""
预算Agent测试
验证预算编制、执行跟踪、差异分析与预算调整功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.budget.agent import BudgetAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> BudgetAgent:
    """创建并初始化预算Agent（不启动主循环，仅注册身份和订阅）"""
    agent = BudgetAgent()
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

    assert agent.name == "预算Agent"
    assert agent.domain == "budget"
    assert agent.security_clearance == "confidential"
    assert "budget_preparation" in agent.capabilities
    assert "budget_execution" in agent.capabilities
    assert "budget_analysis" in agent.capabilities
    assert "variance_report" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载正确"""
    agent = await _setup_agent()

    # 5个种子预算
    budgets = agent.get_budgets()
    assert len(budgets) == 5

    # 2026年预算
    budgets_2026 = agent.get_budgets(period="2026")
    assert len(budgets_2026) == 5

    # 销售部预算500万
    sales = agent.get_budgets(department="销售部")
    assert len(sales) == 1
    assert sales[0]["amount"] == 5_000_000
    assert sales[0]["status"] == "approved"


# ========== test_create_budget ==========

async def test_create_budget():
    """创建预算方案"""
    agent = await _setup_agent()

    budget = agent.create_budget(
        period="2026-H2",
        department="研发部",
        category="operating",
        amount=2_000_000,
        items=["人员费用", "设备采购"],
    )

    assert budget["period"] == "2026-H2"
    assert budget["department"] == "研发部"
    assert budget["amount"] == 2_000_000
    assert budget["status"] == "draft"
    assert budget["budget_id"].startswith("BGT")
    assert budget["actual_amount"] == 0.0


# ========== test_approve_budget ==========

async def test_approve_budget():
    """审批预算"""
    agent = await _setup_agent()

    budget = agent.create_budget(
        period="2026-H2",
        department="研发部",
        category="operating",
        amount=1_000_000,
    )

    approved = await agent.approve_budget(budget["budget_id"], "总经理")
    assert approved["status"] == "approved"
    assert approved["approved_by"] == "总经理"
    assert approved["approved_at"] != ""


# ========== test_record_execution ==========

async def test_record_execution():
    """记录预算执行"""
    agent = await _setup_agent()

    # 使用已审批的种子预算
    result = await agent.record_execution(
        "BGT0004",  # 行政部，预算100万
        actual_amount=300_000,
        description="办公租金",
        date="2026-07-01",
    )

    assert result["budget_id"] == "BGT0004"
    assert result["actual_amount"] == 300_000
    assert result["remaining"] == 700_000
    assert 0 < result["utilization_rate"] < 1


# ========== test_record_execution_exceed ==========

async def test_record_execution_exceed():
    """预算超支检测"""
    agent = await _setup_agent()

    # 行政部预算100万，记录超过预算的执行
    result = await agent.record_execution(
        "BGT0004",
        actual_amount=1_200_000,
        description="超支支出",
        date="2026-07-01",
    )

    # 预算状态应变为exceeded
    budget = agent.get_budget("BGT0004")
    assert budget["status"] == "exceeded"
    assert budget["actual_amount"] > budget["amount"]


# ========== test_get_variance ==========

async def test_get_variance():
    """获取预算差异分析"""
    agent = await _setup_agent()

    # 先记录一些执行
    await agent.record_execution("BGT0004", 500_000, "日常支出", "2026-07-01")

    variance = agent.get_variance("BGT0004")
    assert variance["budget_id"] == "BGT0004"
    assert variance["planned_amount"] == 1_000_000
    assert variance["actual_amount"] == 500_000
    assert variance["variance"] == 500_000
    assert 0 < variance["utilization_rate"] < 1
    assert variance["status"] == "on_track"


# ========== test_get_variances_list ==========

async def test_get_variances_list():
    """获取预算差异分析列表"""
    agent = await _setup_agent()

    variances = agent.get_variances(period="2026")
    assert len(variances) == 5
    for v in variances:
        assert "planned_amount" in v
        assert "actual_amount" in v
        assert "variance" in v
        assert "status" in v


# ========== test_adjust_budget ==========

async def test_adjust_budget():
    """调整预算"""
    agent = await _setup_agent()

    adjustment = await agent.adjust_budget(
        "BGT0004",  # 原100万
        new_amount=1_100_000,
        reason="增加办公租金",
        approved_by="总经理",
    )

    assert adjustment["old_amount"] == 1_000_000
    assert adjustment["new_amount"] == 1_100_000
    assert adjustment["reason"] == "增加办公租金"

    # 验证预算已更新
    budget = agent.get_budget("BGT0004")
    assert budget["amount"] == 1_100_000


# ========== test_get_budget_summary ==========

async def test_get_budget_summary():
    """获取预算汇总"""
    agent = await _setup_agent()

    summary = agent.get_budget_summary("2026")
    assert summary["period"] == "2026"
    assert summary["total_budgets"] == 5
    assert summary["total_planned"] > 0
    assert "by_department" in summary
    assert "销售部" in summary["by_department"]


# ========== test_get_stats ==========

async def test_get_stats():
    """获取预算统计"""
    agent = await _setup_agent()

    stats = agent.get_stats()
    assert stats["total_budgets"] >= 5
    assert "by_status" in stats
    assert "by_department" in stats
    assert "total_planned" in stats
    assert stats["total_planned"] > 0


# ========== test_make_decision_approve ==========

async def test_make_decision_approve():
    """预算审批决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "approve_budget",
        "budget_id": "BGT0004",  # 100万，正常范围
    })

    assert decision.decision_type == "budget_approval"
    assert decision.confidence == 0.9


# ========== test_make_decision_approve_large ==========

async def test_make_decision_approve_large():
    """大额预算需人工审批"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "approve_budget",
        "budget_id": "BGT0001",  # 500万，超过阈值
    })

    assert decision.decision_type == "budget_approval"
    assert decision.requires_human_approval is True


# ========== test_make_decision_approve_nonexistent ==========

async def test_make_decision_approve_nonexistent():
    """审批不存在的预算决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "approve_budget",
        "budget_id": "NONEXISTENT",
    })

    assert decision.decision_type == "budget_approval"
    assert decision.confidence == 1.0
    assert "不存在" in decision.reasoning


# ========== test_make_decision_adjust ==========

async def test_make_decision_adjust():
    """预算调整决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "adjust_budget",
        "budget_id": "BGT0004",
        "new_amount": 1_050_000,  # 5%调整，在20%以内
    })

    assert decision.decision_type == "budget_adjustment"
    assert decision.confidence == 0.85


# ========== test_make_decision_adjust_large ==========

async def test_make_decision_adjust_large():
    """大幅预算调整需人工审批"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "adjust_budget",
        "budget_id": "BGT0004",
        "new_amount": 2_000_000,  # 100%调整
    })

    assert decision.decision_type == "budget_adjustment"
    assert decision.requires_human_approval is True


# ========== test_make_decision_create ==========

async def test_make_decision_create():
    """预算创建决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "create_budget",
        "amount": 500_000,
        "department": "测试部",
        "period": "2026-H2",
    })

    assert decision.decision_type == "budget_creation"
    assert decision.confidence == 0.9


# ========== test_make_decision_unknown ==========

async def test_make_decision_unknown():
    """未知操作决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"action": "unknown_action"})
    assert decision.decision_type == "budget_unknown"
    assert decision.confidence == 0.0


# ========== test_voucher_posted_event ==========

async def test_voucher_posted_event():
    """凭证过账事件自动记录预算执行"""
    agent = await _setup_agent()

    event = Event(
        event_type=EventType.VOUCHER_POSTED,
        aggregate_id="V-001",
        aggregate_type="voucher",
        event_data={
            "voucher_id": "V-001",
            "total_debit": 100_000,
            "department": "行政部",  # 匹配BGT0004
        },
    )
    await agent.process_event(event)

    # 验证预算执行已记录
    budget = agent.get_budget("BGT0004")
    assert budget["actual_amount"] == 100_000


# ========== test_agent_start_stop ==========

async def test_agent_start_stop():
    """Agent启动与停止状态管理"""
    from app.core.config import AgentState

    agent = BudgetAgent()
    await agent.start()

    assert agent.state == AgentState.RUNNING
    assert agent.identity is not None

    await agent.stop()
    assert agent.state == AgentState.TERMINATED
