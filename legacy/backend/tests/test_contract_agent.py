# -*- coding: utf-8 -*-
"""
合同Agent测试
验证合同管理、合同执行、合同归档与模板管理功能
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.contract.agent import ContractAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> ContractAgent:
    """创建并初始化合同Agent（不启动主循环，仅注册身份和订阅）"""
    agent = ContractAgent()
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

    assert agent.name == "合同Agent"
    assert agent.domain == "contract"
    assert agent.security_clearance == "confidential"
    assert "contract_management" in agent.capabilities
    assert "contract_execution" in agent.capabilities
    assert "contract_archiving" in agent.capabilities
    assert "template_management" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载正确"""
    agent = await _setup_agent()

    # 3个种子合同
    contracts = agent.get_contracts()
    assert len(contracts) == 3

    # 3个模板
    templates = agent.get_templates()
    assert len(templates) == 3

    # 有执行中的合同
    executing = agent.get_contracts(status="executing")
    assert len(executing) >= 1


# ========== test_get_contracts_filter ==========

async def test_get_contracts_filter():
    """合同列表过滤"""
    agent = await _setup_agent()

    # 按类型过滤
    sales = agent.get_contracts(type="sales")
    assert len(sales) >= 1

    procurement = agent.get_contracts(type="procurement")
    assert len(procurement) >= 1

    # 按状态过滤
    executing = agent.get_contracts(status="executing")
    assert len(executing) >= 1


# ========== test_create_contract ==========

async def test_create_contract():
    """创建合同"""
    agent = await _setup_agent()

    contract = await agent.create_contract(
        title="测试销售合同",
        type="sales",
        counterparty="测试客户",
        amount=100000.0,
        department="销售部",
        start_date="2026-07-01",
        end_date="2027-06-30",
        terms="测试条款",
    )

    assert contract["title"] == "测试销售合同"
    assert contract["type"] == "sales"
    assert contract["amount"] == 100000.0
    assert contract["status"] == "draft"
    assert contract["contract_id"].startswith("CTR-")


# ========== test_approve_contract ==========

async def test_approve_contract():
    """审批合同"""
    agent = await _setup_agent()

    # 创建新合同（draft状态）
    contract = await agent.create_contract(
        title="待审批合同",
        type="service",
        counterparty="服务方",
        amount=50000.0,
        department="技术部",
        start_date="2026-07-01",
        end_date="2026-12-31",
        terms="服务条款",
    )

    approved = await agent.approve_contract(
        contract["contract_id"],
        approved_by="审批人",
        comment="同意",
    )

    assert "error" not in approved
    assert approved["status"] == "approved"
    assert approved["approved_by"] == "审批人"
    assert approved["approval_comment"] == "同意"


# ========== test_approve_contract_invalid_status ==========

async def test_approve_contract_invalid_status():
    """非草拟状态合同无法审批"""
    agent = await _setup_agent()

    # 获取执行中的合同
    executing = agent.get_contracts(status="executing")
    assert len(executing) > 0

    result = await agent.approve_contract(
        executing[0]["contract_id"],
        approved_by="审批人",
        comment="测试",
    )
    assert "error" in result
    assert "invalid_status" in result["error"]


# ========== test_execute_contract ==========

async def test_execute_contract():
    """执行合同"""
    agent = await _setup_agent()

    # 使用已审批的种子合同
    approved = agent.get_contracts(status="approved")
    assert len(approved) > 0

    executed = await agent.execute_contract(approved[0]["contract_id"])
    assert "error" not in executed
    assert executed["status"] == "executing"


# ========== test_complete_contract ==========

async def test_complete_contract():
    """完成合同"""
    agent = await _setup_agent()

    # 使用执行中的种子合同
    executing = agent.get_contracts(status="executing")
    assert len(executing) > 0

    completed = await agent.complete_contract(
        executing[0]["contract_id"],
        completion_notes="合同已履行完毕",
    )
    assert "error" not in completed
    assert completed["status"] == "completed"
    assert completed["completion_notes"] == "合同已履行完毕"


# ========== test_get_templates_filter ==========

async def test_get_templates_filter():
    """模板列表过滤"""
    agent = await _setup_agent()

    sales_templates = agent.get_templates(type="sales")
    assert len(sales_templates) == 1
    assert sales_templates[0]["type"] == "sales"

    service_templates = agent.get_templates(type="service")
    assert len(service_templates) == 1


