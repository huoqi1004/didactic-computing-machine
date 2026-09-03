# -*- coding: utf-8 -*-
"""
资金Agent
负责现金流预测、支付排程、资金调拨与短期投资建议。
监听采购付款与凭证过账事件，优化企业资金运转。

职责：
- 监听 PURCHASE_ORDER_APPROVED 事件（采购付款需求）
- 监听 VOUCHER_POSTED 事件（凭证过账后的资金变动）
- 现金流预测：基于历史收支数据预测未来7天/30天现金流
- 支付排程：根据应付账款到期日和现金流预测，优化支付时序
- 资金调拨：大额转账需人在回路确认（>50万）
- 短期投资：闲置资金自动建议短期理财方案
"""
import asyncio
import structlog
from typing import Optional
from datetime import datetime, timezone, timedelta, date
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class TreasuryAgent(BaseAgent):
    """
    资金Agent

    职责：
    - 监听采购订单审批（PURCHASE_ORDER_APPROVED）形成应付账款
    - 监听凭证过账（VOUCHER_POSTED）记录资金收支变动
    - 基于历史收支数据预测未来现金流（LLM优先，降级到规则）
    - 根据应付账款到期日和现金流预测优化支付时序
    - 大额资金调拨（>50万）需人在回路确认
    - 闲置资金自动建议短期理财方案
    """

    # 大额资金调拨阈值（超过需人在回路确认）
    FUND_TRANSFER_THRESHOLD = 500_000.0

    # 现金流预测缓存时长（秒）：on_tick 每秒触发一次，预测按小时缓存即可，
    # 避免同一预测每秒重算、重发事件、重打 LLM（评估报告 B-01/B-02/B-03）
    FORECAST_CACHE_SECONDS = 3600
    _forecast_cache: Optional[dict] = None

    def __init__(self):
        super().__init__(
            name="资金Agent",
            domain="treasury",
            capabilities=[
                "cash_flow_forecasting",      # 现金流预测
                "payment_scheduling",          # 支付排程
                "short_term_investment",       # 短期投资
                "credit_management",           # 信用管理
                "fund_transfer",               # 资金调拨
                "general",
            ],
            security_clearance="confidential",
        )
        # 现金流缓存：date(ISO字符串) -> {inflow, outflow, net, details}
        self._cash_flow_cache: dict[str, dict] = {}
        # 支付排程：payment_id -> 支付详情
        self._payment_schedule: dict[str, dict] = {}
        # 资金调拨记录：transfer_id -> 调拨详情
        self._fund_transfers: dict[str, dict] = {}
        # 当前现金余额（初始模拟值）
        self._cash_balance: float = 1_000_000.0

    @property
    def description(self) -> str:
        return "现金流预测、支付排程、资金调拨与短期投资建议"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅采购付款与凭证过账事件"""
        self.subscribe_events(
            event_types=[
                EventType.PURCHASE_ORDER_APPROVED,
                EventType.VOUCHER_POSTED,
            ],
            handler=self._handle_event,
        )
        logger.info("treasury_agent.initialized", agent=self.name,
                    cash_balance=self._cash_balance)

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("treasury_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.PURCHASE_ORDER_APPROVED:
            await self._schedule_payment(event.event_data)

        elif event.event_type == EventType.VOUCHER_POSTED:
            await self._record_cash_flow(event.event_data)

    # ========== 现金流记录 ==========

    async def _record_cash_flow(self, voucher_data: dict):
        """记录凭证过账后的资金变动到现金流缓存"""
        today = date.today().isoformat()
        entries = voucher_data.get("entries", [])
        inflow = 0.0
        outflow = 0.0
        for entry in entries:
            debit = float(entry.get("debit", 0) or 0)
            credit = float(entry.get("credit", 0) or 0)
            # 简化规则：贷方增加流入，借方增加流出
            inflow += credit
            outflow += debit

        net = inflow - outflow
        if today not in self._cash_flow_cache:
            self._cash_flow_cache[today] = {
                "date": today,
                "inflow": 0.0,
                "outflow": 0.0,
                "net": 0.0,
                "details": [],
            }
        day_record = self._cash_flow_cache[today]
        day_record["inflow"] += inflow
        day_record["outflow"] += outflow
        day_record["net"] += net
        day_record["details"].append({
            "voucher_id": voucher_data.get("voucher_id", ""),
            "inflow": inflow,
            "outflow": outflow,
            "net": net,
        })
        # 更新现金余额
        self._cash_balance += net

        logger.info("treasury_agent.cash_flow_recorded",
                    date=today, inflow=inflow, outflow=outflow, net=net,
                    balance=self._cash_balance)

    # ========== 支付排程 ==========

    async def _schedule_payment(self, po_data: dict) -> Optional[dict]:
        """
        根据采购订单审批生成支付排程

        流程：
        1. 从采购订单提取应付金额和到期日
        2. 结合现金流预测优化支付时序
        3. 生成支付排程记录
        4. 发布 PAYMENT_SCHEDULED 事件
        """
        po_id = po_data.get("po_id", "")
        total_amount = float(po_data.get("total_amount", 0) or 0)
        if total_amount <= 0 or not po_id:
            return None

        # 默认付款条件 net30，到期日为30天后
        payment_date = (date.today() + timedelta(days=30)).isoformat()
        payment_id = f"PAY-{str(uuid4())[:8]}"

        # 使用LLM优化支付时序（降级到规则）
        optimization = await self._optimize_payment_timing_llm(
            payment_id, po_id, total_amount, payment_date
        )

        payment = {
            "payment_id": payment_id,
            "po_id": po_id,
            "amount": total_amount,
            "payment_date": optimization.get("payment_date", payment_date),
            "status": "scheduled",
            "supplier_id": po_data.get("supplier_id", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "optimization_method": optimization.get("method", "rules"),
            "reasoning": optimization.get("reasoning", ""),
        }
        self._payment_schedule[payment_id] = payment

        # 记录决策
        decision = AgentDecision(
            decision_type="payment_scheduling",
            reasoning=optimization.get("reasoning") or (
                f"采购订单{po_id}应付{total_amount:.2f}元，"
                f"安排于{payment['payment_date']}支付。"
            ),
            action=f"生成支付排程{payment_id}：金额{total_amount:.2f}元",
            confidence=optimization.get("confidence", 0.85),
            requires_human_approval=False,
            context={
                "payment_id": payment_id,
                "po_id": po_id,
                "amount": total_amount,
                "optimization_method": optimization.get("method", "rules"),
            },
        )
        await self.record_decision(decision)

        # 发布支付排程事件
        await self._publish_event(
            EventType.PAYMENT_SCHEDULED,
            {**payment, "decision_id": decision.decision_id},
        )

        logger.info("treasury_agent.payment_scheduled",
                    payment_id=payment_id, po_id=po_id,
                    amount=total_amount,
                    method=optimization.get("method", "rules"))
        return payment

    async def _optimize_payment_timing_llm(
        self, payment_id: str, po_id: str,
        amount: float, default_date: str,
    ) -> dict:
        """
        使用LLM优化支付时序

        LLM不可用时降级到规则：保持默认到期日。
        """
        from app.core.config import settings

        if not settings.llm_enabled:
            return {
                "payment_date": default_date,
                "method": "rules",
                "reasoning": "",
                "confidence": 0.85,
            }

        context = {
            "payment_id": payment_id,
            "po_id": po_id,
            "amount": amount,
            "default_date": default_date,
            "cash_balance": self._cash_balance,
            "cash_flow_cache": {
                k: {kk: vv for kk, vv in v.items() if kk != "details"}
                for k, v in list(self._cash_flow_cache.items())[-10:]
            },
        }
        prompt = (
            "请基于现金流状况优化此笔应付账款的支付时序。"
            "返回JSON，包含 payment_date(ISO日期), reasoning, confidence(0-1)。"
        )

        try:
            result = await self.llm_analyze(prompt, context)
        except Exception as e:
            logger.warning("treasury_agent.llm_optimize_error",
                           agent=self.name, error=str(e))
            return {
                "payment_date": default_date, "method": "rules",
                "reasoning": "", "confidence": 0.85,
            }

        if "error" in result:
            return {
                "payment_date": default_date, "method": "rules",
                "reasoning": "", "confidence": 0.85,
            }

        optimized_date = result.get("payment_date", default_date) or default_date
        try:
            confidence = float(result.get("confidence", 0.85))
        except (TypeError, ValueError):
            confidence = 0.85

        return {
            "payment_date": str(optimized_date),
            "method": "llm",
            "reasoning": str(result.get("reasoning", "")),
            "confidence": confidence,
        }

    # ========== 现金流预测 ==========

    async def get_cash_flow_forecast(self, days: int = 7) -> dict:
        """
        现金流预测

        基于历史收支数据预测未来N天的现金流。
        LLM优先，降级到规则（基于历史日均净流量推算）。

        参数：
        - days: 预测天数（7或30）

        返回：
        {
            "horizon_days": int,
            "forecast": [{date, projected_inflow, projected_outflow, projected_net}],
            "projected_balance": float,
            "method": "llm" | "rules",
            "confidence": float,
            "reasoning": str,
        }
        """
        # 尝试LLM预测
        llm_result = await self._forecast_cash_flow_llm(days)
        if llm_result is not None:
            return llm_result

        # 降级到规则预测
        return self._forecast_cash_flow_rules(days)

    async def _forecast_cash_flow_llm(self, days: int) -> Optional[dict]:
        """使用LLM进行现金流预测，返回None表示不可用"""
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        # 汇总历史数据
        history = [
            {k: v for k, v in record.items() if k != "details"}
            for record in list(self._cash_flow_cache.values())[-30:]
        ]
        context = {
            "days": days,
            "current_balance": self._cash_balance,
            "history": history,
            "pending_payments": [
                {k: v for k, v in p.items() if k != "reasoning"}
                for p in self._payment_schedule.values()
                if p.get("status") == "scheduled"
            ],
        }
        prompt = (
            f"请基于历史收支数据预测未来{days}天的现金流。"
            "返回JSON，包含 horizon_days, forecast(列表，每项含 date, "
            "projected_inflow, projected_outflow, projected_net), "
            "projected_balance, confidence(0-1), reasoning。"
        )

        try:
            result = await self.llm_analyze(prompt, context)
        except Exception as e:
            logger.warning("treasury_agent.llm_forecast_error",
                           agent=self.name, error=str(e))
            return None

        if "error" in result:
            return None

        forecast = result.get("forecast", [])
        if not isinstance(forecast, list):
            return None

        try:
            confidence = float(result.get("confidence", 0.8))
        except (TypeError, ValueError):
            confidence = 0.8

        return {
            "horizon_days": days,
            "forecast": forecast,
            "projected_balance": float(result.get("projected_balance", self._cash_balance)),
            "method": "llm",
            "confidence": confidence,
            "reasoning": str(result.get("reasoning", "")),
        }

    def _forecast_cash_flow_rules(self, days: int) -> dict:
        """规则现金流预测（降级方案）：基于历史日均净流量推算"""
        records = list(self._cash_flow_cache.values())
        if records:
            avg_inflow = sum(r["inflow"] for r in records) / len(records)
            avg_outflow = sum(r["outflow"] for r in records) / len(records)
        else:
            avg_inflow = 0.0
            avg_outflow = 0.0

        forecast = []
        projected_balance = self._cash_balance
        for i in range(1, days + 1):
            d = (date.today() + timedelta(days=i)).isoformat()
            net = avg_inflow - avg_outflow
            projected_balance += net
            forecast.append({
                "date": d,
                "projected_inflow": round(avg_inflow, 2),
                "projected_outflow": round(avg_outflow, 2),
                "projected_net": round(net, 2),
            })

        return {
            "horizon_days": days,
            "forecast": forecast,
            "projected_balance": round(projected_balance, 2),
            "method": "rules",
            "confidence": 0.78,
            "reasoning": "基于历史日均收支净额推算",
        }

    # ========== 短期投资建议 ==========

    async def _suggest_short_term_investment(self) -> Optional[dict]:
        """
        闲置资金短期投资建议

        LLM优先，降级到规则（基于现金余额阈值给出建议）。
        """
        from app.core.config import settings

        # 规则：预留3个月运营资金，闲置部分建议投资
        monthly_outflow = sum(
            r["outflow"] for r in self._cash_flow_cache.values()
        ) / max(len(self._cash_flow_cache), 1) if self._cash_flow_cache else 100000.0
        reserve = monthly_outflow * 3
        idle_funds = max(self._cash_balance - reserve, 0.0)

        if idle_funds < 100000:
            return {
                "suggestion": "hold",
                "idle_funds": idle_funds,
                "reasoning": "闲置资金不足，建议持有观望。",
                "method": "rules",
            }

        if settings.llm_enabled:
            context = {
                "cash_balance": self._cash_balance,
                "idle_funds": idle_funds,
                "monthly_outflow": monthly_outflow,
            }
            prompt = (
                "请为闲置资金推荐短期理财方案。"
                "返回JSON，包含 suggestion, product_type, expected_return, "
                "risk_level, reasoning。"
            )
            try:
                result = await self.llm_analyze(prompt, context)
                if "error" not in result:
                    result.setdefault("idle_funds", idle_funds)
                    result["method"] = "llm"
                    return result
            except Exception as e:
                logger.warning("treasury_agent.llm_investment_error",
                               agent=self.name, error=str(e))

        # 规则降级
        if idle_funds >= 1_000_000:
            return {
                "suggestion": "invest",
                "product_type": "货币基金",
                "idle_funds": idle_funds,
                "expected_return": "2.0%-2.5%",
                "risk_level": "low",
                "reasoning": f"闲置资金{idle_funds:.2f}元超过100万，建议配置货币基金。",
                "method": "rules",
            }
        return {
            "suggestion": "invest",
            "product_type": "银行结构性存款",
            "idle_funds": idle_funds,
            "expected_return": "1.5%-2.0%",
            "risk_level": "low",
            "reasoning": f"闲置资金{idle_funds:.2f}元，建议配置结构性存款。",
            "method": "rules",
        }

    # ========== 资金调拨 ==========

    async def request_fund_transfer(
        self, amount: float, from_account: str, to_account: str,
        reason: str = "",
    ) -> Optional[dict]:
        """
        请求资金调拨

        大额转账（>50万）需人在回路确认。

        参数：
        - amount: 调拨金额
        - from_account: 来源账户
        - to_account: 目标账户
        - reason: 调拨原因
        """
        if amount <= 0:
            return None

        transfer_id = f"TRF-{str(uuid4())[:8]}"
        requires_approval = amount > self.FUND_TRANSFER_THRESHOLD

        transfer = {
            "transfer_id": transfer_id,
            "amount": amount,
            "from_account": from_account,
            "to_account": to_account,
            "reason": reason,
            "status": "pending_approval" if requires_approval else "approved",
            "requires_human_approval": requires_approval,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._fund_transfers[transfer_id] = transfer

        # 记录决策
        decision = AgentDecision(
            decision_type="fund_transfer",
            reasoning=(
                f"资金调拨{amount:.2f}元从{from_account}到{to_account}"
                + (f"，超过阈值{self.FUND_TRANSFER_THRESHOLD}元需人工审批" if requires_approval else "")
            ),
            action=f"资金调拨请求{transfer_id}：{amount:.2f}元",
            confidence=0.9,
            requires_human_approval=requires_approval,
            context={
                "transfer_id": transfer_id,
                "amount": amount,
                "from_account": from_account,
                "to_account": to_account,
            },
        )
        await self.record_decision(decision)

        # 发布资金调拨请求事件
        await self._publish_event(
            EventType.FUND_TRANSFER_REQUESTED,
            {**transfer, "decision_id": decision.decision_id},
        )

        logger.info("treasury_agent.fund_transfer_requested",
                    transfer_id=transfer_id, amount=amount,
                    requires_approval=requires_approval)
        return transfer

    async def approve_fund_transfer(self, transfer_id: str, approved: bool) -> bool:
        """
        审批资金调拨（人类监督员调用）

        参数：
        - transfer_id: 调拨ID
        - approved: True=批准, False=拒绝
        """
        transfer = self._fund_transfers.get(transfer_id)
        if not transfer:
            logger.warning("treasury_agent.transfer_not_found",
                           transfer_id=transfer_id)
            return False

        if approved:
            transfer["status"] = "approved"
            transfer["approved_at"] = datetime.now(timezone.utc).isoformat()
            self._cash_balance -= float(transfer["amount"])
            logger.info("treasury_agent.transfer_approved",
                        transfer_id=transfer_id)
        else:
            transfer["status"] = "rejected"
            transfer["rejected_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("treasury_agent.transfer_rejected",
                        transfer_id=transfer_id)
        return True

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出资金决策"""
        transfer_id = context.get("transfer_id")
        transfer = self._fund_transfers.get(transfer_id)

        if not transfer:
            return AgentDecision(
                decision_type="error",
                reasoning=f"资金调拨{transfer_id}不存在",
                action="无法做出资金决策",
                confidence=0.0,
            )

        amount = float(transfer["amount"])
        if amount > self.FUND_TRANSFER_THRESHOLD:
            return AgentDecision(
                decision_type="fund_transfer_approval",
                reasoning=(
                    f"资金调拨{transfer_id}金额{amount:.2f}元超过阈值"
                    f"{self.FUND_TRANSFER_THRESHOLD}元，需人在回路确认"
                ),
                action=f"请求人工审批：资金调拨{transfer_id}",
                confidence=0.9,
                requires_human_approval=True,
                context={"transfer_id": transfer_id},
            )
        return AgentDecision(
            decision_type="fund_transfer_auto",
            reasoning=f"资金调拨{transfer_id}金额{amount:.2f}元在阈值内，自动批准",
            action=f"自动批准资金调拨{transfer_id}",
            confidence=0.95,
            requires_human_approval=False,
            context={"transfer_id": transfer_id},
        )

    async def on_tick(self):
        """每日检查现金流状况，生成预测和支付建议（预测结果按小时缓存）"""
        # 命中缓存则跳过：LLM预测与事件发布都随缓存降频，
        # 失败场景下由 llm_analyze 的 agent 级冷却兜底（连续失败3次冷却10分钟）
        now = datetime.now(timezone.utc)
        cached = self._forecast_cache
        if (
            cached is not None
            and (now - cached["at"]).total_seconds() < self.FORECAST_CACHE_SECONDS
        ):
            return

        # 生成现金流预测并发布事件
        forecast = await self.get_cash_flow_forecast(days=7)
        self._forecast_cache = {"at": now, "forecast": forecast}
        await self._publish_event(
            EventType.CASH_FLOW_FORECAST_COMPLETED,
            {
                "horizon_days": forecast["horizon_days"],
                "projected_balance": forecast["projected_balance"],
                "method": forecast["method"],
                "confidence": forecast["confidence"],
            },
        )

    # ========== 查询接口 ==========

    def get_payment_schedule(self, status: Optional[str] = None) -> list[dict]:
        """获取支付排程列表"""
        payments = list(self._payment_schedule.values())
        if status:
            payments = [p for p in payments if p["status"] == status]
        return payments

    def get_fund_transfers(self, status: Optional[str] = None) -> list[dict]:
        """获取资金调拨记录列表"""
        transfers = list(self._fund_transfers.values())
        if status:
            transfers = [t for t in transfers if t["status"] == status]
        return transfers

    def get_cash_balance(self) -> float:
        """获取当前现金余额"""
        return self._cash_balance
