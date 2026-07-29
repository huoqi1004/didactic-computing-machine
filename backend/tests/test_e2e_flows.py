# -*- coding: utf-8 -*-
"""
端到端(E2E)测试体系
覆盖跨多个Agent的完整业务流程，验证事件驱动架构下的Agent协作。

测试场景：
1. 库存补货全流程（库存不足 → 补货建议 → 采购订单）
2. 凭证过账触发税务计算全流程（凭证生成 → 过账 → 税额计算）
3. 合规检查全流程（采购审批 → 控制点检查 → 违规发现）
4. 多Agent协作 - 采购到支付全流程（采购审批 → 支付排程 → 进项税计算）
5. 内审警报触发合规审查（高风险交易 → 审计警报 → 合规违规 → 流程冻结）
6. 需求预测到生产计划全流程（库存消耗 → 需求预测 → 生产订单）
7. 事件总线统计与死信队列（失败handler → 重试耗尽 → 死信队列）
8. Agent熔断器机制（连续失败 → 熔断 → 恢复）
"""
import asyncio
import pytest
from datetime import datetime, timezone, timedelta

from app.core.event.bus import event_bus
from app.core.event.models import Event, EventType, EventSubscription
from app.core.agent.base import CircuitBreaker
from app.core.config import AgentState
from app.core.security.did import did_registry

from app.agents.inventory.agent import InventoryGuardianAgent
from app.agents.procurement.agent import ProcurementAgent
from app.agents.accounting.agent import BookkeepingAgent
from app.agents.tax.agent import TaxAgent
from app.agents.compliance.agent import ComplianceAgent
from app.agents.treasury.agent import TreasuryAgent
from app.agents.audit.agent import InternalAuditAgent
from app.agents.demand.agent import DemandForecastAgent
from app.agents.production.agent import ProductionAgent


# ========== 辅助工具 ==========

class EventCollector:
    """
    事件收集器：订阅事件总线，按事件类型收集事件

    用于在E2E测试中验证事件是否被正确发布和传递。
    """

    def __init__(self):
        self.events: list[Event] = []
        self._subscriptions: list[EventSubscription] = []

    def subscribe(self, event_types: list[EventType], subscriber_id: str = "test_collector"):
        """订阅指定事件类型"""
        sub = EventSubscription(
            subscriber_id=subscriber_id,
            event_types=event_types,
            handler=self._handle,
        )
        self._subscriptions.append(sub)
        event_bus.subscribe(sub)

    async def _handle(self, event: Event):
        """事件处理：收集事件"""
        self.events.append(event)

    def get_events_by_type(self, event_type: EventType) -> list[Event]:
        """获取指定类型的所有事件"""
        return [e for e in self.events if e.event_type == event_type]

    def clear(self):
        """清空已收集的事件"""
        self.events.clear()


async def _wait_for_propagation(seconds: float = 1.0):
    """等待事件总线完成事件传播"""
    await asyncio.sleep(seconds)


def _make_event(
    event_type: EventType,
    aggregate_id: str,
    aggregate_type: str,
    event_data: dict,
) -> Event:
    """创建事件实例"""
    return Event(
        event_type=event_type,
        aggregate_id=aggregate_id,
        aggregate_type=aggregate_type,
        event_data=event_data,
    )


async def _start_agents(*agents):
    """启动多个Agent"""
    for agent in agents:
        await agent.start()


async def _stop_agents(*agents):
    """停止多个Agent"""
    for agent in agents:
        await agent.stop()


# ========== E2E测试夹具 ==========

@pytest.fixture(autouse=True)
async def e2e_event_bus():
    """每个E2E测试前启动事件总线，测试后停止并清理"""
    await event_bus.start()
    yield event_bus
    await event_bus.stop()
    event_bus._subscriptions.clear()


# ========== 场景1: 库存补货全流程 ==========

