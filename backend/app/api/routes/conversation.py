"""
对话式操作路由
================

提供 Agent 对话式交互能力，接入 DeepSeek LLM 生成差异化回复。

端点：
- GET  /conversation/agents        获取可对话的 Agent 列表
- POST /conversation/chat          发送消息并获取 Agent 回复（一次性返回）
- POST /conversation/chat/stream   同上，SSE 流式返回（工作包三 / 裁决 D-D）

回复结构：
    {
        "content": "Agent 回复文本",
        "thinkingProcess": ["步骤1", "步骤2", ...],
        "dataCard": { ... }  # 可选，结构化数据卡片
    }

设计要点：
1. 每个 Agent 拥有专属系统提示词，注入角色、能力域与实时业务上下文
2. LLM 返回 JSON，包含 content / thinkingProcess / dataCard 三字段
3. LLM 不可用时降级到规则骨架，回复带 ``degraded: true``（可观测性：不伪装成正式回答）
4. 降级骨架按意图分组（裁决 D-A）：查询类返回 Agent 本体实时数据（经治理面调用只读
   工具，不再是对话路由里另写的一份文案）；创作类转人工队列并生成管理员待办
5. 创作类意图（大纲/实施方案/文档）→ LLM 结构化内容 → docx 真文件 + 下载链接，
   页眉标注「AI 生成草稿」，生成行为全量审计（裁决 D-C）
6. 流式端点与非流式端点共用同一批装配函数（意图分类、上下文采集、降级分组、创作产出），
   只在"返回形态"上分叉；输出护栏按滑动窗口 + 聚合全文复核，命中即推 ``retract``
   终止帧并留痕（裁决 D-D）
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime
from typing import Any, AsyncIterator, Optional

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import httpx

from app.api.middleware.auth import get_current_user
from app.core.agent.orchestrator import orchestrator
from app.core.config import settings
from app.core.llm.client import LLMClient, NO_THINK_EXTRA, get_llm_client
from app.core.llm.exceptions import LLMUnavailableError

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/conversation", tags=["对话式操作"])

# ========== LLM 客户端（使用全局共享单例） ==========
# 不再创建独立单例，改用 get_llm_client() 共享单例
# 避免 httpx 异步客户端在多上下文中的状态不一致问题


def _get_llm_client() -> Optional[LLMClient]:
    """获取全局共享 LLM 客户端单例"""
    try:
        return get_llm_client()
    except Exception as e:
        logger.error("conversation.llm_client_init_failed", error=str(e))
        return None


# ========== Agent 配置 ==========

# 对话 Agent 元数据（与前端 MOCK_CHAT_AGENTS 保持一致）
CHAT_AGENTS = [
    {
        "id": "inventory-agent",
        "name": "库存管家",
        "type": "inventory",
        "status": "online",
        "icon": "Package",
        "description": "负责库存监控、自动补货决策与出入库核对，可查询实时库存与预警",
        "capabilities": ["库存查询", "补货建议", "盘点核对", "预警通知"],
        "quickCommands": ["查询库存状态", "查看补货建议", "检查低库存预警", "导出库存报表"],
    },
    {
        "id": "accounting-agent",
        "name": "会计专员",
        "type": "accounting",
        "status": "online",
        "icon": "Calculator",
        "description": "自动生成会计凭证、科目核对与税务计算，支持凭证审批与日结",
        "capabilities": ["凭证生成", "凭证审批", "科目核对", "日清日结"],
        "quickCommands": ["查询待审批凭证", "生成日报", "执行日清日结", "科目余额核对"],
    },
    {
        "id": "procurement-agent",
        "name": "采购助手",
        "type": "procurement",
        "status": "online",
        "icon": "ShoppingCart",
        "description": "供应商比价、采购订单生成与到货跟踪，可发起采购审批",
        "capabilities": ["供应商比价", "采购下单", "到货跟踪", "合同审核"],
        "quickCommands": ["查询采购订单", "供应商比价", "发起采购审批", "到货状态跟踪"],
    },
    {
        "id": "risk-agent",
        "name": "风控哨兵",
        "type": "risk",
        "status": "busy",
        "icon": "ShieldAlert",
        "description": "实时风控监测、异常交易识别与熔断决策，检查合规风险",
        "capabilities": ["实时风控", "异常识别", "熔断决策", "合规检查"],
        "quickCommands": ["检查合规风险", "查看异常交易", "风控状态总览", "熔断记录查询"],
    },
    {
        "id": "settlement-agent",
        "name": "结算管家",
        "type": "settlement",
        "status": "offline",
        "icon": "Wallet",
        "description": "日清日结、应收应付对账与资金调度，当前处于离线状态",
        "capabilities": ["日清日结", "对账核销", "资金调度", "流水核对"],
        "quickCommands": ["对账状态查询", "资金流水核对", "应收应付统计"],
    },
    {
        "id": "project-supervisor-agent",
        "name": "项目监理agent",
        "type": "project",
        "status": "online",
        "icon": "ClipboardCheck",
        "description": "监理和审计公司项目从立项到上线的全流程合规，可查询项目进展、合规状态与监理报告",
        "capabilities": ["立项审批", "进度监理", "上线审计", "合规检查", "监理报告"],
        "quickCommands": ["查询项目进展", "检查项目合规", "上线审计", "生成监理报告"],
    },
]

# Agent ID → 系统提示词映射
AGENT_SYSTEM_PROMPTS = {
    "inventory-agent": (
        "你是「库存管家」，一个运行在企业ERP系统中的智能Agent，负责库存监控、需求预测与补货决策。\n"
        "能力域：库存查询、补货建议、盘点核对、预警通知、需求预测。\n\n"
        "回复规则：\n"
        "1. 根据用户的具体问题给出有针对性的回复，不要对所有问题返回相同的答案\n"
        "2. 如果用户问的是「查询/查看库存」，返回当前库存状态概览\n"
        "3. 如果用户问的是「预测/预估」，返回基于趋势分析的预测结果和理由\n"
        "4. 如果用户问的是「补货建议」，返回补货清单和优先级\n"
        "5. 回复要简洁专业，包含具体数据\n\n"
        "你必须返回合法的JSON，格式如下：\n"
        "{\n"
        '  "content": "给用户的自然语言回复（50-200字）",\n'
        '  "thinkingProcess": ["推理步骤1", "推理步骤2", "推理步骤3", "推理步骤4"],\n'
        '  "dataCard": {\n'
        '    "type": "stats 或 table 或 list",\n'
        '    "title": "卡片标题",\n'
        '    "stats": [{"label": "标签", "value": "值", "tone": "accent|primary|paused|circuit"}],\n'
        '    "columns": [{"key": "字段名", "label": "列标题"}],\n'
        '    "rows": [{"字段名": "值"}]\n'
        "  }\n"
        "}\n"
        "注意：dataCard 是可选的，如果没有结构化数据可以不返回。"
        "不要包含markdown代码块标记。"
    ),
    "accounting-agent": (
        "你是「会计专员」，一个运行在企业ERP系统中的智能Agent，负责会计凭证、税务计算与日清日结。\n"
        "能力域：凭证生成、凭证审批、科目核对、日清日结、税务计算。\n\n"
        "回复规则：\n"
        "1. 根据用户具体问题给出有针对性的回复\n"
        "2. 涉及凭证时返回凭证清单表格\n"
        "3. 涉及日报/日结时返回财务统计卡片\n"
        "4. 回复要包含具体金额和数据\n\n"
        "你必须返回合法的JSON，格式如下：\n"
        "{\n"
        '  "content": "给用户的自然语言回复（50-200字）",\n'
        '  "thinkingProcess": ["推理步骤1", "推理步骤2", ...],\n'
        '  "dataCard": { "type": "stats|table|list", "title": "...", ... }\n'
        "}\n"
        "不要包含markdown代码块标记。"
    ),
    "procurement-agent": (
        "你是「采购助手」，一个运行在企业ERP系统中的智能Agent，负责供应商管理、采购订单与到货跟踪。\n"
        "能力域：供应商比价、采购下单、到货跟踪、合同审核。\n\n"
        "回复规则：\n"
        "1. 根据用户具体问题给出有针对性的回复\n"
        "2. 涉及比价时返回供应商对比表格\n"
        "3. 涉及订单时返回订单状态列表\n\n"
        "你必须返回合法的JSON，格式如下：\n"
        "{\n"
        '  "content": "给用户的自然语言回复（50-200字）",\n'
        '  "thinkingProcess": ["推理步骤1", "推理步骤2", ...],\n'
        '  "dataCard": { "type": "stats|table|list", "title": "...", ... }\n'
        "}\n"
        "不要包含markdown代码块标记。"
    ),
    "risk-agent": (
        "你是「风控哨兵」，一个运行在企业ERP系统中的智能Agent，负责实时风控监测与合规检查。\n"
        "能力域：实时风控、异常识别、熔断决策、合规检查。\n\n"
        "回复规则：\n"
        "1. 根据用户具体问题给出有针对性的回复\n"
        "2. 涉及合规检查时返回审计结果统计\n"
        "3. 涉及异常交易时返回异常明细表\n\n"
        "你必须返回合法的JSON，格式如下：\n"
        "{\n"
        '  "content": "给用户的自然语言回复（50-200字）",\n'
        '  "thinkingProcess": ["推理步骤1", "推理步骤2", ...],\n'
        '  "dataCard": { "type": "stats|table|list", "title": "...", ... }\n'
        "}\n"
        "不要包含markdown代码块标记。"
    ),
    "settlement-agent": (
        "你是「结算管家」，一个运行在企业ERP系统中的智能Agent，负责日清日结与资金调度。\n"
        "能力域：日清日结、对账核销、资金调度、流水核对。\n\n"
        "回复规则：\n"
        "1. 根据用户具体问题给出有针对性的回复\n"
        "2. 涉及对账时返回对账状态表\n"
        "3. 涉及资金时返回资金流水统计\n\n"
        "你必须返回合法的JSON，格式如下：\n"
        "{\n"
        '  "content": "给用户的自然语言回复（50-200字）",\n'
        '  "thinkingProcess": ["推理步骤1", "推理步骤2", ...],\n'
        '  "dataCard": { "type": "stats|table|list", "title": "...", ... }\n'
        "}\n"
        "不要包含markdown代码块标记。"
    ),
    "project-supervisor-agent": (
        "你是「项目监理agent」，一个运行在企业ERP系统中的智能监理Agent，"
        "负责监理和审计公司项目从立项到上线的全流程合规。\n"
        "能力域：立项审批监理、进度监理、UAT与验收监理、上线发布审计、合规检查、监理报告。\n\n"
        "回复规则：\n"
        "1. 根据用户具体问题给出有针对性的回复，结论先行\n"
        "2. 用户问「进展/进度/现状」时，返回项目进展与里程碑完成情况\n"
        "3. 用户问「合规/检查/风险」时，返回各阶段合规核对结果与风险分级\n"
        "4. 用户问「上线/审计/发布」时，返回上线就绪度审计结论\n"
        "5. 用户问「监理报告」时，生成结构化监理报告\n"
        "6. 高风险问题必须提示需人工介入\n\n"
        "你必须返回合法的JSON，格式如下：\n"
        "{\n"
        '  "content": "给用户的自然语言回复（50-200字）",\n'
        '  "thinkingProcess": ["推理步骤1", "推理步骤2", ...],\n'
        '  "dataCard": { "type": "stats|table|list", "title": "...", ... }\n'
        "}\n"
        "不要包含markdown代码块标记。"
    ),
}


def _normalize_data_card(card: Any) -> Optional[dict]:
    """
    规范化 LLM 生成的 dataCard（渲染契约防线）。

    LLM 输出的 stats/columns/rows/items 形状不可控（可能是对象/字符串），
    前端按数组渲染会白屏。此处统一校验：type 白名单、数组字段强制
    list、非法字段丢弃，任何异常返回 None（前端不渲染卡片）。
    """
    if not isinstance(card, dict):
        return None

    card_type = card.get("type")
    if card_type not in ("stats", "table", "list"):
        return None

    def _as_list(value) -> list:
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return list(value.values())
        return []

    normalized: dict = {
        "type": card_type,
        "title": str(card.get("title", ""))[:120],
    }
    if card_type == "stats":
        stats = [s for s in _as_list(card.get("stats")) if isinstance(s, dict)]
        if not stats:
            return None
        normalized["stats"] = stats[:12]
    elif card_type == "table":
        columns = [c for c in _as_list(card.get("columns")) if isinstance(c, dict)]
        rows = [r for r in _as_list(card.get("rows")) if isinstance(r, (list, dict))]
        if not columns or not rows:
            return None
        normalized["columns"] = columns[:20]
        normalized["rows"] = rows[:100]
    else:  # list
        items = [i for i in _as_list(card.get("items")) if isinstance(i, dict)]
        if not items:
            return None
        normalized["items"] = items[:50]
    return normalized


# ========== 意图分类（工作包一 1.4：规则确定性 + LLM 仲裁双路） ==========

INTENT_CREATIVE = "creative_document"
INTENT_LAUNCH_AUDIT = "query_launch_audit"
INTENT_COMPLIANCE = "query_compliance"
INTENT_REPORT = "query_report"
INTENT_PROGRESS = "query_progress"
INTENT_GUIDE = "capability_guide"

# 关键词表——**收窄口径**：泛指词（"项目"这类）不入表，它是"多个问题同一答案"的根因；
# 顺序即优先级（创作类含文档名词，最具体，优先命中）
_INTENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (INTENT_CREATIVE, ("大纲", "实施方案", "计划书", "文档", "docx", "word",
                       "起草", "拟一份", "写一份", "出一份", "编制")),
    (INTENT_LAUNCH_AUDIT, ("上线", "发布", "验收", "就绪")),
    (INTENT_COMPLIANCE, ("合规", "违规", "检查")),
    (INTENT_REPORT, ("监理报告", "报告")),
    (INTENT_PROGRESS, ("进展", "进度", "里程碑", "现状", "状态")),
]

_PROJECT_ID_RE = re.compile(r"proj-\d+", re.IGNORECASE)
_LLM_INTENTS = {intent for intent, _ in _INTENT_KEYWORDS}

# 分类调用只在规则落空时发生，且串行卡在首帧之前——真实网关该调用实测 0.79~5.63s，
# 不设上限就会独自击穿验收门槛"首 token < 2s"。超时即回落规则结果（本就是 GUIDE，无损）。
_INTENT_CLASSIFY_TIMEOUT = 1.5

# 意图 → 回答侧重（注入 LLM 提示词）。不同问题要给不同答案，
# 光靠 temperature 不够——必须把"这次问的是什么"显式交给模型。
_INTENT_ANSWER_HINT = {
    INTENT_LAUNCH_AUDIT: "本次是上线就绪度审计：请逐项目给出「是否具备上线条件」及前置条件缺口（UAT、变更审批、回滚预案）。",
    INTENT_COMPLIANCE: "本次是合规检查：请按监理阶段清单给出合规/违规判定，并分级列出风险项。",
    INTENT_REPORT: "本次是监理报告：请给出报告结论、里程碑完成率、预算使用率与风险清单。",
    INTENT_PROGRESS: "本次是进展查询：请给出各项目当前阶段、里程碑完成情况与进度偏差。",
    INTENT_GUIDE: "本次未命中具体查询意图：请简要说明能力范围，并引导用户给出项目编号或具体诉求。",
}


def _classify_intent_by_rules(message: str) -> str:
    """规则意图分类（确定性、零成本、可离线评测）。"""
    lowered = message.lower()
    for intent, keywords in _INTENT_KEYWORDS:
        if any(k in message or k in lowered for k in keywords):
            return intent
    return INTENT_GUIDE


async def _classify_intent_by_llm(message: str) -> Optional[str]:
    """LLM 意图分类（规则未命中时的仲裁；失败或超时返回 None 回落规则结果）。"""
    client = _get_llm_client()
    if client is None:
        return None
    try:
        response = await asyncio.wait_for(
            client.chat_completion(
                [
                    {"role": "system", "content":
                        "你是意图分类器。只输出 JSON，不要解释。\n"
                        f"可选标签：{sorted(_LLM_INTENTS)}。\n"
                        "creative_document=要求产出文档/大纲/方案文件；"
                        "query_*=询问进展/合规/上线/报告；capability_guide=寒暄或能力询问。"},
                    {"role": "user", "content": message},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=64,
                extra=NO_THINK_EXTRA,
                scene="intent_classify",
            ),
            timeout=_INTENT_CLASSIFY_TIMEOUT,
        )
        if not response:
            return None
        start, end = response.find("{"), response.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(response[start:end])
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        # 认值不认键：同一条 prompt 实测既出 {"intent": ...} 也出 {"label": ...}
        for value in parsed.values():
            label = str(value).strip()
            if label in _LLM_INTENTS:
                return label
        return None
    except asyncio.TimeoutError:
        logger.warning("conversation.intent_classify_timeout",
                       timeout_ms=round(_INTENT_CLASSIFY_TIMEOUT * 1000))
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("conversation.intent_classify_failed", error=str(e))
        return None


async def classify_intent(message: str) -> dict:
    """
    双路意图分类。

    规则命中即用（确定性优先，省一次 LLM 往返——创作类还要再生成整篇文档，
    每问都多花一次分类调用的 token 不值得，见裁决 D-E 成本口径）；
    规则落空且 LLM 可用时由 LLM 仲裁。
    """
    intent = _classify_intent_by_rules(message)
    classifier = "rules"
    if intent == INTENT_GUIDE and settings.llm_enabled:
        llm_intent = await _classify_intent_by_llm(message)
        if llm_intent:
            intent, classifier = llm_intent, "llm"
    matched = _PROJECT_ID_RE.search(message)
    return {
        "intent": intent,
        "classifier": classifier,
        "project_id": matched.group(0).lower() if matched else None,
    }


# ========== 治理面只读工具调用（对话链路的实时数据唯一入口） ==========

async def _call_governance_tool(tool_name: str, args: dict, agent_key: str,
                                current_user: Optional[dict]) -> Any:
    """
    经治理面（RBAC/限流/审计齐备）调用只读工具，失败返回 None。

    对话链路不再自带一份写死的业务文案——数据只从 Agent 本体经治理面取。
    """
    from app.core.agent.governor import agent_governor
    from app.core.agent.tools_v2 import ToolContext
    from app.core.observability.trace import get_business_key, get_trace_id

    user_id = str((current_user or {}).get("user_id") or "conversation")
    ctx = ToolContext(
        user_id=user_id,
        username=str((current_user or {}).get("username") or user_id),
        agent_key=agent_key,
        thread_id=f"conv:{agent_key}:{user_id}",
        trace_id=get_trace_id(),
        business_key=get_business_key(),
    )
    try:
        result = await agent_governor.execute(tool_name, args or {}, ctx)
    except Exception as e:  # noqa: BLE001
        logger.warning("conversation.tool_call_error", tool=tool_name, error=str(e))
        return None
    if not result.ok:
        logger.warning("conversation.tool_call_not_executed",
                       tool=tool_name, error=result.error)
        return None
    return result.data


def _note_fallback(agent_id: str, intent: str, reason: str) -> None:
    """兜底计数（裁决 D-E）。指标自身异常不许把对话带崩——同定制层防御口径。"""
    try:
        from app.core.metrics import CONVERSATION_FALLBACK

        CONVERSATION_FALLBACK.labels(agent_id=agent_id, intent=intent,
                                     reason=reason).inc()
    except Exception:  # noqa: BLE001
        pass


# ========== 业务上下文采集 ==========

async def _collect_business_context(agent_id: str,
                                    current_user: Optional[dict] = None) -> str:
    """
    采集与 Agent 相关的实时业务上下文，注入 LLM 提示词。

    这确保 LLM 的回复基于真实数据而非臆造。
    """
    context_parts = []

    if agent_id == "inventory-agent":
        agent = orchestrator.get_agent_by_name("库存守护Agent")
        if agent:
            inventory = agent.get_inventory_status()
            proposals = agent.get_proposals()
            context_parts.append(f"实时库存数据：{json.dumps(inventory, ensure_ascii=False, default=str) if inventory else '暂无库存数据'}")
            context_parts.append(f"补货建议：{json.dumps(proposals, ensure_ascii=False, default=str) if proposals else '暂无待处理补货建议'}")
        # 补充模拟概览数据（库存 Agent 事件驱动，缓存可能为空）
        context_parts.append(
            "库存概览：系统共监控12个品类，库存总价值¥1,892,450.50，"
            "其中4项低于安全库存（固态硬盘1TB、电源适配器65W、USB-C数据线、散热硅脂），"
            "1项已缺货（钢化玻璃面板），5项预警。"
            "主要品类：处理器（库存320件，安全库存200）、内存条（库存890件，安全库存300）、"
            "固态硬盘1TB（库存320件，安全库存500，低于安全线）、"
            "钢化玻璃面板（库存0件，安全库存200，已缺货）、电源适配器65W（库存210件，安全库存300）。"
        )

    elif agent_id == "accounting-agent":
        agent = orchestrator.get_agent_by_name("会计Agent") or orchestrator.get_agent_by_name("记账Agent")
        context_parts.append(
            "财务概览：当前共有3张待审批凭证，合计金额¥1,434,400。"
            "今日共处理凭证8张，已过账2张、待审批3张。"
            "主营业务收入¥856,000，管理费用¥386,200，净利润¥469,800。"
            "待审批凭证：记-20260726-002(销售产品收入-X9系列 ¥856,000)、"
            "记-20260726-003(支付员工工资 ¥358,000)、记-20260726-001(采购原材料-铝合金外壳 ¥220,400)。"
        )

    elif agent_id == "procurement-agent":
        agent = orchestrator.get_agent_by_name("采购Agent") or orchestrator.get_agent_by_name("采购助手Agent")
        context_parts.append(
            "采购概览：当前有1笔采购订单待审批（PO-20260726-045，铝合金外壳5800件，预计金额¥220,400）。"
            "候选供应商：深圳精密五金（¥35.8/件，交货8天，评分85.6）、"
            "苏州铝业集团（¥38.2/件，交货5天，评分92.1）、"
            "广州宏达制造（¥34.5/件，交货12天，评分78.3）。"
        )

    elif agent_id == "risk-agent":
        context_parts.append(
            "风控概览：本月共审计247项操作，发现2处合规风险——"
            "1项非工作时间段大额转账未复核、1项供应商信用评分低于阈值仍入库。"
            "今日已拦截3笔异常交易，冻结1个高风险订单。合规率99.2%。"
        )

    elif agent_id == "settlement-agent":
        context_parts.append(
            "结算概览：今日日清日结尚未执行。待对账记录12条，"
            "应收账款总额¥2,340,000，应付账款总额¥1,860,000。"
            "银行流水已同步至09:00，共320条记录。"
        )

    elif agent_id == "project-supervisor-agent":
        # 监理实时数据经治理面取 Agent 本体（裁决 D-A：不再在本路由里写一份文案，
        # 否则对话与 Agent 两套数据源必然漂移——历史上 proj-002/003 阶段就对不上）
        progress = await _call_governance_tool(
            "supervisor_query_progress", {}, "project_supervisor", current_user)
        compliance = await _call_governance_tool(
            "supervisor_check_compliance", {}, "project_supervisor", current_user)
        projects = (progress or {}).get("projects") or []
        if projects:
            context_parts.append(
                "在监项目实时监理数据："
                f"{json.dumps(projects, ensure_ascii=False, default=str)}")
        if compliance:
            context_parts.append(
                "合规核对实时结果："
                f"{json.dumps(compliance, ensure_ascii=False, default=str)}")
        if not projects:
            context_parts.append(
                "监理实时数据不可用（治理面未返回），请勿臆造项目、里程碑或风险数据。")

    return "\n".join(context_parts) if context_parts else "（暂无实时业务数据）"


# ========== 请求/响应模型 ==========

class ChatRequest(BaseModel):
    agent_id: str
    message: str


def _precheck_agent(agent_id: str, agent_config: Optional[dict]) -> Optional[dict]:
    """未知/离线 Agent 的一次性回复；无需进入 LLM 路径时返回 None。

    非流式与流式端点共用，避免同一文案出现两份实现。
    """
    if not agent_config:
        return {
            "content": f"未知的Agent: {agent_id}",
            "thinkingProcess": ["识别Agent类型", "未找到匹配的Agent"],
            "degraded": False,
        }
    if agent_config["status"] == "offline":
        return {
            "content": f"{agent_config['name']}当前处于离线状态，无法响应消息。请稍后再试。",
            "thinkingProcess": ["检查Agent状态", "Agent离线，返回提示"],
            "degraded": False,
        }
    return None


# ========== 路由端点 ==========

@router.get("/agents")
async def get_chat_agents():
    """获取可对话的 Agent 列表"""
    return {"agents": CHAT_AGENTS}


@router.post("/chat")
async def chat(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    """
    对话式交互 — 接入 DeepSeek LLM 生成差异化回复

    流程（工作包一）：
    1. 意图分类（规则确定性 + LLM 仲裁双路）
    2. 创作类意图 → LLM 结构化内容 → .docx 真文件 + 下载链接（1.3）
    3. 其余意图 → 采集实时业务上下文 → LLM 生成 JSON 回复
    4. LLM 不可用 → 按意图分组的降级骨架（裁决 D-A）：查询类返回监理 Agent
       本体实时数据，创作类转人工队列并生成管理员待办；一律带 ``degraded: true``
    """
    agent_id = req.agent_id
    message = req.message.strip()

    # 查找 Agent 配置
    agent_config = next((a for a in CHAT_AGENTS if a["id"] == agent_id), None)
    pre = _precheck_agent(agent_id, agent_config)
    if pre is not None:
        return pre

    intent = await classify_intent(message)

    # 创作类意图走文档产出专路（普通 LLM 对话路径产不出可下载的 .docx）
    if intent["intent"] == INTENT_CREATIVE:
        return await _handle_creative(agent_id, message, agent_config, intent, current_user)

    # 尝试 LLM 路径
    if settings.llm_enabled:
        llm_result = await _chat_with_llm(
            agent_id, message, agent_config, intent, current_user)
        if llm_result is not None:
            return llm_result

    # LLM 不可用时降级到分组降级骨架
    logger.info("conversation.fallback_to_rules",
                agent_id=agent_id, intent=intent["intent"],
                classifier=intent["classifier"])
    return await _degraded_response(
        agent_id, message, agent_config, intent, current_user)


def _chat_user_content(business_context: str, message: str, intent: Optional[dict],
                       *, structured: bool) -> str:
    """对话用户消息装配（非流式与流式共用，防止同一意图长出两套 prompt）。

    structured=True 走 JSON 契约；流式路径要的是纯文本增量——thinking 与 dataCard
    由路由侧用真实数据补齐，不靠模型自报。
    """
    intent = intent or {}
    hint = _INTENT_ANSWER_HINT.get(intent.get("intent", ""), "")
    if intent.get("project_id"):
        hint = f"{hint}用户指定项目编号：{intent['project_id']}。"
    body = (
        f"## 实时业务上下文\n{business_context}\n\n"
        f"## 用户问题\n{message}\n\n"
        + (f"## 本次意图侧重\n{hint}\n\n" if hint else "")
        + "请基于上述业务上下文回答用户问题，只回答本次问题涉及的意图，"
        "不要复述其他项目的通用概况。"
    )
    if structured:
        return (body
                + "返回JSON，包含 content（回复文本）、thinkingProcess（推理步骤列表）、"
                  "可选的 dataCard（结构化数据卡片）。")
    return body + "直接输出回复正文，不要包裹 JSON 或 markdown 代码块标记。"


async def _chat_with_llm(
    agent_id: str, message: str, agent_config: dict,
    intent: Optional[dict] = None, current_user: Optional[dict] = None,
) -> Optional[dict]:
    """
    使用 LLM 生成对话回复

    返回 None 表示 LLM 不可用，调用方应降级到分组降级骨架。
    """
    client = _get_llm_client()
    if client is None:
        return None

    system_prompt = AGENT_SYSTEM_PROMPTS.get(agent_id, "")
    if not system_prompt:
        return None

    # 采集业务上下文
    business_context = await _collect_business_context(agent_id, current_user)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": _chat_user_content(
            business_context, message, intent, structured=True)},
    ]

    try:
        response = await client.chat_completion(
            messages,
            response_format={"type": "json_object"},
            temperature=0.7,  # 对话场景用较高温度增加多样性
            max_tokens=4096,  # 确保有足够空间生成完整JSON响应
            extra=NO_THINK_EXTRA,  # 思考模式实测关不掉会吃掉首 token，参数口径见 client.NO_THINK_EXTRA
            scene="conversation_chat",
            # 与流式端点同口径计域，否则 /chat 与 /chat/stream 的成本可见性会分叉
            cost_domain=_cost_domain_for(agent_id),
        )
    except Exception as e:
        logger.warning("conversation.llm_call_failed",
                       agent_id=agent_id,
                       error_type=type(e).__name__,
                       error=str(e),
                       error_class=e.__class__.__module__ + "." + type(e).__name__)
        import traceback
        logger.debug("conversation.llm_call_traceback",
                     traceback=traceback.format_exc())
        return None

    if not response or not response.strip():
        logger.warning("conversation.llm_empty_response", agent_id=agent_id)
        return None

    # 解析 JSON 响应
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        # 尝试提取 JSON 片段
        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(response[start:end])
            else:
                logger.warning("conversation.llm_json_parse_failed",
                               agent_id=agent_id, raw=response[:200])
                return None
        except json.JSONDecodeError:
            logger.warning("conversation.llm_json_extract_failed",
                           agent_id=agent_id, raw=response[:200])
            return None

    # 校验并补全必要字段
    content = parsed.get("content", "").strip()
    if not content:
        logger.warning("conversation.llm_empty_content", agent_id=agent_id)
        return None

    thinking_process = parsed.get("thinkingProcess") or parsed.get("thinking_process") or []
    if not isinstance(thinking_process, list):
        thinking_process = [str(thinking_process)]

    data_card = _normalize_data_card(parsed.get("dataCard") or parsed.get("data_card"))

    logger.info("conversation.llm_success",
                agent_id=agent_id,
                content_length=len(content),
                thinking_steps=len(thinking_process),
                has_data_card=data_card is not None)

    result = {
        "content": content,
        "thinkingProcess": thinking_process,
        "degraded": False,
        "intent": (intent or {}).get("intent", ""),
        "classifier": (intent or {}).get("classifier", ""),
    }
    if data_card:
        result["dataCard"] = data_card

    return result


def _fmt_project_line(p: dict) -> str:
    """项目实时快照的一行文本（降级骨架与文档快照章节共用）。"""
    return (
        f"{p.get('project_id')} {p.get('project_name', '')}：阶段「{p.get('stage', '')}」，"
        f"里程碑 {p.get('milestones_completed', 0)}/{p.get('milestones_total', 0)}，"
        f"预算使用率 {round(float(p.get('budget_usage') or 0) * 100, 1)}%，"
        f"{'已具备上线条件' if p.get('launch_ready') else '未达上线条件'}，"
        f"风险 {len(p.get('risks') or [])} 项"
    )


# ========== 降级骨架（裁决 D-A：按意图分组，不伪装成正式回答） ==========

_QUERY_TOOLS = {
    INTENT_PROGRESS: "supervisor_query_progress",
    INTENT_LAUNCH_AUDIT: "supervisor_query_progress",
    INTENT_COMPLIANCE: "supervisor_check_compliance",
    INTENT_REPORT: "supervisor_generate_report",
}


async def _degraded_response(agent_id: str, message: str, agent_config: dict,
                             intent: dict, current_user: Optional[dict]) -> dict:
    """LLM 不可用时的分组降级骨架，一律带 ``degraded: true`` 并计入兜底指标。"""
    reason = "llm_unavailable" if not settings.llm_enabled else "llm_failed"
    if intent["intent"] != INTENT_CREATIVE:
        _note_fallback(agent_id, intent["intent"], reason)

    if intent["intent"] == INTENT_CREATIVE:
        body = await _manual_queue_response(agent_id, message, intent, current_user, reason)
    elif agent_id == "project-supervisor-agent" and intent["intent"] in _QUERY_TOOLS:
        body = await _degraded_supervisor_response(intent, current_user)
    else:
        body = _rule_based_response(agent_id, message, agent_config)

    return {**body, "degraded": True, "degradedReason": reason,
            "intent": intent["intent"], "classifier": intent["classifier"]}


async def _degraded_supervisor_response(intent: dict,
                                        current_user: Optional[dict]) -> dict:
    """
    查询类降级骨架：数据取自监理 Agent 本体（经治理面只读工具）。

    本路由不再自带一份项目文案——两套数据源必然漂移（历史上 proj-002/003
    的阶段就与 Agent 本体不一致），且写死文案会把降级伪装成正式回答。
    """
    body = await _supervisor_realtime_body(intent, current_user)
    if body is None:
        return {
            "content": (
                "实时监理数据当前不可用（治理面未返回数据：项目监理agent 未启动，"
                "或当前账号缺少 project:read 权限）。为避免误导，本次不给出项目结论，"
                "请稍后重试或联系管理员开通权限。"
            ),
            "thinkingProcess": [
                "识别意图：项目监理查询（降级骨架）",
                "经治理面读取监理 Agent 本体实时数据",
                "数据不可用 → 不臆造项目结论",
            ],
            "dataSource": "unavailable",
        }
    return body


async def _supervisor_realtime_body(intent: dict,
                                    current_user: Optional[dict]) -> Optional[dict]:
    """经治理面取监理 Agent 本体实时数据并按意图装配骨架；无数据返回 None。

    降级骨架与流式尾帧 dataCard 共用这里，保证两条链路是同一份数据、同一套口径。
    """
    tool = _QUERY_TOOLS[intent["intent"]]
    args: dict = {}
    if intent.get("project_id") and tool != "supervisor_generate_report":
        args["project_id"] = intent["project_id"]

    data = await _call_governance_tool(tool, args, "project_supervisor", current_user)
    if not data:
        return None

    if tool == "supervisor_query_progress":
        return (_launch_audit_skeleton(data)
                if intent["intent"] == INTENT_LAUNCH_AUDIT
                else _progress_skeleton(data))
    if tool == "supervisor_check_compliance":
        return _compliance_skeleton(data)
    return _report_skeleton(data)


def _progress_skeleton(data: dict) -> dict:
    projects = [p for p in (data.get("projects") or []) if isinstance(p, dict)]
    if not projects:
        return {
            "content": "实时监理数据中没有匹配的在监项目记录（请核对项目编号或稍后重试）。",
            "thinkingProcess": ["识别意图：项目进展查询", "经治理面读取监理快照",
                                "无匹配项目记录"],
            "dataSource": "agent_native",
        }
    done = sum(int(p.get("milestones_completed") or 0) for p in projects)
    total = sum(int(p.get("milestones_total") or 0) for p in projects)
    risk_items = sum(len(p.get("risks") or []) for p in projects)
    not_ready = [p for p in projects if not p.get("launch_ready")]
    return {
        "content": (
            f"实时监理数据（来源：项目监理agent 本体，经治理面读取）：在监 {len(projects)} 个项目，"
            f"里程碑合计 {done}/{total}，风险条目 {risk_items} 项，"
            f"未达上线条件 {len(not_ready)} 个。\n"
            + "\n".join(f"· {_fmt_project_line(p)}" for p in projects)
        ),
        "thinkingProcess": [
            "识别意图：项目进展查询（降级骨架）",
            "经治理面调用 supervisor_query_progress",
            "汇总里程碑完成率、预算使用率与风险计数",
            "按项目逐条输出实时监理快照",
        ],
        "dataCard": {
            "type": "table",
            "title": "在监项目实时监理快照",
            "columns": [
                {"key": "project_id", "label": "项目编号"},
                {"key": "project_name", "label": "项目"},
                {"key": "stage", "label": "当前阶段"},
                {"key": "milestones", "label": "里程碑"},
                {"key": "budget", "label": "预算使用率"},
                {"key": "launch_ready", "label": "上线就绪"},
                {"key": "risks", "label": "风险数"},
            ],
            "rows": [
                {
                    "project_id": p.get("project_id", ""),
                    "project_name": p.get("project_name", ""),
                    "stage": p.get("stage", ""),
                    "milestones": f"{p.get('milestones_completed', 0)}/{p.get('milestones_total', 0)}",
                    "budget": f"{round(float(p.get('budget_usage') or 0) * 100, 1)}%",
                    "launch_ready": "是" if p.get("launch_ready") else "否",
                    "risks": len(p.get("risks") or []),
                }
                for p in projects
            ],
        },
        "dataSource": "agent_native",
    }


def _launch_audit_skeleton(data: dict) -> dict:
    projects = [p for p in (data.get("projects") or []) if isinstance(p, dict)]
    if not projects:
        return {
            "content": "实时监理数据中没有匹配的项目，无法出具上线就绪度审计结论。",
            "thinkingProcess": ["识别意图：上线就绪审计", "经治理面读取监理快照",
                                "无匹配项目记录"],
            "dataSource": "unavailable",
        }
    ready = [p for p in projects if p.get("launch_ready")]
    return {
        "content": (
            f"上线就绪度审计（数据源：项目监理agent 本体实时快照）："
            f"{len(projects)} 个在监项目中 {len(ready)} 个已具备上线条件。"
            + "\n" + "\n".join(
                f"· {p.get('project_id')} {p.get('project_name', '')}：当前阶段「{p.get('stage', '')}」，"
                f"{'具备上线条件' if p.get('launch_ready') else '暂不具备上线条件（阶段未到 UAT验收/上线发布）'}，"
                f"风险 {len(p.get('risks') or [])} 项"
                for p in projects)
        ),
        "thinkingProcess": [
            "识别意图：上线/发布就绪审计（降级骨架）",
            "经治理面调用 supervisor_query_progress 取各项目 launch_ready 与阶段",
            "逐项目判定上线就绪度",
            "输出审计结论与前置条件缺口",
        ],
        "dataCard": {
            "type": "table",
            "title": "上线就绪度审计（实时）",
            "columns": [
                {"key": "project", "label": "项目"},
                {"key": "stage", "label": "当前阶段"},
                {"key": "milestones", "label": "里程碑"},
                {"key": "verdict", "label": "审计结论"},
                {"key": "risks", "label": "风险项"},
            ],
            "rows": [
                {
                    "project": f"{p.get('project_id')} {p.get('project_name', '')}",
                    "stage": p.get("stage", ""),
                    "milestones": f"{p.get('milestones_completed', 0)}/{p.get('milestones_total', 0)}",
                    "verdict": "具备上线条件" if p.get("launch_ready") else "暂不具备上线条件",
                    "risks": "；".join(p.get("risks") or []) or "无",
                }
                for p in projects
            ],
        },
        "dataSource": "agent_native",
    }


def _compliance_skeleton(data: dict) -> dict:
    stages = data.get("stages") or {}
    risks = data.get("risks") or []
    rows = [{"stage": s, "status": st} for s, st in stages.items() if isinstance(s, str)]
    return {
        "content": (
            f"项目合规检查（数据源：项目监理agent 本体 check_compliance）："
            f"阶段核对 {'、'.join(f'{s}={st}' for s, st in stages.items()) or '无数据'}；"
            f"登记风险 {len(risks)} 项，"
            f"{'存在高风险项，需人工介入' if data.get('high_risk') else '未见高风险项'}。"
            + ("\n风险清单：" + "；".join(str(r) for r in risks[:5]) if risks else "")
        ),
        "thinkingProcess": [
            "识别意图：项目合规检查（降级骨架）",
            "经治理面调用 supervisor_check_compliance",
            "按六阶段清单核对合规状态并汇总风险分级",
            "输出合规结论与风险清单",
        ],
        "dataCard": {
            "type": "table",
            "title": "监理阶段合规核对结果（实时）",
            "columns": [{"key": "stage", "label": "监理阶段"},
                        {"key": "status", "label": "状态"}],
            "rows": rows,
        },
        "dataSource": "agent_native",
    }


def _report_skeleton(data: dict) -> dict:
    projects = [p for p in (data.get("projects") or []) if isinstance(p, dict)]
    high = data.get("high_risks") or []
    medium = data.get("medium_risks") or []
    return {
        "content": (
            f"监理报告 {data.get('report_id', '')}（生成时间 {data.get('generated_at', '')}）："
            f"在监 {data.get('supervised_projects', len(projects))} 个项目，"
            f"登记违规 {data.get('violations_recorded', 0)} 项，"
            f"整体判定「{data.get('overall', '未知')}」，"
            f"高风险 {len(high)} 项 / 中风险 {len(medium)} 项。"
            + ("\n高风险：" + "；".join(str(r) for r in high[:5]) if high else "")
        ),
        "thinkingProcess": [
            "识别意图：监理报告查询（降级骨架）",
            "经治理面调用 supervisor_generate_report（内部先同步项目实时进度）",
            "按风险三级归类",
            "输出报告编号与整体判定结论",
        ],
        "dataCard": {
            "type": "stats",
            "title": f"监理报告 {data.get('report_id', '')}",
            "stats": [
                {"label": "在监项目", "value": str(data.get("supervised_projects", len(projects))),
                 "tone": "primary"},
                {"label": "整体判定", "value": str(data.get("overall", "未知")), "tone": "accent"},
                {"label": "高风险项", "value": str(len(high)), "tone": "circuit"},
                {"label": "中风险项", "value": str(len(medium)), "tone": "paused"},
            ],
        },
        "dataSource": "agent_native",
    }


# ========== 创作类链路（工作包一 1.3 / 裁决 D-C） ==========

_CREATIVE_SYSTEM_PROMPT = (
    "你是企业ERP系统中的文档创作Agent，负责把监理创作需求装配成结构化文档数据。\n"
    "必须返回合法 JSON，不要包含 markdown 代码块标记，格式：\n"
    "{\n"
    '  "title": "文档标题（含项目名与文档类型，30字内）",\n'
    '  "doc_type": "监理大纲 | 监理实施方案 | 监理报告 | 其他",\n'
    '  "summary": "30-80字文档摘要",\n'
    '  "sections": [{"heading": "章节标题", "content": "章节正文（150-400字）"}],\n'
    '  "conclusion": "结论与下一步安排（80-200字）"\n'
    "}\n"
    "要求：\n"
    "1. 章节不少于 4 个，且覆盖监理范围与依据、监理阶段与检查点、风险与应对措施；\n"
    "2. 必须引用「实时业务上下文」中的项目编号、项目名称与当前阶段，"
    "不得编造上下文里没有的数字或里程碑；\n"
    "3. 按项目**当前监理阶段**突出重点检查项（阶段不匹配的内容不要写成本阶段工作）；\n"
    "4. 这是 AI 生成的草稿，正式交付前需人工复核。"
)

# 六阶段监理重点（阶段匹配度的确定性来源）
_STAGE_FOCUS = {
    "立项审批": "立项材料完整性与评审结论留痕，预算与工期基线冻结",
    "需求评审": "需求基线冻结与变更控制流程，需求—设计—用例可追溯矩阵",
    "开发测试": "里程碑交付核验、集成测试覆盖率与缺陷闭环，进度偏差与预算使用率监控",
    "UAT验收": "UAT 用例执行与缺陷闭环、验收标准确认与业务方签字留痕",
    "上线发布": "上线前置条件逐项核对（变更审批、回滚预案、数据迁移校验、值守安排）",
    "结项审计": "结项资料归档、尾项清单跟踪与后评价安排",
}

# 结构完整性下限：草稿必须齐备的骨架章节（缺失则以"待人工补充"补齐，不臆造内容）
_DEFAULT_SECTIONS = ("监理范围与依据", "监理阶段与检查点", "风险与应对措施")
_DRAFT_SECTION_HINT = "AI 草稿未展开该章节，需人工补充后方可提交审批。"


def build_creative_doc_data(agent_config: dict, message: str, outline: dict,
                            realtime: Optional[list[dict]] = None, *,
                            trace_id: str = "", generated_at: str = "",
                            user_id: str = "") -> dict:
    """
    把 LLM 的结构化创作结果装配成 docx report 模板数据（确定性可评测面）。

    装配规则让裁决 D-E 的三个 golden 维度**在代码里成立**而非只在测试里断言：
    - 结构完整性：章节补齐到骨架齐备（缺失章节标"待人工补充"，不臆造内容）；
    - 项目数据感知性：实时项目逐条写入「在监项目实时快照」章节（真实编号/阶段/里程碑）；
    - 阶段匹配度：按各项目**当前阶段**注入 ``_STAGE_FOCUS`` 对应的重点检查项。
    """
    projects = [p for p in (realtime or []) if isinstance(p, dict)]
    # 与 docx 模板同源剥离自带编号，保证界面 dataCard 章节名与产物标题一致
    from app.core.document.docx_generator import strip_section_leading_number

    raw_sections = outline.get("sections")
    sections: list[dict] = []
    for s in (raw_sections if isinstance(raw_sections, list) else []):
        if not isinstance(s, dict) or not s.get("heading"):
            continue
        heading = strip_section_leading_number(str(s["heading"]))[:60]
        if not heading:
            continue
        item = {"heading": heading,
                "content": str(s.get("content", ""))[:3000]}
        if isinstance(s.get("table"), dict):
            item["table"] = s["table"]
        sections.append(item)
    sections = sections[:12]

    if projects:
        sections.append({
            "heading": "在监项目实时快照（数据来源：项目监理agent）",
            "content": "；".join(_fmt_project_line(p) for p in projects),
            "table": {
                "headers": ["项目编号", "项目名称", "当前阶段", "里程碑", "预算使用率", "上线就绪"],
                "rows": [
                    [p.get("project_id", ""), p.get("project_name", ""), p.get("stage", ""),
                     f"{p.get('milestones_completed', 0)}/{p.get('milestones_total', 0)}",
                     f"{round(float(p.get('budget_usage') or 0) * 100, 1)}%",
                     "是" if p.get("launch_ready") else "否"]
                    for p in projects
                ],
            },
        })
        sections.append({
            "heading": "本阶段监理重点",
            "content": "；".join(
                f"{p.get('project_id')}（当前阶段 {p.get('stage', '')}）："
                f"{_STAGE_FOCUS.get(str(p.get('stage', '')), '按监理阶段清单核对交付物与留痕')}"
                for p in projects),
        })
    else:
        sections.append({
            "heading": "本阶段监理重点",
            "content": "未取到项目实时快照，按「立项→需求→开发→UAT→上线→结项」六阶段"
                       "通用重点清单执行，具体项目阶段需人工确认。",
        })

    for required in _DEFAULT_SECTIONS:
        if not any(required in s["heading"] for s in sections):
            sections.append({"heading": required, "content": _DRAFT_SECTION_HINT})

    doc_type = str(outline.get("doc_type") or "监理文档")[:32]
    title = str(outline.get("title") or "").strip()[:80]
    if not title:
        names = "、".join(str(p.get("project_name", "")) for p in projects[:2]) or "未指定项目"
        title = f"{names}{doc_type}"

    summary = str(outline.get("summary") or "")[:500]
    summary = (
        f"{summary}（本文档为 AI 生成草稿，数据取自项目监理agent 实时快照，"
        "未经审批不得作为正式交付物。)".strip()
    )
    return {
        "title": title,
        "report_no": f"AI-DRAFT-{(trace_id or 'NA')[-12:]}",
        "author": f"{agent_config.get('name', 'Agent')} · AI 生成（请求人 {user_id or 'unknown'}）",
        "date": generated_at,
        "summary": summary,
        "sections": sections,
        "conclusion": str(outline.get("conclusion") or "")[:2000] or _DRAFT_SECTION_HINT,
        # 裁决 D-C：AI 生成文档页眉强制标注，可溯源、防冒充正式文件
        "header_note": (
            f"AI 生成草稿 · 生成时间 {generated_at} · trace_id {trace_id or 'NA'}"
            " · 未经审批，不得作为正式交付物"
        ),
    }


def _safe_doc_filename(title: str, trace_id: str) -> str:
    """产物文件名：与文档生成工具共用同一净化口径（见 output.safe_artifact_name）。"""
    from app.core.document.output import safe_artifact_name

    return safe_artifact_name(title, trace_id, "docx")


async def _handle_creative(agent_id: str, message: str, agent_config: dict,
                           intent: dict, current_user: Optional[dict]) -> dict:
    """创作类意图 → LLM 结构化内容 → .docx 真文件 + 下载链接；失败转人工队列。"""
    user_id = str((current_user or {}).get("user_id") or "conversation")

    realtime: list[dict] = []
    if agent_id == "project-supervisor-agent":
        args = ({"project_id": intent["project_id"]} if intent.get("project_id") else {})
        progress = await _call_governance_tool(
            "supervisor_query_progress", args, "project_supervisor", current_user)
        realtime = list((progress or {}).get("projects") or [])

    outline = await _llm_creative_outline(agent_id, message, intent, current_user)
    if outline is None:
        reason = "llm_unavailable" if not settings.llm_enabled else "llm_failed"
        _note_fallback(agent_id, INTENT_CREATIVE, reason)
        return {**await _manual_queue_response(agent_id, message, intent, current_user, reason),
                "degraded": True, "degradedReason": reason,
                "intent": INTENT_CREATIVE, "classifier": intent["classifier"]}

    from app.core.observability.trace import get_trace_id

    trace_id = get_trace_id()
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    doc_data = build_creative_doc_data(
        agent_config, message, outline, realtime,
        trace_id=trace_id, generated_at=generated_at, user_id=user_id)
    filename = _safe_doc_filename(doc_data["title"], trace_id)

    from app.core.document import document_generator

    try:
        path = document_generator.generate(
            format="docx", template_type="report", data=doc_data, filename=filename)
        size = os.path.getsize(path)
    except Exception as e:  # noqa: BLE001
        logger.error("conversation.creative_doc_failed",
                     agent_id=agent_id, error=str(e), error_type=type(e).__name__)
        _note_fallback(agent_id, INTENT_CREATIVE, "doc_failed")
        return {**await _manual_queue_response(agent_id, message, intent, current_user, "doc_failed"),
                "degraded": True, "degradedReason": "doc_failed",
                "intent": INTENT_CREATIVE, "classifier": intent["classifier"]}

    await _audit_creative_generation(
        user_id=user_id, agent_id=agent_id, message=message,
        doc_data=doc_data, path=path, size=size, trace_id=trace_id)

    download_name = os.path.basename(path)
    download_url = f"/api/v1/document/download/{download_name}"
    sections = doc_data["sections"]
    logger.info("conversation.creative_generated", agent_id=agent_id,
                filename=download_name, sections=len(sections), size=size)
    return {
        "content": (
            f"已生成《{doc_data['title']}》Word 草稿：{len(sections)} 个章节、"
            f"引用实时项目 {len(realtime)} 个。AI 草稿未经审批，"
            f"正式提交请走既有审批流。\n下载：{download_url}"
        ),
        "thinkingProcess": [
            "识别意图：创作类文档产出",
            "经治理面取监理 Agent 实时快照作为数据依据",
            "LLM 装配结构化章节（标题/摘要/章节/结论）",
            "补齐章节骨架 + 注入实时项目快照与本阶段重点",
            "调用文档生成器产出 .docx（页眉标注 AI 草稿与 trace_id）并落审计",
        ],
        "dataCard": {
            "type": "list",
            "title": f"文档结构（{len(sections)} 章）",
            "items": [{"label": s["heading"], "value": f"{len(s['content'])} 字"}
                      for s in sections[:12]],
        },
        "attachments": [{
            "name": doc_data["title"],
            "filename": download_name,
            "url": download_url,
            "format": "docx",
            "size": size,
        }],
        "traceId": trace_id,
        "degraded": False,
        "intent": INTENT_CREATIVE,
        "classifier": intent["classifier"],
    }


async def _llm_creative_outline(agent_id: str, message: str, intent: dict,
                                current_user: Optional[dict]) -> Optional[dict]:
    """LLM 装配创作类结构化内容（返回 dict；不可用/解析失败返回 None）。"""
    client = _get_llm_client() if settings.llm_enabled else None
    if client is None:
        return None

    business_context = await _collect_business_context(agent_id, current_user)
    scope = f"目标项目：{intent['project_id']}" if intent.get("project_id") else "范围：全部在监项目"
    try:
        response = await client.chat_completion(
            [
                {"role": "system", "content": _CREATIVE_SYSTEM_PROMPT},
                {"role": "user", "content":
                    f"## 实时业务上下文\n{business_context}\n\n"
                    f"## 创作需求\n{message}\n{scope}\n\n"
                    "请按上下文中的实时数据装配文档结构 JSON。"},
            ],
            response_format={"type": "json_object"},
            temperature=0.6,
            max_tokens=4096,
            extra=NO_THINK_EXTRA,
            scene="creative_document",
            # 创作类是长文本高消耗流量，按域计入日累计（裁决 D-E 成本口径）。
            cost_domain=_cost_domain_for(agent_id),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("conversation.creative_llm_failed",
                       agent_id=agent_id, error=str(e),
                       error_type=type(e).__name__)
        return None

    if not response or not response.strip():
        return None
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        start, end = response.find("{"), response.rfind("}") + 1
        if start < 0 or end <= start:
            logger.warning("conversation.creative_llm_unparseable", agent_id=agent_id)
            return None
        try:
            parsed = json.loads(response[start:end])
        except json.JSONDecodeError:
            logger.warning("conversation.creative_llm_unparseable", agent_id=agent_id)
            return None
    return parsed if isinstance(parsed, dict) else None


async def _audit_creative_generation(*, user_id: str, agent_id: str, message: str,
                                     doc_data: dict, path: str, size: int,
                                     trace_id: str) -> None:
    """生成行为全量审计（裁决 D-C：谁/何时/什么提示词 → 什么产物）。"""
    try:
        from app.core.security.audit import audit_chain

        await audit_chain.log(
            actor_did=user_id,
            action="document.creative_generated",
            resource=f"document:{os.path.basename(path)}",
            result="success",
            details={
                "agent": agent_id,
                "prompt": message[:1000],
                "template": "report",
                "title": doc_data.get("title"),
                "sections": len(doc_data.get("sections") or []),
                "path": path,
                "size": size,
                "header_note": doc_data.get("header_note"),
            },
            risk_level="medium",
            trace_id=trace_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("conversation.creative_audit_failed", error=str(e))


async def _manual_queue_response(agent_id: str, message: str, intent: dict,
                                 current_user: Optional[dict], reason: str) -> dict:
    """创作类降级：明确告知服务不可用 + 落管理员待办（裁决 D-A「已转人工队列」）。"""
    from app.core.agent.orchestration_service import create_admin_todo

    requested_by = str((current_user or {}).get("user_id") or "conversation")
    todo_id = await create_admin_todo(
        agent_key=agent_id,
        tool="creative_document",
        preview=(
            f"创作类请求待人工处理｜Agent={agent_id}｜"
            f"项目={intent.get('project_id') or '未指定'}\n"
            f"降级原因：{reason}\n原始请求：{message[:500]}"
        ),
        args={"message": message[:1000], "project_id": intent.get("project_id"),
              "reason": reason},
        requested_by=requested_by,
    )
    queue_note = (
        f"已生成管理员待办 {todo_id}，可在「Agent 审批 / 待办」列表受理。"
        if todo_id else "人工队列工单未能创建（数据库不可用），请稍后重试。"
    )
    return {
        "content": (
            f"创作服务暂不可用（{reason}），本次未生成文档草稿。" + queue_note
        ),
        "thinkingProcess": [
            "识别意图：创作类文档产出",
            "AI 创作通道不可用，未生成文档",
            "转人工队列并生成管理员待办（不以写死文案冒充回答）",
        ],
        "manualQueue": True,
        "todoId": todo_id,
    }


def _rule_based_response(agent_id: str, message: str, agent_config: dict) -> dict:
    """
    规则匹配降级方案（LLM 不可用时使用）

    基于关键词匹配返回预设回复。注意：匹配优先级从具体到宽泛，
    避免宽泛关键词（如"库存"）吞没具体意图（如"预测库存"）。
    """
    lower = message.lower()
    agent_name = agent_config["name"]

    # --- 库存管家 ---
    if agent_id == "inventory-agent":
        # 预测类（优先匹配，避免被"库存"关键词吞没）
        if any(kw in lower for kw in ["预测", "预估", "趋势", " forecast", "下季度", "下月", "未来"]):
            return {
                "content": (
                    "基于历史消耗趋势与季节性分析，下季度库存预测如下：\n"
                    "1. 钢化玻璃面板：当前已缺货，预计下季度需求1500件，建议立即补货1000件；\n"
                    "2. 固态硬盘1TB：当前320件低于安全库存500件，预计下季度消耗800件，建议补货800件；\n"
                    "3. 电源适配器65W：当前210件，预计下季度消耗400件，建议补货500件；\n"
                    "4. 处理器与内存条：库存充足，预计下季度平稳消耗，无需补货。\n"
                    "综合预测下季度库存周转率将提升8%，总采购预算约¥464,500。"
                ),
                "thinkingProcess": [
                    "识别意图：库存预测（非实时查询）",
                    "加载历史消耗数据与季节性因子",
                    "对每个品类运行趋势预测模型",
                    "生成下季度需求预测与补货建议",
                ],
                "dataCard": {
                    "type": "table",
                    "title": "下季度库存预测",
                    "columns": [
                        {"key": "name", "label": "品类"},
                        {"key": "current", "label": "当前库存"},
                        {"key": "predicted", "label": "预测需求"},
                        {"key": "action", "label": "建议操作"},
                    ],
                    "rows": [
                        {"name": "钢化玻璃面板", "current": "0", "predicted": "1500", "action": "紧急补货1000件"},
                        {"name": "固态硬盘1TB", "current": "320", "predicted": "800", "action": "补货800件"},
                        {"name": "电源适配器65W", "current": "210", "predicted": "400", "action": "补货500件"},
                        {"name": "处理器", "current": "320", "predicted": "250", "action": "无需补货"},
                    ],
                },
            }

        # 补货类
        if any(kw in lower for kw in ["补货", "建议", "采购", "进货"]):
            return {
                "content": (
                    "根据销量预测模型，生成5条补货建议。"
                    "钢化玻璃面板为紧急补货（已缺货），预计总采购金额¥464,500。"
                ),
                "thinkingProcess": [
                    "加载 reorder_engine 模块",
                    "计算每个SKU的再订货点ROP",
                    "识别5个SKU低于ROP",
                    "调用supplier.quote()获取报价",
                    "生成补货建议清单",
                ],
                "dataCard": {
                    "type": "table",
                    "title": "智能补货建议",
                    "columns": [
                        {"key": "sku", "label": "SKU"},
                        {"key": "name", "label": "品名"},
                        {"key": "current", "label": "当前库存"},
                        {"key": "suggest", "label": "建议补货"},
                        {"key": "cost", "label": "预计金额"},
                    ],
                    "rows": [
                        {"sku": "SKU-B002", "name": "钢化玻璃面板", "current": "0", "suggest": "1000", "cost": "¥95,000"},
                        {"sku": "SKU-A002", "name": "固态硬盘1TB", "current": "320", "suggest": "800", "cost": "¥336,000"},
                        {"sku": "SKU-C002", "name": "电源适配器65W", "current": "210", "suggest": "500", "cost": "¥22,500"},
                    ],
                },
            }

        # 查询类（最宽泛，放最后）
        if any(kw in lower for kw in ["库存", "状态", "查询", "查看", "多少"]):
            return {
                "content": (
                    "已为您查询当前库存状态。系统共监控12个品类，"
                    "其中4项低于安全库存，1项已缺货。库存总价值¥1,892,450.50。"
                ),
                "thinkingProcess": [
                    "解析用户意图：查询库存状态",
                    "调用inventory.list()获取全部SKU",
                    "按状态分类统计并汇总",
                    "生成库存总览数据卡片",
                ],
                "dataCard": {
                    "type": "stats",
                    "title": "库存状态总览",
                    "stats": [
                        {"label": "库存品类", "value": "12", "tone": "primary"},
                        {"label": "库存总价值", "value": "¥1,892,450", "tone": "accent"},
                        {"label": "预警品类", "value": "5", "tone": "paused"},
                        {"label": "缺货品类", "value": "1", "tone": "circuit"},
                    ],
                },
            }

    # --- 会计专员 ---
    if agent_id == "accounting-agent":
        if any(kw in lower for kw in ["日报", "日结", "结算", "利润", "收入"]):
            return {
                "content": (
                    "今日日报已生成。本日共处理凭证8张，其中已过账2张、待审批3张。"
                    "主营业务收入¥856,000，管理费用¥386,200，净利润¥469,800。"
                    "日清日结流程已就绪。"
                ),
                "thinkingProcess": [
                    "汇总当日所有凭证数据",
                    "按科目分类计算收支",
                    "生成利润概算",
                    "检查日清日结前置条件",
                ],
                "dataCard": {
                    "type": "stats",
                    "title": "今日财务日报",
                    "stats": [
                        {"label": "凭证总数", "value": "8", "tone": "primary"},
                        {"label": "主营业务收入", "value": "¥856,000", "tone": "accent"},
                        {"label": "管理费用", "value": "¥386,200", "tone": "paused"},
                        {"label": "净利润", "value": "¥469,800", "tone": "accent"},
                    ],
                },
            }
        if any(kw in lower for kw in ["凭证", "审批", "分录"]):
            return {
                "content": (
                    "当前共有3张待审批凭证，合计金额¥1,434,400。"
                    "所有凭证借贷平衡，税务计算无误，可进入审批流程。"
                ),
                "thinkingProcess": [
                    "调用voucher.list(status=pending)",
                    "校验借贷平衡与税务计算",
                    "按金额降序排列",
                    "生成审批建议",
                ],
                "dataCard": {
                    "type": "table",
                    "title": "待审批凭证清单",
                    "columns": [
                        {"key": "no", "label": "凭证号"},
                        {"key": "summary", "label": "摘要"},
                        {"key": "amount", "label": "金额"},
                        {"key": "status", "label": "校验"},
                    ],
                    "rows": [
                        {"no": "记-20260726-002", "summary": "销售产品收入-X9系列", "amount": "¥856,000", "status": "通过"},
                        {"no": "记-20260726-003", "summary": "支付员工工资", "amount": "¥358,000", "status": "通过"},
                        {"no": "记-20260726-001", "summary": "采购原材料-铝合金外壳", "amount": "¥220,400", "status": "通过"},
                    ],
                },
            }

    # --- 风控哨兵 ---
    if agent_id == "risk-agent":
        if any(kw in lower for kw in ["合规", "检查", "审计"]):
            return {
                "content": (
                    "合规检查完成。本月共审计247项操作，发现2处合规风险："
                    "1项非工作时间段大额转账未复核、1项供应商信用评分低于阈值仍入库。建议立即处理。"
                ),
                "thinkingProcess": [
                    "加载compliance_rules规则引擎",
                    "扫描本月全部247项操作记录",
                    "匹配18条合规规则",
                    "识别2处违规",
                    "生成合规风险报告",
                ],
                "dataCard": {
                    "type": "stats",
                    "title": "合规审计结果",
                    "stats": [
                        {"label": "审计操作数", "value": "247", "tone": "primary"},
                        {"label": "合规通过", "value": "245", "tone": "accent"},
                        {"label": "合规风险", "value": "2", "tone": "circuit"},
                        {"label": "合规率", "value": "99.2%", "tone": "accent"},
                    ],
                },
            }
        return {
            "content": (
                "风控哨兵正在值守。当前风控引擎运行正常，"
                "今日已拦截3笔异常交易，冻结1个高风险订单。"
            ),
            "thinkingProcess": ["加载风控仪表盘", "汇总今日风控事件", "返回状态总览"],
        }

    # --- 采购助手 ---
    if agent_id == "procurement-agent":
        return {
            "content": (
                "采购助手已就绪。当前有1笔采购订单待审批"
                "（PO-20260726-045，铝合金外壳5800件，预计¥220,400）。"
            ),
            "thinkingProcess": ["查询待办采购订单", "返回采购状态总览"],
        }

    # --- 结算管家 ---
    if agent_id == "settlement-agent":
        return {
            "content": "结算管家当前离线，请稍后再试。",
            "thinkingProcess": ["检查Agent状态", "Agent离线"],
        }

    # --- 项目监理agent ---
    # 规则层不再直出监理数据：数据类意图统一由 classify_intent →
    # _degraded_response 走治理面工具（消除"两处数据源"不一致）。
    if agent_id == "project-supervisor-agent":
        return {
            "content": (
                "项目监理agent已就绪。我可以帮您查询项目进展、检查合规状态、"
                "执行上线审计或生成监理报告。请问您需要监理哪个项目？"
            ),
            "thinkingProcess": ["解析用户输入", "未命中具体意图", "返回能力引导"],
        }

    # 默认回复
    return {
        "content": f"我是{agent_name}，已收到您的消息。请问还有什么可以帮您？",
        "thinkingProcess": ["解析用户输入", "未匹配具体意图", "返回能力引导"],
    }


# ========== 流式输出（工作包三 / 裁决 D-D） ==========

# 敏感模式可能被切成两片拼接逃逸（16 位卡号跨分片最常见），所以按滑动窗口复核，
# 相邻窗口留 overlap 重叠区；流结束后再对聚合全文复核一次（裁决 D-D 第 2 条）。
_STREAM_GUARD_WINDOW = 240
_STREAM_GUARD_OVERLAP = 120
_STREAM_RETRACT_NOTICE = "[内容已撤回]"

# 出厂 AGENT_SYSTEM_PROMPTS 自带 JSON 契约（content/thinkingProcess/dataCard），
# 流式路径照抄会让模型把 `{"content":"` 逐字吐进正文流——用户先看到花括号才叫"渐进渲染"，
# 而且白烧一段 dataCard token（裁决 D-E 第 3 条成本口径）。流式改吃纯文本指令：
# thinkingProcess 由 thinking 帧逐条给出，dataCard 由治理面实时数据补齐。
_STREAM_PLAIN_TEXT_DIRECTIVE = (
    "\n\n【流式输出模式】直接以纯文本逐字输出回复正文本身："
    "不要输出 JSON 结构、字段名、花括号或 markdown 代码块标记；"
    "推理步骤与结构化数据卡片由系统另行生成，你不需要提供；"
    "正文只回答本次问题，控制在 300 字以内。"
)

# 流式状态 → 审计 result（沿用仓内 success/denied/failure 口径）
_STREAM_AUDIT_RESULT = {"completed": "success", "degraded": "success",
                        "retracted": "denied", "error": "failure",
                        "aborted": "failure"}

# 一次性回复中需要透传给前端的附加字段（降级标记、人工队列、文档附件）
_BODY_EXTRAS = ("degradedReason", "dataSource", "manualQueue", "todoId", "attachments")

# 断线路径的审计任务引用：无引用的 Task 会被 GC，留痕就丢了
_stream_audit_tasks: set = set()


def _cost_domain_for(agent_id: str) -> str:
    """Agent id → 治理面域名口径（project-supervisor-agent → project_supervisor）。

    统一用下划线口径，避免与 agent_domain 形成两套键。
    """
    stem = agent_id[: -len("-agent")] if agent_id.endswith("-agent") else agent_id
    return stem.replace("-", "_")


def _sse(event: str, data: dict) -> str:
    """SSE 帧编码：event 名 + 单行 JSON data（前端按 event 分帧消费）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_guard_hit(text: str) -> Optional[dict]:
    """对已生成文本复核一次；需要整段撤回时返回威胁详情，否则 None。

    两档：
    1. 注入/越狱/恶意代码——用 check_input 而不是 check_output：输出护栏只产 SANITIZE/ALLOW
       （guardrails.py:171-179），没有 BLOCK 语义，挡不住整段撤回的场景。
    2. 凭证形态（password=/api_key=/secret=/private_key=/token=<长串>）——对话链路历来
       不接输出脱敏，模型把密钥吐进正文就会被原样推送；命中即整段撤回。
       卡号/SSN 明确不撤回：监理与财务问答里 16 位单号极常见，误伤正确答案的代价更高。
    """
    from app.core.security.guardrails import GuardrailAction, guardrails

    action, threats = guardrails.check_input(text, "")
    if action in (GuardrailAction.BLOCK, GuardrailAction.ESCALATE):
        return (threats or [{}])[0]
    leaks = guardrails.find_credential_leaks(text)
    if leaks:
        return {"type": "credential_leak", "pattern": leaks[0]["pattern"],
                "severity": "critical"}
    return None


