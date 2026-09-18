"""
数据库模型层 (SQLAlchemy 2.0 ORM)
================================

定义 Agent-Native ERP 系统的全部业务表 ORM 模型。

设计约定
--------
- 使用 SQLAlchemy 2.0 ``DeclarativeBase`` 风格（``Mapped`` / ``mapped_column``）；
- 所有表统一使用 ``utf8mb4`` 字符集；
- 主键采用业务可读字符串 ID（部分事件 / 审计表使用 BIGINT 自增）；
- 字符串字段显式指定长度，便于 DDL 生成；
- JSON 字段统一使用 ``sqlalchemy.JSON`` 类型，兼容多种后端；
- 时间字段统一使用 ``created_at`` / ``updated_at``（where applicable）。

涵盖的模块
----------
- RBAC：部门 / 用户 / 角色 / 权限 / 用户角色 / 角色权限
- 财务业务：发票 / 报销 / 税务 / 凭证 / 资金调拨
- 人力资源：员工 / 考勤 / 薪资
- 销售与市场：客户 / 销售订单 / 营销活动
- 运营管理：库存 / 资产 / 项目 / 质检
- 法务风控：合同 / 风险
- 战略分析：预算 / BI 报表
- 知识库：文档 / 分类
- 事件与审计：事件记录 / 审计条目
- 工作流：工作流请求
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    String,
    Integer,
    BigInteger,
    Float,
    Text,
    JSON,
    DateTime,
    Date,
    Boolean,
    ForeignKey,
    UniqueConstraint,
    Index,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """所有 ORM 模型的声明基类。

    通过 ``mysql_charset='utf8mb4'`` 强制所有表使用 utf8mb4 字符集，
    以支持完整的 Unicode 字符（含 emoji 与生僻汉字）。
    """

    pass


# 统一 MySQL 表选项：所有表使用 utf8mb4 字符集
_UTF8MB4_TABLE_ARGS = {"mysql_charset": "utf8mb4", "mysql_engine": "InnoDB"}


# sqlite 仅对 INTEGER 主键做 rowid 自增；MySQL 用 BIGINT。
# with_variant 保证两种方言下自增主键都可用（测试用 sqlite / 生产 MySQL）。
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


def _utc_now() -> datetime:
    """统一的当前时间戳生成函数。"""
    return datetime.now()


# ===========================================================================
# RBAC 权限管理（5 张实体表 + 2 张关联表）
# ===========================================================================


class Department(Base):
    """
    部门表
    ------
    支持自引用的层级组织结构（通过 ``parent_department_id`` 建立父子关系）。
    """

    __tablename__ = "departments"
    __table_args__ = _UTF8MB4_TABLE_ARGS

    department_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    department_name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_department_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("departments.department_id"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    # 自引用关系
    parent: Mapped["Department | None"] = relationship(
        "Department",
        remote_side=[department_id],
        back_populates="children",
        foreign_keys=[parent_department_id],
    )
    children: Mapped[list["Department"]] = relationship(
        "Department",
        back_populates="parent",
        foreign_keys=[parent_department_id],
    )

    def __repr__(self) -> str:
        return (
            f"<Department department_id={self.department_id!r} "
            f"code={self.department_code!r} name={self.department_name!r}>"
        )


class User(Base):
    """
    用户表
    ------
    RBAC 主体表，存储登录凭证与个人信息。每个用户关联一个部门。
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_department", "department_id"),
        Index("ix_users_status", "status"),
        _UTF8MB4_TABLE_ARGS,
    )

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # department_id 列存储部门名称字符串（兼容 RBAC 引擎设计），非外键
    department_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    email: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    position: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # D-04：首登强制改密标记。种子用户与新建用户默认为 True，
    # 通过修改密码接口清除。server_default 供 bootstrap 列差集对存量库补列。
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 关联
    roles: Mapped[list["UserRole"]] = relationship(
        "UserRole", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<User user_id={self.user_id!r} username={self.username!r} "
            f"status={self.status!r}>"
        )


