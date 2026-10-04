# -*- coding: utf-8 -*-
"""
内审Agent测试
验证交易监控、舞弊检测、异常识别与流程冻结
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.audit.agent import InternalAuditAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> InternalAuditAgent:
    """创建并初始化内审Agent（不启动主循环，仅注册身份和订阅）"""
    agent = InternalAuditAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_voucher_event(
    voucher_id: str = "V-001",
    total_debit: float = 5000.0,
) -> Event:
    """创建凭证生成事件"""
    return Event(
        event_type=EventType.VOUCHER_GENERATED,
        aggregate_id=voucher_id,
        aggregate_type="voucher",
        event_data={
            "voucher_id": voucher_id,
            "total_debit": total_debit,
            "total_credit": total_debit,
            "status": "draft",
        },
    )


def _make_purchase_order_event(
    po_id: str = "PO-001",
    total_amount: float = 50000.0,
) -> Event:
    """创建采购订单创建事件"""
    return Event(
        event_type=EventType.PURCHASE_ORDER_CREATED,
        aggregate_id=po_id,
        aggregate_type="purchase_order",
        event_data={
            "po_id": po_id,
            "total_amount": total_amount,
            "supplier_id": "sup-001",
        },
    )


def _make_fund_transfer_event(
    transfer_id: str = "TRF-001",
    amount: float = 300000.0,
) -> Event:
    """创建资金调拨请求事件"""
    return Event(
        event_type=EventType.FUND_TRANSFER_REQUESTED,
        aggregate_id=transfer_id,
        aggregate_type="fund_transfer",
        event_data={
            "transfer_id": transfer_id,
            "amount": amount,
            "from_account": "main",
            "to_account": "sub",
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "内审Agent"
    assert agent.domain == "audit"
    assert agent.security_clearance == "top_secret"
    assert "transaction_monitoring" in agent.capabilities
    assert "fraud_detection" in agent.capabilities
    assert "anomaly_identification" in agent.capabilities
    assert "compliance_audit" in agent.capabilities
    assert "risk_assessment" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_normal_transaction_no_alert ==========

async def test_normal_transaction_no_alert():
    """正常交易不产生警报"""
    agent = await _setup_agent()

    event = _make_voucher_event(voucher_id="V-001", total_debit=5000.0)
    await agent.process_event(event)

    alerts = agent.get_alerts()
    assert len(alerts) == 0


# ========== test_amount_anomaly_alert ==========

async def test_amount_anomaly_alert():
    """金额异常触发警报（超过20万阈值）"""
    agent = await _setup_agent()

    event = _make_fund_transfer_event(transfer_id="TRF-001", amount=300000.0)
    await agent.process_event(event)

    alerts = agent.get_alerts()
    assert len(alerts) >= 1

    amount_alerts = [a for a in alerts if a["alert_type"] == "amount_anomaly"]
    assert len(amount_alerts) >= 1
    assert amount_alerts[0]["severity"] == "medium"
    assert amount_alerts[0]["amount"] == 300000.0


# ========== test_high_risk_freezes_process ==========

async def test_high_risk_freezes_process():
    """高风险交易冻结流程（超过100万阈值）"""
    agent = await _setup_agent()

    event = _make_fund_transfer_event(transfer_id="TRF-001", amount=1200000.0)
    await agent.process_event(event)

    alerts = agent.get_alerts()
    high_alerts = [a for a in alerts if a["severity"] == "high"]
    assert len(high_alerts) >= 1

    # 验证流程被冻结
    frozen = agent.get_frozen_processes()
    assert len(frozen) >= 1
    assert "TRF-001" in frozen


# ========== test_unfreeze_process ==========

async def test_unfreeze_process():
    """解冻流程"""
    agent = await _setup_agent()

    # 先冻结
    event = _make_fund_transfer_event(transfer_id="TRF-001", amount=1200000.0)
    await agent.process_event(event)
    assert "TRF-001" in agent.get_frozen_processes()

    # 解冻
    result = await agent.unfreeze_process("TRF-001")
    assert result is True
    assert "TRF-001" not in agent.get_frozen_processes()


# ========== test_unfreeze_nonexistent ==========

async def test_unfreeze_nonexistent():
    """解冻不存在的流程返回False"""
    agent = await _setup_agent()

    result = await agent.unfreeze_process("nonexistent")
    assert result is False


# ========== test_alerts_filter_by_severity ==========

async def test_alerts_filter_by_severity():
    """警报按严重程度过滤"""
    agent = await _setup_agent()

    # 中风险（30万，超过20万阈值但低于100万）
    event1 = _make_fund_transfer_event(transfer_id="TRF-001", amount=300000.0)
    await agent.process_event(event1)

    # 高风险（120万，超过100万阈值）
    event2 = _make_fund_transfer_event(transfer_id="TRF-002", amount=1200000.0)
    await agent.process_event(event2)

    all_alerts = agent.get_alerts()
    assert len(all_alerts) >= 2

    high_alerts = agent.get_alerts(severity="high")
    assert len(high_alerts) >= 1

    medium_alerts = agent.get_alerts(severity="medium")
    assert len(medium_alerts) >= 1


# ========== test_make_decision_high_risk ==========

async def test_make_decision_high_risk():
    """高风险警报决策需冻结流程"""
    agent = await _setup_agent()

    event = _make_fund_transfer_event(transfer_id="TRF-001", amount=1200000.0)
    await agent.process_event(event)

    alerts = agent.get_alerts(severity="high")
    alert_id = alerts[0]["alert_id"]

    decision = await agent.make_decision({"alert_id": alert_id})
    assert decision.decision_type == "audit_freeze"
    assert decision.requires_human_approval is True
    assert "冻结" in decision.action


# ========== test_make_decision_medium_risk ==========

async def test_make_decision_medium_risk():
    """中风险警报决策持续监控"""
    agent = await _setup_agent()

    event = _make_fund_transfer_event(transfer_id="TRF-001", amount=300000.0)
    await agent.process_event(event)

    alerts = agent.get_alerts(severity="medium")
    assert len(alerts) >= 1
    alert_id = alerts[0]["alert_id"]

    decision = await agent.make_decision({"alert_id": alert_id})
    assert decision.decision_type == "audit_monitor"
    assert decision.requires_human_approval is False


# ========== test_make_decision_nonexistent ==========

async def test_make_decision_nonexistent():
    """对不存在的警报做出决策返回错误"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"alert_id": "nonexistent"})
    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_transaction_patterns ==========

