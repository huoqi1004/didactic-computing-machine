"""
LLM 成本治理冒烟测试（评估报告 B-01 / 路线图任务 1.1、1.6②）
==============================================================

验证：
1. LLM 持续失败（模拟 402 欠费）时，agent 在连续失败达到阈值后进入冷却，
   冷却期内不再发起任何出站请求——出站请求总数被阈值上界封顶；
2. 冷却到期后自动恢复尝试并重置失败计数；
3. treasury on_tick 的 forecast 结果按小时缓存：缓存命中时不重算、不重复发布事件。
"""
import pytest
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.event.models import EventType
from app.core.security.did import did_registry
from app.agents.treasury.agent import TreasuryAgent


class _FailingLLMClient:
    """模拟欠费/故障 LLM 客户端：每次调用计数并抛错（如 402 Payment Required）。"""

    def __init__(self, error_message: str = "402 Payment Required"):
        self.calls = 0
        self.error_message = error_message

    async def chat_completion(self, messages, **kwargs):
        self.calls += 1
        raise RuntimeError(self.error_message)


async def _make_agent() -> TreasuryAgent:
    agent = TreasuryAgent()
    agent.identity = did_registry.register_agent(
        name=agent.name,
        domain=agent.domain,
        capabilities=agent.capabilities,
        security_clearance=agent.security_clearance,
    )
    return agent


async def test_llm_402_outbound_requests_capped_by_cooldown(monkeypatch):
    """402 欠费场景：连续失败 3 次进入冷却，之后 0 出站请求。"""
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(settings, "llm_failure_cooldown_threshold", 3)
    monkeypatch.setattr(settings, "llm_cooldown_seconds", 600)

    agent = await _make_agent()
    client = _FailingLLMClient()
    monkeypatch.setattr(agent, "_get_llm_client", lambda: client)

    results = []
    for _ in range(10):
        results.append(await agent.llm_analyze("分析任务", {"k": "v"}))

    # 出站请求被封顶在阈值（3 次），其余 7 次全部被冷却拦截——
    # 对照 B-01 基线"402 欠费状态 3,600 次失败请求/小时"
    assert client.calls == 3
    # 冷却期内的调用返回明确的冷却标记
    assert all(r.get("reason") == "llm_cooldown" for r in results[3:])
    # 冷却时间已设置
    assert agent._llm_cooldown_until is not None
    assert agent._llm_cooldown_until > datetime.now(timezone.utc)


async def test_llm_cooldown_expires_and_recovers(monkeypatch):
    """冷却到期后恢复尝试，失败计数清零。"""
    monkeypatch.setattr(settings, "llm_enabled", True)

    agent = await _make_agent()
    agent._llm_fail_count = 3
    agent._llm_cooldown_until = datetime.now(timezone.utc) - timedelta(seconds=1)

    client = _FailingLLMClient()
    monkeypatch.setattr(agent, "_get_llm_client", lambda: client)

    # 冷却已过期：本次调用允许出站（失败一次，计数从 0 重新累计）
    result = await agent.llm_analyze("分析任务", {"k": "v"})
    assert client.calls == 1
    assert result.get("error") == "llm_unavailable"
    assert agent._llm_fail_count == 1
    # 未达到阈值，不进入冷却
    assert agent._llm_cooldown_until is None


async def test_llm_success_resets_fail_count(monkeypatch):
    """成功的 LLM 调用重置失败计数（偶发失败不累积冷却）。"""
    monkeypatch.setattr(settings, "llm_enabled", True)

    agent = await _make_agent()
    agent._llm_fail_count = 2

    class _OkClient:
        async def chat_completion(self, messages, **kwargs):
            return '{"answer": "ok"}'

    monkeypatch.setattr(agent, "_get_llm_client", lambda: _OkClient())
    result = await agent.llm_analyze("分析任务", {"k": "v"})
    assert result == {"answer": "ok"}
    assert agent._llm_fail_count == 0


async def test_treasury_on_tick_forecast_cache_suppresses_recompute(monkeypatch):
    """on_tick 二次触发命中缓存：不重算预测、不重复发布事件。"""
    agent = await _make_agent()
    agent._forecast_cache = None

    forecast_calls = 0
    events_published = []

    async def _fake_forecast(days: int = 7):
        nonlocal forecast_calls
        forecast_calls += 1
        return {
            "horizon_days": days,
            "projected_balance": 100000.0,
            "method": "rules_fallback",
            "confidence": 0.6,
        }

    async def _fake_publish(event_type, data):
        events_published.append(event_type)

    monkeypatch.setattr(agent, "get_cash_flow_forecast", _fake_forecast)
    monkeypatch.setattr(agent, "_publish_event", _fake_publish)

    await agent.on_tick()
    await agent.on_tick()  # 1 小时缓存内再次触发
    await agent.on_tick()

    assert forecast_calls == 1, "缓存命中时不应重算 forecast"
    assert events_published.count(EventType.CASH_FLOW_FORECAST_COMPLETED) == 1, (
        "缓存命中时不应重复发布预测完成事件"
    )
