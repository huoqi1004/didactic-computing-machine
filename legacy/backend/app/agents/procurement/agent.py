# -*- coding: utf-8 -*-
"""
采购Agent
负责自动询比价、合约协商、交期跟踪与异常处理。
每个关键供应商由专用Agent对接。

职责：
- 监听 REPLENISHMENT_PROPOSED 事件（来自库存守护Agent）
- 自动生成采购订单
- 供应商比价（使用LLM分析供应商报价，LLM不可用时降级到规则）
- 交期跟踪与异常预警
- 大额采购触发多Agent共识
- 采购订单需人在回路确认
"""
import asyncio
import structlog
from typing import Optional
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType
from app.core.config import SecurityLevel, Permission

logger = structlog.get_logger()


# ========== 知识图谱（轻量级内存实现） ==========

class KnowledgeGraph:
    """
    轻量级知识图谱

    存储供应商、产品等业务实体节点，供Agent查询。
    生产环境将替换为图数据库（Neo4j / NebulaGraph）。
    """

    def __init__(self):
        self._nodes: dict[str, list[dict]] = {}

    def add_node(self, node_type: str, node_id: str, **properties):
        """添加节点"""
        node = {
            "node_type": node_type,
            "node_id": node_id,
            **properties,
        }
        self._nodes.setdefault(node_type, []).append(node)

    def find_nodes(self, node_type: str) -> list[dict]:
        """查找指定类型的所有节点"""
        return list(self._nodes.get(node_type, []))

    def find_node(self, node_type: str, node_id: str) -> Optional[dict]:
        """查找单个节点"""
        for node in self._nodes.get(node_type, []):
            if node.get("node_id") == node_id:
                return node
        return None

    def find_suppliers_for_product(self, product_id: str) -> list[dict]:
        """查找能供应指定产品的供应商"""
        suppliers = self.find_nodes("supplier")
        matched = []
        for sup in suppliers:
            products = sup.get("products", {})
            if product_id in products:
                matched.append(sup)
        return matched

    def clear(self):
        """清空所有节点"""
        self._nodes.clear()


# 全局知识图谱单例
knowledge_graph = KnowledgeGraph()


# ========== 采购Agent ==========

