# -*- coding: utf-8 -*-
"""
合规Agent测试
验证法规解析、合规检查、控制点管理与合规报告
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.compliance.agent import ComplianceAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> ComplianceAgent:
    """创建并初始化合规Agent（不启动主循环，仅注册身份和订阅）"""
    agent = ComplianceAgent()
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


def _make_po_event(
    po_id: str = "PO-001",
    total_amount: float = 50000.0,
    requires_human_approval: bool = False,
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
            "requires_human_approval": requires_human_approval,
        },
    )


def _make_audit_alert_event(
    alert_id: str = "ALT-001",
    severity: str = "high",
) -> Event:
    """创建内审警报事件"""
    return Event(
        event_type=EventType.AUDIT_ALERT_RAISED,
        aggregate_id=alert_id,
        aggregate_type="audit",
        event_data={
            "alert_id": alert_id,
            "alert_type": "fraud_suspicion",
            "severity": severity,
            "amount": 1200000.0,
            "process_id": "TRF-001",
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "合规Agent"
    assert agent.domain == "compliance"
    assert agent.security_clearance == "confidential"
    assert "regulation_parsing" in agent.capabilities
    assert "compliance_checking" in agent.capabilities
    assert "control_point_management" in agent.capabilities
    assert "policy_update" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_regulations ==========

async def test_seed_regulations():
    """种子法规数据正确加载"""
    agent = await _setup_agent()

    regulations = agent.get_regulations()
    assert len(regulations) >= 3

    reg_ids = [r["reg_id"] for r in regulations]
    assert "reg-vat-001" in reg_ids
    assert "reg-acc-001" in reg_ids
    assert "reg-audit-001" in reg_ids

    # 验证法规数据结构
    for reg in regulations:
        assert "title" in reg
        assert "category" in reg
        assert "requirements" in reg
        assert reg["status"] == "active"


# ========== test_seed_control_points ==========

async def test_seed_control_points():
    """种子控制点数据正确加载"""
    agent = await _setup_agent()

    control_points = agent.get_control_points()
    assert len(control_points) >= 3

    cp_ids = [cp["cp_id"] for cp in control_points]
    assert "cp-001" in cp_ids
    assert "cp-002" in cp_ids
    assert "cp-003" in cp_ids

    # 验证控制点数据结构
    for cp in control_points:
        assert "name" in cp
        assert "process" in cp
        assert "control_type" in cp
        assert cp["status"] == "active"


# ========== test_compliance_check_pass ==========

async def test_compliance_check_pass():
    """正常交易合规检查通过"""
    agent = await _setup_agent()

    event = _make_voucher_event(voucher_id="V-001", total_debit=5000.0)
    result = await agent.process_event(event)

    assert result["passed"] is True
    assert len(result["violations"]) == 0


# ========== test_compliance_check_violation ==========

async def test_compliance_check_violation():
    """大额采购未触发人工审批产生违规"""
    agent = await _setup_agent()

    # 金额超过cp-001阈值(100000)但未标记requires_human_approval
    event = _make_po_event(
        po_id="PO-001", total_amount=150000.0,
        requires_human_approval=False,
    )
    result = await agent.process_event(event)

    assert result["passed"] is False
    assert len(result["violations"]) >= 1
    assert "control_point" in result["violations"][0]


# ========== test_compliance_check_with_approval ==========

async def test_compliance_check_with_approval():
    """大额采购已触发人工审批则合规通过"""
    agent = await _setup_agent()

    event = _make_po_event(
        po_id="PO-001", total_amount=150000.0,
        requires_human_approval=True,
    )
    result = await agent.process_event(event)

    assert result["passed"] is True
    assert len(result["violations"]) == 0


# ========== test_audit_alert_triggers_compliance_check ==========

async def test_audit_alert_triggers_compliance_check():
    """内审警报触发合规检查"""
    agent = await _setup_agent()

    event = _make_audit_alert_event(alert_id="ALT-001", severity="high")
    violations = await agent.process_event(event)

    # 高风险警报应产生合规违规
    assert len(violations) >= 1
    assert violations[0]["regulation_id"] == "reg-audit-001"


# ========== test_check_compliance_sync ==========

async def test_check_compliance_sync():
    """同步合规检查方法"""
    agent = await _setup_agent()

    # 合规：小额采购无需审批
    result = agent.check_compliance({
        "process": "procurement",
        "amount": 50000.0,
        "requires_human_approval": False,
    })
    assert result["passed"] is True

    # 违规：大额采购未审批
    result = agent.check_compliance({
        "process": "procurement",
        "amount": 150000.0,
        "requires_human_approval": False,
    })
    assert result["passed"] is False
    assert len(result["violations"]) >= 1


# ========== test_regulations_filter ==========

async def test_regulations_filter():
    """法规按类别过滤"""
    agent = await _setup_agent()

    all_regs = agent.get_regulations()
    assert len(all_regs) >= 3

    tax_regs = agent.get_regulations(category="tax")
    assert len(tax_regs) >= 1
    assert all(r["category"] == "tax" for r in tax_regs)

    accounting_regs = agent.get_regulations(category="accounting")
    assert len(accounting_regs) >= 1


# ========== test_control_points_filter ==========

async def test_control_points_filter():
    """控制点按流程过滤"""
    agent = await _setup_agent()

    all_cps = agent.get_control_points()
    assert len(all_cps) >= 3

    procurement_cps = agent.get_control_points(process="procurement")
    assert len(procurement_cps) >= 1
    assert all(cp["process"] == "procurement" for cp in procurement_cps)


# ========== test_make_decision ==========

async def test_make_decision():
    """Agent合规决策功能"""
    agent = await _setup_agent()

    # 合规通过
    decision = await agent.make_decision({
        "process": "procurement",
        "amount": 50000.0,
        "requires_human_approval": False,
    })
    assert decision.decision_type == "compliance_pass"
    assert decision.requires_human_approval is False

    # 合规违规
    decision = await agent.make_decision({
        "process": "procurement",
        "amount": 150000.0,
        "requires_human_approval": False,
    })
    assert decision.decision_type == "compliance_violation"
    assert decision.requires_human_approval is True


# ========== test_generate_compliance_report ==========

async def test_generate_compliance_report():
    """生成合规报告"""
    agent = await _setup_agent()

    # 产生一些合规检查
    await agent.process_event(_make_voucher_event(
        voucher_id="V-001", total_debit=5000.0))
    await agent.process_event(_make_po_event(
        po_id="PO-001", total_amount=50000.0))

    report = await agent.generate_compliance_report()

    assert report["total_checks"] >= 2
    assert "violations" in report
    assert "pass_rate" in report
    assert "regulations_count" in report
    assert "control_points_count" in report
    assert report["status"] == "generated"

    # 验证报告记录
    reports = agent.get_compliance_reports()
    assert len(reports) == 1


# ========== test_parse_regulation ==========

async def test_parse_regulation():
    """法规解析（规则降级模式）"""
    agent = await _setup_agent()

    doc_text = "本条例规定所有企业应当按时申报增值税。"
    regulation = await agent.parse_regulation(doc_text)

    assert regulation is not None
    assert regulation["reg_id"] != ""
    assert regulation["status"] == "active"
    assert regulation["method"] == "rules"

    # 验证法规已入库
    regulations = agent.get_regulations()
    assert any(r["reg_id"] == regulation["reg_id"] for r in regulations)
