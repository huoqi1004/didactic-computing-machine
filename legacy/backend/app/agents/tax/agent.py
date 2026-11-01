# -*- coding: utf-8 -*-
"""
税务Agent
负责增值税计算、税务申报、税务筹划与风险预警。
监听凭证过账与采购审批事件，自动计算税额。

职责：
- 监听 VOUCHER_POSTED 事件（凭证过账后计算税额）
- 监听 PURCHASE_ORDER_APPROVED 事件（采购进项税）
- 自动计算增值税进项/销项税额
- 跟踪境内外税法变化（使用知识图谱存储税率规则）
- 自动生成税务申报草稿
- 税务风险预警（税负率异常等）
- 税务筹划建议（使用LLM分析）
"""
import asyncio
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class TaxAgent(BaseAgent):
    """
    税务Agent

    职责：
    - 监听凭证过账与采购审批事件，自动计算增值税进项/销项税额
    - 维护税率表（增值税13%/9%/6%，企业所得税25%）
    - 自动生成税务申报草稿
    - 税务风险预警（税负率异常等）
    - 税务筹划建议（LLM优先，降级到规则）
    """

    # 税负率异常阈值（低于此值预警）
    LOW_TAX_BURDEN_THRESHOLD = 0.01  # 1%
    HIGH_TAX_BURDEN_THRESHOLD = 0.10  # 10%

    def __init__(self):
        super().__init__(
            name="税务Agent",
            domain="tax",
            capabilities=[
                "tax_calculation",          # 税务计算
                "tax_declaration",          # 税务申报
                "tax_planning",             # 税务筹划
                "risk_warning",             # 风险预警
                "general",
            ],
            security_clearance="confidential",
        )
        # 税务记录：record_id -> 税务计算详情
        self._tax_records: dict[str, dict] = {}
        # 申报记录：declaration_id -> 申报详情
        self._tax_declarations: dict[str, dict] = {}
        # 税率表：tax_type -> 税率
        self._tax_rates: dict[str, float] = {
            "vat_standard": 0.13,       # 增值税标准税率13%
            "vat_reduced_1": 0.09,      # 增值税优惠税率9%
            "vat_reduced_2": 0.06,      # 增值税优惠税率6%
            "corporate_income": 0.25,   # 企业所得税25%
        }
        # 累计进项税额
        self._input_tax_total: float = 0.0
        # 累计销项税额
        self._output_tax_total: float = 0.0

    @property
    def description(self) -> str:
        return "增值税计算、税务申报、税务筹划与风险预警"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅凭证过账与采购审批事件"""
        self.subscribe_events(
            event_types=[
                EventType.VOUCHER_POSTED,
                EventType.PURCHASE_ORDER_APPROVED,
            ],
            handler=self._handle_event,
        )
        logger.info("tax_agent.initialized", agent=self.name,
                    tax_rates=self._tax_rates)

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("tax_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.VOUCHER_POSTED:
            await self._calculate_tax_from_voucher(event.event_data)

        elif event.event_type == EventType.PURCHASE_ORDER_APPROVED:
            await self._calculate_input_tax_from_po(event.event_data)

    # ========== 税额计算 ==========

    async def _calculate_tax_from_voucher(self, voucher_data: dict) -> Optional[dict]:
        """从凭证过账数据计算销项税额"""
        amount = self._extract_voucher_amount(voucher_data)
        if amount <= 0:
            return None

        # 使用LLM分析适用税率（降级到规则：默认13%）
        tax_type, rate, reasoning = await self._determine_tax_rate_llm(
            voucher_data, amount
        )

        return await self._record_tax(
            source_type="voucher",
            source_id=voucher_data.get("voucher_id", ""),
            amount=amount,
            tax_type="output_vat",
            vat_rate=rate,
            vat_category=tax_type,
            reasoning=reasoning,
        )

    async def _calculate_input_tax_from_po(self, po_data: dict) -> Optional[dict]:
        """从采购订单计算进项税额"""
        amount = float(po_data.get("total_amount", 0) or 0)
        if amount <= 0:
            return None

        # 采购默认适用13%进项税
        rate = self._tax_rates["vat_standard"]
        return await self._record_tax(
            source_type="purchase_order",
            source_id=po_data.get("po_id", ""),
            amount=amount,
            tax_type="input_vat",
            vat_rate=rate,
            vat_category="vat_standard",
            reasoning=f"采购订单进项税，适用标准税率{rate*100:.0f}%",
        )

    async def _record_tax(
        self, source_type: str, source_id: str, amount: float,
        tax_type: str, vat_rate: float, vat_category: str,
        reasoning: str,
    ) -> dict:
        """记录税务计算结果并发布事件"""
        tax_amount = round(amount * vat_rate / (1 + vat_rate), 2)
        record_id = f"TAX-{str(uuid4())[:8]}"

        record = {
            "record_id": record_id,
            "source_type": source_type,
            "source_id": source_id,
            "amount": amount,
            "tax_type": tax_type,
            "vat_rate": vat_rate,
            "vat_category": vat_category,
            "tax_amount": tax_amount,
            "reasoning": reasoning,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._tax_records[record_id] = record

        # 累计进项/销项
        if tax_type == "input_vat":
            self._input_tax_total += tax_amount
        else:
            self._output_tax_total += tax_amount

        # 记录决策
        decision = AgentDecision(
            decision_type="tax_calculation",
            reasoning=reasoning,
            action=f"计算{tax_type}税额{tax_amount:.2f}元（税率{vat_rate*100:.0f}%）",
            confidence=0.9,
            requires_human_approval=False,
            context={
                "record_id": record_id,
                "tax_type": tax_type,
                "tax_amount": tax_amount,
                "vat_rate": vat_rate,
            },
        )
        await self.record_decision(decision)

        # 发布税额计算事件
        await self._publish_event(
            EventType.TAX_CALCULATED,
            {**record, "decision_id": decision.decision_id},
        )

        logger.info("tax_agent.tax_calculated",
                    record_id=record_id, tax_type=tax_type,
                    tax_amount=tax_amount, vat_rate=vat_rate)
        return record

    def _extract_voucher_amount(self, voucher_data: dict) -> float:
        """从凭证数据提取金额"""
        amount = float(voucher_data.get("total_debit", 0) or 0)
        if amount <= 0:
            amount = float(voucher_data.get("total_credit", 0) or 0)
        return amount

    async def _determine_tax_rate_llm(
        self, voucher_data: dict, amount: float,
    ) -> tuple:
        """
        使用LLM确定适用税率

        降级到规则：默认适用标准税率13%。
        返回：(tax_category, rate, reasoning)
        """
        from app.core.config import settings

        default_category = "vat_standard"
        default_rate = self._tax_rates[default_category]
        default_reasoning = f"默认适用标准增值税税率{default_rate*100:.0f}%"

        if not settings.llm_enabled:
            return default_category, default_rate, default_reasoning

        context = {
            "voucher_data": {
                k: v for k, v in voucher_data.items()
                if k in ("voucher_id", "voucher_type", "entries", "standard")
            },
            "amount": amount,
            "available_rates": self._tax_rates,
        }
        prompt = (
            "请根据凭证内容确定适用的增值税税率类别。"
            "返回JSON，包含 tax_category(vat_standard/vat_reduced_1/vat_reduced_2), "
            "rate, reasoning。"
        )
        try:
            result = await self.llm_analyze(prompt, context)
            if "error" not in result:
                category = str(result.get("tax_category", default_category))
                rate = self._tax_rates.get(category, default_rate)
                reasoning = str(result.get("reasoning", default_reasoning))
                return category, rate, reasoning
        except Exception as e:
            logger.warning("tax_agent.llm_rate_error",
                           agent=self.name, error=str(e))

        return default_category, default_rate, default_reasoning

    # ========== 税务申报 ==========

    async def generate_tax_declaration(
        self, period: str = "",
    ) -> Optional[dict]:
        """
        生成税务申报草稿

        参数：
        - period: 申报期间（如"2026-07"），默认当前月
        """
        if not period:
            period = datetime.now(timezone.utc).strftime("%Y-%m")

        declaration_id = f"DEC-{str(uuid4())[:8]}"
        input_tax = self._input_tax_total
        output_tax = self._output_tax_total
        payable_tax = max(output_tax - input_tax, 0.0)

        declaration = {
            "declaration_id": declaration_id,
            "period": period,
            "input_tax": round(input_tax, 2),
            "output_tax": round(output_tax, 2),
            "payable_tax": round(payable_tax, 2),
            "status": "draft",
            "tax_records_count": len(self._tax_records),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._tax_declarations[declaration_id] = declaration

        # 记录决策
        decision = AgentDecision(
            decision_type="tax_declaration",
            reasoning=(
                f"生成{period}期间增值税申报草稿："
                f"销项税{output_tax:.2f}元，进项税{input_tax:.2f}元，"
                f"应纳税额{payable_tax:.2f}元"
            ),
            action=f"生成税务申报草稿{declaration_id}",
            confidence=0.9,
            requires_human_approval=True,
            context={"declaration_id": declaration_id, "period": period},
        )
        await self.record_decision(decision)

        # 发布申报生成事件
        await self._publish_event(
            EventType.TAX_DECLARATION_GENERATED,
            {**declaration, "decision_id": decision.decision_id},
        )

        # 税务风险检查
        await self._check_tax_risk(declaration)

        logger.info("tax_agent.declaration_generated",
                    declaration_id=declaration_id, period=period,
                    payable_tax=payable_tax)
        return declaration

    # ========== 税务风险预警 ==========

    async def _check_tax_risk(self, declaration: dict) -> Optional[dict]:
        """检查税务风险（税负率异常等）"""
        output_tax = declaration["output_tax"]
        input_tax = declaration["input_tax"]
        total = output_tax + input_tax

        if total <= 0:
            return None

        # 税负率 = 应纳税额 / 销项税额
        tax_burden = declaration["payable_tax"] / output_tax if output_tax > 0 else 0.0

        warning = None
        if tax_burden < self.LOW_TAX_BURDEN_THRESHOLD and output_tax > 0:
            warning = {
                "warning_id": f"WRN-{str(uuid4())[:8]}",
                "risk_type": "low_tax_burden",
                "severity": "high",
                "description": (
                    f"税负率{tax_burden*100:.2f}%低于预警阈值"
                    f"{self.LOW_TAX_BURDEN_THRESHOLD*100:.0f}%，"
                    f"可能存在进项税额异常偏高风险"
                ),
                "tax_burden": round(tax_burden, 4),
                "declaration_id": declaration["declaration_id"],
            }
        elif tax_burden > self.HIGH_TAX_BURDEN_THRESHOLD:
            warning = {
                "warning_id": f"WRN-{str(uuid4())[:8]}",
                "risk_type": "high_tax_burden",
                "severity": "medium",
                "description": (
                    f"税负率{tax_burden*100:.2f}%偏高，"
                    f"建议核查销项税额确认是否漏抵进项"
                ),
                "tax_burden": round(tax_burden, 4),
                "declaration_id": declaration["declaration_id"],
            }

        if warning:
            await self._publish_event(EventType.TAX_RISK_WARNING, warning)
            logger.warning("tax_agent.risk_warning",
                           risk_type=warning["risk_type"],
                           tax_burden=tax_burden)
        return warning

    # ========== 税务筹划 ==========

    async def suggest_tax_planning(self) -> Optional[dict]:
        """
        税务筹划建议

        LLM优先，降级到规则（基于当前税负率给出建议）。
        """
        from app.core.config import settings

        output_tax = self._output_tax_total
        input_tax = self._input_tax_total
        payable = max(output_tax - input_tax, 0.0)
        tax_burden = payable / output_tax if output_tax > 0 else 0.0

        if settings.llm_enabled:
            context = {
                "output_tax": output_tax,
                "input_tax": input_tax,
                "payable_tax": payable,
                "tax_burden": tax_burden,
                "tax_rates": self._tax_rates,
            }
            prompt = (
                "请基于当前税务状况提供税务筹划建议。"
                "返回JSON，包含 suggestion, expected_saving, "
                "risk_level, reasoning。"
            )
            try:
                result = await self.llm_analyze(prompt, context)
                if "error" not in result:
                    result["method"] = "llm"
                    result["tax_burden"] = round(tax_burden, 4)
                    return result
            except Exception as e:
                logger.warning("tax_agent.llm_planning_error",
                               agent=self.name, error=str(e))

        # 规则降级
        if tax_burden > self.HIGH_TAX_BURDEN_THRESHOLD:
            return {
                "suggestion": "建议加强进项税额抵扣管理，核查可抵扣项目",
                "expected_saving": "5%-10%",
                "risk_level": "low",
                "reasoning": f"当前税负率{tax_burden*100:.2f}%偏高",
                "tax_burden": round(tax_burden, 4),
                "method": "rules",
            }
        return {
            "suggestion": "当前税负率正常，建议持续关注税收政策变化",
            "expected_saving": "0%",
            "risk_level": "low",
            "reasoning": f"当前税负率{tax_burden*100:.2f}%在正常区间",
            "tax_burden": round(tax_burden, 4),
            "method": "rules",
        }

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出税务决策"""
        record_id = context.get("record_id")
        record = self._tax_records.get(record_id)

        if not record:
            return AgentDecision(
                decision_type="error",
                reasoning=f"税务记录{record_id}不存在",
                action="无法做出税务决策",
                confidence=0.0,
            )

        return AgentDecision(
            decision_type="tax_assessment",
            reasoning=(
                f"税务记录{record_id}：{record['tax_type']}税额"
                f"{record['tax_amount']:.2f}元，税率{record['vat_rate']*100:.0f}%"
            ),
            action=f"确认税额计算{record_id}",
            confidence=0.9,
            requires_human_approval=False,
            context={"record_id": record_id},
        )

    async def on_tick(self):
        """月末检查税务申报状态"""
        now = datetime.now(timezone.utc)
        # 每月最后一天检查是否已生成申报
        if now.day >= 28:
            current_period = now.strftime("%Y-%m")
            existing = [
                d for d in self._tax_declarations.values()
                if d["period"] == current_period
            ]
            if not existing and self._tax_records:
                await self.generate_tax_declaration(current_period)

    # ========== 查询接口 ==========

    def calculate_tax(self, voucher_data: dict) -> dict:
        """
        同步计算税额（供API直接调用）

        根据凭证数据计算增值税额。
        """
        amount = self._extract_voucher_amount(voucher_data)
        rate = self._tax_rates["vat_standard"]
        tax_amount = round(amount * rate / (1 + rate), 2)
        return {
            "amount": amount,
            "vat_rate": rate,
            "tax_amount": tax_amount,
            "tax_type": "output_vat",
        }

    def get_tax_records(self, tax_type: Optional[str] = None) -> list[dict]:
        """获取税务记录列表"""
        records = list(self._tax_records.values())
        if tax_type:
            records = [r for r in records if r["tax_type"] == tax_type]
        return records

    def get_tax_declarations(self, status: Optional[str] = None) -> list[dict]:
        """获取申报记录列表"""
        declarations = list(self._tax_declarations.values())
        if status:
            declarations = [d for d in declarations if d["status"] == status]
        return declarations

    def get_tax_rates(self) -> dict[str, float]:
        """获取税率表"""
        return dict(self._tax_rates)
