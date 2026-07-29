# -*- coding: utf-8 -*-
"""
动态调度Agent
动态调度跨Agent任务，进行资源分配和冲突解决。
监听Agent决策与状态变化事件，发布调度决策事件。

职责：
- 监听 AGENT_DECISION_MADE 事件（任务调度）
- 监听 AGENT_STATE_CHANGED 事件（资源重新分配）
- 动态调度跨Agent任务
- 资源分配和冲突解决
- 优先级管理：紧急采购、大额支付、合规违规优先处理
- 发布 SCHEDULING_DECISION 和 PRIORITY_UPDATED 事件
"""
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4
from collections import defaultdict

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class DynamicSchedulerAgent(BaseAgent):
    """
    动态调度Agent

    职责：
    - 监听Agent决策与状态变化事件，动态调度跨Agent任务
    - 资源分配和冲突解决
    - 优先级管理：紧急采购、大额支付、合规违规优先处理
    - 高优先级调度冲突需人工确认
    """

    # 优先级映射：决策类型 -> 优先级（1-10，10最高）
    PRIORITY_MAP = {
        "compliance_violation": 10,       # 合规违规最高优先级
        "supply_risk_warning": 9,          # 供应风险
        "demand_anomaly": 8,              # 需求异常
        "procurement_consensus": 8,       # 大额采购共识
        "procurement_approval": 7,        # 采购审批
        "fund_transfer_approval": 7,      # 大额资金调拨
        "production_approval": 7,         # 生产审批
        "logistics_approval": 6,          # 物流审批
        "tax_declaration": 5,            # 税务申报
        "replenishment_proposal": 5,      # 补货建议
        "purchase_order_generation": 4,  # 采购订单生成
        "production_order_creation": 4,  # 生产订单创建
        "shipment_dispatch": 3,          # 发货
        "tax_calculation": 3,            # 税务计算
        "demand_forecast": 2,            # 需求预测
        "supplier_evaluation": 2,        # 供应商评估
    }
    # 默认优先级
    DEFAULT_PRIORITY = 3
    # 高优先级阈值（超过此值需人工确认调度冲突）
    HIGH_PRIORITY_THRESHOLD = 8

    def __init__(self):
        super().__init__(
            name="动态调度Agent",
            domain="scheduler",
            capabilities=[
                "task_scheduling",           # 任务调度
                "resource_allocation",       # 资源分配
                "priority_management",       # 优先级管理
                "conflict_resolution",       # 冲突解决
                "general",
            ],
            security_clearance="internal",
        )
        # 调度计划：task_id -> 调度详情
        self._schedule: dict[str, dict] = {}
        # 优先级记录：task_id -> 优先级详情
        self._priorities: dict[str, dict] = {}
        # 资源分配：resource_name -> 分配详情
        self._resource_allocation: dict[str, dict] = {}
        # 待处理任务队列（按优先级排序）
        self._pending_tasks: list[dict] = []

    @property
    def description(self) -> str:
        return "动态调度跨Agent任务，管理资源分配与优先级，解决调度冲突"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅Agent决策与状态变化事件"""
        self.subscribe_events(
            event_types=[
                EventType.AGENT_DECISION_MADE,
                EventType.AGENT_STATE_CHANGED,
            ],
            handler=self._handle_event,
        )
        logger.info("scheduler_agent.initialized", agent=self.name)

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("scheduler_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.AGENT_DECISION_MADE:
            await self._schedule_task(event.event_data)

        elif event.event_type == EventType.AGENT_STATE_CHANGED:
            await self._handle_state_change(event.event_data)

    async def _schedule_task(self, decision_data: dict):
        """
        调度Agent决策产生的任务

        根据决策类型分配优先级，安排执行顺序。
        """
        decision_type = decision_data.get("decision_type", "")
        decision_id = decision_data.get("decision_id", "")
        agent_name = decision_data.get("context", {}).get("agent_name", "")

        # 计算优先级（LLM优先，降级到规则）
        priority = await self._determine_priority(decision_type, decision_data)

        task_id = f"TASK-{str(uuid4())[:8]}"
        task = {
            "task_id": task_id,
            "decision_id": decision_id,
            "decision_type": decision_type,
            "priority": priority,
            "source_agent": agent_name,
            "status": "scheduled",
            "scheduled_at": datetime.now(timezone.utc).isoformat(),
            "decision_data": decision_data,
        }
        self._schedule[task_id] = task

        # 记录优先级
        self._priorities[task_id] = {
            "task_id": task_id,
            "decision_type": decision_type,
            "priority": priority,
            "method": "rules" if priority == self.PRIORITY_MAP.get(
                decision_type, self.DEFAULT_PRIORITY
            ) else "llm",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        # 分配资源
        await self._allocate_resource(task_id, decision_type, priority)

        # 记录决策
        requires_human = priority >= self.HIGH_PRIORITY_THRESHOLD
        decision = AgentDecision(
            decision_type="scheduling",
            reasoning=(
                f"调度任务{task_id}：决策类型'{decision_type}'，"
                f"优先级{priority}，来源Agent'{agent_name}'"
            ),
            action=f"安排任务{task_id}执行（优先级{priority}）",
            confidence=0.85,
            requires_human_approval=requires_human,
            context={
                "task_id": task_id,
                "decision_type": decision_type,
                "priority": priority,
            },
        )
        await self.record_decision(decision)

        # 发布调度决策事件
        await self._publish_event(
            EventType.SCHEDULING_DECISION,
            {
                "task_id": task_id,
                "decision_id": decision_id,
                "decision_type": decision_type,
                "priority": priority,
                "source_agent": agent_name,
                "status": "scheduled",
                "scheduled_at": task["scheduled_at"],
                "decision_id_sched": decision.decision_id,
            },
        )

        # 发布优先级更新事件
        await self._publish_event(
            EventType.PRIORITY_UPDATED,
            {
                "task_id": task_id,
                "decision_type": decision_type,
                "priority": priority,
                "updated_at": task["scheduled_at"],
            },
        )

        # 添加到待处理队列并按优先级排序
        self._pending_tasks.append(task)
        self._pending_tasks.sort(key=lambda t: t["priority"], reverse=True)

        logger.info("scheduler_agent.task_scheduled",
                    task_id=task_id, decision_type=decision_type,
                    priority=priority, agent=agent_name)

    async def _handle_state_change(self, state_data: dict):
        """处理Agent状态变化，重新分配资源"""
        agent_id = state_data.get("agent_id", "")
        new_state = state_data.get("state", "")
        reason = state_data.get("reason", "")

        # 当Agent暂停或熔断时，重新分配其任务
        if new_state in ("paused", "fused", "human_takeover"):
            logger.info("scheduler_agent.reallocating",
                        agent_id=agent_id, state=new_state, reason=reason)
            # 标记相关任务需要重新调度
            for task_id, task in self._schedule.items():
                if task.get("source_agent") == agent_id and task["status"] == "scheduled":
                    task["status"] = "pending_reallocation"
                    task["reallocation_reason"] = f"agent_{new_state}"

    # ========== 优先级管理 ==========

    async def _determine_priority(self, decision_type: str, decision_data: dict) -> int:
        """
        确定任务优先级

        LLM优先（注入决策上下文），降级到规则（基于决策类型映射表）。
        """
        llm_priority = await self._determine_priority_llm(decision_type, decision_data)
        if llm_priority is not None:
            return llm_priority
        return self.PRIORITY_MAP.get(decision_type, self.DEFAULT_PRIORITY)

    async def _determine_priority_llm(
        self, decision_type: str, decision_data: dict,
    ) -> Optional[int]:
        """
        使用LLM确定任务优先级

        返回None表示LLM不可用，调用方应降级到规则模式。
        """
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        context = {
            "decision_type": decision_type,
            "decision_data": {
                k: v for k, v in decision_data.items()
                if k in ("decision_type", "reasoning", "action", "confidence",
                         "requires_human_approval", "context")
            },
            "priority_map": self.PRIORITY_MAP,
        }
        prompt = (
            "请确定该任务的优先级（1-10，10最高）。"
            "返回JSON，包含 priority(整数), reasoning, method(llm)。"
        )

        try:
            result = await self.llm_analyze(prompt, context)
        except Exception as e:
            logger.warning("scheduler_agent.llm_priority_error",
                           agent=self.name, error=str(e))
            return None

        if "error" in result:
            logger.info("scheduler_agent.llm_priority_unavailable",
                        agent=self.name, reason=result.get("error"))
            return None

        if "priority" not in result:
            return None

        try:
            priority = int(result["priority"])
        except (TypeError, ValueError):
            return None

        if 1 <= priority <= 10:
            return priority
        return None

    # ========== 资源分配 ==========

    async def _allocate_resource(
        self, task_id: str, decision_type: str, priority: int,
    ):
        """分配资源给任务"""
        # 根据决策类型确定所需资源
        resource_map = {
            "purchase_order_generation": "procurement_pool",
            "procurement_approval": "procurement_pool",
            "procurement_consensus": "procurement_pool",
            "production_order_creation": "production_pool",
            "production_approval": "production_pool",
            "shipment_dispatch": "logistics_pool",
            "logistics_approval": "logistics_pool",
            "fund_transfer_approval": "treasury_pool",
            "tax_calculation": "finance_pool",
            "tax_declaration": "finance_pool",
            "supplier_evaluation": "supply_pool",
            "demand_forecast": "analytics_pool",
        }

        resource_name = resource_map.get(decision_type, "general_pool")

        if resource_name not in self._resource_allocation:
            self._resource_allocation[resource_name] = {
                "resource_name": resource_name,
                "total_capacity": 10,
                "allocated": 0,
                "tasks": [],
            }

        allocation = self._resource_allocation[resource_name]
        allocation["allocated"] += 1
        allocation["tasks"].append({
            "task_id": task_id,
            "decision_type": decision_type,
            "priority": priority,
            "allocated_at": datetime.now(timezone.utc).isoformat(),
        })

        logger.info("scheduler_agent.resource_allocated",
                    task_id=task_id, resource=resource_name,
                    priority=priority)

    # ========== 冲突解决 ==========

    async def _resolve_conflicts(self) -> list[dict]:
        """
        检测并解决调度冲突

        当多个高优先级任务竞争同一资源时，按优先级排序并标记冲突。
        """
        conflicts = []
        for resource_name, allocation in self._resource_allocation.items():
            if allocation["allocated"] <= allocation["total_capacity"]:
                continue

            # 资源超载，标记冲突
            conflict = {
                "conflict_id": f"CFL-{str(uuid4())[:8]}",
                "resource_name": resource_name,
                "allocated": allocation["allocated"],
                "capacity": allocation["total_capacity"],
                "description": (
                    f"资源'{resource_name}'超载：已分配{allocation['allocated']}，"
                    f"容量{allocation['total_capacity']}"
                ),
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }
            conflicts.append(conflict)

            # 按优先级排序，低优先级任务延后
            tasks = sorted(allocation["tasks"], key=lambda t: t["priority"], reverse=True)
            for task in tasks[allocation["total_capacity"]:]:
                task_id = task["task_id"]
                if task_id in self._schedule:
                    self._schedule[task_id]["status"] = "deferred"
                    self._schedule[task_id]["defer_reason"] = "resource_conflict"

            logger.warning("scheduler_agent.conflict_detected",
                           resource=resource_name,
                           allocated=allocation["allocated"],
                           capacity=allocation["total_capacity"])

        return conflicts

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出调度决策"""
        task_id = context.get("task_id")
        task = self._schedule.get(task_id)

        if not task:
            return AgentDecision(
                decision_type="error",
                reasoning=f"调度任务{task_id}不存在",
                action="无法做出调度决策",
                confidence=0.0,
            )

        priority = task["priority"]
        if priority >= self.HIGH_PRIORITY_THRESHOLD:
            return AgentDecision(
                decision_type="priority_scheduling",
                reasoning=(
                    f"任务{task_id}优先级{priority}为高优先级，"
                    f"需优先执行并人工确认"
                ),
                action=f"优先执行任务{task_id}（优先级{priority}）",
                confidence=0.9,
                requires_human_approval=True,
                context={"task_id": task_id, "priority": priority},
            )

        return AgentDecision(
            decision_type="normal_scheduling",
            reasoning=(
                f"任务{task_id}优先级{priority}，按正常顺序调度"
            ),
            action=f"正常调度任务{task_id}",
            confidence=0.85,
            requires_human_approval=False,
            context={"task_id": task_id, "priority": priority},
        )

    async def on_tick(self):
        """周期性任务：检测并解决调度冲突"""
        await self._resolve_conflicts()

    # ========== 查询接口 ==========

    def get_schedule(self, status: Optional[str] = None) -> list[dict]:
        """获取调度计划列表"""
        tasks = list(self._schedule.values())
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        return tasks

    def get_priorities(self, decision_type: Optional[str] = None) -> list[dict]:
        """获取优先级记录列表"""
        priorities = list(self._priorities.values())
        if decision_type:
            priorities = [p for p in priorities if p.get("decision_type") == decision_type]
        return priorities

    def get_resource_allocation(self) -> list[dict]:
        """获取资源分配列表"""
        return list(self._resource_allocation.values())
