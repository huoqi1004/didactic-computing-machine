"""
记账Agent测试
验证凭证生成、借贷平衡、凭证审批流程、日清日结
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.accounting.agent import BookkeepingAgent, AccountingRuleEngine, AccountingStandard


# ========== 辅助函数 ==========

async def _setup_agent() -> BookkeepingAgent:
    """创建并初始化记账Agent（不启动主循环，仅注册身份和订阅）"""
    agent = BookkeepingAgent()
    # 手动注册身份，不启动 run_loop
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_inventory_event(
    product_id: str = "prod-001",
    product_name: str = "Widget",
    quantity: int = 100,
    old_quantity: int = 0,
    unit_cost: float = 10.0,
) -> Event:
    """创建库存调整事件（含 old_quantity 供会计计算差额）"""
    return Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id=product_id,
        aggregate_type="inventory",
        event_data={
            "product_id": product_id,
            "product_name": product_name,
            "quantity": quantity,
            "old_quantity": old_quantity,
            "unit_cost": unit_cost,
        },
    )


def _make_replenishment_event(
    product_id: str = "prod-repl",
    total_cost: float = 1000.0,
) -> Event:
    """创建补货审批通过事件"""
    return Event(
        event_type=EventType.REPLENISHMENT_APPROVED,
        aggregate_id=product_id,
        aggregate_type="inventory",
        event_data={
            "product_id": product_id,
            "total_cost": total_cost,
        },
    )


# ========== test_voucher_generation ==========

async def test_voucher_generation():
    """业务事件触发凭证生成"""
    agent = await _setup_agent()

    # 库存减少事件（100 -> 50，减少50件）
    event = _make_inventory_event(
        product_id="prod-voucher",
        product_name="VoucherProduct",
        quantity=50,
        old_quantity=100,
        unit_cost=10.0,
    )
    await agent.process_event(event)

    # 验证凭证已生成
    vouchers = agent.get_vouchers()
    assert len(vouchers) == 1
    voucher = vouchers[0]

    assert voucher["voucher_type"] == "转"
    assert voucher["source_event"] == "inventory.adjusted"
    assert voucher["status"] == "draft"
    assert len(voucher["entries"]) == 2  # 借贷两条分录

    # 验证凭证金额 = 50件 * 10元 = 500元
    assert voucher["total_debit"] == 500.0
    assert voucher["total_credit"] == 500.0

    # 验证决策历史
    assert len(agent._decision_history) == 1
    decision = agent._decision_history[0]
    assert decision.decision_type == "voucher_generation"
    assert decision.confidence > 0
    assert decision.requires_human_approval is True


# ========== test_voucher_balanced ==========

async def test_voucher_balanced():
    """凭证借贷平衡"""
    agent = await _setup_agent()

    # 库存增加事件（100 -> 150，增加50件）
    event = _make_inventory_event(
        product_id="prod-balance",
        product_name="BalanceProduct",
        quantity=150,
        old_quantity=100,
        unit_cost=20.0,
    )
    await agent.process_event(event)

    vouchers = agent.get_vouchers()
    assert len(vouchers) == 1
    voucher = vouchers[0]

    # 借贷必须平衡
    assert voucher["is_balanced"] is True
    assert voucher["total_debit"] == voucher["total_credit"]

    # 金额 = 50件 * 20元 = 1000元
    expected_amount = 50 * 20.0
    assert voucher["total_debit"] == expected_amount
    assert voucher["total_credit"] == expected_amount

    # 验证借贷方向：库存增加 -> 借原材料，贷销售费用
    entries = voucher["entries"]
    assert len(entries) == 2
    # 一条借方分录，一条贷方分录
    debit_entry = [e for e in entries if e["debit"] > 0]
    credit_entry = [e for e in entries if e["credit"] > 0]
    assert len(debit_entry) == 1
    assert len(credit_entry) == 1
    assert debit_entry[0]["account_code"] == "1403"  # 原材料
    assert credit_entry[0]["account_code"] == "6601"  # 销售费用

    # 库存减少事件也应平衡
    event2 = _make_inventory_event(
        product_id="prod-balance2",
        product_name="BalanceProduct2",
        quantity=30,
        old_quantity=80,
        unit_cost=15.0,
    )
    await agent.process_event(event2)

    vouchers = agent.get_vouchers()
    assert len(vouchers) == 2
    voucher2 = vouchers[1]
    assert voucher2["is_balanced"] is True
    expected_amount2 = 50 * 15.0  # 减少50件 * 15元
    assert voucher2["total_debit"] == expected_amount2
    assert voucher2["total_credit"] == expected_amount2


# ========== test_voucher_approval ==========

async def test_voucher_approval():
    """凭证审批流程"""
    agent = await _setup_agent()

    # 生成凭证
    event = _make_inventory_event(
        product_id="prod-approval",
        product_name="ApprovalProduct",
        quantity=80,
        old_quantity=100,
        unit_cost=15.0,
    )
    await agent.process_event(event)

    vouchers = agent.get_vouchers()
    assert len(vouchers) == 1
    voucher_id = vouchers[0]["voucher_id"]

    # 初始状态为 draft
    assert vouchers[0]["status"] == "draft"

    # 待审批列表中应有此凭证
    pending = agent.get_pending_vouchers()
    assert len(pending) == 1
    assert pending[0]["voucher_id"] == voucher_id

    # 审批通过
    result = await agent.approve_voucher(voucher_id, approved=True)
    assert result is True

    # 验证状态已更新为 approved
    vouchers = agent.get_vouchers()
    assert vouchers[0]["status"] == "approved"

    # 待审批列表应为空
    pending = agent.get_pending_vouchers()
    assert len(pending) == 0

    # 生成第二张凭证并拒绝
    event2 = _make_inventory_event(
        product_id="prod-reject",
        product_name="RejectProduct",
        quantity=70,
        old_quantity=100,
        unit_cost=10.0,
    )
    await agent.process_event(event2)

    draft_vouchers = agent.get_vouchers(status="draft")
    assert len(draft_vouchers) == 1
    reject_id = draft_vouchers[0]["voucher_id"]

    # 审批拒绝
    result = await agent.approve_voucher(reject_id, approved=False)
    assert result is True

    rejected_vouchers = agent.get_vouchers(status="rejected")
    assert len(rejected_vouchers) == 1
    assert rejected_vouchers[0]["voucher_id"] == reject_id


# ========== test_daily_settlement ==========

async def test_daily_settlement():
    """日清日结"""
    agent = await _setup_agent()

    # 生成3张凭证
    for i in range(3):
        event = _make_inventory_event(
            product_id=f"prod-settle-{i}",
            product_name=f"SettlementProduct{i}",
            quantity=100 + i * 10,
            old_quantity=50,  # 库存增加
            unit_cost=10.0 + i,
        )
        await agent.process_event(event)

    vouchers = agent.get_vouchers()
    assert len(vouchers) == 3

    # 审批第一张凭证
    first_voucher_id = vouchers[0]["voucher_id"]
    await agent.approve_voucher(first_voucher_id, approved=True)

    # 执行日清日结
    settlement = await agent.daily_settlement()

    assert settlement["total_vouchers"] == 3
    assert settlement["approved"] == 1
    assert settlement["pending"] == 2  # 剩余2张仍为 draft
    assert settlement["rejected"] == 0
    assert settlement["balanced"] is True  # 借贷总和平衡
    assert settlement["total_debit"] == settlement["total_credit"]
    assert settlement["total_debit"] > 0
    assert settlement["standard"] == AccountingStandard.CAS


# ========== 额外测试：补货审批事件生成凭证 ==========

async def test_replenishment_voucher():
    """补货审批通过事件触发凭证生成"""
    agent = await _setup_agent()

    event = _make_replenishment_event(
        product_id="prod-repl-voucher",
        total_cost=5000.0,
    )
    await agent.process_event(event)

    vouchers = agent.get_vouchers()
    assert len(vouchers) == 1
    voucher = vouchers[0]

    assert voucher["source_event"] == "inventory.replenishment_approved"
    assert voucher["voucher_type"] == "转"
    assert voucher["is_balanced"] is True
    # 金额应等于 total_cost
    assert voucher["total_debit"] == 5000.0
    assert voucher["total_credit"] == 5000.0

    # 验证分录：借原材料，贷应付账款
    entries = voucher["entries"]
    debit_entry = [e for e in entries if e["debit"] > 0]
    credit_entry = [e for e in entries if e["credit"] > 0]
    assert len(debit_entry) == 1
    assert len(credit_entry) == 1
    assert debit_entry[0]["account_code"] == "1403"  # 原材料
    assert credit_entry[0]["account_code"] == "2202"  # 应付账款


# ========== 额外测试：无变化不生成凭证 ==========

async def test_no_voucher_for_zero_adjustment():
    """库存无变化时不生成凭证"""
    agent = await _setup_agent()

    # old_quantity == quantity，差额为0，不生成凭证
    event = _make_inventory_event(
        product_id="prod-nochange",
        product_name="NoChangeProduct",
        quantity=100,
        old_quantity=100,  # 无变化
        unit_cost=10.0,
    )
    await agent.process_event(event)

    vouchers = agent.get_vouchers()
    assert len(vouchers) == 0


# ========== 额外测试：make_decision ==========

async def test_make_decision():
    """Agent决策功能"""
    agent = await _setup_agent()

    # 先生成一张凭证
    event = _make_inventory_event(
        product_id="prod-decision",
        product_name="DecisionProduct",
        quantity=80,
        old_quantity=100,
        unit_cost=10.0,
    )
    await agent.process_event(event)

    vouchers = agent.get_vouchers()
    voucher_id = vouchers[0]["voucher_id"]

    # 做出过账决策
    decision = await agent.make_decision({"voucher_id": voucher_id})

    assert decision.decision_type == "voucher_posting"
    assert decision.confidence > 0
    assert "过账" in decision.action
    assert decision.requires_human_approval is True
    assert decision.context["voucher_id"] == voucher_id


# ========== 额外测试：make_decision 不存在的凭证 ==========

async def test_make_decision_nonexistent_voucher():
    """对不存在的凭证做出决策返回错误"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"voucher_id": "nonexistent"})

    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== 额外测试：会计准则引擎 ==========

