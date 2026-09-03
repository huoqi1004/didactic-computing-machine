# -*- coding: utf-8 -*-
"""
质量Agent测试
验证质检记录管理、质量控制、改进措施管理、标准维护与决策功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.quality.agent import QualityAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> QualityAgent:
    """创建并初始化质量Agent（不启动主循环，仅注册身份和订阅）"""
    agent = QualityAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_inspection_passed_event(
    inspection_id: str = "insp-001",
    product: str = "智能终端A",
    batch: str = "BAT-20260601-01",
) -> Event:
    """创建质检通过事件"""
    return Event(
        event_type=EventType.QUALITY_INSPECTION_PASSED,
        aggregate_id=inspection_id,
        aggregate_type="inspection",
        event_data={
            "inspection_id": inspection_id,
            "product": product,
            "batch": batch,
            "inspection_type": "incoming",
            "result": "passed",
            "defects": [],
            "notes": "质检通过",
        },
    )


def _make_inspection_failed_event(
    inspection_id: str = "insp-001",
    product: str = "智能终端A",
    batch: str = "BAT-20260601-01",
    defects: list = None,
) -> Event:
    """创建质检不合格事件"""
    return Event(
        event_type=EventType.QUALITY_INSPECTION_FAILED,
        aggregate_id=inspection_id,
        aggregate_type="inspection",
        event_data={
            "inspection_id": inspection_id,
            "product": product,
            "batch": batch,
            "inspection_type": "incoming",
            "result": "failed",
            "defects": defects or [{"name": "外观破损", "severity": "major", "count": 2}],
            "notes": "质检不合格",
        },
    )


def _make_production_completed_event(
    product_name: str = "智能终端C",
    batch: str = "BAT-20260701-05",
    order_id: str = "ORD-001",
) -> Event:
    """创建生产完成事件"""
    return Event(
        event_type=EventType.PRODUCTION_COMPLETED,
        aggregate_id=order_id,
        aggregate_type="production_order",
        event_data={
            "product_name": product_name,
            "batch": batch,
            "order_id": order_id,
            "quantity": 500,
            "completed_at": "2026-07-01T16:00:00+00:00",
        },
    )


def _make_improvement_proposed_event(
    title: str = "来料检验流程优化建议",
    description: str = "建议增加来料抽检比例",
    root_cause: str = "抽检比例不足导致漏检",
    action_plan: str = "调整抽检比例至10%",
    owner: str = "质量部",
    due_date: str = "2026-08-31",
) -> Event:
    """创建改进措施建议事件"""
    return Event(
        event_type=EventType.QUALITY_IMPROVEMENT_PROPOSED,
        aggregate_id="imp-ext-001",
        aggregate_type="improvement",
        event_data={
            "title": title,
            "description": description,
            "root_cause": root_cause,
            "action_plan": action_plan,
            "owner": owner,
            "due_date": due_date,
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "质量Agent"
    assert agent.domain == "quality"
    assert agent.security_clearance == "internal"
    assert "quality_inspection" in agent.capabilities
    assert "quality_control" in agent.capabilities
    assert "improvement_management" in agent.capabilities
    assert "standard_management" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None
    assert agent.DEFECT_RATE_THRESHOLD == 0.15
    assert agent.HUMAN_APPROVAL_ON_FAILED is True


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载4个质量标准与3条质检记录"""
    agent = await _setup_agent()

    assert len(agent._standards) == 4
    assert len(agent._inspections) == 3

    # 验证质量标准
    assert "std-001" in agent._standards
    assert "std-002" in agent._standards
    assert "std-003" in agent._standards
    assert "std-004" in agent._standards
    assert agent._standards["std-001"]["category"] == "incoming"

    # 验证质检记录
    assert "insp-001" in agent._inspections
    assert "insp-002" in agent._inspections
    assert "insp-003" in agent._inspections
    assert agent._inspections["insp-001"]["result"] == "passed"
    assert agent._inspections["insp-002"]["result"] == "failed"
    assert agent._inspections["insp-003"]["result"] == "passed"


# ========== test_create_inspection ==========

