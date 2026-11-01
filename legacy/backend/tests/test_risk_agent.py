# -*- coding: utf-8 -*-
"""
风险Agent测试
验证风险识别、风险评估、风险监控与风险缓解功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.risk.agent import RiskAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> RiskAgent:
    """创建并初始化风险Agent（不启动主循环，仅注册身份和订阅）"""
    agent = RiskAgent()
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

    assert agent.name == "风险Agent"
    assert agent.domain == "risk"
    assert agent.security_clearance == "restricted"
    assert "risk_identification" in agent.capabilities
    assert "risk_assessment" in agent.capabilities
    assert "risk_monitoring" in agent.capabilities
    assert "risk_mitigation" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载正确"""
    agent = await _setup_agent()

    # 5个种子风险
    risks = agent.get_risks()
    assert len(risks) == 5

    # 按类别过滤
    financial = agent.get_risks(category="financial")
    assert len(financial) == 1
    assert financial[0]["title"] == "财务风险"


# ========== test_identify_risk ==========

async def test_identify_risk():
    """识别新风险"""
    agent = await _setup_agent()

    risk = await agent.identify_risk(
        title="新供应链风险",
        category="operational",
        description="供应商集中度过高",
        source="manual",
    )

    assert risk["title"] == "新供应链风险"
    assert risk["category"] == "operational"
    assert risk["status"] == "identified"
    assert risk["risk_level"] == "unassessed"
    assert risk["risk_id"].startswith("RSK-")

    # 验证已添加到登记册
    found = agent.get_risk(risk["risk_id"])
    assert found is not None


# ========== test_assess_risk_low ==========

async def test_assess_risk_low():
    """低风险评估"""
    agent = await _setup_agent()

    risk = await agent.identify_risk(
        title="低风险项",
        category="operational",
        description="低概率低影响",
        source="manual",
    )

    assessed = await agent.assess_risk(
        risk["risk_id"], probability=1, impact=2, owner="运营部",
    )

    # 1 * 2 = 2 → 低风险
    assert assessed["risk_score"] == 2
    assert assessed["risk_level"] == "low"
    assert assessed["status"] == "assessed"
    assert assessed["owner"] == "运营部"


# ========== test_assess_risk_medium ==========

async def test_assess_risk_medium():
    """中风险评估"""
    agent = await _setup_agent()

    risk = await agent.identify_risk(
        title="中风险项",
        category="operational",
        description="中概率中影响",
        source="manual",
    )

    assessed = await agent.assess_risk(
        risk["risk_id"], probability=2, impact=3, owner="运营部",
    )

    # 2 * 3 = 6 → 中风险
    assert assessed["risk_score"] == 6
    assert assessed["risk_level"] == "medium"


# ========== test_assess_risk_high ==========

async def test_assess_risk_high():
    """高风险评估自动创建预警"""
    agent = await _setup_agent()

    risk = await agent.identify_risk(
        title="高风险项",
        category="financial",
        description="高概率高影响",
        source="manual",
    )

    initial_alerts = len(agent.get_alerts())

    assessed = await agent.assess_risk(
        risk["risk_id"], probability=3, impact=4, owner="财务部",
    )

    # 3 * 4 = 12 → 高风险
    assert assessed["risk_score"] == 12
    assert assessed["risk_level"] == "high"

    # 高风险自动创建预警
    assert len(agent.get_alerts()) == initial_alerts + 1


# ========== test_assess_risk_critical ==========

async def test_assess_risk_critical():
    """极高风险评估"""
    agent = await _setup_agent()

    risk = await agent.identify_risk(
        title="极高风险项",
        category="security",
        description="极高概率极高影响",
        source="manual",
    )

    assessed = await agent.assess_risk(
        risk["risk_id"], probability=5, impact=5, owner="安全部",
    )

    # 5 * 5 = 25 → 极高风险
    assert assessed["risk_score"] == 25
    assert assessed["risk_level"] == "critical"


# ========== test_assess_risk_nonexistent ==========

async def test_assess_risk_nonexistent():
    """评估不存在的风险"""
    agent = await _setup_agent()

    result = await agent.assess_risk("NONEXISTENT", 3, 3, "测试")
    assert "error" in result


# ========== test_mitigate_risk ==========

