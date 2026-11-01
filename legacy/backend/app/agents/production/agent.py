# -*- coding: utf-8 -*-
"""
生产Agent
根据需求预测制定生产计划，管理BOM（物料清单）和产能。
监听补货审批与库存低预警事件，发布生产订单事件。

职责：
- 监听 REPLENISHMENT_APPROVED 事件（触发生产计划）
- 监听 STOCK_LOW_WARNING 事件（库存低预警触发补产）
- 根据需求预测和BOM制定生产计划
- 管理产能分配
- 大额生产订单（>50万）需人工审批
- 发布 PRODUCTION_ORDER_CREATED 事件
"""
import structlog
from typing import Optional
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class ProductionAgent(BaseAgent):
    """
    生产Agent

    职责：
    - 监听补货审批与库存低预警事件，自动生成生产订单
    - 管理BOM（物料清单）和产能
    - 大额生产订单（>50万）需人在回路确认
    - 生产计划基于需求预测和BOM展开
    """

    # 大额生产订单阈值（超过50万需人工审批）
    HUMAN_APPROVAL_THRESHOLD = 500_000.0
    # 默认单件生产成本
    DEFAULT_UNIT_COST = 50.0

    def __init__(self):
        super().__init__(
            name="生产Agent",
            domain="production",
            capabilities=[
                "production_planning",       # 生产计划
                "capacity_management",       # 产能管理
                "bom_management",            # BOM管理
                "quality_control",           # 质量控制
                "general",
            ],
            security_clearance="confidential",
        )
        # 生产订单：order_id -> 订单详情
        self._production_orders: dict[str, dict] = {}
        # BOM（物料清单）：product_id -> 物料列表
        self._bom: dict[str, list[dict]] = {}
        # 产能：产线 -> 产能详情
        self._capacity: dict[str, dict] = {}
        # 初始化种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "根据需求预测制定生产计划，管理BOM与产能，控制生产质量"

    # ========== 初始化 ==========

    def _seed_data(self):
        """注入种子BOM与产能数据"""
        # BOM种子数据
        self._bom = {
            "prod-001": [
                {"material_id": "mat-001", "material_name": "原材料A", "quantity": 2, "unit_cost": 5.0},
                {"material_id": "mat-002", "material_name": "原材料B", "quantity": 1, "unit_cost": 10.0},
            ],
            "prod-002": [
                {"material_id": "mat-001", "material_name": "原材料A", "quantity": 3, "unit_cost": 5.0},
                {"material_id": "mat-003", "material_name": "原材料C", "quantity": 1, "unit_cost": 15.0},
            ],
            "prod-003": [
                {"material_id": "mat-002", "material_name": "原材料B", "quantity": 2, "unit_cost": 10.0},
                {"material_id": "mat-004", "material_name": "原材料D", "quantity": 1, "unit_cost": 20.0},
            ],
        }
        # 产能种子数据
        self._capacity = {
            "line-001": {
                "line_id": "line-001",
                "line_name": "一号产线",
                "daily_capacity": 500,
                "utilized": 0,
                "status": "idle",
            },
            "line-002": {
                "line_id": "line-002",
                "line_name": "二号产线",
                "daily_capacity": 800,
                "utilized": 0,
                "status": "idle",
            },
            "line-003": {
                "line_id": "line-003",
                "line_name": "三号产线",
                "daily_capacity": 300,
                "utilized": 0,
                "status": "idle",
            },
        }

    async def initialize(self):
        """初始化Agent：订阅补货审批与库存低预警事件"""
        self.subscribe_events(
            event_types=[
                EventType.REPLENISHMENT_APPROVED,
                EventType.STOCK_LOW_WARNING,
            ],
            handler=self._handle_event,
        )
        logger.info("production_agent.initialized", agent=self.name,
                    bom_products=len(self._bom),
                    production_lines=len(self._capacity))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("production_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.REPLENISHMENT_APPROVED:
            await self._create_production_order(event.event_data)

        elif event.event_type == EventType.STOCK_LOW_WARNING:
            await self._handle_stock_low(event.event_data)

    async def _create_production_order(self, replenishment_data: dict) -> Optional[dict]:
        """
        根据补货审批数据创建生产订单

        流程：
        1. 从补货数据提取产品与数量
        2. 使用LLM制定生产计划（降级到规则）
        3. 检查BOM与产能
        4. 生成生产订单
        5. 大额订单（>50万）需人工审批
        6. 发布 PRODUCTION_ORDER_CREATED 事件
        """
        product_id = replenishment_data.get("product_id", "")
        product_name = replenishment_data.get("product_name", "")
        quantity = replenishment_data.get("reorder_quantity",
                                          replenishment_data.get("quantity", 0))
        if quantity <= 0:
            logger.warning("production_agent.invalid_quantity",
                           product=product_id, quantity=quantity)
            return None

        # 生产计划（LLM优先，降级到规则）
        plan = await self._plan_production(product_id, product_name, quantity)

        # 生成生产订单
        order_id = f"PRD-{str(uuid4())[:8]}"
        unit_cost = plan.get("unit_cost", self.DEFAULT_UNIT_COST)
        total_cost = unit_cost * quantity
        line_id = plan.get("assigned_line", "")
        requires_human_approval = total_cost > self.HUMAN_APPROVAL_THRESHOLD

        production_order = {
            "order_id": order_id,
            "product_id": product_id,
            "product_name": product_name,
            "quantity": quantity,
            "unit_cost": unit_cost,
            "total_cost": total_cost,
            "assigned_line": line_id,
            "bom": plan.get("bom", []),
            "estimated_days": plan.get("estimated_days", 0),
            "status": "pending_approval" if requires_human_approval else "approved",
            "requires_human_approval": requires_human_approval,
            "source": "replenishment_approved",
            "reasoning": plan.get("reasoning", ""),
            "method": plan.get("method", "rules"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expected_completion": (
                datetime.now(timezone.utc) + timedelta(days=plan.get("estimated_days", 7))
            ).isoformat(),
        }
        self._production_orders[order_id] = production_order

        # 记录决策
        decision = AgentDecision(
            decision_type="production_order_creation",
            reasoning=plan.get("reasoning", "生产订单创建"),
            action=(
                f"创建生产订单{order_id}：{product_name} {quantity}件，"
                f"产线{line_id}，总成本{total_cost:.2f}元"
            ),
            confidence=plan.get("confidence", 0.8),
            requires_human_approval=requires_human_approval,
            context={
                "order_id": order_id,
                "product_id": product_id,
                "quantity": quantity,
                "total_cost": total_cost,
                "assigned_line": line_id,
            },
        )
        await self.record_decision(decision)

        # 发布生产订单创建事件
        await self._publish_event(
            EventType.PRODUCTION_ORDER_CREATED,
            {**production_order, "decision_id": decision.decision_id},
        )

        # 分配产能
        if line_id and line_id in self._capacity:
            self._capacity[line_id]["utilized"] += quantity
            self._capacity[line_id]["status"] = "running"

        logger.info("production_agent.order_created",
                    order_id=order_id, product=product_name,
                    quantity=quantity, total_cost=total_cost,
                    status=production_order["status"],
                    method=plan.get("method", "rules"))
        return production_order

    async def _handle_stock_low(self, stock_data: dict):
        """处理库存低预警事件，生成补产订单"""
        product_id = stock_data.get("product_id", "")
        product_name = stock_data.get("product_name", "")
        safety_stock = stock_data.get("safety_stock", 50)
        reorder_point = stock_data.get("reorder_point", 100)

        # 简化：按安全库存的2倍补产
        quantity = max(safety_stock * 2 - reorder_point, safety_stock)

        await self._create_production_order({
            "product_id": product_id,
            "product_name": product_name,
            "reorder_quantity": quantity,
        })

    # ========== 生产计划 ==========

    async def _plan_production(
        self, product_id: str, product_name: str, quantity: int,
    ) -> dict:
        """
        制定生产计划

        LLM优先（注入BOM与产能数据），降级到规则计划。
        """
        llm_result = await self._plan_production_llm(
            product_id, product_name, quantity
        )
        if llm_result is not None:
            return llm_result
        return self._plan_production_rules(product_id, product_name, quantity)

    async def _plan_production_llm(
        self, product_id: str, product_name: str, quantity: int,
    ) -> Optional[dict]:
        """
        使用LLM制定生产计划

        返回None表示LLM不可用，调用方应降级到规则模式。
        """
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        bom = self._bom.get(product_id, [])
        available_lines = [
            {
                "line_id": l["line_id"],
                "line_name": l["line_name"],
                "daily_capacity": l["daily_capacity"],
                "available": l["daily_capacity"] - l["utilized"],
            }
            for l in self._capacity.values()
        ]

        context = {
            "product_id": product_id,
            "product_name": product_name,
            "quantity": quantity,
            "bom": bom,
            "available_lines": available_lines,
        }
        prompt = (
            "请制定生产计划。返回JSON，包含 assigned_line, "
            "estimated_days, unit_cost, confidence(0-1), reasoning, method(llm)。"
        )

        try:
            result = await self.llm_analyze(prompt, context)
        except Exception as e:
            logger.warning("production_agent.llm_plan_error",
                           agent=self.name, error=str(e))
            return None

        if "error" in result:
            logger.info("production_agent.llm_plan_unavailable",
                        agent=self.name, reason=result.get("error"))
            return None

        if "assigned_line" not in result:
            return None

        try:
            result["estimated_days"] = int(result.get("estimated_days", 7))
            result["unit_cost"] = float(result.get("unit_cost", self.DEFAULT_UNIT_COST))
            result["confidence"] = float(result.get("confidence", 0.8))
        except (TypeError, ValueError):
            return None

        result["bom"] = bom
        result.setdefault("reasoning", "LLM生产计划")
        result["method"] = "llm"

        logger.info("production_agent.llm_plan_success",
                    agent=self.name, product=product_id,
                    line=result["assigned_line"])
        return result

    def _plan_production_rules(
        self, product_id: str, product_name: str, quantity: int,
    ) -> dict:
        """
        规则生产计划（降级方案）

        基于BOM计算物料需求，选择可用产能最大的产线。
        """
        bom = self._bom.get(product_id, [])
        # 计算单位成本（BOM物料成本之和）
        if bom:
            unit_cost = sum(m["quantity"] * m["unit_cost"] for m in bom)
        else:
            unit_cost = self.DEFAULT_UNIT_COST

        # 选择可用产能最大的产线
        best_line = None
        best_available = 0
        for line_id, line in self._capacity.items():
            available = line["daily_capacity"] - line["utilized"]
            if available > best_available:
                best_available = available
                best_line = line_id

        assigned_line = best_line or "line-001"
        daily_capacity = self._capacity.get(assigned_line, {}).get("daily_capacity", 500)
        estimated_days = max((quantity + daily_capacity - 1) // daily_capacity, 1)

        return {
            "assigned_line": assigned_line,
            "estimated_days": estimated_days,
            "unit_cost": unit_cost,
            "bom": bom,
            "confidence": 0.8,
            "reasoning": (
                f"基于规则计划：产品'{product_name}'需生产{quantity}件，"
                f"分配至{self._capacity.get(assigned_line, {}).get('line_name', assigned_line)}，"
                f"预计{estimated_days}天完成，单件成本{unit_cost:.2f}元"
            ),
            "method": "rules",
        }

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出生产决策"""
        order_id = context.get("order_id")
        order = self._production_orders.get(order_id)

        if not order:
            return AgentDecision(
                decision_type="error",
                reasoning=f"生产订单{order_id}不存在",
                action="无法做出生产决策",
                confidence=0.0,
            )

        total_cost = order["total_cost"]
        if total_cost > self.HUMAN_APPROVAL_THRESHOLD:
            return AgentDecision(
                decision_type="production_approval",
                reasoning=(
                    f"生产订单{order_id}总成本{total_cost:.2f}元超过"
                    f"人工审批阈值{self.HUMAN_APPROVAL_THRESHOLD}元，需人在回路确认"
                ),
                action=f"请求人工审批：生产订单{order_id}",
                confidence=0.9,
                requires_human_approval=True,
                context={"order_id": order_id},
            )

        return AgentDecision(
            decision_type="production_auto",
            reasoning=(
                f"生产订单{order_id}总成本{total_cost:.2f}元在阈值内，自动批准"
            ),
            action=f"自动批准生产订单{order_id}",
            confidence=0.95,
            requires_human_approval=False,
            context={"order_id": order_id},
        )

    async def on_tick(self):
        """周期性任务：检查产能利用率"""
        for line_id, line in self._capacity.items():
            utilization = line["utilized"] / line["daily_capacity"] if line["daily_capacity"] > 0 else 0
            if utilization > 0.9:
                line["status"] = "overloaded"
                logger.warning("production_agent.capacity_overload",
                               line=line["line_name"],
                               utilization=utilization)
            elif utilization > 0.5:
                line["status"] = "busy"
            elif utilization > 0:
                line["status"] = "running"
            else:
                line["status"] = "idle"

    # ========== 查询接口 ==========

    def get_production_orders(self, status: Optional[str] = None) -> list[dict]:
        """获取生产订单列表"""
        orders = list(self._production_orders.values())
        if status:
            orders = [o for o in orders if o["status"] == status]
        return orders

    def get_bom(self, product_id: Optional[str] = None) -> dict:
        """获取BOM（物料清单）"""
        if product_id:
            return {product_id: self._bom.get(product_id, [])}
        return dict(self._bom)

    def get_capacity(self) -> list[dict]:
        """获取产能状态"""
        return list(self._capacity.values())
