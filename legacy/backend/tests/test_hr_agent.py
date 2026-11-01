# -*- coding: utf-8 -*-
"""
人力资源Agent测试
验证员工档案管理、考勤记录、请假审批与组织架构维护
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.hr.agent import HRAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> HRAgent:
    """创建并初始化HR Agent（不启动主循环，仅注册身份和订阅）"""
    agent = HRAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_attendance_event(
    employee_id: str = "EMP0001",
    date: str = "2026-07-01",
) -> Event:
    """创建考勤记录事件"""
    return Event(
        event_type=EventType.ATTENDANCE_RECORDED,
        aggregate_id=employee_id,
        aggregate_type="attendance",
        event_data={
            "employee_id": employee_id,
            "date": date,
            "check_in": "09:00",
            "check_out": "18:00",
            "status": "normal",
        },
    )


def _make_leave_event(
    employee_id: str = "EMP0001",
    leave_type: str = "personal",
    days: int = 1,
) -> Event:
    """创建请假申请事件"""
    return Event(
        event_type=EventType.LEAVE_REQUESTED,
        aggregate_id=employee_id,
        aggregate_type="leave",
        event_data={
            "employee_id": employee_id,
            "leave_type": leave_type,
            "start_date": "2026-07-10",
            "end_date": "2026-07-10",
            "reason": "私事",
            "days": days,
            "status": "pending",
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "人力资源Agent"
    assert agent.domain == "hr"
    assert agent.security_clearance == "confidential"
    assert "employee_management" in agent.capabilities
    assert "attendance_tracking" in agent.capabilities
    assert "leave_management" in agent.capabilities
    assert "org_management" in agent.capabilities
    assert "salary_structure" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载正确"""
    agent = await _setup_agent()

    # 5个种子员工
    employees = agent.get_employees()
    assert len(employees) == 5

    # 5个部门
    org_chart = agent.get_org_chart()
    assert len(org_chart) == 5
    assert "技术部" in org_chart
    assert "财务部" in org_chart

    # 验证种子员工
    emp = agent.get_employee("EMP0001")
    assert emp is not None
    assert emp["name"] == "张伟"
    assert emp["department"] == "技术部"
    assert emp["status"] == "active"


# ========== test_add_employee ==========

async def test_add_employee():
    """新增员工"""
    agent = await _setup_agent()

    employee = agent.add_employee(
        name="新员工",
        department="技术部",
        position="工程师",
        email="new@company.com",
        base_salary=15000,
    )

    assert employee["name"] == "新员工"
    assert employee["department"] == "技术部"
    assert employee["position"] == "工程师"
    assert employee["status"] == "active"
    assert employee["employee_id"].startswith("EMP")

    # 验证已添加
    found = agent.get_employee(employee["employee_id"])
    assert found is not None


# ========== test_update_employee ==========

async def test_update_employee():
    """更新员工档案"""
    agent = await _setup_agent()

    updated = agent.update_employee("EMP0001", position="技术副总裁", phone="13900000001")
    assert updated["position"] == "技术副总裁"
    assert updated["phone"] == "13900000001"


# ========== test_update_employee_nonexistent ==========

async def test_update_employee_nonexistent():
    """更新不存在的员工返回错误"""
    agent = await _setup_agent()

    result = agent.update_employee("NONEXISTENT", position="测试")
    assert "error" in result


# ========== test_record_attendance ==========

async def test_record_attendance():
    """记录考勤"""
    agent = await _setup_agent()

    record = agent.record_attendance(
        employee_id="EMP0001",
        date="2026-07-01",
        check_in="09:00",
        check_out="18:00",
        status="normal",
    )

    assert record["employee_id"] == "EMP0001"
    assert record["date"] == "2026-07-01"
    assert record["source"] == "manual"

    # 验证考勤记录可查询
    records = agent.get_attendance(employee_id="EMP0001")
    assert len(records) == 1


# ========== test_record_attendance_nonexistent ==========

async def test_record_attendance_nonexistent():
    """为不存在的员工记录考勤"""
    agent = await _setup_agent()

    result = agent.record_attendance(
        employee_id="NONEXISTENT",
        date="2026-07-01",
        check_in="09:00",
        check_out="18:00",
    )
    assert "error" in result


