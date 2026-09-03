# -*- coding: utf-8 -*-
"""
商业智能Agent测试
验证数据分析、报表生成、仪表板管理与KPI监控功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.bi.agent import BIAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> BIAgent:
    """创建并初始化BI Agent（不启动主循环，仅注册身份和订阅）"""
    agent = BIAgent()
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

    assert agent.name == "商业智能Agent"
    assert agent.domain == "bi"
    assert agent.security_clearance == "confidential"
    assert "data_analysis" in agent.capabilities
    assert "report_generation" in agent.capabilities
    assert "dashboard_management" in agent.capabilities
    assert "kpi_monitoring" in agent.capabilities
    assert "trend_analysis" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载正确"""
    agent = await _setup_agent()

    # 3个预置报表
    reports = agent.get_reports()
    assert len(reports) == 3

    # 2个仪表板
    dashboards = agent.get_dashboards()
    assert len(dashboards) == 2

    # 3个KPI
    kpis = agent.get_kpis()
    assert len(kpis) == 3

    # 验证种子报表
    report = agent.get_report("RPT0001")
    assert report is not None
    assert report["type"] == "sales_monthly"


# ========== test_get_reports_filter ==========

async def test_get_reports_filter():
    """报表列表过滤"""
    agent = await _setup_agent()

    # 按类型过滤
    sales_reports = agent.get_reports(type="sales_monthly")
    assert len(sales_reports) == 1

    finance_reports = agent.get_reports(type="finance_quarterly")
    assert len(finance_reports) == 1

    # 按周期过滤
    reports_jun = agent.get_reports(period="2026-06")
    assert len(reports_jun) == 1


# ========== test_generate_report ==========

async def test_generate_report():
    """生成报表"""
    agent = await _setup_agent()

    report = await agent.generate_report(
        type="sales_monthly",
        period="2026-07",
    )

    assert report["type"] == "sales_monthly"
    assert report["period"] == "2026-07"
    assert report["status"] == "generated"
    assert report["report_id"].startswith("RPT")
    assert "data" in report
    assert "summary" in report["data"]


# ========== test_generate_report_custom ==========

async def test_generate_report_custom():
    """生成自定义报表"""
    agent = await _setup_agent()

    report = await agent.generate_report(
        type="custom",
        period="2026-07",
        parameters={"dim": "department"},
    )

    assert report["type"] == "custom"
    assert report["parameters"]["dim"] == "department"


# ========== test_get_dashboards ==========

async def test_get_dashboards():
    """获取仪表板"""
    agent = await _setup_agent()

    dashboards = agent.get_dashboards()
    assert len(dashboards) == 2

    dashboard = agent.get_dashboard("DSH0001")
    assert dashboard is not None
    assert dashboard["name"] == "高管驾驶舱"
    assert "widgets" in dashboard


# ========== test_update_dashboard ==========

async def test_update_dashboard():
    """更新仪表板组件"""
    agent = await _setup_agent()

    new_widgets = [
        {"id": "w1", "type": "kpi_card", "title": "新指标"},
        {"id": "w2", "type": "chart", "title": "新图表"},
    ]

    updated = await agent.update_dashboard("DSH0001", new_widgets)
    assert "error" not in updated
    assert len(updated["widgets"]) == 2
    assert updated["refresh_count"] >= 1


# ========== test_update_dashboard_nonexistent ==========

async def test_update_dashboard_nonexistent():
    """更新不存在的仪表板"""
    agent = await _setup_agent()

    result = await agent.update_dashboard("NONEXISTENT", [])
    assert "error" in result


# ========== test_get_kpis_filter ==========

async def test_get_kpis_filter():
    """KPI列表过滤"""
    agent = await _setup_agent()

    all_kpis = agent.get_kpis()
    assert len(all_kpis) == 3

    # 按仪表板过滤
    dsh1_kpis = agent.get_kpis(dashboard_id="DSH0001")
    assert len(dsh1_kpis) == 2  # KPI0001和KPI0002属于DSH0001


# ========== test_add_kpi ==========

async def test_add_kpi():
    """新增KPI"""
    agent = await _setup_agent()

    kpi = agent.add_kpi(
        name="客户满意度",
        target=95,
        current=90,
        unit="%",
        owner="客服部",
    )

    assert kpi["name"] == "客户满意度"
    assert kpi["target"] == 95
    assert kpi["current"] == 90
    assert kpi["achievement_rate"] == 90 / 95
    assert kpi["status"] == "at_risk"  # 90/95 ≈ 0.947 >= 0.8
    assert kpi["kpi_id"].startswith("KPI")


# ========== test_add_kpi_on_track ==========

async def test_add_kpi_on_track():
    """达成目标的KPI状态为on_track"""
    agent = await _setup_agent()

    kpi = agent.add_kpi(
        name="完成率",
        target=100,
        current=105,
        unit="%",
        owner="生产部",
    )

    assert kpi["status"] == "on_track"


