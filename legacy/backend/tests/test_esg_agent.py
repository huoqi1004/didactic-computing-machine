# -*- coding: utf-8 -*-
"""
ESG Agent测试
验证ESG指标跟踪、报告生成、碳排放记录、社会责任活动、ESG评分与决策
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.esg.agent import ESGAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> ESGAgent:
    """创建并初始化ESG Agent（注册身份、订阅事件、加载种子数据）"""
    agent = ESGAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_esg_target_updated_event(
    indicator_id: str = "ESG0002",
    target: float = 60.0,
) -> Event:
    """创建ESG目标更新事件"""
    return Event(
        event_type=EventType.ESG_TARGET_UPDATED,
        aggregate_id=indicator_id,
        aggregate_type="esg_indicator",
        event_data={
            "indicator_id": indicator_id,
            "target": target,
        },
    )


def _make_audit_completed_event(audit_id: str = "AUD-001") -> Event:
    """创建审计完成事件"""
    return Event(
        event_type=EventType.AUDIT_COMPLETED,
        aggregate_id=audit_id,
        aggregate_type="audit",
        event_data={
            "audit_id": audit_id,
            "result": "passed",
        },
    )


def _make_compliance_passed_event(check_id: str = "COMP-001") -> Event:
    """创建合规检查通过事件"""
    return Event(
        event_type=EventType.COMPLIANCE_CHECK_PASSED,
        aggregate_id=check_id,
        aggregate_type="compliance",
        event_data={
            "check_id": check_id,
            "result": "passed",
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "ESG Agent"
    assert agent.domain == "esg"
    assert agent.security_clearance == "internal"
    assert "esg_reporting" in agent.capabilities
    assert "carbon_tracking" in agent.capabilities
    assert "social_responsibility" in agent.capabilities
    assert "governance_assessment" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载：6个ESG指标、3条碳排放记录、2个社会责任活动"""
    agent = await _setup_agent()

    indicators = agent.get_indicators()
    assert len(indicators) == 6

    carbon_records = agent.get_carbon_records()
    assert len(carbon_records) == 3

    activities = agent.get_social_activities()
    assert len(activities) == 2

    # 验证指标分类分布：E/S/G各2个
    assert len(agent.get_indicators(category="E")) == 2
    assert len(agent.get_indicators(category="S")) == 2
    assert len(agent.get_indicators(category="G")) == 2

    # 验证碳排放按范围分布
    assert len(agent.get_carbon_records(scope="scope1")) == 1
    assert len(agent.get_carbon_records(scope="scope2")) == 1
    assert len(agent.get_carbon_records(scope="scope3")) == 1


# ========== test_indicator_update ==========

async def test_indicator_update():
    """更新ESG指标值"""
    agent = await _setup_agent()

    result = agent.update_indicator("ESG0001", 0.38, "2026-Q2")
    assert "error" not in result
    assert result["current"] == 0.38
    assert result["period"] == "2026-Q2"
    assert "achievement_rate" in result
    assert "history" in result
    assert len(result["history"]) == 1
    assert result["history"][0]["value"] == 0.38
    assert result["history"][0]["old_value"] == 0.45

    # 更新不存在的指标
    error_result = agent.update_indicator("ESG9999", 1.0, "2026-Q2")
    assert "error" in error_result


# ========== test_generate_report ==========

async def test_generate_report():
    """生成ESG报告"""
    agent = await _setup_agent()

    report = await agent.generate_report("2026-Q2", scope="full")
    assert report["period"] == "2026-Q2"
    assert report["scope"] == "full"
    assert report["status"] == "generated"
    assert "report_id" in report
    assert "score" in report
    assert "data" in report
    assert "indicators" in report["data"]
    assert "carbon_summary" in report["data"]
    assert len(report["data"]["indicators"]) == 6

    # 报告应存储到内部字典
    reports = agent.get_reports()
    assert len(reports) == 1

    # 按周期过滤
    q2_reports = agent.get_reports(period="2026-Q2")
    assert len(q2_reports) == 1


