# -*- coding: utf-8 -*-
"""
项目Agent
负责项目全生命周期管理：规划、进度跟踪、成本控制与里程碑管理。
监听项目创建、里程碑达成、项目完成与销售订单确认事件。

职责：
- 监听 PROJECT_CREATED 事件（项目创建归档）
- 监听 PROJECT_MILESTONE_REACHED 事件（里程碑达成归档）
- 监听 PROJECT_COMPLETED 事件（项目完成归档）
- 监听 SALES_ORDER_CONFIRMED 事件（销售确认触发交付项目）
- 管理项目、里程碑、成本记录与项目成员
- 里程碑达成自动发布事件
- 预算超支预警
- 项目完成需人在回路确认
"""
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class ProjectAgent(BaseAgent):
    """
    项目Agent

    职责：
    - 项目创建与状态管理
    - 里程碑规划与达成跟踪
    - 项目成本记录与预算控制
    - 项目进度计算（里程碑完成率、预算使用率、进度偏差）
    - 销售确认后自动创建交付项目
    - 项目完成需人在回路确认
    """

    # 预算超支预警阈值（使用率超过90%预警）
    BUDGET_WARNING_THRESHOLD = 0.9
    # 项目完成需人工审批
    HUMAN_APPROVAL_ON_COMPLETE = True

    def __init__(self):
        super().__init__(
            name="项目Agent",
            domain="project",
            capabilities=[
                "project_planning",       # 项目规划
                "progress_tracking",      # 进度跟踪
                "cost_management",        # 成本管理
                "milestone_management",   # 里程碑管理
                "general",
            ],
            security_clearance="internal",
        )
        # 项目：project_id -> 项目详情
        self._projects: dict[str, dict] = {}
        # 里程碑：milestone_id -> 里程碑详情
        self._milestones: dict[str, dict] = {}
        # 项目成本记录列表
        self._costs: list[dict] = []
        # 项目成员：project_id -> 成员列表
        self._members: dict[str, list[dict]] = {}
        # 初始化种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "管理项目规划、进度跟踪、成本控制与里程碑，监听销售确认自动创建交付项目"

    # ========== 初始化 ==========

    def _seed_data(self):
        """注入种子项目数据（3个项目，每个含2-3个里程碑）"""
        # 项目1：ERP实施
        self._projects["proj-001"] = {
            "project_id": "proj-001",
            "name": "ERP系统实施项目",
            "description": "全公司ERP系统选型、实施与上线",
            "manager": "陈项目经理",
            "start_date": "2026-01-01",
            "end_date": "2026-09-30",
            "budget": 500000.0,
            "department": "信息技术部",
            "status": "in_progress",
            "created_at": "2026-01-01T08:00:00+00:00",
        }
        self._milestones["ms-001"] = {
            "milestone_id": "ms-001",
            "project_id": "proj-001",
            "name": "需求分析完成",
            "target_date": "2026-02-28",
            "description": "完成全公司业务需求调研与分析",
            "status": "completed",
            "actual_date": "2026-02-25",
            "notes": "需求调研顺利完成",
            "created_at": "2026-01-01T08:00:00+00:00",
        }
        self._milestones["ms-002"] = {
            "milestone_id": "ms-002",
            "project_id": "proj-001",
            "name": "系统配置与开发完成",
            "target_date": "2026-06-30",
            "description": "完成ERP系统配置、定制开发与单元测试",
            "status": "in_progress",
            "actual_date": None,
            "notes": "",
            "created_at": "2026-01-01T08:00:00+00:00",
        }
        self._milestones["ms-003"] = {
            "milestone_id": "ms-003",
            "project_id": "proj-001",
            "name": "系统上线",
            "target_date": "2026-09-30",
            "description": "完成UAT测试、培训与正式上线",
            "status": "pending",
            "actual_date": None,
            "notes": "",
            "created_at": "2026-01-01T08:00:00+00:00",
        }
        self._members["proj-001"] = [
            {"name": "陈项目经理", "role": "项目经理", "department": "信息技术部"},
            {"name": "刘架构师", "role": "技术架构", "department": "信息技术部"},
            {"name": "赵业务", "role": "业务分析", "department": "运营部"},
        ]

        # 项目2：官网改版
        self._projects["proj-002"] = {
            "project_id": "proj-002",
            "name": "企业官网改版项目",
            "description": "企业官网UI重新设计与前端重构",
            "manager": "孙设计",
            "start_date": "2026-03-01",
            "end_date": "2026-07-31",
            "budget": 150000.0,
            "department": "市场部",
            "status": "in_progress",
            "created_at": "2026-03-01T08:00:00+00:00",
        }
        self._milestones["ms-004"] = {
            "milestone_id": "ms-004",
            "project_id": "proj-002",
            "name": "视觉设计定稿",
            "target_date": "2026-04-15",
            "description": "完成官网视觉设计与评审",
            "status": "completed",
            "actual_date": "2026-04-10",
            "notes": "设计稿一次通过",
            "created_at": "2026-03-01T08:00:00+00:00",
        }
        self._milestones["ms-005"] = {
            "milestone_id": "ms-005",
            "project_id": "proj-002",
            "name": "前端开发完成",
            "target_date": "2026-06-30",
            "description": "完成前端页面开发与响应式适配",
            "status": "in_progress",
            "actual_date": None,
            "notes": "",
            "created_at": "2026-03-01T08:00:00+00:00",
        }
        self._members["proj-002"] = [
            {"name": "孙设计", "role": "项目经理", "department": "市场部"},
            {"name": "周前端", "role": "前端开发", "department": "信息技术部"},
        ]

        # 项目3：新产品研发
        self._projects["proj-003"] = {
            "project_id": "proj-003",
            "name": "新产品研发项目",
            "description": "新一代智能终端产品研发",
            "manager": "吴研发",
            "start_date": "2026-02-01",
            "end_date": "2026-12-31",
            "budget": 800000.0,
            "department": "研发部",
            "status": "in_progress",
            "created_at": "2026-02-01T08:00:00+00:00",
        }
        self._milestones["ms-006"] = {
            "milestone_id": "ms-006",
            "project_id": "proj-003",
            "name": "原型设计完成",
            "target_date": "2026-04-30",
            "description": "完成产品原型设计与评审",
            "status": "completed",
            "actual_date": "2026-04-28",
            "notes": "原型评审通过",
            "created_at": "2026-02-01T08:00:00+00:00",
        }
        self._milestones["ms-007"] = {
            "milestone_id": "ms-007",
            "project_id": "proj-003",
            "name": "工程样机完成",
            "target_date": "2026-08-31",
            "description": "完成工程样机制作与功能验证",
            "status": "in_progress",
            "actual_date": None,
            "notes": "",
            "created_at": "2026-02-01T08:00:00+00:00",
        }
        self._milestones["ms-008"] = {
            "milestone_id": "ms-008",
            "project_id": "proj-003",
            "name": "量产准备完成",
            "target_date": "2026-12-31",
            "description": "完成量产工装、测试与试产",
            "status": "pending",
            "actual_date": None,
            "notes": "",
            "created_at": "2026-02-01T08:00:00+00:00",
        }
        self._members["proj-003"] = [
            {"name": "吴研发", "role": "项目经理", "department": "研发部"},
            {"name": "郑硬件", "role": "硬件工程师", "department": "研发部"},
            {"name": "王软件", "role": "软件工程师", "department": "研发部"},
        ]

        # 种子成本记录
        self._costs = [
            {
                "cost_id": "cost-001",
                "project_id": "proj-001",
                "category": "software_license",
                "amount": 120000.0,
                "description": "ERP软件许可费用",
                "date": "2026-01-15",
                "created_at": "2026-01-15T08:00:00+00:00",
            },
            {
                "cost_id": "cost-002",
                "project_id": "proj-001",
                "category": "consulting",
                "amount": 80000.0,
                "description": "实施咨询服务费",
                "date": "2026-03-01",
                "created_at": "2026-03-01T08:00:00+00:00",
            },
            {
                "cost_id": "cost-003",
                "project_id": "proj-002",
                "category": "design",
                "amount": 45000.0,
                "description": "视觉设计外包费用",
                "date": "2026-03-20",
                "created_at": "2026-03-20T08:00:00+00:00",
            },
            {
                "cost_id": "cost-004",
                "project_id": "proj-003",
                "category": "material",
                "amount": 200000.0,
                "description": "研发物料采购",
                "date": "2026-02-15",
                "created_at": "2026-02-15T08:00:00+00:00",
            },
        ]

    async def initialize(self):
        """初始化Agent：订阅项目与销售确认事件"""
        self.subscribe_events(
            event_types=[
                EventType.PROJECT_CREATED,
                EventType.PROJECT_MILESTONE_REACHED,
                EventType.PROJECT_COMPLETED,
                EventType.SALES_ORDER_CONFIRMED,
            ],
            handler=self._handle_event,
        )
        logger.info("project_agent.initialized", agent=self.name,
                    projects=len(self._projects),
                    milestones=len(self._milestones))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("project_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.PROJECT_CREATED:
            await self._handle_project_created(event.event_data)

        elif event.event_type == EventType.PROJECT_MILESTONE_REACHED:
            await self._handle_milestone_reached(event.event_data)

        elif event.event_type == EventType.PROJECT_COMPLETED:
            await self._handle_project_completed(event.event_data)

        elif event.event_type == EventType.SALES_ORDER_CONFIRMED:
            await self._handle_sales_confirmed(event.event_data)

    async def _handle_project_created(self, data: dict):
        """处理项目创建事件：同步项目状态"""
        project_id = data.get("project_id")
        if project_id and project_id in self._projects:
            self._projects[project_id]["status"] = "in_progress"
            logger.info("project_agent.created_synced", project_id=project_id)

    async def _handle_milestone_reached(self, data: dict):
        """处理里程碑达成事件：同步里程碑状态"""
        milestone_id = data.get("milestone_id")
        if milestone_id and milestone_id in self._milestones:
            milestone = self._milestones[milestone_id]
            milestone["status"] = "completed"
            milestone["actual_date"] = data.get("actual_date",
                                                 datetime.now(timezone.utc).strftime("%Y-%m-%d"))
            logger.info("project_agent.milestone_reached_synced",
                        milestone_id=milestone_id)

            # 检查项目是否所有里程碑完成
            project_id = milestone.get("project_id")
            if project_id:
                await self._check_project_completion(project_id)

    async def _handle_project_completed(self, data: dict):
        """处理项目完成事件：同步项目状态"""
        project_id = data.get("project_id")
        if project_id and project_id in self._projects:
            self._projects[project_id]["status"] = "completed"
            self._projects[project_id]["completed_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
            logger.info("project_agent.completed_synced", project_id=project_id)

    async def _handle_sales_confirmed(self, data: dict):
        """
        处理销售订单确认事件：自动创建交付项目

        销售订单确认后，自动创建对应的交付/实施项目。
        """
        order_id = data.get("order_id", data.get("sales_order_id", ""))
        customer = data.get("customer_name", data.get("customer", ""))
        product = data.get("product_name", data.get("product", ""))
        amount = data.get("total_amount", data.get("amount", 0.0))

        if not order_id:
            return

        # 避免重复创建
        existing = any(
            p.get("source_order_id") == order_id for p in self._projects.values()
        )
        if existing:
            return

        # 预算按订单金额的15%估算交付成本
        estimated_budget = max(amount * 0.15, 50000.0) if amount > 0 else 50000.0

        project = self.create_project(
            name=f"交付项目-{customer}-{product}",
            description=f"销售订单{order_id}对应的交付实施项目",
            manager="待分配",
            start_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            end_date="",
            budget=estimated_budget,
            department="交付部",
        )
        project["source_order_id"] = order_id

        logger.info("project_agent.auto_created_from_sales",
                    project_id=project["project_id"], order_id=order_id)

    async def _check_project_completion(self, project_id: str):
        """检查项目是否所有里程碑已完成"""
        milestones = self.get_milestones(project_id)
        if not milestones:
            return

        all_completed = all(m.get("status") == "completed" for m in milestones)
        if all_completed:
            project = self._projects.get(project_id)
            if project and project.get("status") != "completed":
                logger.info("project_agent.all_milestones_completed",
                            project_id=project_id)
                # 自动更新为待验收状态
                project["status"] = "pending_acceptance"

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出项目决策"""
        action = context.get("action", "")

        if action == "complete":
            project_id = context.get("project_id")
            project = self._projects.get(project_id)
            if not project:
                return AgentDecision(
                    decision_type="error",
                    reasoning=f"项目{project_id}不存在",
                    action="无法做出项目完成决策",
                    confidence=0.0,
                )
            progress = self.get_progress(project_id)
            return AgentDecision(
                decision_type="project_completion",
                reasoning=(
                    f"项目{project_id}（{project['name']}）申请完成，"
                    f"里程碑完成率{progress.get('milestone_completion_rate', 0):.1%}，"
                    f"预算使用率{progress.get('budget_usage_rate', 0):.1%}，"
                    f"需人工验收确认"
                ),
                action=f"请求人工验收项目：{project_id}",
                confidence=0.9,
                requires_human_approval=self.HUMAN_APPROVAL_ON_COMPLETE,
                context={
                    "project_id": project_id,
                    "progress": progress,
                },
            )

        if action == "budget_warning":
            project_id = context.get("project_id")
            project = self._projects.get(project_id)
            if not project:
                return AgentDecision(
                    decision_type="error",
                    reasoning=f"项目{project_id}不存在",
                    action="无法做出预算决策",
                    confidence=0.0,
                )
            return AgentDecision(
                decision_type="budget_warning",
                reasoning=(
                    f"项目{project_id}（{project['name']}）预算使用率"
                    f"超过预警阈值{self.BUDGET_WARNING_THRESHOLD:.0%}"
                ),
                action=f"发布预算超支预警：{project_id}",
                confidence=0.85,
                requires_human_approval=False,
                context=context,
            )

        # 默认：项目查询决策
        project_id = context.get("project_id")
        project = self._projects.get(project_id)
        if project:
            progress = self.get_progress(project_id)
            return AgentDecision(
                decision_type="project_status",
                reasoning=(
                    f"项目{project_id}（{project['name']}）当前状态"
                    f"{project['status']}，里程碑完成率"
                    f"{progress.get('milestone_completion_rate', 0):.1%}"
                ),
                action=f"查询项目状态：{project_id}",
                confidence=0.85,
                requires_human_approval=False,
                context={"project_id": project_id, "progress": progress},
            )

        return AgentDecision(
            decision_type="project_query",
            reasoning="项目查询操作",
            action="查询项目信息",
            confidence=0.8,
            requires_human_approval=False,
            context=context,
        )

    async def on_tick(self):
        """周期性任务：检查预算使用率与项目进度"""
        for project_id, project in self._projects.items():
            if project.get("status") != "in_progress":
                continue
            progress = self.get_progress(project_id)
            budget_usage = progress.get("budget_usage_rate", 0.0)
            if budget_usage >= self.BUDGET_WARNING_THRESHOLD:
                logger.warning("project_agent.budget_warning",
                               project_id=project_id,
                               name=project["name"],
                               budget_usage=budget_usage)

    # ========== 项目管理 ==========

    def get_projects(self, status: Optional[str] = None,
                     manager: Optional[str] = None) -> list[dict]:
        """获取项目列表，支持按状态与项目经理过滤"""
        projects = list(self._projects.values())
        if status:
            projects = [p for p in projects if p.get("status") == status]
        if manager:
            projects = [p for p in projects if p.get("manager") == manager]
        return projects

    def get_project(self, project_id: str) -> Optional[dict]:
        """获取单个项目详情"""
        return self._projects.get(project_id)

    def create_project(self, name: str, description: str, manager: str,
                       start_date: str, end_date: str, budget: float,
                       department: str) -> dict:
        """
        创建项目

        参数：
        - name: 项目名称
        - description: 项目描述
        - manager: 项目经理
        - start_date: 开始日期
        - end_date: 结束日期
        - budget: 预算
        - department: 所属部门
        """
        project_id = f"PRJ-{str(uuid4())[:8]}"
        project = {
            "project_id": project_id,
            "name": name,
            "description": description,
            "manager": manager,
            "start_date": start_date,
            "end_date": end_date,
            "budget": budget,
            "department": department,
            "status": "planning",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._projects[project_id] = project
        self._members[project_id] = []

        logger.info("project_agent.project_created",
                    project_id=project_id, name=name, manager=manager)
        return project

    async def publish_project_created_event(self, project: dict):
        """发布项目创建事件（供异步调用）"""
        await self._publish_event(
            EventType.PROJECT_CREATED,
            {
                "project_id": project["project_id"],
                "name": project["name"],
                "manager": project["manager"],
                "budget": project["budget"],
                "department": project["department"],
            },
        )

    async def update_project_status(self, project_id: str, status: str) -> dict:
        """
        更新项目状态

        参数：
        - project_id: 项目ID
        - status: 新状态（planning/in_progress/pending_acceptance/completed/cancelled）

        流程：
        1. 更新项目状态
        2. 状态变更为completed时发布项目完成事件
        3. 项目完成需人在回路确认
        """
        project = self._projects.get(project_id)
        if not project:
            return {"error": f"项目{project_id}不存在"}

        old_status = project.get("status")
        project["status"] = status

        # 记录决策
        decision = AgentDecision(
            decision_type="project_status_update",
            reasoning=(
                f"项目{project_id}（{project['name']}）状态从"
                f"{old_status}变更为{status}"
            ),
            action=f"更新项目状态：{project_id} -> {status}",
            confidence=0.9,
            requires_human_approval=(status == "completed" and self.HUMAN_APPROVAL_ON_COMPLETE),
            context={
                "project_id": project_id,
                "old_status": old_status,
                "new_status": status,
            },
        )
        await self.record_decision(decision)

        # 状态变更为completed时发布事件
        if status == "completed":
            project["completed_at"] = datetime.now(timezone.utc).isoformat()
            await self._publish_event(
                EventType.PROJECT_COMPLETED,
                {
                    "project_id": project_id,
                    "name": project["name"],
                    "manager": project["manager"],
                    "budget": project["budget"],
                    "completed_at": project["completed_at"],
                    "decision_id": decision.decision_id,
                },
            )

        logger.info("project_agent.status_updated",
                    project_id=project_id, old_status=old_status, new_status=status)
        return project

    # ========== 里程碑管理 ==========

    def get_milestones(self, project_id: str) -> list[dict]:
        """获取项目的里程碑列表"""
        return [
            m for m in self._milestones.values()
            if m.get("project_id") == project_id
        ]

    def get_milestone(self, milestone_id: str) -> Optional[dict]:
        """获取单个里程碑详情"""
        return self._milestones.get(milestone_id)

    def create_milestone(self, project_id: str, name: str,
                         target_date: str, description: str) -> dict:
        """
        创建里程碑

        参数：
        - project_id: 所属项目ID
        - name: 里程碑名称
        - target_date: 目标日期
        - description: 描述
        """
        milestone_id = f"MS-{str(uuid4())[:8]}"
        milestone = {
            "milestone_id": milestone_id,
            "project_id": project_id,
            "name": name,
            "target_date": target_date,
            "description": description,
            "status": "pending",
            "actual_date": None,
            "notes": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._milestones[milestone_id] = milestone

        logger.info("project_agent.milestone_created",
                    milestone_id=milestone_id, project_id=project_id, name=name)
        return milestone

    async def complete_milestone(self, milestone_id: str, actual_date: str,
                                 notes: str) -> dict:
        """
        完成里程碑

        参数：
        - milestone_id: 里程碑ID
        - actual_date: 实际完成日期
        - notes: 完成备注

        流程：
        1. 更新里程碑状态为已完成
        2. 发布里程碑达成事件
        3. 检查项目是否所有里程碑完成
        """
        milestone = self._milestones.get(milestone_id)
        if not milestone:
            return {"error": f"里程碑{milestone_id}不存在"}

        milestone["status"] = "completed"
        milestone["actual_date"] = actual_date
        milestone["notes"] = notes

        # 记录决策
        decision = AgentDecision(
            decision_type="milestone_completion",
            reasoning=(
                f"里程碑{milestone_id}（{milestone['name']}）已完成，"
                f"实际完成日期{actual_date}"
            ),
            action=f"完成里程碑：{milestone_id}",
            confidence=0.9,
            requires_human_approval=False,
            context={
                "milestone_id": milestone_id,
                "project_id": milestone.get("project_id"),
                "actual_date": actual_date,
            },
        )
        await self.record_decision(decision)

        # 发布里程碑达成事件
        await self._publish_event(
            EventType.PROJECT_MILESTONE_REACHED,
            {
                "milestone_id": milestone_id,
                "project_id": milestone.get("project_id"),
                "name": milestone["name"],
                "target_date": milestone["target_date"],
                "actual_date": actual_date,
                "notes": notes,
                "decision_id": decision.decision_id,
            },
        )

        logger.info("project_agent.milestone_completed",
                    milestone_id=milestone_id, actual_date=actual_date)

        # 检查项目是否所有里程碑完成
        project_id = milestone.get("project_id")
        if project_id:
            await self._check_project_completion(project_id)

        return milestone

    # ========== 成本管理 ==========

    async def record_cost(self, project_id: str, category: str,
                          amount: float, description: str, date: str) -> dict:
        """
        记录项目成本

        参数：
        - project_id: 项目ID
        - category: 成本类别（material/labor/consulting/software_license/design/travel/other）
        - amount: 金额
        - description: 描述
        - date: 发生日期

        流程：
        1. 记录成本
        2. 检查预算使用率，超阈值发布预警
        """
        project = self._projects.get(project_id)
        if not project:
            return {"error": f"项目{project_id}不存在"}

        cost_id = f"COST-{str(uuid4())[:8]}"
        cost = {
            "cost_id": cost_id,
            "project_id": project_id,
            "category": category,
            "amount": amount,
            "description": description,
            "date": date,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._costs.append(cost)

        # 检查预算使用率
        total_cost = sum(c["amount"] for c in self._costs
                         if c["project_id"] == project_id)
        budget = project.get("budget", 0)
        budget_usage = (total_cost / budget) if budget > 0 else 0.0

        # 记录决策
        decision = AgentDecision(
            decision_type="project_cost_record",
            reasoning=(
                f"项目{project_id}（{project['name']}）记录成本"
                f"{category}类别{amount:.2f}元，累计成本{total_cost:.2f}元，"
                f"预算使用率{budget_usage:.1%}"
            ),
            action=f"记录项目成本：{project_id}，{category}，{amount:.2f}元",
            confidence=0.9,
            requires_human_approval=False,
            context={
                "project_id": project_id,
                "cost_id": cost_id,
                "category": category,
                "amount": amount,
                "budget_usage": budget_usage,
            },
        )
        await self.record_decision(decision)

        # 预算超支预警
        if budget_usage >= self.BUDGET_WARNING_THRESHOLD:
            logger.warning("project_agent.budget_exceeded_warning",
                           project_id=project_id,
                           name=project["name"],
                           budget_usage=budget_usage,
                           total_cost=total_cost, budget=budget)

        logger.info("project_agent.cost_recorded",
                    cost_id=cost_id, project_id=project_id,
                    category=category, amount=amount)
        return cost

    def get_costs(self, project_id: Optional[str] = None,
                  category: Optional[str] = None) -> list[dict]:
        """获取成本记录，支持按项目与类别过滤"""
        costs = list(self._costs)
        if project_id:
            costs = [c for c in costs if c.get("project_id") == project_id]
        if category:
            costs = [c for c in costs if c.get("category") == category]
        return costs

    # ========== 进度计算 ==========

    def get_progress(self, project_id: str) -> dict:
        """
        获取项目进度

        返回：含里程碑完成率、预算使用率、进度偏差等
        """
        project = self._projects.get(project_id)
        if not project:
            return {"error": f"项目{project_id}不存在"}

        milestones = self.get_milestones(project_id)
        total_milestones = len(milestones)
        completed_milestones = len([m for m in milestones if m.get("status") == "completed"])
        milestone_completion_rate = (
            (completed_milestones / total_milestones) if total_milestones > 0 else 0.0
        )

        # 预算使用率
        total_cost = sum(c["amount"] for c in self._costs
                         if c["project_id"] == project_id)
        budget = project.get("budget", 0)
        budget_usage_rate = (total_cost / budget) if budget > 0 else 0.0
        budget_remaining = budget - total_cost

        # 进度偏差（基于时间）
        schedule_variance = self._calculate_schedule_variance(project)

        # 里程碑详情
        milestone_details = []
        for m in milestones:
            target = m.get("target_date", "")
            actual = m.get("actual_date", "")
            is_delayed = False
            if m.get("status") == "completed" and target and actual:
                try:
                    target_dt = datetime.strptime(target, "%Y-%m-%d")
                    actual_dt = datetime.strptime(actual, "%Y-%m-%d")
                    is_delayed = actual_dt > target_dt
                except (ValueError, TypeError):
                    pass
            elif m.get("status") != "completed" and target:
                try:
                    target_dt = datetime.strptime(target, "%Y-%m-%d")
                    if datetime.now(timezone.utc) > target_dt.replace(tzinfo=timezone.utc):
                        is_delayed = True
                except (ValueError, TypeError):
                    pass

            milestone_details.append({
                "milestone_id": m["milestone_id"],
                "name": m["name"],
                "status": m["status"],
                "target_date": target,
                "actual_date": actual,
                "is_delayed": is_delayed,
            })

        return {
            "project_id": project_id,
            "project_name": project["name"],
            "project_status": project["status"],
            "total_milestones": total_milestones,
            "completed_milestones": completed_milestones,
            "milestone_completion_rate": round(milestone_completion_rate, 4),
            "budget": budget,
            "total_cost": round(total_cost, 2),
            "budget_remaining": round(budget_remaining, 2),
            "budget_usage_rate": round(budget_usage_rate, 4),
            "budget_warning": budget_usage_rate >= self.BUDGET_WARNING_THRESHOLD,
            "schedule_variance": schedule_variance,
            "milestones": milestone_details,
        }

    def _calculate_schedule_variance(self, project: dict) -> dict:
        """计算进度偏差（基于时间）"""
        start_date_str = project.get("start_date", "")
        end_date_str = project.get("end_date", "")
        now = datetime.now(timezone.utc)

        if not start_date_str:
            return {"status": "unknown", "description": "缺少开始日期"}

        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return {"status": "unknown", "description": "开始日期格式无效"}

        if not end_date_str:
            return {"status": "unknown", "description": "缺少结束日期"}

        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return {"status": "unknown", "description": "结束日期格式无效"}

        total_duration = (end_date - start_date).total_seconds()
        if total_duration <= 0:
            return {"status": "invalid", "description": "项目周期无效"}

        elapsed = (now - start_date).total_seconds()
        expected_progress = max(0.0, min(1.0, elapsed / total_duration))

        # 实际进度基于里程碑完成率
        milestones = self.get_milestones(project["project_id"])
        total_ms = len(milestones)
        completed_ms = len([m for m in milestones if m.get("status") == "completed"])
        actual_progress = (completed_ms / total_ms) if total_ms > 0 else 0.0

        variance = actual_progress - expected_progress

        if variance < -0.1:
            status = "behind"
            description = f"进度落后{abs(variance):.1%}"
        elif variance > 0.1:
            status = "ahead"
            description = f"进度超前{variance:.1%}"
        else:
            status = "on_track"
            description = "进度正常"

        return {
            "status": status,
            "description": description,
            "expected_progress": round(expected_progress, 4),
            "actual_progress": round(actual_progress, 4),
            "variance": round(variance, 4),
        }

    # ========== 统计 ==========

    def get_stats(self) -> dict:
        """
        获取项目统计

        返回：含项目总数、状态分布、预算统计等
        """
        projects = list(self._projects.values())
        milestones = list(self._milestones.values())
        costs = list(self._costs)

        # 按状态统计
        by_status = {}
        for p in projects:
            s = p.get("status", "unknown")
            by_status[s] = by_status.get(s, 0) + 1

        # 按部门统计
        by_department = {}
        for p in projects:
            d = p.get("department", "unknown")
            by_department[d] = by_department.get(d, 0) + 1

        # 预算统计
        total_budget = sum(p.get("budget", 0) for p in projects)
        total_cost = sum(c.get("amount", 0) for c in costs)

        # 里程碑统计
        completed_milestones = len([m for m in milestones if m.get("status") == "completed"])
        pending_milestones = len([m for m in milestones if m.get("status") == "pending"])
        in_progress_milestones = len([m for m in milestones if m.get("status") == "in_progress"])

        # 预算超支项目数
        over_budget_count = 0
        budget_warning_count = 0
        for p in projects:
            pid = p["project_id"]
            project_cost = sum(c["amount"] for c in costs if c["project_id"] == pid)
            budget = p.get("budget", 0)
            if budget > 0:
                usage = project_cost / budget
                if usage >= 1.0:
                    over_budget_count += 1
                elif usage >= self.BUDGET_WARNING_THRESHOLD:
                    budget_warning_count += 1

        return {
            "total_projects": len(projects),
            "by_status": by_status,
            "by_department": by_department,
            "total_milestones": len(milestones),
            "completed_milestones": completed_milestones,
            "in_progress_milestones": in_progress_milestones,
            "pending_milestones": pending_milestones,
            "total_budget": round(total_budget, 2),
            "total_cost": round(total_cost, 2),
            "over_budget_projects": over_budget_count,
            "budget_warning_projects": budget_warning_count,
            "total_cost_records": len(costs),
        }
