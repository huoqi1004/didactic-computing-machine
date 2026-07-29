# -*- coding: utf-8 -*-
"""
法务Agent
负责合同审查、法律咨询、诉讼管理与法规跟踪。
监听合同创建、合规违规与诉讼事件，提供专业法务支持。

职责：
- 监听 CONTRACT_CREATED 事件（新合同创建时自动审查）
- 监听 COMPLIANCE_VIOLATION_FOUND 事件（合规违规时评估法律风险）
- 监听 LAWSUIT_FILED 事件（诉讼案件登记）
- 合同审查：使用LLM分析合同文本，识别风险点并建议条款
- 法律咨询：接收并回答各部门法律咨询
- 诉讼管理：跟踪诉讼案件状态
- 法规跟踪：跟踪最新法律法规变化
"""
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class LegalAgent(BaseAgent):
    """
    法务Agent

    职责：
    - 监听合同创建、合规违规与诉讼事件
    - 合同审查：LLM优先分析合同文本，降级到规则检查
    - 法律咨询：接收各部门法律咨询并提供专业意见
    - 诉讼管理：跟踪诉讼案件全生命周期
    - 法规跟踪：跟踪最新法律法规变化并提示影响
    """

    def __init__(self):
        super().__init__(
            name="法务Agent",
            domain="legal",
            capabilities=[
                "contract_review",           # 合同审查
                "legal_consultation",        # 法律咨询
                "litigation_management",     # 诉讼管理
                "regulation_tracking",       # 法规跟踪
            ],
            security_clearance="confidential",
        )
        # 法律咨询记录：consultation_id -> 咨询详情
        self._consultations: dict[str, dict] = {}
        # 诉讼案件：lawsuit_id -> 案件详情
        self._lawsuits: dict[str, dict] = {}
        # 法规跟踪记录：list[dict]
        self._regulations: list[dict] = []

        # 初始化种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "合同审查、法律咨询、诉讼管理与法规跟踪"

    # ========== 初始化 ==========

    def _seed_data(self):
        """注入种子法规跟踪数据"""
        seed_regs = [
            {
                "regulation_id": f"REG-{str(uuid4())[:8]}",
                "title": "中华人民共和国劳动合同法",
                "category": "labor",
                "effective_date": "2008-01-01",
                "summary": (
                    "规范劳动合同的订立、履行、变更、解除和终止，"
                    "保护劳动者合法权益，构建和发展和谐稳定的劳动关系。"
                ),
                "key_articles": [
                    "第十条：建立劳动关系应当订立书面劳动合同",
                    "第十九条：劳动合同期限分为固定期限、无固定期限和以完成一定工作任务为期限",
                    "第四十条：用人单位提前三十日以书面形式通知劳动者本人或额外支付一个月工资后可解除劳动合同",
                ],
                "status": "active",
                "tracked_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "regulation_id": f"REG-{str(uuid4())[:8]}",
                "title": "中华人民共和国民法典（合同编）",
                "category": "contract",
                "effective_date": "2021-01-01",
                "summary": (
                    "规范合同的订立、效力、履行、保全、变更和转让、"
                    "合同的权利义务终止及违约责任，是合同法律关系的基本准则。"
                ),
                "key_articles": [
                    "第四百六十四条：合同是民事主体之间设立、变更、终止民事法律关系的协议",
                    "第五百零九条：当事人应当按照约定全面履行自己的义务",
                    "第五百七十七条：当事人一方不履行合同义务或履行不符合约定的，应承担违约责任",
                ],
                "status": "active",
                "tracked_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "regulation_id": f"REG-{str(uuid4())[:8]}",
                "title": "中华人民共和国公司法",
                "category": "corporate",
                "effective_date": "2024-07-01",
                "summary": (
                    "规范公司的组织和行为，保护公司、股东和债权人的合法权益，"
                    "维护社会经济秩序，促进社会主义市场经济的发展。"
                ),
                "key_articles": [
                    "第一条：为了规范公司的组织和行为，保护公司、股东和债权人的合法权益",
                    "第五十三条：股东按期足额缴纳公司章程规定的出资额",
                    "第二百一十一条：公司应当每年向股东披露财务会计报告",
                ],
                "status": "active",
                "tracked_at": datetime.now(timezone.utc).isoformat(),
            },
        ]
        self._regulations.extend(seed_regs)

    async def initialize(self):
        """初始化Agent：订阅合同创建、合规违规与诉讼事件"""
        self.subscribe_events(
            event_types=[
                EventType.CONTRACT_CREATED,
                EventType.COMPLIANCE_VIOLATION_FOUND,
                EventType.LAWSUIT_FILED,
            ],
            handler=self._handle_event,
        )
        logger.info("legal_agent.initialized", agent=self.name,
                    regulations=len(self._regulations),
                    consultations=len(self._consultations),
                    lawsuits=len(self._lawsuits))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("legal_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.CONTRACT_CREATED:
            # 新合同创建时自动审查
            return await self._handle_contract_created(event.event_data)
        elif event.event_type == EventType.COMPLIANCE_VIOLATION_FOUND:
            # 合规违规时评估法律风险
            return await self._handle_compliance_violation(event.event_data)
        elif event.event_type == EventType.LAWSUIT_FILED:
            # 诉讼案件自动登记
            return await self._handle_lawsuit_filed(event.event_data)

    async def _handle_contract_created(self, data: dict):
        """处理合同创建事件：自动审查合同"""
        contract_id = data.get("contract_id", "")
        contract_text = data.get("contract_text", data.get("terms", ""))
        contract_type = data.get("contract_type", data.get("type", "general"))
        amount = float(data.get("amount", 0) or 0)

        if contract_text:
            result = await self.review_contract(
                contract_id, contract_text, contract_type, amount
            )
            logger.info("legal_agent.auto_reviewed",
                        contract_id=contract_id,
                        risk_points=len(result.get("risk_points", [])))
            return result
        return None

    async def _handle_compliance_violation(self, data: dict):
        """处理合规违规事件：评估法律风险"""
        violations = data.get("violations", [])
        legal_risks = []
        for v in violations:
            legal_risks.append({
                "source": "compliance_violation",
                "description": v.get("description", ""),
                "regulation_id": v.get("regulation_id", ""),
                "legal_risk_level": "medium",
            })

        if legal_risks:
            decision = AgentDecision(
                decision_type="legal_risk_assessment",
                reasoning=(
                    f"合规违规发现{len(legal_risks)}项潜在法律风险，"
                    f"需法务部门跟进评估"
                ),
                action="标记法律风险并通知法务团队",
                confidence=0.8,
                requires_human_approval=True,
                context={
                    "source": "compliance_violation",
                    "legal_risks": legal_risks,
                    "violations": violations,
                },
            )
            await self.record_decision(decision)
        return legal_risks

    async def _handle_lawsuit_filed(self, data: dict):
        """处理诉讼立案事件：自动登记诉讼案件"""
        title = data.get("title", "外部立案诉讼")
        counterparty = data.get("counterparty", data.get("defendant", ""))
        amount = float(data.get("amount", 0) or 0)
        lawsuit_type = data.get("type", data.get("lawsuit_type", "civil"))
        description = data.get("description", data.get("reason", ""))

        lawsuit = await self.create_lawsuit(
            title=title,
            counterparty=counterparty,
            amount=amount,
            type=lawsuit_type,
            description=description,
        )
        return lawsuit

    # ========== 法律咨询管理 ==========

    def get_consultations(self, status: Optional[str] = None) -> list[dict]:
        """获取法律咨询记录列表，可按状态过滤"""
        consultations = list(self._consultations.values())
        if status:
            consultations = [
                c for c in consultations if c.get("status") == status
            ]
        return consultations

    async def create_consultation(
        self, title: str, question: str,
        department: str, priority: str = "normal",
    ) -> dict:
        """创建法律咨询记录"""
        consultation_id = f"CONS-{str(uuid4())[:8]}"
        consultation = {
            "consultation_id": consultation_id,
            "title": title,
            "question": question,
            "department": department,
            "priority": priority,
            "status": "pending",
            "answer": "",
            "legal_basis": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "answered_at": None,
        }
        self._consultations[consultation_id] = consultation

        logger.info("legal_agent.consultation_created",
                    consultation_id=consultation_id,
                    department=department, priority=priority)
        return consultation

    async def answer_consultation(
        self, consultation_id: str, answer: str,
        legal_basis: str,
    ) -> dict:
        """回答法律咨询"""
        consultation = self._consultations.get(consultation_id)
        if not consultation:
            return {"error": "consultation_not_found",
                    "consultation_id": consultation_id}

        # LLM增强：如果提供了部分答案，可使用LLM补充法律依据
        from app.core.config import settings
        final_answer = answer
        final_basis = legal_basis

        if settings.llm_enabled and not legal_basis:
            context = {
                "title": consultation["title"],
                "question": consultation["question"],
                "department": consultation["department"],
                "draft_answer": answer,
                "applicable_regulations": [
                    r for r in self._regulations
                    if r["status"] == "active"
                ],
            }
            prompt = (
                "请为以下法律咨询答案补充法律依据。"
                "返回JSON，包含 legal_basis(法律依据条文说明), "
                "additional_notes(补充建议)。"
            )
            try:
                result = await self.llm_analyze(prompt, context)
                if "error" not in result:
                    final_basis = str(result.get("legal_basis", ""))
                    notes = str(result.get("additional_notes", ""))
                    if notes:
                        final_answer = f"{answer}\n\n补充建议：{notes}"
            except Exception as e:
                logger.warning("legal_agent.llm_basis_error",
                               agent=self.name, error=str(e))

        consultation["answer"] = final_answer
        consultation["legal_basis"] = final_basis
        consultation["status"] = "answered"
        consultation["answered_at"] = datetime.now(timezone.utc).isoformat()

        decision = AgentDecision(
            decision_type="legal_consultation",
            reasoning=f"回答{consultation['department']}部门法律咨询：{consultation['title']}",
            action=f"提供法律意见（咨询编号{consultation_id}）",
            confidence=0.85,
            requires_human_approval=(consultation["priority"] == "urgent"),
            context={
                "consultation_id": consultation_id,
                "department": consultation["department"],
                "priority": consultation["priority"],
            },
        )
        await self.record_decision(decision)

        logger.info("legal_agent.consultation_answered",
                    consultation_id=consultation_id)
        return consultation

    # ========== 诉讼案件管理 ==========

    def get_lawsuits(self, status: Optional[str] = None) -> list[dict]:
        """获取诉讼案件列表，可按状态过滤"""
        lawsuits = list(self._lawsuits.values())
        if status:
            lawsuits = [
                l for l in lawsuits if l.get("status") == status
            ]
        return lawsuits

    async def create_lawsuit(
        self, title: str, counterparty: str, amount: float,
        type: str, description: str,
    ) -> dict:
        """创建诉讼案件"""
        lawsuit_id = f"CASE-{str(uuid4())[:8]}"
        lawsuit = {
            "lawsuit_id": lawsuit_id,
            "title": title,
            "counterparty": counterparty,
            "amount": float(amount),
            "type": type,
            "description": description,
            "status": "filed",
            "status_history": [
                {
                    "status": "filed",
                    "note": "案件立案登记",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._lawsuits[lawsuit_id] = lawsuit

        decision = AgentDecision(
            decision_type="lawsuit_filed",
            reasoning=(
                f"登记诉讼案件：{title}，对方当事人{counterparty}，"
                f"涉诉金额{amount:.2f}元，类型{type}"
            ),
            action=f"登记诉讼案件{lawsuit_id}",
            confidence=0.9,
            requires_human_approval=(amount > 500000),
            context={
                "lawsuit_id": lawsuit_id,
                "amount": amount,
                "type": type,
            },
        )
        await self.record_decision(decision)

        logger.info("legal_agent.lawsuit_created",
                    lawsuit_id=lawsuit_id,
                    counterparty=counterparty, amount=amount)
        return lawsuit

    async def update_lawsuit_status(
        self, lawsuit_id: str, status: str, update_note: str,
    ) -> dict:
        """更新诉讼案件状态"""
        lawsuit = self._lawsuits.get(lawsuit_id)
        if not lawsuit:
            return {"error": "lawsuit_not_found",
                    "lawsuit_id": lawsuit_id}

        lawsuit["status"] = status
        lawsuit["status_history"].append({
            "status": status,
            "note": update_note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        lawsuit["updated_at"] = datetime.now(timezone.utc).isoformat()

        logger.info("legal_agent.lawsuit_updated",
                    lawsuit_id=lawsuit_id, status=status)
        return lawsuit

    # ========== 法规跟踪管理 ==========

    def get_regulations(self, category: Optional[str] = None) -> list[dict]:
        """获取法规跟踪记录列表，可按类别过滤"""
        regulations = list(self._regulations)
        if category:
            regulations = [
                r for r in regulations if r.get("category") == category
            ]
        return regulations

    async def add_regulation(
        self, title: str, category: str,
        effective_date: str, summary: str,
    ) -> dict:
        """添加法规跟踪记录"""
        regulation_id = f"REG-{str(uuid4())[:8]}"
        regulation = {
            "regulation_id": regulation_id,
            "title": title,
            "category": category,
            "effective_date": effective_date,
            "summary": summary,
            "key_articles": [],
            "status": "active",
            "tracked_at": datetime.now(timezone.utc).isoformat(),
        }
        self._regulations.append(regulation)

        logger.info("legal_agent.regulation_added",
                    regulation_id=regulation_id, title=title)
        return regulation

    # ========== 合同审查 ==========

    async def review_contract(
        self, contract_id: str, contract_text: str,
        contract_type: str, amount: float,
    ) -> dict:
        """
        审查合同文本

        LLM优先分析合同文本，识别风险点并建议条款。
        LLM不可用时降级到规则审查。
        返回：{review_id, contract_id, opinions, risk_points, suggested_clauses, method}
        """
        review_id = f"REV-{str(uuid4())[:8]}"

        # 尝试LLM审查
        llm_result = await self._review_contract_llm(
            contract_id, contract_text, contract_type, amount
        )
        if llm_result is not None:
            result = llm_result
        else:
            result = self._review_contract_rules(
                contract_text, contract_type, amount
            )

        result["review_id"] = review_id
        result["contract_id"] = contract_id
        result["contract_type"] = contract_type
        result["amount"] = amount
        result["reviewed_at"] = datetime.now(timezone.utc).isoformat()

        # 记录决策
        risk_count = len(result.get("risk_points", []))
        decision = AgentDecision(
            decision_type="contract_review",
            reasoning=(
                f"审查合同{contract_id}（{contract_type}，金额{amount:.2f}元），"
                f"发现{risk_count}个风险点，"
                f"方法：{result.get('method', 'rules')}"
            ),
            action=f"完成合同审查{review_id}",
            confidence=0.85 if result.get("method") == "llm" else 0.75,
            requires_human_approval=(risk_count > 3 or amount > 1000000),
            context={
                "contract_id": contract_id,
                "review_id": review_id,
                "risk_count": risk_count,
                "method": result.get("method"),
            },
        )
        await self.record_decision(decision)

        logger.info("legal_agent.contract_reviewed",
                    contract_id=contract_id, review_id=review_id,
                    risk_points=risk_count,
                    method=result.get("method"))
        return result

    async def _review_contract_llm(
        self, contract_id: str, contract_text: str,
        contract_type: str, amount: float,
    ) -> Optional[dict]:
        """使用LLM审查合同，返回None表示不可用"""
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        context = {
            "contract_id": contract_id,
            "contract_type": contract_type,
            "amount": amount,
            "contract_text": contract_text[:4000],
            "applicable_regulations": [
                {
                    "title": r["title"],
                    "category": r["category"],
                    "key_articles": r.get("key_articles", []),
                }
                for r in self._regulations
                if r["status"] == "active"
                and (r["category"] == contract_type
                     or r["category"] == "contract")
            ],
        }
        prompt = (
            "请审查以下合同文本，从法务角度分析。"
            "返回JSON，包含 opinions(审查意见列表，每项含 issue, suggestion), "
            "risk_points(风险点列表，每项含 description, severity(high/medium/low), "
            "clause_reference), suggested_clauses(建议条款列表，每项含 "
            "clause_name, clause_text, reason), overall_risk_level(low/medium/high), "
            "reasoning。"
        )
        try:
            result = await self.llm_analyze(prompt, context)
        except Exception as e:
            logger.warning("legal_agent.llm_review_error",
                           agent=self.name, error=str(e))
            return None

        if "error" in result:
            return None

        return {
            "opinions": result.get("opinions", []),
            "risk_points": result.get("risk_points", []),
            "suggested_clauses": result.get("suggested_clauses", []),
            "overall_risk_level": str(result.get("overall_risk_level", "medium")),
            "reasoning": str(result.get("reasoning", "")),
            "method": "llm",
        }

    def _review_contract_rules(
        self, contract_text: str, contract_type: str, amount: float,
    ) -> dict:
        """规则合同审查（降级方案）"""
        risk_points = []
        opinions = []
        suggested_clauses = []

        text_lower = contract_text.lower() if contract_text else ""

        # 检查1：是否有违约责任条款
        has_breach_clause = any(
            kw in contract_text for kw in
            ["违约", "违约责任", "breach", "违约金"]
        ) if contract_text else False
        if not has_breach_clause:
            risk_points.append({
                "description": "合同缺少违约责任条款，无法约束对方违约行为",
                "severity": "high",
                "clause_reference": "全文",
            })
            suggested_clauses.append({
                "clause_name": "违约责任条款",
                "clause_text": (
                    "任何一方违反本合同约定的，应向守约方支付违约金，"
                    "并赔偿由此造成的全部损失。"
                ),
                "reason": "保障合同履行的基本约束",
            })

        # 检查2：是否有争议解决条款
        has_dispute_clause = any(
            kw in contract_text for kw in
            ["争议解决", "仲裁", "诉讼", "管辖", "dispute"]
        ) if contract_text else False
        if not has_dispute_clause:
            risk_points.append({
                "description": "合同缺少争议解决条款，发生纠纷时管辖不确定",
                "severity": "medium",
                "clause_reference": "全文",
            })
            suggested_clauses.append({
                "clause_name": "争议解决条款",
                "clause_text": (
                    "因本合同引起的或与本合同有关的任何争议，"
                    "双方应友好协商解决；协商不成的，"
                    "提交合同签订地有管辖权的人民法院诉讼解决。"
                ),
                "reason": "明确争议解决方式与管辖法院",
            })

        # 检查3：大额合同是否有担保条款
        if amount > 500000:
            has_guarantee = any(
                kw in contract_text for kw in
                ["担保", "保证", "抵押", "质押", "guarantee"]
            ) if contract_text else False
            if not has_guarantee:
                risk_points.append({
                    "description": (
                        f"大额合同（{amount:.2f}元）缺少担保条款，"
                        f"履约风险较高"
                    ),
                    "severity": "high",
                    "clause_reference": "全文",
                })
                suggested_clauses.append({
                    "clause_name": "担保条款",
                    "clause_text": "乙方应提供等额担保作为履约保证。",
                    "reason": "大额合同建议设置担保以降低风险",
                })

        # 检查4：是否有合同期限条款
        has_term = any(
            kw in contract_text for kw in
            ["期限", "有效期", "起止", "term", "duration"]
        ) if contract_text else False
        if not has_term:
            risk_points.append({
                "description": "合同缺少期限条款，合同有效期不明确",
                "severity": "medium",
                "clause_reference": "全文",
            })

        # 检查5：是否有保密条款
        has_confidentiality = any(
            kw in contract_text for kw in
            ["保密", "保密义务", "confidential"]
        ) if contract_text else False
        if not has_confidentiality:
            opinions.append({
                "issue": "合同未包含保密条款",
                "suggestion": "建议增加保密条款以保护商业秘密",
            })
            suggested_clauses.append({
                "clause_name": "保密条款",
                "clause_text": (
                    "双方对因履行本合同而知悉的对方商业秘密负有保密义务，"
                    "未经对方书面同意不得向第三方披露。"
                ),
                "reason": "保护双方商业秘密",
            })

        overall_risk = "high" if any(
            rp["severity"] == "high" for rp in risk_points
        ) else ("medium" if risk_points else "low")

        return {
            "opinions": opinions,
            "risk_points": risk_points,
            "suggested_clauses": suggested_clauses,
            "overall_risk_level": overall_risk,
            "reasoning": (
                f"规则审查完成：发现{len(risk_points)}个风险点，"
                f"整体风险等级{overall_risk}"
            ),
            "method": "rules",
        }

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出法务决策"""
        action_type = context.get("action_type", "review")

        if action_type == "review":
            contract_text = context.get("contract_text", "")
            contract_type = context.get("contract_type", "general")
            amount = float(context.get("amount", 0) or 0)
            result = self._review_contract_rules(
                contract_text, contract_type, amount
            )
            risk_count = len(result["risk_points"])
            if result["overall_risk_level"] == "high":
                return AgentDecision(
                    decision_type="contract_review",
                    reasoning=(
                        f"合同审查发现{risk_count}个风险点，"
                        f"整体风险等级为高，需人工复核"
                    ),
                    action="标记高风险合同，建议法务总监审批",
                    confidence=0.8,
                    requires_human_approval=True,
                    context=context,
                )
            return AgentDecision(
                decision_type="contract_review",
                reasoning=(
                    f"合同审查发现{risk_count}个风险点，"
                    f"整体风险等级为{result['overall_risk_level']}"
                ),
                action="合同审查通过，附审查意见",
                confidence=0.85,
                requires_human_approval=False,
                context=context,
            )

        elif action_type == "consultation":
            return AgentDecision(
                decision_type="legal_consultation",
                reasoning=(
                    f"处理{context.get('department', '')}部门"
                    f"法律咨询：{context.get('title', '')}"
                ),
                action="提供法律咨询意见",
                confidence=0.85,
                requires_human_approval=(
                    context.get("priority") == "urgent"
                ),
                context=context,
            )

        elif action_type == "litigation":
            amount = float(context.get("amount", 0) or 0)
            return AgentDecision(
                decision_type="litigation_management",
                reasoning=(
                    f"诉讼案件登记：{context.get('title', '')}，"
                    f"涉诉金额{amount:.2f}元"
                ),
                action="登记诉讼案件并跟踪管理",
                confidence=0.9,
                requires_human_approval=(amount > 500000),
                context=context,
            )

        return AgentDecision(
            decision_type="legal_general",
            reasoning="常规法务事务处理",
            action="处理法务事务",
            confidence=0.75,
            requires_human_approval=False,
            context=context,
        )

    async def on_tick(self):
        """周期性任务：检查法规跟踪状态"""
        # 检查是否有新法规需要关注（实际系统中可对接法规库API）
        pass

    # ========== 统计 ==========

    def get_stats(self) -> dict:
        """获取法务Agent统计数据"""
        consultations = list(self._consultations.values())
        lawsuits = list(self._lawsuits.values())

        pending_consultations = sum(
            1 for c in consultations if c["status"] == "pending"
        )
        answered_consultations = sum(
            1 for c in consultations if c["status"] == "answered"
        )
        active_lawsuits = sum(
            1 for l in lawsuits
            if l["status"] not in ("closed", "withdrawn", "settled")
        )
        total_lawsuit_amount = sum(
            l["amount"] for l in lawsuits
        )

        return {
            "consultations_total": len(consultations),
            "consultations_pending": pending_consultations,
            "consultations_answered": answered_consultations,
            "lawsuits_total": len(lawsuits),
            "lawsuits_active": active_lawsuits,
            "lawsuits_total_amount": round(total_lawsuit_amount, 2),
            "regulations_tracked": len(self._regulations),
            "reviews_completed": sum(
                1 for d in self._decision_history
                if d.decision_type == "contract_review"
            ),
        }