async def test_库存补货全流程_从库存不足到采购订单创建():
    """
    E2E场景1: 库存补货全流程

    流程: 库存调整(低于再订货点) → 库存Agent生成补货建议 → 采购Agent创建采购订单

    验证:
    - 库存Agent内部状态更新（_inventory_cache, _replenishment_proposals）
    - REPLENISHMENT_PROPOSED 事件发布
    - 采购Agent响应并创建采购订单（_purchase_orders）
    - PURCHASE_ORDER_CREATED 事件发布
    """
    # 1. 创建库存守护Agent和采购Agent
    inventory_agent = InventoryGuardianAgent()
    procurement_agent = ProcurementAgent()

    # 事件收集器：捕获补货建议和采购订单创建事件
    collector = EventCollector()
    collector.subscribe([
        EventType.REPLENISHMENT_PROPOSED,
        EventType.PURCHASE_ORDER_CREATED,
    ], subscriber_id="e2e_collector_s1")

    # 2. 启动Agent（注册身份、订阅事件、启动主循环）
    await _start_agents(inventory_agent, procurement_agent)
    await _wait_for_propagation(0.5)

    # 3. 模拟库存调整：库存数量低于再订货点
    low_stock_event = _make_event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="prod-001",
        aggregate_type="inventory",
        event_data={
            "product_id": "prod-001",
            "product_name": "原材料A",
            "quantity": 30,           # 当前库存30
            "safety_stock": 50,       # 安全库存50
            "reorder_point": 100,     # 再订货点100（30 <= 100 触发补货）
            "unit_cost": 10.0,
            "warehouse": "WH-A",
        },
    )
    await event_bus.publish(low_stock_event)

    # 4. 等待事件传播：库存Agent → 补货建议 → 采购Agent → 采购订单
    await _wait_for_propagation(2.0)

    # 5. 验证库存Agent内部状态
    assert len(inventory_agent._inventory_cache) == 1
    inv = inventory_agent._inventory_cache["prod-001"]
    assert inv.quantity == 30
    assert inv.needs_replenishment() is True

    # 验证补货建议已生成
    proposals = inventory_agent.get_proposals()
    assert len(proposals) >= 1
    proposal = proposals[0]
    assert proposal["product_id"] == "prod-001"
    assert proposal["reorder_qty"] > 0
    assert proposal["total_cost"] > 0

    # 6. 验证REPLENISHMENT_PROPOSED事件已发布
    replenish_events = collector.get_events_by_type(EventType.REPLENISHMENT_PROPOSED)
    assert len(replenish_events) >= 1
    assert replenish_events[0].event_data["product_id"] == "prod-001"

    # 7. 验证采购Agent响应并创建采购订单
    po_list = procurement_agent.get_purchase_orders()
    assert len(po_list) >= 1
    po = po_list[0]
    assert po["product_id"] == "prod-001"
    assert po["quantity"] > 0
    assert po["total_amount"] > 0
    assert po["supplier_id"] is not None

    # 8. 验证PURCHASE_ORDER_CREATED事件已发布
    po_events = collector.get_events_by_type(EventType.PURCHASE_ORDER_CREATED)
    assert len(po_events) >= 1

    # 9. 验证事件总线统计
    stats = event_bus.get_stats()
    assert stats["published"] >= 2  # 至少补货建议 + 采购订单创建

    # 清理
    await _stop_agents(inventory_agent, procurement_agent)


# ========== 场景2: 凭证过账触发税务计算全流程 ==========

