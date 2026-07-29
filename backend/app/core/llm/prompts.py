"""
Prompt模板管理
Agent决策prompt模板系统，基于Jinja2渲染

提供以下模板：
- 库存补货决策prompt：输入库存状态、历史数据、预测结果，输出补货建议(JSON)
- 凭证生成prompt：输入业务事件描述，输出会计分录建议(JSON)
- 通用Agent推理prompt：输入上下文，输出决策建议

所有模板均输出包含confidence字段的JSON，便于决策引擎统一处理。
"""
import json
from typing import Any, Optional

import structlog
from jinja2 import Environment, StrictUndefined, Template

logger = structlog.get_logger()


# ========== 内置Prompt模板 ==========

# 系统提示：定义LLM的角色与输出规范
SYSTEM_PROMPT_BASE = """你是一个运行在企业ERP系统中的智能Agent，负责辅助业务决策。
你必须严格遵守以下规则：
1. 所有响应必须是合法JSON格式
2. 必须包含 "confidence" 字段（0-1之间的浮点数），表示你对决策的置信度
3. 决策必须基于输入的上下文数据，不得臆造数据
4. 如数据不足以做出决策，confidence应低于0.5
5. 不得输出任何敏感数据（密码、密钥等）
6. 不得执行任何越权操作"""

# 库存补货决策prompt模板
INVENTORY_REPLENISHMENT_TEMPLATE = """你是库存守护Agent，需要根据库存状态生成补货建议。

## 当前库存状态
- 产品ID: {{ product_id }}
- 产品名称: {{ product_name }}
- 当前库存: {{ current_qty }}
- 安全库存: {{ safety_stock }}
- 再订货点: {{ reorder_point }}
- 单位成本: {{ unit_cost }} 元
- 仓库: {{ warehouse }}

## 历史与预测数据
{% if history %}
历史销售/消耗数据（最近若干天）:
{{ history | tojson }}
{% else %}
（无历史数据可用）
{% endif %}

{% if forecast %}
基础预测结果（规则引擎）:
{{ forecast | tojson }}
{% endif %}

{% if external_factors %}
外部因素（季节性、促销、市场趋势等）:
{{ external_factors | tojson }}
{% endif %}

## 你的任务
分析上述数据，综合考虑季节性、促销活动、市场趋势等因素，给出最优补货建议。

## 输出要求
返回JSON，包含以下字段：
{
  "predicted_demand": <未来7天预测需求量, 整数>,
  "suggested_qty": <建议补货数量, 整数, 应 >= predicted_demand - 当前库存>,
  "confidence": <置信度, 0-1>,
  "reasoning": "<决策理由, 简短描述>",
  "factors": ["<考虑的关键因素1>", "<因素2>", ...],
  "time_horizon": "7_days",
  "risk_warning": "<可选, 风险提示>"
}"""

# 凭证生成prompt模板
VOUCHER_GENERATION_TEMPLATE = """你是记账Agent，需要根据业务事件生成会计分录建议。

## 业务事件描述
- 事件类型: {{ event_type }}
- 事件描述: {{ event_description | default("无") }}
- 发生时间: {{ event_time | default("未知") }}

## 事件数据
{{ event_data | tojson }}

## 会计准则
{{ standard | default("CAS") }}（CAS=中国企业会计准则, IFRS=国际财务报告准则, US_GAAP=美国通用会计准则）

## 常用会计科目参考
- 1001 库存现金
- 1002 银行存款
- 1122 应收账款
- 1403 原材料
- 1405 库存商品
- 2202 应付账款
- 5001 生产成本
- 6001 主营业务收入
- 6601 销售费用
- 6602 管理费用

## 你的任务
1. 分析业务事件的经济实质
2. 推荐合适的会计科目
3. 生成借贷平衡的会计分录
4. 生成规范的凭证摘要

## 输出要求
返回JSON，包含以下字段：
{
  "voucher_type": "<凭证类型: 收/付/转>",
  "summary": "<凭证摘要, 简洁规范>",
  "entries": [
    {
      "account_code": "<科目代码>",
      "account_name": "<科目名称>",
      "debit": <借方金额, 数字>,
      "credit": <贷方金额, 数字>,
      "summary": "<分录摘要>"
    }
  ],
  "confidence": <置信度, 0-1>,
  "reasoning": "<决策理由>",
  "standard": "{{ standard | default('CAS') }}"
}

注意：所有分录的借方合计必须等于贷方合计。"""

