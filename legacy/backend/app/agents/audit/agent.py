# -*- coding: utf-8 -*-
"""
内审Agent
负责持续交易监控、舞弊检测、异常识别与合规审计。
监听所有业务事件，识别异常模式并冻结高风险流程。

职责：
- 监听所有业务事件（VOUCHER_GENERATED, PURCHASE_ORDER_CREATED,
  FUND_TRANSFER_REQUESTED, INVENTORY_ADJUSTED）
- 持续监控交易模式，识别异常（金额异常、频率异常、关联交易）
- 舞弊检测：识别异常关联交易、资金异常流向
- 发送警报并冻结相关流程（高风险时）
- 交易模式分析：使用LLM分析交易上下文
"""
import asyncio
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class InternalAuditAgent(BaseAgent):
    """
    内审Agent

    职责：
    - 监听所有业务事件，持续监控交易模式
    - 识别金额异常、频率异常、关联交易等异常模式
    - 舞弊检测：识别异常关联交易与资金异常流向
    - 高风险时发送警报并冻结相关流程
    - 使用LLM分析交易上下文（降级到规则）
    """

    # 金额异常阈值（单笔超过此值标记为需关注）
    AMOUNT_ANOMALY_THRESHOLD = 200_000.0
    # 高风险阈值（超过此值冻结流程）
    HIGH_RISK_THRESHOLD = 1_000_000.0
    # 频率异常阈值（同一来源短时间内事件次数）
    FREQUENCY_ANOMALY_THRESHOLD = 10

    def __init__(self):
        super().__init__(
            name="内审Agent",
            domain="audit",
            capabilities=[
                "transaction_monitoring",     # 交易监控
                "fraud_detection",            # 舞弊检测
                "anomaly_identification",     # 异常识别
                "compliance_audit",           # 合规审计
                "risk_assessment",            # 风险评估
                "general",
            ],
            security_clearance="top_secret",
        )
        # 警报记录：alert_id -> 警报详情
        self._alerts: dict[str, dict] = {}
        # 已识别的交易模式：pattern_id -> 模式详情
        self._transaction_patterns: dict[str, dict] = {}
        # 冻结的流程ID集合
        self._frozen_processes: set[str] = set()
        # 交易流水记录（用于频率分析）：source -> [timestamps]
        self._transaction_log: dict[str, list] = {}

    @property
    def description(self) -> str:
        return "持续交易监控、舞弊检测、异常识别与合规审计"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅所有业务事件"""
        self.subscribe_events(
            event_types=[
                EventType.VOUCHER_GENERATED,
                EventType.PURCHASE_ORDER_CREATED,
                EventType.FUND_TRANSFER_REQUESTED,
                EventType.INVENTORY_ADJUSTED,
            ],
            handler=self._handle_event,
        )
        logger.info("audit_agent.initialized", agent=self.name)

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("audit_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件：监控交易并识别异常"""
        # 记录交易流水
        source = event.event_data.get("po_id") or \
                 event.event_data.get("voucher_id") or \
                 event.event_data.get("transfer_id") or \
                 event.aggregate_id
        self._record_transaction(source, event.event_type.value)

        # 提取金额
        amount = self._extract_amount(event)

        # 1. 金额异常检测
        if amount > self.AMOUNT_ANOMALY_THRESHOLD:
            await self._raise_alert(
                event=event,
                alert_type="amount_anomaly",
                severity="high" if amount > self.HIGH_RISK_THRESHOLD else "medium",
                amount=amount,
                description=(
                    f"交易金额{amount:.2f}元超过异常阈值"
                    f"{self.AMOUNT_ANOMALY_THRESHOLD}元"
                ),
            )

        # 2. 频率异常检测
        freq_anomaly = self._check_frequency_anomaly(source)
        if freq_anomaly:
            await self._raise_alert(
                event=event,
                alert_type="frequency_anomaly",
                severity="medium",
                amount=amount,
                description=f"来源{source}短时间内事件次数异常：{freq_anomaly}次",
            )

        # 3. 舞弊检测（LLM优先，降级到规则）
        await self._detect_fraud(event, amount)

    def _extract_amount(self, event: Event) -> float:
        """从事件中提取金额"""
        data = event.event_data
        for key in ("total_amount", "amount", "total_cost", "total_debit"):
            val = data.get(key, 0)
            try:
                v = float(val or 0)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        return 0.0

    def _record_transaction(self, source: str, event_type: str):
        """记录交易流水用于频率分析"""
        if source not in self._transaction_log:
            self._transaction_log[source] = []
        self._transaction_log[source].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
        })

    def _check_frequency_anomaly(self, source: str) -> int:
        """检查频率异常，返回异常次数（0表示无异常）"""
        records = self._transaction_log.get(source, [])
        now = datetime.now(timezone.utc)
        recent = [
            r for r in records
            if (now - datetime.fromisoformat(r["timestamp"])).total_seconds() < 3600
        ]
        if len(recent) > self.FREQUENCY_ANOMALY_THRESHOLD:
            return len(recent)
        return 0

    # ========== 舞弊检测 ==========

    async def _detect_fraud(self, event: Event, amount: float):
        """
        舞弊检测

        LLM优先分析交易上下文，降级到规则（基于金额和关联性）。
        """
        from app.core.config import settings

        if settings.llm_enabled:
            context = {
                "event_type": event.event_type.value,
                "aggregate_id": event.aggregate_id,
                "amount": amount,
                "event_data": {
                    k: v for k, v in event.event_data.items()
                    if k in ("po_id", "voucher_id", "transfer_id",
                             "supplier_id", "from_account", "to_account")
                },
            }
            prompt = (
                "请分析此交易是否存在舞弊风险。"
                "返回JSON，包含 risk_level(low/medium/high), "
                "is_suspicious(bool), reasoning, indicators(列表)。"
            )
            try:
                result = await self.llm_analyze(prompt, context)
                if "error" not in result:
                    risk_level = str(result.get("risk_level", "low"))
                    is_suspicious = bool(result.get("is_suspicious", False))
                    if is_suspicious or risk_level == "high":
                        await self._raise_alert(
                            event=event,
                            alert_type="fraud_suspicion",
                            severity=risk_level,
                            amount=amount,
                            description=str(result.get("reasoning", "LLM识别舞弊风险")),
                            indicators=result.get("indicators", []),
                        )
                    return
            except Exception as e:
                logger.warning("audit_agent.llm_fraud_error",
                               agent=self.name, error=str(e))

        # 规则降级：超大金额直接标记为高风险
        if amount > self.HIGH_RISK_THRESHOLD:
            await self._raise_alert(
                event=event,
                alert_type="fraud_suspicion",
                severity="high",
                amount=amount,
                description=(
                    f"交易金额{amount:.2f}元超过高风险阈值"
                    f"{self.HIGH_RISK_THRESHOLD}元，存在舞弊风险"
                ),
                indicators=["amount_exceeds_high_risk_threshold"],
            )

    # ========== 警报与流程冻结 ==========

    async def _raise_alert(
        self, event: Event, alert_type: str, severity: str,
        amount: float, description: str, indicators: Optional[list] = None,
    ) -> dict:
        """
        发出审计警报

        高风险警报自动冻结相关流程。
        """
        alert_id = f"ALT-{str(uuid4())[:8]}"
        process_id = event.event_data.get("po_id") or \
                     event.event_data.get("voucher_id") or \
                     event.event_data.get("transfer_id") or \
                     event.aggregate_id

        alert = {
            "alert_id": alert_id,
            "alert_type": alert_type,
            "severity": severity,
            "amount": amount,
            "description": description,
            "indicators": indicators or [],
            "process_id": process_id,
            "event_type": event.event_type.value,
            "aggregate_id": event.aggregate_id,
            "status": "open",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._alerts[alert_id] = alert

        # 记录决策
        decision = AgentDecision(
            decision_type="audit_alert",
            reasoning=description,
            action=f"发出审计警报{alert_id}：{alert_type}（{severity}）",
            confidence=0.85 if severity == "high" else 0.75,
            requires_human_approval=(severity == "high"),
            context={
                "alert_id": alert_id,
                "alert_type": alert_type,
                "severity": severity,
                "amount": amount,
            },
        )
        await self.record_decision(decision)

        # 发布审计警报事件
        await self._publish_event(
            EventType.AUDIT_ALERT_RAISED,
            {**alert, "decision_id": decision.decision_id},
        )

        # 高风险自动冻结流程
        if severity == "high":
            await self.freeze_process(process_id, alert_id)

        logger.warning("audit_agent.alert_raised",
                       alert_id=alert_id, alert_type=alert_type,
                       severity=severity, amount=amount)
        return alert

    async def freeze_process(self, process_id: str, alert_id: str = "") -> bool:
        """冻结业务流程"""
        self._frozen_processes.add(process_id)
        await self._publish_event(
            EventType.PROCESS_FROZEN,
            {
                "process_id": process_id,
                "alert_id": alert_id,
                "frozen_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.warning("audit_agent.process_frozen",
                       process_id=process_id, alert_id=alert_id)
        return True

    async def unfreeze_process(self, process_id: str) -> bool:
        """解冻业务流程（人类监督员调用）"""
        if process_id not in self._frozen_processes:
            return False
        self._frozen_processes.discard(process_id)
        logger.info("audit_agent.process_unfrozen", process_id=process_id)
        return True

    # ========== 交易模式分析 ==========

    async def _analyze_transaction_patterns(self) -> list[dict]:
        """
        分析交易模式

        使用LLM分析交易上下文，识别异常模式（降级到规则）。
        返回新识别的模式列表。
        """
        from app.core.config import settings

        new_patterns = []

        if settings.llm_enabled and self._transaction_log:
            context = {
                "transaction_summary": {
                    src: len(recs) for src, recs in
                    list(self._transaction_log.items())[:20]
                },
                "total_sources": len(self._transaction_log),
                "alert_count": len(self._alerts),
            }
            prompt = (
                "请分析交易模式，识别异常模式。"
                "返回JSON，包含 patterns(列表，每项含 pattern_id, "
                "description, risk_level, recommendation)。"
            )
            try:
                result = await self.llm_analyze(prompt, context)
                if "error" not in result:
                    for p in result.get("patterns", []):
                        pattern_id = p.get("pattern_id", f"PAT-{str(uuid4())[:8]}")
                        pattern = {
                            "pattern_id": pattern_id,
                            "description": str(p.get("description", "")),
                            "risk_level": str(p.get("risk_level", "low")),
                            "recommendation": str(p.get("recommendation", "")),
                            "method": "llm",
                            "identified_at": datetime.now(timezone.utc).isoformat(),
                        }
                        self._transaction_patterns[pattern_id] = pattern
                        new_patterns.append(pattern)
                    return new_patterns
            except Exception as e:
                logger.warning("audit_agent.llm_pattern_error",
                               agent=self.name, error=str(e))

        # 规则降级：基于警报统计识别模式
        if len(self._alerts) > 5:
            pattern_id = f"PAT-{str(uuid4())[:8]}"
            pattern = {
                "pattern_id": pattern_id,
                "description": f"近期警报数量异常：{len(self._alerts)}条",
                "risk_level": "medium",
                "recommendation": "建议加强交易审核频率",
                "method": "rules",
                "identified_at": datetime.now(timezone.utc).isoformat(),
            }
            self._transaction_patterns[pattern_id] = pattern
            new_patterns.append(pattern)
        return new_patterns

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出审计决策"""
        alert_id = context.get("alert_id")
        alert = self._alerts.get(alert_id)

        if not alert:
            return AgentDecision(
                decision_type="error",
                reasoning=f"警报{alert_id}不存在",
                action="无法做出审计决策",
                confidence=0.0,
            )

        severity = alert["severity"]
        if severity == "high":
            return AgentDecision(
                decision_type="audit_freeze",
                reasoning=f"警报{alert_id}为高风险，需冻结流程{alert['process_id']}",
                action=f"冻结流程{alert['process_id']}",
                confidence=0.9,
                requires_human_approval=True,
                context={"alert_id": alert_id, "process_id": alert["process_id"]},
            )
        return AgentDecision(
            decision_type="audit_monitor",
            reasoning=f"警报{alert_id}为{severity}风险，持续监控",
            action=f"持续监控流程{alert['process_id']}",
            confidence=0.8,
            requires_human_approval=False,
            context={"alert_id": alert_id},
        )

    async def on_tick(self):
        """周期性扫描最近交易，识别异常模式"""
        await self._analyze_transaction_patterns()

    # ========== 查询接口 ==========

    def get_alerts(self, severity: Optional[str] = None) -> list[dict]:
        """获取警报列表，可按严重程度过滤"""
        alerts = list(self._alerts.values())
        if severity:
            alerts = [a for a in alerts if a["severity"] == severity]
        return alerts

    def get_frozen_processes(self) -> list[str]:
        """获取冻结的流程ID列表"""
        return list(self._frozen_processes)

    def get_transaction_patterns(self) -> list[dict]:
        """获取已识别的交易模式列表"""
        return list(self._transaction_patterns.values())