def test_accounting_rule_engine():
    """会计准则引擎直接测试"""
    engine = AccountingRuleEngine()

    # 测试补货审批事件
    event = Event(
        event_type=EventType.REPLENISHMENT_APPROVED,
        aggregate_id="prod-engine",
        aggregate_type="inventory",
        event_data={"total_cost": 3000.0},
    )
    voucher = engine.generate_voucher(event, AccountingStandard.CAS)

    assert voucher is not None
    assert voucher.voucher_type == "转"
    assert voucher.standard == AccountingStandard.CAS
    assert voucher.is_balanced is True
    assert voucher.total_debit == 3000.0

    # 测试库存调整事件
    inv_event = Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-engine2",
        aggregate_type="inventory",
        event_data={
            "quantity": 200,
            "old_quantity": 150,
            "unit_cost": 5.0,
        },
    )
    voucher2 = engine.generate_voucher(inv_event, AccountingStandard.IFRS)

    assert voucher2 is not None
    assert voucher2.standard == AccountingStandard.IFRS
    assert voucher2.is_balanced is True
    # 50件 * 5元 = 250
    assert voucher2.total_debit == 250.0


# ========== 额外测试：空日清日结 ==========

async def test_empty_daily_settlement():
    """无凭证时日清日结"""
    agent = await _setup_agent()

    settlement = await agent.daily_settlement()

    assert settlement["total_vouchers"] == 0
    assert settlement["approved"] == 0
    assert settlement["pending"] == 0
    assert settlement["rejected"] == 0
    assert settlement["total_debit"] == 0
    assert settlement["total_credit"] == 0
    assert settlement["balanced"] is True  # 0 == 0