# 凭证摘要生成prompt模板
VOUCHER_SUMMARY_TEMPLATE = """你是记账Agent，需要为会计凭证生成规范的摘要。

## 业务事件
- 事件类型: {{ event_type }}
- 事件数据: {{ event_data | tojson }}

## 你的任务
生成一句简洁、规范的会计凭证摘要，长度不超过30个汉字。
摘要应体现业务实质，如"采购原材料入库"、"销售商品收款"等。

## 输出要求
返回JSON：
{
  "summary": "<凭证摘要>",
  "confidence": <置信度, 0-1>
}"""

# 通用Agent推理prompt模板
GENERAL_REASONING_TEMPLATE = """你是{{ agent_name | default("企业ERP智能Agent") }}，需要根据上下文做出决策。

## Agent信息
- 名称: {{ agent_name | default("通用Agent") }}
- 域: {{ agent_domain | default("general") }}
- 能力: {{ agent_capabilities | default([]) | tojson }}

## 当前上下文
{{ context | tojson }}

## 任务描述
{{ task | default("请根据上下文给出决策建议") }}

## 你的任务
分析上下文，给出合理的决策建议。如上下文不足以决策，应明确指出缺失信息。

## 输出要求
返回JSON，包含以下字段：
{
  "decision": "<决策建议>",
  "action": "<具体动作>",
  "confidence": <置信度, 0-1>,
  "reasoning": "<决策理由>",
  "requires_human_approval": <是否需要人工审批, true/false>,
  "alternatives": [
    {"action": "<备选方案>", "pros": "<优点>", "cons": "<缺点>"}
  ]
}"""

# 需求预测分析prompt模板（库存Agent子任务）
DEMAND_FORECAST_TEMPLATE = """你是库存需求预测专家，需要分析历史趋势和季节性因素，预测未来需求。

## 产品信息
- 产品ID: {{ product_id }}
- 产品名称: {{ product_name }}
- 当前库存: {{ current_qty }}
- 安全库存: {{ safety_stock }}
- 再订货点: {{ reorder_point }}

## 历史数据
{% if history %}
{{ history | tojson }}
{% else %}
（无历史数据，请基于安全库存和再订货点保守估计）
{% endif %}

{% if seasonal_factors %}
季节性因素:
{{ seasonal_factors | tojson }}
{% endif %}

## 你的任务
分析历史趋势、季节性波动、可能的促销活动等，预测未来7天的需求量。

## 输出要求
返回JSON：
{
  "predicted_demand": <未来7天预测需求, 整数>,
  "suggested_qty": <建议补货量, 整数>,
  "confidence": <置信度, 0-1>,
  "factors": ["<考虑的关键因素>"],
  "trend": "<上升/平稳/下降>",
  "seasonal_note": "<季节性说明>",
  "time_horizon": "7_days"
}"""


