"""
子 Agent 输入层意图识别（整改方案 1.4：LLM 分类优先 / 规则兜底双路）
======================================================================

为各子 Agent 提供统一的输入意图分类：

- **LLM 路径**：单次 JSON 分类调用（复用治理面计量过的 LLM 客户端），
  超时/解析失败自动回落规则路径；
- **规则路径**：关键词表匹配（按意图集可扩展），零成本确定性——CI 与
  LLM 不可用时保证可用（评审 D-A 教训：降级不可伪装、不可缺失）。

标准意图集（可按域扩展）：

- ``creation``  创作/文档类（写大纲/方案/报告/纪要/文档）
- ``query``     查询类（查/看/统计/状态/进展）
- ``action``    业务动作类（提交/审批/转账/创建/修改）
- ``chitchat``  寒暄/能力咨询（无业务动作）

用法::

    from app.core.agent.intent import classify_intent
    result = await classify_intent(message, domain="project_supervisor")
    # {"intent": "creation", "confidence": 0.9, "source": "rules", "entities": {...}}
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import structlog

logger = structlog.get_logger()

STANDARD_INTENTS = ("creation", "query", "action", "chitchat")

# 规则兜底关键词表（顺序即优先级；creation 最先——创作词最特异）
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("creation", re.compile(
        r"(写|生成|编制|起草|输出|拟定).{0,24}(大纲|方案|报告|纪要|文档|计划书|总结)"
        r"|(监理大纲|实施方案|周报|月报|会议纪要)|(word|docx|excel|ppt)文档",
        re.IGNORECASE)),
    ("query", re.compile(
        r"(查|统计|多少|状态|进展|进度|现状|列表|明细|余额|报表|看一?下)")),
    ("action", re.compile(
        r"(提交|审批|转账|调拨|创建|修改|删除|下单|确认|支付|取消)")),
    ("chitchat", re.compile(
        r"(你好|您好|你是谁|能做什么|帮忙做什么|谢谢|再见)")),
]

# LLM 分类提示词（JSON 契约）
_CLASSIFY_PROMPT = (
    "你是企业 ERP 的意图分类器。将用户消息分类为以下意图之一：\n"
    "creation（创作/文档类）、query（查询类）、action（业务动作类）、"
    "chitchat（寒暄/能力咨询）。\n"
    '只返回 JSON：{"intent": "...", "confidence": 0.0~1.0, '
    '"entities": {"project_id": "...", "amount": 0}}\n'
    "用户消息：{message}"
)


async def classify_intent(
    message: str, domain: str = "", llm_client: Any = None,
) -> dict:
    """分类用户消息意图。

    LLM 可用且启用时走 LLM 分类（source="llm"）；任何失败静默回落
    规则路径（source="rules"）——分类器自身永不阻断业务链路。
    """
    if not message or not message.strip():
        return {"intent": "chitchat", "confidence": 0.3,
                "source": "rules", "entities": {}}

    if llm_client is not None:
        try:
            raw = await llm_client.chat(
                _CLASSIFY_PROMPT.replace('{message}', message[:2000]))
            parsed = json.loads(raw)
            intent = str(parsed.get("intent", ""))
            if intent in STANDARD_INTENTS:
                return {"intent": intent,
                        "confidence": float(parsed.get("confidence", 0.8)),
                        "source": "llm",
                        "entities": parsed.get("entities") or {}}
        except Exception as e:  # noqa: BLE001
            logger.debug("intent.llm_classify_failed", error=str(e))

    return _rule_classify(message)


def _rule_classify(message: str) -> dict:
    lower = message.lower()
    for intent, pattern in _RULES:
        if pattern.search(lower):
            return {"intent": intent, "confidence": 0.7,
                    "source": "rules", "entities": _extract_entities(message)}
    return {"intent": "chitchat", "confidence": 0.3,
            "source": "rules", "entities": _extract_entities(message)}


def _extract_entities(message: str) -> dict:
    """轻量实体抽取：单据编号/项目号/金额（启发式，供工具调用预填）。"""
    entities: dict[str, Any] = {}
    m = re.search(r"\b(proj|PO|RE|CT|PAY)-?[0-9A-Za-z-]{2,20}", message,
                  re.IGNORECASE)
    if m:
        entities["ref_id"] = m.group(0)
    m = re.search(r"([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(元|万|块)", message)
    if m:
        amount = float(m.group(1).replace(",", "").replace("，", ""))
        if "万" in m.group(2):
            amount *= 10000
        entities["amount"] = amount
    return entities
