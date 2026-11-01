"""
商业智能Agent
负责数据分析、报表生成、仪表板管理与KPI监控。
监听销售确认、凭证过账、生产完成、库存调整等业务事件，
自动刷新相关报表与仪表板数据，并提供趋势分析能力。
"""
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class BIAgent(BaseAgent):
    """
    商业智能Agent - 数据分析与可视化

    职责：
    - 报表生成：按类型/周期生成业务报表
    - 仪表板管理：维护可配置的仪表板与组件
    - KPI监控：定义与追踪关键绩效指标
    - 趋势分析：基于历史数据计算线性/移动平均趋势
    - 数据采集：监听业务事件自动更新数据源
    """

    def __init__(self):
        super().__init__(
            name="商业智能Agent",
            domain="bi",
            capabilities=[
                "data_analysis",           # 数据分析
                "report_generation",       # 报表生成
                "dashboard_management",    # 仪表板管理
                "kpi_monitoring",          # KPI监控
                "trend_analysis",          # 趋势分析
                "general",
            ],
            security_clearance="confidential",
        )
        # 报表: report_id -> dict
        self._reports: dict[str, dict] = {}
        # 仪表板: dashboard_id -> dict
        self._dashboards: dict[str, dict] = {}
        # KPI监控: kpi_id -> dict
        self._kpis: dict[str, dict] = {}
        # 分析记录列表
        self._analyses: list[dict] = []
        # 报表编号计数器
        self._report_seq: int = 0
        # 仪表板编号计数器
        self._dashboard_seq: int = 0
        # KPI编号计数器
        self._kpi_seq: int = 0
        # 业务数据时序缓存（用于趋势分析）
        self._metrics_history: dict[str, list[dict]] = {}
        # 加载种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "数据分析、报表生成、仪表板管理与KPI监控"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅BI相关事件"""
        self.subscribe_events(
            event_types=[
                EventType.REPORT_GENERATED,
                EventType.DASHBOARD_UPDATED,
                EventType.SALES_ORDER_CONFIRMED,
                EventType.VOUCHER_POSTED,
                EventType.PRODUCTION_COMPLETED,
                EventType.INVENTORY_ADJUSTED,
            ],
            handler=self._handle_event,
        )
        logger.info("bi_agent.initialized", agent=self.name,
                    reports=len(self._reports),
                    dashboards=len(self._dashboards))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("bi_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        data = event.event_data

        if event.event_type == EventType.REPORT_GENERATED:
            await self._handle_report_generated(data)
        elif event.event_type == EventType.DASHBOARD_UPDATED:
            await self._handle_dashboard_updated(data)
        elif event.event_type == EventType.SALES_ORDER_CONFIRMED:
            await self._handle_sales_confirmed(data)
        elif event.event_type == EventType.VOUCHER_POSTED:
            await self._handle_voucher_posted(data)
        elif event.event_type == EventType.PRODUCTION_COMPLETED:
            await self._handle_production_completed(data)
        elif event.event_type == EventType.INVENTORY_ADJUSTED:
            await self._handle_inventory_adjusted(data)

    async def _handle_report_generated(self, data: dict):
        """处理报表生成事件（同步外部报表数据）"""
        report_id = data.get("report_id", "")
        if report_id and report_id not in self._reports:
            report = {
                "report_id": report_id,
                "type": data.get("type", "custom"),
                "period": data.get("period", ""),
                "title": data.get("title", ""),
                "data": data.get("data", {}),
                "parameters": data.get("parameters", {}),
                "status": "generated",
                "created_at": data.get("created_at",
                                        datetime.now(timezone.utc).isoformat()),
            }
            self._reports[report_id] = report
            logger.info("bi_agent.report_synced", report_id=report_id)

    async def _handle_dashboard_updated(self, data: dict):
        """处理仪表板更新事件"""
        dashboard_id = data.get("dashboard_id", "")
        if dashboard_id and dashboard_id in self._dashboards:
            self._dashboards[dashboard_id]["updated_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
            self._dashboards[dashboard_id]["refresh_count"] = (
                self._dashboards[dashboard_id].get("refresh_count", 0) + 1
            )
            logger.info("bi_agent.dashboard_refreshed",
                        dashboard_id=dashboard_id)

    async def _handle_sales_confirmed(self, data: dict):
        """处理销售确认事件，记录销售额指标"""
        amount = float(data.get("total_amount", 0) or data.get("amount", 0))
        if amount > 0:
            self._record_metric("sales_revenue", amount, data.get("date", ""))

    async def _handle_voucher_posted(self, data: dict):
        """处理凭证过账事件，记录财务指标"""
        amount = float(data.get("total_debit", 0) or data.get("amount", 0))
        if amount > 0:
            self._record_metric("financial_transaction", amount,
                                data.get("timestamp", ""))

    async def _handle_production_completed(self, data: dict):
        """处理生产完成事件，记录产量指标"""
        quantity = float(data.get("quantity", 0))
        if quantity > 0:
            self._record_metric("production_output", quantity,
                                data.get("date", ""))

    async def _handle_inventory_adjusted(self, data: dict):
        """处理库存调整事件，记录库存变动指标"""
        quantity = float(data.get("quantity", 0))
        if quantity != 0:
            self._record_metric("inventory_change", quantity,
                                data.get("date", ""))

    def _record_metric(self, metric: str, value: float, date: str):
        """记录指标时序数据"""
        if metric not in self._metrics_history:
            self._metrics_history[metric] = []
        self._metrics_history[metric].append({
            "value": value,
            "date": date or datetime.now(timezone.utc).isoformat(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.debug("bi_agent.metric_recorded",
                     metric=metric, value=value)

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出BI决策"""
        action = context.get("action", "")

        if action == "generate_report":
            return await self._decide_generate_report(context)
        elif action == "update_dashboard":
            return await self._decide_update_dashboard(context)
        elif action == "add_kpi":
            return await self._decide_add_kpi(context)

        return AgentDecision(
            decision_type="bi_unknown",
            reasoning=f"未知操作: {action}",
            action="拒绝执行",
            confidence=0.0,
            context=context,
        )

    async def _decide_generate_report(self, context: dict) -> AgentDecision:
        """报表生成决策"""
        report_type = context.get("type", "")
        period = context.get("period", "")
        if not report_type or not period:
            return AgentDecision(
                decision_type="report_generation",
                reasoning="报表类型和周期不能为空",
                action="拒绝生成",
                confidence=1.0,
                context=context,
            )
        return AgentDecision(
            decision_type="report_generation",
            reasoning=f"生成{report_type}报表，周期{period}",
            action="生成报表",
            confidence=0.9,
            context=context,
        )

    async def _decide_update_dashboard(self, context: dict) -> AgentDecision:
        """仪表板更新决策"""
        dashboard_id = context.get("dashboard_id", "")
        dashboard = self._dashboards.get(dashboard_id)
        if not dashboard:
            return AgentDecision(
                decision_type="dashboard_update",
                reasoning=f"仪表板不存在: {dashboard_id}",
                action="拒绝更新",
                confidence=1.0,
                context=context,
            )
        return AgentDecision(
            decision_type="dashboard_update",
            reasoning=f"更新仪表板{dashboard_id}组件配置",
            action="更新仪表板",
            confidence=0.9,
            context=context,
        )

    async def _decide_add_kpi(self, context: dict) -> AgentDecision:
        """新增KPI决策"""
        name = context.get("name", "")
        if not name:
            return AgentDecision(
                decision_type="kpi_addition",
                reasoning="KPI名称不能为空",
                action="拒绝新增",
                confidence=1.0,
                context=context,
            )
        return AgentDecision(
            decision_type="kpi_addition",
            reasoning=f"新增KPI指标: {name}",
            action="新增KPI",
            confidence=0.9,
            context=context,
        )

    # ========== 公开 API ==========

    def _generate_report_id(self) -> str:
        """生成报表编号"""
        self._report_seq += 1
        return f"RPT{self._report_seq:04d}"

    def _generate_dashboard_id(self) -> str:
        """生成仪表板编号"""
        self._dashboard_seq += 1
        return f"DSH{self._dashboard_seq:04d}"

    def _generate_kpi_id(self) -> str:
        """生成KPI编号"""
        self._kpi_seq += 1
        return f"KPI{self._kpi_seq:04d}"

    def get_reports(self, type: Optional[str] = None,
                    period: Optional[str] = None) -> list[dict]:
        """获取报表列表，可按类型和周期过滤"""
        reports = list(self._reports.values())
        if type:
            reports = [r for r in reports if r.get("type") == type]
        if period:
            reports = [r for r in reports if r.get("period") == period]
        return reports

    def get_report(self, report_id: str) -> Optional[dict]:
        """获取单个报表"""
        return self._reports.get(report_id)

    async def generate_report(self, type: str, period: str,
                              parameters: Optional[dict] = None) -> dict:
        """根据类型生成报表数据"""
        report_id = self._generate_report_id()
        now = datetime.now(timezone.utc).isoformat()
        parameters = parameters or {}
        # 根据报表类型生成模拟数据
        data = self._build_report_data(type, period, parameters)
        report = {
            "report_id": report_id,
            "type": type,
            "period": period,
            "title": self._report_title(type, period),
            "data": data,
            "parameters": parameters,
            "status": "generated",
            "created_at": now,
        }
        self._reports[report_id] = report

        # 发布报表生成事件
        await self._publish_event(
            EventType.REPORT_GENERATED,
            {
                "report_id": report_id,
                "type": type,
                "period": period,
                "title": report["title"],
            },
        )
        logger.info("bi_agent.report_generated",
                    report_id=report_id, type=type, period=period)
        return report

    def _report_title(self, report_type: str, period: str) -> str:
        """生成报表标题"""
        titles = {
            "sales_monthly": "销售月报",
            "finance_quarterly": "财务季报",
            "operation_weekly": "运营周报",
            "custom": "自定义报表",
        }
        base = titles.get(report_type, report_type)
        return f"{base}-{period}"

    def _build_report_data(self, report_type: str, period: str,
                           parameters: dict) -> dict:
        """根据报表类型构建报表数据"""
        if report_type == "sales_monthly":
            return {
                "summary": {
                    "total_revenue": 1_250_000,
                    "total_orders": 320,
                    "avg_order_value": 3906.25,
                    "growth_rate": 0.12,
                },
                "by_product": [
                    {"product": "产品A", "revenue": 450000, "orders": 120},
                    {"product": "产品B", "revenue": 380000, "orders": 95},
                    {"product": "产品C", "revenue": 420000, "orders": 105},
                ],
                "by_region": [
                    {"region": "华东", "revenue": 520000},
                    {"region": "华北", "revenue": 380000},
                    {"region": "华南", "revenue": 350000},
                ],
            }
        elif report_type == "finance_quarterly":
            return {
                "summary": {
                    "total_revenue": 3_750_000,
                    "total_expense": 2_800_000,
                    "net_profit": 950_000,
                    "profit_margin": 0.253,
                },
                "by_category": [
                    {"category": "营业收入", "amount": 3750000},
                    {"category": "营业成本", "amount": 1800000},
                    {"category": "管理费用", "amount": 600000},
                    {"category": "销售费用", "amount": 400000},
                ],
            }
        elif report_type == "operation_weekly":
            return {
                "summary": {
                    "production_output": 8500,
                    "inventory_turnover": 3.2,
                    "order_fulfillment_rate": 0.96,
                    "on_time_delivery_rate": 0.94,
                },
                "by_day": [
                    {"day": "周一", "output": 1200, "orders": 45},
                    {"day": "周二", "output": 1350, "orders": 52},
                    {"day": "周三", "output": 1100, "orders": 38},
                    {"day": "周四", "output": 1400, "orders": 55},
                    {"day": "周五", "output": 1250, "orders": 48},
                ],
            }
        else:
            # 自定义报表
            return {
                "summary": {"note": "自定义报表数据"},
                "parameters": parameters,
            }

    def get_dashboards(self) -> list[dict]:
        """获取所有仪表板"""
        return list(self._dashboards.values())

    def get_dashboard(self, dashboard_id: str) -> Optional[dict]:
        """获取单个仪表板"""
        return self._dashboards.get(dashboard_id)

    async def update_dashboard(self, dashboard_id: str,
                               widgets: list[dict]) -> dict:
        """更新仪表板组件"""
        dashboard = self._dashboards.get(dashboard_id)
        if not dashboard:
            return {"error": f"仪表板不存在: {dashboard_id}"}
        dashboard["widgets"] = widgets
        dashboard["updated_at"] = datetime.now(timezone.utc).isoformat()
        dashboard["refresh_count"] = dashboard.get("refresh_count", 0) + 1

        # 发布仪表板更新事件
        await self._publish_event(
            EventType.DASHBOARD_UPDATED,
            {
                "dashboard_id": dashboard_id,
                "name": dashboard["name"],
                "widgets_count": len(widgets),
            },
        )
        logger.info("bi_agent.dashboard_updated",
                    dashboard_id=dashboard_id,
                    widgets=len(widgets))
        return dashboard

    def get_kpis(self, dashboard_id: Optional[str] = None) -> list[dict]:
        """获取KPI列表，可按仪表板过滤"""
        kpis = list(self._kpis.values())
        if dashboard_id:
            kpis = [k for k in kpis if k.get("dashboard_id") == dashboard_id]
        return kpis

    def add_kpi(self, name: str, target: float, current: float,
                unit: str, owner: str) -> dict:
        """新增KPI指标"""
        kpi_id = self._generate_kpi_id()
        now = datetime.now(timezone.utc).isoformat()
        achievement = (current / target) if target > 0 else 0.0
        kpi = {
            "kpi_id": kpi_id,
            "name": name,
            "target": float(target),
            "current": float(current),
            "unit": unit,
            "owner": owner,
            "achievement_rate": achievement,
            "status": "on_track" if achievement >= 1.0 else (
                "at_risk" if achievement >= 0.8 else "behind"
            ),
            "created_at": now,
            "updated_at": now,
        }
        self._kpis[kpi_id] = kpi
        logger.info("bi_agent.kpi_added", kpi_id=kpi_id, name=name)
        return kpi

    def analyze_trend(self, metric: str, period_range: str,
                      method: str = "linear") -> dict:
        """
        趋势分析

        参数：
        - metric: 指标名称
        - period_range: 分析周期范围（如 "2026-Q1"）
        - method: 分析方法（linear/moving_average）
        """
        history = self._metrics_history.get(metric, [])
        now = datetime.now(timezone.utc).isoformat()

        if not history:
            analysis = {
                "metric": metric,
                "period_range": period_range,
                "method": method,
                "data_points": 0,
                "trend": "no_data",
                "slope": 0.0,
                "forecast": None,
                "analyzed_at": now,
            }
            self._analyses.append(analysis)
            return analysis

        values = [h["value"] for h in history]
        n = len(values)

        if method == "moving_average" and n >= 3:
            window = min(3, n)
            avg = sum(values[-window:]) / window
            slope = (values[-1] - values[0]) / (n - 1) if n > 1 else 0.0
            forecast = avg + slope
            trend = "up" if slope > 0 else ("down" if slope < 0 else "stable")
        else:
            # 线性回归（最小二乘法）
            if n >= 2:
                x_mean = (n - 1) / 2
                y_mean = sum(values) / n
                numerator = sum((i - x_mean) * (v - y_mean)
                                for i, v in enumerate(values))
                denominator = sum((i - x_mean) ** 2 for i in range(n))
                slope = numerator / denominator if denominator != 0 else 0.0
                intercept = y_mean - slope * x_mean
                forecast = intercept + slope * n
            else:
                slope = 0.0
                forecast = values[0]
            trend = "up" if slope > 0 else ("down" if slope < 0 else "stable")

        analysis = {
            "metric": metric,
            "period_range": period_range,
            "method": method,
            "data_points": n,
            "trend": trend,
            "slope": round(slope, 4),
            "current_value": values[-1],
            "average_value": round(sum(values) / n, 4),
            "min_value": min(values),
            "max_value": max(values),
            "forecast": round(forecast, 4),
            "history": history,
            "analyzed_at": now,
        }
        self._analyses.append(analysis)
        logger.info("bi_agent.trend_analyzed",
                    metric=metric, method=method, trend=trend)
        return analysis

    def get_stats(self) -> dict:
        """获取BI统计信息"""
        all_kpis = list(self._kpis.values())
        on_track = [k for k in all_kpis if k.get("status") == "on_track"]
        at_risk = [k for k in all_kpis if k.get("status") == "at_risk"]
        behind = [k for k in all_kpis if k.get("status") == "behind"]
        by_report_type: dict[str, int] = {}
        for r in self._reports.values():
            rtype = r.get("type", "unknown")
            by_report_type[rtype] = by_report_type.get(rtype, 0) + 1
        return {
            "total_reports": len(self._reports),
            "total_dashboards": len(self._dashboards),
            "total_kpis": len(all_kpis),
            "total_analyses": len(self._analyses),
            "kpi_on_track": len(on_track),
            "kpi_at_risk": len(at_risk),
            "kpi_behind": len(behind),
            "by_report_type": by_report_type,
            "metrics_tracked": len(self._metrics_history),
        }

    # ========== 种子数据 ==========

    def _seed_data(self):
        """加载种子数据：3个预置报表和2个仪表板"""
        now = datetime.now(timezone.utc).isoformat()

        # 3个预置报表
        seed_reports = [
            {
                "report_id": "RPT0001",
                "type": "sales_monthly",
                "period": "2026-06",
                "title": "销售月报-2026-06",
                "data": self._build_report_data("sales_monthly", "2026-06", {}),
                "parameters": {},
                "status": "generated",
                "created_at": now,
            },
            {
                "report_id": "RPT0002",
                "type": "finance_quarterly",
                "period": "2026-Q1",
                "title": "财务季报-2026-Q1",
                "data": self._build_report_data("finance_quarterly", "2026-Q1", {}),
                "parameters": {},
                "status": "generated",
                "created_at": now,
            },
            {
                "report_id": "RPT0003",
                "type": "operation_weekly",
                "period": "2026-W26",
                "title": "运营周报-2026-W26",
                "data": self._build_report_data("operation_weekly", "2026-W26", {}),
                "parameters": {},
                "status": "generated",
                "created_at": now,
            },
        ]
        for report in seed_reports:
            self._reports[report["report_id"]] = report
        self._report_seq = 3

        # 2个仪表板
        seed_dashboards = [
            {
                "dashboard_id": "DSH0001",
                "name": "高管驾驶舱",
                "description": "面向高管的核心经营指标全景视图",
                "owner": "CEO办公室",
                "widgets": [
                    {"id": "w1", "type": "kpi_card", "title": "月度营收",
                     "metric": "sales_revenue"},
                    {"id": "w2", "type": "chart", "title": "营收趋势",
                     "chart_type": "line"},
                    {"id": "w3", "type": "kpi_card", "title": "净利润率",
                     "metric": "profit_margin"},
                    {"id": "w4", "type": "table", "title": "部门绩效排名"},
                ],
                "status": "active",
                "refresh_count": 0,
                "created_at": now,
                "updated_at": now,
            },
            {
                "dashboard_id": "DSH0002",
                "name": "部门绩效看板",
                "description": "各部门KPI达成情况监控",
                "owner": "运营管理部",
                "widgets": [
                    {"id": "w1", "type": "chart", "title": "部门产出对比",
                     "chart_type": "bar"},
                    {"id": "w2", "type": "kpi_card", "title": "生产达成率",
                     "metric": "production_output"},
                    {"id": "w3", "type": "chart", "title": "库存周转",
                     "chart_type": "line"},
                ],
                "status": "active",
                "refresh_count": 0,
                "created_at": now,
                "updated_at": now,
            },
        ]
        for dashboard in seed_dashboards:
            self._dashboards[dashboard["dashboard_id"]] = dashboard
        self._dashboard_seq = 2

        # 预置KPI
        seed_kpis = [
            {
                "kpi_id": "KPI0001",
                "name": "月度销售额",
                "target": 1_500_000,
                "current": 1_250_000,
                "unit": "元",
                "owner": "销售部",
                "dashboard_id": "DSH0001",
                "achievement_rate": 1_250_000 / 1_500_000,
                "status": "at_risk",
                "created_at": now,
                "updated_at": now,
            },
            {
                "kpi_id": "KPI0002",
                "name": "季度净利润",
                "target": 1_000_000,
                "current": 950_000,
                "unit": "元",
                "owner": "财务部",
                "dashboard_id": "DSH0001",
                "achievement_rate": 950_000 / 1_000_000,
                "status": "at_risk",
                "created_at": now,
                "updated_at": now,
            },
            {
                "kpi_id": "KPI0003",
                "name": "生产达成率",
                "target": 100,
                "current": 96,
                "unit": "%",
                "owner": "生产部",
                "dashboard_id": "DSH0002",
                "achievement_rate": 96 / 100,
                "status": "at_risk",
                "created_at": now,
                "updated_at": now,
            },
        ]
        for kpi in seed_kpis:
            self._kpis[kpi["kpi_id"]] = kpi
        self._kpi_seq = 3

        # 预置指标历史数据（用于趋势分析演示）
        self._metrics_history["sales_revenue"] = [
            {"value": 980000, "date": "2026-01", "timestamp": now},
            {"value": 1050000, "date": "2026-02", "timestamp": now},
            {"value": 1120000, "date": "2026-03", "timestamp": now},
            {"value": 1180000, "date": "2026-04", "timestamp": now},
            {"value": 1210000, "date": "2026-05", "timestamp": now},
            {"value": 1250000, "date": "2026-06", "timestamp": now},
        ]

        logger.info("bi_agent.seed_data_loaded",
                    reports=len(self._reports),
                    dashboards=len(self._dashboards),
                    kpis=len(self._kpis))