# ========== test_request_leave ==========

async def test_request_leave():
    """提交请假申请"""
    agent = await _setup_agent()

    request = agent.request_leave(
        employee_id="EMP0001",
        leave_type="personal",
        start_date="2026-07-10",
        end_date="2026-07-10",
        reason="私事",
    )

    assert request["employee_id"] == "EMP0001"
    assert request["leave_type"] == "personal"
    assert request["status"] == "pending"
    assert request["source"] == "manual"

    # 验证请假记录可查询
    leaves = agent.get_leave_requests(employee_id="EMP0001")
    assert len(leaves) == 1


# ========== test_get_employees_filter ==========

async def test_get_employees_filter():
    """按部门过滤员工"""
    agent = await _setup_agent()

    tech_employees = agent.get_employees(department="技术部")
    assert len(tech_employees) == 1
    assert tech_employees[0]["department"] == "技术部"

    finance_employees = agent.get_employees(department="财务部")
    assert len(finance_employees) == 1


# ========== test_get_stats ==========

async def test_get_stats():
    """获取HR统计信息"""
    agent = await _setup_agent()

    stats = agent.get_stats()
    assert stats["total_employees"] == 5
    assert stats["active_employees"] == 5
    assert stats["departments"] == 5
    assert "by_department" in stats
    assert stats["by_department"]["技术部"] == 1


# ========== test_make_decision_approve_leave ==========

async def test_make_decision_approve_leave():
    """请假审批决策 - 正常"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "approve_leave",
        "employee_id": "EMP0001",
        "leave_type": "personal",
        "days": 2,
    })

    assert decision.decision_type == "leave_approval"
    assert decision.confidence == 0.9
    assert "批准" in decision.action


# ========== test_make_decision_leave_exceeds_limit ==========

async def test_make_decision_leave_exceeds_limit():
    """年假超过5天需人工审批"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "approve_leave",
        "employee_id": "EMP0001",
        "leave_type": "annual",
        "days": 7,
    })

    assert decision.decision_type == "leave_approval"
    assert decision.requires_human_approval is True


# ========== test_make_decision_onboard ==========

async def test_make_decision_onboard():
    """入职决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "onboard_employee",
        "department": "技术部",
    })

    assert decision.decision_type == "employee_onboard"
    assert decision.confidence == 0.95


# ========== test_make_decision_onboard_invalid_dept ==========

async def test_make_decision_onboard_invalid_dept():
    """不存在部门的入职决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action": "onboard_employee",
        "department": "不存在的部门",
    })

    assert decision.decision_type == "employee_onboard"
    assert "不存在" in decision.reasoning


# ========== test_make_decision_unknown ==========

async def test_make_decision_unknown():
    """未知操作决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"action": "unknown_action"})
    assert decision.decision_type == "hr_unknown"
    assert decision.confidence == 0.0


# ========== test_attendance_event ==========

async def test_attendance_event():
    """考勤记录事件同步"""
    agent = await _setup_agent()

    event = _make_attendance_event(employee_id="EMP0001", date="2026-07-01")
    await agent.process_event(event)

    records = agent.get_attendance(employee_id="EMP0001")
    assert len(records) == 1
    assert records[0]["source"] == "event"


# ========== test_leave_event ==========

async def test_leave_event():
    """请假申请事件同步"""
    agent = await _setup_agent()

    event = _make_leave_event(employee_id="EMP0001", leave_type="sick", days=1)
    await agent.process_event(event)

    leaves = agent.get_leave_requests(employee_id="EMP0001")
    assert len(leaves) == 1
    assert leaves[0]["source"] == "event"
    assert leaves[0]["leave_type"] == "sick"


# ========== test_agent_start_stop ==========

async def test_agent_start_stop():
    """Agent启动与停止状态管理"""
    from app.core.config import AgentState

    agent = HRAgent()
    await agent.start()

    assert agent.state == AgentState.RUNNING
    assert agent.identity is not None

    await agent.stop()
    assert agent.state == AgentState.TERMINATED