async def test_transaction_patterns():
    """交易模式分析（规则降级）"""
    agent = await _setup_agent()

    # 产生足够多的警报以触发模式识别
    for i in range(6):
        event = _make_fund_transfer_event(
            transfer_id=f"TRF-{i:03d}", amount=300000.0,
        )
        await agent.process_event(event)

    # 触发交易模式分析（on_tick调用）
    await agent._analyze_transaction_patterns()

    patterns = agent.get_transaction_patterns()
    # 警报数>5应触发规则模式识别
    assert len(patterns) >= 1
    assert patterns[0]["method"] == "rules"
    assert "risk_level" in patterns[0]


# ========== test_frequency_anomaly ==========

async def test_frequency_anomaly():
    """频率异常检测"""
    agent = await _setup_agent()

    # 同一来源短时间内产生大量事件
    for i in range(12):
        event = _make_voucher_event(voucher_id="V-001", total_debit=1000.0)
        await agent.process_event(event)

    alerts = agent.get_alerts()
    freq_alerts = [a for a in alerts if a["alert_type"] == "frequency_anomaly"]
    assert len(freq_alerts) >= 1


# ---------------------------------------------------------------------------
# 生产化整改（2026-10-04）：本文件用例验证**规则路径**的确定性输出，
# 显式钉 LLM 关闭——LLM 路径由 test_e2e_flows 场景 1（真 Agnes）与
# 注入/安全套件覆盖。（真 LLM 接入曾使本组用例全部走网络调用而失败）
# ---------------------------------------------------------------------------
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _pin_rule_mode(monkeypatch):
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "llm_enabled", False)