class Role(Base):
    """
    角色表
    ------
    支持角色层级（通过 ``parent_role_id``），``level`` 表示角色等级。
    """

    __tablename__ = "roles"
    __table_args__ = _UTF8MB4_TABLE_ARGS

    role_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role_name: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_role_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("roles.role_id"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    # 自引用关系
    parent: Mapped["Role | None"] = relationship(
        "Role", remote_side=[role_id], foreign_keys=[parent_role_id]
    )

    def __repr__(self) -> str:
        return (
            f"<Role role_id={self.role_id!r} code={self.role_code!r} "
            f"name={self.role_name!r} level={self.level}>"
        )


class Permission(Base):
    """
    权限表
    ------
    权限以 ``accounting:read`` 形式定义，``resource`` 与 ``action`` 分别标识资源与动作。
    """

    __tablename__ = "permissions"
    __table_args__ = _UTF8MB4_TABLE_ARGS

    permission_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sensitivity: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<Permission permission_id={self.permission_id!r} "
            f"resource={self.resource!r} action={self.action!r} "
            f"sensitivity={self.sensitivity!r}>"
        )


class UserRole(Base):
    """用户-角色关联表（复合唯一索引确保一个用户不重复分配同一角色）。"""

    __tablename__ = "user_role"
    __table_args__ = (
        UniqueConstraint("user_id", "role_code", name="uq_user_role"),
        Index("ix_user_role_user", "user_id"),
        Index("ix_user_role_role", "role_code"),
        _UTF8MB4_TABLE_ARGS,
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.user_id"), nullable=False
    )
    role_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("roles.role_code"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )

    user: Mapped["User"] = relationship("User", back_populates="roles")
    role: Mapped["Role"] = relationship("Role")

    def __repr__(self) -> str:
        return (
            f"<UserRole id={self.id} user_id={self.user_id!r} "
            f"role_code={self.role_code!r}>"
        )


class RolePermission(Base):
    """角色-权限关联表（复合唯一索引确保一个角色不重复授予同一权限）。"""

    __tablename__ = "role_permission"
    __table_args__ = (
        UniqueConstraint("role_code", "permission_id", name="uq_role_permission"),
        Index("ix_role_permission_role", "role_code"),
        Index("ix_role_permission_permission", "permission_id"),
        _UTF8MB4_TABLE_ARGS,
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    role_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("roles.role_code"), nullable=False
    )
    permission_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("permissions.permission_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )

    role: Mapped["Role"] = relationship("Role")
    permission: Mapped["Permission"] = relationship("Permission")

    def __repr__(self) -> str:
        return (
            f"<RolePermission id={self.id} role_code={self.role_code!r} "
            f"permission_id={self.permission_id!r}>"
        )


# ===========================================================================
# 财务业务表
# ===========================================================================


class Invoice(Base):
    """
    发票表
    ------
    记录销售 / 采购发票的完整信息，包含校验与作废状态。
    """

    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_status", "status"),
        Index("ix_invoices_issue_date", "issue_date"),
        Index("ix_invoices_buyer", "buyer_name"),
        Index("ix_invoices_seller", "seller_name"),
        _UTF8MB4_TABLE_ARGS,
    )

    invoice_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    invoice_number: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    invoice_type: Mapped[str] = mapped_column(String(32), nullable=False)
    buyer_name: Mapped[str] = mapped_column(String(256), nullable=False)
    seller_name: Mapped[str] = mapped_column(String(256), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tax_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tax_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="issued")
    issue_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    verify_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    cancel_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<Invoice invoice_id={self.invoice_id!r} "
            f"number={self.invoice_number!r} status={self.status!r}>"
        )


class Reimbursement(Base):
    """
    报销申请表
    ----------
    记录员工报销的全生命周期：提交 -> 审核 -> 审批 -> 支付 / 驳回。
    """

    __tablename__ = "reimbursements"
    __table_args__ = (
        Index("ix_reimbursements_status", "status"),
        Index("ix_reimbursements_employee", "employee_id"),
        Index("ix_reimbursements_category", "category"),
        _UTF8MB4_TABLE_ARGS,
    )

    req_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    req_number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="submitted"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<Reimbursement req_id={self.req_id!r} "
            f"number={self.req_number!r} status={self.status!r}>"
        )