# ========== test_analyze_trend ==========

async def test_analyze_trend():
    """趋势分析（有历史数据）"""
    agent = await _setup_agent()

    # sales_revenue有6个月种子数据
    analysis = agent.analyze_trend("sales_revenue", "2026-H1", method="linear")

    assert analysis["metric"] == "sales_revenue"
    assert analysis["data_points"] == 6
    assert analysis["trend"] == "up"  # 种子数据是递增的
    assert analysis["slope"] > 0
    assert "forecast" in analysis
    assert "current_value" in analysis


# ========== test_analyze_trend_no_data ==========

async def test_analyze_trend_no_data():
    """趋势分析（无数据）"""
    agent = await _setup_agent()

    analysis = agent.analyze_trend("nonexistent_metric", "2026-H1")

    assert analysis["data_points"] == 0
    assert analysis["trend"] == "no_data"
    assert analysis["forecast"] is None


# ========== test_analyze_trend_moving_average ==========

async def test_analyze_trend_moving_average():
    """移动平均趋势分析"""
    agent = await _setup_agent()

    analysis = agent.analyze_trend("sales_revenue", "2026-H1", method="moving_average")

    assert analysis["method"] == "moving_average"
    assert analysis["data_points"] == 6
    assert "forecast" in analysis


# ========== test_get_stats ==========

async def test_get_stats():
    """获取BI统计"""
    agent = await _setup_agent()

    stats = agent.get_stats()
    assert stats["total_reports"] >= 3
    assert stats["total_dashboards"] >= 2
    assert stats["total_kpis"] >= 3
    assert "kpi_on_track" in stats
    assert "kpi_at_risk" in stats
    assert "metrics_tracked" in stats
    assert stats["metrics_tracked"] >= 1


# ========== test_make_decision_generate_report ==========

async def test_make_decision_generate_report():
    """报表生成决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "generate_report",
        "type": "sales_monthly",
        "period": "2026-07",
    })

    assert decision.decision_type == "report_generation"
    assert decision.confidence == 0.9


# ========== test_make_decision_generate_report_invalid ==========

async def test_make_decision_generate_report_invalid():
    """缺少类型或周期的报表生成决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "generate_report",
        "type": "",
        "period": "",
    })

    assert decision.decision_type == "report_generation"
    assert decision.confidence == 1.0
    assert "不能为空" in decision.reasoning


# ========== test_make_decision_update_dashboard ==========

async def test_make_decision_update_dashboard():
    """仪表板更新决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "update_dashboard",
        "dashboard_id": "DSH0001",
    })

    assert decision.decision_type == "dashboard_update"
    assert decision.confidence == 0.9


# ========== test_make_decision_update_dashboard_nonexistent ==========

async def test_make_decision_update_dashboard_nonexistent():
    """更新不存在的仪表板决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "update_dashboard",
        "dashboard_id": "NONEXISTENT",
    })

    assert decision.decision_type == "dashboard_update"
    assert decision.confidence == 1.0
    assert "不存在" in decision.reasoning


# ========== test_make_decision_add_kpi ==========

async def test_make_decision_add_kpi():
    """新增KPI决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "add_kpi",
        "name": "测试KPI",
    })

    assert decision.decision_type == "kpi_addition"
    assert decision.confidence == 0.9


# ========== test_make_decision_add_kpi_no_name ==========

async def test_make_decision_add_kpi_no_name():
    """无名称的KPI决策被拒绝"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "add_kpi",
        "name": "",
    })

    assert decision.decision_type == "kpi_addition"
    assert decision.confidence == 1.0
    assert "不能为空" in decision.reasoning


# ========== test_make_decision_unknown ==========

async def test_make_decision_unknown():
    """未知操作决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"action": "unknown_action"})
    assert decision.decision_type == "bi_unknown"
    assert decision.confidence == 0.0


# ========== test_sales_confirmed_event ==========

async def test_sales_confirmed_event():
    """销售确认事件记录指标"""
    agent = await _setup_agent()

    initial_metrics = len(agent._metrics_history.get("sales_revenue", []))

    event = Event(
        event_type=EventType.SALES_ORDER_CONFIRMED,
        aggregate_id="SO-001",
        aggregate_type="sales_order",
        event_data={
            "total_amount": 50000,
            "date": "2026-07-15",
        },
    )
    await agent.process_event(event)

    # 验证指标已记录
    assert len(agent._metrics_history["sales_revenue"]) == initial_metrics + 1


# ========== test_agent_start_stop ==========

async def test_agent_start_stop():
    """Agent启动与停止状态管理"""
    from app.core.config import AgentState

    agent = BIAgent()
    await agent.start()

    assert agent.state == AgentState.RUNNING
    assert agent.identity is not None

    await agent.stop()
    assert agent.state == AgentState.TERMINATED
