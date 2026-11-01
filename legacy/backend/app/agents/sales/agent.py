"""
销售Agent
负责销售订单管理、销售目标跟踪、销售预测与合同管理
监听销售订单与目标事件，驱动销售全流程闭环
"""
import structlog
from typing import Optional
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.db.repositories.base import has_db, schedule
from app.db.repositories.sales_repository import sales_order_repository
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class SalesAgent(BaseAgent):
    """
    销售Agent

    职责：
    - 销售订单管理（创建、确认、查询）
    - 销售目标管理（设定、跟踪、完成率计算）
    - 销售预测（基于历史数据的趋势预测）
    - 合同跟踪
    - 监听销售订单与目标事件，同步外部变更
    """

    def __init__(self):
        super().__init__(
            name="销售Agent",
            domain="sales",
            capabilities=[
                "order_management",        # 订单管理
                "sales_forecasting",       # 销售预测
                "target_management",       # 目标管理
                "contract_tracking",       # 合同跟踪
            ],
            security_clearance="internal",
        )
        # 内部数据存储
        self._orders: dict[str, dict] = {}            # 销售订单
        self._targets: dict[str, dict] = {}           # 销售目标
        self._forecasts: dict[str, dict] = {}         # 销售预测
        self._contracts: dict[str, dict] = {}         # 合同
        self._seed_data()

    @property
    def description(self) -> str:
        return "管理销售订单、销售目标、销售预测与合同跟踪，驱动销售全流程闭环"

    # ========== 种子数据 ==========

    def _seed_data(self):
        """预置3个销售订单和2个销售目标（季度目标）"""
        now = datetime.now(timezone.utc)
        current_quarter = self._get_quarter(now)

        # 种子销售订单
        seed_orders = [
            {
                "customer_id": "CUST-SEED-001",
                "customer_name": "华盛科技有限公司",
                "items": [
                    {"product_id": "P001", "product_name": "ERP系统许可", "qty": 1, "unit_price": 300000},
                    {"product_id": "P002", "product_name": "实施服务", "qty": 1, "unit_price": 100000},
                ],
                "total_amount": 400000,
                "delivery_date": (now + timedelta(days=30)).isoformat(),
                "status": "confirmed",
            },
            {
                "customer_id": "CUST-SEED-002",
                "customer_name": "恒泰制造集团",
                "items": [
                    {"product_id": "P003", "product_name": "自动化控制系统", "qty": 2, "unit_price": 250000},
                ],
                "total_amount": 500000,
                "delivery_date": (now + timedelta(days=45)).isoformat(),
                "status": "pending",
            },
            {
                "customer_id": "CUST-SEED-003",
                "customer_name": "瑞达金融服务",
                "items": [
                    {"product_id": "P004", "product_name": "风控平台年费", "qty": 1, "unit_price": 200000},
                    {"product_id": "P005", "product_name": "技术支持", "qty": 1, "unit_price": 50000},
                ],
                "total_amount": 250000,
                "delivery_date": (now + timedelta(days=15)).isoformat(),
                "status": "confirmed",
            },
        ]

        for od in seed_orders:
            order_id = f"SO-{str(uuid4())[:8]}"
            self._orders[order_id] = {
                "order_id": order_id,
                "customer_id": od["customer_id"],
                "customer_name": od["customer_name"],
                "items": od["items"],
                "total_amount": od["total_amount"],
                "delivery_date": od["delivery_date"],
                "status": od["status"],
                "created_at": now.isoformat(),
                "confirmed_at": now.isoformat() if od["status"] == "confirmed" else None,
            }

        # 种子销售目标（季度目标）
        seed_targets = [
            {
                "salesperson_id": "SP-001",
                "salesperson_name": "刘洋",
                "period": current_quarter,
                "amount": 2000000,
                "product_line": "企业软件",
            },
            {
                "salesperson_id": "SP-002",
                "salesperson_name": "陈芳",
                "period": current_quarter,
                "amount": 1500000,
                "product_line": "云服务",
            },
        ]

        for tg in seed_targets:
            target_id = f"TGT-{str(uuid4())[:8]}"
            self._targets[target_id] = {
                "target_id": target_id,
                "salesperson_id": tg["salesperson_id"],
                "salesperson_name": tg["salesperson_name"],
                "period": tg["period"],
                "amount": tg["amount"],
                "product_line": tg["product_line"],
                "achieved_amount": 0.0,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }

        # 根据种子订单更新目标完成额（已确认订单计入完成额）
        for order in self._orders.values():
            if order["status"] == "confirmed":
                for target in self._targets.values():
                    if target["salesperson_id"] in ("SP-001", "SP-002"):
                        # 简化：将已确认订单金额平均分配到两个目标
                        target["achieved_amount"] += order["total_amount"] / 2

        logger.info("sales_agent.seeded",
                    orders=len(self._orders),
                    targets=len(self._targets))

    @staticmethod
    def _get_quarter(dt: datetime) -> str:
        """获取季度标识，如 2026Q3"""
        quarter = (dt.month - 1) // 3 + 1
        return f"{dt.year}Q{quarter}"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅销售订单与目标事件"""
        self.subscribe_events(
            event_types=[
                EventType.SALES_ORDER_CREATED,
                EventType.SALES_ORDER_CONFIRMED,
                EventType.SALES_TARGET_UPDATED,
                EventType.OPPORTUNITY_WON,
            ],
            handler=self._handle_event,
        )
        logger.info("sales_agent.initialized", agent=self.name,
                    orders=len(self._orders))

    async def hydrate_from_db(self) -> bool:
        """
        启动水合：DB 优先、内存种子兜底、空库回播种（路线图 2.2 批次一）。
        """
        if not has_db():
            return False

        orders = await sales_order_repository.list_all()
        if orders:
            self._orders = {
                o["order_id"]: {**self._orders.get(o["order_id"], {}), **o}
                for o in orders
            }
        elif self._orders:
            for o in list(self._orders.values()):
                await sales_order_repository.upsert(o, "order_id")
            logger.info("sales_agent.orders_seeded_to_db",
                        count=len(self._orders))

        logger.info("sales_agent.hydrated", orders=len(self._orders))
        return True

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("sales_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        data = event.event_data

        if event.event_type == EventType.SALES_ORDER_CREATED:
            # 外部系统创建的订单，同步到本地（避免重复）
            order_id = data.get("order_id")
            if order_id and order_id not in self._orders:
                self._orders[order_id] = {
                    "order_id": order_id,
                    "customer_id": data.get("customer_id", ""),
                    "customer_name": data.get("customer_name", ""),
                    "items": data.get("items", []),
                    "total_amount": data.get("total_amount", 0.0),
                    "delivery_date": data.get("delivery_date", ""),
                    "status": data.get("status", "pending"),
                    "created_at": data.get("created_at",
                                            datetime.now(timezone.utc).isoformat()),
                    "confirmed_at": None,
                }
                logger.info("sales_agent.order_synced",
                            order_id=order_id, source="event")

        elif event.event_type == EventType.SALES_ORDER_CONFIRMED:
            order_id = data.get("order_id")
            if order_id and order_id in self._orders:
                self._orders[order_id]["status"] = "confirmed"
                self._orders[order_id]["confirmed_at"] = \
                    datetime.now(timezone.utc).isoformat()
                # 更新相关销售目标完成额
                salesperson_id = data.get("salesperson_id")
                if salesperson_id:
                    for target in self._targets.values():
                        if target["salesperson_id"] == salesperson_id:
                            target["achieved_amount"] += \
                                self._orders[order_id]["total_amount"]
                            target["updated_at"] = \
                                datetime.now(timezone.utc).isoformat()
                logger.info("sales_agent.order_confirmed_via_event",
                            order_id=order_id)

        elif event.event_type == EventType.SALES_TARGET_UPDATED:
            target_id = data.get("target_id")
            salesperson_id = data.get("salesperson_id")
            # 更新已有目标或创建新目标
            if target_id and target_id in self._targets:
                target = self._targets[target_id]
                if "amount" in data:
                    target["amount"] = data["amount"]
                if "achieved_amount" in data:
                    target["achieved_amount"] = data["achieved_amount"]
                target["updated_at"] = datetime.now(timezone.utc).isoformat()
            elif salesperson_id:
                # 按salesperson_id匹配
                for target in self._targets.values():
                    if target["salesperson_id"] == salesperson_id:
                        if "amount" in data:
                            target["amount"] = data["amount"]
                        if "achieved_amount" in data:
                            target["achieved_amount"] = data["achieved_amount"]
                        target["updated_at"] = \
                            datetime.now(timezone.utc).isoformat()
                        break
            logger.info("sales_agent.target_updated_via_event",
                        target_id=target_id, salesperson_id=salesperson_id)

        elif event.event_type == EventType.OPPORTUNITY_WON:
            # 商机赢单自动生成销售订单
            customer_id = data.get("customer_id")
            customer_name = data.get("customer_name", "")
            amount = data.get("actual_amount", data.get("amount", 0))
            if customer_id and amount > 0:
                await self.create_order(
                    customer_id=customer_id,
                    items=[{
                        "product_id": data.get("product_id", ""),
                        "product_name": data.get("opportunity_name", "赢单转化"),
                        "qty": 1,
                        "unit_price": amount,
                    }],
                    total_amount=amount,
                    delivery_date=(datetime.now(timezone.utc)
                                   + timedelta(days=30)).isoformat(),
                )
                logger.info("sales_agent.order_auto_created_from_opportunity",
                            customer_id=customer_id, amount=amount)

    # ========== 订单管理 ==========

    def get_orders(self, status: Optional[str] = None,
                   customer_id: Optional[str] = None) -> list[dict]:
        """获取销售订单列表，支持按状态和客户筛选"""
        orders = list(self._orders.values())
        if status:
            orders = [o for o in orders if o.get("status") == status]
        if customer_id:
            orders = [o for o in orders if o.get("customer_id") == customer_id]
        return orders

    def get_order(self, order_id: str) -> Optional[dict]:
        """获取单个订单详情"""
        return self._orders.get(order_id)

    async def create_order(self, customer_id: str, items: list[dict],
                           total_amount: float, delivery_date: str) -> dict:
        """创建销售订单"""
        order_id = f"SO-{str(uuid4())[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        order = {
            "order_id": order_id,
            "customer_id": customer_id,
            "customer_name": "",
            "items": items,
            "total_amount": total_amount,
            "delivery_date": delivery_date,
            "status": "pending",
            "created_at": now,
            "confirmed_at": None,
        }
        self._orders[order_id] = order
        schedule(
            sales_order_repository.upsert(order, "order_id"),
            "sales_order_repo",
        )

        logger.info("sales_agent.order_created",
                    order_id=order_id,
                    customer_id=customer_id,
                    total_amount=total_amount)

        # 发布销售订单创建事件
        await self._publish_event(
            EventType.SALES_ORDER_CREATED,
            {
                "order_id": order_id,
                "customer_id": customer_id,
                "total_amount": total_amount,
                "status": "pending",
                "created_at": now,
            },
        )

        return order

    async def confirm_order(self, order_id: str) -> dict:
        """确认销售订单"""
        order = self._orders.get(order_id)
        if not order:
            raise ValueError(f"订单{order_id}不存在")

        order["status"] = "confirmed"
        order["confirmed_at"] = datetime.now(timezone.utc).isoformat()
        schedule(
            sales_order_repository.update_status(
                order_id, "confirmed",
                confirmed_at=datetime.now(timezone.utc),
            ),
            "sales_order_repo",
        )

        logger.info("sales_agent.order_confirmed", order_id=order_id)

        # 发布订单确认事件
        await self._publish_event(
            EventType.SALES_ORDER_CONFIRMED,
            {
                "order_id": order_id,
                "customer_id": order["customer_id"],
                "total_amount": order["total_amount"],
                "status": "confirmed",
            },
        )

        return order

    # ========== 目标管理 ==========

    def get_targets(self, period: Optional[str] = None,
                    salesperson_id: Optional[str] = None) -> list[dict]:
        """获取销售目标列表，支持按周期和销售人员筛选"""
        targets = list(self._targets.values())
        if period:
            targets = [t for t in targets if t.get("period") == period]
        if salesperson_id:
            targets = [t for t in targets if t.get("salesperson_id") == salesperson_id]
        return targets

    def set_target(self, salesperson_id: str, period: str, amount: float,
                   product_line: str) -> dict:
        """设定销售目标"""
        target_id = f"TGT-{str(uuid4())[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        target = {
            "target_id": target_id,
            "salesperson_id": salesperson_id,
            "salesperson_name": "",
            "period": period,
            "amount": amount,
            "product_line": product_line,
            "achieved_amount": 0.0,
            "created_at": now,
            "updated_at": now,
        }
        self._targets[target_id] = target

        logger.info("sales_agent.target_set",
                    target_id=target_id,
                    salesperson_id=salesperson_id,
                    period=period, amount=amount)
        return target

    def get_target_progress(self, salesperson_id: str, period: str) -> dict:
        """获取目标完成率"""
        targets = [
            t for t in self._targets.values()
            if t.get("salesperson_id") == salesperson_id
            and t.get("period") == period
        ]

        if not targets:
            return {
                "salesperson_id": salesperson_id,
                "period": period,
                "target_amount": 0,
                "achieved_amount": 0,
                "completion_rate": 0.0,
                "found": False,
            }

        total_target = sum(t["amount"] for t in targets)
        total_achieved = sum(t["achieved_amount"] for t in targets)
        completion_rate = (total_achieved / total_target * 100) if total_target > 0 else 0.0

        return {
            "salesperson_id": salesperson_id,
            "period": period,
            "target_amount": total_target,
            "achieved_amount": total_achieved,
            "completion_rate": round(completion_rate, 2),
            "remaining": max(0, total_target - total_achieved),
            "found": True,
        }

    # ========== 销售预测 ==========

    def forecast_sales(self, period: str, method: str = "historical") -> dict:
        """
        销售预测

        基于历史订单数据预测未来销售额。
        method:
        - historical: 基于历史订单均值预测
        - trend: 基于趋势线性回归预测
        """
        now = datetime.now(timezone.utc)
        forecast_id = f"FCST-{str(uuid4())[:8]}"

        # 收集历史已确认订单金额
        confirmed_orders = [
            o for o in self._orders.values()
            if o.get("status") == "confirmed"
        ]

        if not confirmed_orders:
            forecast = {
                "forecast_id": forecast_id,
                "period": period,
                "method": method,
                "predicted_amount": 0.0,
                "confidence": 0.3,
                "data_points": 0,
                "reasoning": "无历史已确认订单数据，预测置信度低",
                "created_at": now.isoformat(),
            }
            self._forecasts[forecast_id] = forecast
            return forecast

        amounts = [o["total_amount"] for o in confirmed_orders]
        avg_amount = sum(amounts) / len(amounts)

        if method == "trend":
            # 简单趋势：按创建时间排序，计算线性趋势
            sorted_orders = sorted(
                confirmed_orders,
                key=lambda o: o.get("created_at", ""),
            )
            n = len(sorted_orders)
            if n >= 2:
                # 简单线性回归 y = a*x + b
                x_vals = list(range(n))
                y_vals = [o["total_amount"] for o in sorted_orders]
                x_mean = sum(x_vals) / n
                y_mean = sum(y_vals) / n
                numerator = sum((x - x_mean) * (y - y_mean)
                                for x, y in zip(x_vals, y_vals))
                denominator = sum((x - x_mean) ** 2 for x in x_vals)
                slope = numerator / denominator if denominator != 0 else 0
                intercept = y_mean - slope * x_mean
                # 预测下一个周期
                predicted = slope * n + intercept
                predicted = max(0, predicted)
                confidence = 0.75 if n >= 3 else 0.6
                reasoning = (
                    f"基于{n}个历史订单的趋势分析，"
                    f"斜率{slope:.2f}，预测{period}销售额{predicted:.2f}元"
                )
            else:
                predicted = avg_amount
                confidence = 0.5
                reasoning = f"历史数据不足（{n}个），使用均值预测"
        else:
            # historical: 基于历史均值
            predicted = avg_amount
            confidence = 0.7 if len(amounts) >= 3 else 0.5
            reasoning = (
                f"基于{len(amounts)}个历史已确认订单均值"
                f"（{avg_amount:.2f}元）预测{period}销售额"
            )

        forecast = {
            "forecast_id": forecast_id,
            "period": period,
            "method": method,
            "predicted_amount": round(predicted, 2),
            "confidence": confidence,
            "data_points": len(amounts),
            "historical_avg": round(avg_amount, 2),
            "reasoning": reasoning,
            "created_at": now.isoformat(),
        }
        self._forecasts[forecast_id] = forecast

        logger.info("sales_agent.forecast_generated",
                    forecast_id=forecast_id,
                    period=period,
                    method=method,
                    predicted=predicted)

        return forecast

    # ========== 统计 ==========

    def get_stats(self) -> dict:
        """获取销售统计信息"""
        confirmed_orders = [o for o in self._orders.values()
                            if o.get("status") == "confirmed"]
        pending_orders = [o for o in self._orders.values()
                          if o.get("status") == "pending"]

        total_confirmed = sum(o["total_amount"] for o in confirmed_orders)
        total_pending = sum(o["total_amount"] for o in pending_orders)

        # 目标整体完成率
        total_target = sum(t["amount"] for t in self._targets.values())
        total_achieved = sum(t["achieved_amount"] for t in self._targets.values())
        overall_completion = (
            (total_achieved / total_target * 100) if total_target > 0 else 0.0
        )

        return {
            "orders": len(self._orders),
            "confirmed_orders": len(confirmed_orders),
            "pending_orders": len(pending_orders),
            "targets": len(self._targets),
            "forecasts": len(self._forecasts),
            "contracts": len(self._contracts),
            "total_confirmed_amount": total_confirmed,
            "total_pending_amount": total_pending,
            "total_target_amount": total_target,
            "total_achieved_amount": total_achieved,
            "overall_completion_rate": round(overall_completion, 2),
        }

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出销售决策"""
        action = context.get("action", "")

        if action == "confirm_order":
            order_id = context.get("order_id")
            order = self._orders.get(order_id, {})
            amount = order.get("total_amount", 0)

            # 大额订单需人工确认
            if amount > 500000:
                return AgentDecision(
                    decision_type="order_confirmation",
                    reasoning=(
                        f"订单{order_id}金额{amount:.2f}元超过50万，"
                        f"需人工确认后生效"
                    ),
                    action=f"请求人工确认订单{order_id}",
                    confidence=0.9,
                    requires_human_approval=True,
                    context=context,
                )
            return AgentDecision(
                decision_type="order_confirmation",
                reasoning=f"订单{order_id}金额{amount:.2f}元在阈值内，自动确认",
                action=f"自动确认订单{order_id}",
                confidence=0.95,
                requires_human_approval=False,
                context=context,
            )

        elif action == "set_target":
            salesperson_id = context.get("salesperson_id")
            period = context.get("period")
            amount = context.get("amount", 0)
            return AgentDecision(
                decision_type="target_setting",
                reasoning=(
                    f"为销售人员{salesperson_id}设定{period}周期"
                    f"销售目标{amount}元"
                ),
                action=f"设定销售目标并通知相关人员",
                confidence=0.85,
                context=context,
            )

        elif action == "forecast":
            period = context.get("period")
            return AgentDecision(
                decision_type="sales_forecast",
                reasoning=f"基于历史数据预测{period}销售趋势",
                action="生成销售预测报告并推送管理层",
                confidence=0.8,
                context=context,
            )

        # 默认决策
        return AgentDecision(
            decision_type="sales_general",
            reasoning="基于销售数据做出综合决策",
            action="执行销售常规操作",
            confidence=0.7,
            context=context,
        )