class TaxRecord(Base):
    """
    税务记录表
    ----------
    记录每个税期的销项税、进项税与应纳税额，以及申报状态。
    """

    __tablename__ = "tax_records"
    __table_args__ = (
        Index("ix_tax_records_period", "tax_period"),
        Index("ix_tax_records_status", "status"),
        Index("ix_tax_records_type", "tax_type"),
        _UTF8MB4_TABLE_ARGS,
    )

    record_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tax_period: Mapped[str] = mapped_column(String(16), nullable=False)
    tax_type: Mapped[str] = mapped_column(String(32), nullable=False)
    output_tax: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_tax: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    payable_tax: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    declaration_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    filed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<TaxRecord record_id={self.record_id!r} "
            f"period={self.tax_period!r} type={self.tax_type!r} "
            f"status={self.status!r}>"
        )


class ReimbursementInvoice(Base):
    """
    报销发票识别件表
    ----------------
    财务模块 · 报销管理发票上传识别（解析 PDF 文本层得到的票面要素
    与报销表单的关联记录）。``status``: parsed（已识别未关联）
    / linked（已关联报销单）/ invalid（人工标记无效）。
    """

    __tablename__ = "reimbursement_invoices"
    __table_args__ = (
        Index("ix_reimb_invoices_number", "invoice_number"),
        Index("ix_reimb_invoices_reimb", "reimbursement_id"),
        Index("ix_reimb_invoices_status", "status"),
        Index("ix_reimb_invoices_uploader", "uploaded_by"),
        _UTF8MB4_TABLE_ARGS,
    )

    invoice_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_by_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ---- 票面要素（识别结果快照，便于查询；完整解析在 parsed_json）----
    invoice_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    invoice_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    buyer_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    taxpayer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    invoice_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    invoice_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    total_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    tax_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_without_tax: Mapped[float | None] = mapped_column(Float, nullable=True)
    seller_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(256), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="parsed")
    reimbursement_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<ReimbursementInvoice invoice_id={self.invoice_id!r} "
            f"number={self.invoice_number!r} status={self.status!r}>"
        )


class Voucher(Base):
    """
    会计凭证表
    ----------
    凭证头表，``entries`` 字段以 JSON 形式存储多分录明细。
    """

    __tablename__ = "vouchers"
    __table_args__ = (
        Index("ix_vouchers_period", "period"),
        Index("ix_vouchers_status", "status"),
        Index("ix_vouchers_date", "voucher_date"),
        _UTF8MB4_TABLE_ARGS,
    )

    voucher_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    voucher_number: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    voucher_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    debit_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    credit_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    entries: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<Voucher voucher_id={self.voucher_id!r} "
            f"number={self.voucher_number!r} period={self.period!r} "
            f"status={self.status!r}>"
        )


class FundTransfer(Base):
    """
    资金调拨表
    ----------
    记录账户间资金调拨的发起与审批状态。
    """

    __tablename__ = "fund_transfers"
    __table_args__ = (
        Index("ix_fund_transfers_status", "status"),
        Index("ix_fund_transfers_date", "transfer_date"),
        _UTF8MB4_TABLE_ARGS,
    )

    transfer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    from_account: Mapped[str] = mapped_column(String(128), nullable=False)
    to_account: Mapped[str] = mapped_column(String(128), nullable=False)
    # 资金 Agent 批次二扩展列（bootstrap 对存量库增量补列）
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    requires_human_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    transfer_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<FundTransfer transfer_id={self.transfer_id!r} "
            f"from={self.from_account!r} to={self.to_account!r} "
            f"amount={self.amount} status={self.status!r}>"
        )


# ===========================================================================
# 人力资源表
# ===========================================================================


