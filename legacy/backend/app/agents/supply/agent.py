# -*- coding: utf-8 -*-
"""
供应规划Agent
评估供应商绩效，监控供应风险，发布供应风险预警。
监听采购审批与交期延期事件，优化供应策略。

职责：
- 监听 PURCHASE_ORDER_APPROVED 事件（评估供应商绩效）
- 监听 DELIVERY_DELAYED 事件（供应风险预警）
- 评估供应商绩效（交货准时率、质量合格率、价格竞争力）
- 供应风险预警（供应商集中度、地缘风险）
- 发布 SUPPLY_RISK_WARNING 和 SUPPLIER_PERFORMANCE_UPDATED 事件
- 高风险供应事件需人工确认
"""
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4
from collections import defaultdict

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class SupplyPlanningAgent(BaseAgent):
    """
    供应规划Agent

    职责：
    - 监听采购订单审批事件，记录供应商交易并评估绩效
    - 监听交期延期事件，发布供应风险预警
    - 评估供应商综合绩效（准时率、质量、价格竞争力）
    - 供应商集中度风险分析
    - 高风险供应事件需人在回路确认
    """

    # 供应商集中度风险阈值（单一供应商占比超过此值预警）
    CONCENTRATION_RISK_THRESHOLD = 0.6
    # 准时率低阈值
    LOW_ON_TIME_RATE_THRESHOLD = 0.8
    # 质量合格率低阈值
    LOW_QUALITY_RATE_THRESHOLD = 0.95

    def __init__(self):
        super().__init__(
            name="供应规划Agent",
            domain="supply",
            capabilities=[
                "supplier_evaluation",       # 供应商评估
                "supply_optimization",       # 供应优化
                "risk_assessment",           # 供应风险评估
                "general",
            ],
            security_clearance="internal",
        )
        # 供应商评估：supplier_id -> 评估详情
        self._supplier_evaluations: dict[str, dict] = {}
        # 供应风险：risk_id -> 风险详情
        self._supply_risks: dict[str, dict] = {}
        # 供应商交易记录：supplier_id -> 交易次数
        self._supplier_transactions: dict[str, int] = defaultdict(int)
        # 供应商延期次数：supplier_id -> 延期次数
        self._supplier_delays: dict[str, int] = defaultdict(int)

    @property
    def description(self) -> str:
        return "评估供应商绩效，监控供应风险，发布供应风险预警"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅采购审批与交期延期事件"""
        self.subscribe_events(
            event_types=[
                EventType.PURCHASE_ORDER_APPROVED,
                EventType.DELIVERY_DELAYED,
            ],
            handler=self._handle_event,
        )
        logger.info("supply_agent.initialized", agent=self.name)

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("supply_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.PURCHASE_ORDER_APPROVED:
            await self._record_supplier_transaction(event.event_data)

        elif event.event_type == EventType.DELIVERY_DELAYED:
            await self._handle_delivery_delay(event.event_data)

    async def _record_supplier_transaction(self, po_data: dict):
        """记录供应商交易并评估绩效"""
        supplier_id = po_data.get("supplier_id", "")
        if not supplier_id:
            return

        self._supplier_transactions[supplier_id] += 1

        # 评估供应商绩效
        await self._evaluate_supplier(supplier_id)

        logger.info("supply_agent.transaction_recorded",
                    supplier=supplier_id,
                    total_transactions=self._supplier_transactions[supplier_id])

    async def _handle_delivery_delay(self, delay_data: dict):
        """处理交期延期事件，发布供应风险预警"""
        supplier_id = delay_data.get("supplier_id", "")
        supplier_name = delay_data.get("supplier_name", "")
        po_id = delay_data.get("po_id", "")

        if not supplier_id:
            return

        self._supplier_delays[supplier_id] += 1

        # 创建供应风险记录
        risk_id = f"RISK-{str(uuid4())[:8]}"
        risk = {
            "risk_id": risk_id,
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "risk_type": "delivery_delay",
            "severity": "high",
            "description": (
                f"供应商'{supplier_name}'采购订单{po_id}交期延期，"
                f"累计延期{self._supplier_delays[supplier_id]}次"
            ),
            "po_id": po_id,
            "delay_count": self._supplier_delays[supplier_id],
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
        self._supply_risks[risk_id] = risk

        # 记录决策
        decision = AgentDecision(
            decision_type="supply_risk_warning",
            reasoning=risk["description"],
            action=f"发布供应风险预警：供应商'{supplier_name}'交期延期",
            confidence=0.88,
            requires_human_approval=self._supplier_delays[supplier_id] >= 3,
            context={
                "risk_id": risk_id,
                "supplier_id": supplier_id,
                "delay_count": self._supplier_delays[supplier_id],
            },
        )
        await self.record_decision(decision)

        # 发布供应风险预警事件
        await self._publish_event(
            EventType.SUPPLY_RISK_WARNING,
            {**risk, "decision_id": decision.decision_id},
        )

        # 重新评估供应商绩效
        await self._evaluate_supplier(supplier_id)

        logger.warning("supply_agent.risk_warning",
                       supplier=supplier_name, po_id=po_id,
                       delay_count=self._supplier_delays[supplier_id])

    # ========== 供应商评估 ==========

    async def _evaluate_supplier(self, supplier_id: str) -> Optional[dict]:
        """
        评估供应商综合绩效

        LLM优先（注入交易记录与延期数据），降级到规则评估。
        """
        llm_result = await self._evaluate_supplier_llm(supplier_id)
        if llm_result is not None:
            evaluation = llm_result
        else:
            evaluation = self._evaluate_supplier_rules(supplier_id)

        # 补充评估元数据
        evaluation["supplier_id"] = supplier_id
        evaluation["evaluated_at"] = datetime.now(timezone.utc).isoformat()
        self._supplier_evaluations[supplier_id] = evaluation

        # 记录决策
        decision = AgentDecision(
            decision_type="supplier_evaluation",
            reasoning=evaluation.get("reasoning", "供应商绩效评估"),
            action=(
                f"评估供应商'{evaluation.get('supplier_name', supplier_id)}'，"
                f"综合评分{evaluation.get('overall_score', 0):.2f}"
            ),
            confidence=evaluation.get("confidence", 0.75),
            requires_human_approval=evaluation.get("overall_score", 1.0) < 0.6,
            context={
                "supplier_id": supplier_id,
                "overall_score": evaluation.get("overall_score"),
                "method": evaluation.get("method", "rules"),
            },
        )
        await self.record_decision(decision)

        # 发布供应商绩效更新事件
        await self._publish_event(
            EventType.SUPPLIER_PERFORMANCE_UPDATED,
            {**evaluation, "decision_id": decision.decision_id},
        )

        logger.info("supply_agent.supplier_evaluated",
                    supplier=supplier_id,
                    score=evaluation.get("overall_score"),
                    method=evaluation.get("method", "rules"))
        return evaluation

    async def _evaluate_supplier_llm(self, supplier_id: str) -> Optional[dict]:
        """
        使用LLM评估供应商绩效

        返回None表示LLM不可用，调用方应降级到规则模式。
        """
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        total_transactions = self._supplier_transactions.get(supplier_id, 0)
        delay_count = self._supplier_delays.get(supplier_id, 0)

        context = {
            "supplier_id": supplier_id,
            "total_transactions": total_transactions,
            "delay_count": delay_count,
            "on_time_rate": (
                (total_transactions - delay_count) / total_transactions
                if total_transactions > 0 else 1.0
            ),
        }
        prompt = (
            "请评估该供应商的综合绩效。返回JSON，包含 "
            "supplier_name, on_time_rate(0-1), quality_rate(0-1), "
            "price_competitiveness(0-1), overall_score(0-1), "
            "risk_level(low/medium/high), reasoning, method(llm)。"
        )

        try:
            result = await self.llm_analyze(prompt, context)
        except Exception as e:
            logger.warning("supply_agent.llm_eval_error",
                           agent=self.name, error=str(e))
            return None

        if "error" in result:
            logger.info("supply_agent.llm_eval_unavailable",
                        agent=self.name, reason=result.get("error"))
            return None

        required_keys = ("overall_score",)
        if not all(k in result for k in required_keys):
            return None

        try:
            result["overall_score"] = float(result["overall_score"])
        except (TypeError, ValueError):
            return None

        result.setdefault("supplier_name", supplier_id)
        result.setdefault("on_time_rate", context["on_time_rate"])
        result.setdefault("quality_rate", 0.95)
        result.setdefault("price_competitiveness", 0.8)
        result.setdefault("risk_level", "low")
        result.setdefault("reasoning", "LLM供应商评估")
        result["method"] = "llm"

        return result

    def _evaluate_supplier_rules(self, supplier_id: str) -> dict:
        """
        规则评估（降级方案）

        基于交易次数与延期次数计算准时率，
        综合评分 = 准时率(50%) + 质量率(30%) + 价格竞争力(20%)。
        """
        total_transactions = self._supplier_transactions.get(supplier_id, 0)
        delay_count = self._supplier_delays.get(supplier_id, 0)

        if total_transactions > 0:
            on_time_rate = (total_transactions - delay_count) / total_transactions
        else:
            on_time_rate = 1.0

        # 质量合格率（简化：无延期时默认较高）
        quality_rate = max(1.0 - delay_count * 0.02, 0.85)

        # 价格竞争力（简化：默认中等偏上）
        price_competitiveness = 0.8

        overall_score = (
            on_time_rate * 0.5 +
            quality_rate * 0.3 +
            price_competitiveness * 0.2
        )

        # 风险等级
        if on_time_rate < self.LOW_ON_TIME_RATE_THRESHOLD:
            risk_level = "high"
        elif on_time_rate < 0.9:
            risk_level = "medium"
        else:
            risk_level = "low"

        return {
            "supplier_name": supplier_id,
            "on_time_rate": round(on_time_rate, 4),
            "quality_rate": round(quality_rate, 4),
            "price_competitiveness": round(price_competitiveness, 4),
            "overall_score": round(overall_score, 4),
            "risk_level": risk_level,
            "total_transactions": total_transactions,
            "delay_count": delay_count,
            "reasoning": (
                f"基于规则评估：准时率{on_time_rate*100:.1f}%，"
                f"累计交易{total_transactions}次，延期{delay_count}次，"
                f"综合评分{overall_score:.2f}"
            ),
            "method": "rules",
            "confidence": 0.78,
        }

    # ========== 供应商集中度风险 ==========

    async def _check_concentration_risk(self) -> list[dict]:
        """检查供应商集中度风险"""
        total = sum(self._supplier_transactions.values())
        if total == 0:
            return []

        warnings = []
        for supplier_id, count in self._supplier_transactions.items():
            ratio = count / total
            if ratio > self.CONCENTRATION_RISK_THRESHOLD:
                risk_id = f"CONC-{str(uuid4())[:8]}"
                evaluation = self._supplier_evaluations.get(supplier_id, {})
                warning = {
                    "risk_id": risk_id,
                    "supplier_id": supplier_id,
                    "supplier_name": evaluation.get("supplier_name", supplier_id),
                    "risk_type": "concentration",
                    "severity": "high",
                    "description": (
                        f"供应商集中度风险：'{supplier_id}'占总采购"
                        f"{ratio*100:.1f}%，超过阈值"
                        f"{self.CONCENTRATION_RISK_THRESHOLD*100:.0f}%"
                    ),
                    "concentration_ratio": round(ratio, 4),
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }
                self._supply_risks[risk_id] = warning
                warnings.append(warning)

                await self._publish_event(
                    EventType.SUPPLY_RISK_WARNING,
                    warning,
                )

                logger.warning("supply_agent.concentration_risk",
                               supplier=supplier_id, ratio=ratio)

        return warnings

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出供应规划决策"""
        supplier_id = context.get("supplier_id")
        evaluation = self._supplier_evaluations.get(supplier_id)

        if not evaluation:
            return AgentDecision(
                decision_type="error",
                reasoning=f"供应商{supplier_id}无评估记录",
                action="无法做出供应规划决策",
                confidence=0.0,
            )

        overall_score = evaluation.get("overall_score", 0)
        risk_level = evaluation.get("risk_level", "low")

        if risk_level == "high" or overall_score < 0.6:
            return AgentDecision(
                decision_type="supply_risk_mitigation",
                reasoning=(
                    f"供应商'{evaluation.get('supplier_name', supplier_id)}'"
                    f"综合评分{overall_score:.2f}偏低，风险等级{risk_level}，"
                    f"建议引入备用供应商"
                ),
                action=f"建议降低对供应商{supplier_id}的依赖",
                confidence=0.85,
                requires_human_approval=True,
                context={"supplier_id": supplier_id, "risk_level": risk_level},
            )

        return AgentDecision(
            decision_type="supply_assessment",
            reasoning=(
                f"供应商'{evaluation.get('supplier_name', supplier_id)}'"
                f"综合评分{overall_score:.2f}，风险等级{risk_level}"
            ),
            action=f"确认供应商{supplier_id}评估结果",
            confidence=evaluation.get("confidence", 0.78),
            requires_human_approval=False,
            context={"supplier_id": supplier_id},
        )

    async def on_tick(self):
        """周期性任务：检查供应商集中度风险"""
        await self._check_concentration_risk()

    # ========== 查询接口 ==========

    def get_supplier_evaluations(self, supplier_id: Optional[str] = None) -> list[dict]:
        """获取供应商评估列表"""
        evaluations = list(self._supplier_evaluations.values())
        if supplier_id:
            evaluations = [e for e in evaluations if e.get("supplier_id") == supplier_id]
        return evaluations

    def get_supply_risks(self, risk_type: Optional[str] = None) -> list[dict]:
        """获取供应风险列表"""
        risks = list(self._supply_risks.values())
        if risk_type:
            risks = [r for r in risks if r.get("risk_type") == risk_type]
        return risks