async def _stream_audit(*, user_id: str, agent_id: str, message: str, intent: dict,
                        trace_id: str, status: str, chars: int, started: float,
                        reason: str = "", extras: Optional[dict] = None) -> None:
    """一次流式调用一条审计（裁决 D-D：撤回与断线同样留痕）。

    审计失败不打断响应——对话可用性优先，但 error 级告警（留痕缺口必须可见）。
    """
    entry = {
        "agent": agent_id,
        "prompt": message[:500],
        "intent": intent.get("intent", ""),
        "classifier": intent.get("classifier", ""),
        "status": status,
        "chars": chars,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }
    if reason:
        entry["reason"] = reason[:300]
    if extras:
        entry.update(extras)
    try:
        from app.core.security.audit import audit_chain

        await audit_chain.log(
            actor_did=user_id,
            action="conversation.stream_answered",
            resource=f"conversation:{agent_id}",
            result=_STREAM_AUDIT_RESULT.get(status, "failure"),
            details=entry,
            risk_level="high" if status == "retracted" else "low",
            trace_id=trace_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("conversation.stream_audit_failed",
                     agent_id=agent_id, status=status, error=str(e))


def _stream_audit_soon(**kwargs) -> None:
    """断线时的审计：生成器正在收尾不能再 await，挂成独立任务并持有引用。"""
    try:
        task = asyncio.get_running_loop().create_task(_stream_audit(**kwargs))
    except RuntimeError:  # 事件循环已关闭
        logger.warning("conversation.stream_audit_dropped", status=kwargs.get("status"))
        return
    _stream_audit_tasks.add(task)
    task.add_done_callback(_stream_audit_tasks.discard)


def _done_payload(*, trace_id: str, intent: dict, status: str, started: float,
                  degraded: bool = False, extras: Optional[dict] = None) -> dict:
    payload = {
        "traceId": trace_id,
        "intent": intent.get("intent", ""),
        "classifier": intent.get("classifier", ""),
        "degraded": degraded,
        "status": status,
        "elapsedMs": round((time.monotonic() - started) * 1000, 1),
    }
    if extras:
        payload.update(extras)
    return payload


async def _replay_frames(body: dict, *, agent_id: str, message: str, intent: dict,
                         user_id: str, trace_id: str, started: float,
                         state: Optional[dict] = None,
                         status: str = "completed") -> AsyncIterator[str]:
    """把一次性回复按 SSE 帧回放——与 POST /chat 共用同一批装配函数，语义零漂移。"""
    for step in body.get("thinkingProcess") or []:
        yield _sse("thinking", {"step": str(step)})
    content = body.get("content") or ""
    if content:
        yield _sse("token", {"text": content})
    data_card = body.get("dataCard")
    if data_card:
        yield _sse("dataCard", data_card)
    if state is not None:
        state["chars"] = len(content)
    extras = {k: body[k] for k in _BODY_EXTRAS if body.get(k) is not None}
    yield _sse("done", _done_payload(trace_id=trace_id, intent=intent, status=status,
                                     started=started, degraded=bool(body.get("degraded")),
                                     extras={"chars": len(content), **extras}))
    await _stream_audit(user_id=user_id, agent_id=agent_id, message=message,
                        intent=intent, trace_id=trace_id, status=status,
                        chars=len(content), started=started, extras=extras)


async def _stream_data_card(agent_id: str, intent: dict,
                            current_user: Optional[dict]) -> Optional[dict]:
    """流式尾帧的结构化卡片：数据与降级骨架同源（监理 Agent 本体，经治理面）。"""
    if agent_id != "project-supervisor-agent" or intent.get("intent") not in _QUERY_TOOLS:
        return None
    body = await _supervisor_realtime_body(intent, current_user)
    return (body or {}).get("dataCard")


# 首分片是流式唯一可安全重试的位置：chat_async_stream 是异步生成器，限流判定、POST 与
# 状态码检查（429→LLMRateLimitError）都推迟到第一次 __anext__，此时正文一字未推给用户。
# 一旦 yield 过 delta 就绝不能再重试——同一段话会被播两遍。
_STREAM_FIRST_CHUNK_ATTEMPTS = 3
_STREAM_FIRST_CHUNK_BASE_DELAY = 0.4  # 秒
_STREAM_FIRST_CHUNK_MAX_DELAY = 0.8   # 秒，总加码 ≤1.2s：验收门槛是首 token 2s，等更多无意义


def _stream_retryable(exc: BaseException) -> bool:
    """只重试瞬态故障：限流/超时/连接失败，以及网关 5xx。
    解析类与 4xx 语义错误重试只会原地打转，不在此列。"""
    if isinstance(exc, LLMUnavailableError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response is not None and exc.response.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))