class Employee(Base):
    """
    员工表
    ------
    人力资源主数据表，关联部门与考勤 / 薪资记录。
    """

    __tablename__ = "employees"
    __table_args__ = (
        Index("ix_employees_department", "department_id"),
        Index("ix_employees_status", "status"),
        _UTF8MB4_TABLE_ARGS,
    )

    employee_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    employee_number: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(8), nullable=True)
    birth_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Agent 侧的部门名称（HR Agent 用名称管理，如"技术部"），无 FK。
    # 命名避开 Employee.department relationship。
    department_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    department_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("departments.department_id"), nullable=True
    )
    position: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hire_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    department: Mapped["Department | None"] = relationship("Department")

    def __repr__(self) -> str:
        return (
            f"<Employee employee_id={self.employee_id!r} "
            f"number={self.employee_number!r} name={self.full_name!r}>"
        )


class AttendanceRecord(Base):
    """
    考勤记录表
    ----------
    记录员工每日打卡与加班情况。
    """

    __tablename__ = "attendance_records"
    __table_args__ = (
        Index("ix_attendance_employee", "employee_id"),
        Index("ix_attendance_date", "date"),
        Index("ix_attendance_status", "status"),
        _UTF8MB4_TABLE_ARGS,
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    check_in: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    check_out: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    overtime_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<AttendanceRecord id={self.id} employee_id={self.employee_id!r} "
            f"date={self.date} status={self.status!r}>"
        )


class PayrollRecord(Base):
    """
    薪资记录表
    ----------
    记录员工每个薪资周期的各项明细与实发金额。

    路线图 2.2 批次一扩展列（record_id/allowances/gross_salary 等）：
    由 bootstrap 列差集对存量库增量补列，承载薪酬 Agent 的完整记录字典。
    """

    __tablename__ = "payroll_records"
    __table_args__ = (
        Index("ix_payroll_employee", "employee_id"),
        Index("ix_payroll_period", "period"),
        Index("ix_payroll_status", "status"),
        Index("ix_payroll_record_id", "record_id", unique=True),
        _UTF8MB4_TABLE_ARGS,
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    # Agent 侧记录标识（uuid）；存量行为 NULL，新记录唯一
    record_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    base_salary: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    bonus: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Agent 字典的 allowances（津贴）独立成列，不再与 bonus 混用
    allowances: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    deduction: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gross_salary: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    social_insurance: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    tax: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Agent 字典的 individual_tax（个税）显式列（tax 列保留兼容）
    individual_tax: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    net_salary: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # 批次一扩展：公积金 / 应纳税所得额 / 来源 / 明细 JSON
    housing_fund: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    taxable_income: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detail_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<PayrollRecord id={self.id} employee_id={self.employee_id!r} "
            f"period={self.period!r} status={self.status!r}>"
        )


class SalaryStructure(Base):
    """
    薪资结构表
    ----------
    员工薪资结构（基本工资/津贴/扣款/银行卡），路线图 2.2 批次一新增，
    承接薪酬 Agent 内存字典的落库（D-01/D-02 行级隔离的数据前提）。
    """

    __tablename__ = "salary_structures"
    __table_args__ = (
        Index("ix_salary_structures_employee", "employee_id"),
        _UTF8MB4_TABLE_ARGS,
    )

    employee_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    base_salary: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    allowances: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    deductions: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    bank_account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    effective_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<SalaryStructure employee_id={self.employee_id!r} "
            f"base_salary={self.base_salary}>"
        )


# ===========================================================================
# 销售与市场表
# ===========================================================================


class Customer(Base):
    """
    客户表
    ------
    维护客户主数据与信用评级。
    """

    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_status", "status"),
        Index("ix_customers_credit_rating", "credit_rating"),
        _UTF8MB4_TABLE_ARGS,
    )

    customer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_name: Mapped[str] = mapped_column(String(256), nullable=False)
    contact_person: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    credit_rating: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 2.9 行级隔离首批：客户归属部门（CRUD 后补列，无归属则全部门可见）
    owner_department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<Customer customer_id={self.customer_id!r} "
            f"name={self.customer_name!r} status={self.status!r}>"
        )