async def test_凭证过账触发税务计算_从凭证生成到税额计算():
    """
    E2E场景2: 凭证过账触发税务计算全流程

    流程: 补货审批 → 记账Agent生成凭证 → 模拟凭证过账 → 税务Agent计算税额

    验证:
    - VOUCHER_GENERATED 事件发布
    - 记账Agent内部状态（_vouchers）
    - VOUCHER_POSTED 事件触发税务Agent
    - TAX_CALCULATED 事件发布
    - 税务Agent内部状态（_tax_records）
    """
    # 1. 创建记账Agent和税务Agent
    bookkeeping_agent = BookkeepingAgent()
    tax_agent = TaxAgent()

    # 事件收集器
    collector = EventCollector()
    collector.subscribe([
        EventType.VOUCHER_GENERATED,
        EventType.TAX_CALCULATED,
    ], subscriber_id="e2e_collector_s2")

    # 2. 启动Agent
    await _start_agents(bookkeeping_agent, tax_agent)
    await _wait_for_propagation(0.5)

    # 3. 模拟补货审批事件（触发记账Agent生成凭证）
    replenish_approved_event = _make_event(
        event_type=EventType.REPLENISHMENT_APPROVED,
        aggregate_id="proposal-001",
        aggregate_type="replenishment",
        event_data={
            "proposal_id": "proposal-001",
            "product_id": "prod-001",
            "product_name": "原材料A",
            "reorder_quantity": 200,
            "total_cost": 20000.0,
            "unit_cost": 10.0,
        },
    )
    await event_bus.publish(replenish_approved_event)
    await _wait_for_propagation(2.0)

    # 4. 验证凭证已生成
    voucher_events = collector.get_events_by_type(EventType.VOUCHER_GENERATED)
    assert len(voucher_events) >= 1, "记账Agent应发布VOUCHER_GENERATED事件"

    vouchers = bookkeeping_agent.get_vouchers()
    assert len(vouchers) >= 1
    voucher = vouchers[0]
    assert voucher["is_balanced"] is True
    assert voucher["total_debit"] > 0
    assert voucher["total_credit"] > 0
    assert voucher["total_debit"] == voucher["total_credit"]

    # 5. 模拟凭证过账（手动发布VOUCHER_POSTED事件）
    posted_event = _make_event(
        event_type=EventType.VOUCHER_POSTED,
        aggregate_id=voucher["voucher_id"],
        aggregate_type="voucher",
        event_data={
            "voucher_id": voucher["voucher_id"],
            "voucher_type": voucher["voucher_type"],
            "standard": voucher["standard"],
            "entries": voucher["entries"],
            "total_debit": voucher["total_debit"],
            "total_credit": voucher["total_credit"],
            "source_event": "replenishment_approved",
        },
    )
    await event_bus.publish(posted_event)
    await _wait_for_propagation(2.0)

    # 6. 验证税务Agent响应并计算税额
    tax_events = collector.get_events_by_type(EventType.TAX_CALCULATED)
    assert len(tax_events) >= 1, "税务Agent应发布TAX_CALCULATED事件"

    tax_records = tax_agent.get_tax_records()
    assert len(tax_records) >= 1
    record = tax_records[0]
    assert record["tax_amount"] > 0
    assert record["vat_rate"] > 0
    assert record["tax_type"] == "output_vat"

    # 验证税务Agent累计销项税额
    assert tax_agent._output_tax_total > 0

    # 清理
    await _stop_agents(bookkeeping_agent, tax_agent)


# ========== 场景3: 合规检查全流程 ==========

async def test_合规检查_采购订单超阈值未审批触发违规():
    """
    E2E场景3: 合规检查全流程

    流程: 采购订单审批(金额超控制点阈值但无人工审批标记) → 合规Agent检测违规

    验证:
    - 合规Agent检测到违规（_violation_count增加）
    - COMPLIANCE_VIOLATION_FOUND 事件发布
    - 合规Agent内部状态更新
    """
    # 1. 创建合规Agent
    compliance_agent = ComplianceAgent()

    # 事件收集器
    collector = EventCollector()
    collector.subscribe([
        EventType.COMPLIANCE_VIOLATION_FOUND,
        EventType.COMPLIANCE_CHECK_PASSED,
    ], subscriber_id="e2e_collector_s3")

    # 2. 启动Agent
    await compliance_agent.start()
    await _wait_for_propagation(0.5)

    # 3. 发布采购订单审批事件：金额超过控制点阈值(100000)但requires_human_approval=False
    po_approved_event = _make_event(
        event_type=EventType.PURCHASE_ORDER_APPROVED,
        aggregate_id="po-test-001",
        aggregate_type="purchase_order",
        event_data={
            "po_id": "po-test-001",
            "supplier_id": "sup-001",
            "total_amount": 150000.0,    # 超过控制点阈值100000
            "requires_human_approval": False,  # 但未标记需人工审批 → 违规
            "status": "approved",
        },
    )
    await event_bus.publish(po_approved_event)
    await _wait_for_propagation(2.0)

    # 4. 验证合规Agent检测到违规
    assert compliance_agent._violation_count >= 1, "合规Agent应检测到违规"
    assert compliance_agent._check_count >= 1, "合规检查计数应增加"

    # 5. 验证COMPLIANCE_VIOLATION_FOUND事件发布
    violation_events = collector.get_events_by_type(EventType.COMPLIANCE_VIOLATION_FOUND)
    assert len(violation_events) >= 1
    violation_data = violation_events[0].event_data
    assert len(violation_data["violations"]) >= 1
    assert "control_point" in violation_data["violations"][0]

    # 6. 验证违规描述包含金额信息
    violation_desc = violation_data["violations"][0]["description"]
    assert "150000" in violation_desc or "超" in violation_desc

    # 清理
    await compliance_agent.stop()


