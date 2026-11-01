"""
预算Agent
负责预算编制、执行跟踪、差异分析与预算调整。
监听预算创建/审批/超支事件，以及凭证过账、采购审批、资金调拨等业务事件，
自动记录预算执行情况并在超支时发出预警。
"""
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class BudgetAgent(BaseAgent):
    """
    预算Agent - 管理预算全生命周期

    职责：
    - 预算编制：按周期/部门/类别创建预算方案
    - 预算执行：跟踪实际支出，记录执行明细
    - 差异分析：对比预算与实际，输出差异报告
    - 预算调整：支持预算变更并留存变更记录
    - 超支预警：执行超出预算时发布BUDGET_EXCEEDED事件
    """

    def __init__(self):
        super().__init__(
            name="预算Agent",
            domain="budget",
            capabilities=[
                "budget_preparation",   # 预算编制
                "budget_execution",     # 预算执行
                "budget_analysis",      # 预算分析
                "variance_report",      # 差异报告
                "general",
            ],
            security_clearance="confidential",
        )
        # 预算方案: budget_id -> dict
        self._budgets: dict[str, dict] = {}
        # 预算执行记录列表
        self._executions: list[dict] = []
        # 预算变更记录列表
        self._adjustments: list[dict] = []
        # 预算编号计数器
        self._budget_seq: int = 0
        # 加载种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "预算编制、执行跟踪、差异分析与预算调整"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅预算相关事件"""
        self.subscribe_events(
            event_types=[
                EventType.BUDGET_CREATED,
                EventType.BUDGET_APPROVED,
                EventType.BUDGET_EXCEEDED,
                EventType.VOUCHER_POSTED,
                EventType.PURCHASE_ORDER_APPROVED,
                EventType.FUND_TRANSFER_REQUESTED,
            ],
            handler=self._handle_event,
        )
        logger.info("budget_agent.initialized", agent=self.name,
                    budgets=len(self._budgets))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("budget_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        data = event.event_data

        if event.event_type == EventType.BUDGET_CREATED:
            await self._handle_budget_created(data)
        elif event.event_type == EventType.BUDGET_APPROVED:
            await self._handle_budget_approved(data)
        elif event.event_type == EventType.BUDGET_EXCEEDED:
            await self._handle_budget_exceeded(data)
        elif event.event_type == EventType.VOUCHER_POSTED:
            await self._handle_voucher_posted(data)
        elif event.event_type == EventType.PURCHASE_ORDER_APPROVED:
            await self._handle_purchase_approved(data)
        elif event.event_type == EventType.FUND_TRANSFER_REQUESTED:
            await self._handle_fund_transfer(data)

    async def _handle_budget_created(self, data: dict):
        """处理预算创建事件（同步外部预算数据）"""
        budget_id = data.get("budget_id", "")
        if budget_id and budget_id not in self._budgets:
            budget = {
                "budget_id": budget_id,
                "period": data.get("period", ""),
                "department": data.get("department", ""),
                "category": data.get("category", "operating"),
                "amount": float(data.get("amount", 0)),
                "actual_amount": 0.0,
                "items": data.get("items", []),
                "status": data.get("status", "draft"),
                "approved_by": "",
                "created_at": data.get("created_at",
                                       datetime.now(timezone.utc).isoformat()),
                "approved_at": "",
            }
            self._budgets[budget_id] = budget
            logger.info("budget_agent.budget_synced",
                        budget_id=budget_id)

    async def _handle_budget_approved(self, data: dict):
        """处理预算审批事件"""
        budget_id = data.get("budget_id", "")
        if budget_id and budget_id in self._budgets:
            self._budgets[budget_id]["status"] = "approved"
            self._budgets[budget_id]["approved_by"] = data.get("approved_by", "")
            self._budgets[budget_id]["approved_at"] = data.get(
                "approved_at", datetime.now(timezone.utc).isoformat()
            )
            logger.info("budget_agent.budget_approved_synced",
                        budget_id=budget_id)

    async def _handle_budget_exceeded(self, data: dict):
        """处理预算超支事件"""
        budget_id = data.get("budget_id", "")
        if budget_id and budget_id in self._budgets:
            self._budgets[budget_id]["status"] = "exceeded"
            logger.warning("budget_agent.budget_exceeded_alert",
                           budget_id=budget_id,
                           department=self._budgets[budget_id].get("department"))

    async def _handle_voucher_posted(self, data: dict):
        """处理凭证过账事件，自动记录预算执行"""
        amount = float(data.get("total_debit", 0) or data.get("amount", 0))
        department = data.get("department", "")
        if amount <= 0 or not department:
            return
        # 匹配同部门的已审批预算
        matched = self._match_budget(department=department)
        if matched:
            await self._apply_execution(
                matched, amount,
                description=f"凭证过账: {data.get('voucher_id', '')}",
                date=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
                source="voucher_posted",
            )

    async def _handle_purchase_approved(self, data: dict):
        """处理采购审批事件，自动记录预算执行"""
        amount = float(data.get("total_cost", 0) or data.get("amount", 0))
        department = data.get("department", "采购部")
        if amount <= 0:
            return
        matched = self._match_budget(department=department)
        if matched:
            await self._apply_execution(
                matched, amount,
                description=f"采购订单审批: {data.get('order_id', '')}",
                date=datetime.now(timezone.utc).isoformat(),
                source="purchase_approved",
            )

    async def _handle_fund_transfer(self, data: dict):
        """处理资金调拨事件，自动记录预算执行"""
        amount = float(data.get("amount", 0))
        department = data.get("department", "")
        if amount <= 0 or not department:
            return
        matched = self._match_budget(department=department)
        if matched:
            await self._apply_execution(
                matched, amount,
                description=f"资金调拨: {data.get('transfer_id', '')}",
                date=datetime.now(timezone.utc).isoformat(),
                source="fund_transfer",
            )

    def _match_budget(self, department: str) -> Optional[dict]:
        """匹配指定部门的已审批预算"""
        candidates = [
            b for b in self._budgets.values()
            if b.get("department") == department and b.get("status") == "approved"
        ]
        if not candidates:
            return None
        # 取最近创建的预算
        return max(candidates, key=lambda b: b.get("created_at", ""))

    async def _apply_execution(self, budget: dict, amount: float,
                               description: str, date: str,
                               source: str = "manual"):
        """应用预算执行记录，并在超支时发布事件"""
        budget["actual_amount"] += amount
        execution = {
            "execution_id": str(uuid4()),
            "budget_id": budget["budget_id"],
            "department": budget["department"],
            "actual_amount": amount,
            "description": description,
            "date": date,
            "source": source,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._executions.append(execution)

        # 超支检测
        if budget["actual_amount"] > budget["amount"] and budget.get("status") != "exceeded":
            budget["status"] = "exceeded"
            await self._publish_event(
                EventType.BUDGET_EXCEEDED,
                {
                    "budget_id": budget["budget_id"],
                    "department": budget["department"],
                    "period": budget["period"],
                    "budget_amount": budget["amount"],
                    "actual_amount": budget["actual_amount"],
                    "over_amount": budget["actual_amount"] - budget["amount"],
                },
            )
            logger.warning("budget_agent.exceeded",
                           budget_id=budget["budget_id"],
                           department=budget["department"])

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出预算决策"""
        action = context.get("action", "")

        if action == "approve_budget":
            return await self._decide_approve(context)
        elif action == "adjust_budget":
            return await self._decide_adjust(context)
        elif action == "create_budget":
            return await self._decide_create(context)

        return AgentDecision(
            decision_type="budget_unknown",
            reasoning=f"未知操作: {action}",
            action="拒绝执行",
            confidence=0.0,
            context=context,
        )

    async def _decide_approve(self, context: dict) -> AgentDecision:
        """预算审批决策"""
        budget_id = context.get("budget_id", "")
        budget = self._budgets.get(budget_id)
        if not budget:
            return AgentDecision(
                decision_type="budget_approval",
                reasoning=f"预算不存在: {budget_id}",
                action="拒绝审批",
                confidence=1.0,
                context=context,
            )
        if budget["amount"] <= 0:
            return AgentDecision(
                decision_type="budget_approval",
                reasoning="预算金额必须大于0",
                action="拒绝审批",
                confidence=1.0,
                requires_human_approval=True,
                context=context,
            )
        # 大额预算需人工审批
        if budget["amount"] >= 5_000_000:
            return AgentDecision(
                decision_type="budget_approval",
                reasoning=f"预算金额{budget['amount']:,.0f}元超过500万，需高管审批",
                action="转人工审批",
                confidence=0.8,
                requires_human_approval=True,
                context=context,
            )
        return AgentDecision(
            decision_type="budget_approval",
            reasoning=f"预算{budget_id}金额合理，批准执行",
            action="批准预算",
            confidence=0.9,
            context=context,
        )

    async def _decide_adjust(self, context: dict) -> AgentDecision:
        """预算调整决策"""
        budget_id = context.get("budget_id", "")
        new_amount = context.get("new_amount", 0)
        budget = self._budgets.get(budget_id)
        if not budget:
            return AgentDecision(
                decision_type="budget_adjustment",
                reasoning=f"预算不存在: {budget_id}",
                action="拒绝调整",
                confidence=1.0,
                context=context,
            )
        # 调整幅度超过20%需人工审批
        old_amount = budget["amount"]
        if old_amount > 0:
            change_ratio = abs(new_amount - old_amount) / old_amount
            if change_ratio > 0.2:
                return AgentDecision(
                    decision_type="budget_adjustment",
                    reasoning=f"调整幅度{change_ratio:.0%}超过20%，需人工审批",
                    action="转人工审批",
                    confidence=0.75,
                    requires_human_approval=True,
                    context=context,
                )
        return AgentDecision(
            decision_type="budget_adjustment",
            reasoning=f"预算{budget_id}调整幅度在合理范围内",
            action="执行预算调整",
            confidence=0.85,
            context=context,
        )

    async def _decide_create(self, context: dict) -> AgentDecision:
        """预算创建决策"""
        amount = context.get("amount", 0)
        if amount <= 0:
            return AgentDecision(
                decision_type="budget_creation",
                reasoning="预算金额必须大于0",
                action="拒绝创建",
                confidence=1.0,
                context=context,
            )
        return AgentDecision(
            decision_type="budget_creation",
            reasoning=f"创建{context.get('department', '')}"
                      f"{context.get('period', '')}预算{amount:,.0f}元",
            action="创建预算方案",
            confidence=0.9,
            context=context,
        )

    # ========== 公开 API ==========

    def _generate_budget_id(self) -> str:
        """生成预算编号"""
        self._budget_seq += 1
        return f"BGT{self._budget_seq:04d}"

    def get_budgets(self, period: Optional[str] = None,
                    department: Optional[str] = None,
                    status: Optional[str] = None) -> list[dict]:
        """获取预算列表，可按周期/部门/状态过滤"""
        budgets = list(self._budgets.values())
        if period:
            budgets = [b for b in budgets if b.get("period") == period]
        if department:
            budgets = [b for b in budgets if b.get("department") == department]
        if status:
            budgets = [b for b in budgets if b.get("status") == status]
        return budgets

    def get_budget(self, budget_id: str) -> Optional[dict]:
        """获取单个预算方案"""
        return self._budgets.get(budget_id)

    def create_budget(self, period: str, department: str,
                      category: str, amount: float,
                      items: Optional[list] = None) -> dict:
        """创建预算方案"""
        budget_id = self._generate_budget_id()
        now = datetime.now(timezone.utc).isoformat()
        budget = {
            "budget_id": budget_id,
            "period": period,
            "department": department,
            "category": category,
            "amount": float(amount),
            "actual_amount": 0.0,
            "items": items or [],
            "status": "draft",
            "approved_by": "",
            "created_at": now,
            "approved_at": "",
        }
        self._budgets[budget_id] = budget
        logger.info("budget_agent.budget_created",
                    budget_id=budget_id, department=department, period=period)
        return budget

    async def approve_budget(self, budget_id: str, approved_by: str) -> dict:
        """审批预算"""
        budget = self._budgets.get(budget_id)
        if not budget:
            return {"error": f"预算不存在: {budget_id}"}
        budget["status"] = "approved"
        budget["approved_by"] = approved_by
        budget["approved_at"] = datetime.now(timezone.utc).isoformat()

        # 发布预算审批事件
        await self._publish_event(
            EventType.BUDGET_APPROVED,
            {
                "budget_id": budget_id,
                "department": budget["department"],
                "period": budget["period"],
                "amount": budget["amount"],
                "approved_by": approved_by,
            },
        )
        logger.info("budget_agent.budget_approved",
                    budget_id=budget_id, approved_by=approved_by)
        return budget

    async def record_execution(self, budget_id: str, actual_amount: float,
                               description: str, date: str) -> dict:
        """记录预算执行（手动）"""
        budget = self._budgets.get(budget_id)
        if not budget:
            return {"error": f"预算不存在: {budget_id}"}
        await self._apply_execution(
            budget, float(actual_amount), description, date,
            source="manual",
        )
        logger.info("budget_agent.execution_recorded",
                    budget_id=budget_id, amount=actual_amount)
        return {
            "budget_id": budget_id,
            "actual_amount": budget["actual_amount"],
            "budget_amount": budget["amount"],
            "remaining": budget["amount"] - budget["actual_amount"],
            "utilization_rate": (
                budget["actual_amount"] / budget["amount"]
                if budget["amount"] > 0 else 0
            ),
        }

    def get_variance(self, budget_id: str) -> dict:
        """获取单个预算的差异分析"""
        budget = self._budgets.get(budget_id)
        if not budget:
            return {"error": f"预算不存在: {budget_id}"}
        return self._calc_variance(budget)

    def get_variances(self, period: Optional[str] = None,
                      department: Optional[str] = None) -> list[dict]:
        """获取预算差异分析列表"""
        budgets = list(self._budgets.values())
        if period:
            budgets = [b for b in budgets if b.get("period") == period]
        if department:
            budgets = [b for b in budgets if b.get("department") == department]
        return [self._calc_variance(b) for b in budgets]

    def _calc_variance(self, budget: dict) -> dict:
        """计算单个预算的差异"""
        planned = budget["amount"]
        actual = budget["actual_amount"]
        variance = planned - actual
        variance_ratio = (variance / planned) if planned > 0 else 0.0
        utilization = (actual / planned) if planned > 0 else 0.0
        if actual > planned:
            status = "over_budget"
        elif utilization >= 0.9:
            status = "near_limit"
        elif actual > 0:
            status = "on_track"
        else:
            status = "not_started"
        return {
            "budget_id": budget["budget_id"],
            "period": budget["period"],
            "department": budget["department"],
            "category": budget["category"],
            "planned_amount": planned,
            "actual_amount": actual,
            "variance": variance,
            "variance_ratio": variance_ratio,
            "utilization_rate": utilization,
            "status": status,
        }

    async def adjust_budget(self, budget_id: str, new_amount: float,
                            reason: str, approved_by: str) -> dict:
        """调整预算"""
        budget = self._budgets.get(budget_id)
        if not budget:
            return {"error": f"预算不存在: {budget_id}"}
        old_amount = budget["amount"]
        adjustment = {
            "adjustment_id": str(uuid4()),
            "budget_id": budget_id,
            "old_amount": old_amount,
            "new_amount": float(new_amount),
            "reason": reason,
            "approved_by": approved_by,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._adjustments.append(adjustment)
        budget["amount"] = float(new_amount)
        # 调整后重新评估状态
        if budget["actual_amount"] > budget["amount"]:
            budget["status"] = "exceeded"
        elif budget["status"] == "exceeded":
            budget["status"] = "approved"
        logger.info("budget_agent.budget_adjusted",
                    budget_id=budget_id,
                    old_amount=old_amount, new_amount=new_amount)
        return adjustment

    def get_budget_summary(self, period: str) -> dict:
        """获取指定周期的预算汇总"""
        budgets = [b for b in self._budgets.values() if b.get("period") == period]
        total_planned = sum(b["amount"] for b in budgets)
        total_actual = sum(b["actual_amount"] for b in budgets)
        by_department: dict[str, dict] = {}
        for b in budgets:
            dept = b["department"]
            if dept not in by_department:
                by_department[dept] = {
                    "planned": 0.0,
                    "actual": 0.0,
                    "count": 0,
                }
            by_department[dept]["planned"] += b["amount"]
            by_department[dept]["actual"] += b["actual_amount"]
            by_department[dept]["count"] += 1
        # 计算各部门利用率
        for dept in by_department:
            planned = by_department[dept]["planned"]
            actual = by_department[dept]["actual"]
            by_department[dept]["utilization_rate"] = (
                actual / planned if planned > 0 else 0.0
            )
        return {
            "period": period,
            "total_budgets": len(budgets),
            "total_planned": total_planned,
            "total_actual": total_actual,
            "total_variance": total_planned - total_actual,
            "overall_utilization": (
                total_actual / total_planned if total_planned > 0 else 0.0
            ),
            "by_department": by_department,
        }

    def get_stats(self) -> dict:
        """获取预算统计信息"""
        all_budgets = list(self._budgets.values())
        by_status: dict[str, int] = {}
        by_department: dict[str, int] = {}
        for b in all_budgets:
            status = b.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1
            dept = b.get("department", "unknown")
            by_department[dept] = by_department.get(dept, 0) + 1
        total_planned = sum(b["amount"] for b in all_budgets)
        total_actual = sum(b["actual_amount"] for b in all_budgets)
        exceeded = [b for b in all_budgets if b.get("status") == "exceeded"]
        return {
            "total_budgets": len(all_budgets),
            "total_executions": len(self._executions),
            "total_adjustments": len(self._adjustments),
            "by_status": by_status,
            "by_department": by_department,
            "total_planned": total_planned,
            "total_actual": total_actual,
            "exceeded_count": len(exceeded),
        }

    # ========== 种子数据 ==========

    def _seed_data(self):
        """加载种子数据：2026年年度预算（5个部门）"""
        now = datetime.now(timezone.utc).isoformat()
        seed_budgets = [
            {
                "budget_id": "BGT0001",
                "period": "2026",
                "department": "销售部",
                "category": "operating",
                "amount": 5_000_000,
                "actual_amount": 0.0,
                "items": ["市场推广", "差旅费", "客户招待", "销售佣金"],
                "status": "approved",
                "approved_by": "总经理",
                "created_at": now,
                "approved_at": now,
            },
            {
                "budget_id": "BGT0002",
                "period": "2026",
                "department": "技术部",
                "category": "operating",
                "amount": 3_000_000,
                "actual_amount": 0.0,
                "items": ["研发投入", "服务器费用", "软件许可", "培训费用"],
                "status": "approved",
                "approved_by": "总经理",
                "created_at": now,
                "approved_at": now,
            },
            {
                "budget_id": "BGT0003",
                "period": "2026",
                "department": "生产部",
                "category": "operating",
                "amount": 4_000_000,
                "actual_amount": 0.0,
                "items": ["原材料", "设备维护", "生产耗材", "质检费用"],
                "status": "approved",
                "approved_by": "总经理",
                "created_at": now,
                "approved_at": now,
            },
            {
                "budget_id": "BGT0004",
                "period": "2026",
                "department": "行政部",
                "category": "operating",
                "amount": 1_000_000,
                "actual_amount": 0.0,
                "items": ["办公租金", "办公用品", "物业水电", "安保费用"],
                "status": "approved",
                "approved_by": "总经理",
                "created_at": now,
                "approved_at": now,
            },
            {
                "budget_id": "BGT0005",
                "period": "2026",
                "department": "财务部",
                "category": "operating",
                "amount": 800_000,
                "actual_amount": 0.0,
                "items": ["审计费用", "税务咨询", "银行手续费", "财务软件"],
                "status": "approved",
                "approved_by": "总经理",
                "created_at": now,
                "approved_at": now,
            },
        ]
        for budget in seed_budgets:
            self._budgets[budget["budget_id"]] = budget
        self._budget_seq = 5
        logger.info("budget_agent.seed_data_loaded",
                    budgets=len(self._budgets))
