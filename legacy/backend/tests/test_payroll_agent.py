# -*- coding: utf-8 -*-
"""
薪酬Agent测试
验证薪资计算、社保公积金核算、个税计算与薪酬报表功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.payroll.agent import PayrollAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> PayrollAgent:
    """创建并初始化薪酬Agent（不启动主循环，仅注册身份和订阅）"""
    agent = PayrollAgent()
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

    assert agent.name == "薪酬Agent"
    assert agent.domain == "payroll"
    assert agent.security_clearance == "restricted"
    assert "salary_calculation" in agent.capabilities
    assert "social_insurance" in agent.capabilities
    assert "tax_calculation" in agent.capabilities
    assert "payroll_report" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载正确"""
    agent = await _setup_agent()

    # 5个薪资结构
    assert len(agent._salary_structures) == 5

    # EMP0001基本工资25000
    structure = agent.get_salary_structure("EMP0001")
    assert structure is not None
    assert structure["base_salary"] == 25000
    assert structure["allowances"] == 5000

    # 社保配置
    config = agent.get_social_insurance_config()
    assert config["pension"] == 0.08
    assert config["medical"] == 0.02
    assert config["unemployment"] == 0.005
    assert config["housing_fund"] == 0.12

    # 个税税率表
    assert len(agent._tax_brackets) == 7
    assert agent._tax_brackets[0]["rate"] == 0.03

    # 起征点
    assert agent.TAX_THRESHOLD == 5000


# ========== test_calculate_individual_tax ==========

async def test_calculate_individual_tax():
    """个税计算"""
    agent = await _setup_agent()

    # 0收入免税
    assert agent.calculate_individual_tax(0) == 0.0

    # 负数免税
    assert agent.calculate_individual_tax(-1000) == 0.0

    # 第一档（3000以内）税率3%
    tax_low = agent.calculate_individual_tax(2000)
    assert tax_low == round(2000 * 0.03, 2)

    # 第二档（3000-12000）税率10%，速算扣除210
    tax_mid = agent.calculate_individual_tax(10000)
    assert tax_mid == round(10000 * 0.10 - 210, 2)


# ========== test_calculate_payroll ==========

async def test_calculate_payroll():
    """计算单个员工薪酬"""
    agent = await _setup_agent()

    record = agent.calculate_payroll("EMP0001", "2026-07")

    assert "error" not in record
    assert record["employee_id"] == "EMP0001"
    assert record["period"] == "2026-07"
    assert record["base_salary"] == 25000
    assert record["allowances"] == 5000
    assert record["gross_salary"] == 30000  # 25000 + 5000 - 0
    assert record["social_insurance"] > 0
    assert record["housing_fund"] > 0
    assert record["net_salary"] > 0
    assert record["net_salary"] < record["gross_salary"]
    assert record["status"] == "calculated"


# ========== test_calculate_payroll_nonexistent ==========

async def test_calculate_payroll_nonexistent():
    """计算不存在员工的薪酬"""
    agent = await _setup_agent()

    result = agent.calculate_payroll("NONEXISTENT", "2026-07")
    assert "error" in result


# ========== test_batch_calculate_payroll ==========

async def test_batch_calculate_payroll():
    """批量计算薪酬"""
    agent = await _setup_agent()

    results = agent.batch_calculate_payroll("2026-07")
    assert len(results) == 5  # 5个种子员工
    for record in results:
        assert record["period"] == "2026-07"
        assert record["net_salary"] > 0


# ========== test_get_payroll_records_filter ==========

async def test_get_payroll_records_filter():
    """薪酬记录过滤"""
    agent = await _setup_agent()

    agent.calculate_payroll("EMP0001", "2026-07")
    agent.calculate_payroll("EMP0002", "2026-07")
    agent.calculate_payroll("EMP0001", "2026-06")

    # 按员工过滤
    emp1_records = agent.get_payroll_records(employee_id="EMP0001")
    assert len(emp1_records) == 2

    # 按周期过滤
    july_records = agent.get_payroll_records(period="2026-07")
    assert len(july_records) == 2

    # 全部
    all_records = agent.get_payroll_records()
    assert len(all_records) == 3


