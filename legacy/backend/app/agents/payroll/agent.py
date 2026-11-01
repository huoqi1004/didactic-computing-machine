"""
薪酬 Agent
负责薪资计算、社保公积金核算、个税计算与薪酬报表生成。
监听薪酬计算、薪资调整与考勤记录事件，自动更新薪酬数据。

个税采用2024年中国个人所得税累进税率表（综合所得），
起征点5000元/月，社保公积金按法定比例计算。
"""
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType
from app.db.repositories.base import has_db, schedule, warn_throttled
from app.db.repositories.payroll_repository import (
    payroll_record_repository,
    salary_structure_repository,
)

logger = structlog.get_logger()


class PayrollAgent(BaseAgent):
    """薪酬 Agent - 管理薪资计算、社保与个税"""

    # 个税起征点（元/月）
    TAX_THRESHOLD = 5000

    def __init__(self):
        super().__init__(
            name="薪酬Agent",
            domain="payroll",
            capabilities=[
                "salary_calculation",
                "social_insurance",
                "tax_calculation",
                "payroll_report",
                "general",
            ],
            security_clearance="restricted",
        )
        # 薪资结构: employee_id -> dict
        self._salary_structures: dict[str, dict] = {}
        # 薪酬记录列表
        self._payroll_records: list[dict] = []
        # 社保配置
        self._social_insurance_config: dict[str, float] = {}
        # 个税税率表
        self._tax_brackets: list[dict] = []
        # 加载种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "薪资计算、社保公积金核算、个税计算与薪酬报表"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅薪酬相关事件"""
        self.subscribe_events(
            event_types=[
                EventType.PAYROLL_CALCULATED,
                EventType.SALARY_ADJUSTED,
                EventType.ATTENDANCE_RECORDED,
            ],
            handler=self._handle_event,
        )
        logger.info("payroll_agent.initialized", agent=self.name,
                    salary_structures=len(self._salary_structures))

    # ========== 持久化（路线图 2.2 批次一） ==========

    async def hydrate_from_db(self) -> bool:
        """
        启动水合：DB 优先、内存种子兜底、空库回播种（重启一致性）。

        - DB 有数据 → 覆盖内存状态（以库为准）；
        - DB 空且内存有种子 → 将种子幂等播种入库；
        - DB 不可用 → 保持内存种子（降级告警由 repository 层输出）。

        Returns:
            True 表示状态已与 DB 对齐（水合或播种）。
        """
        if not has_db():
            return False

        hydrated = False
        structures = await salary_structure_repository.list_all()
        if structures:
            self._salary_structures = {
                s["employee_id"]: s for s in structures
            }
            hydrated = True
        elif self._salary_structures:
            for s in list(self._salary_structures.values()):
                await salary_structure_repository.upsert(s, "employee_id")
            logger.info("payroll_agent.structures_seeded_to_db",
                        count=len(self._salary_structures))
            hydrated = True

        records = await payroll_record_repository.list_all()
        if records:
            self._payroll_records = records
            hydrated = True

        logger.info(
            "payroll_agent.hydrated",
            from_db=hydrated,
            structures=len(self._salary_structures),
            records=len(self._payroll_records),
        )
        return hydrated

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("payroll_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        data = event.event_data

        if event.event_type == EventType.PAYROLL_CALCULATED:
            await self._handle_payroll_calculated(data)
        elif event.event_type == EventType.SALARY_ADJUSTED:
            await self._handle_salary_adjusted(data)
        elif event.event_type == EventType.ATTENDANCE_RECORDED:
            await self._handle_attendance_recorded(data)

    async def _handle_payroll_calculated(self, data: dict):
        """处理薪酬计算事件（同步外部薪酬数据）"""
        employee_id = data.get("employee_id", "")
        period = data.get("period", "")
        if employee_id:
            record = {
                "record_id": data.get("record_id", str(uuid4())),
                "employee_id": employee_id,
                "period": period,
                "base_salary": data.get("base_salary", 0),
                "allowances": data.get("allowances", 0),
                "deductions": data.get("deductions", 0),
                "social_insurance": data.get("social_insurance", 0),
                "housing_fund": data.get("housing_fund", 0),
                "individual_tax": data.get("individual_tax", 0),
                "net_salary": data.get("net_salary", 0),
                "status": data.get("status", "calculated"),
                "source": "event",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._payroll_records.append(record)
            schedule(
                payroll_record_repository.upsert_by_record_id(record),
                "payroll_record_repo",
            )
            logger.info("payroll_agent.record_synced",
                        employee_id=employee_id, period=period)

    async def _handle_salary_adjusted(self, data: dict):
        """处理薪资调整事件"""
        employee_id = data.get("employee_id", "")
        if employee_id and employee_id in self._salary_structures:
            structure = self._salary_structures[employee_id]
            if "base_salary" in data:
                structure["base_salary"] = data["base_salary"]
            if "allowances" in data:
                structure["allowances"] = data["allowances"]
            if "deductions" in data:
                structure["deductions"] = data["deductions"]
            structure["updated_at"] = datetime.now(timezone.utc).isoformat()
            structure["adjustment_reason"] = data.get("reason", "")
            logger.info("payroll_agent.salary_adjusted",
                        employee_id=employee_id)

    async def _handle_attendance_recorded(self, data: dict):
        """处理考勤记录事件（用于缺勤扣款参考）"""
        employee_id = data.get("employee_id", "")
        status = data.get("status", "normal")
        if employee_id and status != "normal":
            logger.info("payroll_agent.attendance_noted",
                        employee_id=employee_id,
                        status=status,
                        note="非正常考勤将影响薪酬计算")

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出薪酬决策"""
        action = context.get("action", "")

        if action == "calculate_payroll":
            return await self._decide_calculate(context)
        elif action == "adjust_salary":
            return await self._decide_adjust(context)

        return AgentDecision(
            decision_type="payroll_unknown",
            reasoning=f"未知操作: {action}",
            action="拒绝执行",
            confidence=0.0,
            context=context,
        )

    async def _decide_calculate(self, context: dict) -> AgentDecision:
        """薪酬计算决策"""
        employee_id = context.get("employee_id", "")
        period = context.get("period", "")
        structure = self._salary_structures.get(employee_id)
        if not structure:
            return AgentDecision(
                decision_type="payroll_calculation",
                reasoning=f"员工薪资结构不存在: {employee_id}",
                action="拒绝计算",
                confidence=1.0,
            )
        base_salary = structure.get("base_salary", 0)
        if base_salary <= 0:
            return AgentDecision(
                decision_type="payroll_calculation",
                reasoning=f"员工{employee_id}基本工资为0，无法计算",
                action="拒绝计算",
                confidence=0.9,
            )
        return AgentDecision(
            decision_type="payroll_calculation",
            reasoning=f"计算员工{employee_id}在{period}周期的薪酬",
            action="执行薪酬计算",
            confidence=0.95,
            context=context,
        )

    async def _decide_adjust(self, context: dict) -> AgentDecision:
        """薪资调整决策"""
        employee_id = context.get("employee_id", "")
        new_base = context.get("base_salary", 0)
        structure = self._salary_structures.get(employee_id)
        if not structure:
            return AgentDecision(
                decision_type="salary_adjustment",
                reasoning=f"员工薪资结构不存在: {employee_id}",
                action="拒绝调整",
                confidence=1.0,
            )
        old_base = structure.get("base_salary", 0)
        if new_base <= 0:
            return AgentDecision(
                decision_type="salary_adjustment",
                reasoning="调整后基本工资必须大于0",
                action="拒绝调整",
                confidence=1.0,
            )
        # 调整幅度超过50%需人工审批
        if old_base > 0:
            change_ratio = abs(new_base - old_base) / old_base
            if change_ratio > 0.5:
                return AgentDecision(
                    decision_type="salary_adjustment",
                    reasoning=f"薪资调整幅度{change_ratio*100:.0f}%超过50%，需人工审批",
                    action="转人工审批",
                    confidence=0.8,
                    requires_human_approval=True,
                    context=context,
                )
        return AgentDecision(
            decision_type="salary_adjustment",
            reasoning=f"员工{employee_id}薪资从{old_base}调整为{new_base}",
            action="执行薪资调整",
            confidence=0.9,
            requires_human_approval=True,
            context=context,
        )

    # ========== 公开 API ==========

    def get_salary_structure(self, employee_id: str) -> Optional[dict]:
        """获取员工薪资结构"""
        return self._salary_structures.get(employee_id)

    def set_salary_structure(self, employee_id: str, base_salary: float,
                             allowances: float = 0,
                             deductions: float = 0, **kwargs) -> dict:
        """设置员工薪资结构"""
        now = datetime.now(timezone.utc).isoformat()
        structure = {
            "employee_id": employee_id,
            "base_salary": base_salary,
            "allowances": allowances,
            "deductions": deductions,
            "bank_account": kwargs.get("bank_account", ""),
            "payment_method": kwargs.get("payment_method", "bank_transfer"),
            "effective_date": kwargs.get("effective_date", now[:10]),
            "created_at": now,
            "updated_at": now,
        }
        self._salary_structures[employee_id] = structure
        schedule(
            salary_structure_repository.upsert(structure, "employee_id"),
            "salary_structure_repo",
        )
        logger.info("payroll_agent.salary_structure_set",
                    employee_id=employee_id, base_salary=base_salary)
        return structure

    def get_social_insurance_config(self) -> dict:
        """获取社保配置"""
        return dict(self._social_insurance_config)

    def update_social_insurance_config(self, rates: dict) -> dict:
        """更新社保配置"""
        for key, value in rates.items():
            self._social_insurance_config[key] = float(value)
        self._social_insurance_config["updated_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        logger.info("payroll_agent.social_insurance_updated",
                    rates=rates)
        return self.get_social_insurance_config()

    def calculate_individual_tax(self, taxable_income: float) -> float:
        """
        计算个人所得税（累进税率）

        使用综合所得年度税率表，按月预扣预缴。
        taxable_income 为应纳税所得额（已扣除起征点和专项扣除）。
        """
        if taxable_income <= 0:
            return 0.0

        tax = 0.0
        remaining = taxable_income
        prev_limit = 0

        for bracket in self._tax_brackets:
            lower = prev_limit
            upper = bracket["limit"]
            rate = bracket["rate"]
            quick_deduction = bracket["quick_deduction"]

            if taxable_income > lower:
                # 该档应纳税额
                taxable_in_bracket = min(taxable_income, upper) - lower
                tax += taxable_in_bracket * rate
                prev_limit = upper
            else:
                break

        # 使用速算扣除数法：tax = taxable_income * rate - quick_deduction
        # 找到适用的最高档
        applicable_bracket = None
        for bracket in self._tax_brackets:
            if taxable_income > (applicable_bracket["limit"] if applicable_bracket else 0):
                applicable_bracket = bracket
            else:
                break

        if applicable_bracket:
            tax = round(
                taxable_income * applicable_bracket["rate"]
                - applicable_bracket["quick_deduction"], 2
            )

        return max(tax, 0.0)

    def calculate_payroll(self, employee_id: str, period: str) -> dict:
        """
        计算单个员工薪酬

        返回包含：基本工资、津贴、扣款、社保、公积金、个税、实发工资
        """
        structure = self._salary_structures.get(employee_id)
        if not structure:
            return {"error": f"员工薪资结构不存在: {employee_id}"}

        base_salary = float(structure.get("base_salary", 0))
        allowances = float(structure.get("allowances", 0))
        deductions = float(structure.get("deductions", 0))

        # 计算社保（个人缴纳部分）
        config = self._social_insurance_config
        pension = round(base_salary * config.get("pension", 0), 2)
        medical = round(base_salary * config.get("medical", 0), 2)
        unemployment = round(base_salary * config.get("unemployment", 0), 2)
        social_insurance_total = round(
            pension + medical + unemployment, 2
        )

        # 公积金（个人缴纳部分）
        housing_fund = round(
            base_salary * config.get("housing_fund", 0), 2
        )

        # 应纳税所得额 = 基本工资 + 津贴 - 扣款 - 社保 - 公积金 - 起征点
        gross_salary = round(base_salary + allowances - deductions, 2)
        taxable_income = round(
            gross_salary - social_insurance_total - housing_fund
            - self.TAX_THRESHOLD, 2
        )
        individual_tax = self.calculate_individual_tax(taxable_income)

        # 实发工资 = 应发工资 - 社保 - 公积金 - 个税
        net_salary = round(
            gross_salary - social_insurance_total - housing_fund
            - individual_tax, 2
        )

        record = {
            "record_id": str(uuid4()),
            "employee_id": employee_id,
            "period": period,
            "base_salary": base_salary,
            "allowances": allowances,
            "deductions": deductions,
            "gross_salary": gross_salary,
            "social_insurance": social_insurance_total,
            "social_insurance_detail": {
                "pension": pension,
                "medical": medical,
                "unemployment": unemployment,
            },
            "housing_fund": housing_fund,
            "taxable_income": taxable_income,
            "individual_tax": individual_tax,
            "net_salary": net_salary,
            "status": "calculated",
            "source": "manual",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._payroll_records.append(record)
        schedule(
            payroll_record_repository.upsert_by_record_id(record),
            "payroll_record_repo",
        )
        logger.info("payroll_agent.calculated",
                    employee_id=employee_id, period=period,
                    net_salary=net_salary)
        return record

    def batch_calculate_payroll(self, period: str) -> list[dict]:
        """批量计算所有员工薪酬"""
        results = []
        for employee_id in list(self._salary_structures.keys()):
            record = self.calculate_payroll(employee_id, period)
            if "error" not in record:
                results.append(record)
        logger.info("payroll_agent.batch_calculated",
                    period=period, count=len(results))
        return results

    def get_payroll_records(self, employee_id: Optional[str] = None,
                            period: Optional[str] = None) -> list[dict]:
        """获取薪酬记录，可按员工和周期过滤"""
        records = list(self._payroll_records)
        if employee_id:
            records = [r for r in records if r["employee_id"] == employee_id]
        if period:
            records = [r for r in records if r["period"] == period]
        return records

    def get_stats(self) -> dict:
        """获取薪酬统计信息"""
        all_records = list(self._payroll_records)
        total_gross = sum(r.get("gross_salary", 0) for r in all_records)
        total_net = sum(r.get("net_salary", 0) for r in all_records)
        total_tax = sum(r.get("individual_tax", 0) for r in all_records)
        total_social = sum(r.get("social_insurance", 0) for r in all_records)
        total_fund = sum(r.get("housing_fund", 0) for r in all_records)

        # 按周期统计
        by_period: dict[str, dict] = {}
        for r in all_records:
            p = r.get("period", "unknown")
            if p not in by_period:
                by_period[p] = {"count": 0, "total_net": 0}
            by_period[p]["count"] += 1
            by_period[p]["total_net"] += r.get("net_salary", 0)

        return {
            "total_employees_with_structure": len(self._salary_structures),
            "total_payroll_records": len(all_records),
            "total_gross_salary": round(total_gross, 2),
            "total_net_salary": round(total_net, 2),
            "total_individual_tax": round(total_tax, 2),
            "total_social_insurance": round(total_social, 2),
            "total_housing_fund": round(total_fund, 2),
            "by_period": by_period,
        }

    # ========== 种子数据 ==========

    def _seed_data(self):
        """加载种子数据：预置社保比例与个税税率表"""
        now = datetime.now(timezone.utc).isoformat()

        # 社保比例（个人缴纳部分）
        self._social_insurance_config = {
            "pension": 0.08,         # 养老保险 8%
            "medical": 0.02,         # 医疗保险 2%
            "unemployment": 0.005,   # 失业保险 0.5%
            "work_injury": 0.0,      # 工伤保险（单位缴纳，个人0%）
            "maternity": 0.0,        # 生育保险（单位缴纳，个人0%）
            "housing_fund": 0.12,    # 住房公积金 12%
            "updated_at": now,
        }

        # 个税累进税率表（综合所得，按月换算）
        # limit: 该档上限（元/月），rate: 税率，quick_deduction: 速算扣除数
        self._tax_brackets = [
            {"limit": 3000, "rate": 0.03, "quick_deduction": 0},
            {"limit": 12000, "rate": 0.10, "quick_deduction": 210},
            {"limit": 25000, "rate": 0.20, "quick_deduction": 1410},
            {"limit": 35000, "rate": 0.25, "quick_deduction": 2660},
            {"limit": 55000, "rate": 0.30, "quick_deduction": 4410},
            {"limit": 80000, "rate": 0.35, "quick_deduction": 7160},
            {"limit": float("inf"), "rate": 0.45, "quick_deduction": 15160},
        ]

        # 预置与HR种子员工对应的薪资结构
        seed_structures = [
            {
                "employee_id": "EMP0001",
                "base_salary": 25000,
                "allowances": 5000,
                "deductions": 0,
                "bank_account": "6222000000000000001",
                "payment_method": "bank_transfer",
                "effective_date": now[:10],
            },
            {
                "employee_id": "EMP0002",
                "base_salary": 22000,
                "allowances": 4000,
                "deductions": 0,
                "bank_account": "6222000000000000002",
                "payment_method": "bank_transfer",
                "effective_date": now[:10],
            },
            {
                "employee_id": "EMP0003",
                "base_salary": 18000,
                "allowances": 3000,
                "deductions": 0,
                "bank_account": "6222000000000000003",
                "payment_method": "bank_transfer",
                "effective_date": now[:10],
            },
            {
                "employee_id": "EMP0004",
                "base_salary": 15000,
                "allowances": 2500,
                "deductions": 0,
                "bank_account": "6222000000000000004",
                "payment_method": "bank_transfer",
                "effective_date": now[:10],
            },
            {
                "employee_id": "EMP0005",
                "base_salary": 12000,
                "allowances": 2000,
                "deductions": 0,
                "bank_account": "6222000000000000005",
                "payment_method": "bank_transfer",
                "effective_date": now[:10],
            },
        ]

        for s in seed_structures:
            s["created_at"] = now
            s["updated_at"] = now
            self._salary_structures[s["employee_id"]] = s

        logger.info("payroll_agent.seed_data_loaded",
                    structures=len(self._salary_structures),
                    tax_brackets=len(self._tax_brackets))
