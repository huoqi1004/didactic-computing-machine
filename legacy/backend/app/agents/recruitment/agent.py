"""
招聘 Agent
负责职位发布、候选人管理、面试安排与Offer发放。
监听候选人创建、面试安排与Offer发放事件，自动推进招聘流程。
"""
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class RecruitmentAgent(BaseAgent):
    """招聘 Agent - 管理招聘全流程"""

    # 候选人状态
    STATUS_NEW = "new"                # 新建
    STATUS_SCREENING = "screening"    # 筛选中
    STATUS_INTERVIEWING = "interviewing"  # 面试中
    STATUS_OFFERED = "offered"        # 已发Offer
    STATUS_ACCEPTED = "accepted"      # 已接受
    STATUS_REJECTED = "rejected"      # 已拒绝
    STATUS_ONBOARDED = "onboarded"    # 已入职

    # 面试状态
    INTERVIEW_SCHEDULED = "scheduled"
    INTERVIEW_COMPLETED = "completed"
    INTERVIEW_CANCELLED = "cancelled"

    # Offer状态
    OFFER_PENDING = "pending"
    OFFER_ACCEPTED = "accepted"
    OFFER_DECLINED = "declined"
    OFFER_EXPIRED = "expired"

    def __init__(self):
        super().__init__(
            name="招聘Agent",
            domain="recruitment",
            capabilities=[
                "candidate_management",
                "interview_scheduling",
                "offer_management",
                "job_posting",
                "general",
            ],
            security_clearance="confidential",
        )
        # 职位列表: position_id -> dict
        self._positions: dict[str, dict] = {}
        # 候选人: candidate_id -> dict
        self._candidates: dict[str, dict] = {}
        # 面试记录列表
        self._interviews: list[dict] = []
        # Offer记录列表
        self._offers: list[dict] = []
        # 编号计数器
        self._position_seq: int = 0
        self._candidate_seq: int = 0
        # 加载种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "职位发布、候选人管理、面试安排与Offer发放"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅招聘相关事件"""
        self.subscribe_events(
            event_types=[
                EventType.CANDIDATE_CREATED,
                EventType.INTERVIEW_SCHEDULED,
                EventType.OFFER_EXTENDED,
            ],
            handler=self._handle_event,
        )
        logger.info("recruitment_agent.initialized", agent=self.name,
                    positions=len(self._positions))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("recruitment_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        data = event.event_data

        if event.event_type == EventType.CANDIDATE_CREATED:
            await self._handle_candidate_created(data)
        elif event.event_type == EventType.INTERVIEW_SCHEDULED:
            await self._handle_interview_scheduled(data)
        elif event.event_type == EventType.OFFER_EXTENDED:
            await self._handle_offer_extended(data)

    async def _handle_candidate_created(self, data: dict):
        """处理候选人创建事件（同步外部数据）"""
        candidate_id = data.get("candidate_id", "")
        if candidate_id and candidate_id not in self._candidates:
            candidate = {
                "candidate_id": candidate_id,
                "name": data.get("name", ""),
                "position_id": data.get("position_id", ""),
                "source": data.get("source", "external"),
                "resume_summary": data.get("resume_summary", ""),
                "status": data.get("status", self.STATUS_NEW),
                "source_type": "event",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._candidates[candidate_id] = candidate
            logger.info("recruitment_agent.candidate_synced",
                        candidate_id=candidate_id)

    async def _handle_interview_scheduled(self, data: dict):
        """处理面试安排事件（同步外部数据）"""
        candidate_id = data.get("candidate_id", "")
        if candidate_id and candidate_id in self._candidates:
            interview = {
                "interview_id": data.get("interview_id", str(uuid4())),
                "candidate_id": candidate_id,
                "position_id": data.get("position_id", ""),
                "interview_date": data.get("interview_date", ""),
                "interviewer": data.get("interviewer", ""),
                "round_num": data.get("round_num", 1),
                "status": data.get("status", self.INTERVIEW_SCHEDULED),
                "source": "event",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._interviews.append(interview)
            # 更新候选人状态为面试中
            self._candidates[candidate_id]["status"] = self.STATUS_INTERVIEWING
            logger.info("recruitment_agent.interview_synced",
                        candidate_id=candidate_id)

    async def _handle_offer_extended(self, data: dict):
        """处理Offer发放事件（同步外部数据）"""
        candidate_id = data.get("candidate_id", "")
        if candidate_id and candidate_id in self._candidates:
            offer = {
                "offer_id": data.get("offer_id", str(uuid4())),
                "candidate_id": candidate_id,
                "position_id": data.get("position_id", ""),
                "salary": data.get("salary", 0),
                "position": data.get("position", ""),
                "start_date": data.get("start_date", ""),
                "status": data.get("status", self.OFFER_PENDING),
                "source": "event",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._offers.append(offer)
            # 更新候选人状态为已发Offer
            self._candidates[candidate_id]["status"] = self.STATUS_OFFERED
            logger.info("recruitment_agent.offer_synced",
                        candidate_id=candidate_id)

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出招聘决策"""
        action = context.get("action", "")

        if action == "schedule_interview":
            return await self._decide_interview(context)
        elif action == "extend_offer":
            return await self._decide_offer(context)
        elif action == "reject_candidate":
            return await self._decide_reject(context)

        return AgentDecision(
            decision_type="recruitment_unknown",
            reasoning=f"未知操作: {action}",
            action="拒绝执行",
            confidence=0.0,
            context=context,
        )

    async def _decide_interview(self, context: dict) -> AgentDecision:
        """面试安排决策"""
        candidate_id = context.get("candidate_id", "")
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return AgentDecision(
                decision_type="interview_scheduling",
                reasoning=f"候选人不存在: {candidate_id}",
                action="拒绝安排面试",
                confidence=1.0,
            )

        position = self._positions.get(candidate.get("position_id", ""))
        if not position:
            return AgentDecision(
                decision_type="interview_scheduling",
                reasoning=f"职位不存在: {candidate.get('position_id')}",
                action="拒绝安排面试",
                confidence=1.0,
            )

        if position.get("status") != "open":
            return AgentDecision(
                decision_type="interview_scheduling",
                reasoning=f"职位{position.get('title')}已关闭",
                action="拒绝安排面试",
                confidence=0.9,
            )

        return AgentDecision(
            decision_type="interview_scheduling",
            reasoning=f"候选人{candidate.get('name')}符合{position.get('title')}要求，安排面试",
            action="安排面试",
            confidence=0.85,
            context=context,
        )

    async def _decide_offer(self, context: dict) -> AgentDecision:
        """Offer发放决策"""
        candidate_id = context.get("candidate_id", "")
        salary = context.get("salary", 0)
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return AgentDecision(
                decision_type="offer_extension",
                reasoning=f"候选人不存在: {candidate_id}",
                action="拒绝发放Offer",
                confidence=1.0,
            )

        position = self._positions.get(candidate.get("position_id", ""))
        if position:
            salary_range = position.get("salary_range", {})
            max_salary = salary_range.get("max", 0)
            if max_salary and salary > max_salary:
                return AgentDecision(
                    decision_type="offer_extension",
                    reasoning=f"Offer薪资{salary}超出职位上限{max_salary}",
                    action="转人工审批",
                    confidence=0.8,
                    requires_human_approval=True,
                    context=context,
                )

        return AgentDecision(
            decision_type="offer_extension",
            reasoning=f"向候选人{candidate.get('name')}发放Offer，薪资{salary}元",
            action="发放Offer",
            confidence=0.9,
            requires_human_approval=True,
            context=context,
        )

    async def _decide_reject(self, context: dict) -> AgentDecision:
        """候选人淘汰决策"""
        candidate_id = context.get("candidate_id", "")
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return AgentDecision(
                decision_type="candidate_rejection",
                reasoning=f"候选人不存在: {candidate_id}",
                action="无法操作",
                confidence=0.0,
            )
        return AgentDecision(
            decision_type="candidate_rejection",
            reasoning=f"淘汰候选人{candidate.get('name')}",
            action="更新候选人状态为已拒绝",
            confidence=0.85,
            context=context,
        )

    # ========== 公开 API ==========

    def _generate_position_id(self) -> str:
        """生成职位编号"""
        self._position_seq += 1
        return f"POS{self._position_seq:04d}"

    def _generate_candidate_id(self) -> str:
        """生成候选人编号"""
        self._candidate_seq += 1
        return f"CAND{self._candidate_seq:04d}"

    def get_positions(self, status: Optional[str] = None) -> list[dict]:
        """获取职位列表，可按状态过滤"""
        positions = list(self._positions.values())
        if status:
            positions = [p for p in positions if p.get("status") == status]
        return positions

    def create_position(self, title: str, department: str,
                        requirements: list, headcount: int,
                        **kwargs) -> dict:
        """创建职位"""
        position_id = kwargs.get("position_id") or self._generate_position_id()
        now = datetime.now(timezone.utc).isoformat()
        position = {
            "position_id": position_id,
            "title": title,
            "department": department,
            "requirements": requirements,
            "headcount": headcount,
            "filled": 0,
            "salary_range": kwargs.get("salary_range", {}),
            "status": kwargs.get("status", "open"),
            "description": kwargs.get("description", ""),
            "created_at": now,
        }
        self._positions[position_id] = position
        logger.info("recruitment_agent.position_created",
                    position_id=position_id, title=title)
        return position

    def get_candidates(self, position_id: Optional[str] = None,
                       status: Optional[str] = None) -> list[dict]:
        """获取候选人列表，可按职位和状态过滤"""
        candidates = list(self._candidates.values())
        if position_id:
            candidates = [c for c in candidates if c.get("position_id") == position_id]
        if status:
            candidates = [c for c in candidates if c.get("status") == status]
        return candidates

    def add_candidate(self, name: str, position_id: str,
                      source: str, resume_summary: str,
                      **kwargs) -> dict:
        """新增候选人"""
        candidate_id = kwargs.get("candidate_id") or self._generate_candidate_id()
        now = datetime.now(timezone.utc).isoformat()
        candidate = {
            "candidate_id": candidate_id,
            "name": name,
            "position_id": position_id,
            "source": source,
            "resume_summary": resume_summary,
            "phone": kwargs.get("phone", ""),
            "email": kwargs.get("email", ""),
            "status": kwargs.get("status", self.STATUS_NEW),
            "source_type": "manual",
            "created_at": now,
        }
        self._candidates[candidate_id] = candidate
        logger.info("recruitment_agent.candidate_added",
                    candidate_id=candidate_id, name=name)
        return candidate

    def update_candidate_status(self, candidate_id: str,
                                status: str) -> dict:
        """更新候选人状态"""
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return {"error": f"候选人不存在: {candidate_id}"}
        candidate["status"] = status
        candidate["updated_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("recruitment_agent.candidate_status_updated",
                    candidate_id=candidate_id, status=status)
        return candidate

    def schedule_interview(self, candidate_id: str, interview_date: str,
                           interviewer: str, round_num: int = 1) -> dict:
        """安排面试"""
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return {"error": f"候选人不存在: {candidate_id}"}

        interview = {
            "interview_id": str(uuid4()),
            "candidate_id": candidate_id,
            "position_id": candidate.get("position_id", ""),
            "interview_date": interview_date,
            "interviewer": interviewer,
            "round_num": round_num,
            "status": self.INTERVIEW_SCHEDULED,
            "source": "manual",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._interviews.append(interview)
        # 更新候选人状态
        candidate["status"] = self.STATUS_INTERVIEWING
        candidate["updated_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("recruitment_agent.interview_scheduled",
                    candidate_id=candidate_id, round_num=round_num)
        return interview

    def get_interviews(self, candidate_id: Optional[str] = None,
                       status: Optional[str] = None) -> list[dict]:
        """获取面试记录，可按候选人和状态过滤"""
        interviews = list(self._interviews)
        if candidate_id:
            interviews = [i for i in interviews if i["candidate_id"] == candidate_id]
        if status:
            interviews = [i for i in interviews if i["status"] == status]
        return interviews

    def extend_offer(self, candidate_id: str, salary: float,
                     position: str, start_date: str) -> dict:
        """发放Offer"""
        candidate = self._candidates.get(candidate_id)
        if not candidate:
            return {"error": f"候选人不存在: {candidate_id}"}

        offer = {
            "offer_id": str(uuid4()),
            "candidate_id": candidate_id,
            "position_id": candidate.get("position_id", ""),
            "salary": salary,
            "position": position,
            "start_date": start_date,
            "status": self.OFFER_PENDING,
            "source": "manual",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._offers.append(offer)
        # 更新候选人状态
        candidate["status"] = self.STATUS_OFFERED
        candidate["updated_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("recruitment_agent.offer_extended",
                    candidate_id=candidate_id, salary=salary)
        return offer

    def get_offers(self, status: Optional[str] = None) -> list[dict]:
        """获取Offer记录，可按状态过滤"""
        offers = list(self._offers)
        if status:
            offers = [o for o in offers if o["status"] == status]
        return offers

    def get_stats(self) -> dict:
        """获取招聘统计信息"""
        all_candidates = list(self._candidates.values())
        open_positions = [p for p in self._positions.values()
                          if p.get("status") == "open"]

        by_status: dict[str, int] = {}
        for c in all_candidates:
            s = c.get("status", "unknown")
            by_status[s] = by_status.get(s, 0) + 1

        total_offers = len(self._offers)
        accepted_offers = len([o for o in self._offers
                               if o["status"] == self.OFFER_ACCEPTED])

        return {
            "total_positions": len(self._positions),
            "open_positions": len(open_positions),
            "total_candidates": len(all_candidates),
            "candidates_by_status": by_status,
            "total_interviews": len(self._interviews),
            "total_offers": total_offers,
            "accepted_offers": accepted_offers,
            "offer_acceptance_rate": (
                round(accepted_offers / total_offers, 2) if total_offers else 0
            ),
        }

    # ========== 种子数据 ==========

    def _seed_data(self):
        """加载种子数据：预置3个职位"""
        now = datetime.now(timezone.utc).isoformat()
        seed_positions = [
            {
                "position_id": "POS0001",
                "title": "Java工程师",
                "department": "技术部",
                "requirements": [
                    "3年以上Java开发经验",
                    "熟悉Spring Boot/Cloud框架",
                    "熟悉MySQL、Redis等数据库",
                    "良好的沟通能力与团队协作精神",
                ],
                "headcount": 2,
                "filled": 0,
                "salary_range": {"min": 15000, "max": 30000},
                "status": "open",
                "description": "负责后端服务开发与维护",
                "created_at": now,
            },
            {
                "position_id": "POS0002",
                "title": "产品经理",
                "department": "产品部",
                "requirements": [
                    "3年以上产品经理经验",
                    "熟悉产品规划与需求分析",
                    "具备数据驱动决策能力",
                    "优秀的跨部门协调能力",
                ],
                "headcount": 1,
                "filled": 0,
                "salary_range": {"min": 18000, "max": 35000},
                "status": "open",
                "description": "负责产品规划、需求管理与迭代推进",
                "created_at": now,
            },
            {
                "position_id": "POS0003",
                "title": "财务专员",
                "department": "财务部",
                "requirements": [
                    "财务、会计相关专业本科及以上学历",
                    "2年以上企业财务工作经验",
                    "熟悉用友/金蝶等财务软件",
                    "持有初级会计师及以上证书",
                ],
                "headcount": 1,
                "filled": 0,
                "salary_range": {"min": 8000, "max": 15000},
                "status": "open",
                "description": "负责日常账务处理与财务报表编制",
                "created_at": now,
            },
        ]

        for pos in seed_positions:
            self._positions[pos["position_id"]] = pos

        self._position_seq = 3
        logger.info("recruitment_agent.seed_data_loaded",
                    positions=len(self._positions))