class ProcurementAgent(BaseAgent):
    """
    采购Agent

    职责：
    - 监听库存守护Agent的补货建议（REPLENISHMENT_PROPOSED）
    - 自动生成采购订单，进行供应商询比价
    - 使用LLM分析供应商报价（LLM不可用时降级到规则：选择评分最高的供应商）
    - 交期跟踪与延期异常预警
    - 金额超过10万需人在回路确认
    - 金额超过50万触发多Agent共识
    - 采购订单审批流程
    """

    # 金额阈值
    HUMAN_APPROVAL_THRESHOLD = 100_000.0   # 超过10万需人在回路
    CONSENSUS_THRESHOLD = 500_000.0        # 超过50万触发多Agent共识

    def __init__(self):
        super().__init__(
            name="采购Agent",
            domain="procurement",
            capabilities=[
                "supplier_comparison",         # 供应商比价
                "purchase_order_generation",   # 采购订单生成
                "delivery_tracking",            # 交期跟踪
                "contract_negotiation",         # 合约协商
                "general",
            ],
            security_clearance="confidential",
        )
        # 供应商缓存：supplier_id -> {name, rating, products, lead_time, payment_terms}
        self._suppliers: dict[str, dict] = {}
        # 采购订单缓存：po_id -> 订单详情
        self._purchase_orders: dict[str, dict] = {}

        # 初始化知识图谱种子数据
        self._seed_knowledge_graph()

    @property
    def description(self) -> str:
        return "自动询比价、生成采购订单、交期跟踪与异常处理"

    # ========== 初始化 ==========

    def _seed_knowledge_graph(self):
        """向知识图谱注入种子供应商数据"""
        # 清空已有节点，避免重复实例化时数据累积
        knowledge_graph.clear()
        suppliers = [
            {
                "node_id": "sup-001",
                "name": "华东材料供应商",
                "rating": 4.5,
                "products": {
                    "prod-001": 9.50,
                    "prod-002": 14.00,
                    "prod-003": 25.00,
                },
                "lead_time": 7,
                "payment_terms": "net30",
                "contact": "sales@huadong-supply.com",
            },
            {
                "node_id": "sup-002",
                "name": "华南贸易公司",
                "rating": 4.0,
                "products": {
                    "prod-001": 9.00,
                    "prod-002": 13.50,
                    "prod-003": 26.00,
                },
                "lead_time": 10,
                "payment_terms": "net45",
                "contact": "info@southtrade.com",
            },
            {
                "node_id": "sup-003",
                "name": "北方工业集团",
                "rating": 4.8,
                "products": {
                    "prod-001": 10.50,
                    "prod-002": 15.00,
                    "prod-003": 24.00,
                },
                "lead_time": 5,
                "payment_terms": "net30",
                "contact": "procurement@northindustrial.com",
            },
        ]
        for sup in suppliers:
            knowledge_graph.add_node("supplier", **sup)

        # 同步到本地缓存
        self._sync_suppliers_from_kg()

    def _sync_suppliers_from_kg(self):
        """从知识图谱同步供应商到本地缓存"""
        for node in knowledge_graph.find_nodes("supplier"):
            sid = node["node_id"]
            self._suppliers[sid] = {
                "supplier_id": sid,
                "name": node.get("name", ""),
                "rating": node.get("rating", 0.0),
                "products": node.get("products", {}),
                "lead_time": node.get("lead_time", 0),
                "payment_terms": node.get("payment_terms", ""),
                "contact": node.get("contact", ""),
            }

    async def initialize(self):
        """初始化Agent：订阅补货建议与采购订单更新事件"""
        self.subscribe_events(
            event_types=[
                EventType.REPLENISHMENT_PROPOSED,
                EventType.PURCHASE_ORDER_UPDATED,
            ],
            handler=self._handle_event,
        )
        logger.info("procurement_agent.initialized", agent=self.name,
                    suppliers=len(self._suppliers))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("procurement_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.REPLENISHMENT_PROPOSED:
            await self._generate_purchase_order(event.event_data)

        elif event.event_type == EventType.PURCHASE_ORDER_UPDATED:
            # 采购订单状态更新（如外部系统回传到货确认）
            po_id = event.event_data.get("po_id")
            new_status = event.event_data.get("status")
            if po_id and po_id in self._purchase_orders and new_status:
                self._purchase_orders[po_id]["status"] = new_status
                logger.info("procurement_agent.po_status_updated",
                            po_id=po_id, status=new_status)

    # ========== 采购订单生成 ==========

    async def _generate_purchase_order(self, proposal_data: dict) -> Optional[dict]:
        """
        根据补货建议生成采购订单

        流程：
        1. 从知识图谱查找匹配供应商（knowledge_graph.find_nodes("supplier")）
        2. 使用LLM进行供应商比价分析（self.llm_analyze）
        3. LLM不可用时降级到规则：选择评分最高的供应商
        4. 生成采购订单
        5. 金额超过10万需人在回路确认
        6. 金额超过50万触发多Agent共识
        7. 发布 PURCHASE_ORDER_CREATED 和 SUPPLIER_EVALUATED 事件
        """
        product_id = proposal_data.get("product_id", "")
        product_name = proposal_data.get("product_name", "")
        quantity = proposal_data.get("reorder_quantity",
                                     proposal_data.get("quantity", 0))
        unit_cost = proposal_data.get("unit_cost", 0.0)
        # 补货建议可能未直接提供 unit_cost，从 total_cost 推导
        if unit_cost <= 0 and quantity > 0:
            total_cost = proposal_data.get("total_cost", 0.0)
            if total_cost > 0:
                unit_cost = total_cost / quantity
        proposal_id = proposal_data.get("proposal_id", "")

        if quantity <= 0:
            logger.warning("procurement_agent.invalid_quantity",
                           product=product_id, quantity=quantity)
            return None

        # 1. 从知识图谱查找匹配供应商
        candidate_suppliers = knowledge_graph.find_suppliers_for_product(product_id)
        if not candidate_suppliers:
            # 没有精确匹配的供应商，使用全部供应商并以补货单价作为报价
            # 使用副本避免修改知识图谱中的原始节点
            candidate_suppliers = []
            for sup in knowledge_graph.find_nodes("supplier"):
                new_sup = dict(sup)
                products = dict(sup.get("products", {}))
                products[product_id] = unit_cost
                new_sup["products"] = products
                candidate_suppliers.append(new_sup)

        if not candidate_suppliers:
            logger.warning("procurement_agent.no_suppliers", product=product_id)
            return None

        # 2 & 3. 供应商比价（LLM优先，降级到规则）
        comparison = await self._compare_suppliers(
            candidate_suppliers, product_id, quantity
        )

        recommended = comparison.get("recommended_supplier")
        if not recommended:
            logger.warning("procurement_agent.no_recommended_supplier",
                           product=product_id)
            return None

        supplier_id = recommended["supplier_id"]
        unit_price = recommended.get("unit_price", unit_cost)
        total_amount = unit_price * quantity
        lead_time = recommended.get("lead_time", 7)
        expected_delivery_date = datetime.now(timezone.utc) + timedelta(days=lead_time)

        # 5 & 6. 金额阈值判断
        requires_human_approval = total_amount > self.HUMAN_APPROVAL_THRESHOLD
        requires_consensus = total_amount > self.CONSENSUS_THRESHOLD

        if requires_human_approval:
            status = "pending_approval"
        else:
            status = "approved"  # 小额自动批准

        # 生成采购订单
        po_id = f"PO-{str(uuid4())[:8]}"
        purchase_order = {
            "po_id": po_id,
            "supplier_id": supplier_id,
            "supplier_name": recommended.get("name", ""),
            "product_id": product_id,
            "product_name": product_name,
            "quantity": quantity,
            "unit_price": unit_price,
            "total_amount": total_amount,
            "expected_delivery_date": expected_delivery_date.isoformat(),
            "status": status,
            "requires_human_approval": requires_human_approval,
            "requires_consensus": requires_consensus,
            "source_proposal_id": proposal_id,
            "comparison_method": comparison.get("method", "rules"),
            "comparison_result": comparison,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._purchase_orders[po_id] = purchase_order

        # 构建决策理由（LLM理由优先，降级到规则描述）
        llm_reasoning = comparison.get("reasoning", "")
        rule_reasoning = (
            f"补货建议触发采购：产品'{product_name}'需采购{quantity}件，"
            f"推荐供应商'{recommended.get('name', '')}'（评分{recommended.get('rating', 0)}），"
            f"单价{unit_price}元，总金额{total_amount:.2f}元，"
            f"交期{lead_time}天。"
        )
        reasoning = llm_reasoning if llm_reasoning and comparison.get("method") == "llm" else rule_reasoning

        # 记录决策
        decision = AgentDecision(
            decision_type="purchase_order_generation",
            reasoning=reasoning,
            action=(
                f"生成采购订单{po_id}：{product_name} {quantity}件，"
                f"供应商{recommended.get('name', '')}，总金额{total_amount:.2f}元"
            ),
            confidence=comparison.get("confidence", 0.8),
            requires_human_approval=requires_human_approval,
            context={
                "po_id": po_id,
                "product_id": product_id,
                "supplier_id": supplier_id,
                "quantity": quantity,
                "total_amount": total_amount,
                "requires_consensus": requires_consensus,
                "comparison_method": comparison.get("method", "rules"),
            },
        )
        await self.record_decision(decision)

        # 发布供应商评估事件
        await self._publish_event(
            EventType.SUPPLIER_EVALUATED,
            {
                "product_id": product_id,
                "candidate_count": len(candidate_suppliers),
                "recommended_supplier_id": supplier_id,
                "recommended_supplier_name": recommended.get("name", ""),
                "comparison_method": comparison.get("method", "rules"),
                "analysis": comparison.get("analysis", []),
                "decision_id": decision.decision_id,
            },
        )

        # 发布采购订单创建事件
        await self._publish_event(
            EventType.PURCHASE_ORDER_CREATED,
            {
                **purchase_order,
                "decision_id": decision.decision_id,
            },
        )

        # 6. 大额采购触发多Agent共识
        if requires_consensus:
            logger.info("procurement_agent.consensus_triggered",
                        po_id=po_id, total_amount=total_amount)
            await self._publish_event(
                EventType.PURCHASE_ORDER_UPDATED,
                {
                    "po_id": po_id,
                    "status": "pending_consensus",
                    "total_amount": total_amount,
                    "action": "consensus_required",
                },
            )

        logger.info("procurement_agent.purchase_order_created",
                    po_id=po_id,
                    product=product_name,
                    supplier=recommended.get("name", ""),
                    quantity=quantity,
                    total_amount=total_amount,
                    status=status,
                    method=comparison.get("method", "rules"))

        return purchase_order

    # ========== 供应商比价 ==========

    async def _compare_suppliers(
        self, suppliers: list[dict], product_id: str, quantity: int
    ) -> dict:
        """
        供应商比价分析

        优先使用LLM分析供应商报价，LLM不可用时降级到规则（选择评分最高的供应商）。

        返回：
        {
            "recommended_supplier": {...},
            "recommended_supplier_id": "...",
            "reasoning": "...",
            "confidence": 0.0-1.0,
            "method": "llm" | "rules",
            "analysis": [...],
        }
        """
        # 尝试LLM比价
        llm_result = await self._compare_suppliers_llm(suppliers, product_id, quantity)
        if llm_result is not None:
            return llm_result

        # 降级到规则比价
        return self._compare_suppliers_rules(suppliers, product_id, quantity)

    async def _compare_suppliers_llm(
        self, suppliers: list[dict], product_id: str, quantity: int
    ) -> Optional[dict]:
        """
        使用LLM进行供应商比价分析

        返回None表示LLM不可用，调用方应降级到规则模式。
        """
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        # 构建供应商比价上下文
        candidates = []
        for sup in suppliers:
            sid = sup.get("node_id") or sup.get("supplier_id", "")
            products = sup.get("products", {})
            unit_price = products.get(product_id, 0.0)
            candidates.append({
                "supplier_id": sid,
                "name": sup.get("name", ""),
                "rating": sup.get("rating", 0.0),
                "unit_price": unit_price,
                "total_price": unit_price * quantity,
                "lead_time": sup.get("lead_time", 0),
                "payment_terms": sup.get("payment_terms", ""),
            })

        context = {
            "product_id": product_id,
            "quantity": quantity,
            "candidates": candidates,
        }
        prompt = (
            "请对比以下候选供应商，综合考虑价格、评分、交期和付款条件，"
            "推荐最优供应商。返回JSON，包含 recommended_supplier_id, "
            "reasoning, confidence(0-1), analysis(列表，每项含supplier_id, "
            "score, pros, cons)。"
        )

        try:
            result = await self.llm_analyze(prompt, context)
        except Exception as e:
            logger.warning("procurement_agent.llm_compare_error",
                           agent=self.name, error=str(e))
            return None

        if "error" in result:
            logger.info("procurement_agent.llm_compare_unavailable",
                        agent=self.name, reason=result.get("error"))
            return None

        recommended_id = result.get("recommended_supplier_id")
        if not recommended_id:
            return None

        # 查找推荐的供应商详情
        recommended_sup = None
        for sup in suppliers:
            sid = sup.get("node_id") or sup.get("supplier_id", "")
            if sid == recommended_id:
                products = sup.get("products", {})
                recommended_sup = {
                    "supplier_id": sid,
                    "name": sup.get("name", ""),
                    "rating": sup.get("rating", 0.0),
                    "unit_price": products.get(product_id, 0.0),
                    "lead_time": sup.get("lead_time", 0),
                    "payment_terms": sup.get("payment_terms", ""),
                }
                break

        if not recommended_sup:
            return None

        try:
            confidence = float(result.get("confidence", 0.8))
        except (TypeError, ValueError):
            confidence = 0.8

        logger.info("procurement_agent.llm_compare_success",
                     agent=self.name, recommended=recommended_id,
                     confidence=confidence)

        return {
            "recommended_supplier": recommended_sup,
            "recommended_supplier_id": recommended_id,
            "reasoning": str(result.get("reasoning", "")),
            "confidence": confidence,
            "method": "llm",
            "analysis": result.get("analysis", []),
        }

    def _compare_suppliers_rules(
        self, suppliers: list[dict], product_id: str, quantity: int
    ) -> dict:
        """
        规则比价（降级方案）

        综合评分 = 评分(40%) + 价格分(30%) + 交期分(30%)
        选择综合评分最高的供应商。
        """
        candidates = []
        for sup in suppliers:
            sid = sup.get("node_id") or sup.get("supplier_id", "")
            products = sup.get("products", {})
            unit_price = products.get(product_id, 0.0)
            candidates.append({
                "supplier_id": sid,
                "name": sup.get("name", ""),
                "rating": sup.get("rating", 0.0),
                "unit_price": unit_price,
                "total_price": unit_price * quantity,
                "lead_time": sup.get("lead_time", 0),
                "payment_terms": sup.get("payment_terms", ""),
            })

        if not candidates:
            return {
                "recommended_supplier": None,
                "recommended_supplier_id": None,
                "reasoning": "无可用供应商",
                "confidence": 0.0,
                "method": "rules",
                "analysis": [],
            }

        # 计算归一化分数
        prices = [c["unit_price"] for c in candidates if c["unit_price"] > 0]
        lead_times = [c["lead_time"] for c in candidates if c["lead_time"] > 0]
        ratings = [c["rating"] for c in candidates]

        min_price = min(prices) if prices else 0
        max_price = max(prices) if prices else 0
        min_lead = min(lead_times) if lead_times else 0
        max_lead = max(lead_times) if lead_times else 0
        min_rating = min(ratings) if ratings else 0
        max_rating = max(ratings) if ratings else 0

        analysis = []
        for c in candidates:
            # 评分分（0-1）：评分越高分越高
            rating_score = (
                (c["rating"] - min_rating) / (max_rating - min_rating)
                if max_rating > min_rating else 1.0
            )
            # 价格分（0-1）：价格越低分越高
            price_score = (
                (max_price - c["unit_price"]) / (max_price - min_price)
                if max_price > min_price else 1.0
            )
            # 交期分（0-1）：交期越短分越高
            lead_score = (
                (max_lead - c["lead_time"]) / (max_lead - min_lead)
                if max_lead > min_lead else 1.0
            )
            # 综合评分
            total_score = rating_score * 0.4 + price_score * 0.3 + lead_score * 0.3
            analysis.append({
                "supplier_id": c["supplier_id"],
                "name": c["name"],
                "rating": c["rating"],
                "unit_price": c["unit_price"],
                "lead_time": c["lead_time"],
                "total_score": round(total_score, 4),
                "rating_score": round(rating_score, 4),
                "price_score": round(price_score, 4),
                "lead_score": round(lead_score, 4),
            })

        # 选择综合评分最高的供应商
        analysis.sort(key=lambda x: x["total_score"], reverse=True)
        best = analysis[0]
        best_candidate = next(c for c in candidates if c["supplier_id"] == best["supplier_id"])

        reasoning = (
            f"基于规则比价：供应商'{best['name']}'综合评分最高({best['total_score']:.2f})，"
            f"评分{best['rating']}，单价{best['unit_price']}元，交期{best['lead_time']}天。"
        )

        recommended = {
            "supplier_id": best_candidate["supplier_id"],
            "name": best_candidate["name"],
            "rating": best_candidate["rating"],
            "unit_price": best_candidate["unit_price"],
            "lead_time": best_candidate["lead_time"],
            "payment_terms": best_candidate["payment_terms"],
        }

        return {
            "recommended_supplier": recommended,
            "recommended_supplier_id": best_candidate["supplier_id"],
            "reasoning": reasoning,
            "confidence": 0.8,
            "method": "rules",
            "analysis": analysis,
        }

    # ========== 交期跟踪 ==========

    async def _track_delivery(self, po_id: str = None):
        """
        跟踪交期，延期发出预警

        检查指定采购订单（或全部订单）是否延期，
        延期则标记状态并发布 DELIVERY_DELAYED 事件。
        """
        now = datetime.now(timezone.utc)
        delayed_pos = []

        if po_id:
            po_ids = [po_id] if po_id in self._purchase_orders else []
        else:
            po_ids = list(self._purchase_orders.keys())

        for pid in po_ids:
            po = self._purchase_orders[pid]
            # 仅跟踪已下单/已批准但未到货的订单
            if po["status"] in ("delivered", "rejected", "cancelled"):
                continue

            expected_str = po.get("expected_delivery_date", "")
            if not expected_str:
                continue

            try:
                expected_date = datetime.fromisoformat(expected_str)
            except (ValueError, TypeError):
                continue

            if now > expected_date and po["status"] != "delayed":
                po["status"] = "delayed"
                delayed_pos.append(po)
                logger.warning("procurement_agent.delivery_delayed",
                               po_id=pid,
                               expected_delivery=expected_str,
                               supplier=po.get("supplier_name", ""))

        # 发布延期预警事件
        for po in delayed_pos:
            await self._publish_event(
                EventType.DELIVERY_DELAYED,
                {
                    "po_id": po["po_id"],
                    "supplier_id": po["supplier_id"],
                    "supplier_name": po.get("supplier_name", ""),
                    "product_id": po["product_id"],
                    "product_name": po.get("product_name", ""),
                    "expected_delivery_date": po["expected_delivery_date"],
                    "total_amount": po["total_amount"],
                },
            )

        return delayed_pos

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出采购决策"""
        po_id = context.get("po_id")
        po = self._purchase_orders.get(po_id)

        if not po:
            return AgentDecision(
                decision_type="error",
                reasoning=f"采购订单{po_id}不存在",
                action="无法做出采购决策",
                confidence=0.0,
            )

        total = po["total_amount"]
        if total > self.CONSENSUS_THRESHOLD:
            return AgentDecision(
                decision_type="procurement_consensus",
                reasoning=(
                    f"采购订单{po_id}金额{total:.2f}元超过共识阈值"
                    f"{self.CONSENSUS_THRESHOLD}元，需触发多Agent共识"
                ),
                action=f"触发多Agent共识：采购订单{po_id}",
                confidence=0.85,
                requires_human_approval=True,
                context={"po_id": po_id, "requires_consensus": True},
            )
        elif total > self.HUMAN_APPROVAL_THRESHOLD:
            return AgentDecision(
                decision_type="procurement_approval",
                reasoning=(
                    f"采购订单{po_id}金额{total:.2f}元超过人工审批阈值"
                    f"{self.HUMAN_APPROVAL_THRESHOLD}元，需人在回路确认"
                ),
                action=f"请求人工审批：采购订单{po_id}",
                confidence=0.9,
                requires_human_approval=True,
                context={"po_id": po_id},
            )
        else:
            return AgentDecision(
                decision_type="procurement_auto",
                reasoning=(
                    f"采购订单{po_id}金额{total:.2f}元在阈值内，自动批准"
                ),
                action=f"自动批准采购订单{po_id}",
                confidence=0.95,
                requires_human_approval=False,
                context={"po_id": po_id},
            )

    async def on_tick(self):
        """周期性检查采购订单状态与交期"""
        await self._track_delivery()

    # ========== 查询接口 ==========

    def get_purchase_orders(self, status: Optional[str] = None) -> list[dict]:
        """获取采购订单列表"""
        orders = list(self._purchase_orders.values())
        if status:
            orders = [o for o in orders if o["status"] == status]
        return orders

    def get_suppliers(self) -> list[dict]:
        """获取供应商列表"""
        return list(self._suppliers.values())

    # ========== 审批流程 ==========

    async def approve_purchase_order(self, po_id: str, approved: bool) -> bool:
        """
        审批采购订单（人类监督员调用）

        参数：
        - po_id: 采购订单ID
        - approved: True=批准, False=拒绝
        """
        po = self._purchase_orders.get(po_id)
        if not po:
            logger.warning("procurement_agent.po_not_found", po_id=po_id)
            return False

        if approved:
            po["status"] = "approved"
            po["approved_at"] = datetime.now(timezone.utc).isoformat()
            await self._publish_event(
                EventType.PURCHASE_ORDER_APPROVED,
                {
                    "po_id": po_id,
                    "supplier_id": po["supplier_id"],
                    "total_amount": po["total_amount"],
                    "status": "approved",
                },
            )
            logger.info("procurement_agent.po_approved", po_id=po_id)
        else:
            po["status"] = "rejected"
            po["rejected_at"] = datetime.now(timezone.utc).isoformat()
            await self._publish_event(
                EventType.PURCHASE_ORDER_REJECTED,
                {
                    "po_id": po_id,
                    "supplier_id": po["supplier_id"],
                    "total_amount": po["total_amount"],
                    "status": "rejected",
                },
            )
            logger.info("procurement_agent.po_rejected", po_id=po_id)

        # 发布采购订单更新事件
        await self._publish_event(
            EventType.PURCHASE_ORDER_UPDATED,
            {
                "po_id": po_id,
                "status": po["status"],
                "action": "approved" if approved else "rejected",
            },
        )

        return True