# ========== test_record_carbon ==========

async def test_record_carbon():
    """记录碳排放数据"""
    agent = await _setup_agent()

    record = agent.record_carbon(
        scope="scope1", amount=500.0, unit="tCO2e",
        source="工厂燃油排放", date="2026-07",
    )
    assert record["scope"] == "scope1"
    assert record["amount"] == 500.0
    assert record["unit"] == "tCO2e"
    assert record["source"] == "工厂燃油排放"
    assert "record_id" in record

    # 验证记录已添加（种子1条scope1 + 新增1条 = 2条）
    scope1_records = agent.get_carbon_records(scope="scope1")
    assert len(scope1_records) == 2

    # 按周期过滤
    july_records = agent.get_carbon_records(period="2026-07")
    assert len(july_records) == 1
    assert july_records[0]["amount"] == 500.0


# ========== test_esg_score ==========

async def test_esg_score():
    """ESG综合评分计算（E=40%, S=30%, G=30%）"""
    agent = await _setup_agent()

    score = agent.get_esg_score()
    assert "total" in score
    assert "rating" in score
    assert "by_category" in score
    assert "weights" in score
    assert score["total"] > 0
    assert score["rating"] in ("AAA", "AA", "A", "BBB", "BB", "B")
    assert score["weights"]["E"] == 0.4
    assert score["weights"]["S"] == 0.3
    assert score["weights"]["G"] == 0.3
    for cat in ("E", "S", "G"):
        assert cat in score["by_category"]
        assert "score" in score["by_category"][cat]
        assert "count" in score["by_category"][cat]
        assert score["by_category"][cat]["count"] > 0


# ========== test_social_activity ==========

async def test_social_activity():
    """创建社会责任活动"""
    agent = await _setup_agent()

    activity = agent.create_social_activity(
        name="乡村图书捐赠",
        type="公益",
        description="为乡村学校捐赠图书",
        date="2026-09-01",
        beneficiaries="5所乡村小学",
    )
    assert activity["name"] == "乡村图书捐赠"
    assert activity["type"] == "公益"
    assert activity["status"] == "planned"
    assert "activity_id" in activity

    # 验证活动已添加（种子2条 + 新增1条 = 3条）
    activities = agent.get_social_activities()
    assert len(activities) == 3

    # 按状态过滤
    planned = agent.get_social_activities(status="planned")
    assert len(planned) == 1
    completed = agent.get_social_activities(status="completed")
    assert len(completed) == 2


# ========== test_esg_target_updated_event ==========

async def test_esg_target_updated_event():
    """ESG目标更新事件处理"""
    agent = await _setup_agent()

    event = _make_esg_target_updated_event(indicator_id="ESG0002", target=60.0)
    await agent.process_event(event)

    indicator = agent.get_indicator("ESG0002")
    assert indicator["target"] == 60.0
    assert "updated_at" in indicator


# ========== test_audit_completed_event ==========

async def test_audit_completed_event():
    """审计完成事件更新合规率指标"""
    agent = await _setup_agent()

    # 先修改合规率指标以验证事件效果
    agent._indicators["ESG0006"]["current"] = 95.0

    event = _make_audit_completed_event()
    await agent.process_event(event)

    indicator = agent.get_indicator("ESG0006")
    assert indicator["current"] == 100.0


# ========== test_compliance_passed_event ==========

async def test_compliance_passed_event():
    """合规检查通过事件更新合规率指标"""
    agent = await _setup_agent()

    # 先修改合规率指标以验证事件效果
    agent._indicators["ESG0006"]["current"] = 90.0

    event = _make_compliance_passed_event()
    await agent.process_event(event)

    indicator = agent.get_indicator("ESG0006")
    assert indicator["current"] == 100.0


# ========== test_make_decision_generate_report ==========