# ========== 场景4: 多Agent协作 - 采购到支付全流程 ==========

async def test_多Agent协作_采购到支付全流程():
    """
    E2E场景4: 多Agent协作 - 采购到支付全流程

    流程:
    1. 库存不足 → 补货建议 → 采购Agent创建采购订单
    2. 采购订单审批 → 资金Agent排程支付 + 税务Agent计算进项税

    验证:
    - 采购Agent创建采购订单
    - 资金Agent响应PURCHASE_ORDER_APPROVED创建支付排程
    - 税务Agent响应PURCHASE_ORDER_APPROVED计算进项税
    - PAYMENT_SCHEDULED事件发布
    - TAX_CALCULATED事件发布
    """
    # 1. 创建采购Agent、资金Agent、税务Agent
    procurement_agent = ProcurementAgent()
    treasury_agent = TreasuryAgent()
    tax_agent = TaxAgent()

    # 事件收集器
    collector = EventCollector()
    collector.subscribe([
        EventType.PURCHASE_ORDER_CREATED,
        EventType.PAYMENT_SCHEDULED,
        EventType.TAX_CALCULATED,
        EventType.PURCHASE_ORDER_APPROVED,
    ], subscriber_id="e2e_collector_s4")

    # 2. 启动所有Agent
    await _start_agents(procurement_agent, treasury_agent, tax_agent)
    await _wait_for_propagation(0.5)

    # 3. 通过补货建议触发采购Agent创建采购订单
    replenish_proposed_event = _make_event(
        event_type=EventType.REPLENISHMENT_PROPOSED,
        aggregate_id="proposal-e2e-004",
        aggregate_type="replenishment",
        event_data={
            "proposal_id": "proposal-e2e-004",
            "product_id": "prod-001",
            "product_name": "原材料A",
            "reorder_quantity": 500,
            "total_cost": 5000.0,
            "unit_cost": 10.0,
        },
    )
    await event_bus.publish(replenish_proposed_event)
    await _wait_for_propagation(2.0)

    # 4. 验证采购订单已创建
    po_list = procurement_agent.get_purchase_orders()
    assert len(po_list) >= 1, "采购Agent应创建采购订单"
    po = po_list[0]
    po_id = po["po_id"]
    assert po["total_amount"] > 0

    # 5. 审批采购订单（触发PURCHASE_ORDER_APPROVED事件）
    await procurement_agent.approve_purchase_order(po_id, approved=True)
    await _wait_for_propagation(2.0)

    # 6. 验证PURCHASE_ORDER_APPROVED事件发布
    approved_events = collector.get_events_by_type(EventType.PURCHASE_ORDER_APPROVED)
    assert len(approved_events) >= 1, "应发布PURCHASE_ORDER_APPROVED事件"

    # 7. 验证资金Agent响应：创建支付排程
    payments = treasury_agent.get_payment_schedule()
    assert len(payments) >= 1, "资金Agent应创建支付排程"
    payment = payments[0]
    assert payment["po_id"] == po_id
    assert payment["amount"] > 0
    assert payment["status"] == "scheduled"

    # 8. 验证PAYMENT_SCHEDULED事件发布
    payment_events = collector.get_events_by_type(EventType.PAYMENT_SCHEDULED)
    assert len(payment_events) >= 1

    # 9. 验证税务Agent响应：计算进项税
    tax_records = tax_agent.get_tax_records(tax_type="input_vat")
    assert len(tax_records) >= 1, "税务Agent应计算进项税"
    input_tax = tax_records[0]
    assert input_tax["tax_amount"] > 0
    assert input_tax["source_type"] == "purchase_order"

    # 10. 验证TAX_CALCULATED事件发布
    tax_events = collector.get_events_by_type(EventType.TAX_CALCULATED)
    assert len(tax_events) >= 1

    # 清理
    await _stop_agents(procurement_agent, treasury_agent, tax_agent)