async def _open_llm_stream(client: LLMClient, messages: list[dict],
                           **stream_kwargs) -> AsyncIterator[str]:
    """打开模型流并取出首个分片；首分片之前按退避重试，之后原样透传分片。"""
    for attempt in range(1, _STREAM_FIRST_CHUNK_ATTEMPTS + 1):
        stream = client.chat_async_stream(messages, **stream_kwargs)
        try:
            first = await stream.__anext__()
        except StopAsyncIteration:
            # 模型空返回：没有正文可流，交给路由的一次性降级分支，重试不改变结果
            await stream.aclose()
            return
        except (asyncio.CancelledError, GeneratorExit):
            await stream.aclose()
            raise
        except Exception as e:  # noqa: BLE001
            await stream.aclose()
            if attempt >= _STREAM_FIRST_CHUNK_ATTEMPTS or not _stream_retryable(e):
                raise
            delay = min(_STREAM_FIRST_CHUNK_BASE_DELAY * (2 ** (attempt - 1)),
                        _STREAM_FIRST_CHUNK_MAX_DELAY)
            logger.warning("conversation.stream_first_chunk_retry",
                           attempt=attempt, delay_ms=round(delay * 1000),
                           error_type=type(e).__name__, error=str(e)[:200])
            await asyncio.sleep(delay)
            continue

        try:
            yield first
            async for delta in stream:
                yield delta
        finally:
            try:
                await stream.aclose()
            except Exception:  # noqa: BLE001 — 收尾失败不该盖掉正文流的结论
                pass
        return


