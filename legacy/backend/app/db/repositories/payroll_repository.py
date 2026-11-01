"""
薪酬持久化 Repository（路线图 2.1/2.2 批次一：payroll）
========================================================

承载薪酬 Agent 的薪资结构与薪酬记录落库：
- 写路径：Agent 更新内存后调用 upsert（fire-and-forget 双写）
- 读路径：启动水合（DB 优先、内存种子兜底）
"""
from __future__ import annotations

from typing import Optional

from app.db.models import PayrollRecord, SalaryStructure
from app.db.repositories.base import RepositoryBase, has_db, session_scope


class SalaryStructureRepository(RepositoryBase):
    """薪资结构（employee_id 为主键的 upsert 语义）。"""

    __model__ = SalaryStructure
    __category__ = "salary_structure_repo"

    @staticmethod
    def __to_row__(s: dict) -> dict:
        return {
            "employee_id": s["employee_id"],
            "base_salary": float(s.get("base_salary", 0)),
            "allowances": float(s.get("allowances", 0)),
            "deductions": float(s.get("deductions", 0)),
            "bank_account": s.get("bank_account") or None,
            "payment_method": s.get("payment_method") or None,
            "effective_date": s.get("effective_date") or None,
        }

    @staticmethod
    def __to_dict__(row: SalaryStructure) -> dict:
        from datetime import datetime

        return {
            "employee_id": row.employee_id,
            "base_salary": row.base_salary,
            "allowances": row.allowances,
            "deductions": row.deductions,
            "bank_account": row.bank_account or "",
            "payment_method": row.payment_method or "bank_transfer",
            "effective_date": row.effective_date or "",
            "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
            "updated_at": row.updated_at.isoformat() if isinstance(row.updated_at, datetime) else None,
        }


class PayrollRecordRepository(RepositoryBase):
    """薪酬记录（按 record_id 幂等 upsert）。"""

    __model__ = PayrollRecord
    __category__ = "payroll_record_repo"

    @staticmethod
    def __to_row__(r: dict) -> dict:
        return {
            "record_id": r.get("record_id"),
            "employee_id": r["employee_id"],
            "period": r["period"],
            "base_salary": float(r.get("base_salary", 0)),
            "allowances": float(r.get("allowances", 0)),
            "deduction": float(r.get("deductions", 0)),
            "gross_salary": float(r.get("gross_salary", 0)),
            "social_insurance": float(r.get("social_insurance", 0)),
            "housing_fund": float(r.get("housing_fund", 0)),
            "taxable_income": float(r.get("taxable_income", 0)),
            "individual_tax": float(r.get("individual_tax", 0)),
            "tax": float(r.get("individual_tax", 0)),
            "net_salary": float(r.get("net_salary", 0)),
            "source": r.get("source") or None,
            "detail_json": r.get("social_insurance_detail") or None,
            "status": r.get("status", "calculated"),
        }

    @staticmethod
    def __to_dict__(row: PayrollRecord) -> dict:
        from datetime import datetime

        def _dt(v):
            return v.isoformat() if isinstance(v, datetime) else v

        return {
            "record_id": row.record_id or f"legacy-{row.id}",
            "employee_id": row.employee_id,
            "period": row.period,
            "base_salary": row.base_salary,
            "allowances": row.allowances,
            "deductions": row.deduction,
            "gross_salary": row.gross_salary,
            "social_insurance": row.social_insurance,
            "housing_fund": row.housing_fund,
            "taxable_income": row.taxable_income,
            "individual_tax": row.individual_tax,
            "net_salary": row.net_salary,
            "source": row.source or "db",
            "status": row.status,
            "created_at": _dt(row.created_at),
        }

    async def upsert_by_record_id(self, record: dict) -> bool:
        """按 record_id 幂等写入（record_id 缺失时跳过，避免重复行）。"""
        if not record.get("record_id") or not has_db():
            return False
        return await self.upsert(record, key_field="record_id")


    async def delete_by_record_id(self, record_id: str) -> bool:
        """按 record_id 删除（薪酬计算工具的补偿回滚）。"""
        if not record_id or not has_db():
            return False
        try:
            from sqlalchemy import delete

            async with session_scope() as session:
                await session.execute(
                    delete(self.__model__).where(self.__model__.record_id == record_id)
                )
            return True
        except Exception as e:  # noqa: BLE001
            self._warn(f"{self.__category__}.delete_failed", error=str(e))
            return False


# 模块级单例
salary_structure_repository = SalaryStructureRepository()
payroll_record_repository = PayrollRecordRepository()