class SalesOrder(Base):
    """
    销售订单表
    ----------
    关联客户，记录订单金额与交付状态。
    """

    __tablename__ = "sales_orders"
    __table_args__ = (
        Index("ix_sales_orders_customer", "customer_id"),
        Index("ix_sales_orders_status", "status"),
        Index("ix_sales_orders_date", "order_date"),
        _UTF8MB4_TABLE_ARGS,
    )

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("customers.customer_id"), nullable=False
    )
    # 批次一扩展：客户名快照 / 行项 JSON / 确认时间（销售 Agent 订单落库）
    customer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    order_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    delivery_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    salesperson: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    customer: Mapped["Customer"] = relationship("Customer")

    def __repr__(self) -> str:
        return (
            f"<SalesOrder order_id={self.order_id!r} "
            f"number={self.order_number!r} status={self.status!r}>"
        )


class Campaign(Base):
    """
    营销活动表
    ----------
    记录市场活动的预算与实际花费，用于 ROI 分析。
    """

    __tablename__ = "campaigns"
    __table_args__ = (
        Index("ix_campaigns_status", "status"),
        Index("ix_campaigns_dates", "start_date", "end_date"),
        _UTF8MB4_TABLE_ARGS,
    )

    campaign_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_name: Mapped[str] = mapped_column(String(256), nullable=False)
    start_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    budget: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    actual_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="planning"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<Campaign campaign_id={self.campaign_id!r} "
            f"name={self.campaign_name!r} status={self.status!r}>"
        )


# ===========================================================================
# 运营管理表
# ===========================================================================


class InventoryItem(Base):
    """
    库存物料表
    ----------
    维护每个物料的实时库存与安全库存，支持低库存预警。
    """

    __tablename__ = "inventory_items"
    __table_args__ = (
        Index("ix_inventory_items_category", "category"),
        Index("ix_inventory_items_warehouse", "warehouse"),
        Index("ix_inventory_items_quantity_safety", "quantity", "safety_stock"),
        _UTF8MB4_TABLE_ARGS,
    )

    item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    item_name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safety_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    warehouse: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<InventoryItem item_id={self.item_id!r} "
            f"code={self.item_code!r} qty={self.quantity} "
            f"safety={self.safety_stock}>"
        )


class Asset(Base):
    """
    资产表
    ------
    固定资产主数据，记录原值与净值以支持折旧分析。
    """

    __tablename__ = "assets"
    __table_args__ = (
        Index("ix_assets_category", "category"),
        Index("ix_assets_status", "status"),
        Index("ix_assets_user", "user_name"),
        _UTF8MB4_TABLE_ARGS,
    )

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    asset_name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    purchase_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    original_value: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    net_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="in_use")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<Asset asset_id={self.asset_id!r} "
            f"code={self.asset_code!r} name={self.asset_name!r} "
            f"status={self.status!r}>"
        )


class Project(Base):
    """
    项目表
    ------
    记录项目预算、实际花费与进度。
    """

    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_status", "status"),
        Index("ix_projects_manager", "manager"),
        _UTF8MB4_TABLE_ARGS,
    )

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    project_name: Mapped[str] = mapped_column(String(256), nullable=False)
    manager: Mapped[str | None] = mapped_column(String(128), nullable=True)
    start_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    budget: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    actual_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="planning")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<Project project_id={self.project_id!r} "
            f"code={self.project_code!r} name={self.project_name!r} "
            f"status={self.status!r} progress={self.progress}>"
        )


class QualityInspection(Base):
    """
    质量检验表
    ----------
    记录产品批次质检结果与缺陷信息。
    """

    __tablename__ = "quality_inspections"
    __table_args__ = (
        Index("ix_quality_inspections_date", "inspection_date"),
        Index("ix_quality_inspections_result", "result"),
        Index("ix_quality_inspections_inspector", "inspector"),
        _UTF8MB4_TABLE_ARGS,
    )

    inspection_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_name: Mapped[str] = mapped_column(String(256), nullable=False)
    batch_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inspection_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    inspector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    defects: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<QualityInspection inspection_id={self.inspection_id!r} "
            f"product={self.product_name!r} result={self.result!r}>"
        )


