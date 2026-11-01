"""
报销发票识别件 Repository（财务模块 · 发票上传识别）
====================================================

承载上传发票的解析结果落库与报销单关联（幂等 upsert）。
"""
from __future__ import annotations

from typing import Optional

from app.db.models import Reimbursement, ReimbursementInvoice
from app.db.repositories.base import RepositoryBase, has_db, session_scope


class ReimbursementInvoiceRepository(RepositoryBase):
    """报销发票识别件（invoice_id 主键）。"""

    __model__ = ReimbursementInvoice
    __category__ = "reimb_invoice_repo"

    # 票面要素列（record dict 键 -> ORM 列同名）
    _FIELD_COLUMNS = (
        "invoice_type", "invoice_title", "buyer_type", "taxpayer_id",
        "invoice_date", "invoice_number", "invoice_code",
        "total_amount", "tax_amount", "amount_without_tax",
        "seller_name", "remark", "confidence",
    )

    @staticmethod
    def __to_row__(r: dict) -> dict:
        row = {
            "invoice_id": r["invoice_id"],
            "file_name": r.get("file_name", ""),
            "file_size": int(r.get("file_size", 0)),
            "file_path": r.get("file_path"),
            "uploaded_by": r.get("uploaded_by"),
            "uploaded_by_name": r.get("uploaded_by_name"),
            "raw_text": r.get("raw_text"),
            "parsed_json": r.get("parsed_json"),
            "status": r.get("status", "parsed"),
            "reimbursement_id": r.get("reimbursement_id"),
        }
        for col in ReimbursementInvoiceRepository._FIELD_COLUMNS:
            if col in r and r[col] is not None:
                row[col] = r[col]
        return row

    @staticmethod
    def __to_dict__(row: ReimbursementInvoice) -> dict:
        from datetime import datetime

        def _iso(v):
            return v.isoformat() if isinstance(v, datetime) else v

        return {
            "invoice_id": row.invoice_id,
            "file_name": row.file_name,
            "file_size": row.file_size,
            "uploaded_by": row.uploaded_by,
            "uploaded_by_name": row.uploaded_by_name,
            "invoice_type": row.invoice_type,
            "invoice_title": row.invoice_title,
            "buyer_type": row.buyer_type,
            "taxpayer_id": row.taxpayer_id,
            "invoice_date": row.invoice_date,
            "invoice_number": row.invoice_number,
            "invoice_code": row.invoice_code,
            "total_amount": row.total_amount,
            "tax_amount": row.tax_amount,
            "amount_without_tax": row.amount_without_tax,
            "seller_name": row.seller_name,
            "remark": row.remark,
            "confidence": row.confidence,
            "status": row.status,
            "reimbursement_id": row.reimbursement_id,
            "linked_at": _iso(row.linked_at),
            "created_at": _iso(row.created_at),
        }

    async def get_by_id(self, invoice_id: str) -> Optional[dict]:
        if not has_db():
            return None
        try:
            async with session_scope() as session:
                row = await session.get(self.__model__, invoice_id)
            return self.__to_dict__(row) if row else None
        except Exception as e:  # noqa: BLE001
            self._warn(f"{self.__category__}.get_failed", error=str(e))
            return None

    async def get_full_by_id(self, invoice_id: str) -> Optional[dict]:
        """详情查询：在基础字段上附加完整解析 JSON 与原始文本。"""
        if not has_db():
            return None
        try:
            async with session_scope() as session:
                row = await session.get(self.__model__, invoice_id)
            if row is None:
                return None
            detail = self.__to_dict__(row)
            detail["parsed_json"] = row.parsed_json
            detail["raw_text"] = row.raw_text
            return detail
        except Exception as e:  # noqa: BLE001
            self._warn(f"{self.__category__}.get_full_failed", error=str(e))
            return None

    async def list_mine(
        self, uploaded_by: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        """按上传人查询（uploaded_by 为 None 时返回全部，供财务角色）。"""
        if not has_db():
            return []
        try:
            from sqlalchemy import select

            stmt = select(self.__model__).order_by(
                self.__model__.created_at.desc()
            )
            if uploaded_by:
                stmt = stmt.where(self.__model__.uploaded_by == uploaded_by)
            async with session_scope() as session:
                rows = (await session.execute(stmt.limit(limit))).scalars().all()
            return [self.__to_dict__(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            self._warn(f"{self.__category__}.list_failed", error=str(e))
            return []

    async def mark_linked(self, invoice_id: str, reimbursement_id: str) -> bool:
        """报销提交后绑定报销单（状态 parsed -> linked）。"""
        if not has_db():
            return False
        try:
            from sqlalchemy import update
            from datetime import datetime, timezone

            async with session_scope() as session:
                result = await session.execute(
                    update(self.__model__)
                    .where(
                        self.__model__.invoice_id == invoice_id,
                        self.__model__.status == "parsed",
                    )
                    .values(
                        status="linked",
                        reimbursement_id=reimbursement_id,
                        linked_at=datetime.now(timezone.utc),
                    )
                )
                return bool(result.rowcount)
        except Exception as e:  # noqa: BLE001
            self._warn(f"{self.__category__}.link_failed", error=str(e))
            return False


# 模块级单例
reimbursement_invoice_repository = ReimbursementInvoiceRepository()


class ReimbursementRequestRepository(RepositoryBase):
    """报销单据（req_id 主键，Phase A/A6：报销 Agent 单据落库）。"""

    __model__ = Reimbursement
    __category__ = "reimbursement_request_repo"

    @staticmethod
    def __to_row__(r: dict) -> dict:
        from datetime import datetime

        def _dt(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None

        return {
            "req_id": r["reimbursement_id"],
            "req_number": r.get("reimbursement_no") or r["reimbursement_id"],
            "employee_id": r.get("employee_id") or "unknown",
            "employee_name": r.get("employee_name") or None,
            "department": r.get("department") or None,
            "has_receipt": bool(r.get("has_receipt", False)),
            "receipt_amount": float(r.get("receipt_amount", 0) or 0),
            "invoice_id": r.get("invoice_id") or None,
            "project_id": r.get("project_id") or None,
            "project_name": r.get("project_name") or None,
            "category": r.get("category", "other"),
            "amount": float(r.get("amount", 0) or 0),
            "description": r.get("description") or None,
            "status": r.get("status", "pending"),
            "submitted_at": _dt(r.get("submitted_at")),
        }

    @staticmethod
    def __to_dict__(row: Reimbursement) -> dict:
        from datetime import datetime

        def _iso(v):
            return v.isoformat() if isinstance(v, datetime) else v

        return {
            "reimbursement_id": row.req_id,
            "reimbursement_no": row.req_number,
            "employee_id": row.employee_id,
            "employee_name": row.employee_name or "",
            "department": row.department or "",
            "category": row.category,
            "amount": row.amount,
            "description": row.description or "",
            "has_receipt": bool(row.has_receipt),
            "receipt_amount": row.receipt_amount,
            "invoice_id": row.invoice_id or "",
            "project_id": row.project_id or "",
            "project_name": row.project_name or "",
            "status": row.status,
            "submitted_at": _iso(row.submitted_at),
            "created_at": _iso(row.created_at),
        }

    async def delete_by_id(self, req_id: str) -> bool:
        """精确回滚用（补偿函数）。"""
        if not has_db():
            return False
        try:
            from sqlalchemy import delete

            async with session_scope() as session:
                await session.execute(
                    delete(self.__model__).where(self.__model__.req_id == req_id)
                )
            return True
        except Exception as e:  # noqa: BLE001
            self._warn(f"{self.__category__}.delete_failed", error=str(e))
            return False


# 模块级单例
reimbursement_request_repository = ReimbursementRequestRepository()
