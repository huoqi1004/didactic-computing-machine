"""
记账Agent
监听业务事件，依据会计准则自动生成多准则凭证，实现日清日结
支持CAS（中国企业会计准则）和IFRS（国际财务报告准则）
"""
import asyncio
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType
from app.core.event.aggregate import AggregateRoot
from app.core.config import SecurityLevel, Permission

logger = structlog.get_logger()


class AccountingStandard(str):
    """会计准则"""
    CAS = "CAS"      # 中国企业会计准则
    IFRS = "IFRS"    # 国际财务报告准则
    US_GAAP = "US_GAAP"  # 美国通用会计准则


class JournalEntry:
    """会计分录"""
    def __init__(self, account_code: str, account_name: str,
                 debit: float = 0, credit: float = 0, summary: str = ""):
        self.account_code = account_code    # 科目代码
        self.account_name = account_name    # 科目名称
        self.debit = debit                  # 借方金额
        self.credit = credit                # 贷方金额
        self.summary = summary              # 摘要

    def to_dict(self) -> dict:
        return {
            "account_code": self.account_code,
            "account_name": self.account_name,
            "debit": self.debit,
            "credit": self.credit,
            "summary": self.summary,
        }


class Voucher:
    """会计凭证"""
    def __init__(self, voucher_type: str, source_event: str,
                 entries: list[JournalEntry], standard: str = AccountingStandard.CAS):
        self.voucher_id = str(uuid4())
        self.voucher_type = voucher_type      # 凭证类型（收/付/转）
        self.source_event = source_event      # 来源事件
        self.entries = entries                # 分录列表
        self.standard = standard              # 会计准则
        self.timestamp = datetime.now(timezone.utc)
        self.status = "draft"                 # draft, approved, posted

    @property
    def total_debit(self) -> float:
        return sum(e.debit for e in self.entries)

    @property
    def total_credit(self) -> float:
        return sum(e.credit for e in self.entries)

    @property
    def is_balanced(self) -> bool:
        """借贷是否平衡"""
        return abs(self.total_debit - self.total_credit) < 0.01

    def to_dict(self) -> dict:
        return {
            "voucher_id": self.voucher_id,
            "voucher_type": self.voucher_type,
            "source_event": self.source_event,
            "standard": self.standard,
            "status": self.status,
            "entries": [e.to_dict() for e in self.entries],
            "total_debit": self.total_debit,
            "total_credit": self.total_credit,
            "is_balanced": self.is_balanced,
            "timestamp": self.timestamp.isoformat(),
        }


class AccountingRuleEngine:
    """
    会计准则引擎
    将业务事件映射为标准会计分录
    """

    # 业务事件到会计分录的映射规则
    # 使用 _AMOUNT 占位符标记需要填充金额的位置
    _AMOUNT = -1

    RULES = {
        EventType.REPLENISHMENT_APPROVED: {
            "description": "采购入库",
            "entries": [
                # 借：原材料/库存商品, 贷：应付账款
                JournalEntry("1403", "原材料", debit=_AMOUNT, summary="采购入库-借方"),
                JournalEntry("2202", "应付账款", credit=_AMOUNT, summary="采购入库-贷方"),
            ],
        },
        EventType.PURCHASE_ORDERED: {
            "description": "采购下单",
            "entries": [
                JournalEntry("1403", "原材料", debit=_AMOUNT, summary="采购订单-借方"),
                JournalEntry("2202", "应付账款", credit=_AMOUNT, summary="采购订单-贷方"),
            ],
        },
        EventType.INVENTORY_ADJUSTED: {
            "description": "库存调整",
            "entries": [
                # 根据调整方向决定借贷
            ],
        },
    }

    def generate_voucher(self, event: Event, standard: str = AccountingStandard.CAS) -> Optional[Voucher]:
        """根据业务事件生成会计凭证"""
        rule = self.RULES.get(event.event_type)
        if not rule:
            return None

        # 特殊处理库存调整
        if event.event_type == EventType.INVENTORY_ADJUSTED:
            return self._generate_inventory_adjustment_voucher(event, standard)

        # 通用规则处理
        amount = event.event_data.get("total_cost", 0)
        if amount <= 0:
            amount = event.event_data.get("amount", 0)

        entries = []
        for template in rule["entries"]:
            entry = JournalEntry(
                account_code=template.account_code,
                account_name=template.account_name,
                debit=amount if template.debit == self._AMOUNT else template.debit,
                credit=amount if template.credit == self._AMOUNT else template.credit,
                summary=template.summary,
            )
            entries.append(entry)

        voucher = Voucher(
            voucher_type="转",  # 转账凭证
            source_event=event.event_type.value,
            entries=entries,
            standard=standard,
        )

        return voucher

    def _generate_inventory_adjustment_voucher(self, event: Event,
                                                standard: str) -> Optional[Voucher]:
        """生成库存调整凭证"""
        data = event.event_data
        old_qty = data.get("old_quantity", 0)
        new_qty = data.get("quantity", 0)
        unit_cost = data.get("unit_cost", 0)
        diff = new_qty - old_qty
        amount = abs(diff) * unit_cost

        if amount == 0:
            return None

        if diff > 0:
            # 库存增加：借库存，贷相关科目
            entries = [
                JournalEntry("1403", "原材料", debit=amount, summary=f"库存增加{diff}件"),
                JournalEntry("6601", "销售费用", credit=amount, summary="库存调整-贷方"),
            ]
        else:
            # 库存减少：借相关科目，贷库存
            entries = [
                JournalEntry("6601", "销售费用", debit=amount, summary=f"库存减少{abs(diff)}件"),
                JournalEntry("1403", "原材料", credit=amount, summary="库存调整-贷方"),
            ]

        return Voucher(
            voucher_type="转",
            source_event=event.event_type.value,
            entries=entries,
            standard=standard,
        )