# ========== 场景5: 内审警报触发合规审查 ==========

async def test_内审警报触发合规审查_高风险交易流程冻结():
    """
    E2E场景5: 内审警报触发合规审查

    流程: 高风险交易(金额>100万) → 内审Agent发出警报 → 合规Agent深度合规检查 → 流程冻结

    验证:
    - 内审Agent检测到金额异常
    - AUDIT_ALERT_RAISED 事件发布
    - 合规Agent响应警报进行深度合规检查
    - COMPLIANCE_VIOLATION_FOUND 事件发布
    - 内审Agent冻结流程（_frozen_processes）
    """
    # 1. 创建内审Agent和合规Agent
    audit_agent = InternalAuditAgent()
    compliance_agent = ComplianceAgent()

    # 事件收集器
    collector = EventCollector()
    collector.subscribe([
        EventType.AUDIT_ALERT_RAISED,
        EventType.COMPLIANCE_VIOLATION_FOUND,
        EventType.PROCESS_FROZEN,
    ], subscriber_id="e2e_collector_s5")

    # 2. 启动Agent
    await _start_agents(audit_agent, compliance_agent)
    await _wait_for_propagation(0.5)

    # 3. 模拟高风险交易：发布凭证生成事件，金额超过高风险阈值(100万)
    high_risk_event = _make_event(
        event_type=EventType.VOUCHER_GENERATED,
        aggregate_id="voucher-high-risk-001",
        aggregate_type="voucher",
        event_data={
            "voucher_id": "voucher-high-risk-001",
            "voucher_type": "转",
            "total_debit": 1_500_000.0,   # 超过高风险阈值100万
            "total_credit": 1_500_000.0,
            "entries": [
                {"account_code": "1403", "account_name": "原材料",
                 "debit": 1500000, "credit": 0, "summary": "高风险交易"},
                {"account_code": "2202", "account_name": "应付账款",
                 "debit": 0, "credit": 1500000, "summary": "高风险交易"},
            ],
            "standard": "CAS",
            "status": "draft",
        },
    )
    await event_bus.publish(high_risk_event)
    await _wait_for_propagation(3.0)

    # 4. 验证内审Agent检测到异常并发出警报
    alerts = audit_agent.get_alerts()
    assert len(alerts) >= 1, "内审Agent应发出警报"
    alert = alerts[0]
    assert alert["severity"] == "high", "金额超100万应为高风险"
    assert alert["amount"] == 1_500_000.0

    # 5. 验证AUDIT_ALERT_RAISED事件发布
    alert_events = collector.get_events_by_type(EventType.AUDIT_ALERT_RAISED)
    assert len(alert_events) >= 1

    # 6. 验证合规Agent响应警报进行深度合规检查
    assert compliance_agent._violation_count >= 1, "合规Agent应发现违规"

    # 7. 验证COMPLIANCE_VIOLATION_FOUND事件发布
    violation_events = collector.get_events_by_type(EventType.COMPLIANCE_VIOLATION_FOUND)
    assert len(violation_events) >= 1

    # 8. 验证内审Agent冻结流程
    frozen = audit_agent.get_frozen_processes()
    assert len(frozen) >= 1, "高风险警报应冻结流程"

    # 9. 验证PROCESS_FROZEN事件发布
    frozen_events = collector.get_events_by_type(EventType.PROCESS_FROZEN)
    assert len(frozen_events) >= 1

    # 清理
    await _stop_agents(audit_agent, compliance_agent)


