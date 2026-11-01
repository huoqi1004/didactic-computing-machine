"""
意图识别输入层测试（整改方案 1.4 / D-E）
==========================================
规则路径全离线确定性；LLM 路径用假客户端验证回落与解析。
"""
import pytest

from app.core.agent.intent import _extract_entities, classify_intent


@pytest.mark.asyncio
@pytest.mark.parametrize("message,expected", [
    ("帮我写一份proj-003的监理大纲", "creation"),
    ("生成一份月度分析报告", "creation"),
    ("起草实施方案并以word文档输出", "creation"),
    ("输出会议纪要", "creation"),
    ("查询最近的报销单", "query"),
    ("现在库存状态怎么样", "query"),
    ("统计本月销售额", "query"),
    ("提交一笔5000元的调拨申请", "action"),
    ("审批这个采购订单", "action"),
    ("你好，你能做什么", "chitchat"),
])
async def test_rule_classification_coverage(message, expected):
    result = await classify_intent(message, llm_client=None)
    assert result["intent"] == expected, (message, result)
    assert result["source"] == "rules"


async def test_llm_path_parses_and_falls_back(monkeypatch):
    class _GoodLLM:
        async def chat(self, prompt):
            return '{"intent": "creation", "confidence": 0.95, "entities": {"ref_id": "proj-003"}}'

    r = await classify_intent("写个大纲", llm_client=_GoodLLM())
    assert r["intent"] == "creation" and r["source"] == "llm" and r["confidence"] == 0.95

    class _BadLLM:
        async def chat(self, prompt):
            return "不是JSON"

    r2 = await classify_intent("写个大纲", llm_client=_BadLLM())
    assert r2["source"] == "rules" and r2["intent"] == "creation"  # 解析失败回落规则

    class _ErrLLM:
        async def chat(self, prompt):
            raise RuntimeError("超时")

    r3 = await classify_intent("查一下库存", llm_client=_ErrLLM())
    assert r3["intent"] == "query" and r3["source"] == "rules"  # LLM 异常不阻断


def test_entity_extraction():
    e = _extract_entities("proj-003 的监理大纲，预算 80 万")
    assert e.get("ref_id", "").lower().startswith("proj")
    assert e.get("amount") == 800000.0