# ===========================================================================
# 法务风控表
# ===========================================================================


class Contract(Base):
    """
    合同表
    ------
    记录合同主体、金额与履行周期。
    """

    __tablename__ = "contracts"
    __table_args__ = (
        Index("ix_contracts_status", "status"),
        Index("ix_contracts_party_a", "party_a"),
        Index("ix_contracts_party_b", "party_b"),
        Index("ix_contracts_dates", "start_date", "end_date"),
        _UTF8MB4_TABLE_ARGS,
    )

    contract_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_number: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    party_a: Mapped[str] = mapped_column(String(256), nullable=False)
    party_b: Mapped[str] = mapped_column(String(256), nullable=False)
    # 合同 Agent 批次二扩展列（bootstrap 对存量库增量补列）
    contract_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_comment: Mapped[str | None] = mapped_column(String(512), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    start_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft"
    )
    signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<Contract contract_id={self.contract_id!r} "
            f"number={self.contract_number!r} status={self.status!r}>"
        )


class PaymentSchedule(Base):
    """
    支付排程表
    ----------
    路线图 2.3 批次二新增：承接资金 Agent 依据采购订单生成的应付支付排程
    （get_payment_schedule 接口数据真实化的前提）。
    """

    __tablename__ = "payment_schedules"
    __table_args__ = (
        Index("ix_payment_schedules_po", "po_id"),
        Index("ix_payment_schedules_status", "status"),
        _UTF8MB4_TABLE_ARGS,
    )

    payment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    po_id: Mapped[str] = mapped_column(String(64), nullable=False)
    supplier_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    payment_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="scheduled"
    )
    optimization_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<PaymentSchedule payment_id={self.payment_id!r} "
            f"po_id={self.po_id!r} amount={self.amount}>"
        )


class Risk(Base):
    """
    风险表
    ------
    记录风险登记项，包含概率、影响与缓解措施。
    """

    __tablename__ = "risks"
    __table_args__ = (
        Index("ix_risks_type", "risk_type"),
        Index("ix_risks_level", "risk_level"),
        Index("ix_risks_status", "status"),
        Index("ix_risks_owner", "owner"),
        _UTF8MB4_TABLE_ARGS,
    )

    risk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    risk_name: Mapped[str] = mapped_column(String(256), nullable=False)
    risk_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    probability: Mapped[str | None] = mapped_column(String(16), nullable=True)
    impact: Mapped[str | None] = mapped_column(String(16), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mitigation: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<Risk risk_id={self.risk_id!r} name={self.risk_name!r} "
            f"level={self.risk_level!r} status={self.status!r}>"
        )


# ===========================================================================
# 战略分析表
# ===========================================================================


class Budget(Base):
    """
    预算表
    ------
    记录各预算科目在指定财年 / 期间的计划与实际金额。
    """

    __tablename__ = "budgets"
    __table_args__ = (
        Index("ix_budgets_department", "department"),
        Index("ix_budgets_period", "period"),
        Index("ix_budgets_fiscal_year", "fiscal_year"),
        Index("ix_budgets_status", "status"),
        _UTF8MB4_TABLE_ARGS,
    )

    budget_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_year: Mapped[str] = mapped_column(String(8), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    planned_amount: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    actual_amount: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="planned"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<Budget budget_id={self.budget_id!r} "
            f"department={self.department!r} period={self.period!r} "
            f"status={self.status!r}>"
        )


class BIReport(Base):
    """
    BI 报表表
    ---------
    存储各类业务分析报表的元数据与结果数据（JSON）。
    """

    __tablename__ = "bi_reports"
    __table_args__ = (
        Index("ix_bi_reports_type", "report_type"),
        Index("ix_bi_reports_period", "period"),
        _UTF8MB4_TABLE_ARGS,
    )

    report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_name: Mapped[str] = mapped_column(String(256), nullable=False)
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<BIReport report_id={self.report_id!r} "
            f"name={self.report_name!r} type={self.report_type!r}>"
        )


