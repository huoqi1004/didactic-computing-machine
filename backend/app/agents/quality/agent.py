# -*- coding: utf-8 -*-
"""
质量Agent
负责质检记录管理、质量控制、改进措施管理与质量标准维护。
监听质检结果与生产完成事件，自动触发质检与改进建议。

职责：
- 监听 QUALITY_INSPECTION_PASSED / QUALITY_INSPECTION_FAILED 事件（质检结果归档）
- 监听 PRODUCTION_COMPLETED 事件（生产完成触发成品质检）
- 监听 QUALITY_IMPROVEMENT_PROPOSED 事件（接收改进措施建议）
- 管理质检记录、质量标准与改进措施全生命周期
- 不合格率超阈值时自动发起改进措施并发布事件
- 不合格批次需人在回路确认处置
"""
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class QualityAgent(BaseAgent):
    """
    质量Agent

    职责：
    - 管理质检记录（来料、过程、成品检验）
    - 维护质量标准库（含检验项目、判定准则与公差）
    - 跟踪改进措施从提出到验证闭环
    - 监听生产完成事件自动触发成品质检
    - 不合格率超阈值自动发起改进建议
    - 不合格批次处置需人在回路确认
    """

    # 不合格率阈值（超过15%自动发起改进建议）
    DEFECT_RATE_THRESHOLD = 0.15
    # 不合格批次需人工审批
    HUMAN_APPROVAL_ON_FAILED = True

    def __init__(self):
        super().__init__(
            name="质量Agent",
            domain="quality",
            capabilities=[
                "quality_inspection",        # 质量检验
                "quality_control",           # 质量控制
                "improvement_management",    # 改进管理
                "standard_management",       # 标准管理
                "general",
            ],
            security_clearance="internal",
        )
        # 质检记录：inspection_id -> 质检详情
        self._inspections: dict[str, dict] = {}
        # 质量标准：standard_id -> 标准详情
        self._standards: dict[str, dict] = {}
        # 改进措施列表
        self._improvements: list[dict] = []
        # 初始化种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "管理质检记录、质量标准与改进措施，监听生产完成事件触发质检，控制产品质量"

    # ========== 初始化 ==========

    def _seed_data(self):
        """注入种子质量标准与质检记录"""
        # 质量标准种子数据（4个）
        self._standards = {
            "std-001": {
                "standard_id": "std-001",
                "name": "来料检验标准",
                "category": "incoming",
                "criteria": "外观无破损、尺寸符合图纸、材质报告齐全",
                "tolerance": "尺寸公差 ±0.05mm",
                "version": "1.0",
                "status": "active",
                "created_at": "2026-01-10T08:00:00+00:00",
            },
            "std-002": {
                "standard_id": "std-002",
                "name": "过程检验标准",
                "category": "process",
                "criteria": "首件检验合格、过程抽检合格率≥98%、关键工序全检",
                "tolerance": "抽检不合格率 ≤2%",
                "version": "2.1",
                "status": "active",
                "created_at": "2026-01-10T08:00:00+00:00",
            },
            "std-003": {
                "standard_id": "std-003",
                "name": "成品检验标准",
                "category": "finished",
                "criteria": "功能测试通过、外观无瑕疵、包装完整、性能指标达标",
                "tolerance": "性能偏差 ≤±3%",
                "version": "1.5",
                "status": "active",
                "created_at": "2026-01-10T08:00:00+00:00",
            },
            "std-004": {
                "standard_id": "std-004",
                "name": "ISO9001质量管理体系标准",
                "category": "system",
                "criteria": "文件控制、记录控制、内部审核、管理评审、纠正预防措施",
                "tolerance": "不符合项 0 项（关键）、≤3项（一般）",
                "version": "2026",
                "status": "active",
                "created_at": "2026-01-01T08:00:00+00:00",
            },
        }

        # 质检记录种子数据（3条）
        self._inspections = {
            "insp-001": {
                "inspection_id": "insp-001",
                "product": "智能终端A",
                "batch": "BAT-20260601-01",
                "inspection_type": "incoming",
                "inspector": "张质检",
                "items": [
                    {"name": "外观", "result": "pass"},
                    {"name": "尺寸", "result": "pass"},
                    {"name": "材质报告", "result": "pass"},
                ],
                "result": "passed",
                "defects": [],
                "notes": "来料检验合格",
                "standard_id": "std-001",
                "status": "completed",
                "created_at": "2026-06-01T09:00:00+00:00",
                "completed_at": "2026-06-01T10:30:00+00:00",
            },
            "insp-002": {
                "inspection_id": "insp-002",
                "product": "智能终端A",
                "batch": "BAT-20260615-02",
                "inspection_type": "process",
                "inspector": "李检验",
                "items": [
                    {"name": "首件检验", "result": "pass"},
                    {"name": "过程抽检", "result": "fail"},
                    {"name": "关键工序", "result": "pass"},
                ],
                "result": "failed",
                "defects": [
                    {"name": "过程抽检不合格", "severity": "major", "count": 3},
                ],
                "notes": "过程抽检发现3件尺寸超差，已隔离",
                "standard_id": "std-002",
                "status": "completed",
                "created_at": "2026-06-15T14:00:00+00:00",
                "completed_at": "2026-06-15T16:00:00+00:00",
            },
            "insp-003": {
                "inspection_id": "insp-003",
                "product": "智能终端B",
                "batch": "BAT-20260620-03",
                "inspection_type": "finished",
                "inspector": "王质检",
                "items": [
                    {"name": "功能测试", "result": "pass"},
                    {"name": "外观", "result": "pass"},
                    {"name": "包装", "result": "pass"},
                    {"name": "性能", "result": "pass"},
                ],
                "result": "passed",
                "defects": [],
                "notes": "成品检验全部合格",
                "standard_id": "std-003",
                "status": "completed",
                "created_at": "2026-06-20T10:00:00+00:00",
                "completed_at": "2026-06-20T12:00:00+00:00",
            },
        }

    async def initialize(self):
        """初始化Agent：订阅质检结果与生产完成事件"""
        self.subscribe_events(
            event_types=[
                EventType.QUALITY_INSPECTION_PASSED,
                EventType.QUALITY_INSPECTION_FAILED,
                EventType.PRODUCTION_COMPLETED,
                EventType.QUALITY_IMPROVEMENT_PROPOSED,
            ],
            handler=self._handle_event,
        )
        logger.info("quality_agent.initialized", agent=self.name,
                    standards=len(self._standards),
                    inspections=len(self._inspections))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("quality_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        if event.event_type == EventType.QUALITY_INSPECTION_PASSED:
            await self._handle_inspection_passed(event.event_data)

        elif event.event_type == EventType.QUALITY_INSPECTION_FAILED:
            await self._handle_inspection_failed(event.event_data)

        elif event.event_type == EventType.PRODUCTION_COMPLETED:
            await self._handle_production_completed(event.event_data)

        elif event.event_type == EventType.QUALITY_IMPROVEMENT_PROPOSED:
            await self._handle_improvement_proposed(event.event_data)

    async def _handle_inspection_passed(self, data: dict):
        """处理质检通过事件：同步质检记录状态"""
        inspection_id = data.get("inspection_id")
        if inspection_id and inspection_id in self._inspections:
            self._inspections[inspection_id]["result"] = "passed"
            self._inspections[inspection_id]["status"] = "completed"
            self._inspections[inspection_id]["completed_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
            logger.info("quality_agent.inspection_passed_synced",
                        inspection_id=inspection_id)

    async def _handle_inspection_failed(self, data: dict):
        """处理质检不合格事件：记录缺陷并评估是否发起改进"""
        inspection_id = data.get("inspection_id")
        if inspection_id and inspection_id in self._inspections:
            inspection = self._inspections[inspection_id]
            inspection["result"] = "failed"
            inspection["status"] = "completed"
            inspection["completed_at"] = datetime.now(timezone.utc).isoformat()
            if data.get("defects"):
                inspection["defects"] = data["defects"]

        # 记录决策：不合格批次处置
        decision = AgentDecision(
            decision_type="quality_failed_handling",
            reasoning=(
                f"质检记录{inspection_id}不合格，需人工确认处置方式"
                f"（返工/让步接收/报废）"
            ),
            action=f"请求人工确认不合格批次处置：质检记录{inspection_id}",
            confidence=0.9,
            requires_human_approval=self.HUMAN_APPROVAL_ON_FAILED,
            context={"inspection_id": inspection_id, "defects": data.get("defects", [])},
        )
        await self.record_decision(decision)

        # 评估不合格率，超阈值自动发起改进建议
        await self._evaluate_defect_rate()

    async def _handle_production_completed(self, data: dict):
        """处理生产完成事件：自动触发成品质检"""
        product = data.get("product_name", data.get("product_id", ""))
        batch = data.get("batch", data.get("order_id", ""))
        if not product:
            return

        # 自动创建成品质检记录
        inspection = self.create_inspection(
            product=product,
            batch=batch,
            inspection_type="finished",
            inspector=self.name,
            items=[
                {"name": "功能测试", "result": "pending"},
                {"name": "外观", "result": "pending"},
                {"name": "包装", "result": "pending"},
                {"name": "性能", "result": "pending"},
            ],
        )

        logger.info("quality_agent.auto_inspection_created",
                    inspection_id=inspection["inspection_id"],
                    product=product, batch=batch)

    async def _handle_improvement_proposed(self, data: dict):
        """处理改进措施建议事件：纳入改进措施列表"""
        improvement = self.create_improvement(
            title=data.get("title", "外部改进建议"),
            description=data.get("description", ""),
            root_cause=data.get("root_cause", ""),
            action_plan=data.get("action_plan", ""),
            owner=data.get("owner", ""),
            due_date=data.get("due_date", ""),
        )
        logger.info("quality_agent.improvement_imported",
                    improvement_id=improvement["improvement_id"])

    async def _evaluate_defect_rate(self):
        """评估不合格率，超阈值自动发起改进建议"""
        stats = self.get_stats()
        defect_rate = stats.get("defect_rate", 0.0)
        if defect_rate >= self.DEFECT_RATE_THRESHOLD:
            improvement = self.create_improvement(
                title=f"不合格率预警改进（当前{defect_rate:.1%}）",
                description=(
                    f"近期不合格率达到{defect_rate:.1%}，超过阈值"
                    f"{self.DEFECT_RATE_THRESHOLD:.1%}，需系统性改进"
                ),
                root_cause="待分析（建议使用鱼骨图/5Why分析）",
                action_plan="1.成立改进小组 2.根因分析 3.制定纠正措施 4.验证有效性",
                owner="质量部",
                due_date="",
            )

            # 发布改进建议事件
            await self._publish_event(
                EventType.QUALITY_IMPROVEMENT_PROPOSED,
                {
                    "improvement_id": improvement["improvement_id"],
                    "title": improvement["title"],
                    "defect_rate": defect_rate,
                    "threshold": self.DEFECT_RATE_THRESHOLD,
                    "status": "proposed",
                },
            )
            logger.warning("quality_agent.defect_rate_exceeded",
                           defect_rate=defect_rate,
                           threshold=self.DEFECT_RATE_THRESHOLD)

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出质量决策"""
        inspection_id = context.get("inspection_id")
        inspection = self._inspections.get(inspection_id)

        if not inspection:
            return AgentDecision(
                decision_type="error",
                reasoning=f"质检记录{inspection_id}不存在",
                action="无法做出质量决策",
                confidence=0.0,
            )

        result = inspection.get("result", "pending")

        if result == "failed":
            defect_count = len(inspection.get("defects", []))
            return AgentDecision(
                decision_type="quality_disposition",
                reasoning=(
                    f"质检记录{inspection_id}（{inspection.get('product', '')}"
                    f"批次{inspection.get('batch', '')}）不合格，"
                    f"发现{defect_count}类缺陷，需人工确认处置"
                ),
                action=f"请求人工确认不合格批次处置：{inspection_id}",
                confidence=0.9,
                requires_human_approval=True,
                context={
                    "inspection_id": inspection_id,
                    "product": inspection.get("product"),
                    "defects": inspection.get("defects", []),
                },
            )

        if result == "passed":
            return AgentDecision(
                decision_type="quality_release",
                reasoning=(
                    f"质检记录{inspection_id}合格，准予放行"
                ),
                action=f"自动放行合格批次：{inspection_id}",
                confidence=0.95,
                requires_human_approval=False,
                context={"inspection_id": inspection_id},
            )

        return AgentDecision(
            decision_type="quality_pending",
            reasoning=f"质检记录{inspection_id}尚未完成，等待结果",
            action=f"等待质检结果：{inspection_id}",
            confidence=0.5,
            requires_human_approval=False,
            context={"inspection_id": inspection_id},
        )

    async def on_tick(self):
        """周期性任务：检查不合格率趋势"""
        stats = self.get_stats()
        if stats.get("total", 0) >= 5:
            defect_rate = stats.get("defect_rate", 0.0)
            if defect_rate >= self.DEFECT_RATE_THRESHOLD:
                logger.warning("quality_agent.defect_rate_monitoring",
                               defect_rate=defect_rate,
                               threshold=self.DEFECT_RATE_THRESHOLD)

    # ========== 质检记录管理 ==========

    def get_inspections(self, status: Optional[str] = None,
                        product: Optional[str] = None) -> list[dict]:
        """获取质检记录列表，支持按状态与产品过滤"""
        inspections = list(self._inspections.values())
        if status:
            inspections = [i for i in inspections if i.get("status") == status]
        if product:
            inspections = [i for i in inspections if i.get("product") == product]
        return inspections

    def get_inspection(self, inspection_id: str) -> Optional[dict]:
        """获取单条质检记录"""
        return self._inspections.get(inspection_id)

    def create_inspection(self, product: str, batch: str,
                          inspection_type: str, inspector: str,
                          items: list[dict]) -> dict:
        """
        创建质检记录

        参数：
        - product: 产品名称
        - batch: 批次号
        - inspection_type: 检验类型（incoming/process/finished）
        - inspector: 检验员
        - items: 检验项目列表 [{"name":..., "result":...}, ...]
        """
        inspection_id = f"INSP-{str(uuid4())[:8]}"
        # 根据检验类型匹配质量标准
        category_map = {
            "incoming": "incoming",
            "process": "process",
            "finished": "finished",
        }
        category = category_map.get(inspection_type, "")
        standard_id = ""
        for std in self._standards.values():
            if std.get("category") == category:
                standard_id = std["standard_id"]
                break

        inspection = {
            "inspection_id": inspection_id,
            "product": product,
            "batch": batch,
            "inspection_type": inspection_type,
            "inspector": inspector,
            "items": items,
            "result": "pending",
            "defects": [],
            "notes": "",
            "standard_id": standard_id,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        }
        self._inspections[inspection_id] = inspection
        logger.info("quality_agent.inspection_created",
                    inspection_id=inspection_id, product=product,
                    inspection_type=inspection_type)
        return inspection

    async def record_result(self, inspection_id: str, result: str,
                            defects: Optional[list[dict]] = None,
                            notes: str = "") -> dict:
        """
        记录质检结果

        参数：
        - inspection_id: 质检记录ID
        - result: 质检结果（passed/failed）
        - defects: 缺陷列表 [{"name":..., "severity":..., "count":...}, ...]
        - notes: 备注

        流程：
        1. 更新质检记录状态与结果
        2. 发布质检通过/不合格事件
        3. 不合格时评估不合格率并可能发起改进建议
        """
        inspection = self._inspections.get(inspection_id)
        if not inspection:
            return {"error": f"质检记录{inspection_id}不存在"}

        inspection["result"] = result
        inspection["status"] = "completed"
        inspection["defects"] = defects or []
        inspection["notes"] = notes
        inspection["completed_at"] = datetime.now(timezone.utc).isoformat()

        # 记录决策
        decision = AgentDecision(
            decision_type="quality_inspection_result",
            reasoning=(
                f"质检记录{inspection_id}（{inspection.get('product', '')}）"
                f"结果为{result}"
            ),
            action=(
                f"记录质检结果{result}：{inspection_id}"
                + (f"，发现{len(inspection['defects'])}类缺陷" if result == "failed" else "")
            ),
            confidence=0.95 if result == "passed" else 0.85,
            requires_human_approval=(result == "failed" and self.HUMAN_APPROVAL_ON_FAILED),
            context={
                "inspection_id": inspection_id,
                "result": result,
                "defects": inspection["defects"],
            },
        )
        await self.record_decision(decision)

        # 发布质检结果事件
        event_type = (
            EventType.QUALITY_INSPECTION_PASSED if result == "passed"
            else EventType.QUALITY_INSPECTION_FAILED
        )
        await self._publish_event(
            event_type,
            {
                "inspection_id": inspection_id,
                "product": inspection.get("product"),
                "batch": inspection.get("batch"),
                "inspection_type": inspection.get("inspection_type"),
                "result": result,
                "defects": inspection["defects"],
                "notes": notes,
                "decision_id": decision.decision_id,
            },
        )

        # 不合格时评估不合格率
        if result == "failed":
            await self._evaluate_defect_rate()

        logger.info("quality_agent.result_recorded",
                    inspection_id=inspection_id, result=result)
        return inspection

    # ========== 质量标准管理 ==========

    def get_standards(self, category: Optional[str] = None) -> list[dict]:
        """获取质量标准列表，支持按类别过滤"""
        standards = list(self._standards.values())
        if category:
            standards = [s for s in standards if s.get("category") == category]
        return standards

    def create_standard(self, name: str, category: str,
                        criteria: str, tolerance: str) -> dict:
        """
        创建质量标准

        参数：
        - name: 标准名称
        - category: 类别（incoming/process/finished/system）
        - criteria: 判定准则
        - tolerance: 公差范围
        """
        standard_id = f"STD-{str(uuid4())[:8]}"
        standard = {
            "standard_id": standard_id,
            "name": name,
            "category": category,
            "criteria": criteria,
            "tolerance": tolerance,
            "version": "1.0",
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._standards[standard_id] = standard
        logger.info("quality_agent.standard_created",
                    standard_id=standard_id, name=name, category=category)
        return standard

    # ========== 改进措施管理 ==========

    def get_improvements(self, status: Optional[str] = None) -> list[dict]:
        """获取改进措施列表，支持按状态过滤"""
        improvements = self._improvements
        if status:
            improvements = [i for i in improvements if i.get("status") == status]
        return improvements

    def create_improvement(self, title: str, description: str,
                           root_cause: str, action_plan: str,
                           owner: str, due_date: str) -> dict:
        """
        创建改进措施

        参数：
        - title: 标题
        - description: 问题描述
        - root_cause: 根本原因
        - action_plan: 行动计划
        - owner: 负责人
        - due_date: 截止日期
        """
        improvement_id = f"IMP-{str(uuid4())[:8]}"
        improvement = {
            "improvement_id": improvement_id,
            "title": title,
            "description": description,
            "root_cause": root_cause,
            "action_plan": action_plan,
            "owner": owner,
            "due_date": due_date,
            "status": "proposed",
            "result": None,
            "effectiveness": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        }
        self._improvements.append(improvement)
        logger.info("quality_agent.improvement_created",
                    improvement_id=improvement_id, title=title)
        return improvement

    async def complete_improvement(self, improvement_id: str, result: str,
                                   effectiveness: str) -> dict:
        """
        完成改进措施

        参数：
        - improvement_id: 改进措施ID
        - result: 改进结果
        - effectiveness: 有效性评价（effective/partially_effective/ineffective）
        """
        improvement = None
        for imp in self._improvements:
            if imp["improvement_id"] == improvement_id:
                improvement = imp
                break

        if not improvement:
            return {"error": f"改进措施{improvement_id}不存在"}

        improvement["status"] = "completed"
        improvement["result"] = result
        improvement["effectiveness"] = effectiveness
        improvement["completed_at"] = datetime.now(timezone.utc).isoformat()

        # 记录决策
        decision = AgentDecision(
            decision_type="quality_improvement_completion",
            reasoning=(
                f"改进措施{improvement_id}（{improvement['title']}）已完成，"
                f"有效性：{effectiveness}"
            ),
            action=f"关闭改进措施{improvement_id}，有效性评价：{effectiveness}",
            confidence=0.9,
            requires_human_approval=False,
            context={
                "improvement_id": improvement_id,
                "effectiveness": effectiveness,
            },
        )
        await self.record_decision(decision)

        logger.info("quality_agent.improvement_completed",
                    improvement_id=improvement_id,
                    effectiveness=effectiveness)
        return improvement

    # ========== 统计 ==========

    def get_stats(self) -> dict:
        """
        获取质量统计

        返回：含合格率、不合格趋势、改进措施统计等
        """
        inspections = list(self._inspections.values())
        completed = [i for i in inspections if i.get("status") == "completed"]
        passed = [i for i in completed if i.get("result") == "passed"]
        failed = [i for i in completed if i.get("result") == "failed"]
        pending = [i for i in inspections if i.get("status") == "pending"]

        total_completed = len(completed)
        pass_count = len(passed)
        fail_count = len(failed)
        pass_rate = (pass_count / total_completed) if total_completed > 0 else 0.0
        defect_rate = (fail_count / total_completed) if total_completed > 0 else 0.0

        # 按检验类型统计
        by_type = {}
        for insp in completed:
            itype = insp.get("inspection_type", "unknown")
            by_type.setdefault(itype, {"total": 0, "passed": 0, "failed": 0})
            by_type[itype]["total"] += 1
            if insp.get("result") == "passed":
                by_type[itype]["passed"] += 1
            elif insp.get("result") == "failed":
                by_type[itype]["failed"] += 1

        # 改进措施统计
        improvements = self._improvements
        imp_by_status = {}
        for imp in improvements:
            s = imp.get("status", "unknown")
            imp_by_status[s] = imp_by_status.get(s, 0) + 1

        # 缺陷汇总
        all_defects = []
        for insp in failed:
            all_defects.extend(insp.get("defects", []))

        return {
            "total_inspections": len(inspections),
            "completed": total_completed,
            "pending": len(pending),
            "passed": pass_count,
            "failed": fail_count,
            "pass_rate": round(pass_rate, 4),
            "defect_rate": round(defect_rate, 4),
            "by_type": by_type,
            "total_standards": len(self._standards),
            "active_standards": len([s for s in self._standards.values()
                                     if s.get("status") == "active"]),
            "total_improvements": len(improvements),
            "improvements_by_status": imp_by_status,
            "total_defects": len(all_defects),
            "defect_rate_threshold": self.DEFECT_RATE_THRESHOLD,
        }