async def test_create_inspection():
    """创建质检记录"""
    agent = await _setup_agent()

    inspection = agent.create_inspection(
        product="智能终端D",
        batch="BAT-20260701-06",
        inspection_type="incoming",
        inspector="张质检",
        items=[
            {"name": "外观", "result": "pending"},
            {"name": "尺寸", "result": "pending"},
        ],
    )

    assert inspection["product"] == "智能终端D"
    assert inspection["batch"] == "BAT-20260701-06"
    assert inspection["inspection_type"] == "incoming"
    assert inspection["inspector"] == "张质检"
    assert inspection["result"] == "pending"
    assert inspection["status"] == "pending"
    assert inspection["defects"] == []

    # 验证自动匹配质量标准
    assert inspection["standard_id"] == "std-001"

    # 验证已存入内部存储
    assert inspection["inspection_id"] in agent._inspections


# ========== test_record_result_passed ==========

async def test_record_result_passed():
    """记录质检通过结果"""
    agent = await _setup_agent()

    inspection = agent.create_inspection(
        product="智能终端E",
        batch="BAT-20260702-07",
        inspection_type="finished",
        inspector="王质检",
        items=[{"name": "功能测试", "result": "pass"}],
    )

    result = await agent.record_result(
        inspection_id=inspection["inspection_id"],
        result="passed",
        notes="成品检验合格",
    )

    assert result["result"] == "passed"
    assert result["status"] == "completed"
    assert result["notes"] == "成品检验合格"
    assert result["completed_at"] is not None

    # 验证决策已记录
    decisions = [d for d in agent._decision_history
                 if d.decision_type == "quality_inspection_result"]
    assert len(decisions) >= 1
    assert decisions[-1].confidence == 0.95
    assert decisions[-1].requires_human_approval is False


# ========== test_record_result_failed ==========

async def test_record_result_failed():
    """记录质检不合格结果"""
    agent = await _setup_agent()

    inspection = agent.create_inspection(
        product="智能终端F",
        batch="BAT-20260703-08",
        inspection_type="process",
        inspector="李检验",
        items=[{"name": "过程抽检", "result": "fail"}],
    )

    defects = [{"name": "尺寸超差", "severity": "major", "count": 5}]

    result = await agent.record_result(
        inspection_id=inspection["inspection_id"],
        result="failed",
        defects=defects,
        notes="过程抽检不合格",
    )

    assert result["result"] == "failed"
    assert result["status"] == "completed"
    assert result["defects"] == defects
    assert result["notes"] == "过程抽检不合格"

    # 验证决策已记录（不合格需人工审批）
    decisions = [d for d in agent._decision_history
                 if d.decision_type == "quality_inspection_result"]
    assert len(decisions) >= 1
    assert decisions[-1].requires_human_approval is True
    assert decisions[-1].confidence == 0.85


# ========== test_production_completed_event ==========

async def test_production_completed_event():
    """生产完成事件自动触发成品质检"""
    agent = await _setup_agent()

    original_count = len(agent._inspections)

    event = _make_production_completed_event(
        product_name="智能终端G",
        batch="BAT-20260704-09",
    )
    await agent.process_event(event)

    # 验证自动创建了成品质检记录
    assert len(agent._inspections) == original_count + 1

    # 查找新创建的质检记录
    new_inspections = [i for i in agent._inspections.values()
                       if i["product"] == "智能终端G"]
    assert len(new_inspections) == 1

    inspection = new_inspections[0]
    assert inspection["inspection_type"] == "finished"
    assert inspection["inspector"] == "质量Agent"
    assert inspection["result"] == "pending"
    assert inspection["standard_id"] == "std-003"  # 成品检验标准


# ========== test_inspection_passed_event ==========

async def test_inspection_passed_event():
    """质检通过事件同步质检记录状态"""
    agent = await _setup_agent()

    # insp-002初始状态为failed，发送passed事件改为passed
    assert agent._inspections["insp-002"]["result"] == "failed"

    event = _make_inspection_passed_event(inspection_id="insp-002")
    await agent.process_event(event)

    inspection = agent._inspections["insp-002"]
    assert inspection["result"] == "passed"
    assert inspection["status"] == "completed"
    assert inspection["completed_at"] is not None


# ========== test_inspection_failed_event ==========