# ========== 场景6: 需求预测到生产计划全流程 ==========

async def test_需求预测到生产计划_库存消耗驱动生产():
    """
    E2E场景6: 需求预测到生产计划全流程

    流程:
    1. 多次库存消耗(INVENTORY_ADJUSTED) → 需求Agent积累历史数据
    2. 历史数据足够(>=3条) → 需求Agent生成预测 → DEMAND_FORECAST_GENERATED
    3. 补货审批 → 生产Agent创建生产订单 → PRODUCTION_ORDER_CREATED

    验证:
    - 需求Agent生成预测（_forecasts）
    - DEMAND_FORECAST_GENERATED 事件发布
    - 生产Agent创建生产订单（_production_orders）
    - PRODUCTION_ORDER_CREATED 事件发布
    """
    # 1. 创建需求先知Agent和生产Agent
    demand_agent = DemandForecastAgent()
    production_agent = ProductionAgent()

    # 事件收集器
    collector = EventCollector()
    collector.subscribe([
        EventType.DEMAND_FORECAST_GENERATED,
        EventType.PRODUCTION_ORDER_CREATED,
    ], subscriber_id="e2e_collector_s6")

    # 2. 启动Agent
    await _start_agents(demand_agent, production_agent)
    await _wait_for_propagation(0.5)

    # 3. 模拟多次库存消耗（>=3次触发预测）
    for i in range(4):
        inventory_event = _make_event(
            event_type=EventType.INVENTORY_ADJUSTED,
            aggregate_id=f"prod-001",
            aggregate_type="inventory",
            event_data={
                "product_id": "prod-001",
                "product_name": "原材料A",
                "quantity": 100 - i * 20,   # 递减消耗
                "safety_stock": 50,
                "reorder_point": 100,
                "unit_cost": 10.0,
            },
        )
        await event_bus.publish(inventory_event)
        await _wait_for_propagation(1.0)

    # 4. 验证需求Agent生成预测
    assert len(demand_agent._forecasts) >= 1, "需求Agent应生成至少一条预测"
    forecast = list(demand_agent._forecasts.values())[-1]
    assert forecast["predicted_demand"] >= 0
    assert "confidence" in forecast
    assert "method" in forecast

    # 5. 验证DEMAND_FORECAST_GENERATED事件发布
    forecast_events = collector.get_events_by_type(EventType.DEMAND_FORECAST_GENERATED)
    assert len(forecast_events) >= 1, "应发布DEMAND_FORECAST_GENERATED事件"

    # 6. 模拟补货审批触发生产Agent创建生产订单
    replenish_approved = _make_event(
        event_type=EventType.REPLENISHMENT_APPROVED,
        aggregate_id="proposal-prod-001",
        aggregate_type="replenishment",
        event_data={
            "proposal_id": "proposal-prod-001",
            "product_id": "prod-001",
            "product_name": "原材料A",
            "reorder_quantity": 200,
            "total_cost": 10000.0,
        },
    )
    await event_bus.publish(replenish_approved)
    await _wait_for_propagation(2.0)

    # 7. 验证生产Agent创建生产订单
    assert len(production_agent._production_orders) >= 1, "生产Agent应创建生产订单"
    prod_order = list(production_agent._production_orders.values())[0]
    assert prod_order["product_id"] == "prod-001"
    assert prod_order["quantity"] > 0
    assert prod_order["total_cost"] > 0
    assert prod_order["assigned_line"] is not None

    # 8. 验证PRODUCTION_ORDER_CREATED事件发布
    production_events = collector.get_events_by_type(EventType.PRODUCTION_ORDER_CREATED)
    assert len(production_events) >= 1

    # 清理
    await _stop_agents(demand_agent, production_agent)


# ========== 场景7: 事件总线统计与死信队列 ==========

