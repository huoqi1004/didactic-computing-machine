# -*- coding: utf-8 -*-
"""
风险Agent
负责风险识别、风险评估、风险监控与风险缓解。
监听风险识别、安全警报、合规违规与内审警报事件，
维护企业风险登记册并生成风险矩阵。

职责：
- 监听 RISK_IDENTIFIED 事件（新风险识别时自动登记）
- 监听 RISK_ASSESSED 事件（风险评估完成时更新登记册）
- 监听 SECURITY_ALERT 事件（安全警报转化为风险预警）
- 监听 COMPLIANCE_VIOLATION_FOUND 事件（合规违规转化为风险）
- 监听 AUDIT_ALERT_RAISED 事件（内审警报转化为风险）
- 风险识别：使用LLM分析业务数据识别潜在风险
- 风险评估：基于概率×影响计算风险等级
- 风险矩阵：生成概率×影响的二维风险矩阵
- 风险缓解：制定并跟踪风险缓解措施
"""
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class RiskAgent(BaseAgent):
    """
    风险Agent

    职责：
    - 监听风险识别、安全警报、合规违规与内审警报事件
    - 维护企业风险登记册（风险全生命周期管理）
    - 风险评估：基于概率×影响计算风险等级
    - 生成风险矩阵（概率×影响的二维矩阵）
    - 风险预警：监控风险变化并发出预警
    - 风险缓解：制定并跟踪风险缓解措施
    """

    # 风险等级阈值（基于概率×影响的乘积）
    RISK_LEVEL_LOW = 4        # 1-4: 低风险
    RISK_LEVEL_MEDIUM = 9     # 5-9: 中风险
    RISK_LEVEL_HIGH = 15      # 10-15: 高风险
    # 16-25: 极高风险

    def __init__(self):
        super().__init__(
            name="风险Agent",
            domain="risk",
            capabilities=[
                "risk_identification",      # 风险识别
                "risk_assessment",          # 风险评估
                "risk_monitoring",          # 风险监控
                "risk_mitigation",          # 风险缓解
            ],
            security_clearance="restricted",
        )
        # 风险登记册：risk_id -> 风险详情
        self._risk_register: dict[str, dict] = {}
        # 风险评估记录：list[dict]
        self._assessments: list[dict] = []
        # 风险预警：list[dict]
        self._alerts: list[dict] = []

        # 初始化种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "风险识别、风险评估、风险监控与风险缓解"

    # ========== 初始化 ==========

    def _seed_data(self):
        """注入种子风险数据：5个预置风险"""
        seed_risks = [
            {
                "risk_id": f"RSK-{str(uuid4())[:8]}",
                "title": "财务风险",
                "category": "financial",
                "description": (
                    "企业面临现金流断裂、债务违约、汇率波动等"
                    "财务风险，可能导致资金链断裂或财务损失。"
                ),
                "source": "seed",
                "probability": 3,
                "impact": 4,
                "risk_level": "medium",
                "risk_score": 12,
                "owner": "财务部",
                "status": "identified",
                "mitigation_plan": "",
                "due_date": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "risk_id": f"RSK-{str(uuid4())[:8]}",
                "title": "合规风险",
                "category": "compliance",
                "description": (
                    "企业可能面临法规变化、合规违规、行政处罚等"
                    "合规风险，可能导致罚款或业务限制。"
                ),
                "source": "seed",
                "probability": 2,
                "impact": 5,
                "risk_level": "medium",
                "risk_score": 10,
                "owner": "合规部",
                "status": "identified",
                "mitigation_plan": "",
                "due_date": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "risk_id": f"RSK-{str(uuid4())[:8]}",
                "title": "运营风险",
                "category": "operational",
                "description": (
                    "企业运营过程中可能面临流程中断、人员失误、"
                    "系统故障等运营风险，影响业务连续性。"
                ),
                "source": "seed",
                "probability": 4,
                "impact": 3,
                "risk_level": "medium",
                "risk_score": 12,
                "owner": "运营部",
                "status": "identified",
                "mitigation_plan": "",
                "due_date": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "risk_id": f"RSK-{str(uuid4())[:8]}",
                "title": "市场风险",
                "category": "market",
                "description": (
                    "企业面临市场需求变化、竞争加剧、价格波动等"
                    "市场风险，可能导致市场份额下降或收入减少。"
                ),
                "source": "seed",
                "probability": 3,
                "impact": 4,
                "risk_level": "medium",
                "risk_score": 12,
                "owner": "市场部",
                "status": "identified",
                "mitigation_plan": "",
                "due_date": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "risk_id": f"RSK-{str(uuid4())[:8]}",
                "title": "技术风险",
                "category": "technology",
                "description": (
                    "企业面临技术落后、数据泄露、系统安全等"
                    "技术风险，可能导致竞争优势丧失或数据损失。"
                ),
                "source": "seed",
                "probability": 2,
                "impact": 5,
                "risk_level": "medium",
                "risk_score": 10,
                "owner": "技术部",
                "status": "identified",
                "mitigation_plan": "",
                "due_date": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        ]
        for risk in seed_risks:
            self._risk_register[risk["risk_id"]] = risk

    async def initialize(self):
        """初始化Agent：订阅风险、安全、合规与内审事件"""
        self.subscribe_events(
            event_types=[
                EventType.RISK_IDENTIFIED,
                EventType.RISK_ASSESSED,
                EventType.SECURITY_ALERT,
                EventType.COMPLIANCE_VIOLATION_FOUND,
                EventType.AUDIT_ALERT_RAISED,
            ],
            handler=self._handle_event,
        )
        logger.info("risk_agent.initialized", agent=self.name,
                    risks=len(self._risk_register),
                    assessments=len(self._assessments),
                    alerts=len(self._alerts))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("risk_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.RISK_IDENTIFIED:
            # 新风险识别事件：自动登记
            return await self._handle_risk_identified(event.event_data)
        elif event.event_type == EventType.RISK_ASSESSED:
            # 风险评估完成事件：更新登记册
            return await self._handle_risk_assessed(event.event_data)
        elif event.event_type == EventType.SECURITY_ALERT:
            # 安全警报：转化为风险预警
            return await self._handle_security_alert(event.event_data)
        elif event.event_type == EventType.COMPLIANCE_VIOLATION_FOUND:
            # 合规违规：转化为合规风险
            return await self._handle_compliance_violation(event.event_data)
        elif event.event_type == EventType.AUDIT_ALERT_RAISED:
            # 内审警报：转化为运营风险
            return await self._handle_audit_alert(event.event_data)

    async def _handle_risk_identified(self, data: dict):
        """处理风险识别事件：自动登记到风险登记册"""
        title = data.get("title", "外部识别风险")
        category = data.get("category", "general")
        description = data.get("description", "")
        source = data.get("source", "event")

        risk = await self.identify_risk(
            title=title, category=category,
            description=description, source=source,
        )
        return risk

    async def _handle_risk_assessed(self, data: dict):
        """处理风险评估完成事件：更新风险登记册"""
        risk_id = data.get("risk_id", "")
        probability = int(data.get("probability", 0) or 0)
        impact = int(data.get("impact", 0) or 0)
        owner = data.get("owner", "")

        if risk_id in self._risk_register and probability and impact:
            return await self.assess_risk(
                risk_id, probability, impact, owner,
            )
        return None

    async def _handle_security_alert(self, data: dict):
        """处理安全警报：转化为风险预警"""
        risk_id = data.get("risk_id", "")
        severity = data.get("severity", "medium")
        message = data.get("description", data.get("message", "安全警报"))

        # 尝试关联到现有风险，否则创建新风险
        if risk_id not in self._risk_register:
            risk = await self.identify_risk(
                title=f"安全风险-{message[:30]}",
                category="security",
                description=message,
                source="security_alert",
            )
            risk_id = risk["risk_id"]

        alert = await self.create_alert(
            risk_id=risk_id,
            severity=severity,
            message=f"安全警报：{message}",
        )
        return alert

    async def _handle_compliance_violation(self, data: dict):
        """处理合规违规：转化为合规风险"""
        violations = data.get("violations", [])
        descriptions = "; ".join(
            v.get("description", "") for v in violations
        ) if violations else "合规违规"

        risk = await self.identify_risk(
            title=f"合规风险-{descriptions[:30]}",
            category="compliance",
            description=f"合规违规发现：{descriptions}",
            source="compliance_violation",
        )
        return risk

    async def _handle_audit_alert(self, data: dict):
        """处理内审警报：转化为运营风险"""
        alert_type = data.get("alert_type", "audit")
        severity = data.get("severity", "medium")
        description = data.get("description", "内审警报")

        risk = await self.identify_risk(
            title=f"运营风险-内审警报({alert_type})",
            category="operational",
            description=f"内审警报：{description}",
            source="audit_alert",
        )

        alert = await self.create_alert(
            risk_id=risk["risk_id"],
            severity=severity,
            message=f"内审警报：{description}",
        )
        return alert

    # ========== 风险登记册管理 ==========

    def get_risks(
        self, category: Optional[str] = None,
        level: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        """获取风险列表，可按类别、等级、状态过滤"""
        risks = list(self._risk_register.values())
        if category:
            risks = [r for r in risks if r.get("category") == category]
        if level:
            risks = [r for r in risks if r.get("risk_level") == level]
        if status:
            risks = [r for r in risks if r.get("status") == status]
        return risks

    def get_risk(self, risk_id: str) -> Optional[dict]:
        """获取单个风险详情"""
        return self._risk_register.get(risk_id)

    async def identify_risk(
        self, title: str, category: str,
        description: str, source: str,
    ) -> dict:
        """识别新风险并登记到风险登记册"""
        risk_id = f"RSK-{str(uuid4())[:8]}"
        risk = {
            "risk_id": risk_id,
            "title": title,
            "category": category,
            "description": description,
            "source": source,
            "probability": 0,
            "impact": 0,
            "risk_level": "unassessed",
            "risk_score": 0,
            "owner": "",
            "status": "identified",
            "mitigation_plan": "",
            "due_date": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._risk_register[risk_id] = risk

        # 发布风险识别事件
        await self._publish_event(
            EventType.RISK_IDENTIFIED,
            {
                "risk_id": risk_id,
                "title": title,
                "category": category,
                "source": source,
                "description": description,
            },
        )

        # 记录决策
        decision = AgentDecision(
            decision_type="risk_identification",
            reasoning=f"识别新风险：{title}（类别：{category}，来源：{source}）",
            action=f"登记风险{risk_id}到风险登记册",
            confidence=0.85,
            requires_human_approval=False,
            context={
                "risk_id": risk_id,
                "category": category,
                "source": source,
            },
        )
        await self.record_decision(decision)

        logger.info("risk_agent.risk_identified",
                    risk_id=risk_id, title=title, category=category)
        return risk

    async def assess_risk(
        self, risk_id: str, probability: int,
        impact: int, owner: str,
    ) -> dict:
        """
        评估风险

        风险等级 = 概率(1-5) × 影响(1-5)
        - 1-4: 低风险
        - 5-9: 中风险
        - 10-15: 高风险
        - 16-25: 极高风险
        """
        risk = self._risk_register.get(risk_id)
        if not risk:
            return {"error": "risk_not_found", "risk_id": risk_id}

        # 确保概率和影响在1-5范围内
        probability = max(1, min(5, int(probability)))
        impact = max(1, min(5, int(impact)))
        risk_score = probability * impact

        # 计算风险等级
        if risk_score <= self.RISK_LEVEL_LOW:
            risk_level = "low"
        elif risk_score <= self.RISK_LEVEL_MEDIUM:
            risk_level = "medium"
        elif risk_score <= self.RISK_LEVEL_HIGH:
            risk_level = "high"
        else:
            risk_level = "critical"

        risk["probability"] = probability
        risk["impact"] = impact
        risk["risk_score"] = risk_score
        risk["risk_level"] = risk_level
        risk["owner"] = owner
        risk["status"] = "assessed"
        risk["updated_at"] = datetime.now(timezone.utc).isoformat()

        # 记录评估记录
        assessment = {
            "assessment_id": f"ASM-{str(uuid4())[:8]}",
            "risk_id": risk_id,
            "probability": probability,
            "impact": impact,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "owner": owner,
            "assessed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._assessments.append(assessment)

        # 发布风险评估事件
        await self._publish_event(
            EventType.RISK_ASSESSED,
            {
                "risk_id": risk_id,
                "probability": probability,
                "impact": impact,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "owner": owner,
            },
        )

        # 高风险自动创建预警
        if risk_level in ("high", "critical"):
            await self.create_alert(
                risk_id=risk_id,
                severity=risk_level,
                message=(
                    f"风险'{risk['title']}'评估为{risk_level}级风险"
                    f"（概率{probability}×影响{impact}={risk_score}）"
                ),
            )

        # 记录决策
        decision = AgentDecision(
            decision_type="risk_assessment",
            reasoning=(
                f"评估风险'{risk['title']}'：概率{probability}×影响{impact}"
                f"={risk_score}，等级{risk_level}，责任人{owner}"
            ),
            action=f"完成风险评估{assessment['assessment_id']}",
            confidence=0.9,
            requires_human_approval=(risk_level == "critical"),
            context={
                "risk_id": risk_id,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "owner": owner,
            },
        )
        await self.record_decision(decision)

        logger.info("risk_agent.risk_assessed",
                    risk_id=risk_id, risk_score=risk_score,
                    risk_level=risk_level)
        return risk

    async def mitigate_risk(
        self, risk_id: str, action: str,
        mitigation_plan: str, due_date: str,
    ) -> dict:
        """制定风险缓解措施"""
        risk = self._risk_register.get(risk_id)
        if not risk:
            return {"error": "risk_not_found", "risk_id": risk_id}

        risk["status"] = "mitigating"
        risk["mitigation_plan"] = mitigation_plan
        risk["due_date"] = due_date
        risk["updated_at"] = datetime.now(timezone.utc).isoformat()

        # 发布风险缓解事件
        await self._publish_event(
            EventType.RISK_MITIGATED,
            {
                "risk_id": risk_id,
                "action": action,
                "mitigation_plan": mitigation_plan,
                "due_date": due_date,
            },
        )

        # 记录决策
        decision = AgentDecision(
            decision_type="risk_mitigation",
            reasoning=(
                f"制定风险'{risk['title']}'的缓解措施：{action}，"
                f"截止日期{due_date}"
            ),
            action=f"启动风险缓解{risk_id}",
            confidence=0.85,
            requires_human_approval=False,
            context={
                "risk_id": risk_id,
                "action": action,
                "due_date": due_date,
            },
        )
        await self.record_decision(decision)

        logger.info("risk_agent.risk_mitigated",
                    risk_id=risk_id, action=action)
        return risk

    # ========== 风险预警管理 ==========

    def get_alerts(self, severity: Optional[str] = None) -> list[dict]:
        """获取风险预警列表，可按严重程度过滤"""
        alerts = list(self._alerts)
        if severity:
            alerts = [a for a in alerts if a.get("severity") == severity]
        return alerts

    async def create_alert(
        self, risk_id: str, severity: str, message: str,
    ) -> dict:
        """创建风险预警"""
        alert_id = f"ALR-{str(uuid4())[:8]}"
        alert = {
            "alert_id": alert_id,
            "risk_id": risk_id,
            "severity": severity,
            "message": message,
            "status": "open",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "resolved_at": None,
        }
        self._alerts.append(alert)

        # 发布风险预警事件
        await self._publish_event(
            EventType.RISK_ALERT,
            {
                "alert_id": alert_id,
                "risk_id": risk_id,
                "severity": severity,
                "message": message,
            },
        )

        logger.warning("risk_agent.alert_created",
                       alert_id=alert_id, risk_id=risk_id,
                       severity=severity)
        return alert

    # ========== 风险矩阵 ==========

    def get_risk_matrix(self) -> dict:
        """
        生成风险矩阵：概率(1-5) × 影响(1-5) 的二维矩阵

        返回：
        - matrix: 5x5二维数组，每个单元格包含该区域的风险列表
        - summary: 各等级风险数量统计
        - assessed_count: 已评估风险数量
        - unassessed_count: 未评估风险数量
        """
        # 初始化5x5矩阵（概率为行，影响为列）
        matrix = [[[] for _ in range(5)] for _ in range(5)]

        for risk in self._risk_register.values():
            prob = risk.get("probability", 0)
            impact = risk.get("impact", 0)
            if prob >= 1 and impact >= 1:
                matrix[prob - 1][impact - 1].append({
                    "risk_id": risk["risk_id"],
                    "title": risk["title"],
                    "category": risk["category"],
                    "risk_score": risk.get("risk_score", 0),
                    "risk_level": risk.get("risk_level", "unassessed"),
                    "owner": risk.get("owner", ""),
                    "status": risk.get("status", ""),
                })

        # 统计各等级风险数量
        level_counts = {"low": 0, "medium": 0, "high": 0, "critical": 0,
                        "unassessed": 0}
        for risk in self._risk_register.values():
            level = risk.get("risk_level", "unassessed")
            if level in level_counts:
                level_counts[level] += 1
            else:
                level_counts["unassessed"] += 1

        total = len(self._risk_register)
        assessed = sum(
            1 for r in self._risk_register.values()
            if r.get("risk_level") != "unassessed"
        )

        return {
            "matrix": matrix,
            "summary": {
                "total_risks": total,
                "assessed": assessed,
                "unassessed": total - assessed,
                "level_distribution": level_counts,
            },
            "axis_labels": {
                "probability": ["1-极低", "2-低", "3-中", "4-高", "5-极高"],
                "impact": ["1-极低", "2-低", "3-中", "4-高", "5-极高"],
            },
            "level_thresholds": {
                "low": f"1-{self.RISK_LEVEL_LOW}",
                "medium": f"{self.RISK_LEVEL_LOW + 1}-{self.RISK_LEVEL_MEDIUM}",
                "high": f"{self.RISK_LEVEL_MEDIUM + 1}-{self.RISK_LEVEL_HIGH}",
                "critical": f"{self.RISK_LEVEL_HIGH + 1}-25",
            },
        }

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出风险决策"""
        action_type = context.get("action_type", "assess")

        if action_type == "identify":
            return AgentDecision(
                decision_type="risk_identification",
                reasoning=(
                    f"识别新风险：{context.get('title', '')}"
                    f"（类别：{context.get('category', '')}）"
                ),
                action="登记风险到风险登记册",
                confidence=0.85,
                requires_human_approval=False,
                context=context,
            )

        elif action_type == "assess":
            risk_id = context.get("risk_id", "")
            risk = self._risk_register.get(risk_id)
            if not risk:
                return AgentDecision(
                    decision_type="error",
                    reasoning=f"风险{risk_id}不存在",
                    action="无法做出风险评估决策",
                    confidence=0.0,
                    context=context,
                )

            risk_level = risk.get("risk_level", "unassessed")
            if risk_level == "critical":
                return AgentDecision(
                    decision_type="risk_assessment",
                    reasoning=(
                        f"风险'{risk['title']}'为极高风险"
                        f"（评分{risk.get('risk_score', 0)}），"
                        f"需立即启动应急响应"
                    ),
                    action="启动极高风险应急响应流程",
                    confidence=0.95,
                    requires_human_approval=True,
                    context=context,
                )
            elif risk_level == "high":
                return AgentDecision(
                    decision_type="risk_assessment",
                    reasoning=(
                        f"风险'{risk['title']}'为高风险"
                        f"（评分{risk.get('risk_score', 0)}），"
                        f"需制定缓解计划"
                    ),
                    action="制定高风险缓解计划",
                    confidence=0.9,
                    requires_human_approval=True,
                    context=context,
                )
            return AgentDecision(
                decision_type="risk_assessment",
                reasoning=(
                    f"风险'{risk['title']}'为{risk_level}风险"
                    f"（评分{risk.get('risk_score', 0)}），"
                    f"常规监控"
                ),
                action="纳入常规风险监控",
                confidence=0.85,
                requires_human_approval=False,
                context=context,
            )

        elif action_type == "mitigate":
            return AgentDecision(
                decision_type="risk_mitigation",
                reasoning=(
                    f"制定风险缓解措施：{context.get('action', '')}"
                ),
                action="启动风险缓解计划",
                confidence=0.85,
                requires_human_approval=False,
                context=context,
            )

        return AgentDecision(
            decision_type="risk_general",
            reasoning="常规风险管理事务",
            action="处理风险管理事务",
            confidence=0.75,
            requires_human_approval=False,
            context=context,
        )

    async def on_tick(self):
        """周期性任务：检查高风险预警和到期缓解措施"""
        # 检查是否有未处理的极高风险预警
        open_critical_alerts = [
            a for a in self._alerts
            if a["severity"] == "critical" and a["status"] == "open"
        ]
        if open_critical_alerts:
            logger.warning("risk_agent.critical_alerts_pending",
                           agent=self.name,
                           count=len(open_critical_alerts))

    # ========== 统计 ==========

    def get_stats(self) -> dict:
        """获取风险Agent统计数据"""
        risks = list(self._risk_register.values())

        level_distribution = {"low": 0, "medium": 0, "high": 0,
                              "critical": 0, "unassessed": 0}
        category_distribution: dict[str, int] = {}
        for risk in risks:
            level = risk.get("risk_level", "unassessed")
            if level in level_distribution:
                level_distribution[level] += 1
            else:
                level_distribution["unassessed"] += 1
            cat = risk.get("category", "unknown")
            category_distribution[cat] = category_distribution.get(cat, 0) + 1

        open_alerts = sum(1 for a in self._alerts if a["status"] == "open")
        mitigating = sum(
            1 for r in risks if r.get("status") == "mitigating"
        )

        return {
            "risks_total": len(risks),
            "risks_assessed": sum(
                1 for r in risks
                if r.get("risk_level") != "unassessed"
            ),
            "risks_unassessed": sum(
                1 for r in risks
                if r.get("risk_level") == "unassessed"
            ),
            "risks_mitigating": mitigating,
            "level_distribution": level_distribution,
            "category_distribution": category_distribution,
            "alerts_total": len(self._alerts),
            "alerts_open": open_alerts,
            "assessments_total": len(self._assessments),
        }
