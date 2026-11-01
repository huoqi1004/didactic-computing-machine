# -*- coding: utf-8 -*-
"""
动态调度Agent测试
验证任务调度、优先级管理、资源分配、冲突解决、LLM降级与查询接口
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.scheduler.agent import DynamicSchedulerAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> DynamicSchedulerAgent:
    """创建并初始化动态调度Agent（不启动主循环，仅注册身份和订阅）"""
    agent = DynamicSchedulerAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_decision_made_event(
    decision_id: str = "DEC-001",
    decision_type: str = "purchase_order_generation",
    agent_name: str = "采购Agent",
    confidence: float = 0.85,
    requires_human_approval: bool = False,
) -> Event:
    """创建Agent决策事件"""
    return Event(
        event_type=EventType.AGENT_DECISION_MADE,
        aggregate_id=decision_id,
        aggregate_type="agent_decision",
        event_data={
            "decision_id": decision_id,
            "decision_type": decision_type,
            "context": {"agent_name": agent_name},
            "confidence": confidence,
            "requires_human_approval": requires_human_approval,
        },
    )


def _make_state_changed_event(
    agent_id: str = "agent-001",
    state: str = "paused",
    reason: str = "manual_pause",
) -> Event:
    """创建Agent状态变化事件"""
    return Event(
        event_type=EventType.AGENT_STATE_CHANGED,
        aggregate_id=agent_id,
        aggregate_type="agent",
        event_data={
            "agent_id": agent_id,
            "state": state,
            "reason": reason,
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "动态调度Agent"
    assert agent.domain == "scheduler"
    assert agent.security_clearance == "internal"
    assert "task_scheduling" in agent.capabilities
    assert "resource_allocation" in agent.capabilities
    assert "priority_management" in agent.capabilities
    assert "conflict_resolution" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_task_scheduling ==========

async def test_task_scheduling():
    """Agent决策事件触发任务调度"""
    agent = await _setup_agent()

    event = _make_decision_made_event(
        decision_type="purchase_order_generation",
        agent_name="采购Agent",
    )
    await agent.process_event(event)

    schedule = agent.get_schedule()
    assert len(schedule) == 1

    task = schedule[0]
    assert task["decision_type"] == "purchase_order_generation"
    assert task["status"] == "scheduled"
    assert task["priority"] > 0
    assert "scheduled_at" in task

    # 验证决策历史
    assert len(agent._decision_history) == 1
    assert agent._decision_history[0].decision_type == "scheduling"


# ========== test_priority_mapping ==========

async def test_priority_mapping():
    """优先级映射表正确分配优先级"""
    agent = await _setup_agent()

    # 合规违规应为最高优先级（10）
    event = _make_decision_made_event(
        decision_type="compliance_violation",
        agent_name="合规Agent",
    )
    await agent.process_event(event)

    schedule = agent.get_schedule()
    assert len(schedule) == 1
    assert schedule[0]["priority"] == 10

    # 采购订单生成应为较低优先级（4）
    event2 = _make_decision_made_event(
        decision_id="DEC-002",
        decision_type="purchase_order_generation",
        agent_name="采购Agent",
    )
    await agent.process_event(event2)

    schedule = agent.get_schedule()
    # 按优先级降序排列
    assert schedule[0]["priority"] >= schedule[1]["priority"]


# ========== test_high_priority_human_approval ==========

async def test_high_priority_human_approval():
    """高优先级任务（>=8）需人工审批"""
    agent = await _setup_agent()

    # 合规违规优先级=10 >= 8
    event = _make_decision_made_event(
        decision_type="compliance_violation",
        agent_name="合规Agent",
    )
    await agent.process_event(event)

    decisions = agent._decision_history
    assert len(decisions) == 1
    assert decisions[0].requires_human_approval is True


# ========== test_low_priority_auto_scheduled ==========

async def test_low_priority_auto_scheduled():
    """低优先级任务无需人工审批"""
    agent = await _setup_agent()

    # 需求预测优先级=2 < 8
    event = _make_decision_made_event(
        decision_type="demand_forecast",
        agent_name="需求先知Agent",
    )
    await agent.process_event(event)

    decisions = agent._decision_history
    assert len(decisions) == 1
    assert decisions[0].requires_human_approval is False


# ========== test_resource_allocation ==========

async def test_resource_allocation():
    """资源分配到正确的资源池"""
    agent = await _setup_agent()

    event = _make_decision_made_event(
        decision_type="purchase_order_generation",
        agent_name="采购Agent",
    )
    await agent.process_event(event)

    resources = agent.get_resource_allocation()
    assert len(resources) >= 1

    # 采购订单生成应分配到procurement_pool
    procurement_pool = next(
        (r for r in resources if r["resource_name"] == "procurement_pool"), None
    )
    assert procurement_pool is not None
    assert procurement_pool["allocated"] >= 1
    assert len(procurement_pool["tasks"]) >= 1


# ========== test_state_change_reallocation ==========

async def test_state_change_reallocation():
    """Agent状态变化触发任务重新调度"""
    agent = await _setup_agent()

    # 先创建一个任务
    event = _make_decision_made_event(
        decision_type="purchase_order_generation",
        agent_name="采购Agent",
    )
    await agent.process_event(event)

    schedule = agent.get_schedule()
    assert len(schedule) == 1
    assert schedule[0]["status"] == "scheduled"

    # 发送Agent暂停事件（使用source_agent匹配的agent_id）
    # 注意：_schedule_task中source_agent取自context.agent_name
    # _handle_state_change中匹配的是source_agent == agent_id
    state_event = _make_state_changed_event(
        agent_id="采购Agent",  # 匹配source_agent
        state="paused",
    )
    await agent.process_event(state_event)

    schedule = agent.get_schedule()
    assert schedule[0]["status"] == "pending_reallocation"


# ========== test_make_decision ==========

async def test_make_decision():
    """Agent调度决策功能"""
    agent = await _setup_agent()

    event = _make_decision_made_event(
        decision_type="demand_forecast",
        agent_name="需求先知Agent",
    )
    await agent.process_event(event)

    schedule = agent.get_schedule()
    task_id = schedule[0]["task_id"]

    decision = await agent.make_decision({"task_id": task_id})
    assert decision.decision_type == "normal_scheduling"
    assert decision.confidence > 0
    assert decision.requires_human_approval is False


# ========== test_make_decision_high_priority ==========

async def test_make_decision_high_priority():
    """高优先级任务决策需人工确认"""
    agent = await _setup_agent()

    event = _make_decision_made_event(
        decision_type="compliance_violation",
        agent_name="合规Agent",
    )
    await agent.process_event(event)

    schedule = agent.get_schedule()
    task_id = schedule[0]["task_id"]

    decision = await agent.make_decision({"task_id": task_id})
    assert decision.decision_type == "priority_scheduling"
    assert decision.requires_human_approval is True


# ========== test_make_decision_not_found ==========

async def test_make_decision_not_found():
    """不存在的任务ID返回错误决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"task_id": "nonexistent"})
    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_get_schedule_filter ==========

