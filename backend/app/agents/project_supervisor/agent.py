# -*- coding: utf-8 -*-
"""
项目监理Agent
=============
监理和审计公司项目从立项到上线的全流程合规。

监听项目创建、里程碑达成、项目完成与合规违规事件，维护监理记录、
按「立项→需求→开发→UAT→上线→结项」阶段核对合规清单、生成监理
报告并对风险分级，高风险问题提示人工介入。

职责：
- 监听 PROJECT_CREATED / PROJECT_MILESTONE_REACHED / PROJECT_COMPLETED
  事件（项目全生命周期监理留痕）
- 监听 COMPLIANCE_VIOLATION_FOUND 事件（合规违规监理登记）
- 按监理阶段清单核对项目合规状态
- 生成监理报告（优先从项目Agent同步实时进度）
- 风险分级与人工介入提示
"""
import structlog
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class ProjectSupervisorAgent(BaseAgent):
    """
    项目监理Agent

    职责：
    - 项目全生命周期监理留痕（立项→需求→开发→UAT→上线→结项）
    - 合规违规登记与风险分级
    - 监理报告生成（含实时进度同步）
    - 高风险提示人工介入
    """

    # 监理阶段清单（立项 → 上线）
    STAGES = ["立项审批", "需求评审", "开发测试", "UAT验收", "上线发布", "结项审计"]

    def __init__(self):
        super().__init__(
            name="项目监理agent",
            domain="project_supervisor",
            capabilities=[
                "立项审批监理",     # 立项审批合规监理
                "进度监理",         # 项目进度与里程碑监理
                "上线审计",         # 上线就绪度审计
                "合规检查",         # 流程合规核对
                "监理报告",         # 监理报告生成
                "general",
            ],
            security_clearance="confidential",
        )
        # 监理记录：project_id -> 监理快照
        self._supervision: dict[str, dict] = {}
        # 监理报告：report_id -> 报告
        self._reports: dict[str, dict] = {}
        # 违规登记：violation_id -> 违规
        self._violations: list[dict] = []
        self._seed_supervision()

    @property
    def description(self) -> str:
        return "监理和审计公司项目从立项到上线的全流程合规，生成监理报告与风险分级"

    # ========== 初始化 ==========

    def _seed_supervision(self):
        """注入种子监理记录（与项目Agent种子数据对应）"""
        now = datetime.now(timezone.utc).isoformat()
        seed = [
            {
                "project_id": "proj-001",
                "project_name": "ERP系统实施项目",
                "stage": "开发测试",
                "project_status": "in_progress",
                "milestones_total": 3,
                "milestones_completed": 2,
                "budget_usage": 0.40,
                "launch_ready": False,
                "risks": [
                    "进度轻微落后，需关注2026-09-30上线窗口",
                    "UAT未完成，暂不具备上线条件",
                ],
                "updated_at": now,
            },
            {
                "project_id": "proj-002",
                "project_name": "企业官网改版项目",
                "stage": "开发测试",
                "project_status": "in_progress",
                "milestones_total": 2,
                "milestones_completed": 1,
                "budget_usage": 0.30,
                "launch_ready": False,
                "risks": [],
                "updated_at": now,
            },
            {
                "project_id": "proj-003",
                "project_name": "新产品研发项目",
                "stage": "开发测试",
                "project_status": "in_progress",
                "milestones_total": 3,
                "milestones_completed": 1,
                "budget_usage": 0.25,
                "launch_ready": False,
                "risks": [],
                "updated_at": now,
            },
        ]
        for item in seed:
            self._supervision[item["project_id"]] = item

    async def initialize(self):
        """初始化Agent：订阅项目与合规事件"""
        self.subscribe_events(
            event_types=[
                EventType.PROJECT_CREATED,
                EventType.PROJECT_MILESTONE_REACHED,
                EventType.PROJECT_COMPLETED,
                EventType.COMPLIANCE_VIOLATION_FOUND,
            ],
            handler=self._handle_event,
        )
        logger.info("project_supervisor.initialized", agent=self.name,
                    projects=len(self._supervision))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("project_supervisor.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件：更新监理留痕"""
        if event.event_type == EventType.PROJECT_CREATED:
            await self._on_project_created(event.event_data)
        elif event.event_type == EventType.PROJECT_MILESTONE_REACHED:
            await self._on_milestone_reached(event.event_data)
        elif event.event_type == EventType.PROJECT_COMPLETED:
            await self._on_project_completed(event.event_data)
        elif event.event_type == EventType.COMPLIANCE_VIOLATION_FOUND:
            await self._on_compliance_violation(event.event_data)

    async def _on_project_created(self, data: dict):
        """项目创建：登记监理记录"""
        project_id = data.get("project_id")
        if not project_id:
            return
        if project_id not in self._supervision:
            self._supervision[project_id] = {
                "project_id": project_id,
                "project_name": data.get("name", project_id),
                "stage": "立项审批",
                "project_status": "planning",
                "milestones_total": 0,
                "milestones_completed": 0,
                "budget_usage": 0.0,
                "launch_ready": False,
                "risks": [],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        logger.info("project_supervisor.project_registered",
                    project_id=project_id)

    async def _on_milestone_reached(self, data: dict):
        """里程碑达成：更新监理进度"""
        project_id = data.get("project_id")
        rec = self._supervision.get(project_id)
        if not rec:
            return
        rec["milestones_completed"] = rec.get("milestones_completed", 0) + 1
        rec["updated_at"] = datetime.now(timezone.utc).isoformat()
        # 全部里程碑完成 → 阶段推进到 UAT/上线审计
        if rec.get("milestones_completed", 0) >= rec.get("milestones_total", 0):
            rec["stage"] = "UAT验收"
        logger.info("project_supervisor.milestone_synced", project_id=project_id)

    async def _on_project_completed(self, data: dict):
        """项目完成：更新监理状态并生成结项记录"""
        project_id = data.get("project_id")
        rec = self._supervision.get(project_id)
        if not rec:
            return
        rec["project_status"] = "completed"
        rec["stage"] = "结项审计"
        rec["launch_ready"] = True
        rec["updated_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("project_supervisor.project_completed_synced",
                    project_id=project_id)

    async def _on_compliance_violation(self, data: dict):
        """合规违规：监理登记并分级"""
        violation = {
            "violation_id": f"SV-{str(uuid4())[:8]}",
            "source": data.get("source", data.get("event_type", "unknown")),
            "process": data.get("process", ""),
            "aggregate_id": data.get("aggregate_id", ""),
            "violations": data.get("violations", []),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self._violations.append(violation)
        # 与项目相关的违规同步到项目监理记录
        agg = str(data.get("aggregate_id", ""))
        if agg.startswith("proj-"):
            rec = self._supervision.get(agg)
            if rec:
                rec["risks"] = list(rec.get("risks", [])) + [
                    f"合规违规：{len(data.get('violations', []))}项（{data.get('process', '')}）"
                ]
                rec["updated_at"] = violation["recorded_at"]
        logger.warning("project_supervisor.violation_recorded",
                       violation_id=violation["violation_id"],
                       violations=len(violation["violations"]))

    # ========== 监理查询 ==========

    def get_supervision(self, project_id: str | None = None) -> list[dict]:
        """获取监理记录，支持按项目过滤"""
        recs = list(self._supervision.values())
        if project_id:
            recs = [r for r in recs if r.get("project_id") == project_id]
        return recs

    def get_violations(self) -> list[dict]:
        """获取合规违规登记列表"""
        return list(self._violations)

    def get_reports(self) -> list[dict]:
        """获取监理报告列表"""
        return list(self._reports)

    # ========== 监理报告 ==========

    async def generate_report(self) -> dict:
        """
        生成监理报告（优先从项目Agent同步实时进度）

        返回：报告详情（含各项目合规状态、风险分级、结论）
        """
        # 尝试从项目Agent同步实时进度
        try:
            from app.core.agent.orchestrator import orchestrator

            project_agent = orchestrator.get_agent_by_name("项目Agent")
            if project_agent:
                for project in project_agent.get_projects():
                    pid = project.get("project_id")
                    rec = self._supervision.get(pid)
                    if rec is None:
                        continue
                    progress = project_agent.get_progress(pid)
                    rec["project_status"] = project.get("status", rec.get("project_status"))
                    rec["milestones_completed"] = progress.get(
                        "completed_milestones", rec.get("milestones_completed", 0))
                    rec["milestones_total"] = progress.get(
                        "total_milestones", rec.get("milestones_total", 0))
                    rec["budget_usage"] = progress.get(
                        "budget_usage_rate", rec.get("budget_usage", 0.0))
                    rec["updated_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:  # noqa: BLE001
            logger.warning("project_supervisor.report_sync_failed",
                           agent=self.name, error=str(e))

        # 风险分级
        high_risks, medium_risks, low_risks = [], [], []
        for rec in self._supervision.values():
            risks = rec.get("risks", [])
            if not risks:
                continue
            if rec.get("project_status") == "completed":
                low_risks.extend(risks)
            elif rec.get("budget_usage", 0) >= 0.9 or not rec.get("launch_ready", True):
                high_risks.extend(risks)
            else:
                medium_risks.extend(risks)

        overall = (
            "存在高风险" if high_risks
            else "存在中风险" if medium_risks
            else "整体合规"
        )
        report_id = f"SRPT-{str(uuid4())[:8]}"
        report = {
            "report_id": report_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "supervised_projects": len(self._supervision),
            "violations_recorded": len(self._violations),
            "overall": overall,
            "high_risks": high_risks,
            "medium_risks": medium_risks,
            "low_risks": low_risks,
            "projects": [
                {
                    "project_id": r["project_id"],
                    "project_name": r["project_name"],
                    "stage": r["stage"],
                    "project_status": r["project_status"],
                    "milestones": f"{r['milestones_completed']}/{r['milestones_total']}",
                    "budget_usage": round(r["budget_usage"], 4),
                    "launch_ready": r["launch_ready"],
                    "risks": r["risks"],
                }
                for r in self._supervision.values()
            ],
            "status": "generated",
        }
        self._reports[report_id] = report

        # 记录决策
        decision = AgentDecision(
            decision_type="supervision_report",
            reasoning=(
                f"生成项目监理报告：在监{len(self._supervision)}个项目，"
                f"登记{len(self._violations)}项违规，总体判定：{overall}"
            ),
            action=f"生成监理报告{report_id}",
            confidence=0.9,
            requires_human_approval=bool(high_risks),
            context={"report_id": report_id, "overall": overall},
        )
        await self.record_decision(decision)

        logger.info("project_supervisor.report_generated",
                    report_id=report_id, overall=overall)
        return report

    # ========== 合规检查 ==========

    def check_compliance(self, project_id: str | None = None) -> dict:
        """
        按监理阶段清单核对合规状态

        返回：{stage: 状态} 对照表与风险分级
        """
        stages = {s: "通过" for s in self.STAGES}
        risks = []
        for rec in self._supervision.values():
            if project_id and rec.get("project_id") != project_id:
                continue
            stage = rec.get("stage", "立项审批")
            # 当前阶段及之前默认已过，当前阶段标记进行中，之后标记未到
            idx = self.STAGES.index(stage) if stage in self.STAGES else 0
            for i, s in enumerate(self.STAGES):
                if i < idx:
                    stages[s] = "通过"
                elif i == idx:
                    stages[s] = "进行中"
                else:
                    stages[s] = "未到"
            risks.extend(rec.get("risks", []))

        return {
            "stages": stages,
            "high_risk": any("上线" in r or "预算" in r for r in risks),
            "risks": risks,
        }

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出监理决策"""
        action = context.get("action", "")

        if action == "generate_report":
            report = await self.generate_report()
            return AgentDecision(
                decision_type="supervision_report",
                reasoning=report.get("overall", "整体合规"),
                action=f"生成监理报告：{report['report_id']}",
                confidence=0.9,
                requires_human_approval=bool(report.get("high_risks")),
                context={"report_id": report["report_id"]},
            )

        if action == "compliance_check":
            result = self.check_compliance(context.get("project_id"))
            return AgentDecision(
                decision_type="supervision_compliance",
                reasoning=f"核对{len(self.STAGES)}个监理阶段，高风险：{result['high_risk']}",
                action="项目合规监理检查",
                confidence=0.85,
                requires_human_approval=result["high_risk"],
                context=result,
            )

        # 默认：监理查询
        return AgentDecision(
            decision_type="supervision_query",
            reasoning=f"在监{len(self._supervision)}个项目，登记{len(self._violations)}项违规",
            action="查询项目监理状态",
            confidence=0.8,
            requires_human_approval=False,
            context=context,
        )

    async def on_tick(self):
        """周期性任务：检查高风险项目（简化日志）"""
        for rec in self._supervision.values():
            if rec.get("budget_usage", 0) >= 0.9:
                logger.warning("project_supervisor.high_risk",
                               project_id=rec["project_id"],
                               budget_usage=rec.get("budget_usage"))

    # ========== 统计 ==========

    def get_stats(self) -> dict:
        """获取监理统计"""
        by_status = {}
        for r in self._supervision.values():
            s = r.get("project_status", "unknown")
            by_status[s] = by_status.get(s, 0) + 1
        return {
            "supervised_projects": len(self._supervision),
            "reports_generated": len(self._reports),
            "violations_recorded": len(self._violations),
            "by_status": by_status,
            "high_risk_projects": sum(
                1 for r in self._supervision.values() if r.get("risks")
            ),
        }
