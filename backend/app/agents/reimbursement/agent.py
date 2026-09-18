"""
报销 Agent
负责员工报销申请、项目费用核验、报销审批与付款
"""
from datetime import datetime, timezone
from uuid import uuid4
from typing import Optional
from enum import Enum

from app.core.agent.base import BaseAgent, AgentDecision
from app.db.repositories.base import has_db, schedule
from app.db.repositories.reimbursement_repository import reimbursement_request_repository
from app.core.config import settings
from app.core.event.models import Event, EventType


class ReimbursementCategory(str, Enum):
    """报销类别"""
    TRAVEL = "travel"              # 差旅费
    MEAL = "meal"                  # 餐饮费
    OFFICE = "office"              # 办公费
    TRANSPORT = "transport"        # 交通费
    COMMUNICATION = "communication"  # 通讯费
    TRAINING = "training"          # 培训费
    ENTERTAINMENT = "entertainment"  # 招待费
    PROJECT = "project"            # 项目费用
    OTHER = "other"                # 其他


class ReimbursementAgent(BaseAgent):
    """报销 Agent - 管理员工报销全流程"""

    # 报销状态
    STATUS_DRAFT = "draft"
    STATUS_PENDING = "pending"         # 待核验
    STATUS_VERIFIED = "verified"       # 已核验，待审批
    STATUS_APPROVED = "approved"       # 已审批，待付款
    STATUS_REJECTED = "rejected"       # 已拒绝
    STATUS_PAID = "paid"               # 已付款

    # 报销类别配置
    CATEGORY_CONFIG = {
        ReimbursementCategory.TRAVEL: {"name": "差旅费", "limit": 5000, "require_receipt": True, "tax_deductible": True},
        ReimbursementCategory.MEAL: {"name": "餐饮费", "limit": 500, "require_receipt": True, "tax_deductible": True},
        ReimbursementCategory.OFFICE: {"name": "办公费", "limit": 2000, "require_receipt": True, "tax_deductible": True},
        ReimbursementCategory.TRANSPORT: {"name": "交通费", "limit": 500, "require_receipt": False, "tax_deductible": True},
        ReimbursementCategory.COMMUNICATION: {"name": "通讯费", "limit": 300, "require_receipt": True, "tax_deductible": True},
        ReimbursementCategory.TRAINING: {"name": "培训费", "limit": 10000, "require_receipt": True, "tax_deductible": True},
        ReimbursementCategory.ENTERTAINMENT: {"name": "招待费", "limit": 2000, "require_receipt": True, "tax_deductible": False},
        ReimbursementCategory.PROJECT: {"name": "项目费用", "limit": 50000, "require_receipt": True, "tax_deductible": True},
        ReimbursementCategory.OTHER: {"name": "其他", "limit": 1000, "require_receipt": True, "tax_deductible": True},
    }

    # 审批阈值：超过此金额需人工审批
    AUTO_APPROVE_THRESHOLD = 1000
    MANUAL_APPROVE_THRESHOLD = 5000

    def __init__(self):
        super().__init__(
            name="报销Agent",
            domain="reimbursement",
            capabilities=[
                "reimbursement_submission",
                "receipt_verification",
                "expense_validation",
                "reimbursement_approval",
                "payment_processing",
                "project_cost_tracking",
                "general",
            ],
            security_clearance="confidential",
        )
        self._requests: dict[str, dict] = {}
        self._employees: dict[str, dict] = {}
        self._projects: dict[str, dict] = {}
        self._receipt_counter: int = 0

    @property
    def description(self) -> str:
        return "负责员工报销申请、票据核验、费用审批与付款，支持项目费用归集"

    async def initialize(self):
        self.subscribe_events(
            event_types=[
                EventType.INVOICE_VERIFIED,
                EventType.FUND_TRANSFER_REQUESTED,
            ],
            handler=self._handle_event,
        )

    async def hydrate_from_db(self) -> bool:
        """
        启动水合（Phase A/A6：报销单据落库）。

        Agent 无内存种子——DB 为空属正常（全新部署），
        返回 False 仅表示"无数据可水合"，不视为降级。
        """
        if not has_db():
            return False

        records = await reimbursement_request_repository.list_all()
        if records:
            self._requests = {
                r["reimbursement_id"]: r for r in records
            }
        logger.info("reimbursement_agent.hydrated",
                    requests=len(self._requests))
        return bool(records)

    async def _handle_event(self, event: Event):
        try:
            await self.process_event(event)
        except Exception as e:
            self._logger.error(
                "reimbursement.event_handler_failed",
                event_type=event.event_type.value,
                error=str(e),
            )

    async def process_event(self, event: Event):
        if event.event_type == EventType.INVOICE_VERIFIED:
            data = event.event_data
            if data.get("linked_reimbursement"):
                await self._link_invoice_to_reimbursement(data)

    async def _link_invoice_to_reimbursement(self, data: dict):
        """将验证通过的发票关联到报销申请"""
        reimb_id = data.get("linked_reimbursement")
        invoice_id = data.get("invoice_id")
        if reimb_id and reimb_id in self._requests:
            self._requests[reimb_id]["invoice_id"] = invoice_id
            self._requests[reimb_id]["invoice_verified"] = True

    async def make_decision(self, context: dict) -> AgentDecision:
        action = context.get("action", "")

        if action == "submit_reimbursement":
            return await self._decide_submit(context)
        elif action == "verify_reimbursement":
            return await self._decide_verify(context)
        elif action == "approve_reimbursement":
            return await self._decide_approve(context)
        elif action == "pay_reimbursement":
            return await self._decide_pay(context)

        return AgentDecision(
            decision_type="reimbursement_unknown",
            reasoning=f"未知操作: {action}",
            action="拒绝执行",
            confidence=0.0,
            requires_human_approval=False,
        )

    async def _decide_submit(self, context: dict) -> AgentDecision:
        """报销提交决策"""
        category_str = context.get("category", "other")
        try:
            category = ReimbursementCategory(category_str)
        except ValueError:
            return AgentDecision(
                decision_type="reimbursement_submit",
                reasoning=f"无效的报销类别: {category_str}",
                action="拒绝提交",
                confidence=1.0,
                requires_human_approval=False,
            )

        amount = context.get("amount", 0)
        if amount <= 0:
            return AgentDecision(
                decision_type="reimbursement_submit",
                reasoning="报销金额必须大于0",
                action="拒绝提交",
                confidence=1.0,
                requires_human_approval=False,
            )

        config = self.CATEGORY_CONFIG[category]
        issues = []

        if amount > config["limit"]:
            issues.append(f"金额{amount:.2f}元超过{config['name']}限额{config['limit']}元")

        if config["require_receipt"] and not context.get("has_receipt"):
            issues.append(f"{config['name']}需要附发票/收据")

        if context.get("project_id") and context.get("project_id") not in self._projects:
            issues.append(f"项目不存在: {context.get('project_id')}")

        if issues:
            return AgentDecision(
                decision_type="reimbursement_submit",
                reasoning=f"报销提交校验未通过: {'; '.join(issues)}",
                action="拒绝提交",
                confidence=0.9,
                requires_human_approval=False,
            )

        # LLM 辅助分析报销合理性
        reasoning = f"提交{config['name']}报销{amount:.2f}元"
        if settings.llm_enabled:
            llm_result = await self.llm_analyze(
                prompt="分析以下报销申请的合理性，检查金额是否异常、类别是否匹配",
                context={
                    "category": category_str,
                    "amount": amount,
                    "description": context.get("description", ""),
                    "employee": context.get("employee_name", ""),
                    "project_id": context.get("project_id", ""),
                },
            )
            if "error" not in llm_result:
                if llm_result.get("reasonable") is False:
                    return AgentDecision(
                        decision_type="reimbursement_submit",
                        reasoning=llm_result.get("reason", "LLM判定不合理"),
                        action="标记异常，需人工审核",
                        confidence=llm_result.get("confidence", 0.8),
                        requires_human_approval=True,
                    )
                reasoning = llm_result.get("reason", reasoning)

        # 判断是否需要人工审批
        requires_approval = amount >= self.AUTO_APPROVE_THRESHOLD

        return AgentDecision(
            decision_type="reimbursement_submit",
            reasoning=reasoning,
            action=f"受理{config['name']}报销申请",
            confidence=0.9,
            requires_human_approval=requires_approval,
            context={
                "category": category_str,
                "amount": amount,
                "limit": config["limit"],
                "tax_deductible": config["tax_deductible"],
            },
        )

    async def _decide_verify(self, context: dict) -> AgentDecision:
        """报销核验决策"""
        reimb_id = context.get("reimbursement_id", "")
        request = self._requests.get(reimb_id)

        if not request:
            return AgentDecision(
                decision_type="reimbursement_verify",
                reasoning=f"报销申请不存在: {reimb_id}",
                action="核验失败",
                confidence=0.0,
                requires_human_approval=False,
            )

        issues = []
        config = self.CATEGORY_CONFIG.get(ReimbursementCategory(request["category"]))
        if not config:
            issues.append("报销类别无效")
        else:
            if request["amount"] > config["limit"]:
                issues.append(f"超限: {request['amount']:.2f} > {config['limit']}")
            if config["require_receipt"] and not request.get("has_receipt"):
                issues.append("缺少发票/收据")

        if issues:
            return AgentDecision(
                decision_type="reimbursement_verify",
                reasoning=f"核验未通过: {'; '.join(issues)}",
                action="退回修改",
                confidence=0.9,
                requires_human_approval=False,
            )

        return AgentDecision(
            decision_type="reimbursement_verify",
            reasoning=f"报销{request['reimbursement_no']}核验通过",
            action="转入审批流程",
            confidence=0.95,
            requires_human_approval=False,
        )

    async def _decide_approve(self, context: dict) -> AgentDecision:
        """报销审批决策"""
        reimb_id = context.get("reimbursement_id", "")
        approved = context.get("approved", False)
        request = self._requests.get(reimb_id)

        if not request:
            return AgentDecision(
                decision_type="reimbursement_approve",
                reasoning=f"报销申请不存在: {reimb_id}",
                action="无法审批",
                confidence=0.0,
                requires_human_approval=False,
            )

        if approved:
            amount = request["amount"]
            if amount >= self.MANUAL_APPROVE_THRESHOLD:
                return AgentDecision(
                    decision_type="reimbursement_approve",
                    reasoning=f"大额报销{amount:.2f}元，需财务负责人确认",
                    action="转人工审批",
                    confidence=0.85,
                    requires_human_approval=True,
                )
            return AgentDecision(
                decision_type="reimbursement_approve",
                reasoning=f"审批通过报销{request['reimbursement_no']}，金额{amount:.2f}元",
                action="批准报销",
                confidence=0.95,
                requires_human_approval=False,
            )
        else:
            return AgentDecision(
                decision_type="reimbursement_approve",
                reasoning=f"拒绝报销{request.get('reimbursement_no', '')}",
                action="拒绝报销",
                confidence=0.9,
                requires_human_approval=False,
            )

    async def _decide_pay(self, context: dict) -> AgentDecision:
        """报销付款决策"""
        reimb_id = context.get("reimbursement_id", "")
        request = self._requests.get(reimb_id)

        if not request:
            return AgentDecision(
                decision_type="reimbursement_pay",
                reasoning=f"报销申请不存在: {reimb_id}",
                action="无法付款",
                confidence=0.0,
                requires_human_approval=False,
            )

        if request["status"] != self.STATUS_APPROVED:
            return AgentDecision(
                decision_type="reimbursement_pay",
                reasoning=f"报销状态为{request['status']}，无法付款",
                action="拒绝付款",
                confidence=1.0,
                requires_human_approval=False,
            )

        return AgentDecision(
            decision_type="reimbursement_pay",
            reasoning=f"付款{request['amount']:.2f}元至{request.get('employee_name', '员工')}",
            action="执行付款",
            confidence=0.9,
            requires_human_approval=True,
        )

    def _generate_reimb_no(self) -> str:
        """生成报销单号"""
        self._receipt_counter += 1
        now = datetime.now(timezone.utc)
        return f"REIMB{now.strftime('%Y%m%d')}{self._receipt_counter:06d}"

    # ==================== 公开 API ====================

    async def submit_reimbursement(self, data: dict) -> dict:
        """提交报销申请"""
        decision = await self._decide_submit(data)
        if decision.confidence < 0.5:
            return {"error": decision.reasoning}

        reimb_id = str(uuid4())
        reimb_no = self._generate_reimb_no()
        now = datetime.now(timezone.utc)

        request = {
            "reimbursement_id": reimb_id,
            "reimbursement_no": reimb_no,
            "employee_id": data.get("employee_id", ""),
            "employee_name": data.get("employee_name", ""),
            "department": data.get("department", ""),
            "category": data.get("category", "other"),
            "amount": data.get("amount", 0),
            "description": data.get("description", ""),
            "has_receipt": data.get("has_receipt", False),
            "receipt_amount": data.get("receipt_amount", 0),
            "invoice_id": data.get("invoice_id", ""),
            "invoice_verified": False,
            "project_id": data.get("project_id", ""),
            "project_name": data.get("project_name", ""),
            "status": self.STATUS_PENDING,
            "submitted_at": now.isoformat(),
            "created_at": now.isoformat(),
        }

        self._requests[reimb_id] = request
        schedule(
            reimbursement_request_repository.upsert(request, key_field="reimbursement_id"),
            "reimbursement_request_repo",
        )
        await self.record_decision(decision)

        await self._publish_event(
            EventType.REIMBURSEMENT_REQUESTED,
            {
                "reimbursement_id": reimb_id,
                "reimbursement_no": reimb_no,
                "employee_name": request["employee_name"],
                "category": request["category"],
                "amount": request["amount"],
                "project_id": request["project_id"],
            },
        )

        return request

    async def verify_reimbursement(self, reimb_id: str) -> dict:
        """核验报销申请"""
        decision = await self._decide_verify({"reimbursement_id": reimb_id})
        await self.record_decision(decision)

        request = self._requests.get(reimb_id)
        if not request:
            return {"error": "报销申请不存在"}

        if decision.confidence >= 0.5:
            request["status"] = self.STATUS_VERIFIED
            request["verified_at"] = datetime.now(timezone.utc).isoformat()
            await self._publish_event(
                EventType.REIMBURSEMENT_VERIFIED,
                {"reimbursement_id": reimb_id, "reimbursement_no": request["reimbursement_no"]},
            )
        else:
            request["status"] = self.STATUS_REJECTED
            request["rejected_at"] = datetime.now(timezone.utc).isoformat()
            request["reject_reason"] = decision.reasoning
            await self._publish_event(
                EventType.REIMBURSEMENT_REJECTED,
                {"reimbursement_id": reimb_id, "reason": decision.reasoning},
            )

        return request

    async def approve_reimbursement(self, reimb_id: str, approved: bool, remark: str = "") -> dict:
        """审批报销申请"""
        decision = await self._decide_approve({
            "reimbursement_id": reimb_id,
            "approved": approved,
        })
        await self.record_decision(decision)

        request = self._requests.get(reimb_id)
        if not request:
            return {"error": "报销申请不存在"}

        if approved and decision.confidence >= 0.5:
            request["status"] = self.STATUS_APPROVED
            request["approved_at"] = datetime.now(timezone.utc).isoformat()
            request["approval_remark"] = remark
            await self._publish_event(
                EventType.REIMBURSEMENT_APPROVED,
                {"reimbursement_id": reimb_id, "amount": request["amount"]},
            )
        else:
            request["status"] = self.STATUS_REJECTED
            request["rejected_at"] = datetime.now(timezone.utc).isoformat()
            request["reject_reason"] = remark or decision.reasoning
            await self._publish_event(
                EventType.REIMBURSEMENT_REJECTED,
                {"reimbursement_id": reimb_id, "reason": remark or decision.reasoning},
            )

        return request

    async def pay_reimbursement(self, reimb_id: str) -> dict:
        """付款"""
        decision = await self._decide_pay({"reimbursement_id": reimb_id})
        await self.record_decision(decision)

        request = self._requests.get(reimb_id)
        if not request:
            return {"error": "报销申请不存在"}

        if decision.confidence >= 0.5:
            request["status"] = self.STATUS_PAID
            request["paid_at"] = datetime.now(timezone.utc).isoformat()
            await self._publish_event(
                EventType.REIMBURSEMENT_PAID,
                {"reimbursement_id": reimb_id, "amount": request["amount"]},
            )

        return request

    def get_reimbursements(
        self,
        status: Optional[str] = None,
        employee_id: Optional[str] = None,
        project_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """获取报销列表"""
        requests = list(self._requests.values())
        if status:
            requests = [r for r in requests if r["status"] == status]
        if employee_id:
            requests = [r for r in requests if r["employee_id"] == employee_id]
        if project_id:
            requests = [r for r in requests if r["project_id"] == project_id]
        requests.sort(key=lambda x: x["created_at"], reverse=True)
        return requests[:limit]

    def get_reimbursement(self, reimb_id: str) -> Optional[dict]:
        """获取单条报销"""
        return self._requests.get(reimb_id)

    def get_pending_reimbursements(self) -> list[dict]:
        """获取待处理报销"""
        return [
            r for r in self._requests.values()
            if r["status"] in (self.STATUS_PENDING, self.STATUS_VERIFIED)
        ]

    def get_reimbursement_stats(self) -> dict:
        """报销统计"""
        all_requests = list(self._requests.values())
        pending = [r for r in all_requests if r["status"] in (self.STATUS_PENDING, self.STATUS_VERIFIED)]
        approved = [r for r in all_requests if r["status"] == self.STATUS_APPROVED]
        paid = [r for r in all_requests if r["status"] == self.STATUS_PAID]
        rejected = [r for r in all_requests if r["status"] == self.STATUS_REJECTED]

        total_pending = sum(r["amount"] for r in pending)
        total_paid = sum(r["amount"] for r in paid)

        by_category = {}
        for r in all_requests:
            cat = r["category"]
            if cat not in by_category:
                by_category[cat] = {"count": 0, "amount": 0}
            by_category[cat]["count"] += 1
            by_category[cat]["amount"] += r["amount"]

        by_project = {}
        for r in all_requests:
            if r.get("project_id"):
                pid = r["project_id"]
                if pid not in by_project:
                    by_project[pid] = {
                        "project_name": r.get("project_name", ""),
                        "count": 0,
                        "amount": 0,
                    }
                by_project[pid]["count"] += 1
                by_project[pid]["amount"] += r["amount"]

        return {
            "total_requests": len(all_requests),
            "pending_count": len(pending),
            "approved_count": len(approved),
            "paid_count": len(paid),
            "rejected_count": len(rejected),
            "total_pending_amount": round(total_pending, 2),
            "total_paid_amount": round(total_paid, 2),
            "by_category": by_category,
            "by_project": by_project,
        }

    def register_project(self, project_id: str, name: str, budget: float = 0) -> dict:
        """注册项目（用于项目费用归集）"""
        project = {
            "project_id": project_id,
            "name": name,
            "budget": budget,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        self._projects[project_id] = project
        return project

    def register_employee(self, employee_id: str, name: str, department: str = "") -> dict:
        """注册员工"""
        employee = {
            "employee_id": employee_id,
            "name": name,
            "department": department,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        self._employees[employee_id] = employee
        return employee

    async def on_tick(self):
        """周期任务：暂无"""
        pass