async def test_get_schedule_filter():
    """调度计划按状态过滤"""
    agent = await _setup_agent()

    # 创建2个任务
    for i in range(2):
        event = _make_decision_made_event(
            decision_id=f"DEC-{i+1:03d}",
            decision_type="demand_forecast",
            agent_name="需求先知Agent",
        )
        await agent.process_event(event)

    all_tasks = agent.get_schedule()
    assert len(all_tasks) == 2

    scheduled = agent.get_schedule(status="scheduled")
    assert len(scheduled) == 2


# ========== test_get_priorities_filter ==========

async def test_get_priorities_filter():
    """优先级记录按决策类型过滤"""
    agent = await _setup_agent()

    await agent.process_event(
        _make_decision_made_event(
            decision_id="DEC-001",
            decision_type="demand_forecast",
            agent_name="需求先知Agent",
        )
    )
    await agent.process_event(
        _make_decision_made_event(
            decision_id="DEC-002",
            decision_type="compliance_violation",
            agent_name="合规Agent",
        )
    )

    all_priorities = agent.get_priorities()
    assert len(all_priorities) == 2

    demand_priorities = agent.get_priorities(decision_type="demand_forecast")
    assert len(demand_priorities) == 1
    assert demand_priorities[0]["decision_type"] == "demand_forecast"


# ========== test_llm_fallback ==========

async def test_llm_fallback(monkeypatch):
    """LLM不可用时降级到规则模式"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "llm_enabled", False)

    agent = await _setup_agent()

    # LLM优先级确定应返回None
    result = await agent._determine_priority_llm(
        "purchase_order_generation", {"decision_type": "purchase_order_generation"}
    )
    assert result is None

    # 规则优先级应正常工作
    priority = agent.PRIORITY_MAP.get("compliance_violation", agent.DEFAULT_PRIORITY)
    assert priority == 10

    priority = agent.PRIORITY_MAP.get("unknown_type", agent.DEFAULT_PRIORITY)
    assert priority == agent.DEFAULT_PRIORITY


# ========== test_on_tick ==========

async def test_on_tick():
    """周期性任务：检测并解决调度冲突"""
    agent = await _setup_agent()

    # 创建超过资源容量的任务（total_capacity=10）
    for i in range(12):
        event = _make_decision_made_event(
            decision_id=f"DEC-{i+1:03d}",
            decision_type="purchase_order_generation",
            agent_name="采购Agent",
        )
        await agent.process_event(event)

    # 执行on_tick触发冲突检测
    await agent.on_tick()

    # 应有部分任务被延迟
    schedule = agent.get_schedule()
    deferred = [t for t in schedule if t["status"] == "deferred"]
    assert len(deferred) >= 1


# ========== test_priority_queue_ordering ==========

async def test_priority_queue_ordering():
    """待处理任务队列按优先级降序排列"""
    agent = await _setup_agent()

    # 按低到高的顺序创建任务
    for decision_type in ["demand_forecast", "purchase_order_generation", "compliance_violation"]:
        event = _make_decision_made_event(
            decision_id=f"DEC-{decision_type}",
            decision_type=decision_type,
            agent_name="测试Agent",
        )
        await agent.process_event(event)

    # 队列应按优先级降序排列
    priorities = [t["priority"] for t in agent._pending_tasks]
    assert priorities == sorted(priorities, reverse=True)
