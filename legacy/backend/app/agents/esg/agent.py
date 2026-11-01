"""
ESG Agent
负责环境、社会与治理（ESG）指标跟踪、报告生成、碳排放记录与社会责任活动管理。
监听ESG报告生成、目标更新、审计完成与合规检查事件，
自动更新ESG指标并生成综合ESG评分。
"""
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class ESGAgent(BaseAgent):
    """
    ESG Agent - 环境、社会与治理管理

    职责：
    - ESG指标跟踪：维护E/S/G三大类指标及其目标值
    - ESG报告生成：按周期生成完整或分项ESG报告
    - 碳排放记录：记录范围1/2/3碳排放数据
    - 社会责任活动：管理公益活动与社会责任项目
    - ESG综合评分：基于各指标达成率计算综合评分
    """

    def __init__(self):
        super().__init__(
            name="ESG Agent",
            domain="esg",
            capabilities=[
                "esg_reporting",              # ESG报告
                "carbon_tracking",            # 碳排放跟踪
                "social_responsibility",      # 社会责任
                "governance_assessment",      # 治理评估
                "general",
            ],
            security_clearance="internal",
        )
        # ESG指标: indicator_id -> dict
        self._indicators: dict[str, dict] = {}
        # ESG报告: report_id -> dict
        self._reports: dict[str, dict] = {}
        # 碳排放记录列表
        self._carbon_records: list[dict] = []
        # 社会责任活动列表
        self._social_activities: list[dict] = []
        # 指标编号计数器
        self._indicator_seq: int = 0
        # 报告编号计数器
        self._report_seq: int = 0
        # 加载种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "ESG指标跟踪、报告生成、碳排放记录与社会责任活动管理"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅ESG相关事件"""
        self.subscribe_events(
            event_types=[
                EventType.ESG_REPORT_GENERATED,
                EventType.ESG_TARGET_UPDATED,
                EventType.AUDIT_COMPLETED,
                EventType.COMPLIANCE_CHECK_PASSED,
            ],
            handler=self._handle_event,
        )
        logger.info("esg_agent.initialized", agent=self.name,
                    indicators=len(self._indicators))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("esg_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        data = event.event_data

        if event.event_type == EventType.ESG_REPORT_GENERATED:
            await self._handle_report_generated(data)
        elif event.event_type == EventType.ESG_TARGET_UPDATED:
            await self._handle_target_updated(data)
        elif event.event_type == EventType.AUDIT_COMPLETED:
            await self._handle_audit_completed(data)
        elif event.event_type == EventType.COMPLIANCE_CHECK_PASSED:
            await self._handle_compliance_passed(data)

    async def _handle_report_generated(self, data: dict):
        """处理ESG报告生成事件（同步外部报告数据）"""
        report_id = data.get("report_id", "")
        if report_id and report_id not in self._reports:
            report = {
                "report_id": report_id,
                "period": data.get("period", ""),
                "scope": data.get("scope", "full"),
                "title": data.get("title", ""),
                "data": data.get("data", {}),
                "score": data.get("score", {}),
                "status": "generated",
                "created_at": data.get("created_at",
                                        datetime.now(timezone.utc).isoformat()),
            }
            self._reports[report_id] = report
            logger.info("esg_agent.report_synced", report_id=report_id)

    async def _handle_target_updated(self, data: dict):
        """处理ESG目标更新事件"""
        indicator_id = data.get("indicator_id", "")
        if indicator_id and indicator_id in self._indicators:
            new_target = data.get("target")
            if new_target is not None:
                self._indicators[indicator_id]["target"] = float(new_target)
                self._indicators[indicator_id]["updated_at"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                logger.info("esg_agent.target_updated",
                            indicator_id=indicator_id,
                            new_target=new_target)

    async def _handle_audit_completed(self, data: dict):
        """处理审计完成事件，更新合规率指标"""
        for indicator in self._indicators.values():
            if indicator.get("name") == "合规率":
                indicator["current"] = 100.0
                indicator["updated_at"] = datetime.now(timezone.utc).isoformat()
                logger.info("esg_agent.compliance_indicator_updated",
                            audit_id=data.get("audit_id", ""))
                break

    async def _handle_compliance_passed(self, data: dict):
        """处理合规检查通过事件，更新合规率指标"""
        for indicator in self._indicators.values():
            if indicator.get("name") == "合规率":
                # 合规检查通过，维持100%合规率
                indicator["current"] = 100.0
                indicator["updated_at"] = datetime.now(timezone.utc).isoformat()
                break

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出ESG决策"""
        action = context.get("action", "")

        if action == "generate_report":
            return await self._decide_generate_report(context)
        elif action == "update_indicator":
            return await self._decide_update_indicator(context)
        elif action == "record_carbon":
            return await self._decide_record_carbon(context)

        return AgentDecision(
            decision_type="esg_unknown",
            reasoning=f"未知操作: {action}",
            action="拒绝执行",
            confidence=0.0,
            context=context,
        )

    async def _decide_generate_report(self, context: dict) -> AgentDecision:
        """报告生成决策"""
        period = context.get("period", "")
        if not period:
            return AgentDecision(
                decision_type="esg_report_generation",
                reasoning="报告周期不能为空",
                action="拒绝生成",
                confidence=1.0,
                context=context,
            )
        return AgentDecision(
            decision_type="esg_report_generation",
            reasoning=f"生成{period}周期ESG报告",
            action="生成ESG报告",
            confidence=0.9,
            context=context,
        )

    async def _decide_update_indicator(self, context: dict) -> AgentDecision:
        """指标更新决策"""
        indicator_id = context.get("indicator_id", "")
        indicator = self._indicators.get(indicator_id)
        if not indicator:
            return AgentDecision(
                decision_type="esg_indicator_update",
                reasoning=f"指标不存在: {indicator_id}",
                action="拒绝更新",
                confidence=1.0,
                context=context,
            )
        value = context.get("value")
        if value is None:
            return AgentDecision(
                decision_type="esg_indicator_update",
                reasoning="指标值不能为空",
                action="拒绝更新",
                confidence=1.0,
                context=context,
            )
        # 指标恶化超过阈值需人工确认
        target = indicator.get("target", 0)
        current = indicator.get("current", 0)
        if target > 0 and current > 0:
            current_ratio = current / target
            new_ratio = float(value) / target if target > 0 else 0
            if new_ratio < current_ratio * 0.8:
                return AgentDecision(
                    decision_type="esg_indicator_update",
                    reasoning=f"指标{indicator.get('name')}恶化超过20%，需人工确认",
                    action="转人工确认",
                    confidence=0.75,
                    requires_human_approval=True,
                    context=context,
                )
        return AgentDecision(
            decision_type="esg_indicator_update",
            reasoning=f"更新指标{indicator.get('name')}为{value}",
            action="更新指标",
            confidence=0.9,
            context=context,
        )

    async def _decide_record_carbon(self, context: dict) -> AgentDecision:
        """碳排放记录决策"""
        amount = context.get("amount", 0)
        if amount <= 0:
            return AgentDecision(
                decision_type="carbon_recording",
                reasoning="碳排放量必须大于0",
                action="拒绝记录",
                confidence=1.0,
                context=context,
            )
        return AgentDecision(
            decision_type="carbon_recording",
            reasoning=f"记录{context.get('scope', '')}碳排放{amount}{context.get('unit', '')}",
            action="记录碳排放",
            confidence=0.9,
            context=context,
        )

    # ========== 公开 API ==========

    def _generate_indicator_id(self) -> str:
        """生成指标编号"""
        self._indicator_seq += 1
        return f"ESG{self._indicator_seq:04d}"

    def _generate_report_id(self) -> str:
        """生成报告编号"""
        self._report_seq += 1
        return f"ESGRPT{self._report_seq:04d}"

    def get_indicators(self, category: Optional[str] = None) -> list[dict]:
        """获取ESG指标列表，可按类别过滤（E/S/G）"""
        indicators = list(self._indicators.values())
        if category:
            indicators = [i for i in indicators if i.get("category") == category]
        return indicators

    def get_indicator(self, indicator_id: str) -> Optional[dict]:
        """获取单个ESG指标"""
        return self._indicators.get(indicator_id)

    def update_indicator(self, indicator_id: str, value: float,
                         period: str) -> dict:
        """更新ESG指标值"""
        indicator = self._indicators.get(indicator_id)
        if not indicator:
            return {"error": f"指标不存在: {indicator_id}"}
        old_value = indicator["current"]
        indicator["current"] = float(value)
        indicator["period"] = period
        indicator["updated_at"] = datetime.now(timezone.utc).isoformat()
        # 计算达成率
        target = indicator.get("target", 0)
        if target > 0:
            indicator["achievement_rate"] = float(value) / target
        # 记录历史
        if "history" not in indicator:
            indicator["history"] = []
        indicator["history"].append({
            "value": float(value),
            "period": period,
            "old_value": old_value,
            "updated_at": indicator["updated_at"],
        })
        logger.info("esg_agent.indicator_updated",
                    indicator_id=indicator_id, value=value, period=period)
        return indicator

    def get_reports(self, period: Optional[str] = None) -> list[dict]:
        """获取ESG报告列表，可按周期过滤"""
        reports = list(self._reports.values())
        if period:
            reports = [r for r in reports if r.get("period") == period]
        return reports

    async def generate_report(self, period: str,
                              scope: str = "full") -> dict:
        """生成ESG报告"""
        report_id = self._generate_report_id()
        now = datetime.now(timezone.utc).isoformat()
        # 按scope筛选指标
        if scope == "full":
            indicators = list(self._indicators.values())
        else:
            # scope可能是E/S/G
            indicators = [
                i for i in self._indicators.values()
                if i.get("category") == scope
            ]
        # 构建报告数据
        score = self._calc_esg_score()
        report_data = {
            "indicators": [
                {
                    "id": i["indicator_id"],
                    "name": i["name"],
                    "category": i["category"],
                    "current": i["current"],
                    "target": i["target"],
                    "unit": i["unit"],
                    "achievement_rate": i.get("achievement_rate", 0),
                }
                for i in indicators
            ],
            "carbon_summary": self._carbon_summary(),
            "social_activities_count": len(self._social_activities),
        }
        report = {
            "report_id": report_id,
            "period": period,
            "scope": scope,
            "title": f"ESG报告-{period}",
            "data": report_data,
            "score": score,
            "status": "generated",
            "created_at": now,
        }
        self._reports[report_id] = report

        # 发布ESG报告生成事件
        await self._publish_event(
            EventType.ESG_REPORT_GENERATED,
            {
                "report_id": report_id,
                "period": period,
                "scope": scope,
                "title": report["title"],
                "esg_score": score.get("total", 0),
            },
        )
        logger.info("esg_agent.report_generated",
                    report_id=report_id, period=period, scope=scope)
        return report

    def record_carbon(self, scope: str, amount: float, unit: str,
                      source: str, date: str) -> dict:
        """记录碳排放数据"""
        record = {
            "record_id": str(uuid4()),
            "scope": scope,           # scope1/scope2/scope3
            "amount": float(amount),
            "unit": unit,             # tCO2e
            "source": source,         # 排放源
            "date": date,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._carbon_records.append(record)
        logger.info("esg_agent.carbon_recorded",
                    scope=scope, amount=amount, source=source)
        return record

    def get_carbon_records(self, scope: Optional[str] = None,
                           period: Optional[str] = None) -> list[dict]:
        """获取碳排放记录，可按范围和周期过滤"""
        records = list(self._carbon_records)
        if scope:
            records = [r for r in records if r.get("scope") == scope]
        if period:
            records = [r for r in records if r.get("date", "").startswith(period)]
        return records

    def _carbon_summary(self) -> dict:
        """碳排放汇总"""
        by_scope: dict[str, float] = {"scope1": 0, "scope2": 0, "scope3": 0}
        total = 0.0
        for r in self._carbon_records:
            s = r.get("scope", "scope3")
            by_scope[s] = by_scope.get(s, 0) + r["amount"]
            total += r["amount"]
        return {
            "total": round(total, 2),
            "by_scope": {k: round(v, 2) for k, v in by_scope.items()},
            "records_count": len(self._carbon_records),
        }

    def get_social_activities(self, status: Optional[str] = None) -> list[dict]:
        """获取社会责任活动列表，可按状态过滤"""
        activities = list(self._social_activities)
        if status:
            activities = [a for a in activities if a.get("status") == status]
        return activities

    def create_social_activity(self, name: str, type: str,
                               description: str, date: str,
                               beneficiaries: str) -> dict:
        """创建社会责任活动"""
        activity = {
            "activity_id": str(uuid4()),
            "name": name,
            "type": type,             # 公益/教育/环保/扶贫
            "description": description,
            "date": date,
            "beneficiaries": beneficiaries,
            "status": "planned",      # planned/ongoing/completed
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._social_activities.append(activity)
        logger.info("esg_agent.social_activity_created",
                    name=name, type=type)
        return activity

    def get_esg_score(self) -> dict:
        """计算ESG综合评分"""
        return self._calc_esg_score()

    def _calc_esg_score(self) -> dict:
        """计算ESG综合评分（0-100）"""
        category_scores: dict[str, dict] = {}
        for cat in ("E", "S", "G"):
            indicators = [
                i for i in self._indicators.values()
                if i.get("category") == cat
            ]
            if not indicators:
                category_scores[cat] = {
                    "score": 0, "count": 0, "avg_achievement": 0
                }
                continue
            # 计算各指标达成率（归一化到0-1）
            achievements = []
            for ind in indicators:
                target = ind.get("target", 0)
                current = ind.get("current", 0)
                if target > 0:
                    # 对于"越低越好"的指标（如碳排放强度、安全事故率），
                    # 达成率 = target/current
                    if ind.get("direction", "higher_better") == "lower_better":
                        ratio = target / current if current > 0 else 1.0
                    else:
                        ratio = current / target
                    achievements.append(min(ratio, 1.0))
                else:
                    achievements.append(0.0)
            avg = sum(achievements) / len(achievements) if achievements else 0
            category_scores[cat] = {
                "score": round(avg * 100, 1),
                "count": len(indicators),
                "avg_achievement": round(avg, 4),
            }
        # 综合评分：E占40%，S占30%，G占30%
        weights = {"E": 0.4, "S": 0.3, "G": 0.3}
        total = sum(
            category_scores[c]["score"] * weights[c] for c in weights
        )
        # 评级
        if total >= 90:
            rating = "AAA"
        elif total >= 80:
            rating = "AA"
        elif total >= 70:
            rating = "A"
        elif total >= 60:
            rating = "BBB"
        elif total >= 50:
            rating = "BB"
        else:
            rating = "B"
        return {
            "total": round(total, 1),
            "rating": rating,
            "by_category": category_scores,
            "weights": weights,
        }

    def get_stats(self) -> dict:
        """获取ESG统计信息"""
        all_indicators = list(self._indicators.values())
        by_category: dict[str, int] = {}
        for i in all_indicators:
            cat = i.get("category", "unknown")
            by_category[cat] = by_category.get(cat, 0) + 1
        carbon = self._carbon_summary()
        activities = self._social_activities
        by_activity_status: dict[str, int] = {}
        for a in activities:
            s = a.get("status", "unknown")
            by_activity_status[s] = by_activity_status.get(s, 0) + 1
        return {
            "total_indicators": len(all_indicators),
            "by_category": by_category,
            "total_reports": len(self._reports),
            "total_carbon_records": len(self._carbon_records),
            "total_carbon_emission": carbon["total"],
            "total_social_activities": len(activities),
            "by_activity_status": by_activity_status,
        }

    # ========== 种子数据 ==========

    def _seed_data(self):
        """加载种子数据：6个ESG指标"""
        now = datetime.now(timezone.utc).isoformat()

        # 6个ESG指标
        seed_indicators = [
            {
                "indicator_id": "ESG0001",
                "name": "碳排放强度",
                "category": "E",
                "current": 0.45,
                "target": 0.40,
                "unit": "tCO2e/万元营收",
                "direction": "lower_better",
                "period": "2026",
                "achievement_rate": 0.40 / 0.45,
                "created_at": now,
                "updated_at": now,
            },
            {
                "indicator_id": "ESG0002",
                "name": "可再生能源占比",
                "category": "E",
                "current": 35.0,
                "target": 50.0,
                "unit": "%",
                "direction": "higher_better",
                "period": "2026",
                "achievement_rate": 35.0 / 50.0,
                "created_at": now,
                "updated_at": now,
            },
            {
                "indicator_id": "ESG0003",
                "name": "员工培训时长",
                "category": "S",
                "current": 32.0,
                "target": 40.0,
                "unit": "小时/人/年",
                "direction": "higher_better",
                "period": "2026",
                "achievement_rate": 32.0 / 40.0,
                "created_at": now,
                "updated_at": now,
            },
            {
                "indicator_id": "ESG0004",
                "name": "安全事故率",
                "category": "S",
                "current": 0.8,
                "target": 0.5,
                "unit": "起/千人工时",
                "direction": "lower_better",
                "period": "2026",
                "achievement_rate": 0.5 / 0.8,
                "created_at": now,
                "updated_at": now,
            },
            {
                "indicator_id": "ESG0005",
                "name": "董事会多样性",
                "category": "G",
                "current": 30.0,
                "target": 40.0,
                "unit": "%（女性董事占比）",
                "direction": "higher_better",
                "period": "2026",
                "achievement_rate": 30.0 / 40.0,
                "created_at": now,
                "updated_at": now,
            },
            {
                "indicator_id": "ESG0006",
                "name": "合规率",
                "category": "G",
                "current": 100.0,
                "target": 100.0,
                "unit": "%",
                "direction": "higher_better",
                "period": "2026",
                "achievement_rate": 1.0,
                "created_at": now,
                "updated_at": now,
            },
        ]
        for indicator in seed_indicators:
            self._indicators[indicator["indicator_id"]] = indicator
        self._indicator_seq = 6

        # 预置碳排放记录
        self._carbon_records = [
            {
                "record_id": str(uuid4()),
                "scope": "scope1",
                "amount": 1200.5,
                "unit": "tCO2e",
                "source": "自有车辆燃油排放",
                "date": "2026-06",
                "created_at": now,
            },
            {
                "record_id": str(uuid4()),
                "scope": "scope2",
                "amount": 2800.0,
                "unit": "tCO2e",
                "source": "外购电力",
                "date": "2026-06",
                "created_at": now,
            },
            {
                "record_id": str(uuid4()),
                "scope": "scope3",
                "amount": 5500.0,
                "unit": "tCO2e",
                "source": "供应链上游排放",
                "date": "2026-06",
                "created_at": now,
            },
        ]

        # 预置社会责任活动
        self._social_activities = [
            {
                "activity_id": str(uuid4()),
                "name": "乡村教育支援计划",
                "type": "教育",
                "description": "为偏远地区学校捐赠教学设备与图书",
                "date": "2026-03-15",
                "beneficiaries": "云南山区3所小学",
                "status": "completed",
                "created_at": now,
            },
            {
                "activity_id": str(uuid4()),
                "name": "社区环保志愿活动",
                "type": "环保",
                "description": "组织员工参与社区垃圾分类与环保宣传",
                "date": "2026-06-05",
                "beneficiaries": "本地社区居民500+人",
                "status": "completed",
                "created_at": now,
            },
        ]

        logger.info("esg_agent.seed_data_loaded",
                    indicators=len(self._indicators),
                    carbon_records=len(self._carbon_records),
                    social_activities=len(self._social_activities))