async def test_make_decision_generate_report():
    """决策：生成ESG报告"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "generate_report",
        "period": "2026-Q3",
    })
    assert decision.decision_type == "esg_report_generation"
    assert decision.confidence == 0.9
    assert decision.requires_human_approval is False

    # 缺少period应拒绝
    decision_no_period = await agent.make_decision({"action": "generate_report"})
    assert decision_no_period.confidence == 1.0
    assert "拒绝" in decision_no_period.action


# ========== test_make_decision_update_indicator ==========

async def test_make_decision_update_indicator():
    """决策：更新ESG指标（正常更新，无需人工确认）"""
    agent = await _setup_agent()

    # ESG0002: current=35, target=50, higher_better
    # current_ratio = 35/50 = 0.7, new value=40 → new_ratio=0.8 > 0.56 → 无恶化
    decision = await agent.make_decision({
        "action": "update_indicator",
        "indicator_id": "ESG0002",
        "value": 40.0,
    })
    assert decision.decision_type == "esg_indicator_update"
    assert decision.confidence == 0.9
    assert decision.requires_human_approval is False

    # 不存在的指标应拒绝
    decision_not_found = await agent.make_decision({
        "action": "update_indicator",
        "indicator_id": "ESG9999",
        "value": 10.0,
    })
    assert decision_not_found.confidence == 1.0
    assert "拒绝" in decision_not_found.action


# ========== test_make_decision_record_carbon_invalid ==========

async def test_make_decision_record_carbon_invalid():
    """决策：碳排放量无效（<=0）应拒绝"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "record_carbon",
        "amount": 0,
        "scope": "scope1",
        "unit": "tCO2e",
    })
    assert decision.decision_type == "carbon_recording"
    assert decision.confidence == 1.0
    assert "拒绝" in decision.action

    # 负数也应拒绝
    decision_neg = await agent.make_decision({
        "action": "record_carbon",
        "amount": -100,
        "scope": "scope1",
        "unit": "tCO2e",
    })
    assert decision_neg.confidence == 1.0
    assert "拒绝" in decision_neg.action


# ========== test_make_decision_indicator_deterioration ==========

async def test_make_decision_indicator_deterioration():
    """决策：指标恶化超过20%需人工确认"""
    agent = await _setup_agent()

    # ESG0002: current=35, target=50, higher_better
    # current_ratio = 35/50 = 0.7
    # new value=25 → new_ratio=25/50=0.5
    # 0.5 < 0.7 * 0.8 = 0.56 → 恶化超过20%
    decision = await agent.make_decision({
        "action": "update_indicator",
        "indicator_id": "ESG0002",
        "value": 25.0,
    })
    assert decision.decision_type == "esg_indicator_update"
    assert decision.requires_human_approval is True
    assert decision.confidence == 0.75
    assert "人工确认" in decision.reasoning


# ========== test_get_indicators_filter ==========

async def test_get_indicators_filter():
    """ESG指标列表按类别过滤"""
    agent = await _setup_agent()

    all_indicators = agent.get_indicators()
    assert len(all_indicators) == 6

    e_indicators = agent.get_indicators(category="E")
    assert len(e_indicators) == 2
    assert all(i["category"] == "E" for i in e_indicators)

    s_indicators = agent.get_indicators(category="S")
    assert len(s_indicators) == 2
    assert all(i["category"] == "S" for i in s_indicators)

    g_indicators = agent.get_indicators(category="G")
    assert len(g_indicators) == 2
    assert all(i["category"] == "G" for i in g_indicators)


# ========== test_get_stats ==========

async def test_get_stats():
    """ESG统计信息"""
    agent = await _setup_agent()

    stats = agent.get_stats()
    assert stats["total_indicators"] == 6
    assert stats["by_category"]["E"] == 2
    assert stats["by_category"]["S"] == 2
    assert stats["by_category"]["G"] == 2
    assert stats["total_reports"] == 0
    assert stats["total_carbon_records"] == 3
    assert stats["total_social_activities"] == 2
    assert stats["total_carbon_emission"] > 0
