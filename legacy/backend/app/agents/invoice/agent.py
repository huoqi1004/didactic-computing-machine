"""
发票 Agent
负责发票开具、验证、管理与统计
支持增值税专用发票、普通发票、电子发票
"""
from datetime import datetime, timezone
from uuid import uuid4
from typing import Optional
import re

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.config import settings
from app.core.event.models import Event, EventType


class InvoiceAgent(BaseAgent):
    """发票 Agent - 管理发票全生命周期"""

    # 发票类型配置
    INVOICE_TYPES = {
        "vat_special": {"name": "增值税专用发票", "code": "01", "tax_rate": 0.13},
        "vat_normal": {"name": "增值税普通发票", "code": "02", "tax_rate": 0.03},
        "electronic": {"name": "增值税电子普通发票", "code": "026", "tax_rate": 0.03},
        "electronic_special": {"name": "增值税电子专用发票", "code": "028", "tax_rate": 0.13},
    }

    # 发票状态
    STATUS_DRAFT = "draft"
    STATUS_ISSUED = "issued"
    STATUS_VERIFIED = "verified"
    STATUS_CANCELLED = "cancelled"
    STATUS_REJECTED = "rejected"

    def __init__(self):
        super().__init__(
            name="发票Agent",
            domain="invoice",
            capabilities=[
                "invoice_issuance",
                "invoice_verification",
                "invoice_management",
                "invoice_statistics",
                "tax_rate_matching",
                "general",
            ],
            security_clearance="confidential",
        )
        self._invoices: dict[str, dict] = {}
        self._invoice_counter: int = 0
        self._buyer_info: dict = {
            "name": "默认购买方",
            "tax_id": "91100000XXXXXXXXXX",
            "address": "",
            "phone": "",
            "bank": "",
            "account": "",
        }
        self._seller_info: dict = {
            "name": "默认销售方",
            "tax_id": "91100000XXXXXXXXXY",
            "address": "",
            "phone": "",
            "bank": "",
            "account": "",
        }

    @property
    def description(self) -> str:
        return "负责发票开具、验证、红冲与统计分析，支持增值税专用/普通/电子发票"

    async def initialize(self):
        self.subscribe_events(
            event_types=[
                EventType.VOUCHER_POSTED,
                EventType.PURCHASE_ORDER_APPROVED,
                EventType.REIMBURSEMENT_APPROVED,
            ],
            handler=self._handle_event,
        )

    async def _handle_event(self, event: Event):
        try:
            await self.process_event(event)
        except Exception as e:
            self._logger.error(
                "invoice.event_handler_failed",
                event_type=event.event_type.value,
                error=str(e),
            )

    async def process_event(self, event: Event):
        if event.event_type == EventType.VOUCHER_POSTED:
            data = event.event_data
            if data.get("amount", 0) > 0:
                await self._auto_issue_invoice(data)

    async def _auto_issue_invoice(self, voucher_data: dict):
        """凭证过账后自动开具发票"""
        amount = voucher_data.get("amount", 0)
        if amount <= 0:
            return

        invoice_data = {
            "invoice_type": "vat_normal",
            "buyer_name": voucher_data.get("counterparty", "客户"),
            "amount": amount,
            "description": voucher_data.get("summary", "销售商品"),
            "auto_generated": True,
        }
        await self.issue_invoice(invoice_data)

    async def make_decision(self, context: dict) -> AgentDecision:
        action = context.get("action", "")

        if action == "issue_invoice":
            return await self._decide_issue(context)
        elif action == "verify_invoice":
            return await self._decide_verify(context)
        elif action == "cancel_invoice":
            return await self._decide_cancel(context)

        return AgentDecision(
            decision_type="invoice_unknown",
            reasoning=f"未知操作: {action}",
            action="拒绝执行",
            confidence=0.0,
            requires_human_approval=False,
        )

    async def _decide_issue(self, context: dict) -> AgentDecision:
        """发票开具决策"""
        invoice_type = context.get("invoice_type", "vat_normal")
        amount = context.get("amount", 0)

        if amount <= 0:
            return AgentDecision(
                decision_type="invoice_issue",
                reasoning="发票金额必须大于0",
                action="拒绝开具",
                confidence=1.0,
                requires_human_approval=False,
            )

        if invoice_type not in self.INVOICE_TYPES:
            return AgentDecision(
                decision_type="invoice_issue",
                reasoning=f"不支持的发票类型: {invoice_type}",
                action="拒绝开具",
                confidence=1.0,
                requires_human_approval=False,
            )

        type_config = self.INVOICE_TYPES[invoice_type]
        tax_rate = type_config["tax_rate"]
        tax_amount = round(amount * tax_rate, 2)
        total = round(amount + tax_amount, 2)

        # LLM 辅助决策（可选）
        reasoning = f"开具{type_config['name']}，金额{amount:.2f}元，税率{tax_rate*100:.0f}%，税额{tax_amount:.2f}元"
        if settings.llm_enabled:
            llm_result = await self.llm_analyze(
                prompt="分析以下发票开具请求是否合规，检查金额、类型和税率是否匹配",
                context={
                    "invoice_type": invoice_type,
                    "amount": amount,
                    "description": context.get("description", ""),
                    "buyer_name": context.get("buyer_name", ""),
                },
            )
            if "error" not in llm_result:
                if llm_result.get("compliant") is False:
                    return AgentDecision(
                        decision_type="invoice_issue",
                        reasoning=llm_result.get("reason", "LLM判定不合规"),
                        action="拒绝开具",
                        confidence=llm_result.get("confidence", 0.8),
                        requires_human_approval=True,
                    )
                reasoning = llm_result.get("reason", reasoning)

        # 大额发票需人工审批
        requires_approval = total >= 100000

        return AgentDecision(
            decision_type="invoice_issue",
            reasoning=reasoning,
            action=f"开具{type_config['name']}，价税合计{total:.2f}元",
            confidence=0.95,
            requires_human_approval=requires_approval,
            context={
                "invoice_type": invoice_type,
                "amount": amount,
                "tax_rate": tax_rate,
                "tax_amount": tax_amount,
                "total": total,
            },
        )

    async def _decide_verify(self, context: dict) -> AgentDecision:
        """发票验证决策"""
        invoice_id = context.get("invoice_id", "")
        invoice = self._invoices.get(invoice_id)

        if not invoice:
            return AgentDecision(
                decision_type="invoice_verify",
                reasoning=f"发票不存在: {invoice_id}",
                action="验证失败",
                confidence=0.0,
                requires_human_approval=False,
            )

        issues = self._validate_invoice(invoice)
        if issues:
            return AgentDecision(
                decision_type="invoice_verify",
                reasoning=f"发票验证未通过: {'; '.join(issues)}",
                action="标记为异常",
                confidence=0.9,
                requires_human_approval=True,
            )

        return AgentDecision(
            decision_type="invoice_verify",
            reasoning=f"发票{invoice['invoice_no']}验证通过",
            action="标记为已验证",
            confidence=0.95,
            requires_human_approval=False,
        )

    async def _decide_cancel(self, context: dict) -> AgentDecision:
        """发票红冲决策"""
        invoice_id = context.get("invoice_id", "")
        invoice = self._invoices.get(invoice_id)

        if not invoice:
            return AgentDecision(
                decision_type="invoice_cancel",
                reasoning=f"发票不存在: {invoice_id}",
                action="无法红冲",
                confidence=0.0,
                requires_human_approval=False,
            )

        if invoice["status"] == self.STATUS_CANCELLED:
            return AgentDecision(
                decision_type="invoice_cancel",
                reasoning="发票已红冲，无需重复操作",
                action="拒绝执行",
                confidence=1.0,
                requires_human_approval=False,
            )

        return AgentDecision(
            decision_type="invoice_cancel",
            reasoning=f"红冲发票{invoice['invoice_no']}，原金额{invoice['total']:.2f}元",
            action="执行红冲",
            confidence=0.85,
            requires_human_approval=True,
        )

    def _validate_invoice(self, invoice: dict) -> list[str]:
        """验证发票信息完整性"""
        issues = []
        if not invoice.get("buyer_name"):
            issues.append("购买方名称为空")
        if not invoice.get("buyer_tax_id") and not invoice.get("auto_generated"):
            issues.append("购买方税号为空")
        if invoice.get("amount", 0) <= 0:
            issues.append("金额异常")
        if not invoice.get("description"):
            issues.append("商品/服务名称为空")
        return issues

    def _generate_invoice_no(self) -> str:
        """生成发票号码"""
        self._invoice_counter += 1
        now = datetime.now(timezone.utc)
        return f"INV{now.strftime('%Y%m%d')}{self._invoice_counter:06d}"

    # ==================== 公开 API ====================

    async def issue_invoice(self, data: dict) -> dict:
        """开具发票"""
        decision = await self._decide_issue(data)
        if decision.confidence < 0.5:
            return {"error": decision.reasoning}

        ctx = decision.context or {}
        invoice_type = data.get("invoice_type", "vat_normal")
        type_config = self.INVOICE_TYPES[invoice_type]

        invoice_id = str(uuid4())
        invoice_no = self._generate_invoice_no()
        now = datetime.now(timezone.utc)

        invoice = {
            "invoice_id": invoice_id,
            "invoice_no": invoice_no,
            "invoice_type": invoice_type,
            "invoice_type_name": type_config["name"],
            "invoice_code": type_config["code"],
            "buyer_name": data.get("buyer_name", ""),
            "buyer_tax_id": data.get("buyer_tax_id", ""),
            "buyer_address": data.get("buyer_address", ""),
            "buyer_phone": data.get("buyer_phone", ""),
            "buyer_bank": data.get("buyer_bank", ""),
            "buyer_account": data.get("buyer_account", ""),
            "seller_name": data.get("seller_name", self._seller_info["name"]),
            "seller_tax_id": self._seller_info["tax_id"],
            "amount": ctx.get("amount", data.get("amount", 0)),
            "tax_rate": ctx.get("tax_rate", type_config["tax_rate"]),
            "tax_amount": ctx.get("tax_amount", 0),
            "total": ctx.get("total", 0),
            "description": data.get("description", ""),
            "status": self.STATUS_ISSUED,
            "issue_date": now.isoformat(),
            "created_at": now.isoformat(),
            "auto_generated": data.get("auto_generated", False),
        }

        self._invoices[invoice_id] = invoice

        await self.record_decision(decision)
        await self._publish_event(
            EventType.INVOICE_CREATED,
            {
                "invoice_id": invoice_id,
                "invoice_no": invoice_no,
                "invoice_type": invoice_type,
                "amount": invoice["amount"],
                "tax_amount": invoice["tax_amount"],
                "total": invoice["total"],
                "buyer_name": invoice["buyer_name"],
            },
        )

        return invoice

    async def verify_invoice(self, invoice_id: str) -> dict:
        """验证发票"""
        decision = await self._decide_verify({"invoice_id": invoice_id})
        await self.record_decision(decision)

        invoice = self._invoices.get(invoice_id)
        if not invoice:
            return {"error": "发票不存在"}

        if decision.confidence >= 0.5:
            invoice["status"] = self.STATUS_VERIFIED
            invoice["verified_at"] = datetime.now(timezone.utc).isoformat()
            await self._publish_event(
                EventType.INVOICE_VERIFIED,
                {"invoice_id": invoice_id, "invoice_no": invoice["invoice_no"]},
            )
        else:
            invoice["status"] = self.STATUS_REJECTED
            await self._publish_event(
                EventType.INVOICE_REJECTED,
                {"invoice_id": invoice_id, "reason": decision.reasoning},
            )

        return invoice

    async def cancel_invoice(self, invoice_id: str, reason: str = "") -> dict:
        """红冲发票"""
        decision = await self._decide_cancel({"invoice_id": invoice_id})
        await self.record_decision(decision)

        invoice = self._invoices.get(invoice_id)
        if not invoice:
            return {"error": "发票不存在"}

        invoice["status"] = self.STATUS_CANCELLED
        invoice["cancelled_at"] = datetime.now(timezone.utc).isoformat()
        invoice["cancel_reason"] = reason

        await self._publish_event(
            EventType.INVOICE_CANCELLED,
            {"invoice_id": invoice_id, "invoice_no": invoice["invoice_no"], "reason": reason},
        )

        return invoice

    def get_invoices(
        self,
        status: Optional[str] = None,
        invoice_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """获取发票列表"""
        invoices = list(self._invoices.values())
        if status:
            invoices = [inv for inv in invoices if inv["status"] == status]
        if invoice_type:
            invoices = [inv for inv in invoices if inv["invoice_type"] == invoice_type]
        invoices.sort(key=lambda x: x["created_at"], reverse=True)
        return invoices[:limit]

    def get_invoice(self, invoice_id: str) -> Optional[dict]:
        """获取单张发票"""
        return self._invoices.get(invoice_id)

    def get_invoice_stats(self) -> dict:
        """发票统计"""
        all_invoices = list(self._invoices.values())
        issued = [inv for inv in all_invoices if inv["status"] in (self.STATUS_ISSUED, self.STATUS_VERIFIED)]
        cancelled = [inv for inv in all_invoices if inv["status"] == self.STATUS_CANCELLED]

        total_amount = sum(inv["amount"] for inv in issued)
        total_tax = sum(inv["tax_amount"] for inv in issued)
        total_total = sum(inv["total"] for inv in issued)

        by_type = {}
        for inv in issued:
            t = inv["invoice_type"]
            if t not in by_type:
                by_type[t] = {"count": 0, "amount": 0, "tax": 0, "total": 0}
            by_type[t]["count"] += 1
            by_type[t]["amount"] += inv["amount"]
            by_type[t]["tax"] += inv["tax_amount"]
            by_type[t]["total"] += inv["total"]

        return {
            "total_invoices": len(all_invoices),
            "issued_count": len(issued),
            "cancelled_count": len(cancelled),
            "total_amount": round(total_amount, 2),
            "total_tax": round(total_tax, 2),
            "total_with_tax": round(total_total, 2),
            "by_type": by_type,
        }

    async def on_tick(self):
        """周期任务：暂无"""
        pass
