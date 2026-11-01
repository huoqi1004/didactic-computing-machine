"""
Agent 持久化 Repository 层（路线图 2.1）
========================================

批次一（2.2）：payroll / hr / crm / sales。
批次二（2.3）：treasury / contract。

用法约定（Agent 侧）：
- 写路径：内存更新后 ``schedule(repo.upsert(...), category)`` 双写；
- 读路径：启动时 ``hydrate_from_db()``，DB 优先、内存种子兜底。
"""
from app.db.repositories.base import (
    RepositoryBase,
    has_db,
    schedule,
    session_scope,
    warn_throttled,
)
from app.db.repositories.hr_repository import (
    attendance_repository,
    employee_repository,
)
from app.db.repositories.payroll_repository import (
    payroll_record_repository,
    salary_structure_repository,
)
from app.db.repositories.sales_repository import (
    customer_repository,
    sales_order_repository,
)
from app.db.repositories.treasury_contract_repository import (
    contract_repository,
    fund_transfer_repository,
    payment_schedule_repository,
)
from app.db.repositories.reimbursement_repository import (
    reimbursement_invoice_repository,
)

__all__ = [
    "RepositoryBase",
    "has_db",
    "schedule",
    "session_scope",
    "warn_throttled",
    "salary_structure_repository",
    "payroll_record_repository",
    "employee_repository",
    "attendance_repository",
    "customer_repository",
    "sales_order_repository",
    "fund_transfer_repository",
    "payment_schedule_repository",
    "contract_repository",
    "reimbursement_invoice_repository",
]