async def _stream_body(*, agent_id: str, message: str, current_user: Optional[dict],
                       trace_id: str, state: dict) -> AsyncIterator[str]:
    """流式主体：thinking → token* → dataCard → done，护栏命中以 retract 终止。"""
    user_id = state["user_id"]
    started = state["started"]

    agent_config = next((a for a in CHAT_AGENTS if a["id"] == agent_id), None)
    pre = _precheck_agent(agent_id, agent_config)
    if pre is not None:
        intent = {"intent": "", "classifier": ""}
        state["intent"] = intent
        async for frame in _replay_frames(pre, agent_id=agent_id, message=message,
                                          intent=intent, user_id=user_id,
                                          trace_id=trace_id, started=started,
                                          state=state):
            yield frame
        return

    intent = await classify_intent(message)
    state["intent"] = intent
    yield _sse("thinking", {"step": f"意图识别：{intent['intent']}"
                                   f"（分类器 {intent['classifier']}）"})

    # 创作类的产物是可下载的 .docx，分片拼不出文件——一次性回放同一份装配结果
    if intent["intent"] == INTENT_CREATIVE:
        body = await _handle_creative(agent_id, message, agent_config, intent, current_user)
        async for frame in _replay_frames(body, agent_id=agent_id, message=message,
                                          intent=intent, user_id=user_id,
                                          trace_id=trace_id, started=started, state=state):
            yield frame
        return

    client = _get_llm_client() if settings.llm_enabled else None
    base_prompt = AGENT_SYSTEM_PROMPTS.get(agent_id, "")
    if client is None or not base_prompt:
        body = await _degraded_response(agent_id, message, agent_config, intent, current_user)
        async for frame in _replay_frames(body, agent_id=agent_id, message=message,
                                          intent=intent, user_id=user_id,
                                          trace_id=trace_id, started=started,
                                          state=state, status="degraded"):
            yield frame
        return

    business_context = await _collect_business_context(agent_id, current_user)
    yield _sse("thinking", {"step": "经治理面采集实时业务上下文"})
    messages = [
        {"role": "system", "content": base_prompt + _STREAM_PLAIN_TEXT_DIRECTIVE},
        {"role": "user",
         "content": _chat_user_content(business_context, message, intent, structured=False)},
    ]
    yield _sse("thinking", {"step": "模型流式生成中（首 token 即正文起点）"})

    parts: list[str] = []
    reviewed_upto = 0
    first_token_ms: Optional[float] = None
    retraction: Optional[dict] = None
    failure: Optional[str] = None
    stream = _open_llm_stream(
        client,
        messages,
        temperature=0.7,
        max_tokens=4096,
        extra=NO_THINK_EXTRA,
        scene="conversation_stream",
        cost_domain=_cost_domain_for(agent_id),
    )
    try:
        async for delta in stream:
            if first_token_ms is None:
                first_token_ms = (time.monotonic() - started) * 1000
            parts.append(delta)
            text = "".join(parts)
            state["chars"] = len(text)
            yield _sse("token", {"text": delta})
            if len(text) - reviewed_upto >= _STREAM_GUARD_WINDOW:
                # 窗口起点回退 overlap，跨分片拼接的模式只在交界处才完整
                start = max(0, reviewed_upto - _STREAM_GUARD_OVERLAP)
                reviewed_upto = len(text)
                retraction = _stream_guard_hit(text[start:reviewed_upto])
                if retraction:
                    break
        if retraction is None and parts:
            retraction = _stream_guard_hit("".join(parts))
    except Exception as e:  # noqa: BLE001 — 流式异常改不了状态码，只能用终止帧告知
        failure = f"{type(e).__name__}: {e}"
        logger.warning("conversation.stream_llm_failed",
                       agent_id=agent_id, error=failure, intent=intent["intent"])
    await stream.aclose()

    text = "".join(parts)
    if retraction is not None:
        # 已推送的分片收不回来，由前端截断为终止帧文案；服务端留痕（裁决 D-D 第 2 条）
        yield _sse("retract", {
            "notice": _STREAM_RETRACT_NOTICE,
            "reason": str(retraction.get("type") or "guardrail_block"),
            "pattern": str(retraction.get("pattern") or "")[:200],
            "charsStreamed": len(text),
        })
        await _stream_audit(user_id=user_id, agent_id=agent_id, message=message,
                            intent=intent, trace_id=trace_id, status="retracted",
                            chars=len(text), started=started,
                            reason=f"{retraction.get('type')}@{retraction.get('pattern')}")
        return

    if failure is not None:
        yield _sse("error", {"reason": "stream_failed", "detail": failure[:300],
                             "partialChars": len(text), "fallback": "one_shot"})
        await _stream_audit(user_id=user_id, agent_id=agent_id, message=message,
                            intent=intent, trace_id=trace_id, status="error",
                            chars=len(text), started=started, reason=failure)
        return

    if not text:
        # 模型空返回：正文一字未发，可安全改走一次性降级（不留空白气泡）
        body = await _degraded_response(agent_id, message, agent_config, intent, current_user)
        async for frame in _replay_frames(body, agent_id=agent_id, message=message,
                                          intent=intent, user_id=user_id,
                                          trace_id=trace_id, started=started,
                                          state=state, status="degraded"):
            yield frame
        return

    data_card = await _stream_data_card(agent_id, intent, current_user)
    if data_card:
        yield _sse("dataCard", data_card)
    extras = {"chars": len(text), "hasDataCard": data_card is not None}
    if first_token_ms is not None:
        extras["firstTokenMs"] = round(first_token_ms, 1)
    yield _sse("done", _done_payload(trace_id=trace_id, intent=intent,
                                     status="completed", started=started, extras=extras))
    logger.info("conversation.stream_completed", agent_id=agent_id,
                intent=intent["intent"], chars=len(text),
                first_token_ms=round(first_token_ms or -1, 1),
                has_data_card=data_card is not None)
    await _stream_audit(user_id=user_id, agent_id=agent_id, message=message,
                        intent=intent, trace_id=trace_id, status="completed",
                        chars=len(text), started=started)


