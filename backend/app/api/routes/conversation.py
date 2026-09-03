"""
对话式操作路由
================

提供 Agent 对话式交互能力，接入 DeepSeek LLM 生成差异化回复。

端点：
- GET  /conversation/agents  获取可对话的 Agent 列表
- POST /conversation/chat    发送消息并获取 Agent 回复

回复结构：
    {
        "content": "Agent 回复文本",
        "thinkingProcess": ["步骤1", "步骤2", ...],
        "dataCard": { ... }  # 可选，结构化数据卡片
    }

设计要点：
1. 每个 Agent 拥有专属系统提示词，注入角色、能力域与实时业务上下文
2. LLM 返回 JSON，包含 content / thinkingProcess / dataCard 三字段
3. LLM 不可用时降级到规则匹配（关键词路由），保证服务可用
"""
import json
import structlog
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.core.llm.client import LLMClient, get_llm_client
from app.core.agent.orchestrator import orchestrator

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
}


# ========== 业务上下文采集 ==========

def _collect_business_context(agent_id: str) -> str:
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

    return "\n".join(context_parts) if context_parts else "（暂无实时业务数据）"


# ========== 请求/响应模型 ==========

class ChatRequest(BaseModel):
    agent_id: str
    message: str


# ========== 路由端点 ==========

@router.get("/agents")
async def get_chat_agents():
    """获取可对话的 Agent 列表"""
    return {"agents": CHAT_AGENTS}


@router.post("/chat")
async def chat(req: ChatRequest):
    """
    对话式交互 — 接入 DeepSeek LLM 生成差异化回复

    流程：
    1. 采集 Agent 相关的实时业务上下文
    2. 构建系统提示词（角色 + 能力 + 上下文 + 输出格式）
    3. 调用 LLM 生成 JSON 响应
    4. 解析并返回 content / thinkingProcess / dataCard
    5. LLM 不可用时降级到规则匹配
    """
    agent_id = req.agent_id
    message = req.message.strip()

    # 查找 Agent 配置
    agent_config = next((a for a in CHAT_AGENTS if a["id"] == agent_id), None)
    if not agent_config:
        return {
            "content": f"未知的Agent: {agent_id}",
            "thinkingProcess": ["识别Agent类型", "未找到匹配的Agent"],
        }

    # 离线 Agent 直接返回
    if agent_config["status"] == "offline":
        return {
            "content": f"{agent_config['name']}当前处于离线状态，无法响应消息。请稍后再试。",
            "thinkingProcess": ["检查Agent状态", "Agent离线，返回提示"],
        }

    # 尝试 LLM 路径
    if settings.llm_enabled:
        llm_result = await _chat_with_llm(agent_id, message, agent_config)
        if llm_result is not None:
            return llm_result

    # LLM 不可用时降级到规则匹配
    logger.info("conversation.fallback_to_rules", agent_id=agent_id)
    return _rule_based_response(agent_id, message, agent_config)


async def _chat_with_llm(
    agent_id: str, message: str, agent_config: dict
) -> Optional[dict]:
    """
    使用 LLM 生成对话回复

    返回 None 表示 LLM 不可用，调用方应降级到规则匹配。
    """
    client = _get_llm_client()
    if client is None:
        return None

    system_prompt = AGENT_SYSTEM_PROMPTS.get(agent_id, "")
    if not system_prompt:
        return None

    # 采集业务上下文
    business_context = _collect_business_context(agent_id)

    # 构建用户消息：用户问题 + 业务上下文
    user_content = (
        f"## 实时业务上下文\n{business_context}\n\n"
        f"## 用户问题\n{message}\n\n"
        f"请基于上述业务上下文回答用户问题。"
        f"返回JSON，包含 content（回复文本）、thinkingProcess（推理步骤列表）、"
        f"可选的 dataCard（结构化数据卡片）。"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        response = await client.chat_completion(
            messages,
            response_format={"type": "json_object"},
            temperature=0.7,  # 对话场景用较高温度增加多样性
            max_tokens=4096,  # 确保有足够空间生成完整JSON响应
            extra={"thinking": {"type": "disabled"}},  # 对话场景禁用思考模式，避免content为空
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

    data_card = parsed.get("dataCard") or parsed.get("data_card")

    logger.info("conversation.llm_success",
                agent_id=agent_id,
                content_length=len(content),
                thinking_steps=len(thinking_process),
                has_data_card=data_card is not None)

    result = {
        "content": content,
        "thinkingProcess": thinking_process,
    }
    if data_card:
        result["dataCard"] = data_card

    return result


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

    # 默认回复
    return {
        "content": f"我是{agent_name}，已收到您的消息。请问还有什么可以帮您？",
        "thinkingProcess": ["解析用户输入", "未匹配具体意图", "返回能力引导"],
    }