async def test_mitigate_risk():
    """制定风险缓解措施"""
    agent = await _setup_agent()

    risk = await agent.identify_risk(
        title="待缓解风险",
        category="operational",
        description="需要缓解",
        source="manual",
    )

    mitigated = await agent.mitigate_risk(
        risk["risk_id"],
        action="增加备用供应商",
        mitigation_plan="引入3家备用供应商，分散采购集中度",
        due_date="2026-09-30",
    )

    assert mitigated["status"] == "mitigating"
    assert mitigated["mitigation_plan"] == "引入3家备用供应商，分散采购集中度"
    assert mitigated["due_date"] == "2026-09-30"


# ========== test_create_alert ==========

async def test_create_alert():
    """创建风险预警"""
    agent = await _setup_agent()

    risks = agent.get_risks()
    risk_id = risks[0]["risk_id"]

    alert = await agent.create_alert(
        risk_id=risk_id,
        severity="high",
        message="测试预警消息",
    )

    assert alert["severity"] == "high"
    assert alert["message"] == "测试预警消息"
    assert alert["status"] == "open"
    assert alert["alert_id"].startswith("ALR-")


# ========== test_get_alerts_filter ==========

async def test_get_alerts_filter():
    """预警列表过滤"""
    agent = await _setup_agent()

    risks = agent.get_risks()
    risk_id = risks[0]["risk_id"]

    await agent.create_alert(risk_id, "high", "高级预警")
    await agent.create_alert(risk_id, "low", "低级预警")

    all_alerts = agent.get_alerts()
    assert len(all_alerts) >= 2

    high_alerts = agent.get_alerts(severity="high")
    assert all(a["severity"] == "high" for a in high_alerts)


# ========== test_risk_matrix ==========

async def test_risk_matrix():
    """风险矩阵"""
    agent = await _setup_agent()

    matrix = agent.get_risk_matrix()
    assert "matrix" in matrix
    assert "summary" in matrix
    assert "axis_labels" in matrix
    assert matrix["summary"]["total_risks"] >= 5
    # 矩阵是5x5
    assert len(matrix["matrix"]) == 5
    assert len(matrix["matrix"][0]) == 5


# ========== test_get_stats ==========

async def test_get_stats():
    """获取风险统计"""
    agent = await _setup_agent()

    stats = agent.get_stats()
    assert stats["risks_total"] >= 5
    assert "level_distribution" in stats
    assert "category_distribution" in stats
    assert "alerts_total" in stats


# ========== test_make_decision_assess ==========

async def test_make_decision_assess():
    """风险评估决策"""
    agent = await _setup_agent()

    risks = agent.get_risks()
    risk_id = risks[0]["risk_id"]

    decision = await agent.make_decision({
        "action_type": "assess",
        "risk_id": risk_id,
    })

    assert decision.decision_type in ("risk_assessment",)
    assert decision.confidence > 0


# ========== test_make_decision_assess_nonexistent ==========

async def test_make_decision_assess_nonexistent():
    """评估不存在的风险决策返回错误"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action_type": "assess",
        "risk_id": "NONEXISTENT",
    })

    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_make_decision_identify ==========

async def test_make_decision_identify():
    """风险识别决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action_type": "identify",
        "title": "新风险",
        "category": "operational",
    })

    assert decision.decision_type == "risk_identification"
    assert decision.confidence == 0.85


# ========== test_risk_identified_event ==========

async def test_risk_identified_event():
    """风险识别事件自动登记"""
    agent = await _setup_agent()

    initial_count = len(agent.get_risks())

    event = Event(
        event_type=EventType.RISK_IDENTIFIED,
        aggregate_id="EXT-RISK-001",
        aggregate_type="risk",
        event_data={
            "title": "外部识别风险",
            "category": "compliance",
            "description": "外部系统识别的合规风险",
            "source": "event",
        },
    )
    await agent.process_event(event)

    assert len(agent.get_risks()) == initial_count + 1


# ========== test_security_alert_event ==========

async def test_security_alert_event():
    """安全警报事件转化为风险预警"""
    agent = await _setup_agent()

    initial_alerts = len(agent.get_alerts())

    event = Event(
        event_type=EventType.SECURITY_ALERT,
        aggregate_id="SEC-001",
        aggregate_type="security",
        event_data={
            "severity": "high",
            "description": "检测到异常登录",
        },
    )
    await agent.process_event(event)

    # 应创建新的风险和预警
    assert len(agent.get_alerts()) > initial_alerts


# ========== test_agent_start_stop ==========

async def test_agent_start_stop():
    """Agent启动与停止状态管理"""
    from app.core.config import AgentState

    agent = RiskAgent()
    await agent.start()

    assert agent.state == AgentState.RUNNING
    assert agent.identity is not None

    await agent.stop()
    assert agent.state == AgentState.TERMINATED
