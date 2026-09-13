"""
人力资源持久化 Repository（路线图 2.1/2.2 批次一：hr）
======================================================

承载 HR Agent 的员工档案与考勤记录落库。
"""
from __future__ import annotations

from typing import Optional

from app.db.models import AttendanceRecord, Employee
from app.db.repositories.base import RepositoryBase, has_db, session_scope


class EmployeeRepository(RepositoryBase):
    """员工档案（employee_id 主键）。"""

    __model__ = Employee
    __category__ = "employee_repo"

    @staticmethod
    def __to_row__(e: dict) -> dict:
        def _date(v):
            if not v:
                return None
            from datetime import datetime

            try:
                return datetime.fromisoformat(str(v)).date()
            except (ValueError, TypeError):
                return None

        return {
            "employee_id": e["employee_id"],
            # employee_number 非空唯一：无编号生成时回退 employee_id
            "employee_number": e.get("employee_number") or e["employee_id"],
            "full_name": e.get("name") or e.get("full_name") or "",
            # 部门名称存 department_name 列（无 FK）；department_id 留给规范部门 ID
            "department_name": e.get("department") or None,
            "position": e.get("position") or None,
            "email": e.get("email") or None,
            "phone": e.get("phone") or None,
            "hire_date": _date(e.get("hire_date")),
            "status": e.get("status", "active"),
        }

    @staticmethod
    def __to_dict__(row: Employee) -> dict:
        from datetime import date, datetime

        def _iso(v):
            if isinstance(v, (datetime, date)):
                return v.isoformat()
            return v

        return {
            "employee_id": row.employee_id,
            "name": row.full_name,
            "department": row.department_name or "",
            "position": row.position or "",
            "email": row.email or "",
            "phone": row.phone or "",
            "hire_date": _iso(row.hire_date) or "",
            "status": row.status,
            "salary_structure": {},
            "manager_id": "",
        }


class AttendanceRepository(RepositoryBase):
    """考勤记录（追加为主，按 record_id 幂等）。"""

    __model__ = AttendanceRecord
    __category__ = "attendance_repo"

    @staticmethod
    def __to_row__(r: dict) -> dict:
        from datetime import datetime

        def _dt(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(str(v))
            except (ValueError, TypeError):
                return None

        return {
            "employee_id": r["employee_id"],
            "date": _dt(r.get("date")),
            "check_in": _dt(r.get("check_in")),
            "check_out": _dt(r.get("check_out")),
            "status": r.get("status", "normal"),
        }

    @staticmethod
    def __to_dict__(row: AttendanceRecord) -> dict:
        from datetime import datetime

        def _iso(v):
            return v.isoformat() if isinstance(v, datetime) else v

        return {
            "record_id": f"att-{row.id}",
            "employee_id": row.employee_id,
            "date": _iso(row.date),
            "check_in": _iso(row.check_in),
            "check_out": _iso(row.check_out),
            "status": row.status,
            "source": "db",
        }

    async def upsert_by_id(self, record: dict) -> bool:
        """考勤为追加型（内存态 record_id 为 uuid，ORM 侧自增主键），直接插入。"""
        if not has_db():
            return False
        try:
            async with session_scope() as session:
                session.add(self.__model__(**self.__to_row__(record)))
            return True
        except Exception as e:  # noqa: BLE001
            self._warn(f"{self.__category__}.insert_failed", error=str(e))
            return False


# 模块级单例
employee_repository = EmployeeRepository()
attendance_repository = AttendanceRepository()