async def test_事件总线统计与死信队列_失败handler进入死信():
    """
    E2E场景7: 事件总线统计与死信队列

    流程:
    1. 注册一个总是失败的handler
    2. 发布事件
    3. 验证事件总线统计正确（published, delivered, failed）
    4. 验证死信队列记录了失败事件

    验证:
    - published计数正确
    - failed计数正确（重试3次后失败）
    - dead_letter_queue包含失败事件
    """
    # 1. 注册一个总是失败的handler
    async def failing_handler(event: Event):
        raise RuntimeError("模拟handler处理失败")

    failing_sub = EventSubscription(
        subscriber_id="failing-subscriber-test",
        event_types=[EventType.INVENTORY_ADJUSTED],
        handler=failing_handler,
    )
    event_bus.subscribe(failing_sub)

    # 2. 发布事件
    test_event = _make_event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id="test-dead-letter-001",
        aggregate_type="inventory",
        event_data={"product_id": "prod-001", "quantity": 50},
    )
    await event_bus.publish(test_event)

    # 3. 等待事件传播和重试完成（重试3次，每次有退避延迟）
    await _wait_for_propagation(5.0)

    # 4. 验证事件总线统计
    stats = event_bus.get_stats()
    assert stats["published"] >= 1, "published计数应至少为1"

    # failed计数：失败handler重试3次后失败，failed += 1
    assert stats["failed"] >= 1, "failed计数应至少为1"

    # 5. 验证死信队列包含失败事件
    assert len(event_bus._dead_letter_queue) >= 1, "死信队列应包含失败事件"

    dead_event, dead_error = event_bus._dead_letter_queue[0]
    assert dead_event.event_type == EventType.INVENTORY_ADJUSTED
    assert isinstance(dead_error, Exception)
    assert "模拟handler处理失败" in str(dead_error) or "RuntimeError" in str(dead_error)


# ========== 场景8: Agent熔断器机制 ==========

async def test_Agent熔断器机制_连续失败触发熔断与恢复():
    """
    E2E场景8: Agent熔断器机制

    流程:
    1. 创建Agent并启动
    2. 模拟连续失败超过熔断阈值(5次)
    3. 验证熔断器打开
    4. 验证Agent状态变为FUSED
    5. 验证熔断器恢复机制

    验证:
    - 熔断器state从closed变为open
    - Agent状态变为FUSED
    - 恢复后熔断器回到closed
    """
    # 1. 创建Agent
    agent = InventoryGuardianAgent()
    await agent.start()
    await _wait_for_propagation(0.5)

    # 2. 验证初始状态：熔断器关闭，Agent运行中
    assert agent.circuit_breaker.state == "closed"
    assert agent.state == AgentState.RUNNING

    # 3. 模拟连续失败（超过熔断阈值5次）
    for i in range(6):
        agent.circuit_breaker.record_failure()

    # 4. 验证熔断器已打开
    assert agent.circuit_breaker.state == "open", "连续失败超过阈值后熔断器应打开"
    assert agent.circuit_breaker.failure_count >= 5

    # 5. 验证can_execute返回False（熔断状态下不可执行）
    assert agent.circuit_breaker.can_execute() is False, "熔断状态下can_execute应返回False"

    # 6. 等待主循环检测到熔断状态并设置Agent状态为FUSED
    await _wait_for_propagation(2.0)
    assert agent.state == AgentState.FUSED, "Agent状态应变为FUSED"

    # 7. 验证熔断器恢复机制：模拟超时后half_open状态
    # 手动设置last_failure_time为过去（超过reset_timeout=60秒）
    agent.circuit_breaker.last_failure_time = datetime.now(timezone.utc) - timedelta(seconds=61)

    # can_execute应返回True（进入half_open状态）
    assert agent.circuit_breaker.can_execute() is True, "超时后应进入half_open状态允许执行"
    assert agent.circuit_breaker.state == "half_open"

    # 8. 模拟成功：熔断器恢复到closed
    agent.circuit_breaker.record_success()
    assert agent.circuit_breaker.state == "closed", "成功后熔断器应恢复到closed"
    assert agent.circuit_breaker.failure_count == 0

    # 清理
    await agent.stop()
