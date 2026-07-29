# -*- coding: utf-8 -*-
"""
需求先知Agent
基于历史销售数据预测未来需求，识别季节性趋势和异常模式。
监听库存调整事件分析消耗模式，发布需求预测事件。

职责：
- 监听 INVENTORY_ADJUSTED 事件（分析消耗模式）
- 基于历史销售数据预测未来需求（LLM优先，降级到移动平均法）
- 识别季节性趋势和异常消耗模式
- 发布 DEMAND_FORECAST_GENERATED 和 DEMAND_ANOMALY_DETECTED 事件
- 提供查询接口供API调用
"""
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4
from collections import defaultdict, deque

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class DemandForecastAgent(BaseAgent):
    """
    需求先知Agent

    职责：
    - 监听库存调整事件，积累销售/消耗历史数据
    - 基于历史数据预测未来需求（LLM优先，降级到移动平均法）
    - 识别季节性趋势和异常消耗模式
    - 高异常波动需人工确认
    """

    # 异常检测阈值：实际消耗偏离预测超过此倍数视为异常
    ANOMALY_DEVIATION_FACTOR = 2.0
    # 移动平均窗口大小
    MOVING_AVERAGE_WINDOW = 7
    # 预测周期（天）
    FORECAST_HORIZON_DAYS = 7

    def __init__(self):
        super().__init__(
            name="需求先知Agent",
            domain="demand",
            capabilities=[
                "demand_forecasting",       # 需求预测
                "trend_analysis",           # 趋势分析
                "seasonality_detection",    # 季节性检测
                "general",
            ],
            security_clearance="internal",
        )
        # 预测记录：forecast_id -> 预测详情
        self._forecasts: dict[str, dict] = {}
        # 销售历史：product_id -> deque[(timestamp, quantity)]
        self._sales_history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=90)
        )
        # 趋势记录：product_id -> 趋势详情
        self._trends: dict[str, dict] = {}

    @property
    def description(self) -> str:
        return "基于历史销售数据预测未来需求，识别季节性趋势与异常消耗模式"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅库存调整事件"""
        self.subscribe_events(
            event_types=[
                EventType.INVENTORY_ADJUSTED,
            ],
            handler=self._handle_event,
        )
        logger.info("demand_agent.initialized", agent=self.name)

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("demand_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.INVENTORY_ADJUSTED:
            await self._analyze_consumption(event.event_data)

    async def _analyze_consumption(self, inventory_data: dict):
        """分析库存消耗模式，记录销售历史并尝试生成预测"""
        product_id = inventory_data.get("product_id", "")
        product_name = inventory_data.get("product_name", "")
        quantity = inventory_data.get("quantity", 0)
        safety_stock = inventory_data.get("safety_stock", 0)
        reorder_point = inventory_data.get("reorder_point", 0)

        if not product_id:
            return

        # 记录消耗历史
        self._sales_history[product_id].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "quantity": quantity,
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
        })

        # 当历史数据足够时，生成预测
        history = list(self._sales_history[product_id])
        if len(history) >= 3:
            await self._generate_forecast(
                product_id, product_name, history
            )

        logger.info("demand_agent.consumption_recorded",
                    product=product_id, quantity=quantity,
                    history_size=len(history))

    # ========== 需求预测 ==========

    async def _generate_forecast(
        self, product_id: str, product_name: str, history: list,
    ) -> Optional[dict]:
        """
        生成需求预测

        优先使用LLM分析（注入历史消耗数据），
        LLM不可用时降级到移动平均法。
        """
        llm_result = await self._forecast_demand_llm(
            product_id, product_name, history
        )
        if llm_result is not None:
            forecast = llm_result
        else:
            forecast = self._forecast_demand_rules(product_id, history)

        # 记录预测
        forecast_id = f"FCST-{str(uuid4())[:8]}"
        forecast["forecast_id"] = forecast_id
        forecast["product_id"] = product_id
        forecast["product_name"] = product_name
        forecast["created_at"] = datetime.now(timezone.utc).isoformat()
        self._forecasts[forecast_id] = forecast

        # 记录决策
        decision = AgentDecision(
            decision_type="demand_forecast",
            reasoning=forecast.get("reasoning", "需求预测"),
            action=(
                f"预测产品'{product_name}'未来{self.FORECAST_HORIZON_DAYS}天"
                f"需求约{forecast['predicted_demand']}件"
            ),
            confidence=forecast.get("confidence", 0.75),
            requires_human_approval=False,
            context={
                "forecast_id": forecast_id,
                "product_id": product_id,
                "method": forecast.get("method", "rules"),
            },
        )
        await self.record_decision(decision)

        # 发布预测生成事件
        await self._publish_event(
            EventType.DEMAND_FORECAST_GENERATED,
            {**forecast, "decision_id": decision.decision_id},
        )

        # 异常检测
        await self._detect_anomaly(product_id, product_name, forecast, history)

        logger.info("demand_agent.forecast_generated",
                    forecast_id=forecast_id, product=product_name,
                    predicted_demand=forecast["predicted_demand"],
                    method=forecast.get("method", "rules"))
        return forecast

    async def _forecast_demand_llm(
        self, product_id: str, product_name: str, history: list,
    ) -> Optional[dict]:
        """
        使用LLM进行需求预测

        返回None表示LLM不可用，调用方应降级到规则模式。
        """
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        context = {
            "product_id": product_id,
            "product_name": product_name,
            "history": history[-30:],  # 最近30条记录
            "horizon_days": self.FORECAST_HORIZON_DAYS,
        }
        prompt = (
            f"请基于历史消耗数据预测未来{self.FORECAST_HORIZON_DAYS}天的需求量。"
            "返回JSON，包含 predicted_demand(整数), confidence(0-1), "
            "trend(上升/下降/平稳), seasonality(是/否及描述), "
            "reasoning, method(llm)。"
        )

        try:
            result = await self.llm_analyze(prompt, context)
        except Exception as e:
            logger.warning("demand_agent.llm_forecast_error",
                           agent=self.name, error=str(e))
            return None

        if "error" in result:
            logger.info("demand_agent.llm_forecast_unavailable",
                        agent=self.name, reason=result.get("error"))
            return None

        required_keys = ("predicted_demand", "confidence")
        if not all(k in result for k in required_keys):
            return None

        try:
            result["predicted_demand"] = int(result["predicted_demand"])
            result["confidence"] = float(result["confidence"])
        except (TypeError, ValueError):
            return None

        result.setdefault("trend", "平稳")
        result.setdefault("seasonality", "否")
        result.setdefault("reasoning", "LLM需求预测")
        result["method"] = "llm"

        # 更新趋势记录
        self._trends[product_id] = {
            "product_id": product_id,
            "product_name": product_name,
            "trend": result["trend"],
            "seasonality": result["seasonality"],
            "confidence": result["confidence"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info("demand_agent.llm_forecast_success",
                    agent=self.name, product=product_id,
                    predicted_demand=result["predicted_demand"])
        return result

    def _forecast_demand_rules(self, product_id: str, history: list) -> dict:
        """
        规则预测（降级方案）：移动平均法

        基于最近 MOVING_AVERAGE_WINDOW 条记录的平均消耗量推算未来需求。
        """
        recent = history[-self.MOVING_AVERAGE_WINDOW:]
        quantities = [h.get("quantity", 0) for h in recent]
        avg_consumption = sum(quantities) / len(quantities) if quantities else 0
        predicted_demand = int(avg_consumption * self.FORECAST_HORIZON_DAYS)

        # 趋势分析：对比前半段与后半段均值
        if len(quantities) >= 4:
            mid = len(quantities) // 2
            first_half_avg = sum(quantities[:mid]) / mid if mid > 0 else 0
            second_half_avg = sum(quantities[mid:]) / (len(quantities) - mid) if (len(quantities) - mid) > 0 else 0
            if second_half_avg > first_half_avg * 1.1:
                trend = "上升"
            elif second_half_avg < first_half_avg * 0.9:
                trend = "下降"
            else:
                trend = "平稳"
        else:
            trend = "平稳"

        # 季节性检测（简化：检测周期性波动）
        if len(quantities) >= 7:
            max_qty = max(quantities)
            min_qty = min(quantities)
            seasonality = "是" if max_qty > min_qty * 2 else "否"
        else:
            seasonality = "否"

        # 更新趋势记录
        self._trends[product_id] = {
            "product_id": product_id,
            "trend": trend,
            "seasonality": seasonality,
            "confidence": 0.72,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        return {
            "predicted_demand": predicted_demand,
            "confidence": 0.72,
            "trend": trend,
            "seasonality": seasonality,
            "reasoning": (
                f"基于移动平均法（窗口{self.MOVING_AVERAGE_WINDOW}），"
                f"平均消耗{avg_consumption:.1f}件/次，预测"
                f"{self.FORECAST_HORIZON_DAYS}天需求{predicted_demand}件"
            ),
            "method": "rules",
            "avg_consumption": round(avg_consumption, 2),
        }

    # ========== 异常检测 ==========

    async def _detect_anomaly(
        self, product_id: str, product_name: str,
        forecast: dict, history: list,
    ) -> Optional[dict]:
        """
        检测需求异常

        当最近消耗量偏离预测值超过阈值时，发布异常事件。
        """
        if not history:
            return None

        recent_qty = history[-1].get("quantity", 0)
        predicted_avg = forecast.get("avg_consumption", 0)
        if predicted_avg <= 0:
            # 用预测需求除以周期估算日均
            predicted_avg = forecast.get("predicted_demand", 0) / self.FORECAST_HORIZON_DAYS

        if predicted_avg <= 0:
            return None

        deviation = abs(recent_qty - predicted_avg) / predicted_avg
        if deviation < self.ANOMALY_DEVIATION_FACTOR:
            return None

        anomaly = {
            "anomaly_id": f"ANOM-{str(uuid4())[:8]}",
            "product_id": product_id,
            "product_name": product_name,
            "actual_consumption": recent_qty,
            "expected_avg": round(predicted_avg, 2),
            "deviation_ratio": round(deviation, 2),
            "severity": "high" if deviation > 3.0 else "medium",
            "description": (
                f"产品'{product_name}'消耗量{recent_qty}偏离预期"
                f"{predicted_avg:.1f}，偏差{deviation:.1f}倍"
            ),
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }

        # 记录决策（异常需人工关注）
        decision = AgentDecision(
            decision_type="demand_anomaly",
            reasoning=anomaly["description"],
            action=f"检测到需求异常：产品'{product_name}'偏差{deviation:.1f}倍",
            confidence=0.85,
            requires_human_approval=anomaly["severity"] == "high",
            context={
                "anomaly_id": anomaly["anomaly_id"],
                "product_id": product_id,
                "deviation_ratio": anomaly["deviation_ratio"],
                "severity": anomaly["severity"],
            },
        )
        await self.record_decision(decision)

        await self._publish_event(
            EventType.DEMAND_ANOMALY_DETECTED,
            {**anomaly, "decision_id": decision.decision_id},
        )

        logger.warning("demand_agent.anomaly_detected",
                       product=product_name, deviation=deviation,
                       severity=anomaly["severity"])
        return anomaly

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出需求预测决策"""
        forecast_id = context.get("forecast_id")
        forecast = self._forecasts.get(forecast_id)

        if not forecast:
            return AgentDecision(
                decision_type="error",
                reasoning=f"预测记录{forecast_id}不存在",
                action="无法做出需求预测决策",
                confidence=0.0,
            )

        return AgentDecision(
            decision_type="demand_forecast_decision",
            reasoning=(
                f"产品'{forecast['product_name']}'预测需求"
                f"{forecast['predicted_demand']}件，趋势{forecast.get('trend', '未知')}"
            ),
            action=f"确认需求预测{forecast_id}",
            confidence=forecast.get("confidence", 0.75),
            requires_human_approval=False,
            context={"forecast_id": forecast_id},
        )

    async def on_tick(self):
        """周期性任务：定期重新生成已有产品的需求预测"""
        # 对已有历史数据的产品，定期刷新趋势
        for product_id, history in self._sales_history.items():
            if len(history) >= 3:
                product_name = history[-1].get("product_name", product_id)
                # 检查是否最近已有预测，避免过于频繁
                recent_forecasts = [
                    f for f in self._forecasts.values()
                    if f.get("product_id") == product_id
                ]
                if not recent_forecasts:
                    await self._generate_forecast(
                        product_id, product_name, list(history)
                    )

    # ========== 查询接口 ==========

    def get_forecasts(self, product_id: Optional[str] = None) -> list[dict]:
        """获取预测记录列表"""
        forecasts = list(self._forecasts.values())
        if product_id:
            forecasts = [f for f in forecasts if f.get("product_id") == product_id]
        return forecasts

    def get_trends(self, product_id: Optional[str] = None) -> list[dict]:
        """获取趋势记录列表"""
        trends = list(self._trends.values())
        if product_id:
            trends = [t for t in trends if t.get("product_id") == product_id]
        return trends
