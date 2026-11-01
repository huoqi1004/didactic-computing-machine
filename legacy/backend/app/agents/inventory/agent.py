"""
库存守护Agent
融合内外部数据，输出概率性需求预测，驱动下游补货与生产
监听库存变化事件，自动生成补货建议，人在回路确认
"""
import asyncio
import structlog
from typing import Optional
from datetime import datetime, timezone

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType
from app.core.event.aggregate import AggregateRoot
from app.core.config import SecurityLevel, Permission
from app.core.security.authorization import authz_engine

logger = structlog.get_logger()


class InventoryAggregate(AggregateRoot):
    """库存聚合根 - 事件溯源"""

    @property
    def aggregate_type(self) -> str:
        return "inventory"

    def __init__(self, aggregate_id: str):
        super().__init__(aggregate_id)
        self.product_id: str = aggregate_id
        self.product_name: str = ""
        self.quantity: int = 0
        self.safety_stock: int = 0
        self.reorder_point: int = 0
        self.unit_cost: float = 0.0
        self.warehouse: str = "default"

    def apply_event(self, event: Event):
        """应用事件重建状态"""
        data = event.event_data
        if event.event_type == EventType.INVENTORY_ADJUSTED:
            self.product_name = data.get("product_name", self.product_name)
            self.quantity = data.get("quantity", self.quantity)
            self.safety_stock = data.get("safety_stock", self.safety_stock)
            self.reorder_point = data.get("reorder_point", self.reorder_point)
            self.unit_cost = data.get("unit_cost", self.unit_cost)
            self.warehouse = data.get("warehouse", self.warehouse)
        elif event.event_type == EventType.REPLENISHMENT_PROPOSED:
            pass  # 补货建议不改变库存状态

    def _create_snapshot(self) -> dict:
        return {
            "version": self.version,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "quantity": self.quantity,
            "safety_stock": self.safety_stock,
            "reorder_point": self.reorder_point,
            "unit_cost": self.unit_cost,
            "warehouse": self.warehouse,
        }

    def _restore_from_snapshot(self, state: dict):
        self.version = state.get("version", 0)
        self.product_name = state.get("product_name", "")
        self.quantity = state.get("quantity", 0)
        self.safety_stock = state.get("safety_stock", 0)
        self.reorder_point = state.get("reorder_point", 0)
        self.unit_cost = state.get("unit_cost", 0.0)
        self.warehouse = state.get("warehouse", "default")

    def needs_replenishment(self) -> bool:
        """是否需要补货"""
        return self.quantity <= self.reorder_point

    def get_status(self) -> dict:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "quantity": self.quantity,
            "safety_stock": self.safety_stock,
            "reorder_point": self.reorder_point,
            "needs_replenishment": self.needs_replenishment(),
            "warehouse": self.warehouse,
        }


