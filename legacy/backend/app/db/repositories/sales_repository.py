"""
销售/CRM 持久化 Repository（路线图 2.1/2.2 批次一：sales + crm）
================================================================

承载 CRM Agent 的客户档案与销售 Agent 的销售订单落库。
customers.owner_department 支撑 2.9 行级隔离首批（部门过滤）。
"""
from __future__ import annotations

from typing import Optional

from app.db.models import Customer, SalesOrder
from app.db.repositories.base import RepositoryBase, has_db, session_scope


class CustomerRepository(RepositoryBase):
    """客户档案（customer_id 主键）。"""

    __model__ = Customer
    __category__ = "customer_repo"

    @staticmethod
    def __to_row__(c: dict) -> dict:
        return {
            "customer_id": c["customer_id"],
            "customer_name": c.get("name") or c.get("customer_name") or "",
            "contact_person": c.get("contact_name") or c.get("contact_person") or None,
            "phone": c.get("phone") or None,
            "email": c.get("email") or None,
            "address": c.get("address") or None,
            "credit_rating": c.get("credit_rating") or None,
            "owner_department": c.get("owner_department") or None,
            "status": c.get("status", "active"),
        }

    @staticmethod
    def __to_dict__(row: Customer) -> dict:
        from datetime import datetime

        return {
            "customer_id": row.customer_id,
            "name": row.customer_name,
            "industry": "",
            "segment": row.credit_rating or "C",
            "contact_person": row.contact_person or "",
            "phone": row.phone or "",
            "email": row.email or "",
            "owner_department": row.owner_department or "",
            "status": row.status,
            "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
        }

    async def list_by_department(
        self,
        owner_department: Optional[str] = None,
        segment: Optional[str] = None,
        industry: Optional[str] = None,
    ) -> list[dict]:
        """
        按归属部门/分级过滤查询（2.9 行级隔离首批的落库侧查询）。

        行级规则：归属部门为空的客户默认对所有部门可见；
        ``owner_department`` 为 None 时返回全量（管理员场景）。
        """
        if not has_db():
            return []
        try:
            from sqlalchemy import or_, select

            stmt = select(self.__model__)
            if owner_department:
                stmt = stmt.where(
                    or_(
                        self.__model__.owner_department == owner_department,
                        self.__model__.owner_department.is_(None),
                    )
                )
            if segment:
                stmt = stmt.where(self.__model__.credit_rating == segment)
            async with session_scope() as session:
                rows = (await session.execute(stmt)).scalars().all()
            return [self.__to_dict__(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            self._warn(f"{self.__category__}.list_by_department_failed", error=str(e))
            return []


class SalesOrderRepository(RepositoryBase):
    """销售订单（order_id 主键）。"""

    __model__ = SalesOrder
    __category__ = "sales_order_repo"

    @staticmethod
    def __to_row__(o: dict) -> dict:
        from datetime import datetime

        def _date(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
            except (ValueError, TypeError):
                return None

        def _dt(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None

        return {
            "order_id": o["order_id"],
            # order_number 非空唯一：无编号生成时回退 order_id
            "order_number": o.get("order_number") or o["order_id"],
            "customer_id": o["customer_id"],
            "customer_name": o.get("customer_name") or None,
            "items": o.get("items") or None,
            "confirmed_at": _dt(o.get("confirmed_at")),
            "total_amount": float(o.get("total_amount", 0)),
            "status": o.get("status", "pending"),
            "delivery_date": _date(o.get("delivery_date")),
            "order_date": _date(o.get("created_at")),
        }

    @staticmethod
    def __to_dict__(row: SalesOrder) -> dict:
        from datetime import datetime

        def _iso(v):
            return v.isoformat() if isinstance(v, datetime) else v

        return {
            "order_id": row.order_id,
            "customer_id": row.customer_id,
            "customer_name": row.customer_name or "",
            "items": row.items or [],
            "total_amount": row.total_amount,
            "status": row.status,
            "delivery_date": _iso(row.delivery_date),
            "created_at": _iso(row.order_date),
            "confirmed_at": _iso(row.confirmed_at),
        }

    async def get_by_id(self, order_id: str) -> Optional[dict]:
        """按订单号读取（未命中返回 None）。"""
        if not has_db():
            return None
        try:
            async with session_scope() as session:
                row = await session.get(self.__model__, order_id)
            return self.__to_dict__(row) if row else None
        except Exception as e:  # noqa: BLE001
            self._warn(f"{self.__category__}.get_failed", error=str(e))
            return None

    async def update_status(self, order_id: str, status: str,
                            confirmed_at=None) -> bool:
        """更新订单状态（确认订单的增量写）。"""
        if not has_db():
            return False
        try:
            from sqlalchemy import update

            values: dict = {"status": status}
            if confirmed_at is not None:
                values["confirmed_at"] = confirmed_at
            async with session_scope() as session:
                await session.execute(
                    update(self.__model__)
                    .where(self.__model__.order_id == order_id)
                    .values(**values)
                )
            return True
        except Exception as e:  # noqa: BLE001
            self._warn(f"{self.__category__}.status_update_failed", error=str(e))
            return False


# 模块级单例
customer_repository = CustomerRepository()
sales_order_repository = SalesOrderRepository()
