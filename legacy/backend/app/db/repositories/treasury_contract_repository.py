"""
资金/合同持久化 Repository（路线图 2.3 批次二：treasury + contract）
====================================================================

- 资金：资金调拨（fund_transfers）、支付排程（payment_schedules 新表）
- 合同：合同档案（contracts，批次二扩展列）
"""
from __future__ import annotations

from typing import Optional

from app.db.models import Contract, FundTransfer, PaymentSchedule
from app.db.repositories.base import RepositoryBase, has_db, session_scope


class FundTransferRepository(RepositoryBase):
    """资金调拨（transfer_id 主键）。"""

    __model__ = FundTransfer
    __category__ = "fund_transfer_repo"

    @staticmethod
    def __to_row__(t: dict) -> dict:
        from datetime import datetime

        def _dt(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None

        return {
            "transfer_id": t["transfer_id"],
            "from_account": t.get("from_account", ""),
            "to_account": t.get("to_account", ""),
            "amount": float(t.get("amount", 0)),
            "reason": t.get("reason") or None,
            "requires_human_approval": bool(t.get("requires_human_approval", False)),
            "status": t.get("status", "pending_approval"),
            "approved_at": _dt(t.get("approved_at")),
            "rejected_at": _dt(t.get("rejected_at")),
            "transfer_date": _dt(t.get("created_at")),
        }

    @staticmethod
    def __to_dict__(row: FundTransfer) -> dict:
        from datetime import datetime

        def _iso(v):
            return v.isoformat() if isinstance(v, datetime) else v

        return {
            "transfer_id": row.transfer_id,
            "amount": row.amount,
            "from_account": row.from_account,
            "to_account": row.to_account,
            "reason": row.reason or "",
            "requires_human_approval": bool(row.requires_human_approval),
            "status": row.status,
            "approved_at": _iso(row.approved_at),
            "rejected_at": _iso(row.rejected_at),
            "created_at": _iso(row.created_at),
        }


class PaymentScheduleRepository(RepositoryBase):
    """支付排程（payment_id 主键）。"""

    __model__ = PaymentSchedule
    __category__ = "payment_schedule_repo"

    @staticmethod
    def __to_row__(p: dict) -> dict:
        return {
            "payment_id": p["payment_id"],
            "po_id": p.get("po_id", ""),
            "supplier_id": p.get("supplier_id") or None,
            "amount": float(p.get("amount", 0)),
            "payment_date": p.get("payment_date") or None,
            "status": p.get("status", "scheduled"),
            "optimization_method": p.get("optimization_method") or None,
            "reasoning": p.get("reasoning") or None,
        }

    @staticmethod
    def __to_dict__(row: PaymentSchedule) -> dict:
        from datetime import datetime

        return {
            "payment_id": row.payment_id,
            "po_id": row.po_id,
            "supplier_id": row.supplier_id or "",
            "amount": row.amount,
            "payment_date": row.payment_date or "",
            "status": row.status,
            "optimization_method": row.optimization_method or "rules",
            "reasoning": row.reasoning or "",
            "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
        }


class ContractRepository(RepositoryBase):
    """合同档案（contract_id 主键）。"""

    __model__ = Contract
    __category__ = "contract_repo"

    @staticmethod
    def __to_row__(c: dict) -> dict:
        from datetime import datetime

        def _date(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
            except (ValueError, TypeError):
                return None

        return {
            "contract_id": c["contract_id"],
            # contract_number 非空唯一：无编号生成时回退 contract_id
            "contract_number": c.get("contract_number") or c["contract_id"],
            "title": c.get("title", ""),
            "party_a": c.get("party_a") or "本公司",
            "party_b": c.get("counterparty") or c.get("party_b") or "",
            "contract_type": c.get("type") or None,
            "department": c.get("department") or None,
            "terms": c.get("terms") or None,
            "approved_by": c.get("approved_by") or None,
            "approval_comment": c.get("approval_comment") or None,
            "approved_at": _date(c.get("approved_at")),
            "completion_notes": c.get("completion_notes") or None,
            "amount": float(c.get("amount", 0)),
            "start_date": _date(c.get("start_date")),
            "end_date": _date(c.get("end_date")),
            "status": c.get("status", "draft"),
        }

    @staticmethod
    def __to_dict__(row: Contract) -> dict:
        from datetime import date, datetime

        def _iso(v):
            if isinstance(v, (datetime, date)):
                return v.isoformat()
            return v

        return {
            "contract_id": row.contract_id,
            "title": row.title,
            "type": row.contract_type or "",
            "counterparty": row.party_b,
            "department": row.department or "",
            "terms": row.terms or "",
            "approved_by": row.approved_by or "",
            "approval_comment": row.approval_comment or "",
            "approved_at": _iso(row.approved_at),
            "completion_notes": row.completion_notes or "",
            "amount": row.amount,
            "start_date": _iso(row.start_date) or "",
            "end_date": _iso(row.end_date) or "",
            "status": row.status,
            "created_at": _iso(row.created_at),
        }

    async def update_status(self, contract_id: str, status: str,
                            extra: Optional[dict] = None) -> bool:
        """合同状态增量更新（审批/签署/完成）。"""
        if not has_db():
            return False
        try:
            from sqlalchemy import update

            from datetime import datetime

            values: dict = {"status": status}
            for key, value in (extra or {}).items():
                if value is None:
                    continue
                if key.endswith("_at") and isinstance(value, str):
                    # Agent 内存存 ISO 字符串；DateTime 列需要 datetime 对象
                    try:
                        value = datetime.fromisoformat(value)
                    except ValueError:
                        continue
                values[key] = value
            async with session_scope() as session:
                await session.execute(
                    update(self.__model__)
                    .where(self.__model__.contract_id == contract_id)
                    .values(**values)
                )
            return True
        except Exception as e:  # noqa: BLE001
            self._warn(f"{self.__category__}.status_update_failed", error=str(e))
            return False


# 模块级单例
fund_transfer_repository = FundTransferRepository()
payment_schedule_repository = PaymentScheduleRepository()
contract_repository = ContractRepository()
