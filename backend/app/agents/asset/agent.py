# -*- coding: utf-8 -*-
"""
资产Agent
负责固定资产全生命周期管理：登记、折旧（直线法）、盘点与处置。
监听资产登记、折旧、处置与采购审批事件，自动创建资产卡片与折旧记录。

职责：
- 监听 ASSET_REGISTERED 事件（资产登记归档）
- 监听 ASSET_DEPRECIATED 事件（折旧记录归档）
- 监听 ASSET_DISPOSED 事件（资产处置归档）
- 监听 PURCHASE_ORDER_APPROVED 事件（采购审批通过自动登记固定资产）
- 直线法计算折旧，支持批量折旧
- 资产处置需人在回路确认
- 盘点差异预警
"""
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class AssetAgent(BaseAgent):
    """
    资产Agent

    职责：
    - 固定资产登记与卡片管理
    - 直线法折旧计算（按月），支持批量折旧
    - 资产盘点计划与差异记录
    - 资产处置（出售/报废/捐赠）管理
    - 采购审批通过后自动登记为固定资产
    - 资产处置需人在回路确认
    """

    # 资产处置需人工审批
    HUMAN_APPROVAL_ON_DISPOSAL = True
    # 大额资产登记阈值（原值超过10万需人工确认）
    HUMAN_APPROVAL_THRESHOLD = 100_000.0

    def __init__(self):
        super().__init__(
            name="资产Agent",
            domain="asset",
            capabilities=[
                "asset_registration",         # 资产登记
                "depreciation_calculation",    # 折旧计算
                "asset_inventory",             # 资产盘点
                "disposal_management",         # 处置管理
                "general",
            ],
            security_clearance="internal",
        )
        # 固定资产：asset_id -> 资产详情
        self._assets: dict[str, dict] = {}
        # 折旧记录列表
        self._depreciation_records: list[dict] = []
        # 盘点记录：inventory_id -> 盘点详情
        self._inventories: dict[str, dict] = {}
        # 初始化种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "管理固定资产登记、直线法折旧、资产盘点与处置，监听采购审批自动登记资产"

    # ========== 初始化 ==========

    def _seed_data(self):
        """注入种子固定资产数据（5个）"""
        self._assets = {
            "asset-001": {
                "asset_id": "asset-001",
                "name": "应用服务器（戴尔R750）",
                "category": "it_equipment",
                "original_value": 85000.0,
                "salvage_rate": 0.05,
                "salvage_value": 4250.0,
                "useful_life": 60,  # 60个月（5年）
                "purchase_date": "2025-03-15",
                "location": "机房A",
                "department": "信息技术部",
                "status": "in_use",
                "accumulated_depreciation": 14166.67,
                "net_value": 70833.33,
                "created_at": "2025-03-15T08:00:00+00:00",
            },
            "asset-002": {
                "asset_id": "asset-002",
                "name": "办公电脑（联想ThinkPad）",
                "category": "office_equipment",
                "original_value": 12000.0,
                "salvage_rate": 0.05,
                "salvage_value": 600.0,
                "useful_life": 36,  # 36个月（3年）
                "purchase_date": "2025-06-01",
                "location": "办公区3F",
                "department": "行政部",
                "status": "in_use",
                "accumulated_depreciation": 3800.0,
                "net_value": 8200.0,
                "created_at": "2025-06-01T08:00:00+00:00",
            },
            "asset-003": {
                "asset_id": "asset-003",
                "name": "商务车（丰田凯美瑞）",
                "category": "vehicle",
                "original_value": 220000.0,
                "salvage_rate": 0.05,
                "salvage_value": 11000.0,
                "useful_life": 120,  # 120个月（10年）
                "purchase_date": "2024-09-10",
                "location": "公司车库",
                "department": "行政部",
                "status": "in_use",
                "accumulated_depreciation": 44000.0,
                "net_value": 176000.0,
                "created_at": "2024-09-10T08:00:00+00:00",
            },
            "asset-004": {
                "asset_id": "asset-004",
                "name": "生产设备（数控机床）",
                "category": "machinery",
                "original_value": 580000.0,
                "salvage_rate": 0.05,
                "salvage_value": 29000.0,
                "useful_life": 120,  # 120个月（10年）
                "purchase_date": "2024-01-20",
                "location": "生产车间B",
                "department": "生产部",
                "status": "in_use",
                "accumulated_depreciation": 193333.33,
                "net_value": 386666.67,
                "created_at": "2024-01-20T08:00:00+00:00",
            },
            "asset-005": {
                "asset_id": "asset-005",
                "name": "打印机（惠普LaserJet）",
                "category": "office_equipment",
                "original_value": 3500.0,
                "salvage_rate": 0.05,
                "salvage_value": 175.0,
                "useful_life": 60,  # 60个月（5年）
                "purchase_date": "2025-01-05",
                "location": "办公区3F",
                "department": "行政部",
                "status": "in_use",
                "accumulated_depreciation": 1416.67,
                "net_value": 2083.33,
                "created_at": "2025-01-05T08:00:00+00:00",
            },
        }

    async def initialize(self):
        """初始化Agent：订阅资产与采购审批事件"""
        self.subscribe_events(
            event_types=[
                EventType.ASSET_REGISTERED,
                EventType.ASSET_DEPRECIATED,
                EventType.ASSET_DISPOSED,
                EventType.PURCHASE_ORDER_APPROVED,
            ],
            handler=self._handle_event,
        )
        logger.info("asset_agent.initialized", agent=self.name,
                    assets=len(self._assets))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("asset_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.ASSET_REGISTERED:
            await self._handle_asset_registered(event.event_data)

        elif event.event_type == EventType.ASSET_DEPRECIATED:
            await self._handle_asset_depreciated(event.event_data)

        elif event.event_type == EventType.ASSET_DISPOSED:
            await self._handle_asset_disposed(event.event_data)

        elif event.event_type == EventType.PURCHASE_ORDER_APPROVED:
            await self._handle_purchase_approved(event.event_data)

    async def _handle_asset_registered(self, data: dict):
        """处理资产登记事件：同步资产状态"""
        asset_id = data.get("asset_id")
        if asset_id and asset_id in self._assets:
            self._assets[asset_id]["status"] = "registered"
            logger.info("asset_agent.registered_synced", asset_id=asset_id)

    async def _handle_asset_depreciated(self, data: dict):
        """处理折旧事件：同步累计折旧"""
        asset_id = data.get("asset_id")
        depreciation = data.get("depreciation_amount", 0.0)
        if asset_id and asset_id in self._assets:
            asset = self._assets[asset_id]
            asset["accumulated_depreciation"] += depreciation
            asset["net_value"] = asset["original_value"] - asset["accumulated_depreciation"]
            logger.info("asset_agent.depreciation_synced",
                        asset_id=asset_id, amount=depreciation)

    async def _handle_asset_disposed(self, data: dict):
        """处理资产处置事件：同步资产状态"""
        asset_id = data.get("asset_id")
        if asset_id and asset_id in self._assets:
            self._assets[asset_id]["status"] = "disposed"
            self._assets[asset_id]["disposal_method"] = data.get("disposal_method", "")
            self._assets[asset_id]["disposal_value"] = data.get("disposal_value", 0.0)
            self._assets[asset_id]["disposed_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("asset_agent.disposal_synced", asset_id=asset_id)

    async def _handle_purchase_approved(self, data: dict):
        """
        处理采购审批通过事件：自动登记为固定资产

        当采购订单审批通过且金额达到固定资产确认标准时，
        自动创建资产卡片。
        """
        po_id = data.get("po_id", "")
        total_amount = data.get("total_amount", 0.0)
        product_name = data.get("product_name", "")

        # 简化判断：金额超过5000元登记为固定资产
        if total_amount < 5000:
            return

        # 避免重复登记
        existing = any(
            a.get("source_po_id") == po_id for a in self._assets.values()
        )
        if existing:
            return

        asset = self.register_asset(
            name=product_name or f"采购资产-{po_id}",
            category="procurement",
            original_value=total_amount,
            purchase_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            useful_life=60,
            salvage_rate=0.05,
            location="待分配",
            department="待分配",
        )
        asset["source_po_id"] = po_id

        logger.info("asset_agent.auto_registered_from_purchase",
                    asset_id=asset["asset_id"], po_id=po_id,
                    value=total_amount)

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出资产决策"""
        action = context.get("action", "")

        if action == "disposal":
            asset_id = context.get("asset_id")
            asset = self._assets.get(asset_id)
            if not asset:
                return AgentDecision(
                    decision_type="error",
                    reasoning=f"资产{asset_id}不存在",
                    action="无法做出资产处置决策",
                    confidence=0.0,
                )
            return AgentDecision(
                decision_type="asset_disposal",
                reasoning=(
                    f"资产{asset_id}（{asset['name']}）处置需人工确认，"
                    f"当前净值{asset.get('net_value', 0):.2f}元"
                ),
                action=f"请求人工确认资产处置：{asset_id}",
                confidence=0.9,
                requires_human_approval=self.HUMAN_APPROVAL_ON_DISPOSAL,
                context={
                    "asset_id": asset_id,
                    "net_value": asset.get("net_value", 0),
                    "disposal_method": context.get("disposal_method"),
                },
            )

        if action == "registration":
            original_value = context.get("original_value", 0.0)
            if original_value > self.HUMAN_APPROVAL_THRESHOLD:
                return AgentDecision(
                    decision_type="asset_registration_approval",
                    reasoning=(
                        f"资产原值{original_value:.2f}元超过人工审批阈值"
                        f"{self.HUMAN_APPROVAL_THRESHOLD}元，需人在回路确认"
                    ),
                    action=f"请求人工确认大额资产登记：{context.get('name', '')}",
                    confidence=0.9,
                    requires_human_approval=True,
                    context=context,
                )
            return AgentDecision(
                decision_type="asset_registration_auto",
                reasoning=f"资产原值{original_value:.2f}元在阈值内，自动登记",
                action=f"自动登记资产：{context.get('name', '')}",
                confidence=0.95,
                requires_human_approval=False,
                context=context,
            )

        return AgentDecision(
            decision_type="asset_query",
            reasoning="资产查询操作",
            action="查询资产信息",
            confidence=0.8,
            requires_human_approval=False,
            context=context,
        )

    async def on_tick(self):
        """周期性任务：检查资产净值异常"""
        for asset_id, asset in self._assets.items():
            net_value = asset.get("net_value", 0)
            salvage_value = asset.get("salvage_value", 0)
            if asset.get("status") == "in_use" and net_value <= salvage_value:
                logger.warning("asset_agent.net_value_below_salvage",
                               asset_id=asset_id, name=asset["name"],
                               net_value=net_value, salvage_value=salvage_value)

    # ========== 固定资产管理 ==========

    def get_assets(self, category: Optional[str] = None,
                   status: Optional[str] = None) -> list[dict]:
        """获取固定资产列表，支持按类别与状态过滤"""
        assets = list(self._assets.values())
        if category:
            assets = [a for a in assets if a.get("category") == category]
        if status:
            assets = [a for a in assets if a.get("status") == status]
        return assets

    def get_asset(self, asset_id: str) -> Optional[dict]:
        """获取单个资产详情"""
        return self._assets.get(asset_id)

    def register_asset(self, name: str, category: str, original_value: float,
                       purchase_date: str, useful_life: int,
                       salvage_rate: float, location: str,
                       department: str) -> dict:
        """
        登记固定资产

        参数：
        - name: 资产名称
        - category: 类别（it_equipment/office_equipment/vehicle/machinery等）
        - original_value: 原值
        - purchase_date: 购置日期（YYYY-MM-DD）
        - useful_life: 使用年限（月）
        - salvage_rate: 残值率（0-1）
        - location: 存放地点
        - department: 使用部门
        """
        asset_id = f"AST-{str(uuid4())[:8]}"
        salvage_value = original_value * salvage_rate
        asset = {
            "asset_id": asset_id,
            "name": name,
            "category": category,
            "original_value": original_value,
            "salvage_rate": salvage_rate,
            "salvage_value": round(salvage_value, 2),
            "useful_life": useful_life,
            "purchase_date": purchase_date,
            "location": location,
            "department": department,
            "status": "in_use",
            "accumulated_depreciation": 0.0,
            "net_value": original_value,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._assets[asset_id] = asset

        # 发布资产登记事件
        decision = AgentDecision(
            decision_type="asset_registration",
            reasoning=(
                f"登记固定资产{name}，原值{original_value:.2f}元，"
                f"使用年限{useful_life}个月，残值率{salvage_rate:.0%}"
            ),
            action=f"登记资产{asset_id}：{name}",
            confidence=0.95,
            requires_human_approval=original_value > self.HUMAN_APPROVAL_THRESHOLD,
            context={
                "asset_id": asset_id,
                "original_value": original_value,
                "useful_life": useful_life,
            },
        )
        # 异步发布事件（在调用方事件循环中执行）
        # 注意：此处为同步方法，事件发布由调用方或initialize后处理
        logger.info("asset_agent.asset_registered",
                    asset_id=asset_id, name=name, value=original_value)
        return asset

    async def publish_registration_event(self, asset: dict):
        """发布资产登记事件（供异步调用）"""
        await self._publish_event(
            EventType.ASSET_REGISTERED,
            {
                "asset_id": asset["asset_id"],
                "name": asset["name"],
                "category": asset["category"],
                "original_value": asset["original_value"],
                "useful_life": asset["useful_life"],
                "department": asset["department"],
            },
        )

    # ========== 折旧计算 ==========

    async def calculate_depreciation(self, asset_id: str, period: str) -> dict:
        """
        直线法计算折旧

        参数：
        - asset_id: 资产ID
        - period: 折旧期间（格式 YYYY-MM）

        直线法公式：
        月折旧额 = (原值 - 残值) / 使用月数
        """
        asset = self._assets.get(asset_id)
        if not asset:
            return {"error": f"资产{asset_id}不存在"}

        if asset.get("status") != "in_use":
            return {"error": f"资产{asset_id}当前状态为{asset.get('status')}，不可计提折旧"}

        original_value = asset["original_value"]
        salvage_value = asset["salvage_value"]
        useful_life = asset["useful_life"]

        if useful_life <= 0:
            return {"error": f"资产{asset_id}使用年限无效"}

        # 直线法月折旧额
        monthly_depreciation = (original_value - salvage_value) / useful_life

        # 检查是否已计提完毕
        if asset["accumulated_depreciation"] >= (original_value - salvage_value):
            return {"error": f"资产{asset_id}折旧已计提完毕"}

        # 更新资产累计折旧与净值
        asset["accumulated_depreciation"] += monthly_depreciation
        asset["net_value"] = original_value - asset["accumulated_depreciation"]

        # 净值不低于残值
        if asset["net_value"] < salvage_value:
            asset["net_value"] = salvage_value
            asset["accumulated_depreciation"] = original_value - salvage_value

        record = {
            "record_id": f"DEP-{str(uuid4())[:8]}",
            "asset_id": asset_id,
            "asset_name": asset["name"],
            "period": period,
            "method": "straight_line",
            "original_value": original_value,
            "salvage_value": salvage_value,
            "useful_life": useful_life,
            "monthly_depreciation": round(monthly_depreciation, 2),
            "accumulated_depreciation": round(asset["accumulated_depreciation"], 2),
            "net_value": round(asset["net_value"], 2),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._depreciation_records.append(record)

        # 记录决策
        decision = AgentDecision(
            decision_type="asset_depreciation",
            reasoning=(
                f"资产{asset_id}（{asset['name']}）{period}期间计提折旧"
                f"{monthly_depreciation:.2f}元（直线法），"
                f"累计折旧{asset['accumulated_depreciation']:.2f}元，"
                f"净值{asset['net_value']:.2f}元"
            ),
            action=f"计提折旧{period}：{asset_id}，金额{monthly_depreciation:.2f}元",
            confidence=0.95,
            requires_human_approval=False,
            context={
                "asset_id": asset_id,
                "period": period,
                "monthly_depreciation": monthly_depreciation,
            },
        )
        await self.record_decision(decision)

        # 发布折旧事件
        await self._publish_event(
            EventType.ASSET_DEPRECIATED,
            {
                "asset_id": asset_id,
                "period": period,
                "depreciation_amount": round(monthly_depreciation, 2),
                "accumulated_depreciation": round(asset["accumulated_depreciation"], 2),
                "net_value": round(asset["net_value"], 2),
                "decision_id": decision.decision_id,
            },
        )

        logger.info("asset_agent.depreciation_calculated",
                    asset_id=asset_id, period=period,
                    amount=monthly_depreciation)
        return record

    async def batch_depreciation(self, period: str) -> list[dict]:
        """
        批量计提折旧

        对所有在用资产按指定期间计提折旧。
        """
        records = []
        in_use_assets = [
            aid for aid, a in self._assets.items() if a.get("status") == "in_use"
        ]
        for asset_id in in_use_assets:
            result = await self.calculate_depreciation(asset_id, period)
            if "error" not in result:
                records.append(result)
            else:
                logger.info("asset_agent.batch_depreciation_skipped",
                            asset_id=asset_id, reason=result["error"])

        logger.info("asset_agent.batch_depreciation_completed",
                    period=period, count=len(records))
        return records

    def get_depreciation_records(self, asset_id: Optional[str] = None,
                                 period: Optional[str] = None) -> list[dict]:
        """获取折旧记录，支持按资产与期间过滤"""
        records = list(self._depreciation_records)
        if asset_id:
            records = [r for r in records if r.get("asset_id") == asset_id]
        if period:
            records = [r for r in records if r.get("period") == period]
        return records

    # ========== 盘点管理 ==========

    def create_inventory(self, name: str, scope: str, date: str) -> dict:
        """
        创建盘点计划

        参数：
        - name: 盘点名称
        - scope: 盘点范围（如"全公司"/"信息技术部"）
        - date: 盘点日期
        """
        inventory_id = f"INV-{str(uuid4())[:8]}"
        inventory = {
            "inventory_id": inventory_id,
            "name": name,
            "scope": scope,
            "date": date,
            "status": "in_progress",
            "results": [],
            "discrepancy_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._inventories[inventory_id] = inventory
        logger.info("asset_agent.inventory_created",
                    inventory_id=inventory_id, name=name)
        return inventory

    def record_inventory_result(self, inventory_id: str, asset_id: str,
                                actual_status: str, discrepancy: str) -> dict:
        """
        记录盘点结果

        参数：
        - inventory_id: 盘点计划ID
        - asset_id: 资产ID
        - actual_status: 实际状态（in_use/idle/missing/damaged）
        - discrepancy: 差异描述（"无"表示账实相符）
        """
        inventory = self._inventories.get(inventory_id)
        if not inventory:
            return {"error": f"盘点计划{inventory_id}不存在"}

        asset = self._assets.get(asset_id)
        if not asset:
            return {"error": f"资产{asset_id}不存在"}

        result = {
            "asset_id": asset_id,
            "asset_name": asset["name"],
            "book_status": asset["status"],
            "actual_status": actual_status,
            "discrepancy": discrepancy,
            "has_discrepancy": discrepancy != "无" and bool(discrepancy),
        }
        inventory["results"].append(result)
        if result["has_discrepancy"]:
            inventory["discrepancy_count"] += 1
            # 同步资产实际状态
            asset["status"] = actual_status
            logger.warning("asset_agent.inventory_discrepancy",
                           asset_id=asset_id, discrepancy=discrepancy)

        return result

    def get_inventory(self, inventory_id: str) -> Optional[dict]:
        """获取盘点计划详情"""
        return self._inventories.get(inventory_id)

    def get_inventories(self) -> list[dict]:
        """获取所有盘点计划"""
        return list(self._inventories.values())

    # ========== 处置管理 ==========

    async def dispose_asset(self, asset_id: str, disposal_method: str,
                            disposal_value: float, reason: str) -> dict:
        """
        资产处置

        参数：
        - asset_id: 资产ID
        - disposal_method: 处置方式（sale/scrap/donation/transfer）
        - disposal_value: 处置价值
        - reason: 处置原因

        流程：
        1. 更新资产状态为已处置
        2. 记录处置损益
        3. 发布资产处置事件
        4. 处置需人在回路确认
        """
        asset = self._assets.get(asset_id)
        if not asset:
            return {"error": f"资产{asset_id}不存在"}

        if asset.get("status") == "disposed":
            return {"error": f"资产{asset_id}已处置"}

        net_value = asset.get("net_value", 0)
        gain_loss = disposal_value - net_value

        asset["status"] = "disposed"
        asset["disposal_method"] = disposal_method
        asset["disposal_value"] = disposal_value
        asset["disposal_gain_loss"] = round(gain_loss, 2)
        asset["disposal_reason"] = reason
        asset["disposed_at"] = datetime.now(timezone.utc).isoformat()

        # 记录决策
        decision = AgentDecision(
            decision_type="asset_disposal",
            reasoning=(
                f"资产{asset_id}（{asset['name']}）以{disposal_method}方式处置，"
                f"处置价值{disposal_value:.2f}元，净值{net_value:.2f}元，"
                f"{'处置收益' if gain_loss >= 0 else '处置损失'}"
                f"{abs(gain_loss):.2f}元"
            ),
            action=(
                f"处置资产{asset_id}：{disposal_method}，"
                f"价值{disposal_value:.2f}元"
            ),
            confidence=0.9,
            requires_human_approval=self.HUMAN_APPROVAL_ON_DISPOSAL,
            context={
                "asset_id": asset_id,
                "disposal_method": disposal_method,
                "disposal_value": disposal_value,
                "net_value": net_value,
                "gain_loss": gain_loss,
            },
        )
        await self.record_decision(decision)

        # 发布资产处置事件
        await self._publish_event(
            EventType.ASSET_DISPOSED,
            {
                "asset_id": asset_id,
                "name": asset["name"],
                "disposal_method": disposal_method,
                "disposal_value": disposal_value,
                "net_value": net_value,
                "gain_loss": round(gain_loss, 2),
                "reason": reason,
                "decision_id": decision.decision_id,
            },
        )

        logger.info("asset_agent.asset_disposed",
                    asset_id=asset_id, method=disposal_method,
                    value=disposal_value, gain_loss=gain_loss)
        return asset

    # ========== 统计 ==========

    def get_stats(self) -> dict:
        """
        获取资产统计

        返回：含总资产值、累计折旧、净值等
        """
        assets = list(self._assets.values())
        in_use = [a for a in assets if a.get("status") == "in_use"]
        disposed = [a for a in assets if a.get("status") == "disposed"]

        total_original = sum(a["original_value"] for a in assets)
        total_accumulated = sum(a.get("accumulated_depreciation", 0) for a in assets)
        total_net = sum(a.get("net_value", 0) for a in in_use)

        # 按类别统计
        by_category = {}
        for asset in in_use:
            cat = asset.get("category", "unknown")
            by_category.setdefault(cat, {
                "count": 0, "original_value": 0.0,
                "net_value": 0.0,
            })
            by_category[cat]["count"] += 1
            by_category[cat]["original_value"] += asset["original_value"]
            by_category[cat]["net_value"] += asset.get("net_value", 0)

        # 按部门统计
        by_department = {}
        for asset in in_use:
            dept = asset.get("department", "unknown")
            by_department[dept] = by_department.get(dept, 0) + 1

        return {
            "total_assets": len(assets),
            "in_use": len(in_use),
            "disposed": len(disposed),
            "total_original_value": round(total_original, 2),
            "total_accumulated_depreciation": round(total_accumulated, 2),
            "total_net_value": round(total_net, 2),
            "depreciation_records": len(self._depreciation_records),
            "inventories": len(self._inventories),
            "by_category": by_category,
            "by_department": by_department,
        }
