# -*- coding: utf-8 -*-
"""
法务Agent测试
验证合同审查、法律咨询、诉讼管理、法规跟踪、事件处理与LLM降级
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.legal.agent import LegalAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> LegalAgent:
    """创建并初始化法务Agent（注册身份、订阅事件、加载种子法规）"""
    agent = LegalAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    await agent.initialize()
    return agent


def _make_contract_created_event(
    contract_id: str = "C-001",
    contract_text: str = "",
    contract_type: str = "general",
    amount: float = 100000.0,
) -> Event:
    """创建合同创建事件"""
    return Event(
        event_type=EventType.CONTRACT_CREATED,
        aggregate_id=contract_id,
        aggregate_type="contract",
        event_data={
            "contract_id": contract_id,
            "contract_text": contract_text,
            "contract_type": contract_type,
            "amount": amount,
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "法务Agent"
    assert agent.domain == "legal"
    assert agent.security_clearance == "confidential"
    assert "contract_review" in agent.capabilities
    assert "legal_consultation" in agent.capabilities
    assert "litigation_management" in agent.capabilities
    assert "regulation_tracking" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_seed_data ==========

async def test_seed_data():
    """种子数据加载：3条法规跟踪记录"""
    agent = await _setup_agent()

    regulations = agent.get_regulations()
    assert len(regulations) == 3
    assert all(r["status"] == "active" for r in regulations)

    # 验证法规分类
    categories = {r["category"] for r in regulations}
    assert "labor" in categories
    assert "contract" in categories
    assert "corporate" in categories

    # 初始无咨询和诉讼
    assert len(agent.get_consultations()) == 0
    assert len(agent.get_lawsuits()) == 0


# ========== test_contract_created_event_auto_review ==========

async def test_contract_created_event_auto_review():
    """合同创建事件自动触发合同审查"""
    agent = await _setup_agent()

    event = _make_contract_created_event(
        contract_id="C-EVT-001",
        contract_text="这是一份测试合同，内容较少。",
        contract_type="general",
        amount=100000,
    )
    result = await agent.process_event(event)

    assert result is not None
    assert "review_id" in result
    assert "risk_points" in result
    assert result["method"] == "rules"
    assert result["contract_id"] == "C-EVT-001"

    # 应记录审查决策
    review_decisions = [
        d for d in agent._decision_history
        if d.decision_type == "contract_review"
    ]
    assert len(review_decisions) == 1


# ========== test_compliance_violation_event ==========

async def test_compliance_violation_event():
    """合规违规事件触发法律风险评估"""
    agent = await _setup_agent()

    event = Event(
        event_type=EventType.COMPLIANCE_VIOLATION_FOUND,
        aggregate_id="comp-viol-001",
        aggregate_type="compliance",
        event_data={
            "violations": [
                {
                    "description": "数据保护不合规",
                    "regulation_id": "REG-001",
                },
            ],
        },
    )
    result = await agent.process_event(event)

    assert len(result) == 1
    assert result[0]["legal_risk_level"] == "medium"
    assert result[0]["source"] == "compliance_violation"

    # 应记录法律风险评估决策（需人工审批）
    risk_decisions = [
        d for d in agent._decision_history
        if d.decision_type == "legal_risk_assessment"
    ]
    assert len(risk_decisions) == 1
    assert risk_decisions[0].requires_human_approval is True


# ========== test_lawsuit_filed_event ==========

async def test_lawsuit_filed_event():
    """诉讼立案事件自动登记诉讼案件"""
    agent = await _setup_agent()

    event = Event(
        event_type=EventType.LAWSUIT_FILED,
        aggregate_id="case-evt-001",
        aggregate_type="lawsuit",
        event_data={
            "title": "买卖合同纠纷",
            "counterparty": "某供应商",
            "amount": 200000,
            "type": "civil",
            "description": "供应商未按期交货",
        },
    )
    result = await agent.process_event(event)

    assert result is not None
    assert "lawsuit_id" in result
    assert result["status"] == "filed"
    assert result["title"] == "买卖合同纠纷"
    assert result["counterparty"] == "某供应商"
    assert result["amount"] == 200000

    # 应记录诉讼决策
    lawsuit_decisions = [
        d for d in agent._decision_history
        if d.decision_type == "lawsuit_filed"
    ]
    assert len(lawsuit_decisions) == 1


# ========== test_review_contract_rules_missing_breach ==========

async def test_review_contract_rules_missing_breach():
    """规则审查：缺少违约责任条款"""
    agent = await _setup_agent()

    # 包含争议解决、期限、保密条款，但无违约责任
    text = (
        "本合同争议解决由北京仲裁委员会仲裁。"
        "合同期限一年，有效期至2026-12-31。"
        "双方负有保密义务。"
    )
    result = agent._review_contract_rules(text, "general", 100000)

    assert result["method"] == "rules"
    breach_risks = [
        r for r in result["risk_points"] if "违约" in r["description"]
    ]
    assert len(breach_risks) == 1
    assert breach_risks[0]["severity"] == "high"
    assert result["overall_risk_level"] == "high"


# ========== test_review_contract_rules_missing_dispute ==========

async def test_review_contract_rules_missing_dispute():
    """规则审查：缺少争议解决条款"""
    agent = await _setup_agent()

    # 包含违约责任、期限、保密条款，但无争议解决
    text = (
        "本合同包含违约责任条款，违约金为合同金额10%。"
        "合同期限一年，有效期至2026-12-31。"
        "双方负有保密义务。"
    )
    result = agent._review_contract_rules(text, "general", 100000)

    assert result["method"] == "rules"
    dispute_risks = [
        r for r in result["risk_points"] if "争议解决" in r["description"]
    ]
    assert len(dispute_risks) == 1
    assert dispute_risks[0]["severity"] == "medium"


# ========== test_review_contract_rules_large_amount ==========

async def test_review_contract_rules_large_amount():
    """规则审查：大额合同缺少担保条款"""
    agent = await _setup_agent()

    # 大额合同（>500000），包含违约/争议/期限/保密，但无担保
    text = (
        "本合同包含违约责任条款，违约金为合同金额10%。"
        "争议解决由法院管辖。"
        "合同期限一年，有效期至2026-12-31。"
        "双方负有保密义务。"
    )
    result = agent._review_contract_rules(text, "general", 600000)

    assert result["method"] == "rules"
    guarantee_risks = [
        r for r in result["risk_points"] if "担保" in r["description"]
    ]
    assert len(guarantee_risks) == 1
    assert guarantee_risks[0]["severity"] == "high"
    assert result["overall_risk_level"] == "high"


# ========== test_review_contract_rules_passed ==========

async def test_review_contract_rules_passed():
    """规则审查：合同条款齐全无风险"""
    agent = await _setup_agent()

    # 所有条款齐全：违约、争议、期限、保密、担保
    text = (
        "本合同包含违约责任条款，违约金为合同金额10%。"
        "争议解决由北京仲裁委员会仲裁。"
        "合同期限一年，有效期至2026-12-31。"
        "双方负有保密义务。"
        "乙方提供担保。"
    )
    result = agent._review_contract_rules(text, "general", 600000)

    assert result["method"] == "rules"
    assert len(result["risk_points"]) == 0
    assert result["overall_risk_level"] == "low"


# ========== test_create_consultation ==========

async def test_create_consultation():
    """创建法律咨询记录"""
    agent = await _setup_agent()

    consultation = await agent.create_consultation(
        title="劳动合同解除条件咨询",
        question="员工在试用期被辞退需要什么条件？",
        department="HR",
        priority="normal",
    )

    assert "consultation_id" in consultation
    assert consultation["status"] == "pending"
    assert consultation["answer"] == ""
    assert consultation["legal_basis"] == ""
    assert consultation["department"] == "HR"
    assert consultation["priority"] == "normal"

    # 验证已存储
    consultations = agent.get_consultations()
    assert len(consultations) == 1

    # 按状态过滤
    pending = agent.get_consultations(status="pending")
    assert len(pending) == 1


# ========== test_answer_consultation ==========

async def test_answer_consultation():
    """回答法律咨询"""
    agent = await _setup_agent()

    consultation = await agent.create_consultation(
        title="合同效力咨询",
        question="电子合同是否具有法律效力？",
        department="IT",
        priority="normal",
    )

    result = await agent.answer_consultation(
        consultation_id=consultation["consultation_id"],
        answer="电子合同具有法律效力，需满足电子签名法要求。",
        legal_basis="依据《电子签名法》第十四条",
    )

    assert result["status"] == "answered"
    assert result["answer"] == "电子合同具有法律效力，需满足电子签名法要求。"
    assert result["legal_basis"] == "依据《电子签名法》第十四条"
    assert result["answered_at"] is not None

    # 应记录咨询决策
    consultation_decisions = [
        d for d in agent._decision_history
        if d.decision_type == "legal_consultation"
    ]
    assert len(consultation_decisions) == 1
    assert consultation_decisions[0].requires_human_approval is False


# ========== test_create_lawsuit ==========

async def test_create_lawsuit():
    """创建诉讼案件"""
    agent = await _setup_agent()

    lawsuit = await agent.create_lawsuit(
        title="买卖合同纠纷",
        counterparty="某供应商",
        amount=200000,
        type="civil",
        description="供应商未按期交货导致损失",
    )

    assert "lawsuit_id" in lawsuit
    assert lawsuit["status"] == "filed"
    assert lawsuit["title"] == "买卖合同纠纷"
    assert lawsuit["counterparty"] == "某供应商"
    assert lawsuit["amount"] == 200000
    assert lawsuit["type"] == "civil"
    assert len(lawsuit["status_history"]) == 1

    # 金额<=500000，无需人工审批
    decisions = [
        d for d in agent._decision_history
        if d.decision_type == "lawsuit_filed"
    ]
    assert len(decisions) == 1
    assert decisions[0].requires_human_approval is False


# ========== test_update_lawsuit_status ==========

async def test_update_lawsuit_status():
    """更新诉讼案件状态"""
    agent = await _setup_agent()

    lawsuit = await agent.create_lawsuit(
        title="租赁纠纷", counterparty="某租客",
        amount=100000, type="civil", description="租金拖欠",
    )

    result = await agent.update_lawsuit_status(
        lawsuit_id=lawsuit["lawsuit_id"],
        status="in_progress",
        update_note="案件审理中",
    )

    assert result["status"] == "in_progress"
    assert len(result["status_history"]) == 2
    assert result["status_history"][1]["status"] == "in_progress"
    assert result["status_history"][1]["note"] == "案件审理中"

    # 更新不存在的案件
    error_result = await agent.update_lawsuit_status(
        "NONEXISTENT", "closed", "不存在"
    )
    assert "error" in error_result


# ========== test_add_regulation ==========

async def test_add_regulation():
    """添加法规跟踪记录"""
    agent = await _setup_agent()

    initial_count = len(agent.get_regulations())

    regulation = await agent.add_regulation(
        title="中华人民共和国数据安全法",
        category="data",
        effective_date="2021-09-01",
        summary="规范数据处理活动，保障数据安全",
    )

    assert "regulation_id" in regulation
    assert regulation["title"] == "中华人民共和国数据安全法"
    assert regulation["category"] == "data"
    assert regulation["status"] == "active"

    assert len(agent.get_regulations()) == initial_count + 1

    # 按类别过滤
    data_regs = agent.get_regulations(category="data")
    assert len(data_regs) == 1


# ========== test_make_decision_review_high_risk ==========

async def test_make_decision_review_high_risk():
    """决策：高风险合同审查需人工审批"""
    agent = await _setup_agent()

    # 空合同文本 → 缺少所有条款 → 高风险
    decision = await agent.make_decision({
        "action_type": "review",
        "contract_text": "",
        "contract_type": "general",
        "amount": 100000,
    })

    assert decision.decision_type == "contract_review"
    assert decision.requires_human_approval is True
    assert decision.confidence == 0.8


# ========== test_make_decision_review_low_risk ==========

async def test_make_decision_review_low_risk():
    """决策：低风险合同审查通过"""
    agent = await _setup_agent()

    # 合同条款齐全 → 低风险
    text = (
        "本合同包含违约责任条款，违约金为合同金额10%。"
        "争议解决由北京仲裁委员会仲裁。"
        "合同期限一年，有效期至2026-12-31。"
        "双方负有保密义务。"
    )
    decision = await agent.make_decision({
        "action_type": "review",
        "contract_text": text,
        "contract_type": "general",
        "amount": 100000,
    })

    assert decision.decision_type == "contract_review"
    assert decision.requires_human_approval is False
    assert decision.confidence == 0.85


# ========== test_make_decision_litigation_large_amount ==========

async def test_make_decision_litigation_large_amount():
    """决策：大额诉讼需人工审批（>500000）"""
    agent = await _setup_agent()

    decision = await agent.make_decision({
        "action_type": "litigation",
        "title": "重大商业诉讼",
        "amount": 600000,
    })

    assert decision.decision_type == "litigation_management"
    assert decision.requires_human_approval is True
    assert decision.confidence == 0.9


# ========== test_get_stats ==========

async def test_get_stats():
    """法务Agent统计数据"""
    agent = await _setup_agent()

    # 初始状态
    stats = agent.get_stats()
    assert stats["consultations_total"] == 0
    assert stats["consultations_pending"] == 0
    assert stats["consultations_answered"] == 0
    assert stats["lawsuits_total"] == 0
    assert stats["lawsuits_active"] == 0
    assert stats["lawsuits_total_amount"] == 0.0
    assert stats["regulations_tracked"] == 3
    assert stats["reviews_completed"] == 0

    # 创建一些数据后检查
    await agent.create_consultation("咨询1", "问题", "HR")
    await agent.create_lawsuit("案件1", "对方", 300000, "civil", "描述")

    stats2 = agent.get_stats()
    assert stats2["consultations_total"] == 1
    assert stats2["consultations_pending"] == 1
    assert stats2["lawsuits_total"] == 1
    assert stats2["lawsuits_active"] == 1
    assert stats2["lawsuits_total_amount"] == 300000.0


# ========== test_llm_fallback ==========

async def test_llm_fallback(monkeypatch):
    """LLM不可用时降级到规则审查"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "llm_enabled", False)

    agent = await _setup_agent()

    # LLM审查应返回None（不可用）
    llm_result = await agent._review_contract_llm(
        "C-001", "合同文本", "general", 100000
    )
    assert llm_result is None

    # 规则审查应正常工作
    rules_result = agent._review_contract_rules(
        "合同文本", "general", 100000
    )
    assert rules_result["method"] == "rules"
    assert "risk_points" in rules_result
    assert "reasoning" in rules_result

    # review_contract应降级使用规则
    review = await agent.review_contract("C-002", "合同文本", "general", 100000)
    assert review["method"] == "rules"
    assert "review_id" in review


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
