"""
人力资源 Agent
负责员工档案管理、考勤记录、请假审批与组织架构维护。
监听员工入离职、考勤与请假事件，自动更新员工档案。
"""
import structlog
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.core.agent.base import BaseAgent, AgentDecision
from app.core.event.models import Event, EventType
from app.db.repositories.base import has_db, schedule
from app.db.repositories.hr_repository import (
    attendance_repository,
    employee_repository,
)

logger = structlog.get_logger()


class HRAgent(BaseAgent):
    """人力资源 Agent - 管理员工全生命周期"""

    def __init__(self):
        super().__init__(
            name="人力资源Agent",
            domain="hr",
            capabilities=[
                "employee_management",
                "attendance_tracking",
                "leave_management",
                "org_management",
                "salary_structure",
                "general",
            ],
            security_clearance="confidential",
        )
        # 员工档案: employee_id -> dict
        self._employees: dict[str, dict] = {}
        # 考勤记录列表
        self._attendance_records: list[dict] = []
        # 请假记录列表
        self._leave_requests: list[dict] = []
        # 组织架构: department_code -> dict
        self._org_chart: dict[str, dict] = {}
        # 员工编号计数器
        self._employee_seq: int = 0
        # 加载种子数据
        self._seed_data()

    @property
    def description(self) -> str:
        return "员工档案管理、考勤跟踪、请假审批与组织架构维护"

    # ========== 初始化 ==========

    async def initialize(self):
        """初始化Agent：订阅HR相关事件"""
        self.subscribe_events(
            event_types=[
                EventType.EMPLOYEE_ONBOARDED,
                EventType.EMPLOYEE_OFFBOARDED,
                EventType.ATTENDANCE_RECORDED,
                EventType.LEAVE_REQUESTED,
            ],
            handler=self._handle_event,
        )
        logger.info("hr_agent.initialized", agent=self.name,
                    employees=len(self._employees))

    async def hydrate_from_db(self) -> bool:
        """
        启动水合：DB 优先、内存种子兜底、空库回播种（路线图 2.2 批次一）。

        Returns:
            True 表示员工档案已与 DB 对齐。
        """
        if not has_db():
            return False

        hydrated = False
        employees = await employee_repository.list_all()
        if employees:
            self._employees = {
                e["employee_id"]: e for e in employees
            }
            self._employee_seq = max(
                (int(e["employee_id"][3:]) for e in employees
                 if e["employee_id"].startswith("EMP") and e["employee_id"][3:].isdigit()),
                default=0,
            )
            hydrated = True
        elif self._employees:
            for e in list(self._employees.values()):
                await employee_repository.upsert(e, "employee_id")
            logger.info("hr_agent.employees_seeded_to_db",
                        count=len(self._employees))
            hydrated = True

        records = await attendance_repository.list_all()
        if records:
            self._attendance_records = records
            hydrated = True

        logger.info(
            "hr_agent.hydrated",
            from_db=hydrated,
            employees=len(self._employees),
            attendance=len(self._attendance_records),
        )
        return hydrated

    async def _handle_event(self, event: Event):
        """事件处理入口（包装异常处理）"""
        try:
            await self.process_event(event)
        except Exception as e:
            self._error_count += 1
            self.circuit_breaker.record_failure()
            logger.error("hr_agent.event_error",
                         agent=self.name, error=str(e))

    # ========== 事件处理 ==========

    async def process_event(self, event: Event):
        """处理接收到的事件"""
        data = event.event_data

        if event.event_type == EventType.EMPLOYEE_ONBOARDED:
            await self._handle_onboarded(data)
        elif event.event_type == EventType.EMPLOYEE_OFFBOARDED:
            await self._handle_offboarded(data)
        elif event.event_type == EventType.ATTENDANCE_RECORDED:
            await self._handle_attendance_event(data)
        elif event.event_type == EventType.LEAVE_REQUESTED:
            await self._handle_leave_event(data)

    async def _handle_onboarded(self, data: dict):
        """处理员工入职事件"""
        employee_id = data.get("employee_id", "")
        if employee_id and employee_id in self._employees:
            # 已存在则更新状态
            self._employees[employee_id]["status"] = "active"
            self._employees[employee_id]["onboarded_at"] = data.get(
                "onboarded_at", datetime.now(timezone.utc).isoformat()
            )
            logger.info("hr_agent.employee_onboarded_updated",
                        employee_id=employee_id)

    async def _handle_offboarded(self, data: dict):
        """处理员工离职事件"""
        employee_id = data.get("employee_id", "")
        if employee_id and employee_id in self._employees:
            self._employees[employee_id]["status"] = "inactive"
            self._employees[employee_id]["offboarded_at"] = data.get(
                "offboarded_at", datetime.now(timezone.utc).isoformat()
            )
            logger.info("hr_agent.employee_offboarded",
                        employee_id=employee_id)

    async def _handle_attendance_event(self, data: dict):
        """处理考勤记录事件（同步外部考勤数据）"""
        employee_id = data.get("employee_id", "")
        if employee_id and employee_id in self._employees:
            record = {
                "record_id": str(uuid4()),
                "employee_id": employee_id,
                "date": data.get("date", ""),
                "check_in": data.get("check_in", ""),
                "check_out": data.get("check_out", ""),
                "status": data.get("status", "normal"),
                "source": "event",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._attendance_records.append(record)
            logger.info("hr_agent.attendance_synced",
                        employee_id=employee_id)

    async def _handle_leave_event(self, data: dict):
        """处理请假申请事件（同步外部请假数据）"""
        employee_id = data.get("employee_id", "")
        if employee_id and employee_id in self._employees:
            request = {
                "request_id": data.get("request_id", str(uuid4())),
                "employee_id": employee_id,
                "leave_type": data.get("leave_type", "personal"),
                "start_date": data.get("start_date", ""),
                "end_date": data.get("end_date", ""),
                "reason": data.get("reason", ""),
                "status": data.get("status", "pending"),
                "source": "event",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._leave_requests.append(request)
            logger.info("hr_agent.leave_synced",
                        employee_id=employee_id)

    # ========== 决策 ==========

    async def make_decision(self, context: dict) -> AgentDecision:
        """做出HR决策"""
        action = context.get("action", "")

        if action == "approve_leave":
            return await self._decide_leave(context)
        elif action == "onboard_employee":
            return await self._decide_onboard(context)
        elif action == "offboard_employee":
            return await self._decide_offboard(context)

        return AgentDecision(
            decision_type="hr_unknown",
            reasoning=f"未知操作: {action}",
            action="拒绝执行",
            confidence=0.0,
            context=context,
        )

    async def _decide_leave(self, context: dict) -> AgentDecision:
        """请假审批决策"""
        employee_id = context.get("employee_id", "")
        leave_type = context.get("leave_type", "personal")
        days = context.get("days", 0)

        employee = self._employees.get(employee_id)
        if not employee:
            return AgentDecision(
                decision_type="leave_approval",
                reasoning=f"员工不存在: {employee_id}",
                action="拒绝审批",
                confidence=1.0,
            )

        # 年假超过剩余天数需人工审批
        if leave_type == "annual" and days > 5:
            return AgentDecision(
                decision_type="leave_approval",
                reasoning=f"年假{days}天超过5天，需主管审批",
                action="转人工审批",
                confidence=0.8,
                requires_human_approval=True,
                context=context,
            )

        return AgentDecision(
            decision_type="leave_approval",
            reasoning=f"员工{employee.get('name')}申请{leave_type}假{days}天，符合规定",
            action="批准请假",
            confidence=0.9,
            context=context,
        )

    async def _decide_onboard(self, context: dict) -> AgentDecision:
        """入职决策"""
        department = context.get("department", "")
        if department not in self._org_chart:
            return AgentDecision(
                decision_type="employee_onboard",
                reasoning=f"部门不存在: {department}",
                action="拒绝入职",
                confidence=1.0,
            )
        return AgentDecision(
            decision_type="employee_onboard",
            reasoning=f"新员工入职到{department}部门",
            action="办理入职",
            confidence=0.95,
            context=context,
        )

    async def _decide_offboard(self, context: dict) -> AgentDecision:
        """离职决策"""
        employee_id = context.get("employee_id", "")
        employee = self._employees.get(employee_id)
        if not employee:
            return AgentDecision(
                decision_type="employee_offboard",
                reasoning=f"员工不存在: {employee_id}",
                action="无法办理离职",
                confidence=0.0,
            )
        return AgentDecision(
            decision_type="employee_offboard",
            reasoning=f"员工{employee.get('name')}办理离职",
            action="办理离职交接",
            confidence=0.9,
            requires_human_approval=True,
            context=context,
        )

    # ========== 公开 API ==========

    def _generate_employee_id(self) -> str:
        """生成员工编号"""
        self._employee_seq += 1
        return f"EMP{self._employee_seq:04d}"

    def get_employees(self, department: Optional[str] = None) -> list[dict]:
        """获取员工列表，可按部门过滤"""
        employees = list(self._employees.values())
        if department:
            employees = [e for e in employees if e.get("department") == department]
        return employees

    def get_employee(self, employee_id: str) -> Optional[dict]:
        """获取单个员工档案"""
        return self._employees.get(employee_id)

    def add_employee(self, name: str, department: str, position: str,
                     **kwargs) -> dict:
        """新增员工"""
        employee_id = kwargs.get("employee_id") or self._generate_employee_id()
        now = datetime.now(timezone.utc).isoformat()
        employee = {
            "employee_id": employee_id,
            "name": name,
            "department": department,
            "position": position,
            "email": kwargs.get("email", ""),
            "phone": kwargs.get("phone", ""),
            "hire_date": kwargs.get("hire_date", now[:10]),
            "status": kwargs.get("status", "active"),
            "salary_structure": kwargs.get("salary_structure", {
                "base_salary": kwargs.get("base_salary", 0),
                "allowances": kwargs.get("allowances", 0),
                "deductions": kwargs.get("deductions", 0),
            }),
            "manager_id": kwargs.get("manager_id", ""),
            "created_at": now,
        }
        self._employees[employee_id] = employee
        schedule(
            employee_repository.upsert(employee, "employee_id"),
            "employee_repo",
        )
        logger.info("hr_agent.employee_added",
                    employee_id=employee_id, name=name, department=department)
        return employee

    def update_employee(self, employee_id: str, **kwargs) -> dict:
        """更新员工档案"""
        employee = self._employees.get(employee_id)
        if not employee:
            return {"error": f"员工不存在: {employee_id}"}
        for key, value in kwargs.items():
            if key != "employee_id":
                employee[key] = value
        employee["updated_at"] = datetime.now(timezone.utc).isoformat()
        schedule(
            employee_repository.upsert(employee, "employee_id"),
            "employee_repo",
        )
        logger.info("hr_agent.employee_updated", employee_id=employee_id)
        return employee

    def record_attendance(self, employee_id: str, date: str,
                          check_in: str, check_out: str,
                          status: str = "normal") -> dict:
        """记录考勤"""
        if employee_id not in self._employees:
            return {"error": f"员工不存在: {employee_id}"}
        record = {
            "record_id": str(uuid4()),
            "employee_id": employee_id,
            "date": date,
            "check_in": check_in,
            "check_out": check_out,
            "status": status,
            "source": "manual",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._attendance_records.append(record)
        schedule(
            attendance_repository.upsert_by_id(record),
            "attendance_repo",
        )
        logger.info("hr_agent.attendance_recorded",
                    employee_id=employee_id, date=date)
        return record

    def get_attendance(self, employee_id: Optional[str] = None,
                       date: Optional[str] = None) -> list[dict]:
        """获取考勤记录，可按员工和日期过滤"""
        records = list(self._attendance_records)
        if employee_id:
            records = [r for r in records if r["employee_id"] == employee_id]
        if date:
            records = [r for r in records if r["date"] == date]
        return records

    def request_leave(self, employee_id: str, leave_type: str,
                      start_date: str, end_date: str,
                      reason: str) -> dict:
        """提交请假申请"""
        if employee_id not in self._employees:
            return {"error": f"员工不存在: {employee_id}"}
        request = {
            "request_id": str(uuid4()),
            "employee_id": employee_id,
            "leave_type": leave_type,
            "start_date": start_date,
            "end_date": end_date,
            "reason": reason,
            "status": "pending",
            "source": "manual",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._leave_requests.append(request)
        logger.info("hr_agent.leave_requested",
                    employee_id=employee_id, leave_type=leave_type)
        return request

    def get_leave_requests(self, employee_id: Optional[str] = None,
                           status: Optional[str] = None) -> list[dict]:
        """获取请假记录，可按员工和状态过滤"""
        requests = list(self._leave_requests)
        if employee_id:
            requests = [r for r in requests if r["employee_id"] == employee_id]
        if status:
            requests = [r for r in requests if r["status"] == status]
        return requests

    def get_org_chart(self) -> dict:
        """获取组织架构"""
        return self._org_chart

    def get_stats(self) -> dict:
        """获取HR统计信息"""
        all_employees = list(self._employees.values())
        active = [e for e in all_employees if e.get("status") == "active"]
        inactive = [e for e in all_employees if e.get("status") == "inactive"]

        by_department: dict[str, int] = {}
        for e in all_employees:
            dept = e.get("department", "unknown")
            by_department[dept] = by_department.get(dept, 0) + 1

        pending_leaves = [
            r for r in self._leave_requests if r["status"] == "pending"
        ]

        return {
            "total_employees": len(all_employees),
            "active_employees": len(active),
            "inactive_employees": len(inactive),
            "by_department": by_department,
            "total_attendance_records": len(self._attendance_records),
            "total_leave_requests": len(self._leave_requests),
            "pending_leave_requests": len(pending_leaves),
            "departments": len(self._org_chart),
        }

    # ========== 种子数据 ==========

    def _seed_data(self):
        """加载种子数据：预置5个示例员工与组织架构"""
        # 组织架构
        self._org_chart = {
            "技术部": {
                "code": "TECH",
                "name": "技术部",
                "head": "EMP0001",
                "parent": "总经办",
            },
            "产品部": {
                "code": "PROD",
                "name": "产品部",
                "head": "EMP0002",
                "parent": "总经办",
            },
            "财务部": {
                "code": "FIN",
                "name": "财务部",
                "head": "EMP0003",
                "parent": "总经办",
            },
            "人事部": {
                "code": "HR",
                "name": "人事部",
                "head": "EMP0004",
                "parent": "总经办",
            },
            "市场部": {
                "code": "MKT",
                "name": "市场部",
                "head": "EMP0005",
                "parent": "总经办",
            },
        }

        # 5个示例员工
        seed_employees = [
            {
                "employee_id": "EMP0001",
                "name": "张伟",
                "department": "技术部",
                "position": "技术总监",
                "email": "zhangwei@company.com",
                "phone": "13800000001",
                "hire_date": "2018-03-15",
                "status": "active",
                "salary_structure": {
                    "base_salary": 25000,
                    "allowances": 5000,
                    "deductions": 0,
                },
                "manager_id": "",
            },
            {
                "employee_id": "EMP0002",
                "name": "李娜",
                "department": "产品部",
                "position": "产品总监",
                "email": "lina@company.com",
                "phone": "13800000002",
                "hire_date": "2019-06-01",
                "status": "active",
                "salary_structure": {
                    "base_salary": 22000,
                    "allowances": 4000,
                    "deductions": 0,
                },
                "manager_id": "",
            },
            {
                "employee_id": "EMP0003",
                "name": "王强",
                "department": "财务部",
                "position": "财务经理",
                "email": "wangqiang@company.com",
                "phone": "13800000003",
                "hire_date": "2017-09-10",
                "status": "active",
                "salary_structure": {
                    "base_salary": 18000,
                    "allowances": 3000,
                    "deductions": 0,
                },
                "manager_id": "",
            },
            {
                "employee_id": "EMP0004",
                "name": "刘洋",
                "department": "人事部",
                "position": "人事经理",
                "email": "liuyang@company.com",
                "phone": "13800000004",
                "hire_date": "2020-01-20",
                "status": "active",
                "salary_structure": {
                    "base_salary": 15000,
                    "allowances": 2500,
                    "deductions": 0,
                },
                "manager_id": "",
            },
            {
                "employee_id": "EMP0005",
                "name": "陈静",
                "department": "市场部",
                "position": "市场专员",
                "email": "chenjing@company.com",
                "phone": "13800000005",
                "hire_date": "2021-07-05",
                "status": "active",
                "salary_structure": {
                    "base_salary": 12000,
                    "allowances": 2000,
                    "deductions": 0,
                },
                "manager_id": "EMP0001",
            },
        ]

        for emp in seed_employees:
            emp["created_at"] = datetime.now(timezone.utc).isoformat()
            self._employees[emp["employee_id"]] = emp

        self._employee_seq = 5
        logger.info("hr_agent.seed_data_loaded",
                    employees=len(self._employees),
                    departments=len(self._org_chart))