class BookkeepingAgent(BaseAgent):
    """
    记账Agent

    职责：
    - 监听所有业务事件
    - 根据会计准则自动生成凭证
    - 支持多准则（CAS/IFRS/US_GAAP）
    - 日清日结：每日自动汇总生成日记账
    - 凭证生成后在人在回路确认后过账
    """

    def __init__(self):
        super().__init__(
            name="记账Agent",
            domain="accounting",
            capabilities=[
                "bookkeeping",            # 记账
                "voucher_generation",     # 凭证生成
                "financial_reporting",    # 财务报告
                "accounting_standards",   # 会计准则
                "reconciliation",         # 对账
                "general",
            ],
            security_clearance="confidential",
        )
        self._rule_engine = AccountingRuleEngine()
        self._vouchers: dict[str, Voucher] = {}
        self._pending_vouchers: list[str] = []
        self._standard = AccountingStandard.CAS
    @property
    def description(self) -> str:
        return "监听业务事件，自动生成会计凭证，实现日清日结"

    async def initialize(self):
        """初始化Agent"""
        # 订阅所有需要记账的业务事件
        self.subscribe_events(
            event_types=[
                EventType.INVENTORY_ADJUSTED,
                EventType.REPLENISHMENT_APPROVED,
                EventType.REPLENISHMENT_REJECTED,
                EventType.PURCHASE_ORDERED,
                EventType.PURCHASE_REQUESTED,
            ],
            handler=self._handle_event,
        )
        logger.info("bookkeeping_agent.initialized", agent=self.name)

    async def _handle_event(self, event: Event):
        """处理接收到的事件"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("bookkeeping_agent.event_error",
                        agent=self.name, error=str(e))

    async def process_event(self, event: Event):
        """处理业务事件，生成凭证"""
        # 请求授权
        authorized = await self.request_authorization(
            intent="voucher_generation",
            resource=f"event:{event.event_type.value}",
            data_level=SecurityLevel.CONFIDENTIAL,
            permissions=[Permission.WRITE, Permission.EXECUTE],
            context={"event_type": event.event_type.value},
        )

        if not authorized:
            logger.warning("bookkeeping_agent.authorization_denied",
                          event_type=event.event_type.value)
            return

        # 生成凭证：优先使用LLM分析业务事件，降级到规则引擎
        llm_result = await self._generate_voucher_with_llm(event)
        if llm_result is not None:
            voucher, llm_reasoning, llm_confidence = llm_result
            reasoning_source = "llm"
        else:
            voucher = self._rule_engine.generate_voucher(event, self._standard)
            llm_reasoning = None
            llm_confidence = None
            reasoning_source = "rules"

        if not voucher:
            logger.debug("bookkeeping_agent.no_rule",
                        event_type=event.event_type.value)
            return

        # 验证借贷平衡
        if not voucher.is_balanced:
            logger.error("bookkeeping_agent.unbalanced_voucher",
                        voucher_id=voucher.voucher_id,
                        debit=voucher.total_debit,
                        credit=voucher.total_credit)
            # 不平衡凭证标记为错误
            voucher.status = "error"
            self._vouchers[voucher.voucher_id] = voucher
            return

        # LLM增强：生成凭证摘要（自然语言描述），降级到规则摘要
        llm_summary = await self._generate_voucher_summary_llm(event, voucher)
        if llm_summary:
            # 用LLM摘要增强缺少描述的分录
            for entry in voucher.entries:
                if not entry.summary:
                    entry.summary = llm_summary

        # 存储凭证
        self._vouchers[voucher.voucher_id] = voucher
        self._pending_vouchers.append(voucher.voucher_id)

        # 构建决策理由（LLM理由优先，降级到规则描述）
        rule_reasoning = (
            f"业务事件'{event.event_type.value}'触发凭证生成，"
            f"借方{voucher.total_debit:.2f}，贷方{voucher.total_credit:.2f}"
        )
        reasoning = llm_reasoning if llm_reasoning else rule_reasoning
        confidence = llm_confidence if llm_confidence is not None else 0.95

        # 记录决策
        decision = AgentDecision(
            decision_type="voucher_generation",
            reasoning=reasoning,
            action=f"生成{voucher.standard}准则凭证，类型：{voucher.voucher_type}字",
            confidence=confidence,
            requires_human_approval=True,  # 凭证需要人工确认后过账
            context={
                "voucher_id": voucher.voucher_id,
                "standard": voucher.standard,
                "reasoning_source": reasoning_source,
            },
        )
        await self.record_decision(decision)

        # 发布凭证生成事件
        await self._publish_event(
            EventType.VOUCHER_GENERATED,
            {
                **voucher.to_dict(),
                "source_event_id": str(event.event_id),
                "agent_id": self.agent_id,
            },
        )

        logger.info("bookkeeping_agent.voucher_generated",
                    voucher_id=voucher.voucher_id,
                    source_event=event.event_type.value,
                    debit=voucher.total_debit,
                    credit=voucher.total_credit,
                    standard=voucher.standard,
                    reasoning_source=reasoning_source)

    # ========== LLM认知引擎集成 ==========

    async def _generate_voucher_with_llm(
        self, event: Event
    ) -> Optional[tuple[Voucher, str, float]]:
        """
        使用LLM分析业务事件，生成会计凭证

        决策流程：
        1. 调用LLM分析业务事件，确定借贷科目和金额
        2. LLM返回结构化JSON：
           {voucher_type, summary, entries, confidence, reasoning, standard}
        3. 将LLM结果转换为Voucher对象并验证借贷平衡

        降级策略：
        - LLM未启用（settings.llm_enabled=False）：返回None，调用方降级到规则引擎
        - LLM不可用（连接失败/超时/重试耗尽）：返回None
        - LLM返回结果无效（字段缺失/借贷不平衡）：返回None

        返回：(voucher, reasoning, confidence) 元组；LLM不可用或结果无效时返回None。
        """
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        # 构建上下文
        context = {
            "event_type": event.event_type.value,
            "event_data": event.event_data,
            "standard": self._standard,
        }
        prompt = (
            "请根据以下业务事件生成会计凭证。"
            "分析业务事件的经济实质，推荐合适的会计科目，"
            "生成借贷平衡的会计分录。"
            "返回JSON，包含 voucher_type, summary, entries (列表，每项含 "
            "account_code, account_name, debit, credit, summary), "
            "confidence, reasoning, standard 字段。"
            "注意：所有分录的借方合计必须等于贷方合计。"
        )

        try:
            result = await self.llm_analyze(prompt, context)
        except Exception as e:
            logger.warning(
                "bookkeeping_agent.llm_voucher_error",
                agent=self.name, error=str(e),
            )
            return None

        if "error" in result:
            logger.info(
                "bookkeeping_agent.llm_voucher_unavailable",
                agent=self.name, reason=result.get("error"),
            )
            return None

        # 将LLM结果转换为Voucher对象
        voucher = self._build_voucher_from_llm_result(result, event)
        if voucher is None:
            return None

        # 验证凭证：借贷平衡且至少有一条分录
        if not voucher.is_balanced or len(voucher.entries) == 0:
            logger.warning(
                "bookkeeping_agent.llm_voucher_invalid",
                agent=self.name,
                balanced=voucher.is_balanced,
                entries_count=len(voucher.entries),
            )
            return None

        reasoning = str(result.get("reasoning", ""))
        try:
            confidence = float(result.get("confidence", 0.8))
        except (TypeError, ValueError):
            confidence = 0.8

        logger.info(
            "bookkeeping_agent.llm_voucher_generated",
            agent=self.name,
            voucher_id=voucher.voucher_id,
            source_event=event.event_type.value,
            confidence=confidence,
        )
        return voucher, reasoning, confidence

    def _build_voucher_from_llm_result(
        self, llm_result: dict, event: Event
    ) -> Optional[Voucher]:
        """
        将LLM返回的JSON结果转换为Voucher对象

        验证字段完整性，解析失败时返回None。
        """
        try:
            entries_data = llm_result.get("entries", [])
            if not entries_data or not isinstance(entries_data, list):
                return None

            entries = []
            for e in entries_data:
                if not isinstance(e, dict):
                    continue
                entry = JournalEntry(
                    account_code=str(e.get("account_code", "")),
                    account_name=str(e.get("account_name", "")),
                    debit=float(e.get("debit", 0) or 0),
                    credit=float(e.get("credit", 0) or 0),
                    summary=str(e.get("summary", "")),
                )
                entries.append(entry)

            if not entries:
                return None

            voucher_type = str(llm_result.get("voucher_type", "转"))
            standard = str(llm_result.get("standard", self._standard))

            voucher = Voucher(
                voucher_type=voucher_type,
                source_event=event.event_type.value,
                entries=entries,
                standard=standard,
            )
            return voucher
        except (TypeError, ValueError) as e:
            logger.warning(
                "bookkeeping_agent.llm_voucher_parse_error",
                agent=self.name, error=str(e),
            )
            return None

    async def _generate_voucher_summary_llm(
        self, event: Event, voucher: Voucher
    ) -> Optional[str]:
        """
        使用LLM生成凭证摘要（自然语言描述）

        返回None表示LLM不可用，调用方应使用规则生成的摘要。
        """
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        context = {
            "event_type": event.event_type.value,
            "event_data": event.event_data,
            "voucher_type": voucher.voucher_type,
            "entries": [e.to_dict() for e in voucher.entries],
            "total_debit": voucher.total_debit,
            "total_credit": voucher.total_credit,
            "standard": voucher.standard,
        }
        prompt = (
            "请为以下会计凭证生成一句简洁规范的摘要（不超过30个汉字），"
            "体现业务实质，如'采购原材料入库'、'销售商品收款'等。"
            "只返回摘要文本，不要返回JSON。"
        )

        try:
            summary = await self.llm_chat(
                [{"role": "user", "content": prompt + "\n\n上下文: " + str(context)}]
            )
        except Exception as e:
            logger.warning(
                "bookkeeping_agent.llm_summary_error",
                agent=self.name, error=str(e),
            )
            return None

        if not summary or not summary.strip():
            return None

        logger.info(
            "bookkeeping_agent.llm_summary_generated",
            agent=self.name, length=len(summary),
        )
        return summary.strip()

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出记账决策"""
        voucher_id = context.get("voucher_id")
        voucher = self._vouchers.get(voucher_id)

        if not voucher:
            return AgentDecision(
                decision_type="error",
                reasoning=f"凭证{voucher_id}不存在",
                action="无法做出记账决策",
                confidence=0.0,
            )

        return AgentDecision(
            decision_type="voucher_posting",
            reasoning=f"凭证{voucher_id}借贷平衡，借方{voucher.total_debit:.2f}，贷方{voucher.total_credit:.2f}",
            action=f"过账凭证{voucher_id}",
            confidence=0.95,
            requires_human_approval=True,
            context={"voucher_id": voucher_id},
        )

    async def approve_voucher(self, voucher_id: str, approved: bool):
        """审批凭证（人类监督员调用）"""
        voucher = self._vouchers.get(voucher_id)
        if not voucher:
            return False

        if approved:
            voucher.status = "approved"
            # 发布凭证审批事件
            await self._publish_event(
                EventType.VOUCHER_APPROVED,
                {"voucher_id": voucher_id, "status": "approved"},
            )
            if voucher_id in self._pending_vouchers:
                self._pending_vouchers.remove(voucher_id)
            logger.info("bookkeeping_agent.voucher_approved",
                        voucher_id=voucher_id)
        else:
            voucher.status = "rejected"
            logger.info("bookkeeping_agent.voucher_rejected",
                        voucher_id=voucher_id)

        return True

    async def daily_settlement(self) -> dict:
        """日清日结 - 汇总当日所有凭证"""
        today = datetime.now(timezone.utc).date()
        today_vouchers = [
            v for v in self._vouchers.values()
            if v.timestamp.date() == today
        ]

        total_debit = sum(v.total_debit for v in today_vouchers)
        total_credit = sum(v.total_credit for v in today_vouchers)

        settlement = {
            "date": today.isoformat(),
            "total_vouchers": len(today_vouchers),
            "approved": sum(1 for v in today_vouchers if v.status == "approved"),
            "pending": sum(1 for v in today_vouchers if v.status == "draft"),
            "rejected": sum(1 for v in today_vouchers if v.status == "rejected"),
            "total_debit": total_debit,
            "total_credit": total_credit,
            "balanced": abs(total_debit - total_credit) < 0.01,
            "standard": self._standard,
        }

        logger.info("bookkeeping_agent.daily_settlement", **settlement)
        return settlement

    def get_vouchers(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        """获取凭证列表"""
        vouchers = list(self._vouchers.values())
        if status:
            vouchers = [v for v in vouchers if v.status == status]
        return [v.to_dict() for v in vouchers[-limit:]]

    def get_pending_vouchers(self) -> list[dict]:
        """获取待审批凭证"""
        return [self._vouchers[vid].to_dict() for vid in self._pending_vouchers]
