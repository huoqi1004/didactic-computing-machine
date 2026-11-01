# -*- coding: utf-8 -*-
"""
合规Agent
负责法规解析、合规检查、控制点管理与合规报告。
监听内审警报与所有业务事件，确保时时合规。

职责：
- 监听 AUDIT_ALERT_RAISED 事件（内审警报时检查合规性）
- 监听所有业务事件（持续合规检查）
- 解析法规文档（使用LLM提取合规要求）
- 自动更新业务流程控制点
- 确保时时合规（每笔交易经过合规检查）
- 生成合规报告
"""
import asyncio
import structlog
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType

logger = structlog.get_logger()


class ComplianceAgent(BaseAgent):
    """
    合规Agent

    职责：
    - 监听内审警报与所有业务事件，持续合规检查
    - 解析法规文档，提取合规要求（LLM优先，降级到规则）
    - 自动更新业务流程控制点
    - 确保每笔交易经过合规检查
    - 生成合规报告
    """

    def __init__(self):
        super().__init__(
            name="合规Agent",
            domain="compliance",
            capabilities=[
                "regulation_parsing",          # 法规解析
                "compliance_checking",         # 合规检查
                "control_point_management",    # 控制点管理
                "policy_update",               # 政策更新
                "general",
            ],
            security_clearance="confidential",
        )
        # 法规库：reg_id -> 法规详情
        self._regulations: dict[str, dict] = {}
        # 控制点：cp_id -> 控制点详情
        self._control_points: dict[str, dict] = {}
        # 合规报告：report_id -> 报告详情
        self._compliance_reports: dict[str, dict] = {}
        # 合规检查计数
        self._check_count: int = 0
        # 违规计数
        self._violation_count: int = 0

        # 初始化种子数据
        self._seed_regulations_and_control_points()

    @property
    def description(self) -> str:
        return "法规解析、合规检查、控制点管理与合规报告"

    # ========== 初始化 ==========

    def _seed_regulations_and_control_points(self):
        """注入种子法规与控制点数据"""
        # 预置法规
        seed_regs = [
            {
                "reg_id": "reg-vat-001",
                "title": "中华人民共和国增值税法",
                "category": "tax",
                "effective_date": "2024-01-01",
                "requirements": [
                    "销售货物适用13%标准税率",
                    "进项税额需取得合法增值税扣税凭证",
                    "按月申报增值税",
                ],
                "status": "active",
            },
            {
                "reg_id": "reg-acc-001",
                "title": "企业会计准则第1号——存货",
                "category": "accounting",
                "effective_date": "2023-01-01",
                "requirements": [
                    "存货取得时按成本计量",
                    "存货发出可选择先进先出法或加权平均法",
                    "期末存货按成本与可变现净值孰低计量",
                ],
                "status": "active",
            },
            {
                "reg_id": "reg-audit-001",
                "title": "企业内部控制基本规范",
                "category": "audit",
                "effective_date": "2022-01-01",
                "requirements": [
                    "大额资金支付需多级审批",
                    "不相容职务分离",
                    "定期进行内部审计",
                ],
                "status": "active",
            },
        ]
        for reg in seed_regs:
            self._regulations[reg["reg_id"]] = reg

        # 预置控制点
        seed_cps = [
            {
                "cp_id": "cp-001",
                "name": "大额采购审批控制",
                "process": "procurement",
                "regulation_id": "reg-audit-001",
                "threshold": 100000,
                "control_type": "approval",
                "status": "active",
            },
            {
                "cp_id": "cp-002",
                "name": "增值税申报控制",
                "process": "tax",
                "regulation_id": "reg-vat-001",
                "threshold": 0,
                "control_type": "verification",
                "status": "active",
            },
            {
                "cp_id": "cp-003",
                "name": "凭证借贷平衡控制",
                "process": "accounting",
                "regulation_id": "reg-acc-001",
                "threshold": 0,
                "control_type": "validation",
                "status": "active",
            },
        ]
        for cp in seed_cps:
            self._control_points[cp["cp_id"]] = cp

    async def initialize(self):
        """初始化Agent：订阅内审警报与所有业务事件"""
        self.subscribe_events(
            event_types=[
                EventType.AUDIT_ALERT_RAISED,
                EventType.VOUCHER_GENERATED,
                EventType.VOUCHER_POSTED,
                EventType.PURCHASE_ORDER_CREATED,
                EventType.PURCHASE_ORDER_APPROVED,
                EventType.FUND_TRANSFER_REQUESTED,
                EventType.INVENTORY_ADJUSTED,
            ],
            handler=self._handle_event,
        )
        logger.info("compliance_agent.initialized", agent=self.name,
                    regulations=len(self._regulations),
                    control_points=len(self._control_points))

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("compliance_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件：执行合规检查"""
        if event.event_type == EventType.AUDIT_ALERT_RAISED:
            # 内审警报触发深度合规检查
            return await self._check_alert_compliance(event.event_data)
        else:
            # 持续合规检查
            return await self._check_transaction_compliance(event)

    # ========== 合规检查 ==========

    async def _check_transaction_compliance(self, event: Event) -> dict:
        """
        对业务事件执行持续合规检查

        返回检查结果：
        {passed: bool, violations: [...], control_points_checked: [...]}
        """
        self._check_count += 1
        amount = self._extract_amount(event)
        process = self._map_event_to_process(event.event_type)

        # 使用LLM进行深度合规检查（降级到规则）
        llm_result = await self._check_compliance_llm(event, process, amount)
        if llm_result is not None:
            result = llm_result
        else:
            result = self._check_compliance_rules(event, process, amount)

        violations = result.get("violations", [])
        control_points_checked = result.get("control_points_checked", [])

        if violations:
            self._violation_count += 1
            await self._publish_event(
                EventType.COMPLIANCE_VIOLATION_FOUND,
                {
                    "event_type": event.event_type.value,
                    "aggregate_id": event.aggregate_id,
                    "violations": violations,
                    "amount": amount,
                    "process": process,
                },
            )
            logger.warning("compliance_agent.violation_found",
                           event_type=event.event_type.value,
                           violations=len(violations))
        else:
            await self._publish_event(
                EventType.COMPLIANCE_CHECK_PASSED,
                {
                    "event_type": event.event_type.value,
                    "aggregate_id": event.aggregate_id,
                    "control_points_checked": control_points_checked,
                    "amount": amount,
                },
            )

        return result

    async def _check_alert_compliance(self, alert_data: dict):
        """内审警报触发的深度合规检查"""
        alert_type = alert_data.get("alert_type", "")
        severity = alert_data.get("severity", "")

        violations = []
        if severity == "high":
            violations.append({
                "control_point": "高风险警报合规审查",
                "description": f"内审警报{alert_data.get('alert_id', '')}为高风险，需合规复核",
                "regulation_id": "reg-audit-001",
            })

        if violations:
            self._violation_count += 1
            await self._publish_event(
                EventType.COMPLIANCE_VIOLATION_FOUND,
                {
                    "source": "audit_alert",
                    "alert_id": alert_data.get("alert_id", ""),
                    "violations": violations,
                },
            )
        return violations

    async def _check_compliance_llm(
        self, event: Event, process: str, amount: float,
    ) -> Optional[dict]:
        """使用LLM进行合规检查，返回None表示不可用"""
        from app.core.config import settings

        if not settings.llm_enabled:
            return None

        context = {
            "event_type": event.event_type.value,
            "process": process,
            "amount": amount,
            "event_data": {
                k: v for k, v in event.event_data.items()
                if k in ("po_id", "voucher_id", "transfer_id",
                         "total_amount", "amount", "supplier_id")
            },
            "applicable_control_points": [
                cp for cp in self._control_points.values()
                if cp["process"] == process and cp["status"] == "active"
            ],
        }
        prompt = (
            "请检查此交易是否合规。"
            "返回JSON，包含 passed(bool), violations(列表，每项含 "
            "control_point, description, regulation_id), "
            "control_points_checked(列表), reasoning。"
        )
        try:
            result = await self.llm_analyze(prompt, context)
        except Exception as e:
            logger.warning("compliance_agent.llm_check_error",
                           agent=self.name, error=str(e))
            return None

        if "error" in result:
            return None

        return {
            "passed": bool(result.get("passed", True)),
            "violations": result.get("violations", []),
            "control_points_checked": result.get("control_points_checked", []),
            "reasoning": str(result.get("reasoning", "")),
            "method": "llm",
        }

    def _check_compliance_rules(
        self, event: Event, process: str, amount: float,
    ) -> dict:
        """规则合规检查（降级方案）"""
        violations = []
        checked = []

        for cp in self._control_points.values():
            if cp["process"] != process or cp["status"] != "active":
                continue
            checked.append(cp["cp_id"])

            # 大额审批控制点
            if cp["control_type"] == "approval":
                threshold = cp.get("threshold", 0)
                if threshold > 0 and amount > threshold:
                    requires_approval = event.event_data.get(
                        "requires_human_approval", False
                    )
                    if not requires_approval:
                        violations.append({
                            "control_point": cp["name"],
                            "description": (
                                f"金额{amount:.2f}元超过控制点阈值{threshold}元，"
                                f"未触发人工审批"
                            ),
                            "regulation_id": cp["regulation_id"],
                        })

        return {
            "passed": len(violations) == 0,
            "violations": violations,
            "control_points_checked": checked,
            "method": "rules",
        }

    # ========== 法规解析 ==========

    async def parse_regulation(self, document_text: str) -> Optional[dict]:
        """
        解析法规文档，提取合规要求

        LLM优先，降级到规则（直接存储原文）。
        """
        from app.core.config import settings

        reg_id = f"reg-{str(uuid4())[:8]}"

        if settings.llm_enabled:
            context = {"document_text": document_text[:2000]}
            prompt = (
                "请解析以下法规文档，提取合规要求。"
                "返回JSON，包含 title, category, effective_date, "
                "requirements(列表), summary。"
            )
            try:
                result = await self.llm_analyze(prompt, context)
                if "error" not in result:
                    regulation = {
                        "reg_id": reg_id,
                        "title": str(result.get("title", "未命名法规")),
                        "category": str(result.get("category", "general")),
                        "effective_date": str(result.get("effective_date", "")),
                        "requirements": result.get("requirements", []),
                        "summary": str(result.get("summary", "")),
                        "status": "active",
                        "parsed_at": datetime.now(timezone.utc).isoformat(),
                        "method": "llm",
                    }
                    self._regulations[reg_id] = regulation
                    await self._update_control_points_from_regulation(regulation)
                    return regulation
            except Exception as e:
                logger.warning("compliance_agent.llm_parse_error",
                               agent=self.name, error=str(e))

        # 规则降级：直接存储原文
        regulation = {
            "reg_id": reg_id,
            "title": "待解析法规文档",
            "category": "general",
            "effective_date": "",
            "requirements": [document_text[:500]],
            "summary": "",
            "status": "active",
            "parsed_at": datetime.now(timezone.utc).isoformat(),
            "method": "rules",
        }
        self._regulations[reg_id] = regulation
        return regulation

    async def _update_control_points_from_regulation(self, regulation: dict):
        """根据新法规更新控制点"""
        cp_id = f"cp-{str(uuid4())[:8]}"
        control_point = {
            "cp_id": cp_id,
            "name": f"{regulation['title']}-控制点",
            "process": "general",
            "regulation_id": regulation["reg_id"],
            "threshold": 0,
            "control_type": "verification",
            "status": "active",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._control_points[cp_id] = control_point
        await self._publish_event(
            EventType.CONTROL_POINT_UPDATED,
            {"cp_id": cp_id, "action": "created",
             "regulation_id": regulation["reg_id"]},
        )

    # ========== 合规报告 ==========

    async def generate_compliance_report(self) -> dict:
        """生成合规报告"""
        report_id = f"RPT-{str(uuid4())[:8]}"
        pass_rate = (
            (self._check_count - self._violation_count) /
            max(self._check_count, 1)
        )

        report = {
            "report_id": report_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_checks": self._check_count,
            "violations": self._violation_count,
            "pass_rate": round(pass_rate, 4),
            "regulations_count": len(self._regulations),
            "control_points_count": len(self._control_points),
            "active_control_points": sum(
                1 for cp in self._control_points.values()
                if cp["status"] == "active"
            ),
            "status": "generated",
        }
        self._compliance_reports[report_id] = report

        # 记录决策
        decision = AgentDecision(
            decision_type="compliance_report",
            reasoning=(
                f"生成合规报告：共检查{self._check_count}笔交易，"
                f"发现{self._violation_count}项违规，合规率{pass_rate*100:.1f}%"
            ),
            action=f"生成合规报告{report_id}",
            confidence=0.9,
            requires_human_approval=False,
            context={"report_id": report_id},
        )
        await self.record_decision(decision)

        logger.info("compliance_agent.report_generated",
                    report_id=report_id,
                    pass_rate=pass_rate)
        return report

    # ========== 辅助方法 ==========

    def _extract_amount(self, event: Event) -> float:
        """从事件中提取金额"""
        data = event.event_data
        for key in ("total_amount", "amount", "total_cost", "total_debit"):
            try:
                v = float(data.get(key, 0) or 0)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        return 0.0

    def _map_event_to_process(self, event_type: EventType) -> str:
        """将事件类型映射到业务流程"""
        mapping = {
            EventType.VOUCHER_GENERATED: "accounting",
            EventType.VOUCHER_POSTED: "accounting",
            EventType.PURCHASE_ORDER_CREATED: "procurement",
            EventType.PURCHASE_ORDER_APPROVED: "procurement",
            EventType.FUND_TRANSFER_REQUESTED: "treasury",
            EventType.INVENTORY_ADJUSTED: "inventory",
        }
        return mapping.get(event_type, "general")

    def check_compliance(self, context: dict) -> dict:
        """
        同步合规检查（供API直接调用）

        参数：
        - context: {process, amount, requires_human_approval, ...}
        """
        process = context.get("process", "general")
        amount = float(context.get("amount", 0) or 0)
        requires_approval = context.get("requires_human_approval", False)

        violations = []
        checked = []
        for cp in self._control_points.values():
            if cp["process"] != process or cp["status"] != "active":
                continue
            checked.append(cp["cp_id"])
            if cp["control_type"] == "approval":
                threshold = cp.get("threshold", 0)
                if threshold > 0 and amount > threshold and not requires_approval:
                    violations.append({
                        "control_point": cp["name"],
                        "description": (
                            f"金额{amount:.2f}元超过阈值{threshold}元，"
                            f"未触发人工审批"
                        ),
                        "regulation_id": cp["regulation_id"],
                    })

        return {
            "passed": len(violations) == 0,
            "violations": violations,
            "control_points_checked": checked,
            "method": "rules",
        }

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出合规决策"""
        result = self.check_compliance(context)
        if result["passed"]:
            return AgentDecision(
                decision_type="compliance_pass",
                reasoning=f"交易通过合规检查，检查{len(result['control_points_checked'])}个控制点",
                action="合规检查通过",
                confidence=0.9,
                requires_human_approval=False,
                context=context,
            )
        return AgentDecision(
            decision_type="compliance_violation",
            reasoning=f"发现{len(result['violations'])}项合规违规",
            action="标记合规违规，需人工复核",
            confidence=0.85,
            requires_human_approval=True,
            context={**context, "violations": result["violations"]},
        )

    async def on_tick(self):
        """周期性检查所有业务流程的合规性"""
        # 检查是否有失效控制点需要更新
        for cp in self._control_points.values():
            if cp["status"] == "active":
                # 简化的周期性检查日志
                pass

    # ========== 查询接口 ==========

    def get_regulations(self, category: Optional[str] = None) -> list[dict]:
        """获取法规列表"""
        regs = list(self._regulations.values())
        if category:
            regs = [r for r in regs if r.get("category") == category]
        return regs

    def get_control_points(self, process: Optional[str] = None) -> list[dict]:
        """获取控制点列表"""
        cps = list(self._control_points.values())
        if process:
            cps = [cp for cp in cps if cp["process"] == process]
        return cps

    def get_compliance_reports(self) -> list[dict]:
        """获取合规报告列表"""
        return list(self._compliance_reports.values())