async def test_inspection_failed_event():
    """质检不合格事件同步状态并触发改进评估"""
    agent = await _setup_agent()

    # insp-001初始状态为passed，发送failed事件改为failed
    assert agent._inspections["insp-001"]["result"] == "passed"

    defects = [{"name": "外观破损", "severity": "major", "count": 3}]
    event = _make_inspection_failed_event(
        inspection_id="insp-001",
        defects=defects,
    )
    await agent.process_event(event)

    inspection = agent._inspections["insp-001"]
    assert inspection["result"] == "failed"
    assert inspection["status"] == "completed"
    assert inspection["defects"] == defects

    # 验证不合格处置决策已记录（需人工审批）
    decisions = [d for d in agent._decision_history
                 if d.decision_type == "quality_failed_handling"]
    assert len(decisions) >= 1
    assert decisions[-1].requires_human_approval is True


# ========== test_improvement_proposed_event ==========

async def test_improvement_proposed_event():
    """改进措施建议事件创建改进措施"""
    agent = await _setup_agent()

    original_count = len(agent._improvements)

    event = _make_improvement_proposed_event(
        title="来料检验流程优化建议",
        description="建议增加来料抽检比例",
    )
    await agent.process_event(event)

    # 验证改进措施已创建
    assert len(agent._improvements) == original_count + 1

    improvement = agent._improvements[-1]
    assert improvement["title"] == "来料检验流程优化建议"
    assert improvement["description"] == "建议增加来料抽检比例"
    assert improvement["status"] == "proposed"


# ========== test_create_standard ==========

async def test_create_standard():
    """创建质量标准"""
    agent = await _setup_agent()

    standard = agent.create_standard(
        name="包装检验标准",
        category="finished",
        criteria="包装完整、标识清晰、防护到位",
        tolerance="破损率 0%",
    )

    assert standard["name"] == "包装检验标准"
    assert standard["category"] == "finished"
    assert standard["criteria"] == "包装完整、标识清晰、防护到位"
    assert standard["tolerance"] == "破损率 0%"
    assert standard["version"] == "1.0"
    assert standard["status"] == "active"

    # 验证已存入内部存储
    assert standard["standard_id"] in agent._standards


# ========== test_create_improvement ==========

async def test_create_improvement():
    """创建改进措施"""
    agent = await _setup_agent()

    improvement = agent.create_improvement(
        title="过程检验不合格率偏高改进",
        description="近期过程抽检不合格率达5%",
        root_cause="操作人员培训不足",
        action_plan="1.组织专项培训 2.加强首件检验 3.增加过程巡检频次",
        owner="生产部",
        due_date="2026-08-15",
    )

    assert improvement["title"] == "过程检验不合格率偏高改进"
    assert improvement["root_cause"] == "操作人员培训不足"
    assert improvement["owner"] == "生产部"
    assert improvement["status"] == "proposed"
    assert improvement["result"] is None
    assert improvement["effectiveness"] is None

    # 验证已追加到改进措施列表
    assert improvement in agent._improvements


# ========== test_complete_improvement ==========

async def test_complete_improvement():
    """完成改进措施"""
    agent = await _setup_agent()

    improvement = agent.create_improvement(
        title="来料检验流程优化",
        description="优化来料检验流程",
        root_cause="检验流程繁琐",
        action_plan="简化检验流程",
        owner="质量部",
        due_date="2026-08-31",
    )

    result = await agent.complete_improvement(
        improvement_id=improvement["improvement_id"],
        result="检验效率提升30%",
        effectiveness="effective",
    )

    assert result["status"] == "completed"
    assert result["result"] == "检验效率提升30%"
    assert result["effectiveness"] == "effective"
    assert result["completed_at"] is not None

    # 验证决策已记录
    decisions = [d for d in agent._decision_history
                 if d.decision_type == "quality_improvement_completion"]
    assert len(decisions) >= 1


# ========== test_make_decision_failed ==========

async def test_make_decision_failed():
    """不合格批次决策需人工确认"""
    agent = await _setup_agent()

    # insp-002为failed状态
    decision = await agent.make_decision({"inspection_id": "insp-002"})

    assert decision.decision_type == "quality_disposition"
    assert decision.confidence == 0.9
    assert decision.requires_human_approval is True
    assert "insp-002" in decision.action
    assert "defects" in decision.context


# ========== test_make_decision_passed ==========