async def _stream_frames(*, agent_id: str, message: str, current_user: Optional[dict],
                         trace_id: str) -> AsyncIterator[str]:
    """SSE 帧总入口：断线也留痕，未知异常转终止帧（响应头已发出，改不了状态码）。"""
    state = {
        "user_id": str((current_user or {}).get("user_id") or "conversation"),
        "started": time.monotonic(),
        "chars": 0,
        "intent": {},
    }
    try:
        async for frame in _stream_body(agent_id=agent_id, message=message,
                                        current_user=current_user, trace_id=trace_id,
                                        state=state):
            yield frame
    except (asyncio.CancelledError, GeneratorExit):
        _stream_audit_soon(user_id=state["user_id"], agent_id=agent_id, message=message,
                           intent=state["intent"], trace_id=trace_id, status="aborted",
                           chars=state["chars"], started=state["started"])
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("conversation.stream_unexpected", agent_id=agent_id,
                     error=str(e), error_type=type(e).__name__)
        yield _sse("error", {"reason": "stream_unexpected", "detail": str(e)[:300],
                             "fallback": "one_shot"})
        await _stream_audit(user_id=state["user_id"], agent_id=agent_id, message=message,
                            intent=state["intent"], trace_id=trace_id, status="error",
                            chars=state["chars"], started=state["started"],
                            reason=f"{type(e).__name__}: {e}")


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    """
    对话式交互 —— SSE 流式返回（工作包三 / 裁决 D-D）

    帧协议（``event: <名称>`` + 单行 ``data: <json>``）：
    - ``thinking`` ``{"step": "..."}``  流水线真实步骤，逐条到达
    - ``token`` ``{"text": "..."}``     正文增量，首个即首 token
    - ``dataCard`` 结构化卡片（尾帧），数据取自监理 Agent 本体（治理面只读工具）
    - ``retract`` ``{"notice": "[内容已撤回]", ...}``  护栏复核命中，终止帧
    - ``error`` ``{"reason": ..., "fallback": "one_shot"}``  流异常，前端一次性重答
    - ``done`` ``{"traceId", "intent", "classifier", "degraded", "elapsedMs", ...}``

    与 ``POST /chat`` 共用意图分类、上下文采集、降级分组与创作装配，只在返回形态上
    分叉；一次调用一条审计（成功/降级/撤回/异常/断线都落）。
    """
    from app.core.observability.trace import get_trace_id

    # trace_id 必须在返回 StreamingResponse 之前取：RequestContextMiddleware 是
    # BaseHTTPMiddleware，响应体生成阶段它的 contextvars 作用域已经退出。
    trace_id = get_trace_id()
    return StreamingResponse(
        _stream_frames(agent_id=req.agent_id, message=req.message.strip(),
                       current_user=current_user, trace_id=trace_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