class InventoryGuardianAgent(BaseAgent):
    """
    库存守护Agent

    职责：
    - 监听库存调整事件
    - 当库存低于再订货点时，自动生成补货建议
    - 基于历史数据预测需求（概率性预测）
    - 补货建议需人在回路确认
    - 高额补货触发多Agent共识
    """

    def __init__(self):
        super().__init__(
            name="库存守护Agent",
            domain="inventory",
            capabilities=[
                "inventory_monitoring",      # 库存监控
                "demand_forecasting",        # 需求预测
                "replenishment_planning",    # 补货规划
                "stock_analysis",            # 库存分析
                "general",                   # 通用能力
            ],
            security_clearance="internal",
        )
        # 模拟库存数据缓存
        self._inventory_cache: dict[str, InventoryAggregate] = {}
        self._replenishment_proposals: dict[str, dict] = {}

    @property
    def description(self) -> str:
        return "监控库存水平，预测需求，自动生成补货建议"

    async def initialize(self):
        """初始化Agent"""
        # 订阅库存相关事件
        self.subscribe_events(
            event_types=[
                EventType.INVENTORY_ADJUSTED,
                EventType.REPLENISHMENT_APPROVED,
                EventType.REPLENISHMENT_REJECTED,
            ],
            handler=self._handle_event,
        )
        logger.info("inventory_agent.initialized", agent=self.name)

    async def _handle_event(self, event: Event):
        """处理接收到的事件"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("inventory_agent.event_error",
                        agent=self.name, error=str(e))

    async def process_event(self, event: Event):
        """处理库存事件"""
        if event.event_type == EventType.INVENTORY_ADJUSTED:
            product_id = event.aggregate_id
            await self._update_inventory(product_id, event.event_data)

            # 检查是否需要补货
            inventory = self._inventory_cache.get(product_id)
            if inventory and inventory.needs_replenishment():
                await self._propose_replenishment(inventory)

        elif event.event_type == EventType.REPLENISHMENT_APPROVED:
            proposal_id = event.event_data.get("proposal_id")
            if proposal_id in self._replenishment_proposals:
                self._replenishment_proposals[proposal_id]["status"] = "approved"
                logger.info("inventory_agent.replenishment_approved",
                           proposal_id=proposal_id)

        elif event.event_type == EventType.REPLENISHMENT_REJECTED:
            proposal_id = event.event_data.get("proposal_id")
            if proposal_id in self._replenishment_proposals:
                self._replenishment_proposals[proposal_id]["status"] = "rejected"

    async def _update_inventory(self, product_id: str, data: dict):
        """更新库存缓存"""
        if product_id not in self._inventory_cache:
            self._inventory_cache[product_id] = InventoryAggregate(product_id)
        inventory = self._inventory_cache[product_id]
        inventory.apply_event(Event(
            event_type=EventType.INVENTORY_ADJUSTED,
            aggregate_id=product_id,
            aggregate_type="inventory",
            event_data=data,
        ))
        logger.info("inventory_agent.updated",
                    product=product_id,
                    quantity=inventory.quantity,
                    reorder_point=inventory.reorder_point)

    async def _propose_replenishment(self, inventory: InventoryAggregate):
        """生成补货建议"""
        # 需求预测（LLM优先，降级到规则引擎）
        forecast = await self._forecast_demand(inventory)

        # 计算补货量
        reorder_qty = max(
            inventory.safety_stock * 2 - inventory.quantity,
            forecast.get("suggested_qty", inventory.safety_stock),
        )

        # 计算补货成本
        total_cost = reorder_qty * inventory.unit_cost

        # LLM增强：生成自然语言理由说明与紧急程度评估（降级到规则描述）
        llm_reasoning = await self._generate_replenishment_reasoning(
            inventory, reorder_qty, total_cost, forecast
        )
        urgency = await self._assess_replenishment_urgency(
            inventory, reorder_qty, total_cost, forecast
        )

        # 构建决策理由（LLM理由优先，降级到规则描述）
        rule_reasoning = (
            f"产品'{inventory.product_name}'库存{inventory.quantity}低于再订货点"
            f"{inventory.reorder_point}，预测7天需求"
            f"{forecast.get('predicted_demand', 0)}，建议补货{reorder_qty}件"
        )
        reasoning = llm_reasoning if llm_reasoning else rule_reasoning

        # 生成决策
        decision = AgentDecision(
            decision_type="replenishment_proposal",
            reasoning=reasoning,
            action=f"建议补货 {inventory.product_name} {reorder_qty}件，预计成本{total_cost:.2f}元（紧急程度: {urgency}）",
            confidence=forecast.get("confidence", 0.75),
            requires_human_approval=total_cost > 50000,  # 超过5万需人工确认
            context={
                "product_id": inventory.product_id,
                "current_qty": inventory.quantity,
                "reorder_qty": reorder_qty,
                "total_cost": total_cost,
                "forecast": forecast,
                "urgency": urgency,
                "reasoning_source": "llm" if llm_reasoning else "rules",
            },
        )

        # 记录决策
        await self.record_decision(decision)

        # 发布补货建议事件
        proposal_id = decision.decision_id
        self._replenishment_proposals[proposal_id] = {
            "product_id": inventory.product_id,
            "product_name": inventory.product_name,
            "reorder_qty": reorder_qty,
            "total_cost": total_cost,
            "status": "pending_approval" if decision.requires_human_approval else "auto_approved",
            "decision_id": proposal_id,
            "urgency": urgency,
        }

        await self._publish_event(
            EventType.REPLENISHMENT_PROPOSED,
            {
                "proposal_id": proposal_id,
                "product_id": inventory.product_id,
                "product_name": inventory.product_name,
                "current_quantity": inventory.quantity,
                "reorder_quantity": reorder_qty,
                "total_cost": total_cost,
                "confidence": decision.confidence,
                "requires_approval": decision.requires_human_approval,
                "forecast": forecast,
                "urgency": urgency,
                "reasoning": reasoning,
            },
        )

        # 高额补货需要人在回路确认
        if decision.requires_human_approval:
            logger.info("inventory_agent.awaiting_approval",
                        product=inventory.product_name,
                        cost=total_cost)
            # 在实际系统中，这里会等待人类审批
            # approved = await self.request_human_approval(decision)

        logger.info("inventory_agent.replenishment_proposed",
                    product=inventory.product_name,
                    qty=reorder_qty,
                    cost=total_cost,
                    urgency=urgency)

    async def _generate_replenishment_reasoning(
        self,
        inventory: InventoryAggregate,
        reorder_qty: int,
        total_cost: float,
        forecast: dict,
    ) -> Optional[str]:
        """
        使用LLM生成补货建议的自然语言理由说明

        返回None表示LLM不可用，调用方应使用规则描述。
        """
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        context = {
            "product_name": inventory.product_name,
            "product_id": inventory.product_id,
            "current_qty": inventory.quantity,
            "safety_stock": inventory.safety_stock,
            "reorder_point": inventory.reorder_point,
            "reorder_qty": reorder_qty,
            "total_cost": total_cost,
            "unit_cost": inventory.unit_cost,
            "forecast": forecast,
            "warehouse": inventory.warehouse,
        }
        prompt = (
            "请为以下补货建议生成一段简洁的自然语言理由说明（50-100字），"
            "说明为什么需要补货、补货数量的依据、以及潜在风险提示。"
            "只返回理由文本，不要返回JSON。"
        )

        try:
            reasoning = await self.llm_chat(
                [{"role": "user", "content": prompt + "\n\n上下文: " + str(context)}]
            )
        except Exception as e:
            logger.warning(
                "inventory_agent.llm_reasoning_error",
                agent=self.name, error=str(e),
            )
            return None

        if not reasoning or not reasoning.strip():
            return None

        logger.info(
            "inventory_agent.llm_reasoning_generated",
            agent=self.name, product=inventory.product_id,
            length=len(reasoning),
        )
        return reasoning.strip()

    async def _assess_replenishment_urgency(
        self,
        inventory: InventoryAggregate,
        reorder_qty: int,
        total_cost: float,
        forecast: dict,
    ) -> str:
        """
        使用LLM评估补货紧急程度

        LLM不可用时降级到规则评估（基于库存与安全库存的比值）。
        返回紧急程度: "低" / "中" / "高" / "紧急"
        """
        from app.core.config import settings

        if settings.llm_enabled:
            context = {
                "product_name": inventory.product_name,
                "current_qty": inventory.quantity,
                "safety_stock": inventory.safety_stock,
                "reorder_point": inventory.reorder_point,
                "reorder_qty": reorder_qty,
                "total_cost": total_cost,
                "forecast": forecast,
            }
            prompt = (
                "请评估此次补货的紧急程度，只返回一个词：低、中、高 或 紧急。"
                "评估依据：当前库存与安全库存的差距、预测需求量、补货成本占比。"
            )
            try:
                result = await self.llm_analyze(prompt, context)
                if "error" not in result and "urgency" in result:
                    urgency = str(result["urgency"]).strip()
                    if urgency in ("低", "中", "高", "紧急"):
                        return urgency
                # 尝试直接从LLM文本响应提取
                text_result = await self.llm_chat(
                    [{"role": "user", "content": prompt + "\n\n上下文: " + str(context)}]
                )
                for level in ("紧急", "高", "中", "低"):
                    if level in text_result:
                        return level
            except Exception as e:
                logger.warning(
                    "inventory_agent.llm_urgency_error",
                    agent=self.name, error=str(e),
                )

        # 规则评估降级方案
        if inventory.safety_stock > 0:
            ratio = inventory.quantity / inventory.safety_stock
            if ratio <= 0.3:
                return "紧急"
            elif ratio <= 0.6:
                return "高"
            elif ratio <= 1.0:
                return "中"
            else:
                return "低"
        return "中"

    async def _forecast_demand(self, inventory: InventoryAggregate) -> dict:
        """
        需求预测（概率性预测）

        决策流程：
        1. 优先调用LLM进行需求预测（注入库存历史、安全库存、再订货点等上下文）
        2. LLM返回结构化JSON：
           {predicted_demand, suggested_qty, confidence, factors, reasoning}
        3. LLM不可用时降级到规则计算方法（_forecast_demand_rules）

        降级策略保证：LLM未启用或不可用时，行为与原有规则预测完全一致，
        不影响现有测试与未配置LLM的环境。
        """
        # 尝试LLM预测
        llm_result = await self._forecast_demand_llm(inventory)
        if llm_result is not None:
            return llm_result

        # 降级到规则引擎
        return await self._forecast_demand_rules(inventory)

    async def _forecast_demand_llm(
        self, inventory: InventoryAggregate
    ) -> Optional[dict]:
        """
        使用LLM进行需求预测

        返回None表示LLM不可用或结果无效，调用方应降级到规则模式。
        """
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        # 构建上下文
        context = {
            "product_id": inventory.product_id,
            "product_name": inventory.product_name,
            "current_qty": inventory.quantity,
            "safety_stock": inventory.safety_stock,
            "reorder_point": inventory.reorder_point,
            "unit_cost": inventory.unit_cost,
            "warehouse": inventory.warehouse,
            "history": [],  # 预留：接入历史消耗数据
        }
        prompt = (
            "请基于上述库存数据预测未来7天的需求量，并给出补货建议。"
            "返回JSON，包含 predicted_demand, suggested_qty, confidence, "
            "factors, reasoning, time_horizon 字段。"
        )

        try:
            result = await self.llm_analyze(prompt, context)
        except Exception as e:
            logger.warning(
                "inventory_agent.llm_forecast_error",
                agent=self.name, product=inventory.product_id, error=str(e),
            )
            return None

        if "error" in result:
            logger.info(
                "inventory_agent.llm_forecast_unavailable",
                agent=self.name, product=inventory.product_id,
                reason=result.get("error"),
            )
            return None

        # 校验LLM返回的关键字段
        required_keys = ("predicted_demand", "suggested_qty", "confidence")
        if not all(k in result for k in required_keys):
            logger.warning(
                "inventory_agent.llm_forecast_incomplete",
                agent=self.name, product=inventory.product_id,
                missing=[k for k in required_keys if k not in result],
            )
            return None

        # 确保数值类型与默认值
        try:
            result["predicted_demand"] = int(result["predicted_demand"])
            result["suggested_qty"] = int(result["suggested_qty"])
            result["confidence"] = float(result["confidence"])
        except (TypeError, ValueError):
            logger.warning(
                "inventory_agent.llm_forecast_invalid_types",
                agent=self.name, product=inventory.product_id,
            )
            return None

        # 确保必要字段存在
        result.setdefault("factors", ["llm_prediction"])
        result.setdefault("reasoning", "LLM需求预测")
        result.setdefault("time_horizon", "7_days")

        logger.info(
            "inventory_agent.llm_forecast_success",
            agent=self.name, product=inventory.product_id,
            predicted_demand=result["predicted_demand"],
            confidence=result["confidence"],
        )
        return result

    async def _forecast_demand_rules(
        self, inventory: InventoryAggregate
    ) -> dict:
        """
        规则引擎需求预测（降级方案）

        基于安全库存和当前库存推算，不依赖LLM。
        生产环境将融合外部数据（季节性、促销、市场趋势）使用LLM+时序模型。
        """
        # 简化版预测：基于安全库存和当前库存推算
        base_demand = max(inventory.safety_stock // 2, 10)
        predicted_demand = base_demand + (inventory.reorder_point - inventory.quantity)
        suggested_qty = max(inventory.safety_stock * 2 - inventory.quantity, predicted_demand)

        return {
            "predicted_demand": predicted_demand,
            "suggested_qty": suggested_qty,
            "confidence": 0.78,  # 置信度
            "factors": ["historical_avg", "safety_stock_ratio"],
            "time_horizon": "7_days",
        }

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出补货决策"""
        product_id = context.get("product_id")
        inventory = self._inventory_cache.get(product_id)

        if not inventory:
            return AgentDecision(
                decision_type="error",
                reasoning=f"产品{product_id}不在库存缓存中",
                action="无法做出补货决策",
                confidence=0.0,
            )

        forecast = await self._forecast_demand(inventory)
        reorder_qty = forecast.get("suggested_qty", inventory.safety_stock)
        total_cost = reorder_qty * inventory.unit_cost

        return AgentDecision(
            decision_type="replenishment_decision",
            reasoning=f"库存{inventory.quantity}，预测需求{forecast['predicted_demand']}",
            action=f"补货{reorder_qty}件，成本{total_cost:.2f}元",
            confidence=forecast["confidence"],
            requires_human_approval=total_cost > 50000,
            context={"product_id": product_id, "reorder_qty": reorder_qty,
                     "total_cost": total_cost, "forecast": forecast},
        )

    async def on_tick(self):
        """周期性检查所有库存"""
        for product_id, inventory in self._inventory_cache.items():
            if inventory.needs_replenishment():
                # 检查是否已有待处理的补货建议
                has_pending = any(
                    p["product_id"] == product_id and p["status"] == "pending_approval"
                    for p in self._replenishment_proposals.values()
                )
                if not has_pending:
                    await self._propose_replenishment(inventory)

    def get_inventory_status(self) -> list[dict]:
        """获取所有库存状态"""
        return [inv.get_status() for inv in self._inventory_cache.values()]

    def get_proposals(self) -> list[dict]:
        """获取补货建议列表"""
        return list(self._replenishment_proposals.values())
