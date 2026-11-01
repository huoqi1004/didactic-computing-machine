"""
绩效 Agent
负责KPI模板管理、绩效周期管理、绩效评审与目标跟踪。
监听绩效评审与KPI更新事件，自动汇总绩效数据。
"""
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class PerformanceAgent(BaseAgent):
    """绩效 Agent - 管理KPI、绩效评审与目标跟踪"""

    # 绩效周期状态
    CYCLE_DRAFT = "draft"
    CYCLE_ACTIVE = "active"
    CYCLE_COMPLETED = "completed"

    # 评审状态
    REVIEW_PENDING = "pending"
    REVIEW_SUBMITTED = "submitted"
    REVIEW_REVIEWED = "reviewed"

    # 目标状态
    GOAL_ACTIVE = "active"
    GOAL_COMPLETED = "completed"
    GOAL_OVERDUE = "overdue"

    # 绩效等级
    GRADE_S = "S"  # 卓越
    GRADE_A = "A"  # 优秀
    GRADE_B = "B"  # 良好
    GRADE_C = "C"  # 合格
    GRADE_D = "D"  # 不合格

    def __init__(self):
        super().__init__(
            name="绩效Agent",
            domain="performance",
            capabilities=[
                "kpi_management",
                "performance_review",
                "goal_tracking",
                "competency_assessment",
                "general",
            ],
            security_clearance="confidential",
        )
        # KPI定义: kpi_id -> dict
        self._kpi_templates: dict[str, dict] = {}
        # 绩效周期: cycle_id -> dict
        self._review_cycles: dict[str, dict] = {}
        # 评审记录列表
        self._reviews: list[dict] = []
        # 目标: goal_id -> dict
        self._goals: dict[str, dict] = {}
        # 编号计数器
        self._kpi_seq: int = 0
        self._cycle_seq: int = 0
        # 加载种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "KPI模板管理、绩效周期管理、绩效评审与目标跟踪"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅绩效相关事件"""
        self.subscribe_events(
            event_types=[
                EventType.PERFORMANCE_REVIEWED,
                EventType.KPI_UPDATED,
            ],
            handler=self._handle_event,
        )
        logger.info("performance_agent.initialized", agent=self.name,
                    kpi_templates=len(self._kpi_templates))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("performance_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        data = event.event_data

        if event.event_type == EventType.PERFORMANCE_REVIEWED:
            await self._handle_performance_reviewed(data)
        elif event.event_type == EventType.KPI_UPDATED:
            await self._handle_kpi_updated(data)

    async def _handle_performance_reviewed(self, data: dict):
        """处理绩效评审事件（同步外部评审数据）"""
        employee_id = data.get("employee_id", "")
        cycle_id = data.get("cycle_id", "")
        review = {
            "review_id": data.get("review_id", str(uuid4())),
            "employee_id": employee_id,
            "cycle_id": cycle_id,
            "scores": data.get("scores", {}),
            "total_score": data.get("total_score", 0),
            "grade": data.get("grade", ""),
            "comments": data.get("comments", ""),
            "status": data.get("status", self.REVIEW_REVIEWED),
            "source": "event",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._reviews.append(review)
        logger.info("performance_agent.review_synced",
                    employee_id=employee_id, cycle_id=cycle_id)

    async def _handle_kpi_updated(self, data: dict):
        """处理KPI更新事件"""
        kpi_id = data.get("kpi_id", "")
        if kpi_id and kpi_id in self._kpi_templates:
            kpi = self._kpi_templates[kpi_id]
            if "target" in data:
                kpi["target"] = data["target"]
            if "weight" in data:
                kpi["weight"] = data["weight"]
            kpi["updated_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("performance_agent.kpi_updated", kpi_id=kpi_id)

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出绩效决策"""
        action = context.get("action", "")

        if action == "submit_review":
            return await self._decide_review(context)
        elif action == "close_cycle":
            return await self._decide_close_cycle(context)
        elif action == "update_kpi":
            return await self._decide_update_kpi(context)

        return AgentDecision(
            decision_type="performance_unknown",
            reasoning=f"未知操作: {action}",
            action="拒绝执行",
            confidence=0.0,
            context=context,
        )

    async def _decide_review(self, context: dict) -> AgentDecision:
        """绩效评审决策"""
        employee_id = context.get("employee_id", "")
        cycle_id = context.get("cycle_id", "")
        scores = context.get("scores", {})

        cycle = self._review_cycles.get(cycle_id)
        if not cycle:
            return AgentDecision(
                decision_type="performance_review",
                reasoning=f"绩效周期不存在: {cycle_id}",
                action="拒绝评审",
                confidence=1.0,
            )

        if cycle.get("status") != self.CYCLE_ACTIVE:
            return AgentDecision(
                decision_type="performance_review",
                reasoning=f"绩效周期{cycle.get('name')}非活跃状态，无法提交评审",
                action="拒绝评审",
                confidence=0.9,
            )

        # 计算总分
        total_score = self._calculate_total_score(scores)
        grade = self._score_to_grade(total_score)

        return AgentDecision(
            decision_type="performance_review",
            reasoning=f"员工{employee_id}绩效评审完成，总分{total_score}，等级{grade}",
            action=f"提交绩效评审，等级{grade}",
            confidence=0.9,
            context={**context, "total_score": total_score, "grade": grade},
        )

    async def _decide_close_cycle(self, context: dict) -> AgentDecision:
        """关闭绩效周期决策"""
        cycle_id = context.get("cycle_id", "")
        cycle = self._review_cycles.get(cycle_id)
        if not cycle:
            return AgentDecision(
                decision_type="cycle_close",
                reasoning=f"绩效周期不存在: {cycle_id}",
                action="无法关闭",
                confidence=0.0,
            )
        return AgentDecision(
            decision_type="cycle_close",
            reasoning=f"关闭绩效周期{cycle.get('name')}",
            action="归档绩效周期",
            confidence=0.9,
            requires_human_approval=True,
            context=context,
        )

    async def _decide_update_kpi(self, context: dict) -> AgentDecision:
        """KPI更新决策"""
        kpi_id = context.get("kpi_id", "")
        kpi = self._kpi_templates.get(kpi_id)
        if not kpi:
            return AgentDecision(
                decision_type="kpi_update",
                reasoning=f"KPI不存在: {kpi_id}",
                action="拒绝更新",
                confidence=1.0,
            )
        return AgentDecision(
            decision_type="kpi_update",
            reasoning=f"更新KPI {kpi.get('name')}",
            action="应用KPI变更",
            confidence=0.9,
            context=context,
        )

    # ========== 辅助方法 ==========

    def _calculate_total_score(self, scores: dict) -> float:
        """根据KPI权重计算加权总分"""
        total_weight = 0
        weighted_sum = 0
        for kpi_id, score in scores.items():
            kpi = self._kpi_templates.get(kpi_id)
            weight = kpi.get("weight", 0) if kpi else 0
            weighted_sum += float(score) * weight
            total_weight += weight
        if total_weight == 0:
            # 无权重信息时取平均分
            if scores:
                return round(sum(float(s) for s in scores.values()) / len(scores), 2)
            return 0
        return round(weighted_sum / total_weight, 2)

    def _score_to_grade(self, score: float) -> str:
        """分数转等级"""
        if score >= 90:
            return self.GRADE_S
        elif score >= 80:
            return self.GRADE_A
        elif score >= 70:
            return self.GRADE_B
        elif score >= 60:
            return self.GRADE_C
        else:
            return self.GRADE_D

    def _generate_kpi_id(self) -> str:
        """生成KPI编号"""
        self._kpi_seq += 1
        return f"KPI{self._kpi_seq:04d}"

    def _generate_cycle_id(self) -> str:
        """生成周期编号"""
        self._cycle_seq += 1
        return f"CYCLE{self._cycle_seq:04d}"

    # ========== 公开 API ==========

    def get_kpi_templates(self, department: Optional[str] = None) -> list[dict]:
        """获取KPI模板列表，可按部门过滤"""
        templates = list(self._kpi_templates.values())
        if department:
            templates = [t for t in templates if t.get("department") == department]
        return templates

    def create_kpi_template(self, name: str, department: str,
                            metric: str, target: float,
                            weight: float, **kwargs) -> dict:
        """创建KPI模板"""
        kpi_id = kwargs.get("kpi_id") or self._generate_kpi_id()
        now = datetime.now(timezone.utc).isoformat()
        template = {
            "kpi_id": kpi_id,
            "name": name,
            "department": department,
            "metric": metric,
            "target": target,
            "weight": weight,
            "unit": kwargs.get("unit", ""),
            "description": kwargs.get("description", ""),
            "status": kwargs.get("status", "active"),
            "created_at": now,
        }
        self._kpi_templates[kpi_id] = template
        logger.info("performance_agent.kpi_created",
                    kpi_id=kpi_id, name=name)
        return template

    def get_review_cycles(self, status: Optional[str] = None) -> list[dict]:
        """获取绩效周期列表，可按状态过滤"""
        cycles = list(self._review_cycles.values())
        if status:
            cycles = [c for c in cycles if c.get("status") == status]
        return cycles

    def create_review_cycle(self, name: str, period: str,
                            start_date: str, end_date: str,
                            **kwargs) -> dict:
        """创建绩效周期"""
        cycle_id = kwargs.get("cycle_id") or self._generate_cycle_id()
        now = datetime.now(timezone.utc).isoformat()
        cycle = {
            "cycle_id": cycle_id,
            "name": name,
            "period": period,
            "start_date": start_date,
            "end_date": end_date,
            "status": kwargs.get("status", self.CYCLE_DRAFT),
            "description": kwargs.get("description", ""),
            "created_at": now,
        }
        self._review_cycles[cycle_id] = cycle
        logger.info("performance_agent.cycle_created",
                    cycle_id=cycle_id, name=name)
        return cycle

    def submit_review(self, employee_id: str, cycle_id: str,
                      scores: dict, comments: str = "") -> dict:
        """提交绩效评审"""
        cycle = self._review_cycles.get(cycle_id)
        if not cycle:
            return {"error": f"绩效周期不存在: {cycle_id}"}

        total_score = self._calculate_total_score(scores)
        grade = self._score_to_grade(total_score)

        review = {
            "review_id": str(uuid4()),
            "employee_id": employee_id,
            "cycle_id": cycle_id,
            "scores": scores,
            "total_score": total_score,
            "grade": grade,
            "comments": comments,
            "status": self.REVIEW_SUBMITTED,
            "source": "manual",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._reviews.append(review)
        logger.info("performance_agent.review_submitted",
                    employee_id=employee_id, total_score=total_score,
                    grade=grade)
        return review

    def get_reviews(self, employee_id: Optional[str] = None,
                    cycle_id: Optional[str] = None) -> list[dict]:
        """获取评审记录，可按员工和周期过滤"""
        reviews = list(self._reviews)
        if employee_id:
            reviews = [r for r in reviews if r["employee_id"] == employee_id]
        if cycle_id:
            reviews = [r for r in reviews if r["cycle_id"] == cycle_id]
        return reviews

    def set_goal(self, employee_id: str, goal_type: str,
                 description: str, target_date: str,
                 **kwargs) -> dict:
        """设定目标"""
        goal_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        goal = {
            "goal_id": goal_id,
            "employee_id": employee_id,
            "goal_type": goal_type,
            "description": description,
            "target_date": target_date,
            "progress": kwargs.get("progress", 0),
            "status": kwargs.get("status", self.GOAL_ACTIVE),
            "cycle_id": kwargs.get("cycle_id", ""),
            "created_at": now,
        }
        self._goals[goal_id] = goal
        logger.info("performance_agent.goal_set",
                    employee_id=employee_id, goal_type=goal_type)
        return goal

    def get_goals(self, employee_id: Optional[str] = None,
                  status: Optional[str] = None) -> list[dict]:
        """获取目标列表，可按员工和状态过滤"""
        goals = list(self._goals.values())
        if employee_id:
            goals = [g for g in goals if g["employee_id"] == employee_id]
        if status:
            goals = [g for g in goals if g["status"] == status]
        return goals

    def get_stats(self) -> dict:
        """获取绩效统计信息"""
        all_reviews = list(self._reviews)
        active_cycles = [c for c in self._review_cycles.values()
                         if c.get("status") == self.CYCLE_ACTIVE]
        completed_cycles = [c for c in self._review_cycles.values()
                            if c.get("status") == self.CYCLE_COMPLETED]

        # 按等级统计
        by_grade: dict[str, int] = {}
        for r in all_reviews:
            grade = r.get("grade", "unknown")
            by_grade[grade] = by_grade.get(grade, 0) + 1

        # 平均分
        avg_score = 0
        if all_reviews:
            avg_score = round(
                sum(r.get("total_score", 0) for r in all_reviews) / len(all_reviews), 2
            )

        # 活跃目标数
        active_goals = len([g for g in self._goals.values()
                            if g.get("status") == self.GOAL_ACTIVE])

        return {
            "total_kpi_templates": len(self._kpi_templates),
            "total_cycles": len(self._review_cycles),
            "active_cycles": len(active_cycles),
            "completed_cycles": len(completed_cycles),
            "total_reviews": len(all_reviews),
            "avg_score": avg_score,
            "reviews_by_grade": by_grade,
            "total_goals": len(self._goals),
            "active_goals": active_goals,
        }

    # ========== 种子数据 ==========

    def _seed_data(self):
        """加载种子数据：预置4个KPI模板"""
        now = datetime.now(timezone.utc).isoformat()
        seed_kpis = [
            {
                "kpi_id": "KPI0001",
                "name": "销售额",
                "department": "市场部",
                "metric": "月度销售额（万元）",
                "target": 100,
                "weight": 0.3,
                "unit": "万元",
                "description": "衡量市场部月度销售业绩",
                "status": "active",
                "created_at": now,
            },
            {
                "kpi_id": "KPI0002",
                "name": "客户满意度",
                "department": "产品部",
                "metric": "客户满意度评分（百分制）",
                "target": 90,
                "weight": 0.25,
                "unit": "分",
                "description": "衡量产品服务质量与客户体验",
                "status": "active",
                "created_at": now,
            },
            {
                "kpi_id": "KPI0003",
                "name": "项目完成率",
                "department": "技术部",
                "metric": "按时完成项目比例（%）",
                "target": 95,
                "weight": 0.25,
                "unit": "%",
                "description": "衡量技术部项目交付能力",
                "status": "active",
                "created_at": now,
            },
            {
                "kpi_id": "KPI0004",
                "name": "代码质量",
                "department": "技术部",
                "metric": "代码缺陷率（千行缺陷数）",
                "target": 3,
                "weight": 0.2,
                "unit": "个/千行",
                "description": "衡量代码质量与工程规范",
                "status": "active",
                "created_at": now,
            },
        ]

        for kpi in seed_kpis:
            self._kpi_templates[kpi["kpi_id"]] = kpi

        self._kpi_seq = 4
        logger.info("performance_agent.seed_data_loaded",
                    kpi_templates=len(self._kpi_templates))
