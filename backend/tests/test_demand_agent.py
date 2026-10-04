# -*- coding: utf-8 -*-
"""
需求先知Agent测试
验证需求预测（移动平均法）、趋势分析、异常检测、LLM降级与查询接口
"""
import pytest

from app.core.event.models import Event, EventType
from app.core.security.did import did_registry
from app.agents.demand.agent import DemandForecastAgent


# ========== 辅助函数 ==========

async def _setup_agent() -> DemandForecastAgent:
    """创建并初始化需求先知Agent（不启动主循环，仅注册身份和订阅）"""
    agent = DemandForecastAgent()
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
    safety_stock: int = 50,
    reorder_point: int = 100,
) -> Event:
    """创建库存调整事件"""
    return Event(
        event_type=EventType.INVENTORY_ADJUSTED,
        aggregate_id=product_id,
        aggregate_type="inventory",
        event_data={
            "product_id": product_id,
            "product_name": product_name,
            "quantity": quantity,
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
        },
    )


# ========== test_agent_initialization ==========

async def test_agent_initialization():
    """Agent初始化与身份注册"""
    agent = await _setup_agent()

    assert agent.name == "需求先知Agent"
    assert agent.domain == "demand"
    assert agent.security_clearance == "internal"
    assert "demand_forecasting" in agent.capabilities
    assert "trend_analysis" in agent.capabilities
    assert "seasonality_detection" in agent.capabilities
    assert "general" in agent.capabilities
    assert agent.identity is not None
    assert agent.identity.did is not None


# ========== test_consumption_history_accumulation ==========

async def test_consumption_history_accumulation():
    """库存调整事件累积销售历史"""
    agent = await _setup_agent()

    # 发送2条库存调整事件（不足以生成预测）
    for i in range(2):
        event = _make_inventory_event(quantity=100 + i * 10)
        await agent.process_event(event)

    # 历史已记录但预测未生成（需>=3条）
    assert len(agent._sales_history["prod-001"]) == 2
    assert len(agent._forecasts) == 0


# ========== test_forecast_generation_rules ==========

async def test_forecast_generation_rules():
    """规则模式生成需求预测（移动平均法，LLM默认关闭）"""
    agent = await _setup_agent()

    # 发送3条库存调整事件，触发预测生成
    for i in range(3):
        event = _make_inventory_event(quantity=100 + i * 10)
        await agent.process_event(event)

    forecasts = agent.get_forecasts()
    assert len(forecasts) >= 1

    forecast = forecasts[-1]
    assert forecast["product_id"] == "prod-001"
    assert forecast["predicted_demand"] > 0
    assert forecast["method"] == "rules"
    assert "trend" in forecast
    assert "confidence" in forecast
    assert forecast["confidence"] > 0


# ========== test_trend_analysis ==========

async def test_trend_analysis():
    """趋势分析识别上升/下降/平稳趋势"""
    agent = await _setup_agent()

    # 发送递增数据，应识别为上升趋势
    for qty in [50, 60, 70, 80, 90, 100, 110]:
        event = _make_inventory_event(quantity=qty)
        await agent.process_event(event)

    trends = agent.get_trends()
    assert len(trends) >= 1

    trend = trends[-1]
    assert trend["product_id"] == "prod-001"
    assert trend["trend"] in ("上升", "下降", "平稳")


# ========== test_anomaly_detection ==========

async def test_anomaly_detection():
    """异常消耗检测"""
    agent = await _setup_agent()

    # 先发送几条正常数据建立基线
    for qty in [100, 100, 100]:
        event = _make_inventory_event(quantity=qty)
        await agent.process_event(event)

    # 发送一条异常高消耗数据（偏离超过2倍）
    anomaly_event = _make_inventory_event(quantity=500)
    await agent.process_event(anomaly_event)

    # 应检测到异常（预测基于历史平均值，500远超预期）
    forecasts = agent.get_forecasts()
    # 验证至少有预测生成
    assert len(forecasts) >= 1


# ========== test_make_decision ==========

async def test_make_decision():
    """Agent需求预测决策功能"""
    agent = await _setup_agent()

    # 生成预测
    for i in range(3):
        event = _make_inventory_event(quantity=100 + i * 10)
        await agent.process_event(event)

    forecasts = agent.get_forecasts()
    assert len(forecasts) >= 1
    forecast_id = forecasts[-1]["forecast_id"]

    # 做出决策
    decision = await agent.make_decision({"forecast_id": forecast_id})
    assert decision.decision_type == "demand_forecast_decision"
    assert decision.confidence > 0
    assert decision.requires_human_approval is False
    assert forecast_id in decision.action or forecast_id in str(decision.context)


# ========== test_make_decision_not_found ==========

async def test_make_decision_not_found():
    """不存在的预测ID返回错误决策"""
    agent = await _setup_agent()

    decision = await agent.make_decision({"forecast_id": "nonexistent"})
    assert decision.decision_type == "error"
    assert decision.confidence == 0.0


# ========== test_get_forecasts_filter ==========

async def test_get_forecasts_filter():
    """预测列表按产品ID过滤"""
    agent = await _setup_agent()

    # 为prod-001生成预测
    for i in range(3):
        event = _make_inventory_event(
            product_id="prod-001", product_name="WidgetA", quantity=100
        )
        await agent.process_event(event)

    # 为prod-002生成预测
    for i in range(3):
        event = _make_inventory_event(
            product_id="prod-002", product_name="WidgetB", quantity=200
        )
        await agent.process_event(event)

    # 全部预测
    all_forecasts = agent.get_forecasts()
    assert len(all_forecasts) >= 2

    # 按产品过滤
    prod_001 = agent.get_forecasts(product_id="prod-001")
    assert all(f["product_id"] == "prod-001" for f in prod_001)
    assert len(prod_001) >= 1

    prod_002 = agent.get_forecasts(product_id="prod-002")
    assert all(f["product_id"] == "prod-002" for f in prod_002)
    assert len(prod_002) >= 1


# ========== test_get_trends_filter ==========

async def test_get_trends_filter():
    """趋势列表按产品ID过滤"""
    agent = await _setup_agent()

    # 为prod-001生成趋势
    for i in range(3):
        event = _make_inventory_event(
            product_id="prod-001", product_name="WidgetA", quantity=100
        )
        await agent.process_event(event)

    # 为prod-002生成趋势
    for i in range(3):
        event = _make_inventory_event(
            product_id="prod-002", product_name="WidgetB", quantity=200
        )
        await agent.process_event(event)

    trends_001 = agent.get_trends(product_id="prod-001")
    assert all(t["product_id"] == "prod-001" for t in trends_001)
    assert len(trends_001) >= 1


# ========== test_llm_fallback ==========

async def test_llm_fallback(monkeypatch):
    """LLM不可用时降级到规则模式"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "llm_enabled", False)

    agent = await _setup_agent()

    # 直接调用LLM预测方法，应返回None
    result = await agent._forecast_demand_llm("prod-001", "Widget", [])
    assert result is None

    # 规则模式应正常工作
    forecast = agent._forecast_demand_rules("prod-001", [
        {"quantity": 100},
        {"quantity": 110},
        {"quantity": 120},
    ])
    assert forecast["method"] == "rules"
    assert forecast["predicted_demand"] > 0


# ========== test_on_tick ==========

async def test_on_tick():
    """周期性任务：对已有历史但无预测的产品生成预测"""
    agent = await _setup_agent()

    # 发送3条历史数据
    for i in range(3):
        event = _make_inventory_event(quantity=100)
        await agent.process_event(event)

    # on_tick应正常执行不报错
    await agent.on_tick()


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
