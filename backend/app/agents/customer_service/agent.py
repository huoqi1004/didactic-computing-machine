"""
客服Agent
负责工单管理、投诉处理、满意度跟踪与知识库支持
监听工单与投诉事件，驱动客户服务全流程闭环
"""
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class CustomerServiceAgent(BaseAgent):
    """
    客服Agent

    职责：
    - 工单管理（创建、更新、分配、查询）
    - 投诉处理（创建、解决、跟踪）
    - 满意度调查（记录、统计）
    - 知识库支持（FAQ搜索、添加）
    - 监听工单与投诉事件，同步外部变更
    """

    def __init__(self):
        super().__init__(
            name="客服Agent",
            domain="customer_service",
            capabilities=[
                "ticket_management",       # 工单管理
                "complaint_handling",       # 投诉处理
                "satisfaction_tracking",    # 满意度跟踪
                "knowledge_support",        # 知识库支持
            ],
            security_clearance="internal",
        )
        # 内部数据存储
        self._tickets: dict[str, dict] = {}            # 工单
        self._complaints: dict[str, dict] = {}          # 投诉
        self._surveys: dict[str, dict] = {}             # 满意度调查
        self._faq: dict[str, dict] = {}                 # FAQ
        self._seed_data()

    @property
    def description(self) -> str:
        return "管理工单、投诉处理、满意度跟踪与FAQ知识库，驱动客户服务全流程闭环"

    # ========== 种子数据 ==========

    def _seed_data(self):
        """预置5个FAQ条目和3个示例工单"""
        now = datetime.now(timezone.utc)

        # FAQ种子数据
        seed_faq = [
            {
                "question": "如何重置系统密码？",
                "answer": "请点击登录页面的'忘记密码'链接，输入注册邮箱，"
                          "系统将发送密码重置链接到您的邮箱，点击链接即可重置密码。",
                "category": "账户管理",
            },
            {
                "question": "系统支持哪些浏览器？",
                "answer": "系统支持Chrome 90+、Firefox 88+、Edge 90+、Safari 14+。"
                          "建议使用最新版Chrome浏览器以获得最佳体验。",
                "category": "技术支持",
            },
            {
                "question": "如何导出报表数据？",
                "answer": "进入报表页面，点击右上角的'导出'按钮，"
                          "选择导出格式（Excel/PDF/CSV），系统将生成并下载报表文件。",
                "category": "功能使用",
            },
            {
                "question": "订阅套餐如何升级？",
                "answer": "登录后进入'账户设置'->'订阅管理'，"
                          "选择目标套餐点击'升级'，按提示完成支付即可。"
                          "升级后差价按剩余周期比例收取。",
                "category": " billing",
            },
            {
                "question": "数据备份频率是怎样的？",
                "answer": "系统每日自动进行全量数据备份，每小时进行增量备份。"
                          "备份数据保留30天，企业版用户可申请更长保留周期。",
                "category": "数据安全",
            },
        ]

        for faq in seed_faq:
            faq_id = f"FAQ-{str(uuid4())[:8]}"
            self._faq[faq_id] = {
                "faq_id": faq_id,
                "question": faq["question"],
                "answer": faq["answer"],
                "category": faq["category"],
                "created_at": now.isoformat(),
            }

        # 工单种子数据
        seed_tickets = [
            {
                "customer_id": "CUST-SEED-001",
                "customer_name": "华盛科技有限公司",
                "subject": "系统登录失败",
                "description": "用户反馈使用Chrome浏览器无法登录系统，"
                               "页面提示'连接超时'，已尝试清除缓存但问题依旧。",
                "priority": "high",
                "category": "技术支持",
                "status": "in_progress",
                "assigned_agent": "AGT-001",
            },
            {
                "customer_id": "CUST-SEED-002",
                "customer_name": "恒泰制造集团",
                "subject": "报表导出格式错误",
                "description": "用户导出Excel报表时，部分数值字段显示为文本格式，"
                               "导致无法直接进行求和计算。",
                "priority": "medium",
                "category": "功能使用",
                "status": "open",
                "assigned_agent": None,
            },
            {
                "customer_id": "CUST-SEED-003",
                "customer_name": "瑞达金融服务",
                "subject": "咨询订阅升级方案",
                "description": "客户希望了解企业版与专业版的功能差异，"
                               "以及升级到企业版的费用和流程。",
                "priority": "low",
                "category": " billing",
                "status": "resolved",
                "assigned_agent": "AGT-002",
                "resolution": "已向客户详细说明企业版功能，发送报价单，"
                              "客户表示将内部评估后回复。",
            },
        ]

        for tk in seed_tickets:
            ticket_id = f"TKT-{str(uuid4())[:8]}"
            self._tickets[ticket_id] = {
                "ticket_id": ticket_id,
                "customer_id": tk["customer_id"],
                "customer_name": tk["customer_name"],
                "subject": tk["subject"],
                "description": tk["description"],
                "priority": tk["priority"],
                "category": tk["category"],
                "status": tk["status"],
                "assigned_agent": tk["assigned_agent"],
                "resolution": tk.get("resolution", ""),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "resolved_at": now.isoformat() if tk["status"] == "resolved" else None,
            }

        logger.info("customer_service_agent.seeded",
                    tickets=len(self._tickets),
                    faq=len(self._faq))

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅工单与投诉事件"""
        self.subscribe_events(
            event_types=[
                EventType.TICKET_CREATED,
                EventType.TICKET_RESOLVED,
                EventType.COMPLAINT_RECEIVED,
            ],
            handler=self._handle_event,
        )
        logger.info("customer_service_agent.initialized", agent=self.name,
                    tickets=len(self._tickets))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("customer_service_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        data = event.event_data

        if event.event_type == EventType.TICKET_CREATED:
            # 外部系统创建的工单，同步到本地（避免重复）
            ticket_id = data.get("ticket_id")
            if ticket_id and ticket_id not in self._tickets:
                self._tickets[ticket_id] = {
                    "ticket_id": ticket_id,
                    "customer_id": data.get("customer_id", ""),
                    "customer_name": data.get("customer_name", ""),
                    "subject": data.get("subject", ""),
                    "description": data.get("description", ""),
                    "priority": data.get("priority", "medium"),
                    "category": data.get("category", "general"),
                    "status": data.get("status", "open"),
                    "assigned_agent": data.get("assigned_agent"),
                    "resolution": "",
                    "created_at": data.get("created_at",
                                           datetime.now(timezone.utc).isoformat()),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "resolved_at": None,
                }
                logger.info("customer_service_agent.ticket_synced",
                            ticket_id=ticket_id, source="event")

        elif event.event_type == EventType.TICKET_RESOLVED:
            ticket_id = data.get("ticket_id")
            if ticket_id and ticket_id in self._tickets:
                self._tickets[ticket_id]["status"] = "resolved"
                self._tickets[ticket_id]["resolution"] = \
                    data.get("resolution", "")
                self._tickets[ticket_id]["resolved_at"] = \
                    datetime.now(timezone.utc).isoformat()
                self._tickets[ticket_id]["updated_at"] = \
                    datetime.now(timezone.utc).isoformat()
                logger.info("customer_service_agent.ticket_resolved_via_event",
                            ticket_id=ticket_id)

        elif event.event_type == EventType.COMPLAINT_RECEIVED:
            # 外部系统收到的投诉，同步到本地
            complaint_id = data.get("complaint_id")
            if complaint_id and complaint_id not in self._complaints:
                self._complaints[complaint_id] = {
                    "complaint_id": complaint_id,
                    "customer_id": data.get("customer_id", ""),
                    "customer_name": data.get("customer_name", ""),
                    "subject": data.get("subject", ""),
                    "description": data.get("description", ""),
                    "severity": data.get("severity", "medium"),
                    "category": data.get("category", "general"),
                    "status": "open",
                    "resolution": "",
                    "follow_up": False,
                    "created_at": data.get("created_at",
                                           datetime.now(timezone.utc).isoformat()),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "resolved_at": None,
                }
                logger.info("customer_service_agent.complaint_synced",
                            complaint_id=complaint_id, source="event")

    # ========== 工单管理 ==========

    def get_tickets(self, status: Optional[str] = None,
                    priority: Optional[str] = None,
                    customer_id: Optional[str] = None) -> list[dict]:
        """获取工单列表，支持按状态、优先级和客户筛选"""
        tickets = list(self._tickets.values())
        if status:
            tickets = [t for t in tickets if t.get("status") == status]
        if priority:
            tickets = [t for t in tickets if t.get("priority") == priority]
        if customer_id:
            tickets = [t for t in tickets if t.get("customer_id") == customer_id]
        return tickets

    def get_ticket(self, ticket_id: str) -> Optional[dict]:
        """获取单个工单详情"""
        return self._tickets.get(ticket_id)

    def create_ticket(self, customer_id: str, subject: str, description: str,
                      priority: str, category: str) -> dict:
        """创建工单"""
        ticket_id = f"TKT-{str(uuid4())[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        ticket = {
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "customer_name": "",
            "subject": subject,
            "description": description,
            "priority": priority,
            "category": category,
            "status": "open",
            "assigned_agent": None,
            "resolution": "",
            "created_at": now,
            "updated_at": now,
            "resolved_at": None,
        }
        self._tickets[ticket_id] = ticket

        logger.info("customer_service_agent.ticket_created",
                    ticket_id=ticket_id,
                    customer_id=customer_id,
                    priority=priority)
        return ticket

    def update_ticket_status(self, ticket_id: str, status: str,
                             resolution: Optional[str] = None) -> dict:
        """更新工单状态"""
        ticket = self._tickets.get(ticket_id)
        if not ticket:
            raise ValueError(f"工单{ticket_id}不存在")

        ticket["status"] = status
        if resolution:
            ticket["resolution"] = resolution
        ticket["updated_at"] = datetime.now(timezone.utc).isoformat()

        if status == "resolved":
            ticket["resolved_at"] = datetime.now(timezone.utc).isoformat()
            if not ticket["resolution"]:
                ticket["resolution"] = resolution or ""

        logger.info("customer_service_agent.ticket_status_updated",
                    ticket_id=ticket_id, status=status)
        return ticket

    def assign_ticket(self, ticket_id: str, agent_id: str) -> dict:
        """分配工单给客服人员"""
        ticket = self._tickets.get(ticket_id)
        if not ticket:
            raise ValueError(f"工单{ticket_id}不存在")

        ticket["assigned_agent"] = agent_id
        if ticket["status"] == "open":
            ticket["status"] = "in_progress"
        ticket["updated_at"] = datetime.now(timezone.utc).isoformat()

        logger.info("customer_service_agent.ticket_assigned",
                    ticket_id=ticket_id, agent_id=agent_id)
        return ticket

    # ========== 投诉管理 ==========

    def get_complaints(self, status: Optional[str] = None,
                       severity: Optional[str] = None) -> list[dict]:
        """获取投诉列表，支持按状态和严重程度筛选"""
        complaints = list(self._complaints.values())
        if status:
            complaints = [c for c in complaints if c.get("status") == status]
        if severity:
            complaints = [c for c in complaints if c.get("severity") == severity]
        return complaints

    def create_complaint(self, customer_id: str, subject: str,
                         description: str, severity: str,
                         category: str) -> dict:
        """创建投诉"""
        complaint_id = f"COMP-{str(uuid4())[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        complaint = {
            "complaint_id": complaint_id,
            "customer_id": customer_id,
            "customer_name": "",
            "subject": subject,
            "description": description,
            "severity": severity,
            "category": category,
            "status": "open",
            "resolution": "",
            "follow_up": False,
            "created_at": now,
            "updated_at": now,
            "resolved_at": None,
        }
        self._complaints[complaint_id] = complaint

        logger.info("customer_service_agent.complaint_created",
                    complaint_id=complaint_id,
                    customer_id=customer_id,
                    severity=severity)
        return complaint

    def resolve_complaint(self, complaint_id: str, resolution: str,
                          follow_up: bool = False) -> dict:
        """解决投诉"""
        complaint = self._complaints.get(complaint_id)
        if not complaint:
            raise ValueError(f"投诉{complaint_id}不存在")

        complaint["status"] = "resolved"
        complaint["resolution"] = resolution
        complaint["follow_up"] = follow_up
        complaint["resolved_at"] = datetime.now(timezone.utc).isoformat()
        complaint["updated_at"] = datetime.now(timezone.utc).isoformat()

        logger.info("customer_service_agent.complaint_resolved",
                    complaint_id=complaint_id,
                    follow_up=follow_up)
        return complaint

    # ========== 满意度调查 ==========

    def get_satisfaction_surveys(self, customer_id: Optional[str] = None,
                                 period: Optional[str] = None) -> list[dict]:
        """获取满意度调查列表，支持按客户和周期筛选"""
        surveys = list(self._surveys.values())
        if customer_id:
            surveys = [s for s in surveys if s.get("customer_id") == customer_id]
        if period:
            surveys = [s for s in surveys if s.get("period") == period]
        return surveys

    def record_satisfaction(self, customer_id: str, ticket_id: str,
                            score: int, feedback: str) -> dict:
        """记录满意度调查"""
        survey_id = f"SVY-{str(uuid4())[:8]}"
        now = datetime.now(timezone.utc)
        # 周期标识：YYYY-MM
        period = now.strftime("%Y-%m")

        survey = {
            "survey_id": survey_id,
            "customer_id": customer_id,
            "ticket_id": ticket_id,
            "score": score,          # 1-5分
            "feedback": feedback,
            "period": period,
            "created_at": now.isoformat(),
        }
        self._surveys[survey_id] = survey

        logger.info("customer_service_agent.satisfaction_recorded",
                    survey_id=survey_id,
                    customer_id=customer_id,
                    score=score)
        return survey

    # ========== FAQ知识库 ==========

    def search_faq(self, query: str) -> list[dict]:
        """搜索FAQ，支持关键词模糊匹配"""
        query_lower = query.lower()
        results = []
        for faq in self._faq.values():
            # 在问题和答案中搜索关键词
            question = faq.get("question", "").lower()
            answer = faq.get("answer", "").lower()
            category = faq.get("category", "").lower()
            if (query_lower in question or
                    query_lower in answer or
                    query_lower in category):
                results.append(faq)
        return results

    def add_faq(self, question: str, answer: str, category: str) -> dict:
        """添加FAQ条目"""
        faq_id = f"FAQ-{str(uuid4())[:8]}"
        faq = {
            "faq_id": faq_id,
            "question": question,
            "answer": answer,
            "category": category,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._faq[faq_id] = faq

        logger.info("customer_service_agent.faq_added",
                    faq_id=faq_id, question=question)
        return faq

    # ========== 统计 ==========

    def get_stats(self) -> dict:
        """获取客服统计信息"""
        open_tickets = [t for t in self._tickets.values()
                        if t.get("status") == "open"]
        in_progress_tickets = [t for t in self._tickets.values()
                               if t.get("status") == "in_progress"]
        resolved_tickets = [t for t in self._tickets.values()
                            if t.get("status") == "resolved"]

        open_complaints = [c for c in self._complaints.values()
                           if c.get("status") == "open"]
        resolved_complaints = [c for c in self._complaints.values()
                               if c.get("status") == "resolved"]

        # 满意度平均分
        scores = [s.get("score", 0) for s in self._surveys.values()]
        avg_satisfaction = sum(scores) / len(scores) if scores else 0.0

        # 工单解决率
        total_tickets = len(self._tickets)
        resolution_rate = (
            (len(resolved_tickets) / total_tickets * 100)
            if total_tickets > 0 else 0.0
        )

        return {
            "tickets": total_tickets,
            "open_tickets": len(open_tickets),
            "in_progress_tickets": len(in_progress_tickets),
            "resolved_tickets": len(resolved_tickets),
            "resolution_rate": round(resolution_rate, 2),
            "complaints": len(self._complaints),
            "open_complaints": len(open_complaints),
            "resolved_complaints": len(resolved_complaints),
            "surveys": len(self._surveys),
            "avg_satisfaction": round(avg_satisfaction, 2),
            "faq_entries": len(self._faq),
        }

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出客服决策"""
        action = context.get("action", "")

        if action == "create_ticket":
            priority = context.get("priority", "medium")
            # 紧急工单需立即分配
            if priority == "urgent":
                return AgentDecision(
                    decision_type="ticket_priority",
                    reasoning=(
                        f"工单优先级为'紧急'，需立即分配客服人员并启动加急处理流程"
                    ),
                    action="立即分配工单并通知值班主管",
                    confidence=0.95,
                    requires_human_approval=False,
                    context=context,
                )
            elif priority == "high":
                return AgentDecision(
                    decision_type="ticket_priority",
                    reasoning="工单优先级为'高'，建议在2小时内分配处理",
                    action="将工单加入高优先级队列并分配客服",
                    confidence=0.9,
                    context=context,
                )
            return AgentDecision(
                decision_type="ticket_creation",
                reasoning="常规工单，按标准流程处理",
                action="创建工单并加入处理队列",
                confidence=0.85,
                context=context,
            )

        elif action == "handle_complaint":
            severity = context.get("severity", "medium")
            if severity == "critical":
                return AgentDecision(
                    decision_type="complaint_handling",
                    reasoning=(
                        "投诉严重程度为'严重'，需立即升级处理，"
                        "通知管理层并启动专项调查"
                    ),
                    action="升级投诉至管理层并启动专项调查",
                    confidence=0.95,
                    requires_human_approval=True,
                    context=context,
                )
            elif severity == "high":
                return AgentDecision(
                    decision_type="complaint_handling",
                    reasoning="投诉严重程度为'高'，需在24小时内响应处理",
                    action="分配专人处理投诉并设定24小时响应时限",
                    confidence=0.9,
                    context=context,
                )
            return AgentDecision(
                decision_type="complaint_handling",
                reasoning="常规投诉，按标准流程处理",
                action="创建投诉记录并分配处理人员",
                confidence=0.8,
                context=context,
            )

        elif action == "search_faq":
            query = context.get("query", "")
            results = self.search_faq(query)
            if results:
                return AgentDecision(
                    decision_type="knowledge_support",
                    reasoning=f"在FAQ知识库中找到{len(results)}条匹配结果",
                    action="返回FAQ匹配结果给客户",
                    confidence=0.85,
                    context={**context, "results_count": len(results)},
                )
            return AgentDecision(
                decision_type="knowledge_support",
                reasoning="FAQ知识库中未找到匹配结果，建议转人工客服",
                action="转接人工客服处理",
                confidence=0.7,
                context=context,
            )

        # 默认决策
        return AgentDecision(
            decision_type="customer_service_general",
            reasoning="基于客服数据做出综合决策",
            action="执行客服常规操作",
            confidence=0.7,
            context=context,
        )