# ===========================================================================
# 知识库表
# ===========================================================================


class KnowledgeCategory(Base):
    """
    知识库分类表
    ------------
    支持自引用层级，组织知识文档的分类树。
    """

    __tablename__ = "knowledge_categories"
    __table_args__ = _UTF8MB4_TABLE_ARGS

    category_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("knowledge_categories.category_id"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    parent: Mapped["KnowledgeCategory | None"] = relationship(
        "KnowledgeCategory",
        remote_side=[category_id],
        foreign_keys=[parent_id],
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeCategory category_id={self.category_id!r} "
            f"name={self.name!r}>"
        )


class KnowledgeDocument(Base):
    """
    知识库文档表
    ------------
    存储文档全文、摘要与版本信息，支持知识检索与引用。
    """

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_knowledge_documents_category", "category"),
        Index("ix_knowledge_documents_status", "status"),
        Index("ix_knowledge_documents_author", "author"),
        _UTF8MB4_TABLE_ARGS,
    )

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft"
    )
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeDocument doc_id={self.doc_id!r} "
            f"title={self.title!r} version={self.version!r} "
            f"status={self.status!r}>"
        )


# ===========================================================================
# 事件与审计表
# ===========================================================================


class EventRecord(Base):
    """
    事件记录表
    ----------
    事件溯源日志（append-only），记录所有业务变更事件。
    ``event_id`` 使用 BIGINT 自增主键，``aggregate_id`` 标识聚合根。
    """

    __tablename__ = "event_records"
    __table_args__ = (
        Index("ix_event_records_aggregate", "aggregate_id"),
        Index("ix_event_records_type", "event_type"),
        Index("ix_event_records_created_at", "created_at"),
        Index("ix_event_records_aggregate_type", "aggregate_id", "event_type"),
        _UTF8MB4_TABLE_ARGS,
    )

    event_id: Mapped[int] = mapped_column(
        BigIntPK, primary_key=True, autoincrement=True
    )
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_data: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<EventRecord event_id={self.event_id} "
            f"aggregate_id={self.aggregate_id!r} type={self.event_type!r}>"
        )


class AuditEntry(Base):
    """
    审计条目表
    ----------
    通过 ``hash_prev`` / ``hash_curr`` 构成不可篡改哈希链，
    记录所有 Agent 与人类操作。
    """

    __tablename__ = "audit_entries"
    __table_args__ = (
        Index("ix_audit_entries_actor", "actor"),
        Index("ix_audit_entries_action", "action"),
        Index("ix_audit_entries_resource", "resource"),
        Index("ix_audit_entries_created_at", "created_at"),
        _UTF8MB4_TABLE_ARGS,
    )

    entry_id: Mapped[int] = mapped_column(
        BigIntPK, primary_key=True, autoincrement=True
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource: Mapped[str] = mapped_column(String(256), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    hash_prev: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hash_curr: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    def __repr__(self) -> str:
        return (
            f"<AuditEntry entry_id={self.entry_id} actor={self.actor!r} "
            f"action={self.action!r} resource={self.resource!r}>"
        )


# ===========================================================================
# 工作流表
# ===========================================================================


class WorkflowRequest(Base):
    """
    工作流请求表
    ------------
    记录各类审批 / 业务流程的请求与状态流转。
    ``payload`` 以 JSON 存储流程上下文数据。
    """

    __tablename__ = "workflow_requests"
    __table_args__ = (
        Index("ix_workflow_requests_type", "request_type"),
        Index("ix_workflow_requests_status", "status"),
        Index("ix_workflow_requests_requester", "requester_id"),
        Index("ix_workflow_requests_priority", "priority"),
        Index("ix_workflow_requests_created_at", "created_at"),
        _UTF8MB4_TABLE_ARGS,
    )

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_type: Mapped[str] = mapped_column(String(64), nullable=False)
    requester_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default="normal"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<WorkflowRequest request_id={self.request_id!r} "
            f"type={self.request_type!r} status={self.status!r} "
            f"priority={self.priority!r}>"
        )


# ===========================================================================
# 内部通讯模块
# ===========================================================================


class ChatChannel(Base):
    """聊天频道：群聊 / 单聊 / Agent对话"""

    __tablename__ = "chat_channels"
    __table_args__ = _UTF8MB4_TABLE_ARGS

    channel_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False)  # group / direct / agent
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 群聊名称
    avatar: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)  # 创建者 user_id
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_message_preview: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return (
            f"<ChatChannel channel_id={self.channel_id!r} "
            f"type={self.channel_type!r} name={self.name!r}>"
        )