# ========== test_create_template ==========

async def test_create_template():
    """创建合同模板"""
    agent = await _setup_agent()

    template = await agent.create_template(
        name="保密协议模板",
        type="service",
        content="保密协议内容",
        clauses=["保密范围", "保密期限", "违约责任"],
    )

    assert template["name"] == "保密协议模板"
    assert template["type"] == "service"
    assert template["status"] == "active"
    assert template["template_id"].startswith("TPL-")


# ========== test_get_expiring_contracts ==========

async def test_get_expiring_contracts():
    """获取即将到期的合同"""
    agent = await _setup_agent()

    expiring = agent.get_expiring_contracts(days=30)
    # 种子数据中有一个合同20天后到期，应该在30天预警范围内
    assert isinstance(expiring, list)
    for contract in expiring:
        assert "days_remaining" in contract
        assert contract["days_remaining"] <= 30


# ========== test_get_stats ==========

async def test_get_stats():
    """获取合同统计"""
    agent = await _setup_agent()

    stats = agent.get_stats()
    assert stats["contracts_total"] >= 3
    assert "status_distribution" in stats
    assert "type_distribution" in stats
    assert "templates_total" in stats
    assert stats["templates_total"] >= 3


# ========== test_make_decision_create ==========

async def test_make_decision_create():
    """创建合同决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action_type": "create",
        "title": "测试合同",
        "amount": 100000,
    })

    assert decision.decision_type == "contract_creation"
    assert decision.confidence == 0.9


# ========== test_make_decision_create_large ==========

async def test_make_decision_create_large():
    """大额合同需人工审批"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action_type": "create",
        "title": "大额合同",
        "amount": 2000000,
    })

    assert decision.decision_type == "contract_creation"
    assert decision.requires_human_approval is True


# ========== test_make_decision_approve ==========

async def test_make_decision_approve():
    """审批合同决策"""
    agent = await _setup_agent()

    contracts = agent.get_contracts()
    decision = await agent.make_decision({
        "action_type": "approve",
        "contract_id": contracts[0]["contract_id"],
    })

    assert decision.decision_type == "contract_approval"
    assert decision.confidence == 0.9


# ========== test_make_decision_approve_nonexistent ==========

async def test_make_decision_approve_nonexistent():
    """审批不存在的合同决策返回错误"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action_type": "approve",
        "contract_id": "NONEXISTENT",
    })

    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_contract_created_event ==========

async def test_contract_created_event():
    """合同创建事件同步"""
    agent = await _setup_agent()

    initial_count = len(agent.get_contracts())

    event = Event(
        event_type=EventType.CONTRACT_CREATED,
        aggregate_id="CTR-EXT-001",
        aggregate_type="contract",
        event_data={
            "contract_id": "CTR-EXT-001",
            "title": "外部合同",
            "type": "sales",
            "counterparty": "外部方",
            "amount": 200000,
        },
    )
    await agent.process_event(event)

    assert len(agent.get_contracts()) == initial_count + 1
    contract = agent.get_contract("CTR-EXT-001")
    assert contract is not None
    assert contract["title"] == "外部合同"


# ========== test_opportunity_won_event ==========

async def test_opportunity_won_event():
    """商机赢单自动生成销售合同"""
    agent = await _setup_agent()

    initial_count = len(agent.get_contracts())

    event = Event(
        event_type=EventType.OPPORTUNITY_WON,
        aggregate_id="OPP-001",
        aggregate_type="opportunity",
        event_data={
            "opportunity_id": "OPP-001",
            "customer_name": "赢单客户",
            "amount": 300000,
        },
    )
    await agent.process_event(event)

    assert len(agent.get_contracts()) == initial_count + 1
    new_contracts = agent.get_contracts(type="sales")
    # 应包含自动生成的销售合同
    assert any("赢单客户" in c["title"] for c in new_contracts)


# ========== test_agent_start_stop ==========

async def test_agent_start_stop():
    """Agent启动与停止状态管理"""
    from app.core.config import AgentState

    agent = ContractAgent()
    await agent.start()

    assert agent.state == AgentState.RUNNING
    assert agent.identity is not None

    await agent.stop()
    assert agent.state == AgentState.TERMINATED