# 采购决策prompt模板（供应商选择、价格谈判、交期评估）
PURCHASE_DECISION_TEMPLATE = """你是采购决策专家，需要综合评估供应商、价格与交期，给出最优采购建议。

## 采购需求
- 产品ID: {{ product_id }}
- 产品名称: {{ product_name }}
- 需求数量: {{ required_qty }}
- 期望交期: {{ expected_delivery_days | default(7) }} 天
- 预算上限: {{ budget_limit | default("无限制") }} 元

## 候选供应商
{% if suppliers is defined and suppliers %}
{{ suppliers | tojson }}
{% else %}
（无候选供应商数据）
{% endif %}

## 历史采购记录
{% if purchase_history is defined and purchase_history %}
{{ purchase_history | tojson }}
{% else %}
（无历史采购记录）
{% endif %}

## 市场行情
{% if market_info is defined and market_info %}
{{ market_info | tojson }}
{% endif %}

## 你的任务
1. 评估每个供应商的资质、价格竞争力、交期可靠性、历史合作表现
2. 综合考虑价格、质量、交期、付款条件等因素
3. 给出最优供应商选择及谈判建议
4. 评估采购风险并提出缓解措施

## 评估维度
- 价格竞争力（权重30%）：单价、批量折扣、总成本
- 交期可靠性（权重25%）：承诺交期、历史准时率
- 质量水平（权重25%）：合格率、退货率、质量认证
- 合作信誉（权重20%）：合作年限、履约记录、财务状况

## 输出要求
返回JSON：
{
  "recommended_supplier": "<推荐供应商ID>",
  "recommended_supplier_name": "<推荐供应商名称>",
  "negotiated_price": <建议成交单价, 数字>,
  "total_cost": <总成本, 数字>,
  "expected_delivery_date": "<预计交货日期>",
  "confidence": <置信度, 0-1>,
  "reasoning": "<决策理由>",
  "factors": ["<关键考量因素>"],
  "negotiation_tips": ["<价格谈判建议>"],
  "risk_assessment": {
    "level": "<低/中/高>",
    "items": ["<风险点>"],
    "mitigations": ["<缓解措施>"]
  },
  "alternatives": [
    {"supplier_id": "<备选供应商ID>", "reason": "<选择理由>"}
  ]
}"""


# 风险评估prompt模板（交易风险分析、异常识别）
RISK_ASSESSMENT_TEMPLATE = """你是企业风控专家，需要分析业务交易的风险并识别异常模式。

## 待评估交易/事件
- 事件类型: {{ event_type }}
- 事件描述: {{ event_description | default("无") }}
- 发生时间: {{ event_time | default("未知") }}

## 交易数据
{{ transaction_data | tojson }}

## 历史基线数据
{% if baseline_data is defined and baseline_data %}
{{ baseline_data | tojson }}
{% else %}
（无历史基线数据，请基于通用风控规则评估）
{% endif %}

## 风控规则参考
{% if risk_rules is defined and risk_rules %}
{{ risk_rules | tojson }}
{% endif %}

## 你的任务
1. 分析交易是否存在异常模式（金额异常、频率异常、对象异常等）
2. 评估交易风险等级（低/中/高/极高）
3. 识别潜在欺诈、合规违规或操作风险
4. 给出风险处置建议

## 异常识别维度
- 金额异常：交易金额偏离历史均值2个标准差以上
- 频率异常：同一对象短时间高频交易
- 时间异常：非工作时间、节假日异常交易
- 对象异常：新供应商/客户大额交易、关联方交易
- 合规异常：缺少必要审批、越权操作、拆分交易规避审批

## 输出要求
返回JSON：
{
  "risk_level": "<低/中/高/极高>",
  "risk_score": <风险评分, 0-100>,
  "confidence": <置信度, 0-1>,
  "anomalies": [
    {
      "type": "<异常类型: amount/frequency/time/party/compliance>",
      "severity": "<低/中/高>",
      "description": "<异常描述>",
      "evidence": "<证据数据>"
    }
  ],
  "risk_factors": ["<风险因素>"],
  "reasoning": "<风险评估理由>",
  "recommendations": ["<处置建议>"],
  "requires_human_review": <是否需要人工复核, true/false>,
  "requires_block": <是否需要阻断交易, true/false>
}"""


