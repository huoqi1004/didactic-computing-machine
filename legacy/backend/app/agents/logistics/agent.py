# -*- coding: utf-8 -*-
"""
物流Agent
管理发货和物流追踪，进行路线优化和成本控制。
监听生产订单与采购订单事件，发布发货与交付事件。

职责：
- 监听 PRODUCTION_ORDER_CREATED 事件（成品发货）
- 监听 PURCHASE_ORDER_APPROVED 事件（采购入库物流）
- 管理发货和物流追踪
- 路线优化和成本控制
- 发布 SHIPMENT_DISPATCHED 和 DELIVERY_CONFIRMED 事件
- 异常物流事件需人工确认
"""
import structlog
from typing import Optional
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class LogisticsAgent(BaseAgent):
    """
    物流Agent

    职责：
    - 监听生产订单创建事件，安排成品发货
    - 监听采购订单审批事件，安排采购入库物流
    - 路线优化和成本控制
    - 物流异常需人在回路确认
    - 发布发货与交付事件
    """

    # 默认运输费率（元/公里）
    DEFAULT_TRANSPORT_RATE = 2.0
    # 默认运输距离（公里）
    DEFAULT_DISTANCE = 100
    # 高成本运输阈值（超过1万需人工审批）
    HIGH_COST_THRESHOLD = 10_000.0

    def __init__(self):
        super().__init__(
            name="物流Agent",
            domain="logistics",
            capabilities=[
                "shipment_tracking",        # 物流追踪
                "route_optimization",        # 路线优化
                "delivery_management",       # 交付管理
                "cost_optimization",         # 成本优化
                "general",
            ],
            security_clearance="internal",
        )
        # 发货记录：shipment_id -> 发货详情
        self._shipments: dict[str, dict] = {}
        # 路线记录：route_id -> 路线详情
        self._routes: dict[str, dict] = {}
        # 可用仓库（模拟）
        self._warehouses = {
            "wh-east": {"name": "华东仓", "location": "上海"},
            "wh-south": {"name": "华南仓", "location": "广州"},
            "wh-north": {"name": "华北仓", "location": "北京"},
        }

    @property
    def description(self) -> str:
        return "管理发货与物流追踪，优化运输路线，控制物流成本"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅生产订单与采购订单事件"""
        self.subscribe_events(
            event_types=[
                EventType.PRODUCTION_ORDER_CREATED,
                EventType.PURCHASE_ORDER_APPROVED,
            ],
            handler=self._handle_event,
        )
        logger.info("logistics_agent.initialized", agent=self.name,
                    warehouses=len(self._warehouses))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("logistics_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.PRODUCTION_ORDER_CREATED:
            await self._arrange_outbound_shipment(event.event_data)

        elif event.event_type == EventType.PURCHASE_ORDER_APPROVED:
            await self._arrange_inbound_shipment(event.event_data)

    async def _arrange_outbound_shipment(self, order_data: dict) -> Optional[dict]:
        """安排成品出库发货"""
        order_id = order_data.get("order_id", "")
        product_id = order_data.get("product_id", "")
        product_name = order_data.get("product_name", "")
        quantity = order_data.get("quantity", 0)
        assigned_line = order_data.get("assigned_line", "")

        if quantity <= 0:
            return None

        return await self._create_shipment(
            shipment_type="outbound",
            source_ref=order_id,
            product_id=product_id,
            product_name=product_name,
            quantity=quantity,
            origin=assigned_line,
        )

    async def _arrange_inbound_shipment(self, po_data: dict) -> Optional[dict]:
        """安排采购入库物流"""
        po_id = po_data.get("po_id", "")
        supplier_id = po_data.get("supplier_id", "")

        return await self._create_shipment(
            shipment_type="inbound",
            source_ref=po_id,
            product_id=po_data.get("product_id", ""),
            product_name=po_data.get("product_name", ""),
            quantity=po_data.get("quantity", 0),
            origin=supplier_id,
        )

    async def _create_shipment(
        self, shipment_type: str, source_ref: str,
        product_id: str, product_name: str, quantity: int,
        origin: str = "",
    ) -> Optional[dict]:
        """
        创建发货记录

        LLM优先进行路线优化，降级到规则（选择默认仓库与最短路线）。
        """
        # 路线优化（LLM优先，降级到规则）
        route = await self._optimize_route(product_id, product_name, quantity, origin)

        shipment_id = f"SHP-{str(uuid4())[:8]}"
        transport_cost = route.get("transport_cost", 0.0)
        requires_human_approval = transport_cost > self.HIGH_COST_THRESHOLD

        shipment = {
            "shipment_id": shipment_id,
            "shipment_type": shipment_type,
            "source_ref": source_ref,
            "product_id": product_id,
            "product_name": product_name,
            "quantity": quantity,
            "origin": origin,
            "destination": route.get("destination", ""),
            "route_id": route.get("route_id", ""),
            "transport_cost": transport_cost,
            "estimated_days": route.get("estimated_days", 3),
            "status": "dispatched",
            "requires_human_approval": requires_human_approval,
            "method": route.get("method", "rules"),
            "reasoning": route.get("reasoning", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expected_delivery": (
                datetime.now(timezone.utc) +
                timedelta(days=route.get("estimated_days", 3))
            ).isoformat(),
        }
        self._shipments[shipment_id] = shipment

        # 记录决策
        decision = AgentDecision(
            decision_type="shipment_dispatch",
            reasoning=route.get("reasoning", "发货安排"),
            action=(
                f"安排发货{shipment_id}：{product_name} {quantity}件，"
                f"目的地{route.get('destination', '')}，运费{transport_cost:.2f}元"
            ),
            confidence=route.get("confidence", 0.8),
            requires_human_approval=requires_human_approval,
            context={
                "shipment_id": shipment_id,
                "transport_cost": transport_cost,
                "destination": route.get("destination", ""),
            },
        )
        await self.record_decision(decision)

        # 发布发货事件
        await self._publish_event(
            EventType.SHIPMENT_DISPATCHED,
            {**shipment, "decision_id": decision.decision_id},
        )

        logger.info("logistics_agent.shipment_dispatched",
                    shipment_id=shipment_id, product=product_name,
                    destination=route.get("destination", ""),
                    cost=transport_cost,
                    method=route.get("method", "rules"))
        return shipment

    # ========== 路线优化 ==========

    async def _optimize_route(
        self, product_id: str, product_name: str, quantity: int, origin: str,
    ) -> dict:
        """
        路线优化

        LLM优先（注入仓库与货物数据），降级到规则（选择第一个仓库）。
        """
        llm_result = await self._optimize_route_llm(
            product_id, product_name, quantity, origin
        )
        if llm_result is not None:
            return llm_result
        return self._optimize_route_rules(product_id, quantity, origin)

    async def _optimize_route_llm(
        self, product_id: str, product_name: str, quantity: int, origin: str,
    ) -> Optional[dict]:
        """
        使用LLM进行路线优化

        返回None表示LLM不可用，调用方应降级到规则模式。
        """
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        context = {
            "product_id": product_id,
            "product_name": product_name,
            "quantity": quantity,
            "origin": origin,
            "warehouses": list(self._warehouses.values()),
            "transport_rate": self.DEFAULT_TRANSPORT_RATE,
        }
        prompt = (
            "请优化运输路线，选择最优目的仓库。返回JSON，包含 "
            "destination(仓库名), distance(公里), transport_cost, "
            "estimated_days, route_id, confidence(0-1), reasoning, method(llm)。"
        )

        try:
            result = await self.llm_analyze(prompt, context)
        except Exception as e:
            logger.warning("logistics_agent.llm_route_error",
                           agent=self.name, error=str(e))
            return None

        if "error" in result:
            logger.info("logistics_agent.llm_route_unavailable",
                        agent=self.name, reason=result.get("error"))
            return None

        if "destination" not in result:
            return None

        try:
            result["transport_cost"] = float(result.get("transport_cost", 0))
            result["estimated_days"] = int(result.get("estimated_days", 3))
            result["confidence"] = float(result.get("confidence", 0.8))
        except (TypeError, ValueError):
            return None

        if "route_id" not in result:
            result["route_id"] = f"RTE-{str(uuid4())[:8]}"

        # 记录路线
        self._routes[result["route_id"]] = {
            "route_id": result["route_id"],
            "destination": result["destination"],
            "distance": result.get("distance", self.DEFAULT_DISTANCE),
            "transport_cost": result["transport_cost"],
            "method": "llm",
        }
        result["method"] = "llm"
        result.setdefault("reasoning", "LLM路线优化")

        logger.info("logistics_agent.llm_route_success",
                    agent=self.name, destination=result["destination"])
        return result

    def _optimize_route_rules(
        self, product_id: str, quantity: int, origin: str,
    ) -> dict:
        """
        规则路线优化（降级方案）

        选择第一个仓库作为目的地，按默认距离与费率计算成本。
        """
        # 选择第一个仓库
        wh_id = next(iter(self._warehouses))
        warehouse = self._warehouses[wh_id]
        destination = warehouse["name"]
        distance = self.DEFAULT_DISTANCE
        transport_cost = round(distance * self.DEFAULT_TRANSPORT_RATE, 2)
        route_id = f"RTE-{str(uuid4())[:8]}"
        estimated_days = max(distance // 50, 1)

        self._routes[route_id] = {
            "route_id": route_id,
            "destination": destination,
            "distance": distance,
            "transport_cost": transport_cost,
            "method": "rules",
        }

        return {
            "route_id": route_id,
            "destination": destination,
            "distance": distance,
            "transport_cost": transport_cost,
            "estimated_days": estimated_days,
            "confidence": 0.8,
            "reasoning": (
                f"基于规则路线优化：选择{destination}，"
                f"距离{distance}公里，运费{transport_cost}元，"
                f"预计{estimated_days}天到达"
            ),
            "method": "rules",
        }

    # ========== 交付确认 ==========

    async def confirm_delivery(self, shipment_id: str) -> bool:
        """
        确认交付（人类监督员或系统调用）

        将发货状态更新为已交付，发布交付确认事件。
        """
        shipment = self._shipments.get(shipment_id)
        if not shipment:
            return False

        shipment["status"] = "delivered"
        shipment["delivered_at"] = datetime.now(timezone.utc).isoformat()

        await self._publish_event(
            EventType.DELIVERY_CONFIRMED,
            {
                "shipment_id": shipment_id,
                "product_id": shipment["product_id"],
                "product_name": shipment["product_name"],
                "quantity": shipment["quantity"],
                "destination": shipment["destination"],
                "status": "delivered",
                "delivered_at": shipment["delivered_at"],
            },
        )

        logger.info("logistics_agent.delivery_confirmed",
                    shipment_id=shipment_id,
                    product=shipment["product_name"])
        return True

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出物流决策"""
        shipment_id = context.get("shipment_id")
        shipment = self._shipments.get(shipment_id)

        if not shipment:
            return AgentDecision(
                decision_type="error",
                reasoning=f"发货记录{shipment_id}不存在",
                action="无法做出物流决策",
                confidence=0.0,
            )

        transport_cost = shipment["transport_cost"]
        if transport_cost > self.HIGH_COST_THRESHOLD:
            return AgentDecision(
                decision_type="logistics_approval",
                reasoning=(
                    f"发货{shipment_id}运费{transport_cost:.2f}元超过"
                    f"高成本阈值{self.HIGH_COST_THRESHOLD}元，需人在回路确认"
                ),
                action=f"请求人工审批：发货{shipment_id}",
                confidence=0.9,
                requires_human_approval=True,
                context={"shipment_id": shipment_id},
            )

        return AgentDecision(
            decision_type="logistics_auto",
            reasoning=(
                f"发货{shipment_id}运费{transport_cost:.2f}元在阈值内，自动批准"
            ),
            action=f"自动批准发货{shipment_id}",
            confidence=0.95,
            requires_human_approval=False,
            context={"shipment_id": shipment_id},
        )

    async def on_tick(self):
        """周期性任务：检查在途发货是否超期"""
        now = datetime.now(timezone.utc)
        for shipment_id, shipment in self._shipments.items():
            if shipment["status"] != "dispatched":
                continue
            expected_str = shipment.get("expected_delivery", "")
            if not expected_str:
                continue
            try:
                expected_date = datetime.fromisoformat(expected_str)
            except (ValueError, TypeError):
                continue
            if now > expected_date:
                shipment["status"] = "exception"
                await self._publish_event(
                    EventType.DELIVERY_EXCEPTION,
                    {
                        "shipment_id": shipment_id,
                        "product_name": shipment["product_name"],
                        "expected_delivery": expected_str,
                        "reason": "delivery_overdue",
                    },
                )
                logger.warning("logistics_agent.delivery_overdue",
                               shipment_id=shipment_id,
                               product=shipment["product_name"])

    # ========== 查询接口 ==========

    def get_shipments(self, status: Optional[str] = None) -> list[dict]:
        """获取发货记录列表"""
        shipments = list(self._shipments.values())
        if status:
            shipments = [s for s in shipments if s["status"] == status]
        return shipments

    def get_routes(self) -> list[dict]:
        """获取路线记录列表"""
        return list(self._routes.values())