# ========== test_set_salary_structure ==========

async def test_set_salary_structure():
    """设置薪资结构"""
    agent = await _setup_agent()

    structure = agent.set_salary_structure(
        "EMP-NEW", base_salary=20000, allowances=3000, deductions=500,
    )
    assert structure["employee_id"] == "EMP-NEW"
    assert structure["base_salary"] == 20000
    assert structure["allowances"] == 3000

    # 验证可查询
    found = agent.get_salary_structure("EMP-NEW")
    assert found is not None


# ========== test_update_social_insurance_config ==========

async def test_update_social_insurance_config():
    """更新社保配置"""
    agent = await _setup_agent()

    updated = agent.update_social_insurance_config({"pension": 0.09})
    assert updated["pension"] == 0.09
    # 其他配置保持不变
    assert updated["medical"] == 0.02


# ========== test_get_stats ==========

async def test_get_stats():
    """获取薪酬统计"""
    agent = await _setup_agent()

    agent.calculate_payroll("EMP0001", "2026-07")
    agent.calculate_payroll("EMP0002", "2026-07")

    stats = agent.get_stats()
    assert stats["total_employees_with_structure"] == 5
    assert stats["total_payroll_records"] == 2
    assert stats["total_gross_salary"] > 0
    assert stats["total_net_salary"] > 0
    assert "by_period" in stats
    assert "2026-07" in stats["by_period"]


# ========== test_make_decision_calculate ==========

async def test_make_decision_calculate():
    """薪酬计算决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "calculate_payroll",
        "employee_id": "EMP0001",
        "period": "2026-07",
    })

    assert decision.decision_type == "payroll_calculation"
    assert decision.confidence == 0.95


# ========== test_make_decision_calculate_nonexistent ==========

async def test_make_decision_calculate_nonexistent():
    """不存在员工的薪酬计算决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "calculate_payroll",
        "employee_id": "NONEXISTENT",
        "period": "2026-07",
    })

    assert decision.decision_type == "payroll_calculation"
    assert decision.confidence == 1.0
    assert "不存在" in decision.reasoning


# ========== test_make_decision_adjust ==========

async def test_make_decision_adjust():
    """薪资调整决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "adjust_salary",
        "employee_id": "EMP0001",
        "base_salary": 26000,  # 从25000调整到26000，幅度4%
    })

    assert decision.decision_type == "salary_adjustment"
    assert decision.confidence == 0.9
    assert decision.requires_human_approval is True


# ========== test_make_decision_adjust_large ==========

async def test_make_decision_adjust_large():
    """大幅薪资调整需人工审批"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "adjust_salary",
        "employee_id": "EMP0001",
        "base_salary": 50000,  # 从25000翻倍，幅度100%
    })

    assert decision.decision_type == "salary_adjustment"
    assert decision.requires_human_approval is True
    assert "50%" in decision.reasoning or "审批" in decision.reasoning


# ========== test_make_decision_unknown ==========

async def test_make_decision_unknown():
    """未知操作决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"action": "unknown_action"})
    assert decision.decision_type == "payroll_unknown"
    assert decision.confidence == 0.0


# ========== test_salary_adjusted_event ==========

async def test_salary_adjusted_event():
    """薪资调整事件同步"""
    agent = await _setup_agent()

    event = Event(
        event_type=EventType.SALARY_ADJUSTED,
        aggregate_id="EMP0001",
        aggregate_type="payroll",
        event_data={
            "employee_id": "EMP0001",
            "base_salary": 28000,
            "reason": "年度调薪",
        },
    )
    await agent.process_event(event)

    structure = agent.get_salary_structure("EMP0001")
    assert structure["base_salary"] == 28000
    assert structure["adjustment_reason"] == "年度调薪"


# ========== test_agent_start_stop ==========

async def test_agent_start_stop():
    """Agent启动与停止状态管理"""
    from app.core.config import AgentState

    agent = PayrollAgent()
    await agent.start()

    assert agent.state == AgentState.RUNNING
    assert agent.identity is not None

    await agent.stop()
    assert agent.state == AgentState.TERMINATED