class PromptManager:
    """
    Prompt模板管理器

    特性：
    - 基于Jinja2模板渲染，支持变量插值
    - 内置Agent决策场景模板（库存补货、凭证生成、通用推理）
    - 支持注册自定义模板
    - 严格模式：缺少变量时抛出异常，避免空值进入LLM
    """

    # 模板名称常量
    INVENTORY_REPLENISHMENT = "inventory_replenishment"
    VOUCHER_GENERATION = "voucher_generation"
    VOUCHER_SUMMARY = "voucher_summary"
    GENERAL_REASONING = "general_reasoning"
    DEMAND_FORECAST = "demand_forecast"
    PURCHASE_DECISION = "purchase_decision"
    RISK_ASSESSMENT = "risk_assessment"

    def __init__(self):
        # 使用严格模式：缺少变量时报错，避免静默生成空内容
        self._env = Environment(
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )
        # 注册内置模板
        self._templates: dict[str, Template] = {
            self.INVENTORY_REPLENISHMENT: self._env.from_string(
                INVENTORY_REPLENISHMENT_TEMPLATE
            ),
            self.VOUCHER_GENERATION: self._env.from_string(
                VOUCHER_GENERATION_TEMPLATE
            ),
            self.VOUCHER_SUMMARY: self._env.from_string(
                VOUCHER_SUMMARY_TEMPLATE
            ),
            self.GENERAL_REASONING: self._env.from_string(
                GENERAL_REASONING_TEMPLATE
            ),
            self.DEMAND_FORECAST: self._env.from_string(
                DEMAND_FORECAST_TEMPLATE
            ),
            self.PURCHASE_DECISION: self._env.from_string(
                PURCHASE_DECISION_TEMPLATE
            ),
            self.RISK_ASSESSMENT: self._env.from_string(
                RISK_ASSESSMENT_TEMPLATE
            ),
        }
        # 系统提示（与模板名称关联）
        self._system_prompts: dict[str, str] = {
            self.INVENTORY_REPLENISHMENT: SYSTEM_PROMPT_BASE
            + "\n\n当前任务：库存补货决策。",
            self.VOUCHER_GENERATION: SYSTEM_PROMPT_BASE
            + "\n\n当前任务：会计凭证生成。",
            self.VOUCHER_SUMMARY: SYSTEM_PROMPT_BASE
            + "\n\n当前任务：凭证摘要生成。",
            self.GENERAL_REASONING: SYSTEM_PROMPT_BASE
            + "\n\n当前任务：通用业务决策。",
            self.DEMAND_FORECAST: SYSTEM_PROMPT_BASE
            + "\n\n当前任务：需求预测分析。",
            self.PURCHASE_DECISION: SYSTEM_PROMPT_BASE
            + "\n\n当前任务：采购决策分析（供应商选择、价格谈判、交期评估）。",
            self.RISK_ASSESSMENT: SYSTEM_PROMPT_BASE
            + "\n\n当前任务：风险评估与异常识别。",
        }

        logger.info("prompt_manager.initialized", templates=list(self._templates.keys()))

    def render(self, template_name: str, variables: dict[str, Any]) -> str:
        """
        渲染指定模板

        参数：
        - template_name: 模板名称（使用类常量）
        - variables: 模板变量字典

        返回：渲染后的prompt字符串
        """
        template = self._templates.get(template_name)
        if template is None:
            raise KeyError(f"未知的prompt模板: {template_name}")
        try:
            return template.render(**variables)
        except Exception as e:
            logger.error(
                "prompt_manager.render_failed",
                template=template_name,
                error=str(e),
            )
            raise

    def render_system(self, template_name: str) -> str:
        """获取模板对应的系统提示"""
        return self._system_prompts.get(template_name, SYSTEM_PROMPT_BASE)

    def render_full(
        self, template_name: str, variables: dict[str, Any]
    ) -> tuple[str, str]:
        """
        渲染完整对话（系统提示 + 用户prompt）

        返回：(system_prompt, user_prompt)
        """
        system = self.render_system(template_name)
        user = self.render(template_name, variables)
        return system, user

    def register_template(self, name: str, template_str: str,
                          system_prompt: Optional[str] = None) -> None:
        """
        注册自定义模板

        参数：
        - name: 模板名称
        - template_str: Jinja2模板字符串
        - system_prompt: 可选系统提示
        """
        self._templates[name] = self._env.from_string(template_str)
        if system_prompt:
            self._system_prompts[name] = system_prompt
        logger.info("prompt_manager.template_registered", name=name)

    def list_templates(self) -> list[str]:
        """列出所有已注册模板"""
        return list(self._templates.keys())

    def has_template(self, name: str) -> bool:
        """判断模板是否存在"""
        return name in self._templates


# 全局Prompt管理器单例
prompt_manager = PromptManager()