async def test_make_decision_passed():
    """合格批次自动放行"""
    agent = await _setup_agent()

    # insp-001为passed状态
    decision = await agent.make_decision({"inspection_id": "insp-001"})

    assert decision.decision_type == "quality_release"
    assert decision.confidence == 0.95
    assert decision.requires_human_approval is False
    assert "insp-001" in decision.action


# ========== test_make_decision_pending ==========

async def test_make_decision_pending():
    """待检状态等待质检结果"""
    agent = await _setup_agent()

    # 创建一个pending状态的质检记录
    inspection = agent.create_inspection(
        product="智能终端H",
        batch="BAT-20260705-10",
        inspection_type="incoming",
        inspector="张质检",
        items=[{"name": "外观", "result": "pending"}],
    )

    decision = await agent.make_decision({
        "inspection_id": inspection["inspection_id"],
    })

    assert decision.decision_type == "quality_pending"
    assert decision.confidence == 0.5
    assert decision.requires_human_approval is False


# ========== test_make_decision_not_found ==========

async def test_make_decision_not_found():
    """不存在的质检记录返回错误决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"inspection_id": "nonexistent"})

    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_get_inspections_filter ==========

async def test_get_inspections_filter():
    """质检记录按状态与产品过滤"""
    agent = await _setup_agent()

    all_inspections = agent.get_inspections()
    assert len(all_inspections) == 3

    # 按状态过滤
    completed = agent.get_inspections(status="completed")
    assert len(completed) == 3

    # 按产品过滤
    terminal_a = agent.get_inspections(product="智能终端A")
    assert len(terminal_a) == 2
    assert all(i["product"] == "智能终端A" for i in terminal_a)

    terminal_b = agent.get_inspections(product="智能终端B")
    assert len(terminal_b) == 1


# ========== test_defect_rate_evaluation ==========

async def test_defect_rate_evaluation():
    """不合格率超阈值自动发起改进建议"""
    agent = await _setup_agent()

    # 种子数据: 3条已完成质检，1条failed(insp-002)，不合格率=1/3=0.3333>0.15
    original_improvement_count = len(agent._improvements)

    await agent._evaluate_defect_rate()

    # 验证自动创建了改进措施
    assert len(agent._improvements) == original_improvement_count + 1

    improvement = agent._improvements[-1]
    assert "不合格率预警改进" in improvement["title"]
    assert improvement["status"] == "proposed"
    assert improvement["owner"] == "质量部"


# ========== test_on_tick ==========

async def test_on_tick():
    """周期性任务：检查不合格率趋势"""
    agent = await _setup_agent()

    # 种子数据有3条质检记录，on_tick应无异常执行
    await agent.on_tick()

    # 增加质检记录使总数达到5条以上，且不合格率仍超阈值
    for i in range(2):
        agent.create_inspection(
            product=f"智能终端Extra{i}",
            batch=f"BAT-20260706-{i:02d}",
            inspection_type="incoming",
            inspector="张质检",
            items=[{"name": "外观", "result": "pending"}],
        )

    # on_tick在总数>=5且不合格率超阈值时应无异常
    await agent.on_tick()

    stats = agent.get_stats()
    assert stats["total_inspections"] >= 5


# ========== test_get_stats ==========

async def test_get_stats():
    """质量统计信息"""
    agent = await _setup_agent()

    stats = agent.get_stats()

    # 种子数据: 3条质检记录, 4个标准
    assert stats["total_inspections"] == 3
    assert stats["completed"] == 3
    assert stats["pending"] == 0
    assert stats["passed"] == 2
    assert stats["failed"] == 1
    assert stats["pass_rate"] == round(2 / 3, 4)
    assert stats["defect_rate"] == round(1 / 3, 4)
    assert stats["total_standards"] == 4
    assert stats["active_standards"] == 4
    assert stats["total_improvements"] == 0
    assert stats["defect_rate_threshold"] == 0.15
    assert "by_type" in stats
    assert "improvements_by_status" in stats

    # 验证按检验类型统计
    assert "incoming" in stats["by_type"]
    assert stats["by_type"]["incoming"]["total"] == 1
    assert stats["by_type"]["incoming"]["passed"] == 1
    assert "process" in stats["by_type"]
    assert stats["by_type"]["process"]["failed"] == 1
    assert "finished" in stats["by_type"]
    assert stats["by_type"]["finished"]["passed"] == 1
