"""
客户关系Agent
负责客户档案管理、商机跟踪、客户分级与联系人管理
监听客户与商机事件，维护客户全生命周期数据
"""
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.db.repositories.base import has_db, schedule
from app.db.repositories.sales_repository import customer_repository
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class CRMAgent(BaseAgent):
    """
    客户关系Agent

    职责：
    - 客户档案管理（创建、更新、查询、分级）
    - 商机跟踪（创建、阶段推进、赢单/输单管理）
    - 联系人管理
    - 客户分级统计（A/B/C分级）
    - 监听客户与商机事件，同步外部变更
    """

    def __init__(self):
        super().__init__(
            name="客户关系Agent",
            domain="crm",
            capabilities=[
                "customer_management",        # 客户管理
                "opportunity_tracking",       # 商机跟踪
                "customer_segmentation",      # 客户分级
                "contact_management",         # 联系人管理
            ],
            security_clearance="internal",
        )
        # 内部数据存储
        self._customers: dict[str, dict] = {}        # 客户档案
        self._opportunities: dict[str, dict] = {}     # 商机
        self._contacts: dict[str, dict] = {}          # 联系人
        self._segments: dict[str, str] = {}           # 客户分级 customer_id -> segment(A/B/C)
        self._seed_data()

    @property
    def description(self) -> str:
        return "管理客户档案、商机跟踪、客户分级与联系人，维护客户全生命周期数据"

    # ========== 种子数据 ==========

    def _seed_data(self):
        """预置5个示例客户（含名称、行业、分级ABC、联系人、商机）"""
        seed_customers = [
            {
                "name": "华盛科技有限公司",
                "industry": "信息技术",
                "segment": "A",
                "contact_name": "张伟",
                "contact_phone": "13800138001",
                "contact_title": "采购总监",
                "contact_email": "zhangwei@huasheng.com",
            },
            {
                "name": "恒泰制造集团",
                "industry": "制造业",
                "segment": "A",
                "contact_name": "李娜",
                "contact_phone": "13800138002",
                "contact_title": "供应链经理",
                "contact_email": "lina@hengtai.com",
            },
            {
                "name": "瑞达金融服务",
                "industry": "金融",
                "segment": "B",
                "contact_name": "王强",
                "contact_phone": "13800138003",
                "contact_title": "IT负责人",
                "contact_email": "wangqiang@ruida.com",
            },
            {
                "name": "绿源环保科技",
                "industry": "环保",
                "segment": "B",
                "contact_name": "赵敏",
                "contact_phone": "13800138004",
                "contact_title": "总经理",
                "contact_email": "zhaomin@lvuyuan.com",
            },
            {
                "name": "天天零售连锁",
                "industry": "零售",
                "segment": "C",
                "contact_name": "陈刚",
                "contact_phone": "13800138005",
                "contact_title": "运营主管",
                "contact_email": "chengang@tiantian.com",
            },
        ]

        # 商机种子数据（关联到客户）
        seed_opportunities = [
            {"customer_idx": 0, "name": "ERP系统升级项目", "amount": 500000, "stage": "negotiation"},
            {"customer_idx": 1, "name": "产线自动化改造", "amount": 1200000, "stage": "proposal"},
            {"customer_idx": 2, "name": "金融风控平台建设", "amount": 800000, "stage": "qualified"},
            {"customer_idx": 3, "name": "环境监测系统", "amount": 300000, "stage": "won"},
            {"customer_idx": 4, "name": "门店POS系统替换", "amount": 150000, "stage": "lost"},
        ]

        for idx, cust in enumerate(seed_customers):
            customer = self.create_customer(
                name=cust["name"],
                industry=cust["industry"],
                segment=cust["segment"],
                contact_name=cust["contact_name"],
                contact_phone=cust["contact_phone"],
                # 稳定种子 ID：与销售 Agent 种子订单的 CUST-SEED-* 引用对齐
                customer_id=f"CUST-SEED-{idx + 1:03d}",
            )
            # 补充联系人邮箱和职位
            if customer["contacts"]:
                contact_id = customer["contacts"][0]
                self._contacts[contact_id]["title"] = cust["contact_title"]
                self._contacts[contact_id]["email"] = cust["contact_email"]

            # 关联商机
            opp = seed_opportunities[idx]
            self.create_opportunity(
                customer_id=customer["customer_id"],
                name=opp["name"],
                amount=opp["amount"],
                stage=opp["stage"],
                expected_close_date="2026-12-31",
            )

        logger.info("crm_agent.seeded",
                    customers=len(self._customers),
                    opportunities=len(self._opportunities),
                    contacts=len(self._contacts))

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅客户与商机事件"""
        self.subscribe_events(
            event_types=[
                EventType.CUSTOMER_CREATED,
                EventType.CUSTOMER_UPDATED,
                EventType.OPPORTUNITY_CREATED,
                EventType.OPPORTUNITY_WON,
                EventType.OPPORTUNITY_LOST,
            ],
            handler=self._handle_event,
        )
        logger.info("crm_agent.initialized", agent=self.name,
                    customers=len(self._customers))

    async def hydrate_from_db(self) -> bool:
        """
        启动水合：DB 优先、内存种子兜底、空库回播种（路线图 2.2 批次一）。
        """
        if not has_db():
            return False

        customers = await customer_repository.list_all()
        if customers:
            self._customers = {
                c["customer_id"]: {**self._customers.get(c["customer_id"], {}), **c}
                for c in customers
            }
            for cid, c in self._customers.items():
                self._segments[cid] = c.get("segment", "C")
        elif self._customers:
            for c in list(self._customers.values()):
                await customer_repository.upsert(c, "customer_id")
            logger.info("crm_agent.customers_seeded_to_db",
                        count=len(self._customers))

        logger.info("crm_agent.hydrated", customers=len(self._customers))
        return True

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("crm_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        data = event.event_data

        if event.event_type == EventType.CUSTOMER_CREATED:
            # 外部系统创建的客户，同步到本地（避免重复）
            customer_id = data.get("customer_id")
            if customer_id and customer_id not in self._customers:
                self._customers[customer_id] = {
                    "customer_id": customer_id,
                    "name": data.get("name", ""),
                    "industry": data.get("industry", ""),
                    "segment": data.get("segment", "C"),
                    "status": data.get("status", "active"),
                    "created_at": data.get("created_at",
                                            datetime.now(timezone.utc).isoformat()),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                self._segments[customer_id] = data.get("segment", "C")
                logger.info("crm_agent.customer_synced",
                            customer_id=customer_id,
                            source="event")

        elif event.event_type == EventType.CUSTOMER_UPDATED:
            customer_id = data.get("customer_id")
            if customer_id and customer_id in self._customers:
                for key in ("name", "industry", "segment", "status"):
                    if key in data:
                        self._customers[customer_id][key] = data[key]
                self._customers[customer_id]["updated_at"] = \
                    datetime.now(timezone.utc).isoformat()
                if "segment" in data:
                    self._segments[customer_id] = data["segment"]
                logger.info("crm_agent.customer_updated_via_event",
                            customer_id=customer_id)

        elif event.event_type == EventType.OPPORTUNITY_CREATED:
            opp_id = data.get("opportunity_id")
            if opp_id and opp_id not in self._opportunities:
                self._opportunities[opp_id] = {
                    "opportunity_id": opp_id,
                    "customer_id": data.get("customer_id", ""),
                    "name": data.get("name", ""),
                    "amount": data.get("amount", 0.0),
                    "stage": data.get("stage", "qualified"),
                    "expected_close_date": data.get("expected_close_date", ""),
                    "status": "open",
                    "created_at": data.get("created_at",
                                           datetime.now(timezone.utc).isoformat()),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                logger.info("crm_agent.opportunity_synced",
                            opportunity_id=opp_id,
                            source="event")

        elif event.event_type == EventType.OPPORTUNITY_WON:
            opp_id = data.get("opportunity_id")
            if opp_id and opp_id in self._opportunities:
                self._opportunities[opp_id]["stage"] = "won"
                self._opportunities[opp_id]["status"] = "won"
                self._opportunities[opp_id]["actual_amount"] = \
                    data.get("actual_amount",
                             self._opportunities[opp_id]["amount"])
                self._opportunities[opp_id]["updated_at"] = \
                    datetime.now(timezone.utc).isoformat()
                logger.info("crm_agent.opportunity_won",
                            opportunity_id=opp_id)

        elif event.event_type == EventType.OPPORTUNITY_LOST:
            opp_id = data.get("opportunity_id")
            if opp_id and opp_id in self._opportunities:
                self._opportunities[opp_id]["stage"] = "lost"
                self._opportunities[opp_id]["status"] = "lost"
                self._opportunities[opp_id]["loss_reason"] = \
                    data.get("loss_reason", "")
                self._opportunities[opp_id]["updated_at"] = \
                    datetime.now(timezone.utc).isoformat()
                logger.info("crm_agent.opportunity_lost",
                            opportunity_id=opp_id)

    # ========== 客户管理 ==========

    def get_customers(self, segment: Optional[str] = None,
                      industry: Optional[str] = None) -> list[dict]:
        """获取客户列表，支持按分级和行业筛选"""
        customers = list(self._customers.values())
        if segment:
            customers = [c for c in customers if c.get("segment") == segment]
        if industry:
            customers = [c for c in customers if c.get("industry") == industry]
        return customers

    def get_customer(self, customer_id: str) -> Optional[dict]:
        """获取单个客户详情"""
        customer = self._customers.get(customer_id)
        if not customer:
            return None
        result = dict(customer)
        # 关联联系人和商机
        result["contacts"] = [
            c for c in self._contacts.values()
            if c.get("customer_id") == customer_id
        ]
        result["opportunities"] = [
            o for o in self._opportunities.values()
            if o.get("customer_id") == customer_id
        ]
        return result

    def create_customer(self, name: str, industry: str, segment: str,
                        contact_name: str, contact_phone: str,
                        customer_id: str | None = None) -> dict:
        """创建客户档案（``customer_id`` 供种子数据传入稳定 ID）"""
        customer_id = customer_id or f"CUST-{str(uuid4())[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        customer = {
            "customer_id": customer_id,
            "name": name,
            "industry": industry,
            "segment": segment,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        self._customers[customer_id] = customer
        self._segments[customer_id] = segment
        schedule(
            customer_repository.upsert(customer, "customer_id"),
            "customer_repo",
        )

        # 自动创建主联系人
        contact = self.add_contact(
            customer_id=customer_id,
            name=contact_name,
            title="主联系人",
            phone=contact_phone,
            email="",
        )
        customer["contacts"] = [contact["contact_id"]]

        logger.info("crm_agent.customer_created",
                    customer_id=customer_id, name=name, segment=segment)
        return customer

    def update_customer(self, customer_id: str, **kwargs) -> dict:
        """更新客户信息"""
        customer = self._customers.get(customer_id)
        if not customer:
            raise ValueError(f"客户{customer_id}不存在")

        for key in ("name", "industry", "segment", "status"):
            if key in kwargs:
                customer[key] = kwargs[key]
                if key == "segment":
                    self._segments[customer_id] = kwargs[key]
        customer["updated_at"] = datetime.now(timezone.utc).isoformat()

        logger.info("crm_agent.customer_updated", customer_id=customer_id)
        return customer

    # ========== 商机管理 ==========

    def get_opportunities(self, customer_id: Optional[str] = None,
                          status: Optional[str] = None) -> list[dict]:
        """获取商机列表，支持按客户和状态筛选"""
        opps = list(self._opportunities.values())
        if customer_id:
            opps = [o for o in opps if o.get("customer_id") == customer_id]
        if status:
            opps = [o for o in opps if o.get("status") == status]
        return opps

    def create_opportunity(self, customer_id: str, name: str, amount: float,
                           stage: str, expected_close_date: str) -> dict:
        """创建商机"""
        if customer_id not in self._customers:
            raise ValueError(f"客户{customer_id}不存在")

        opp_id = f"OPP-{str(uuid4())[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        opportunity = {
            "opportunity_id": opp_id,
            "customer_id": customer_id,
            "name": name,
            "amount": amount,
            "stage": stage,
            "expected_close_date": expected_close_date,
            "status": "won" if stage == "won" else (
                "lost" if stage == "lost" else "open"
            ),
            "created_at": now,
            "updated_at": now,
        }
        self._opportunities[opp_id] = opportunity

        logger.info("crm_agent.opportunity_created",
                    opportunity_id=opp_id,
                    customer_id=customer_id,
                    amount=amount, stage=stage)
        return opportunity

    def update_opportunity_stage(self, opportunity_id: str, stage: str) -> dict:
        """更新商机阶段"""
        opp = self._opportunities.get(opportunity_id)
        if not opp:
            raise ValueError(f"商机{opportunity_id}不存在")

        opp["stage"] = stage
        if stage == "won":
            opp["status"] = "won"
        elif stage == "lost":
            opp["status"] = "lost"
        else:
            opp["status"] = "open"
        opp["updated_at"] = datetime.now(timezone.utc).isoformat()

        logger.info("crm_agent.opportunity_stage_updated",
                    opportunity_id=opportunity_id, stage=stage)
        return opp

    # ========== 联系人管理 ==========

    def get_contacts(self, customer_id: Optional[str] = None) -> list[dict]:
        """获取联系人列表，支持按客户筛选"""
        contacts = list(self._contacts.values())
        if customer_id:
            contacts = [c for c in contacts if c.get("customer_id") == customer_id]
        return contacts

    def add_contact(self, customer_id: str, name: str, title: str,
                    phone: str, email: str) -> dict:
        """添加联系人"""
        if customer_id not in self._customers:
            raise ValueError(f"客户{customer_id}不存在")

        contact_id = f"CONT-{str(uuid4())[:8]}"
        contact = {
            "contact_id": contact_id,
            "customer_id": customer_id,
            "name": name,
            "title": title,
            "phone": phone,
            "email": email,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._contacts[contact_id] = contact

        logger.info("crm_agent.contact_added",
                    contact_id=contact_id,
                    customer_id=customer_id, name=name)
        return contact

    # ========== 客户分级统计 ==========

    def get_customer_segments(self) -> dict:
        """统计各分级客户数"""
        segments = {"A": 0, "B": 0, "C": 0}
        for seg in self._segments.values():
            if seg in segments:
                segments[seg] += 1
            else:
                segments[seg] = 1
        return {
            "segments": segments,
            "total": sum(segments.values()),
        }

    # ========== 统计 ==========

    def get_stats(self) -> dict:
        """获取CRM统计信息"""
        open_opps = [o for o in self._opportunities.values()
                     if o.get("status") == "open"]
        won_opps = [o for o in self._opportunities.values()
                    if o.get("status") == "won"]
        lost_opps = [o for o in self._opportunities.values()
                     if o.get("status") == "lost"]

        total_pipeline = sum(o.get("amount", 0) for o in open_opps)
        total_won = sum(o.get("amount", 0) for o in won_opps)

        win_rate = 0.0
        closed = len(won_opps) + len(lost_opps)
        if closed > 0:
            win_rate = len(won_opps) / closed

        return {
            "customers": len(self._customers),
            "opportunities": len(self._opportunities),
            "open_opportunities": len(open_opps),
            "won_opportunities": len(won_opps),
            "lost_opportunities": len(lost_opps),
            "contacts": len(self._contacts),
            "total_pipeline_amount": total_pipeline,
            "total_won_amount": total_won,
            "win_rate": round(win_rate, 4),
            "segments": self.get_customer_segments()["segments"],
        }

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出CRM决策"""
        action = context.get("action", "")

        if action == "create_opportunity":
            customer_id = context.get("customer_id")
            amount = context.get("amount", 0)
            customer = self._customers.get(customer_id, {})
            segment = customer.get("segment", "C")

            # A级客户高金额商机高优先级
            if segment == "A" and amount > 500000:
                return AgentDecision(
                    decision_type="opportunity_priority",
                    reasoning=(
                        f"A级客户'{customer.get('name', '')}'的高价值商机"
                        f"（{amount}元），建议优先跟进并分配高级销售"
                    ),
                    action=f"将商机标记为高优先级并分配高级销售人员",
                    confidence=0.9,
                    requires_human_approval=False,
                    context=context,
                )
            return AgentDecision(
                decision_type="opportunity_creation",
                reasoning=f"为客户{customer.get('name', '')}创建商机，金额{amount}元",
                action="创建商机并分配跟进人员",
                confidence=0.85,
                context=context,
            )

        elif action == "update_segment":
            customer_id = context.get("customer_id")
            new_segment = context.get("segment", "C")
            return AgentDecision(
                decision_type="customer_segmentation",
                reasoning=(
                    f"根据客户{customer_id}的交易历史与活跃度，"
                    f"调整分级为{new_segment}"
                ),
                action=f"更新客户{customer_id}分级为{new_segment}",
                confidence=0.8,
                context=context,
            )

        # 默认决策
        return AgentDecision(
            decision_type="crm_general",
            reasoning="基于客户关系数据做出综合决策",
            action="执行CRM常规操作",
            confidence=0.7,
            context=context,
        )
