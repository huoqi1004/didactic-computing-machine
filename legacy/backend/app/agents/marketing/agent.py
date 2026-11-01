"""
市场Agent
负责营销活动管理、线索生成与转化、品牌资产管理与市场分析
监听营销活动与线索事件，驱动市场运营全流程
"""
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class MarketingAgent(BaseAgent):
    """
    市场Agent

    职责：
    - 营销活动管理（创建、启动、完成、ROI分析）
    - 线索管理（添加、筛选、质量评估）
    - 品牌资产管理
    - 市场分析报告
    - 监听营销活动与线索事件，同步外部变更
    """

    def __init__(self):
        super().__init__(
            name="市场Agent",
            domain="marketing",
            capabilities=[
                "campaign_management",    # 营销活动管理
                "brand_management",       # 品牌管理
                "lead_generation",        # 线索生成
                "market_analysis",        # 市场分析
            ],
            security_clearance="internal",
        )
        # 内部数据存储
        self._campaigns: dict[str, dict] = {}        # 营销活动
        self._leads: dict[str, dict] = {}             # 线索
        self._brand_assets: dict[str, dict] = {}      # 品牌资产
        self._reports: dict[str, dict] = {}           # 市场分析报告
        self._seed_data()

    @property
    def description(self) -> str:
        return "管理营销活动、线索生成与转化、品牌资产与市场分析，驱动市场运营全流程"

    # ========== 种子数据 ==========

    def _seed_data(self):
        """预置2个营销活动（含预算、渠道、状态）"""
        now = datetime.now(timezone.utc)

        seed_campaigns = [
            {
                "name": "2026夏季企业软件推广季",
                "budget": 500000,
                "channels": ["线上广告", "行业展会", "邮件营销", "社交媒体"],
                "start_date": "2026-06-01",
                "end_date": "2026-08-31",
                "target_audience": "中大型企业IT决策者",
                "status": "active",
            },
            {
                "name": "云服务新品发布会",
                "budget": 300000,
                "channels": ["线上直播", "媒体公关", "KOL合作"],
                "start_date": "2026-09-01",
                "end_date": "2026-09-30",
                "target_audience": "中小企业管理层",
                "status": "planning",
            },
        ]

        for camp in seed_campaigns:
            campaign_id = f"CAMP-{str(uuid4())[:8]}"
            self._campaigns[campaign_id] = {
                "campaign_id": campaign_id,
                "name": camp["name"],
                "budget": camp["budget"],
                "channels": camp["channels"],
                "start_date": camp["start_date"],
                "end_date": camp["end_date"],
                "target_audience": camp["target_audience"],
                "status": camp["status"],
                "actual_spend": 0.0,
                "revenue": 0.0,
                "leads_generated": 0,
                "created_at": now.isoformat(),
                "launched_at": now.isoformat() if camp["status"] == "active" else None,
                "completed_at": None,
                "results": {},
            }

        # 预置几条示例线索
        seed_leads = [
            {
                "name": "周明",
                "source": "线上广告",
                "contact_info": "zhouming@example.com",
                "interest": "ERP系统",
                "campaign_id": list(self._campaigns.keys())[0],
                "status": "new",
            },
            {
                "name": "吴秀英",
                "source": "行业展会",
                "contact_info": "wxy@example.com",
                "interest": "云服务",
                "campaign_id": list(self._campaigns.keys())[0],
                "status": "qualified",
            },
            {
                "name": "郑大海",
                "source": "邮件营销",
                "contact_info": "zhdahai@example.com",
                "interest": "自动化解决方案",
                "campaign_id": list(self._campaigns.keys())[0],
                "status": "new",
            },
        ]

        for ld in seed_leads:
            lead_id = f"LEAD-{str(uuid4())[:8]}"
            self._leads[lead_id] = {
                "lead_id": lead_id,
                "name": ld["name"],
                "source": ld["source"],
                "contact_info": ld["contact_info"],
                "interest": ld["interest"],
                "campaign_id": ld["campaign_id"],
                "status": ld["status"],
                "qualification_score": 0.0,
                "notes": "",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }

        # 更新活动线索计数
        for campaign in self._campaigns.values():
            campaign["leads_generated"] = sum(
                1 for l in self._leads.values()
                if l.get("campaign_id") == campaign["campaign_id"]
            )

        # 预置品牌资产
        self._brand_assets["BRAND-001"] = {
            "asset_id": "BRAND-001",
            "name": "企业Logo",
            "type": "logo",
            "version": "v2.0",
            "created_at": now.isoformat(),
        }

        logger.info("marketing_agent.seeded",
                    campaigns=len(self._campaigns),
                    leads=len(self._leads))

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅营销活动与线索事件"""
        self.subscribe_events(
            event_types=[
                EventType.CAMPAIGN_LAUNCHED,
                EventType.CAMPAIGN_COMPLETED,
                EventType.LEAD_GENERATED,
            ],
            handler=self._handle_event,
        )
        logger.info("marketing_agent.initialized", agent=self.name,
                    campaigns=len(self._campaigns))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("marketing_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        data = event.event_data

        if event.event_type == EventType.CAMPAIGN_LAUNCHED:
            # 外部系统启动的活动，同步到本地
            campaign_id = data.get("campaign_id")
            if campaign_id and campaign_id in self._campaigns:
                self._campaigns[campaign_id]["status"] = "active"
                self._campaigns[campaign_id]["launched_at"] = \
                    datetime.now(timezone.utc).isoformat()
            elif campaign_id:
                self._campaigns[campaign_id] = {
                    "campaign_id": campaign_id,
                    "name": data.get("name", ""),
                    "budget": data.get("budget", 0.0),
                    "channels": data.get("channels", []),
                    "start_date": data.get("start_date", ""),
                    "end_date": data.get("end_date", ""),
                    "target_audience": data.get("target_audience", ""),
                    "status": "active",
                    "actual_spend": 0.0,
                    "revenue": 0.0,
                    "leads_generated": 0,
                    "created_at": data.get("created_at",
                                           datetime.now(timezone.utc).isoformat()),
                    "launched_at": datetime.now(timezone.utc).isoformat(),
                    "completed_at": None,
                    "results": {},
                }
            logger.info("marketing_agent.campaign_launched_via_event",
                        campaign_id=campaign_id)

        elif event.event_type == EventType.CAMPAIGN_COMPLETED:
            campaign_id = data.get("campaign_id")
            if campaign_id and campaign_id in self._campaigns:
                self._campaigns[campaign_id]["status"] = "completed"
                self._campaigns[campaign_id]["completed_at"] = \
                    datetime.now(timezone.utc).isoformat()
                if "revenue" in data:
                    self._campaigns[campaign_id]["revenue"] = data["revenue"]
                if "actual_spend" in data:
                    self._campaigns[campaign_id]["actual_spend"] = \
                        data["actual_spend"]
                if "results" in data:
                    self._campaigns[campaign_id]["results"] = data["results"]
            logger.info("marketing_agent.campaign_completed_via_event",
                        campaign_id=campaign_id)

        elif event.event_type == EventType.LEAD_GENERATED:
            # 外部系统生成的线索，同步到本地
            lead_id = data.get("lead_id")
            if lead_id and lead_id not in self._leads:
                self._leads[lead_id] = {
                    "lead_id": lead_id,
                    "name": data.get("name", ""),
                    "source": data.get("source", ""),
                    "contact_info": data.get("contact_info", ""),
                    "interest": data.get("interest", ""),
                    "campaign_id": data.get("campaign_id", ""),
                    "status": "new",
                    "qualification_score": 0.0,
                    "notes": "",
                    "created_at": data.get("created_at",
                                           datetime.now(timezone.utc).isoformat()),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                # 更新活动线索计数
                campaign_id = data.get("campaign_id")
                if campaign_id and campaign_id in self._campaigns:
                    self._campaigns[campaign_id]["leads_generated"] += 1
            logger.info("marketing_agent.lead_synced",
                        lead_id=lead_id, source="event")

    # ========== 营销活动管理 ==========

    def get_campaigns(self, status: Optional[str] = None) -> list[dict]:
        """获取营销活动列表，支持按状态筛选"""
        campaigns = list(self._campaigns.values())
        if status:
            campaigns = [c for c in campaigns if c.get("status") == status]
        return campaigns

    def get_campaign(self, campaign_id: str) -> Optional[dict]:
        """获取单个营销活动详情"""
        return self._campaigns.get(campaign_id)

    def create_campaign(self, name: str, budget: float, channels: list[str],
                        start_date: str, end_date: str,
                        target_audience: str) -> dict:
        """创建营销活动"""
        campaign_id = f"CAMP-{str(uuid4())[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        campaign = {
            "campaign_id": campaign_id,
            "name": name,
            "budget": budget,
            "channels": channels,
            "start_date": start_date,
            "end_date": end_date,
            "target_audience": target_audience,
            "status": "planning",
            "actual_spend": 0.0,
            "revenue": 0.0,
            "leads_generated": 0,
            "created_at": now,
            "launched_at": None,
            "completed_at": None,
            "results": {},
        }
        self._campaigns[campaign_id] = campaign

        logger.info("marketing_agent.campaign_created",
                    campaign_id=campaign_id, name=name, budget=budget)
        return campaign

    async def launch_campaign(self, campaign_id: str) -> dict:
        """启动营销活动"""
        campaign = self._campaigns.get(campaign_id)
        if not campaign:
            raise ValueError(f"营销活动{campaign_id}不存在")

        campaign["status"] = "active"
        campaign["launched_at"] = datetime.now(timezone.utc).isoformat()

        logger.info("marketing_agent.campaign_launched",
                    campaign_id=campaign_id)

        # 发布活动启动事件
        await self._publish_event(
            EventType.CAMPAIGN_LAUNCHED,
            {
                "campaign_id": campaign_id,
                "name": campaign["name"],
                "budget": campaign["budget"],
                "channels": campaign["channels"],
                "status": "active",
            },
        )

        return campaign

    async def complete_campaign(self, campaign_id: str, results: dict) -> dict:
        """完成营销活动并记录结果"""
        campaign = self._campaigns.get(campaign_id)
        if not campaign:
            raise ValueError(f"营销活动{campaign_id}不存在")

        campaign["status"] = "completed"
        campaign["completed_at"] = datetime.now(timezone.utc).isoformat()
        campaign["results"] = results
        if "revenue" in results:
            campaign["revenue"] = results["revenue"]
        if "actual_spend" in results:
            campaign["actual_spend"] = results["actual_spend"]

        logger.info("marketing_agent.campaign_completed",
                    campaign_id=campaign_id)

        # 发布活动完成事件
        await self._publish_event(
            EventType.CAMPAIGN_COMPLETED,
            {
                "campaign_id": campaign_id,
                "name": campaign["name"],
                "revenue": campaign["revenue"],
                "actual_spend": campaign["actual_spend"],
                "results": results,
                "status": "completed",
            },
        )

        return campaign

    # ========== 线索管理 ==========

    def get_leads(self, source: Optional[str] = None,
                  status: Optional[str] = None) -> list[dict]:
        """获取线索列表，支持按来源和状态筛选"""
        leads = list(self._leads.values())
        if source:
            leads = [l for l in leads if l.get("source") == source]
        if status:
            leads = [l for l in leads if l.get("status") == status]
        return leads

    def add_lead(self, name: str, source: str, contact_info: str,
                 interest: str, campaign_id: Optional[str] = None) -> dict:
        """添加线索"""
        lead_id = f"LEAD-{str(uuid4())[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        lead = {
            "lead_id": lead_id,
            "name": name,
            "source": source,
            "contact_info": contact_info,
            "interest": interest,
            "campaign_id": campaign_id or "",
            "status": "new",
            "qualification_score": 0.0,
            "notes": "",
            "created_at": now,
            "updated_at": now,
        }
        self._leads[lead_id] = lead

        # 更新活动线索计数
        if campaign_id and campaign_id in self._campaigns:
            self._campaigns[campaign_id]["leads_generated"] += 1

        logger.info("marketing_agent.lead_added",
                    lead_id=lead_id, name=name, source=source)
        return lead

    def qualify_lead(self, lead_id: str, qualification_score: float,
                     notes: str) -> dict:
        """线索质量评估"""
        lead = self._leads.get(lead_id)
        if not lead:
            raise ValueError(f"线索{lead_id}不存在")

        lead["qualification_score"] = qualification_score
        lead["notes"] = notes
        # 评分>=60转为qualified，>=80转为hot
        if qualification_score >= 80:
            lead["status"] = "hot"
        elif qualification_score >= 60:
            lead["status"] = "qualified"
        else:
            lead["status"] = "unqualified"
        lead["updated_at"] = datetime.now(timezone.utc).isoformat()

        logger.info("marketing_agent.lead_qualified",
                    lead_id=lead_id,
                    score=qualification_score,
                    status=lead["status"])
        return lead

    # ========== ROI分析 ==========

    def get_campaign_roi(self, campaign_id: str) -> dict:
        """获取营销活动ROI"""
        campaign = self._campaigns.get(campaign_id)
        if not campaign:
            return {
                "campaign_id": campaign_id,
                "found": False,
                "roi": 0.0,
            }

        budget = campaign.get("budget", 0)
        actual_spend = campaign.get("actual_spend", 0) or budget
        revenue = campaign.get("revenue", 0)

        # ROI = (收入 - 投入) / 投入 * 100%
        if actual_spend > 0:
            roi = (revenue - actual_spend) / actual_spend * 100
        else:
            roi = 0.0

        # CPA (每次获取成本) = 投入 / 线索数
        leads = campaign.get("leads_generated", 0)
        cpa = actual_spend / leads if leads > 0 else 0.0

        return {
            "campaign_id": campaign_id,
            "campaign_name": campaign.get("name", ""),
            "status": campaign.get("status", ""),
            "budget": budget,
            "actual_spend": actual_spend,
            "revenue": revenue,
            "leads_generated": leads,
            "roi": round(roi, 2),
            "cpa": round(cpa, 2),
            "profit": round(revenue - actual_spend, 2),
            "found": True,
        }

    # ========== 统计 ==========

    def get_stats(self) -> dict:
        """获取市场统计信息"""
        active_campaigns = [c for c in self._campaigns.values()
                            if c.get("status") == "active"]
        completed_campaigns = [c for c in self._campaigns.values()
                               if c.get("status") == "completed"]
        planning_campaigns = [c for c in self._campaigns.values()
                              if c.get("status") == "planning"]

        total_budget = sum(c.get("budget", 0) for c in self._campaigns.values())
        total_spend = sum(c.get("actual_spend", 0) for c in self._campaigns.values())
        total_revenue = sum(c.get("revenue", 0) for c in self._campaigns.values())

        new_leads = [l for l in self._leads.values()
                     if l.get("status") == "new"]
        qualified_leads = [l for l in self._leads.values()
                           if l.get("status") in ("qualified", "hot")]

        return {
            "campaigns": len(self._campaigns),
            "active_campaigns": len(active_campaigns),
            "completed_campaigns": len(completed_campaigns),
            "planning_campaigns": len(planning_campaigns),
            "leads": len(self._leads),
            "new_leads": len(new_leads),
            "qualified_leads": len(qualified_leads),
            "brand_assets": len(self._brand_assets),
            "reports": len(self._reports),
            "total_budget": total_budget,
            "total_spend": total_spend,
            "total_revenue": total_revenue,
        }

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出市场决策"""
        action = context.get("action", "")

        if action == "launch_campaign":
            campaign_id = context.get("campaign_id")
            campaign = self._campaigns.get(campaign_id, {})
            budget = campaign.get("budget", 0)

            # 大预算活动需人工确认
            if budget > 500000:
                return AgentDecision(
                    decision_type="campaign_launch",
                    reasoning=(
                        f"活动'{campaign.get('name', '')}'预算{budget}元超过50万，"
                        f"需人工确认后启动"
                    ),
                    action=f"请求人工确认启动活动{campaign_id}",
                    confidence=0.9,
                    requires_human_approval=True,
                    context=context,
                )
            return AgentDecision(
                decision_type="campaign_launch",
                reasoning=f"活动'{campaign.get('name', '')}'预算{budget}元在阈值内，自动启动",
                action=f"启动营销活动{campaign_id}",
                confidence=0.85,
                requires_human_approval=False,
                context=context,
            )

        elif action == "qualify_lead":
            lead_id = context.get("lead_id")
            score = context.get("qualification_score", 0)
            lead = self._leads.get(lead_id, {})
            if score >= 80:
                return AgentDecision(
                    decision_type="lead_qualification",
                    reasoning=(
                        f"线索'{lead.get('name', '')}'评分{score}，"
                        f"为高质量线索，建议立即转交销售跟进"
                    ),
                    action=f"将线索{lead_id}标记为hot并转交销售",
                    confidence=0.9,
                    context=context,
                )
            return AgentDecision(
                decision_type="lead_qualification",
                reasoning=f"线索'{lead.get('name', '')}'评分{score}，进入培育流程",
                action=f"将线索{lead_id}加入培育计划",
                confidence=0.75,
                context=context,
            )

        elif action == "analyze_roi":
            campaign_id = context.get("campaign_id")
            roi = self.get_campaign_roi(campaign_id)
            if roi.get("roi", 0) < 0:
                return AgentDecision(
                    decision_type="campaign_analysis",
                    reasoning=(
                        f"活动{campaign_id}的ROI为{roi['roi']}%，"
                        f"为负收益，建议优化渠道投放策略"
                    ),
                    action="生成ROI分析报告并建议优化方案",
                    confidence=0.85,
                    context=context,
                )
            return AgentDecision(
                decision_type="campaign_analysis",
                reasoning=f"活动{campaign_id}的ROI为{roi['roi']}%，表现良好",
                action="记录ROI分析结果并推荐延续类似活动",
                confidence=0.8,
                context=context,
            )

        # 默认决策
        return AgentDecision(
            decision_type="marketing_general",
            reasoning="基于市场数据做出综合决策",
            action="执行市场常规操作",
            confidence=0.7,
            context=context,
        )