class ChatMember(Base):
    """频道成员"""

    __tablename__ = "chat_members"
    __table_args__ = (
        Index("ix_chat_members_channel", "channel_id"),
        Index("ix_chat_members_user", "user_id"),
        _UTF8MB4_TABLE_ARGS,
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_channels.channel_id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # user_id 或 agent_id
    member_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user"
    )  # user / agent
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="member"
    )  # owner / admin / member
    joined_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    last_read_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return (
            f"<ChatMember id={self.id} channel_id={self.channel_id!r} "
            f"user_id={self.user_id!r} role={self.role!r}>"
        )


class ChatMessage(Base):
    """聊天消息"""

    __tablename__ = "chat_messages"
    # 注：channel_id / created_at 的索引由列级 index=True 生成，
    # 不再重复定义同名 Index（sqlite 全局索引命名空间下会冲突）
    __table_args__ = (_UTF8MB4_TABLE_ARGS,)

    message_id: Mapped[int] = mapped_column(
        BigIntPK, primary_key=True, autoincrement=True
    )
    channel_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_channels.channel_id"), nullable=False, index=True
    )
    sender_id: Mapped[str] = mapped_column(String(64), nullable=False)  # user_id 或 agent_id
    sender_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user"
    )  # user / agent / system
    sender_name: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="text"
    )  # text / file / command / system
    file_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reply_to_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, index=True
    )

    def __repr__(self) -> str:
        return (
            f"<ChatMessage message_id={self.message_id} "
            f"channel_id={self.channel_id!r} sender_id={self.sender_id!r}>"
        )


class ChatFile(Base):
    """聊天文件"""

    __tablename__ = "chat_files"
    __table_args__ = _UTF8MB4_TABLE_ARGS

    file_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_channels.channel_id"), nullable=False
    )
    uploaded_by: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    original_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(
        String(128), nullable=False, default="application/octet-stream"
    )
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )

    def __repr__(self) -> str:
        return (
            f"<ChatFile file_id={self.file_id!r} "
            f"channel_id={self.channel_id!r} filename={self.filename!r}>"
        )


# ===========================================================================
# 模型注册表
# ===========================================================================

# 所有 ORM 模型集合，供 Alembic 与测试便利使用
__all__ = [
    # 基类
    "Base",
    # RBAC
    "Department",
    "User",
    "Role",
    "Permission",
    "UserRole",
    "RolePermission",
    # 财务业务
    "Invoice",
    "Reimbursement",
    "TaxRecord",
    "Voucher",
    "FundTransfer",
    # 人力资源
    "Employee",
    "AttendanceRecord",
    "PayrollRecord",
    # 销售与市场
    "Customer",
    "SalesOrder",
    "Campaign",
    # 运营管理
    "InventoryItem",
    "Asset",
    "Project",
    "QualityInspection",
    # 法务风控
    "Contract",
    "Risk",
    # 战略分析
    "Budget",
    "BIReport",
    # 知识库
    "KnowledgeDocument",
    "KnowledgeCategory",
    # 事件与审计
    "EventRecord",
    "AuditEntry",
    # 工作流
    "WorkflowRequest",
    # 内部通讯
    "ChatChannel",
    "ChatMember",
    "ChatMessage",
    "ChatFile",
]
